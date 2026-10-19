"""Resolución de catálogos y normalización provisional, sin simulación eléctrica."""
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

from .topology import DEVICES, TABLES as TOPOLOGY_TABLES, phases

LINE_TYPES = {
    1: ('CYMUNDERGROUNDLINE', 'CableId', 'CYMEQCABLE', 'sequence'),
    2: ('CYMOVERHEADLINE', 'LineId', 'CYMEQOVERHEADLINE', 'sequence'),
    23: ('CYMOVERHEADLINEUNBALANCED', 'LineId', 'CYMEQOVERHEADLINEUNBALANCED', 'phase'),
}
EQUIPMENT = {4: 'CYMEQREGULATOR', 8: 'CYMEQBREAKER', 10: 'CYMEQRECLOSER',
             13: 'CYMEQSWITCH', 14: 'CYMEQFUSE', 17: 'CYMEQSHUNTCAPACITOR'}
TABLES = TOPOLOGY_TABLES | set(EQUIPMENT.values()) | {
    'CYMCUSTOMERLOAD', 'CYMCONSUMERCLASS', 'CYMLOADMODEL', 'CYMEQSOURCE',
    'CYMEQOVERHEADLINE', 'CYMEQOVERHEADLINEUNBALANCED', 'CYMEQCABLE',
    'CYMEQCONDUCTOR', 'CYMEQOVERHEADSPACINGOFCOND',
    'CYMEQGEOMETRICALARRANGEMENT', 'CYMEQAVERAGEGEOARRANGEMENT'}


def number(value):
    if isinstance(value, dict) and set(value) == {'$decimal'}:
        value = float(value['$decimal'])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('missing_or_nonfinite_number')
    return float(value)


def load_profile(path):
    raw = Path(path).read_bytes()
    profile = json.loads(raw)
    if profile.get('authority') not in ('analyst-assumption', 'user-declared'):
        raise ValueError('La autoridad del perfil debe ser analyst-assumption o user-declared')
    supported = {'length_unit': {'m', 'km', 'ft', 'mi'},
                 'series_impedance_unit': {'ohm/km', 'ohm/mi'},
                 'shunt_admittance_unit': {'uS/km', 'uS/mi'},
                 'source_voltage_unit': {'kV_LN'},
                 'multiphase_load_value_scope': {'unverified'}}
    for field, accepted in supported.items():
        if profile.get(field) not in accepted:
            raise ValueError(f'Convención no admitida: {field}')
    if not profile.get('id') or not isinstance(profile.get('load_value_types'), dict):
        raise ValueError('Perfil incompleto')
    if any(mode not in ('kw_kvar', 'kw_pf_percent', 'kva_pf_percent')
           for mode in profile['load_value_types'].values()):
        raise ValueError('Tipo de carga no admitido')
    return {**profile, 'file_sha256': hashlib.sha256(raw).hexdigest()}


def pq(value1, value2, mode):
    a, b = number(value1), number(value2)
    if mode == 'kw_kvar':
        return {'p_w': a * 1000, 'q_var': b * 1000}
    if mode not in ('kw_pf_percent', 'kva_pf_percent'):
        raise ValueError('unmapped_load_value_type')
    # El signo capacitivo/generador requiere una convención adicional explícita.
    if a < 0 or not 0 < b <= 100:
        raise ValueError('unsupported_signed_power_or_power_factor')
    factor = b / 100
    p = a if mode == 'kw_pf_percent' else a * factor
    q = p * math.sqrt(max(0, 1 / factor ** 2 - 1))
    return {'p_w': p * 1000, 'q_var': q * 1000}


def normalized_line(equipment, length, kind, selected_phases, profile):
    length_m = number(length) * {'m': 1, 'km': 1000, 'ft': 0.3048, 'mi': 1609.344}[profile['length_unit']]
    if length_m <= 0:
        raise ValueError('nonpositive_line_length')
    z_scale = length_m / (1000 if profile['series_impedance_unit'] == 'ohm/km' else 1609.344)
    y_scale = length_m / (1000 if profile['shunt_admittance_unit'] == 'uS/km' else 1609.344) * 1e-6

    def z(r, x):
        resistance = number(equipment.get(r))
        if resistance < 0 and not r.startswith('Mutual'):
            raise ValueError('negative_series_resistance')
        return [resistance * z_scale, number(equipment.get(x)) * z_scale]

    def y(g, b):
        return [number(equipment.get(g)) * y_scale, number(equipment.get(b)) * y_scale]

    result = {'length_m': length_m, 'frequency_hz': number(equipment.get('Frequency')),
              'phases': sorted(selected_phases), 'complex_encoding': '[real, imaginary]',
              'parameter_authority': 'stored_catalog_under_declared_profile',
              'user_defined_impedances_raw': equipment.get('UserDefinedImpedances'),
              'lock_impedance_raw': equipment.get('LockImpedance')}
    if result['frequency_hz'] <= 0:
        raise ValueError('nonpositive_frequency')
    if kind == 'sequence':
        result['sequence_z_ohm'] = {s: z(prefix + 'SequenceResistance', prefix + 'SequenceReactance')
                                    for s, prefix in (('positive', 'Positive'), ('zero', 'Zero'))}
        result['sequence_y_siemens'] = {
            s: y(prefix + 'ShuntConductance', prefix + 'ShuntSusceptance')
            for s, prefix in (('positive', 'PosSeq'), ('zero', 'ZeroSequence'))}
        # No fabricar Zabc a partir de dos secuencias sin validar transposición/Z2.
        result['phase_matrix'] = None
    else:
        labels = sorted(selected_phases)
        pairs = {frozenset('AB'): 'AB', frozenset('BC'): 'BC', frozenset('AC'): 'CA'}
        result['z_phase_ohm'] = [
            [z('SelfResistance' + a, 'SelfReactance' + a) if a == b else
             z('MutualResistance' + pairs[frozenset((a, b))], 'MutualReactance' + pairs[frozenset((a, b))])
             for b in labels] for a in labels]
        result['y_catalog_entries_siemens'] = [
            [y('ShuntConductance' + a, 'ShuntSusceptance' + a) if a == b else
             y('MutualShuntConductance' + pairs[frozenset((a, b))],
               'MutualShuntSusceptance' + pairs[frozenset((a, b))]) for b in labels] for a in labels]
        result['shunt_matrix_convention'] = 'catalog_signs_preserved_not_verified_for_nodal_stamp'
        result['phase_matrix_assumption'] = 'reciprocal_mutual_entries'
    return result


def prepare(tables, profile=None):
    issues = []
    indexes = {}
    for table, rows in tables.items():
        index = defaultdict(list)
        for ordinal, row in enumerate(rows):
            if table.startswith('CYMEQ'):
                key = row.get('EquipmentId')
            elif table == 'CYMCONSUMERCLASS':
                key = row.get('ConsumerClassId')
            elif table == 'CYMLOADMODEL':
                key = row.get('LoadModelId')
            else:
                key = (row.get('NetworkId'), row.get('DeviceNumber'))
            index[key].append((ordinal, row))
        indexes[table] = index

    def issue(code, context, **extra):
        issues.append({'code': code, **context, **extra})

    def lookup(table, key, context):
        matches = indexes.get(table, {}).get(key, [])
        if len(matches) != 1:
            issue('missing_reference' if not matches else 'ambiguous_reference', context,
                  table=table, reference=key, matches=len(matches))
            return None
        ordinal, row = matches[0]
        return {'table': table, 'ordinal': ordinal, 'row': row}

    sections = defaultdict(list)
    for row in tables.get('CYMSECTION', []):
        sections[(row.get('NetworkId'), row.get('SectionId'))].append(row)
    attachments = defaultdict(list)
    for row in tables.get('CYMSECTIONDEVICE', []):
        attachments[(row.get('NetworkId'), row.get('DeviceNumber'), row.get('DeviceType'))].append(row)
    lines, devices, sources, loads = [], [], [], []
    for attachment in tables.get('CYMSECTIONDEVICE', []):
        net, device, code = attachment.get('NetworkId'), attachment.get('DeviceNumber'), attachment.get('DeviceType')
        context = {'network_id': net, 'device_id': device, 'device_type_code': code}
        base = {**context, 'section_id': attachment.get('SectionId'),
                'location_raw': attachment.get('Location')}
        if len(attachments[(net, device, code)]) != 1:
            issue('ambiguous_attachment', context)
        if code not in DEVICES:
            issue('unsupported_device_type', context)
            continue
        instance = lookup(DEVICES[code][0], (net, device), context)
        base['instance'] = instance
        if not instance:
            devices.append(base)
            continue
        row = instance['row']
        if code in LINE_TYPES:
            _, field, table, kind = LINE_TYPES[code]
            item = {**base, 'equipment': lookup(table, row.get(field), context), 'normalized_candidate': None}
            lines.append(item)
            if not item['equipment']:
                continue
            eq = item['equipment']['row']
            item['dependencies'] = []
            for key, identifier in eq.items():
                if key.startswith(('PhaseConductorId', 'NeutralConductorId')) and identifier not in (None, '', 'NONE'):
                    item['dependencies'].append({'field': key, 'target': lookup('CYMEQCONDUCTOR', identifier, context)})
            if code != 1:
                spacing = lookup('CYMEQOVERHEADSPACINGOFCOND', eq.get('ConductorSpacingId'), context)
                item['dependencies'].append({'field': 'ConductorSpacingId', 'target': spacing})
                if spacing:
                    for flag, geometry in [('HasGeometricalArrangement', 'CYMEQGEOMETRICALARRANGEMENT'),
                                           ('HasAverageGeoArrangement', 'CYMEQAVERAGEGEOARRANGEMENT')]:
                        if spacing['row'].get(flag) == 1:
                            item['dependencies'].append({'field': flag, 'target': lookup(geometry, eq.get('ConductorSpacingId'), context)})
            if eq.get('UserDefinedImpedances') != 1:
                issue('derived_impedance_requires_recomputation_or_validation', context)
            selected = sections.get((net, attachment.get('SectionId')), [])
            if len(selected) != 1:
                issue('missing_or_ambiguous_section', context)
            elif profile:
                try:
                    item['normalized_candidate'] = normalized_line(eq, row.get('Length'), kind,
                                                                  phases(selected[0].get('Phase')), profile)
                except ValueError as exc:
                    issue(str(exc), context)
        elif code in EQUIPMENT:
            devices.append({**base, 'equipment': lookup(EQUIPMENT[code], row.get('EquipmentId'), context)})
        # Las cargas se preparan desde sus registros por cliente, no como equipos.

    for row in tables.get('CYMSOURCE', []):
        context = {'network_id': row.get('NetworkId'), 'device_id': row.get('DeviceNumber')}
        item = {**context, 'raw': row, 'equipment': lookup('CYMEQSOURCE', row.get('EquipmentId'), context),
                'voltage_ln_v_candidate': None}
        sources.append(item)
        if profile:
            try:
                voltages = {p: number(row.get('OperatingVoltage' + p)) * 1000 for p in 'ABC'}
                if any(v < 0 for v in voltages.values()):
                    raise ValueError('negative_source_voltage')
                item['voltage_ln_v_candidate'] = voltages
            except ValueError as exc:
                issue(str(exc), context)

    for ordinal, row in enumerate(tables.get('CYMCUSTOMERLOAD', [])):
        context = {'network_id': row.get('NetworkId'), 'device_id': row.get('DeviceNumber'),
                   'customer_id': row.get('CustomerNumber'), 'load_ordinal': ordinal}
        links = attachments.get((row.get('NetworkId'), row.get('DeviceNumber'), row.get('DeviceType')), [])
        instance = lookup('CYMLOAD', (row.get('NetworkId'), row.get('DeviceNumber')), context)
        item = {**context, 'raw': row, 'attachments': links, 'instance': instance,
                'consumer_class': lookup('CYMCONSUMERCLASS', row.get('ConsumerClassId'), context),
                'load_model': lookup('CYMLOADMODEL', row.get('LoadModelId'), context),
                'pq_record_candidate': None, 'power_by_phase_label_candidate': None,
                'nodal_injection': None}
        loads.append(item)
        if len(links) != 1:
            issue('missing_or_ambiguous_load_attachment', context)
        if not profile:
            continue
        try:
            label = profile['load_value_types'].get(str(row.get('LoadValueType')))
            value = pq(row.get('LoadValue1'), row.get('LoadValue2'), label)
            selected_phases = sorted(phases(row.get('Phase')))
            item['phases'] = selected_phases
            item['pq_record_candidate'] = value
            if len(selected_phases) == 1:
                item['power_by_phase_label_candidate'] = {selected_phases[0]: value}
            else:
                issue('multiphase_load_scope_unverified', context)
            if len(links) == 1:
                load_sections = sections.get((row.get('NetworkId'), links[0].get('SectionId')), [])
                if len(load_sections) != 1:
                    issue('missing_or_ambiguous_load_section', context)
                elif not set(selected_phases).issubset(phases(load_sections[0].get('Phase'))):
                    issue('load_phases_not_in_section', context)
            if row.get('Status') != 0:
                issue('load_status_unverified', context)
        except ValueError as exc:
            issue(str(exc), context)

    for name in ('CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE'):
        if not tables.get(name):
            issue('empty_required_table', {}, table=name)
    frequencies = sorted({line['normalized_candidate']['frequency_hz'] for line in lines if line['normalized_candidate']})
    if len(frequencies) > 1:
        issue('mixed_line_frequencies', {}, values=frequencies)
    blockers = [
        'Perfil de unidades y códigos Access pendiente de contraste con una exportación contemporánea o API.',
        'Impedancias derivadas: verificar vigencia o recalcular desde geometría y conductores.',
        'Validar matrices de fase, reducción del neutro y signos de admitancias antes del ensamblado nodal.',
        'Resolver conexiones de cargas, reparto ABC, modelo ZIP y posición respecto de maniobras.',
        'Modelar reguladores delta, taps y controles; validar impedancia y ángulos de fuentes.',
        'Contrastar estados operativos y resultados con CYME en un caso reproducible.',
    ]
    if profile is None:
        blockers.insert(0, 'No se seleccionó perfil: se conservaron valores nativos sin conversión.')
    return {'analysis': 'electrical_preparation', 'status': 'candidate_not_solver_ready',
            'solver_ready': False, 'profile': profile, 'blockers': blockers,
            'summary': {'lines': len(lines), 'equipment_devices': len(devices), 'sources': len(sources),
                        'load_records': len(loads),
                        'normalized_line_candidates': sum(x['normalized_candidate'] is not None for x in lines),
                        'single_phase_load_candidates': sum(x['power_by_phase_label_candidate'] is not None for x in loads),
                        'frequency_hz_candidates': frequencies, 'issue_counts': dict(Counter(x['code'] for x in issues))},
            'issues': issues, 'lines': lines, 'devices': devices, 'sources': sources, 'loads': loads}


def summarize(report):
    return {k: v for k, v in report.items() if k not in ('issues', 'lines', 'devices', 'sources', 'loads', 'references')}


def compact(report):
    """Una sola copia por fila referenciada, con coordenada estable en el SQLite."""
    references = {}

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, dict):
            if set(value) == {'table', 'ordinal', 'row'}:
                key = f'{value["table"]}:{value["ordinal"]}'
                if key in references and references[key] != value:
                    raise ValueError(f'Referencia contradictoria: {key}')
                references[key] = value
                return {'reference_id': key}
            return {k: visit(v) for k, v in value.items()}
        return value

    result = visit(report)
    result['report_format'] = 'nandu-electrical-v1'
    result['references'] = references
    result['summary']['unique_referenced_rows'] = len(references)
    return result
