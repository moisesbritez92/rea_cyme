"""Modelo estructural independiente; los códigos CYME se conservan sin reinterpretar."""
from collections import Counter
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Node:
    network_id: str | None
    id: str
    x_native: object
    y_native: object


@dataclass(frozen=True)
class Section:
    network_id: str
    id: str
    from_node_id: str
    to_node_id: str
    phase_code: object


@dataclass(frozen=True)
class Attachment:
    network_id: str
    device_id: str
    section_id: str
    device_type_code: object
    location_code: object


def build_model(tables):
    """Conserva filas repetidas y valores nulos para que la auditoría los detecte."""
    nodes = [asdict(Node(r.get('NetworkId'), r.get('NodeId'), r.get('X'), r.get('Y')))
             for r in tables.get('CYMNODE', [])]
    sections = [asdict(Section(r.get('NetworkId'), r.get('SectionId'), r.get('FromNodeId'),
                              r.get('ToNodeId'), r.get('Phase')))
                for r in tables.get('CYMSECTION', [])]
    attachments = [asdict(Attachment(r.get('NetworkId'), r.get('DeviceNumber'), r.get('SectionId'),
                                    r.get('DeviceType'), r.get('Location')))
                   for r in tables.get('CYMSECTIONDEVICE', [])]
    return {'networks': [r.get('NetworkId') for r in tables.get('CYMNETWORK', [])],
            'nodes': nodes, 'sections': sections, 'attachments': attachments,
            'sources': tables.get('CYMSOURCE', []),
            'loads': tables.get('CYMCUSTOMERLOAD', []),
            'semantics': {'coordinates': 'source_native_crs_unverified',
                          'phase_codes': 'cyme_native_unmapped',
                          'topology': 'structural_not_energized',
                          'electrical_units': 'source_native_unverified'}}


def validate(model):
    """Auditoría estructural, no certificación de aptitud para flujo de carga."""
    issues = []

    def issue(code, severity='error', **context):
        issues.append({'severity': severity, 'code': code, **context})

    networks = set(model['networks'])
    for net, count in Counter(model['networks']).items():
        if not isinstance(net, str) or not net.strip():
            issue('invalid_network_id', network_id=net)
        if count > 1:
            issue('duplicate_network', network_id=net, count=count)
    indices = {}
    for kind in ('nodes', 'sections'):
        counts = Counter((r['network_id'], r['id']) for r in model[kind])
        indices[kind] = set(counts)
        for (net, entity), count in counts.items():
            if not isinstance(entity, str) or not entity.strip():
                issue('invalid_id', kind=kind, network_id=net, id=entity)
            if count > 1:
                issue('duplicate_id', kind=kind, network_id=net, id=entity, count=count)
            if net not in networks:
                issue('boundary_node' if kind == 'nodes' else 'unknown_network',
                      severity='warning' if kind == 'nodes' else 'error',
                      kind=kind, network_id=net, id=entity)
    for row in model['sections']:
        for field in ('from_node_id', 'to_node_id'):
            if (row['network_id'], row[field]) not in indices['nodes']:
                candidates = [n for n in model['nodes'] if n['id'] == row[field]]
                issue('boundary_endpoint_requires_mapping' if candidates else 'missing_endpoint',
                      severity='warning' if candidates else 'error',
                      section=row['id'], network_id=row['network_id'], field=field,
                      node_id=row[field], candidate_network_ids=[n['network_id'] for n in candidates])
        if row['from_node_id'] == row['to_node_id']:
            issue('self_loop', network_id=row['network_id'], section=row['id'])
    attachment_counts = Counter()
    attached = set()
    for row in model['attachments']:
        key = (row['network_id'], row['device_id'], row['device_type_code'])
        attachment_counts[key] += 1
        attached.add(key)
        if (row['network_id'], row['section_id']) not in indices['sections']:
            issue('missing_section', **row)
    for (net, device, kind), count in attachment_counts.items():
        if count > 1:
            issue('ambiguous_attachment', network_id=net, device_id=device,
                  device_type_code=kind, count=count)
    for source in model['sources']:
        if (source.get('NetworkId'), source.get('NodeId')) not in indices['nodes']:
            issue('missing_source_node', network_id=source.get('NetworkId'),
                  device_id=source.get('DeviceNumber'), node_id=source.get('NodeId'))
    for load in model['loads']:
        if (load.get('NetworkId'), load.get('DeviceNumber'), load.get('DeviceType')) not in attached:
            issue('unattached_load', network_id=load.get('NetworkId'),
                  device_id=load.get('DeviceNumber'), customer=load.get('CustomerNumber'))
    errors = sum(i['severity'] == 'error' for i in issues)
    return {'scope': 'structural',
            'status': 'issues_found' if errors else ('review_required' if issues else 'passed'),
            'error_count': errors, 'warning_count': len(issues) - errors,
            'solver_ready': False, 'issues': issues,
            'pending': ['Mapear códigos de fases, dispositivos y estados.',
                        'Verificar unidades, bases eléctricas y sistema de coordenadas.',
                        'Resolver referencias de equipos y parámetros eléctricos.',
                        'Construir la topología energizada por fase y validar el motor.']}
