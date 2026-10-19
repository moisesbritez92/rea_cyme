"""Flujo de carga OpenDSS para el formato eléctrico explícito Ñandu v1.

Alcance: fuente trifásica, líneas matriciales, cargas PQ Yg/delta y capacitores.
No interpreta códigos CYME ni acepta comandos DSS suministrados por el usuario.
"""
from collections import defaultdict, deque
import hashlib
import json
import math

PHASE_NODE = {'A': 1, 'B': 2, 'C': 3}


def numeric(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{field}: se requiere un número finito')
    return float(value)


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        raise ValueError(f'Campos no admitidos o faltantes; requeridos: {sorted(required)}')


def identifiers(items, kind):
    result = {}
    if not isinstance(items, list):
        raise ValueError(f'{kind}: se requiere una lista')
    for item in items:
        name = item.get('id') if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip() or name in result:
            raise ValueError(f'{kind}: identificador vacío o duplicado')
        result[name] = item
    return result


def phase_list(value):
    if (not isinstance(value, list) or not value or any(p not in PHASE_NODE for p in value)
            or len(set(value)) != len(value)):
        raise ValueError('Fases: lista no vacía de A/B/C, sin repeticiones')
    return value


def terminal_phases(item):
    """Cada carga/capacitor es una rama LN o LL, nunca un banco repartido implícitamente."""
    if item.get('connection') == 'wye_grounded':
        if item.get('phase') not in PHASE_NODE:
            raise ValueError('Una rama Yg requiere phase A, B o C')
        return [item['phase']]
    if item.get('connection') == 'delta':
        result = phase_list(item.get('phases'))
        if len(result) != 2:
            raise ValueError('Una rama delta requiere exactamente dos fases distintas')
        return result
    raise ValueError('Conexión no admitida: use wye_grounded o delta')


def matrix(value, size, name):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f'{name}: dimensión incorrecta')
    for row in value:
        if not isinstance(row, list) or len(row) != size:
            raise ValueError(f'{name}: matriz no cuadrada')
        for entry in row:
            numeric(entry, name)
    for i in range(size):
        for j in range(size):
            if not math.isclose(value[i][j], value[j][i], rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError(f'{name}: matriz no simétrica')
    # Criterio PSD mediante todos los menores principales (matrices de hasta 3x3).
    scale = max(1.0, max(abs(x) for row in value for x in row))
    if any(value[i][i] < -1e-12 * scale for i in range(size)):
        raise ValueError(f'{name}: diagonal negativa')
    for i in range(size):
        for j in range(i):
            if value[i][i] * value[j][j] - value[i][j] ** 2 < -1e-12 * scale ** 2:
                raise ValueError(f'{name}: matriz no semidefinida positiva')
    if size == 3:
        a, b, c = value
        determinant = a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0]) + a[2] * (b[0] * c[1] - b[1] * c[0])
        if determinant < -1e-12 * scale ** 3:
            raise ValueError(f'{name}: matriz no semidefinida positiva')


def validate_model(model):
    fields(model, ('format', 'frequency_hz', 'source', 'buses', 'lines', 'loads'), ('solver', 'description', 'capacitors'))
    if model['format'] != 'nandu-electrical-network-v1':
        raise ValueError('Formato eléctrico no compatible')
    if numeric(model['frequency_hz'], 'frequency_hz') <= 0:
        raise ValueError('La frecuencia debe ser positiva')
    buses = identifiers(model['buses'], 'buses')
    if not buses:
        raise ValueError('Se necesita al menos una barra')
    for bus in buses.values():
        fields(bus, ('id', 'phases'))
        phase_list(bus['phases'])
    source = model['source']
    fields(source, ('bus', 'voltage_ln_v', 'angle_deg', 'r1_ohm', 'x1_ohm', 'r0_ohm', 'x0_ohm'))
    if source['bus'] not in buses or set(buses[source['bus']]['phases']) != set('ABC'):
        raise ValueError('La fuente requiere una barra ABC')
    if numeric(source['voltage_ln_v'], 'voltage_ln_v') <= 0:
        raise ValueError('Tensión de fuente no positiva')
    numeric(source['angle_deg'], 'angle_deg')
    for sequence in ('1', '0'):
        r, x = (numeric(source[k + sequence + '_ohm'], k + sequence) for k in ('r', 'x'))
        if r < 0 or x < 0 or r + x <= 0:
            raise ValueError('Impedancia de fuente: R/X no negativos y magnitud no nula')
    lines = identifiers(model['lines'], 'lines')
    adjacency = defaultdict(set)
    for line in lines.values():
        fields(line, ('id', 'from_bus', 'to_bus', 'phases', 'r_ohm', 'x_ohm', 'c_nf', 'enabled'))
        phases = phase_list(line['phases'])
        if type(line['enabled']) is not bool:
            raise ValueError('enabled debe ser booleano')
        if line['from_bus'] == line['to_bus']:
            raise ValueError('Línea con extremos idénticos')
        for end in ('from_bus', 'to_bus'):
            if line[end] not in buses or not set(phases).issubset(buses[line[end]]['phases']):
                raise ValueError('Extremo de línea o fases incompatibles con la barra')
        for key in ('r_ohm', 'x_ohm', 'c_nf'):
            matrix(line[key], len(phases), key)
        for i, p in enumerate(phases):
            if line['r_ohm'][i][i] + line['x_ohm'][i][i] <= 0:
                raise ValueError('Línea de impedancia nula: use una impedancia explícita no nula')
            if line['enabled']:
                a, b = (line['from_bus'], p), (line['to_bus'], p)
                adjacency[a].add(b)
                adjacency[b].add(a)
    reached = {(source['bus'], p) for p in 'ABC'}
    queue = deque(reached)
    while queue:
        for node in adjacency[queue.popleft()]:
            if node not in reached:
                reached.add(node)
                queue.append(node)
    loads = identifiers(model['loads'], 'loads')
    for load in loads.values():
        selector = 'phase' if load.get('connection') == 'wye_grounded' else 'phases'
        fields(load, ('id', 'bus', selector, 'p_w', 'q_var', 'connection', 'model'))
        terminals = terminal_phases(load)
        if load['model'] != 'constant_power':
            raise ValueError('Esta versión admite cargas PQ de potencia constante')
        if load['bus'] not in buses or not set(terminals).issubset(buses[load['bus']]['phases']):
            raise ValueError('Barra o fase de carga inexistente')
        if numeric(load['p_w'], 'p_w') < 0:
            raise ValueError('La generación requiere un modelo propio; p_w debe ser no negativo')
        numeric(load['q_var'], 'q_var')
        if any((load['bus'], phase) not in reached for phase in terminals):
            raise ValueError(f'Carga sin camino por su fase hasta la fuente: {load["id"]}')
    capacitors = identifiers(model.get('capacitors', []), 'capacitors')
    for cap in capacitors.values():
        selector = 'phase' if cap.get('connection') == 'wye_grounded' else 'phases'
        fields(cap, ('id', 'bus', selector, 'connection', 'rated_voltage_v', 'rated_q_var', 'enabled'))
        terminals = terminal_phases(cap)
        if cap['bus'] not in buses or not set(terminals).issubset(buses[cap['bus']]['phases']):
            raise ValueError('Barra o fases de capacitor inexistentes')
        if type(cap['enabled']) is not bool:
            raise ValueError('enabled del capacitor debe ser booleano')
        if numeric(cap['rated_voltage_v'], 'rated_voltage_v') <= 0 or numeric(cap['rated_q_var'], 'rated_q_var') <= 0:
            raise ValueError('El capacitor requiere tensión y potencia nominales positivas')
        if cap['enabled'] and any((cap['bus'], phase) not in reached for phase in terminals):
            raise ValueError(f'Capacitor activo sin camino hasta la fuente: {cap["id"]}')
    solver = model.get('solver', {})
    fields(solver, (), ('max_iterations', 'tolerance'))
    maximum = solver.get('max_iterations', 100)
    if type(maximum) is not int or not 1 <= maximum <= 10000:
        raise ValueError('max_iterations debe estar entre 1 y 10000')
    tolerance = numeric(solver.get('tolerance', 1e-8), 'tolerance')
    if not 0 < tolerance <= 1e-3:
        raise ValueError('tolerance debe estar en (0, 1e-3]')
    return buses, lines, loads, reached


def dss_matrix(value):
    return '[' + ' | '.join(' '.join(format(row[j], '.17g') for j in range(i + 1))
                            for i, row in enumerate(value)) + ']'


def solve(model):
    buses, lines, loads, reached = validate_model(model)
    capacitors = identifiers(model.get('capacitors', []), 'capacitors')
    try:
        from dss import DSS
    except ImportError as exc:
        raise RuntimeError('Instala el motor con pip install -e ".[solver]", o utiliza .venv\\Scripts\\python.exe') from exc
    engine = DSS.NewContext()
    engine.AllowForms = False
    engine.AllowEditor = False
    engine.AllowDOScmd = False
    engine.AllowChangeDir = False
    engine.AdvancedTypes = False
    names = {name: f'b{i}' for i, name in enumerate(buses)}
    source = model['source']
    base_ln_v = source['voltage_ln_v']
    commands = []

    def command(text):
        engine.Text.Command = text
        commands.append(text)

    def bus_connection(name, phases):
        return names[name] + '.' + '.'.join(str(PHASE_NODE[p]) for p in phases)

    def element_result(name):
        engine.ActiveCircuit.SetActiveElement(name)
        element = engine.ActiveCircuit.ActiveCktElement
        currents = list(element.Currents)
        powers = list(element.Powers)
        nodes = list(element.NodeOrder)
        terminals = []
        for terminal in range(element.NumTerminals):
            conductors = []
            for conductor in range(element.NumConductors):
                index = terminal * element.NumConductors + conductor
                real, imag = currents[2 * index:2 * index + 2]
                conductors.append({'node_number': int(nodes[index]), 'current_a': [float(real), float(imag)],
                                   'current_magnitude_a': math.hypot(real, imag),
                                   'p_w': float(powers[2 * index]) * 1000,
                                   'q_var': float(powers[2 * index + 1]) * 1000})
            terminals.append({'terminal': terminal + 1, 'conductors': conductors})
        return {'terminals': terminals, 'net_power_absorbed_w_var': [float(v) for v in element.Losses]}

    try:
        command('clear')
        command(f'set defaultbasefrequency={model["frequency_hz"]:.17g}')
        command(f'new circuit.nandu phases=3 bus1={bus_connection(source["bus"], "ABC")} '
                f'basekv={base_ln_v * math.sqrt(3) / 1000:.17g} pu=1 angle={source["angle_deg"]:.17g} '
                f'frequency={model["frequency_hz"]:.17g} '
                + ' '.join(f'{key[:-4]}={source[key]:.17g}' for key in ('r1_ohm', 'x1_ohm', 'r0_ohm', 'x0_ohm')))
        for i, line in enumerate(lines.values()):
            command(f'new linecode.lc{i} nphases={len(line["phases"])} basefreq={model["frequency_hz"]:.17g} '
                    f'rmatrix={dss_matrix(line["r_ohm"])} xmatrix={dss_matrix(line["x_ohm"])} '
                    f'cmatrix={dss_matrix(line["c_nf"])} units=none')
            command(f'new line.l{i} phases={len(line["phases"])} '
                    f'bus1={bus_connection(line["from_bus"], line["phases"])} '
                    f'bus2={bus_connection(line["to_bus"], line["phases"])} linecode=lc{i} '
                    f'length=1 units=none enabled={"yes" if line["enabled"] else "no"}')
        for i, load in enumerate(loads.values()):
            is_wye = load['connection'] == 'wye_grounded'
            terminals = bus_connection(load['bus'], terminal_phases(load)) + ('.0' if is_wye else '')
            nominal_v = base_ln_v if is_wye else base_ln_v * math.sqrt(3)
            command(f'new load.d{i} phases=1 bus1={terminals} '
                    f'conn={"wye" if is_wye else "delta"} model=1 kv={nominal_v / 1000:.17g} '
                    f'kw={load["p_w"] / 1000:.17g} kvar={load["q_var"] / 1000:.17g} '
                    'vminpu=0.01 vmaxpu=100 status=fixed')
        for i, cap in enumerate(capacitors.values()):
            is_wye = cap['connection'] == 'wye_grounded'
            terminals = bus_connection(cap['bus'], terminal_phases(cap)) + ('.0' if is_wye else '')
            command(f'new capacitor.c{i} phases=1 bus1={terminals} '
                    f'conn={"wye" if is_wye else "delta"} '
                    f'kv={cap["rated_voltage_v"] / 1000:.17g} kvar={cap["rated_q_var"] / 1000:.17g} '
                    f'numsteps=1 states=[1] r=0 xl=0 enabled={"yes" if cap["enabled"] else "no"}')
        config = model.get('solver', {})
        command(f'set mode=snapshot controlmode=off maxiterations={config.get("max_iterations", 100)} '
                f'tolerance={config.get("tolerance", 1e-8):.17g}')
        command('solve')
        circuit = engine.ActiveCircuit
        report = {'analysis': 'load_flow', 'engine': engine.Version, 'format': 'nandu-load-flow-v1',
                  'input_sha256': hashlib.sha256(json.dumps(model, sort_keys=True, ensure_ascii=False,
                                                           allow_nan=False).encode('utf-8')).hexdigest(),
                  'converged': bool(circuit.Solution.Converged), 'iterations': circuit.Solution.Iterations,
                  'status': 'completed' if circuit.Solution.Converged else 'not_converged',
                  'voltage_base_ln_v': base_ln_v, 'commands': commands,
                  'limitations': ['No hay transformadores, reguladores ni estimación de demanda.',
                                  'Cargas y capacitores se especifican por rama LN o LL; no hay reparto automático de bancos.',
                                  'Las matrices de línea representan el tramo completo; capacitancia en nF.',
                                  'Las potencias de terminal son positivas entrando al elemento.']}
        if not report['converged']:
            return report
        bus_results = []
        for name, bus in buses.items():
            mapped = {}
            if circuit.SetActiveBus(names[name]) >= 0:
                active = circuit.ActiveBus
                raw = list(active.Voltages)
                mapped = {int(node): complex(raw[2 * i], raw[2 * i + 1]) for i, node in enumerate(active.Nodes)}
            for phase in bus['phases']:
                voltage = mapped.get(PHASE_NODE[phase])
                bus_results.append({'bus': name, 'phase': phase,
                                    'source_path': (name, phase) in reached,
                                    'voltage_v': [voltage.real, voltage.imag] if voltage is not None else None,
                                    'magnitude_v': abs(voltage) if voltage is not None else None,
                                    'pu': abs(voltage) / base_ln_v if voltage is not None else None})
        report['buses'] = bus_results
        report['lines'] = [{'id': name, 'enabled': line['enabled'],
                            **(element_result(f'line.l{i}') if line['enabled'] else {})}
                           for i, (name, line) in enumerate(lines.items())]
        report['loads'] = [{'id': name, **element_result(f'load.d{i}')} for i, name in enumerate(loads)]
        report['capacitors'] = [{'id': name, 'enabled': cap['enabled'],
                                 **(element_result(f'capacitor.c{i}') if cap['enabled'] else {})}
                                for i, (name, cap) in enumerate(capacitors.items())]
        report['source'] = element_result('vsource.source')
        report['engine_losses_w_var'] = [float(v) for v in circuit.Losses]
        report['network_losses_w_var'] = [sum(item['net_power_absorbed_w_var'][i]
                                              for item in report['lines'] if item['enabled']) for i in (0, 1)]
        supply = [-sum(c[key] for c in report['source']['terminals'][0]['conductors']) for key in ('p_w', 'q_var')]
        consumed = [sum(c[key] for item in report['loads'] for c in item['terminals'][0]['conductors']) for key in ('p_w', 'q_var')]
        shunts = [sum(item['net_power_absorbed_w_var'][i] for item in report['capacitors'] if item['enabled']) for i in (0, 1)]
        report['power_balance'] = {'source_to_network_w_var': supply, 'loads_w_var': consumed,
                                   'capacitors_absorbed_w_var': shunts,
                                   'residual_w_var': [supply[i] - consumed[i] - shunts[i] - report['network_losses_w_var'][i] for i in (0, 1)]}
        checks = []
        for i, quantity in enumerate(('active', 'reactive')):
            residual = report['power_balance']['residual_w_var'][i]
            limit = max(0.001, abs(supply[i]) * 1e-6)
            if not math.isfinite(residual) or abs(residual) > limit:
                checks.append({'code': 'power_balance_mismatch', 'quantity': quantity,
                               'residual': residual, 'limit': limit})
        for item, expected in zip(report['loads'], loads.values()):
            for key in ('p_w', 'q_var'):
                actual = sum(c[key] for c in item['terminals'][0]['conductors'])
                if not math.isfinite(actual) or abs(actual - expected[key]) > max(0.01, abs(expected[key]) * 1e-6):
                    checks.append({'code': 'constant_power_mismatch', 'load': item['id'], 'quantity': key,
                                   'expected': expected[key], 'actual': actual})
        voltage_index = {(item['bus'], item['phase']): complex(*item['voltage_v'])
                         for item in bus_results if item['voltage_v'] is not None}
        for item, cap in zip(report['capacitors'], capacitors.values()):
            if not cap['enabled']:
                continue
            labels = terminal_phases(cap)
            terminal_voltage = voltage_index[(cap['bus'], labels[0])]
            if cap['connection'] == 'delta':
                terminal_voltage -= voltage_index[(cap['bus'], labels[1])]
            expected_q = -cap['rated_q_var'] * (abs(terminal_voltage) / cap['rated_voltage_v']) ** 2
            actual_p, actual_q = item['net_power_absorbed_w_var']
            item['terminal_voltage_v'] = abs(terminal_voltage)
            item['expected_q_absorbed_var'] = expected_q
            if abs(actual_q - expected_q) > max(0.01, abs(expected_q) * 1e-6) or abs(actual_p) > 0.01:
                checks.append({'code': 'capacitor_voltage_law_mismatch', 'capacitor': item['id'],
                               'expected_q_var': expected_q, 'actual_q_var': actual_q})
        for item in bus_results:
            if item['source_path'] and (item['pu'] is None or not math.isfinite(item['pu']) or not 0.01 <= item['pu'] <= 100):
                checks.append({'code': 'voltage_outside_supported_range', 'bus': item['bus'], 'phase': item['phase']})
        report['verification'] = {'passed': not checks, 'issues': checks,
                                  'basis': 'power_balance_and_specified_constant_power_not_cross_solver_equivalence'}
        if checks:
            report['status'] = 'invalid_results'
        magnitudes = [v['pu'] for v in bus_results if v['source_path'] and v['pu'] is not None]
        report['voltage_range_pu'] = {'min': min(magnitudes), 'max': max(magnitudes)} if magnitudes else None
        return report
    finally:
        engine.ClearAll()


def summarize(report):
    return {k: v for k, v in report.items() if k not in ('commands', 'buses', 'lines', 'loads', 'source', 'capacitors')}
