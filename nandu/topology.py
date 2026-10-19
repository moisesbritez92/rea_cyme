"""Conectividad condicional por fase. No calcula tensión ni flujo eléctrico.

Perfil CYME 9: fases 1..7 contrastadas con CymPhase en Cyme64.tlb.
Tipos Access contrastados por coincidencia de instancias con tablas de dispositivos.
Status=0 se admite como hipótesis explícita; cualquier otro estado bloquea el tramo.
"""
from collections import Counter, defaultdict, deque
import math

PHASES = {0: '', 1: 'A', 2: 'B', 3: 'C', 4: 'AB', 5: 'AC', 6: 'BC', 7: 'ABC'}
DEVICES = {
    1: ('CYMUNDERGROUNDLINE', 'series'),
    2: ('CYMOVERHEADLINE', 'series'),
    23: ('CYMOVERHEADLINEUNBALANCED', 'series'),
    4: ('CYMREGULATOR', 'series'),
    8: ('CYMBREAKER', 'switch'),
    10: ('CYMRECLOSER', 'switch'),
    13: ('CYMSWITCH', 'switch'),
    14: ('CYMFUSE', 'switch'),
    17: ('CYMSHUNTCAPACITOR', 'shunt'),
    20: ('CYMLOAD', 'shunt'),
}
TABLES = {'CYMNODE', 'CYMNODEINTER', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE',
          'CYMDBPARAMETERS', *[value[0] for value in DEVICES.values()]}


def phases(code, allow_empty=False):
    # bool y valores fraccionarios no son códigos admitidos.
    if type(code) is not int or code not in PHASES or (code == 0 and not allow_empty):
        raise ValueError(f'Código de fase no admitido: {code!r}')
    return set(PHASES[code])


def analyze(tables):
    issues, boundary_resolutions = [], {}

    def problem(code, **context):
        issues.append({'code': code, **context})

    for table in ('CYMNODE', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE'):
        if not tables.get(table):
            problem('empty_required_table', table=table)

    nodes = defaultdict(list)
    for row in tables.get('CYMNODE', []):
        nodes[(row.get('NetworkId'), row.get('NodeId'))].append(row)
    memberships = {(r.get('NetworkId'), r.get('NodeId')) for r in tables.get('CYMNODEINTER', [])}

    def resolve(net, node, connector=0):
        if connector != 0:
            raise ValueError('nonzero_connector_not_supported')
        local = nodes.get((net, node), [])
        shared = nodes.get((None, node), [])
        if len(local) == 1 and not shared:
            return (net, node)
        if not local and len(shared) == 1 and (net, node) in memberships:
            boundary_resolutions[(net, node)] = {'network_id': net, 'node_id': node,
                                                 'basis': 'CYMNODEINTER + unique unassigned CYMNODE'}
            return (None, node)
        raise ValueError('missing_or_ambiguous_node_membership')

    instance_indices = {}
    for table, role in DEVICES.values():
        index = defaultdict(list)
        for row in tables.get(table, []):
            index[(row.get('NetworkId'), row.get('DeviceNumber'))].append(row)
        instance_indices[table] = index
    attachments = defaultdict(list)
    device_counts = Counter()
    for row in tables.get('CYMSECTIONDEVICE', []):
        attachments[(row.get('NetworkId'), row.get('SectionId'))].append(row)
        device_counts[(row.get('NetworkId'), row.get('DeviceNumber'), row.get('DeviceType'))] += 1
    section_counts = Counter((r.get('NetworkId'), r.get('SectionId')) for r in tables.get('CYMSECTION', []))
    for key in attachments:
        if key not in section_counts:
            problem('attachment_without_section', network_id=key[0], section_id=key[1])

    adjacency = {p: defaultdict(list) for p in 'ABC'}
    present = {p: set() for p in 'ABC'}
    edges = {p: [] for p in 'ABC'}
    sections, switches = [], []
    for row in tables.get('CYMSECTION', []):
        net, sid = row.get('NetworkId'), row.get('SectionId')
        record = {'network_id': net, 'section_id': sid, 'state': 'excluded',
                  'physical_phases': [], 'conducting_phases': [], 'blocked_phases': []}
        sections.append(record)
        try:
            if section_counts[(net, sid)] != 1:
                raise ValueError('duplicate_section')
            physical = phases(row.get('Phase'))
            record['physical_phases'] = sorted(physical)
            a = resolve(net, row.get('FromNodeId'), row.get('FromNodeConnectorIndex', 0))
            b = resolve(net, row.get('ToNodeId'), row.get('ToNodeConnectorIndex', 0))
            if a == b:
                raise ValueError('self_loop')
            record['from_node'] = list(a)
            record['to_node'] = list(b)
            for phase in physical:
                present[phase].update((a, b))
            conducting = set(physical)
            devices = attachments.get((net, sid), [])
            if not devices:
                raise ValueError('section_without_devices')
            series_count = 0
            for device in devices:
                code, number = device.get('DeviceType'), device.get('DeviceNumber')
                if code not in DEVICES:
                    raise ValueError(f'unsupported_device_type:{code}')
                if device_counts[(net, number, code)] != 1:
                    raise ValueError(f'ambiguous_device_attachment:{number}')
                table, role = DEVICES[code]
                matches = instance_indices[table].get((net, number), [])
                if len(matches) != 1:
                    raise ValueError(f'missing_or_duplicate_instance:{table}:{number}')
                instance = matches[0]
                if role == 'shunt':
                    continue
                series_count += 1
                # No confundir Status con ClosedPhase ni con CymStatus de COM.
                if type(instance.get('Status')) is not int or instance['Status'] != 0:
                    raise ValueError(f'unverified_device_status:{table}:{number}:{instance.get("Status")}')
                if role == 'switch':
                    closed = phases(instance.get('ClosedPhase'), allow_empty=True)
                    conducting.intersection_update(closed)
                    switches.append({'network_id': net, 'section_id': sid, 'device_id': number,
                                     'table': table, 'closed_phases': sorted(closed),
                                     'effective_closed_phases': sorted(closed & physical),
                                     'normal_status_raw': instance.get('NormalStatus'),
                                     'status_raw': instance.get('Status')})
            if not series_count:
                raise ValueError('section_without_supported_series_device')
            record.update(state='included', conducting_phases=sorted(conducting),
                          blocked_phases=sorted(physical - conducting))
            for phase in conducting:
                edge_id = len(edges[phase])
                edges[phase].append((a, b, net, sid))
                adjacency[phase][a].append((b, edge_id))
                adjacency[phase][b].append((a, edge_id))
        except ValueError as exc:
            record['reason'] = str(exc)
            problem(str(exc), network_id=net, section_id=sid)

    sources = {p: defaultdict(list) for p in 'ABC'}
    for row in tables.get('CYMSOURCE', []):
        try:
            node = resolve(row.get('NetworkId'), row.get('NodeId'), row.get('ConnectorIndex', 0))
            label = [row.get('NetworkId'), row.get('DeviceNumber')]
            for phase in 'ABC':
                voltage = row.get('OperatingVoltage' + phase)
                if (not isinstance(voltage, (int, float)) or isinstance(voltage, bool)
                        or not math.isfinite(voltage) or voltage < 0):
                    problem('invalid_source_voltage', source=label, phase=phase)
                elif voltage > 0:
                    sources[phase][node].append(label)
                    present[phase].add(node)
        except ValueError as exc:
            problem(str(exc), source=row.get('DeviceNumber'))

    summaries, components, node_results = {}, {}, []
    for phase in 'ABC':
        unseen = set(present[phase])
        phase_components = []
        while unseen:
            root = min(unseen, key=lambda n: (n[0] or '', n[1]))
            queue, members, edge_ids = deque([root]), {root}, set()
            unseen.remove(root)
            while queue:
                current = queue.popleft()
                for nxt, eid in adjacency[phase].get(current, []):
                    edge_ids.add(eid)
                    if nxt not in members:
                        members.add(nxt)
                        unseen.discard(nxt)
                        queue.append(nxt)
            source_labels = sorted({tuple(label) for n in members for label in sources[phase].get(n, [])})
            component = {'id': len(phase_components), 'node_count': len(members),
                         'edge_count': len(edge_ids), 'cycle_rank': len(edge_ids) - len(members) + 1,
                         'sources': [list(s) for s in source_labels],
                         'reached_from_source': bool(source_labels)}
            phase_components.append(component)
            node_results.extend({'network_id': n[0], 'node_id': n[1], 'phase': phase,
                                 'component': component['id'], 'reached_from_source': bool(source_labels)}
                                for n in sorted(members, key=lambda n: (n[0] or '', n[1])))
        components[phase] = phase_components
        summaries[phase] = {'nodes': len(present[phase]), 'conducting_sections': len(edges[phase]),
                            'components': len(phase_components),
                            'reached_nodes': sum(c['node_count'] for c in phase_components if c['sources']),
                            'not_reached_nodes': sum(c['node_count'] for c in phase_components if not c['sources']),
                            'cycle_rank': sum(c['cycle_rank'] for c in phase_components),
                            'multi_source_components': sum(len(c['sources']) > 1 for c in phase_components)}
    coordinate_systems = sorted({r['ValueStr'] for r in tables.get('CYMDBPARAMETERS', [])
                                 if r.get('ID') == 'CoordinateSystem' and isinstance(r.get('ValueStr'), str)})
    return {'analysis': 'phase_connectivity', 'profile': 'cyme9-access-v1',
            'status': 'partial' if issues else 'complete_within_conditional_profile',
            'solver_ready': False, 'physical_energization_verified': False,
            'summary': {'sections': len(sections), 'excluded_sections': sum(s['state'] == 'excluded' for s in sections),
                        'switching_devices': len(switches),
                        'fully_open_devices': sum(not s['effective_closed_phases'] for s in switches),
                        'boundary_memberships_resolved': len(boundary_resolutions), 'phases': summaries},
            'coordinate_system_declarations': coordinate_systems,
            'assumptions': [
                'Status=0 en dispositivos serie se admite como ausencia de restricción adicional; semántica pendiente de contraste en vivo.',
                'ClosedPhase=0 se interpreta como ninguna fase cerrada; 1..7 usan la enumeración de fases, no bits.',
                'Fuentes con OperatingVoltage positivo se consideran disponibles en esa fase.',
                'Las etiquetas A/B/C se propagan sin modelar acoplamiento delta, impedancia, tensión ni controles.',
                'NormalStatus es informativo: el análisis usa ClosedPhase guardado en la base.',
            ],
            'limitations': ['No alcanzado significa sin camino en el grafo admitido, no ausencia física de tensión.',
                            'No se determina alimentación de cargas dentro de tramos con dispositivos abiertos.',
                            'No se modelan redes externas a la importación ni generación distribuida.',
                            'Los códigos/tipos no admitidos excluyen el tramo y producen cobertura parcial.'],
            'issues': issues, 'boundary_resolutions': list(boundary_resolutions.values()),
            'components': components, 'switching_devices': switches, 'sections': sections, 'nodes': node_results}


def summarize(report):
    result = {k: v for k, v in report.items()
            if k not in ('sections', 'nodes', 'switching_devices', 'boundary_resolutions', 'components')}
    if 'source_provenance' in result:
        result['source_provenance'] = {k: v for k, v in result['source_provenance'].items()
                                       if k != 'excluded_tables'}
    return result
