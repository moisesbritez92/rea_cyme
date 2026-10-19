"""Modelo eléctrico por fase desde tablas CYME 9 (Access), listo para el motor nodal.

Cada convención aplicada figura en CONVENTIONS con su evidencia. Las hipótesis no
contrastadas con resultados de CYME se marcan como tales y se copian al informe.
"""
from collections import defaultdict
import cmath
import math

from .topology import PHASES

ABC = 'ABC'
SWITCHING = {8: 'CYMBREAKER', 10: 'CYMRECLOSER', 13: 'CYMSWITCH', 14: 'CYMFUSE'}
LINES = {
    1: ('CYMUNDERGROUNDLINE', 'CableId', 'CYMEQCABLE', 'sequence'),
    2: ('CYMOVERHEADLINE', 'LineId', 'CYMEQOVERHEADLINE', 'sequence'),
    23: ('CYMOVERHEADLINEUNBALANCED', 'LineId', 'CYMEQOVERHEADLINEUNBALANCED', 'phase'),
}
LOCATIONS = {0: 'middle', 1: 'from', 2: 'to'}
CONNECTIONS = {0: 'wye_grounded', 2: 'delta'}
DELTA_BRANCH = {'A': 'AB', 'B': 'BC', 'C': 'CA'}
TABLES = {'CYMNETWORK', 'CYMNODE', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE', 'CYMEQSOURCE',
          'CYMEQUIVALENTSOURCE', 'CYMHEADNODE', 'CYMNETWORKMETER', 'CYMREGULATOR', 'CYMEQREGULATOR',
          'CYMLOAD', 'CYMCUSTOMERLOAD', 'CYMCONSUMERCLASS', 'CYMSHUNTCAPACITOR', 'CYMLDCSETTINGS',
          *SWITCHING.values(), *(v[0] for v in LINES.values()), *(v[2] for v in LINES.values())}

CONVENTIONS = {
    'units': {'value': 'Length m; Z ohm/km; B uS/km; tensiones de fuente kV fase-neutro',
              'authority': 'verified', 'evidence': 'memory.md del MCP (lecturas API) y cymUnit.ini'},
    'device_location': {'value': LOCATIONS, 'authority': 'verified',
                        'evidence': 'Access Location 0/1/2 frente a Middle/From/To del .sxst (580 cargas)'},
    'load_connection': {'value': CONNECTIONS, 'authority': 'verified',
                        'evidence': 'Access 0/2 frente a Yg/D del .sxst; enum CymConnectionConfiguration'},
    'load_value_type': {'value': {0: 'KW_KVAR'}, 'authority': 'verified',
                        'evidence': 'Access LoadValueType=0 frente a KW_KVAR del .sxst (637 filas)'},
    'line_sequence_to_phase': {'value': 'Zs=(Z0+2Z1)/3, Zm=(Z0-Z1)/3; igual para la admitancia shunt',
                               'authority': 'verified',
                               'evidence': 'flujo CYME nandu-ref-lf-20261019-01: 2690 tensiones de nodo, |dV| <= 17,4 V'},
    'line_pi': {'value': 'PI nominal, mitad de la admitancia shunt en cada extremo, carga capacitiva incluida',
                'authority': 'verified', 'evidence': 'sin carga capacitiva el error contra CYME sube de 17 a 188 V'},
    'regulator': {'value': 'tipo A, Vsalida_X = Ventrada_X + n_X (Ventrada_X - Ventrada_Y), n = paso * tap',
                  'authority': 'fitted_to_cyme_results',
                  'evidence': '15 resultados de reguladores CYME (runs/*/result.json): residuo <= 2,6 V; tipo B > 100 V'},
    'source': {'value': 'fuente ideal con OperatingVoltageA/B/C y ángulos 0/-120/120',
               'authority': 'documented', 'evidence': 'IncludeSourceImpedance=0 en LoadFlowParametersConfig'},
    'abc_load_split': {'value': 'una fila ABC se reparte en tercios iguales', 'authority': 'verified',
                       'evidence': 'informe CYME «Load Flow - Loads»: 1011 potencias por fase, |dS| <= 0,09 kVA'},
    'abc_load_on_missing_phases': {'value': 'carga cuyas fases no están en el tramo: potencia cero (se excluye)',
                                   'authority': 'verified',
                                   'evidence': 'CYME reporta ANDE_2424 y ANDE_19787 con 0 kVA'},
    'delta_single_phase': {'value': DELTA_BRANCH, 'authority': 'verified',
                           'evidence': 'informe CYME de cargas (potencia por fase de línea) coincide en las delta'},
    'zip_threshold': {'value': 'bajo LoadFlowVoltPercentOfNominal la parte de potencia constante pasa a Z constante',
                      'authority': 'documented_approximation', 'evidence': 'ayuda PowerFlow, Voltage drop'},
    'frequency': {'value': 'reactancias del catálogo sin reescalar por frecuencia del sistema',
                  'authority': 'verified', 'evidence': 'reescalar 60/50 eleva el error contra CYME de 17 a 663 V'},
    'regulator_control': {
        'value': 'Normal Tap Operation: FixedTap(2)/FixedTapPrimary(4) fijos; Terminal(3) y LoadCenter(0)/RXSettings(1) '
                 'mueven taps discretos si V_control sale de SetVoltage ± Bandwidth/2; '
                 'V_control = 120 V x V/V_base (LN en Yg, LL en delta) - (R+jX) I/CTprimary',
        'authority': 'hypothesis',
        'evidence': 'consignas en CYMLDCSETTINGS; corrida CYME del 6 oct. 2026: reguladores Yg Terminal con PT 200 '
                    'no fueron al tap máximo, lo que descarta V/PT y es coherente con la base de 120 V del estudio'},
}
CONVENTIONS.update({
    'state_estimator': {
        'value': 'WLS por fase; medidor A-FP = |I|, P y Q (precisión 3 % = 3σ); carga σ = precisión × valor; '
                 'cargas bloqueadas fijas; la fila ABC se escribe como suma de sus fases',
        'authority': 'fitted_to_cyme_results',
        'evidence': 'estimador CYME 19 oct. 2026: CZU06, 325 filas de cliente, RMS 0,23 kW / 0,03 kvar con los '
                    'valores exactos del historial de modificaciones; CZU08 queda a 0,67 kW RMS con sesgo de '
                    '-127 kW / +126 kvar sin explicar (docs/cyme-ingenieria-inversa.md)'},
    'load_allocation': {
        'value': 'KVAMethod por fase; filas ABC equilibradas con la asignación de menor módulo entre sus fases',
        'authority': 'fitted_to_cyme_results',
        'evidence': 'Load Allocation CYME 19 oct. 2026, CZU06: 388 potencias por fase, RMS 0,9 kVA (promedio: 6,0); '
                    'en CZU08 CYME no converge («cannot adjust KW after 60 iterations»)'},
})
CONTROL_MODES = {0: 'load_center', 1: 'rx', 2: 'fixed', 3: 'terminal', 4: 'fixed'}


def _num(value, field):
    if isinstance(value, dict) and set(value) == {'$decimal'}:
        value = float(value['$decimal'])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{field}: número ausente o no finito')
    return float(value)


def _phases(code):
    if type(code) is not int or code not in PHASES or code == 0:
        raise ValueError(f'código de fase no admitido: {code!r}')
    return [p for p in ABC if p in PHASES[code]]


def _sequence_matrix(zero, positive, phases):
    s, m = (zero + 2 * positive) / 3, (zero - positive) / 3
    return [[s if a == b else m for b in phases] for a in phases]


def _line_matrices(kind, eq, phases, length_m, parallel=1):
    km = length_m / 1000
    if kind == 'sequence':
        z1 = complex(_num(eq['PositiveSequenceResistance'], 'R1'), _num(eq['PositiveSequenceReactance'], 'X1'))
        z0 = complex(_num(eq['ZeroSequenceResistance'], 'R0'), _num(eq['ZeroSequenceReactance'], 'X0'))
        y1 = complex(_num(eq['PosSeqShuntConductance'], 'G1'), _num(eq['PosSeqShuntSusceptance'], 'B1'))
        y0 = complex(_num(eq['ZeroSequenceShuntConductance'], 'G0'), _num(eq['ZeroSequenceShuntSusceptance'], 'B0'))
        z = _sequence_matrix(z0, z1, phases)
        y = _sequence_matrix(y0, y1, phases)
    else:
        pair = {frozenset('AB'): 'AB', frozenset('BC'): 'BC', frozenset('AC'): 'CA'}

        def term(a, b, r, x):
            key = a if a == b else pair[frozenset((a, b))]
            prefix = 'Self' if a == b else 'Mutual'
            return complex(_num(eq[f'{prefix}{r}{key}'], r), _num(eq[f'{prefix}{x}{key}'], x))

        z = [[term(a, b, 'Resistance', 'Reactance') for b in phases] for a in phases]
        y = [[complex(_num(eq[('ShuntConductance' if a == b else 'MutualShuntConductance') + (a if a == b else pair[frozenset((a, b))])], 'G'),
                      _num(eq[('ShuntSusceptance' if a == b else 'MutualShuntSusceptance') + (a if a == b else pair[frozenset((a, b))])], 'B'))
              for b in phases] for a in phases]
    return ([[v * km / parallel for v in row] for row in z],
            [[v * km * 1e-6 * parallel for v in row] for row in y])


def build(tables, network_ids=None, include_line_charging=True):
    """Devuelve el modelo nodal y la lista de incidencias; no resuelve el flujo."""
    issues = []

    def issue(code, **context):
        issues.append({'code': code, **context})

    nets = set(network_ids or [r['NetworkId'] for r in tables.get('CYMNETWORK', [])])

    def keyed(table, key=('NetworkId', 'DeviceNumber')):
        index = {}
        for row in tables.get(table, []):
            k = tuple(row.get(f) for f in key) if isinstance(key, tuple) else row.get(key)
            index.setdefault(k, []).append(row)
        return index

    eq = {t: keyed(t, 'EquipmentId') for t in tables if t.startswith('CYMEQ')}
    instances = {t: keyed(t) for t in {*SWITCHING.values(), 'CYMREGULATOR', 'CYMLOAD', 'CYMSHUNTCAPACITOR',
                                       *(v[0] for v in LINES.values())}}
    classes = {r['ConsumerClassId']: r for r in tables.get('CYMCONSUMERCLASS', [])}
    ldc = {(r['NetworkId'], r['DeviceNumber']): r for r in tables.get('CYMLDCSETTINGS', [])
           if r.get('Forward') == 1 and r.get('DeviceType') == 4}
    equivalent = {r['NodeId']: r for r in tables.get('CYMEQUIVALENTSOURCE', [])}

    def one(index, key, context):
        rows = index.get(key, [])
        if len(rows) != 1:
            raise ValueError(f'{context}: {"falta" if not rows else "ambigua"} la referencia {key}')
        return rows[0]

    attachments = defaultdict(list)
    for row in tables.get('CYMSECTIONDEVICE', []):
        if row['NetworkId'] in nets:
            attachments[(row['NetworkId'], row['SectionId'])].append(row)

    nominal_kvll = {}
    sources = []
    for row in tables.get('CYMSOURCE', []):
        if row['NetworkId'] not in nets:
            continue
        equiv = equivalent.get(row['NodeId'], {})
        catalog = one(eq.get('CYMEQSOURCE', {}), row['EquipmentId'], row['DeviceNumber'])
        kvll = _num(equiv.get('KVLL', catalog.get('NominalKVLL')), 'KVLL')
        nominal_kvll[row['NetworkId']] = kvll
        angles = [equiv.get(f'OperatingAngle{i}', default) for i, default in ((1, 0.0), (2, -120.0), (3, 120.0))]
        sources.append({'id': row['DeviceNumber'], 'network_id': row['NetworkId'], 'bus': row['NodeId'],
                        'voltage_v': {p: cmath.rect(_num(row[f'OperatingVoltage{p}'], 'OperatingVoltage') * 1000,
                                                    math.radians(_num(a, 'OperatingAngle')))
                                      for p, a in zip(ABC, angles)},
                        'nominal_kvll': kvll,
                        'impedance_ohm': {'z1': complex(catalog['FirstLevelR1'], catalog['FirstLevelX1']),
                                          'z0': complex(catalog['FirstLevelR0'], catalog['FirstLevelX0'])}})

    buses = {}

    def bus(name, phases, network_id):
        entry = buses.setdefault(name, {'phases': set(), 'network_id': network_id, 'internal': '#' in name})
        entry['phases'].update(phases)
        return name

    lines, switches, regulators, loads, capacitors, sections = [], [], [], [], [], []
    for sec in tables.get('CYMSECTION', []):
        net, sid = sec['NetworkId'], sec['SectionId']
        if net not in nets:
            continue
        try:
            phases = _phases(sec['Phase'])
            devices = attachments.get((net, sid), [])
            start = bus(sec['FromNodeId'], phases, net)
            end = bus(sec['ToNodeId'], phases, net)
            series = {'from': [], 'middle': [], 'to': []}
            line, shunts = None, []
            for att in devices:
                code, number = att['DeviceType'], att['DeviceNumber']
                location = LOCATIONS.get(att['Location'])
                if code in LINES:
                    table, field, catalog, kind = LINES[code]
                    inst = one(instances[table], (net, number), number)
                    if inst.get('Status') != 0:
                        raise ValueError(f'{number}: estado de línea {inst.get("Status")} no admitido')
                    cat = one(eq.get(catalog, {}), inst[field], number)
                    length = _num(inst['Length'], 'Length')
                    if length < 0:
                        raise ValueError(f'{number}: longitud negativa')
                    if length == 0:
                        # Tramo de longitud cero: unión sin impedancia en todas sus fases.
                        issue('zero_length_line_as_connection', network_id=net, section_id=sid, device_id=number)
                        series['middle'].append(('switch', {'id': f'{number}#longitud0', 'table': None, 'closed': phases}))
                        continue
                    parallel = inst.get('NumberOfCableInParallel') or 1
                    z, y = _line_matrices(kind, cat, phases, length, parallel)
                    rating = cat.get('NominalRating') or cat.get('NominalRatingA') or 0
                    line = {'id': number, 'kind': kind, 'equipment_id': inst[field], 'length_m': length,
                            'device_code': code, 'rating_a': float(rating) * parallel,
                            'z': z, 'y': y if include_line_charging else [[0j] * len(phases) for _ in phases],
                            'catalog_frequency_hz': cat.get('Frequency')}
                elif code in SWITCHING:
                    inst = one(instances[SWITCHING[code]], (net, number), number)
                    if inst.get('Status') != 0:
                        raise ValueError(f'{number}: estado {inst.get("Status")} no admitido')
                    closed = [p for p in phases if p in PHASES[inst['ClosedPhase']]] if inst['ClosedPhase'] else []
                    series[location or 'from'].append(('switch', {'id': number, 'table': SWITCHING[code],
                                                                   'closed': closed}))
                elif code == 4:
                    inst = one(instances['CYMREGULATOR'], (net, number), number)
                    cat = one(eq.get('CYMEQREGULATOR', {}), inst['EquipmentId'], number)
                    if inst['ConnectionConfiguration'] not in (0, 4):
                        raise ValueError(f'{number}: conexión de regulador {inst["ConnectionConfiguration"]} no admitida')
                    step = _num(cat['MaximumBoost'], 'MaximumBoost') / 100 / (cat['NumberOfTaps'] / 2)
                    mode = CONTROL_MODES.get(inst['SettingOption'])
                    settings = ldc.get((net, number))
                    if mode is None or (mode != 'fixed' and settings is None):
                        issue('regulator_control_not_modeled', device_id=number, setting_option=inst['SettingOption'],
                              note='se usan los taps guardados como fijos')
                        mode = 'fixed'
                    control = None
                    if mode != 'fixed':
                        control = {'mode': mode, 'ct_primary_a': _num(inst['CTPrimaryRating'], 'CTPrimaryRating'),
                                   **{f'{k}_v': {p: _num(settings[f'{name}{p}'], name) for p in ABC}
                                      for k, name in (('set', 'SetVoltage'), ('bandwidth', 'Bandwidth'),
                                                      ('r', 'Rset'), ('x', 'Xset'))}}
                        if mode == 'terminal':
                            control['r_v'] = control['x_v'] = {p: 0.0 for p in ABC}
                    series[location or 'from'].append(('regulator', {
                        'id': number, 'equipment_id': inst['EquipmentId'],
                        'connection': 'closed_delta' if inst['ConnectionConfiguration'] == 4 else 'wye_grounded',
                        'taps': {p: _num(inst[f'TapPosition{p}'], 'TapPosition') for p in ABC},
                        'step_pu': step, 'max_tap': cat['NumberOfTaps'] // 2, 'control': control,
                        'feeding_node': inst.get('NormalFeedingNodeId')}))
                elif code == 20:
                    shunts.append(('load', number, location))
                elif code == 17:
                    shunts.append(('capacitor', number, location))
                else:
                    raise ValueError(f'{number}: tipo de dispositivo {code} no modelado')
            # Cadena física del tramo: extremos, dispositivos y la línea partida si hay cargas al medio.
            chain = series['from'][:]
            mid_needed = any(loc == 'middle' for _, _, loc in shunts) or series['middle']
            if line and mid_needed:
                half = {**line, 'z': [[v / 2 for v in r] for r in line['z']],
                        'y': [[v / 2 for v in r] for r in line['y']]}
                chain += [('line', {**half, 'id': line['id'] + '#1'}), ('middle', None), *series['middle'],
                          ('line', {**half, 'id': line['id'] + '#2'})]
            elif line:
                chain += [('line', line)] + ([('middle', None)] if mid_needed else []) + series['middle']
            else:
                chain += [('middle', None)] + series['middle']
            chain += series['to']
            nodes, current, middle = [start], start, start
            for k, (kind, item) in enumerate(chain):
                if kind == 'middle':
                    middle = current
                    continue
                last = all(c[0] == 'middle' for c in chain[k + 1:])
                nxt = end if last else bus(f'{sid}#{k}', phases, net)
                record = {**item, 'section_id': sid, 'network_id': net, 'from': current, 'to': nxt,
                          'phases': phases}
                {'line': lines, 'switch': switches, 'regulator': regulators}[kind].append(record)
                current = nxt
                nodes.append(nxt)
            if current != end:
                switches.append({'id': f'{sid}#enlace', 'table': None, 'closed': phases, 'section_id': sid,
                                 'network_id': net, 'from': current, 'to': end, 'phases': phases})
            sections.append({'id': sid, 'network_id': net, 'from': start, 'to': end, 'phases': phases,
                             'chain': [c[1]['id'] for c in chain if c[1]]})
            for kind, number, location in shunts:
                where = {'from': start, 'to': end, 'middle': middle}[location or 'to']
                if kind == 'capacitor':
                    inst = one(instances['CYMSHUNTCAPACITOR'], (net, number), number)
                    if inst.get('Status') != 0 or inst.get('CapacitorControlType') not in (0, None):
                        issue('capacitor_skipped', device_id=number, status=inst.get('Status'))
                        continue
                    conn = CONNECTIONS.get(inst['ConnectionConfiguration'])
                    if conn is None:
                        raise ValueError(f'{number}: conexión de capacitor no admitida')
                    for p in phases:
                        q = _num(inst[f'KVAR{p}'], 'KVAR') * 1000
                        if q:
                            capacitors.append({'id': f'{number}:{p}', 'device_id': number, 'bus': where,
                                               'section_id': sid, 'connection': conn,
                                               'phases': list(DELTA_BRANCH[p]) if conn == 'delta' else [p],
                                               'rated_q_var': q,
                                               'rated_v': _num(inst['KVLN'], 'KVLN') * 1000 * (math.sqrt(3) if conn == 'delta' else 1)})
                else:
                    loads.append({'device_id': number, 'bus': where, 'section_id': sid, 'network_id': net,
                                  'section_phases': phases, 'location': location})
        except (ValueError, KeyError, TypeError) as exc:
            issue('section_excluded', network_id=net, section_id=sid, reason=str(exc))

    # Cargas por cliente: una fila por fase o una fila ABC.
    load_devices = {(l['network_id'], l['device_id']): l for l in loads}
    elements = []
    for row in tables.get('CYMCUSTOMERLOAD', []):
        key = (row.get('NetworkId'), row.get('DeviceNumber'))
        place = load_devices.get(key)
        if row.get('NetworkId') not in nets:
            continue
        context = {'device_id': row.get('DeviceNumber'), 'customer': row.get('CustomerNumber')}
        try:
            if not place:
                raise ValueError('carga sin tramo admitido')
            if row.get('Status') != 0:
                raise ValueError(f'estado de cliente {row.get("Status")} no admitido')
            if row.get('LoadValueType') != 0:
                raise ValueError(f'LoadValueType {row.get("LoadValueType")} sin convención verificada')
            inst = one(instances['CYMLOAD'], key, row['DeviceNumber'])
            conn = CONNECTIONS.get(inst['ConnectionConfiguration'])
            if conn is None:
                raise ValueError(f'conexión de carga {inst["ConnectionConfiguration"]} no admitida')
            cls = classes.get(row.get('ConsumerClassId'))
            if not cls:
                raise ValueError('clase de consumo inexistente')
            if cls.get('IsExponentialModel'):
                raise ValueError('modelo exponencial no implementado')
            zip_ = [_num(cls[f'Constant{k}'], k) / 100 for k in ('Impedance', 'Current', 'Power')]
            phases = _phases(row['Phase'])
            if not set(phases) <= set(place['section_phases']):
                raise ValueError(f'fases de carga {phases} fuera del tramo {place["section_phases"]}')
            p, q = _num(row['LoadValue1'], 'LoadValue1') * 1000, _num(row['LoadValue2'], 'LoadValue2') * 1000
            share = 1 / len(phases)
            for ph in phases:
                terminals = list(DELTA_BRANCH[ph]) if conn == 'delta' else [ph]
                if not set(terminals) <= set(place['section_phases']):
                    raise ValueError(f'rama delta {terminals} fuera del tramo')
                elements.append({'id': f'{row["DeviceNumber"]}:{row["CustomerNumber"]}:{ph}',
                                 'device_id': row['DeviceNumber'], 'customer': row['CustomerNumber'],
                                 'bus': place['bus'], 'section_id': place['section_id'],
                                 'network_id': place['network_id'], 'connection': conn, 'phases': terminals,
                                 'phase': ph, 'share': share,
                                 # Fila de cliente: CYME guarda un único valor para una fila ABC.
                                 'row': f'{row["DeviceNumber"]}:{row["CustomerNumber"]}:{"".join(phases)}',
                                 'utilization_pct': _num(cls.get('UtilizationFactor', 100.0), 'UtilizationFactor'),
                                 'p_w': p * share, 'q_var': q * share, 'zip': zip_,
                                 'vz_pu': _num(cls.get('LoadFlowVoltPercentOfNominal', 0), 'Vz') / 100,
                                 'locked': bool(row.get('LockDuringLoadAllocation')),
                                 'connected_kva': row.get('ConnectedKVA'), 'kwh': row.get('KWHUsage')})
        except (ValueError, KeyError, TypeError) as exc:
            issue('customer_load_excluded', reason=str(exc), **context)

    meters = []
    for row in tables.get('CYMNETWORKMETER', []):
        if row.get('NetworkId') in nets:
            meters.append({'network_id': row['NetworkId'], 'node': row['NodeId'], 'demand_type_raw': row['DemandType'],
                           'values': {p: (row.get(f'Val1{p}') or 0.0, row.get(f'Val2{p}') or 0.0) for p in ABC},
                           'total': (row.get('Val1Total') or 0.0, row.get('Val2Total') or 0.0),
                           'is_total': bool(row.get('IsTotalDemand'))})

    for name, entry in buses.items():
        entry['phases'] = [p for p in ABC if p in entry['phases']]
        entry['nominal_kvll'] = nominal_kvll.get(entry['network_id'])
    return {'format': 'nandu-cyme-model-v1', 'networks': sorted(nets), 'buses': buses, 'sources': sources,
            'lines': lines, 'switches': switches, 'regulators': regulators, 'loads': elements,
            'capacitors': capacitors, 'sections': sections, 'meters': meters,
            'conventions': CONVENTIONS, 'issues': issues}
