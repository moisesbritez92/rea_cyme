"""Exportación de un proyecto Ñandu a archivos CYME ASCII (red y equipos).

CYME los importa con Database > Import > CYME ASCII Files (o cympy.db.ImportASCII). Cada sección
del archivo declara sus columnas en una línea FORMAT_<SECCIÓN>=; Ñandu escribe solo las columnas que
sabe llenar, con los nombres tomados tal cual de archivos exportados por CYME 9.0 r4
(data/cyme-ascii-ref, 17 nov. 2026). Las conversiones de códigos Access a texto ASCII se
verificaron cruzando esas exportaciones con sus bases Access (ver CONVENTIONS).

Reglas:
- Una tabla, un tipo de equipo o un código sin correspondencia verificada hace fallar la
  exportación (ExportError con la lista); no se escribe un archivo que pierda red eléctrica.
- Las tablas solo gráficas o de parámetros sin formato ASCII verificado (CYMBUSDISPLAY,
  CYMDBPARAMETERS) se omiten y se informan; las ESQ_* son de Ñandu y no van a CYME.
- Columnas con datos que el formato no recibe se informan en dropped_columns.
- Antes de terminar, los archivos se releen y deben reproducir las filas escritas.
"""
from datetime import datetime
import math
from pathlib import Path

from . import __version__

EVIDENCE = ('Exportación ASCII de CYME 9.0 r4 (data/cyme-ascii-ref, 17 nov. 2026) cruzada con '
            'BD_Centro0926.mdb (75 redes) y bd_nuevo-1.mdb en solo lectura')
CONVENTIONS = [
    {'item': 'Archivo: UTF-8 sin BOM, fin de línea CRLF, [GENERAL] + [SI], valores con 6 decimales',
     'authority': 'verified', 'evidence': EVIDENCE},
    {'item': 'Nombres de columna de cada FORMAT_* escrito', 'authority': 'verified', 'evidence': EVIDENCE},
    {'item': 'NetworkType 0 = FEEDER=, 1 = SUBSTATION= (línea dentro de [SECTION], antes de sus tramos)',
     'authority': 'verified', 'evidence': EVIDENCE + ': 75 de 75 FEEDER con tipo 0; TEST-FEEDER tipo 1'},
    {'item': 'CoordSet=1 con coordenadas geográficas (UTM) en CoordX/CoordY',
     'authority': 'verified', 'evidence': EVIDENCE + ': 75 de 75 redes'},
    {'item': 'Phase y ClosedPhase en texto: 0 NONE, 1 A, 2 B, 3 C, 7 ABC',
     'authority': 'verified', 'evidence': EVIDENCE + ': 36.403 tramos y 1.331 maniobras'},
    {'item': 'Location de maniobras: 0 M, 1 S, 2 L', 'authority': 'verified',
     'evidence': EVIDENCE + ': interruptores, seccionadores, reconectadores, fusibles y reguladores'},
    {'item': 'Location de transformador (5) con el mismo texto que las maniobras', 'authority': 'hypothesis',
     'evidence': EVIDENCE + ': solo se observó 2 = L en un transformador'},
    {'item': 'Location de transformador de tres devanados (39) como número', 'authority': 'hypothesis',
     'evidence': EVIDENCE + ': bd_nuevo escribe 0 para Location 0; no hay otros casos'},
    {'item': 'NStatus = 1 - NormalStatus (Access 1 cerrado se escribe 0)', 'authority': 'verified',
     'evidence': EVIDENCE + ': 1.312 con NormalStatus 1 -> 0 y 18 con 0 -> 1'},
    {'item': 'ConnectionStatus = Status (0 conectado)', 'authority': 'verified',
     'evidence': EVIDENCE + ': solo se observó 0'},
    {'item': 'FromNodeID de las maniobras = NormalFeedingNodeId', 'authority': 'verified',
     'evidence': EVIDENCE + ': 1.330 de 1.330'},
    {'item': 'Cada CYMSOURCE es una cabecera ([HEADNODES]) con su mismo nodo', 'authority': 'verified',
     'evidence': EVIDENCE + ': 74 de 74 fuentes; también sxst_import'},
    {'item': 'SourceID = CYMSOURCE.EquipmentId; tensiones de operación en kV LN sin conversión',
     'authority': 'verified', 'evidence': EVIDENCE},
    {'item': 'Length, coordenadas, SeqNumber = PointIndex y equipos (KVLL = RatedVoltage, Amps = RatedCurrent, '
             'KVA = NominalRatingKVA, KVLLprim/KVLLsec, tensiones y capacidad de tres devanados) sin conversión',
     'authority': 'verified', 'evidence': EVIDENCE},
    {'item': 'TertiaryNodeID/TertiaryNodeIndex = CYMSECTIONNODE.NodeId/NodeConnectorIndex',
     'authority': 'verified', 'evidence': EVIDENCE + ': bd_nuevo, transformador TRANSFO y autotransformador 8'},
    {'item': 'CYME acepta un FORMAT_* con un subconjunto de columnas y completa el resto con sus valores por '
             'defecto', 'authority': 'hypothesis',
     'evidence': 'Ayuda de CYME (cymeasciifiles.htm): la importación es compatible con formatos anteriores. '
                 'Pendiente de contraste importando en CYME'},
]

PHASE = {0: 'NONE', 1: 'A', 2: 'B', 3: 'C', 7: 'ABC'}
LOCATION = {0: 'M', 1: 'S', 2: 'L'}
NETWORK = {0: 'FEEDER', 1: 'SUBSTATION'}
STATUS = {0: '0'}
NSTATUS = {1: '0', 0: '1'}
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
          'November', 'December')
# Tipos de equipo de sección escritos: código -> tabla Access.
DEVICE_TABLE = {2: 'CYMOVERHEADLINE', 5: 'CYMTRANSFORMER', 8: 'CYMBREAKER', 39: 'CYMTHREEWINDINGTRANSFORMER'}
# Tablas que se escriben (las demás con filas, salvo OMITTED y ESQ_*, hacen fallar la exportación).
EXPORTED = {'CYMNETWORK', 'CYMNODE', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMSOURCE', 'CYMHEADNODE',
            'CYMSECTIONNODE', 'CYMINTERMEDIATEPOINT', 'CYMEQBREAKER', 'CYMEQTRANSFORMER',
            'CYMEQTHREEWINDINGTRANSFORMER', *DEVICE_TABLE.values()}
OMITTED = {'CYMBUSDISPLAY': 'dibujo de barras: sin formato ASCII verificado (los nodos se exportan)',
           'CYMDBPARAMETERS': 'parámetros de la base (sistema de coordenadas): sin formato ASCII verificado'}
# Columnas Access consumidas por tabla (para informar las que tienen datos y no se escriben).
USED = {
    'CYMNETWORK': {'NetworkId', 'NetworkType'},
    'CYMNODE': {'NodeId', 'NetworkId', 'X', 'Y', 'UserDefinedBaseVoltage'},
    'CYMSECTION': {'SectionId', 'NetworkId', 'FromNodeId', 'ToNodeId', 'FromNodeConnectorIndex',
                   'ToNodeConnectorIndex', 'Phase'},
    'CYMSECTIONDEVICE': {'SectionId', 'NetworkId', 'DeviceNumber', 'DeviceType', 'Location'},
    'CYMSOURCE': {'DeviceNumber', 'NodeId', 'ConnectorIndex', 'NetworkId', 'EquipmentId',
                  'OperatingVoltageA', 'OperatingVoltageB', 'OperatingVoltageC'},
    'CYMHEADNODE': {'NetworkId', 'NodeId', 'ConnectorIndex'},
    'CYMSECTIONNODE': {'SectionId', 'NodeId', 'NodeConnectorIndex'},
    'CYMINTERMEDIATEPOINT': {'NetworkId', 'SectionId', 'PointIndex', 'X', 'Y'},
    'CYMOVERHEADLINE': {'DeviceNumber', 'NetworkId', 'LineId', 'Length', 'Status'},
    'CYMBREAKER': {'DeviceNumber', 'NetworkId', 'EquipmentId', 'ClosedPhase', 'NormalStatus', 'Status',
                   'NormalFeedingNodeId'},
    'CYMTRANSFORMER': {'DeviceNumber', 'DeviceType', 'NetworkId', 'EquipmentId', 'Status'},
    'CYMTHREEWINDINGTRANSFORMER': {'DeviceNumber', 'DeviceType', 'NetworkId', 'EquipmentId', 'Status'},
    'CYMEQBREAKER': {'EquipmentId', 'RatedVoltage', 'RatedCurrent'},
    'CYMEQTRANSFORMER': {'EquipmentId', 'NominalRatingKVA', 'PrimaryVoltageKVLL', 'SecondaryVoltageKVLL'},
    'CYMEQTHREEWINDINGTRANSFORMER': {'EquipmentId', 'PrimaryRatedCapacity', 'PrimaryVoltage', 'SecondaryVoltage',
                                     'TertiaryVoltage', 'Comments'},
}
# Sección ASCII -> (palabra FORMAT_, columnas). Nombres copiados de la exportación de CYME.
NET_FORMATS = {
    'NODE': ('NODE', ('NodeID', 'CoordX', 'CoordY', 'UserDefinedBaseVoltage')),
    'HEADNODES': ('HEADNODES', ('NodeID', 'NetworkID', 'ConnectorIndex')),
    'SOURCE': ('SOURCE', ('SourceID', 'DeviceNumber', 'NodeID', 'NetworkID', 'OperatingVoltageA',
                          'OperatingVoltageB', 'OperatingVoltageC')),
    'SECTION': ('SECTION', ('SectionID', 'FromNodeID', 'FromNodeIndex', 'ToNodeID', 'ToNodeIndex', 'Phase')),
    'OVERHEADLINE SETTING': ('OVERHEADLINESETTING', ('SectionID', 'DeviceNumber', 'LineCableID', 'Length',
                                                     'ConnectionStatus')),
    'BREAKER SETTING': ('BREAKERSETTING', ('SectionID', 'Location', 'EqID', 'DeviceNumber', 'ClosedPhase',
                                           'NStatus', 'FromNodeID', 'ConnectionStatus')),
    'TRANSFORMER SETTING': ('TRANSFORMERSETTING', ('SectionID', 'Location', 'EqID', 'DeviceNumber',
                                                   'ConnectionStatus')),
    'THREE WINDING TRANSFORMER SETTING': ('THREEWINDINGTRANSFORMERSETTING', (
        'SectionID', 'Location', 'EqID', 'DeviceNumber', 'TertiaryNodeID', 'TertiaryNodeIndex', 'ConnectionStatus')),
    'INTERMEDIATE NODES': ('INTERMEDIATENODE', ('SectionID', 'SeqNumber', 'CoordX', 'CoordY')),
}
NETWORK_FORMAT = ('NetworkID', 'HeadNodeID', 'CoordSet')
EQ_FORMATS = {
    'BREAKER': ('BREAKER', ('ID', 'Amps', 'KVLL')),
    'TRANSFORMER': ('TRANSFORMER', ('ID', 'KVA', 'KVLLprim', 'KVLLsec')),
    'THREE WINDING TRANSFORMER': ('THREEWINDINGTRANSFORMER', (
        'ID', 'PrimaryRatedCapacity', 'PrimaryVoltage', 'SecondaryVoltage', 'TertiaryVoltage', 'Comments')),
}


class ExportError(ValueError):
    def __init__(self, problems):
        self.problems = problems
        super().__init__('No se puede exportar a CYME ASCII sin perder datos: ' + '; '.join(problems[:10])
                         + (f' (y {len(problems) - 10} más)' if len(problems) > 10 else ''))


def _num(value):
    if value is None:
        return ''
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'valor numérico inválido: {value!r}')
    return f'{float(value):.6f}'


def _int(value):
    return '' if value is None else str(int(value))


def _text(value):
    return '' if value is None else str(value)


def _code(table, value, where, problems):
    if value in table:
        return table[value]
    problems.append(f'{where}: código {value!r} sin texto CYME verificado')
    return ''


def _header(source):
    now = datetime.now()
    date = f'{MONTHS[now.month - 1]} {now.day:02d}, {now.year} at {now:%H:%M:%S}'
    return ['[GENERAL]', f'DATE={date}', 'CYME_VERSION=9.0', 'CYME_REVISION=4',
            f'COMMENTS=Exportado por Nandu {__version__} desde {source}', '', '[SI]', '']


def _section(name, keyword, fields, rows):
    lines = [f'[{name}]', f'FORMAT_{keyword}=' + ','.join(fields)]
    lines += [','.join(r[f] for f in fields) for r in rows]
    return lines + ['']


def _check_tables(tables, problems):
    omitted, ignored = {}, []
    for name, rows in sorted(tables.items()):
        if name.startswith('ESQ_'):
            ignored.append(name)
        elif name in OMITTED:
            if rows:
                omitted[name] = {'rows': len(rows), 'reason': OMITTED[name]}
        elif name not in EXPORTED and rows:
            problems.append(f'tabla {name} ({len(rows)} filas) sin formato ASCII verificado')
    return omitted, ignored


def _dropped(tables):
    dropped = {}
    for name, used in USED.items():
        cols = sorted({k for r in tables.get(name, []) for k, v in r.items()
                       if k not in used and v not in (None, '')})
        if cols:
            dropped[name] = cols
    return dropped


class _Builder:
    def __init__(self, tables):
        self.t = tables
        self.problems = []
        self.warnings = []
        self.empty = {}

    def rows(self, name):
        return self.t.get(name, [])

    def _note_empty(self, section, rows):
        for f in rows[0] if rows else ():
            n = sum(1 for r in rows if r[f] == '')
            if n:
                self.empty.setdefault(section, {})[f] = n

    def nodes(self):
        return [{'NodeID': _text(r['NodeId']), 'CoordX': _num(r.get('X')), 'CoordY': _num(r.get('Y')),
                 'UserDefinedBaseVoltage': _num(r.get('UserDefinedBaseVoltage'))} for r in self.rows('CYMNODE')]

    def head_nodes(self):
        heads = self.rows('CYMHEADNODE')
        if not heads:  # Cada fuente es una cabecera en CYME (CONVENTIONS).
            heads = [{'NodeId': s['NodeId'], 'NetworkId': s['NetworkId'], 'ConnectorIndex': s.get('ConnectorIndex')}
                     for s in self.rows('CYMSOURCE')]
            if heads:
                self.warnings.append({'code': 'head_nodes_from_sources', 'count': len(heads),
                                      'detail': 'sin CYMHEADNODE: una cabecera por fuente'})
        return [{'NodeID': _text(h['NodeId']), 'NetworkID': _text(h['NetworkId']),
                 'ConnectorIndex': _int(h.get('ConnectorIndex') or 0)} for h in heads]

    def sources(self):
        out = []
        missing = [s['DeviceNumber'] for s in self.rows('CYMSOURCE') if not s.get('EquipmentId')]
        if missing:
            self.warnings.append({'code': 'source_without_equipment', 'devices': missing,
                                  'detail': 'SourceID vacío: sin equivalente de red (Z de Thévenin) en el proyecto'})
        for s in self.rows('CYMSOURCE'):
            out.append({'SourceID': _text(s.get('EquipmentId')), 'DeviceNumber': _text(s['DeviceNumber']),
                        'NodeID': _text(s['NodeId']), 'NetworkID': _text(s['NetworkId']),
                        **{f'OperatingVoltage{p}': _num(s.get(f'OperatingVoltage{p}')) for p in 'ABC'}})
        return out

    def networks(self, heads):
        by_net = {}
        for h in heads:
            by_net.setdefault(h['NetworkID'], []).append(h['NodeID'])
        sections = {}
        for s in self.rows('CYMSECTION'):
            sections.setdefault(s['NetworkId'], []).append(s)
        out = []
        for net in self.rows('CYMNETWORK'):
            nid = net['NetworkId']
            kind = _code(NETWORK, net.get('NetworkType'), f'red {nid}', self.problems)
            nodes = by_net.get(nid, [])
            head = nodes[0] if len(nodes) == 1 and kind == 'FEEDER' else ''
            if kind == 'FEEDER' and len(nodes) != 1:
                self.warnings.append({'code': 'feeder_head_node_ambiguous', 'network': nid, 'head_nodes': len(nodes),
                                      'detail': 'HeadNodeID vacío: la red no tiene una sola cabecera'})
            rows = []
            for s in sections.pop(nid, []):
                where = f'tramo {s["SectionId"]}'
                rows.append({'SectionID': _text(s['SectionId']), 'FromNodeID': _text(s['FromNodeId']),
                             'FromNodeIndex': _int(s.get('FromNodeConnectorIndex') or 0),
                             'ToNodeID': _text(s['ToNodeId']), 'ToNodeIndex': _int(s.get('ToNodeConnectorIndex') or 0),
                             'Phase': _code(PHASE, s.get('Phase'), where, self.problems)})
                if s.get('FromNodeConnectorIndex') or s.get('ToNodeConnectorIndex'):
                    self.problems.append(f'{where}: índice de conector ≠ 0 sin CYMNODECONNECTOR en ASCII verificado')
            out.append((kind, {'NetworkID': _text(nid), 'HeadNodeID': head, 'CoordSet': '1'}, rows))
        for nid in sections:
            self.problems.append(f'red {nid}: tramos sin fila en CYMNETWORK')
        return out

    def devices(self):
        by_table = {name: {(r['NetworkId'], r['DeviceNumber']): r for r in self.rows(name)
                           if name != 'CYMTRANSFORMER' or r.get('DeviceType') in (None, 5)}
                    for name in DEVICE_TABLE.values()}
        tertiary = {r['SectionId']: r for r in self.rows('CYMSECTIONNODE')}
        out = {k: [] for k in ('OVERHEADLINE SETTING', 'BREAKER SETTING', 'TRANSFORMER SETTING',
                               'THREE WINDING TRANSFORMER SETTING')}
        for d in self.rows('CYMSECTIONDEVICE'):
            code, sid, num = d.get('DeviceType'), d['SectionId'], d['DeviceNumber']
            where = f'equipo {num} ({code}) en {sid}'
            if code not in DEVICE_TABLE:
                self.problems.append(f'{where}: tipo de equipo sin formato ASCII verificado')
                continue
            row = by_table[DEVICE_TABLE[code]].get((d['NetworkId'], num))
            if row is None:
                self.problems.append(f'{where}: falta su fila en {DEVICE_TABLE[code]}')
                continue
            status = _code(STATUS, row.get('Status'), where, self.problems)
            base = {'SectionID': _text(sid), 'DeviceNumber': _text(num), 'ConnectionStatus': status}
            if code == 2:
                out['OVERHEADLINE SETTING'].append({**base, 'LineCableID': _text(row.get('LineId')),
                                                    'Length': _num(row.get('Length'))})
                continue
            if code == 39:
                node = tertiary.get(sid)
                if node is None:
                    self.problems.append(f'{where}: sin nodo terciario en CYMSECTIONNODE')
                    continue
                out['THREE WINDING TRANSFORMER SETTING'].append({
                    **base, 'Location': _int(d.get('Location')), 'EqID': _text(row.get('EquipmentId')),
                    'TertiaryNodeID': _text(node['NodeId']), 'TertiaryNodeIndex': _int(node.get('NodeConnectorIndex') or 0)})
                continue
            base.update(Location=_code(LOCATION, d.get('Location'), where, self.problems),
                        EqID=_text(row.get('EquipmentId')))
            if code == 5:
                out['TRANSFORMER SETTING'].append(base)
                continue
            out['BREAKER SETTING'].append({**base, 'ClosedPhase': _code(PHASE, row.get('ClosedPhase'), where, self.problems),
                                           'NStatus': _code(NSTATUS, row.get('NormalStatus'), where, self.problems),
                                           'FromNodeID': _text(row.get('NormalFeedingNodeId'))})
        return out

    def intermediate(self):
        return [{'SectionID': _text(r['SectionId']), 'SeqNumber': _int(r['PointIndex']), 'CoordX': _num(r['X']),
                 'CoordY': _num(r['Y'])}
                for r in sorted(self.rows('CYMINTERMEDIATEPOINT'), key=lambda r: (r['SectionId'], r['PointIndex']))]

    def equipment(self):
        return {
            'BREAKER': [{'ID': _text(r['EquipmentId']), 'Amps': _num(r.get('RatedCurrent')),
                         'KVLL': _num(r.get('RatedVoltage'))} for r in self.rows('CYMEQBREAKER')],
            'TRANSFORMER': [{'ID': _text(r['EquipmentId']), 'KVA': _num(r.get('NominalRatingKVA')),
                             'KVLLprim': _num(r.get('PrimaryVoltageKVLL')),
                             'KVLLsec': _num(r.get('SecondaryVoltageKVLL'))} for r in self.rows('CYMEQTRANSFORMER')],
            'THREE WINDING TRANSFORMER': [{
                'ID': _text(r['EquipmentId']), 'PrimaryRatedCapacity': _num(r.get('PrimaryRatedCapacity')),
                **{f'{w}Voltage': _num(r.get(f'{w}Voltage')) for w in ('Primary', 'Secondary', 'Tertiary')},
                'Comments': _text(r.get('Comments'))} for r in self.rows('CYMEQTHREEWINDINGTRANSFORMER')],
        }

    def references(self, devices, equipment):
        known = {name: {r['ID'] for r in rows} for name, rows in equipment.items()}
        for section, catalog in (('BREAKER SETTING', 'BREAKER'), ('TRANSFORMER SETTING', 'TRANSFORMER'),
                                 ('THREE WINDING TRANSFORMER SETTING', 'THREE WINDING TRANSFORMER')):
            missing = sorted({r['EqID'] for r in devices[section] if r['EqID'] and r['EqID'] not in known[catalog]})
            if missing:
                self.warnings.append({'code': 'equipment_not_in_file', 'section': section, 'equipment': missing,
                                      'detail': 'debe existir en la base CYME destino'})


def _check_values(sections):
    bad = []
    for name, rows in sections.items():
        for r in rows:
            for f, v in r.items():
                if any(c in v for c in ',\r\n'):
                    bad.append(f'[{name}] {f}={v!r}: contiene coma o salto de línea')
    return bad


def read(path):
    """Lee un archivo CYME ASCII: {sección: [filas como dict]}; las redes van en '__networks__'."""
    sections, formats, current = {}, {}, None
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if not line or current is None:
            continue
        head, _, rest = line.partition('=')
        if head.startswith('FORMAT_') and _:
            formats[(current, head[7:])] = rest.split(',')
            continue
        if _ and (current, head) in formats:
            sections.setdefault('__networks__', []).append(
                {'__kind__': head, **dict(zip(formats[(current, head)], rest.split(',')))})
            continue
        keys = [k for (s, k) in formats if s == current and k not in ('FEEDER', 'SUBSTATION')]
        if not keys:
            sections[current].append(line)
            continue
        values = line.split(',')
        fields = formats[(current, keys[0])]
        if len(values) != len(fields):
            raise ValueError(f'{path}: [{current}] fila con {len(values)} valores y {len(fields)} columnas')
        sections[current].append(dict(zip(fields, values)))
    return sections


def _verify(path, expected, networks=()):
    got = read(path)
    problems = []
    for name, rows in expected.items():
        if got.get(name, []) != rows:
            problems.append(f'[{name}] no se relee igual')
    if networks:
        want = [{'__kind__': k, **n} for k, n, _ in networks]
        if got.get('__networks__', []) != want:
            problems.append('líneas de red no se releen igual')
    if problems:
        raise ExportError(problems)


def build(tables):
    """Arma las filas de los dos archivos; falla si algo no tiene correspondencia verificada."""
    b = _Builder(tables)
    omitted, ignored = _check_tables(tables, b.problems)
    heads = b.head_nodes()
    net = {'NODE': b.nodes(), 'HEADNODES': heads, 'SOURCE': b.sources()}
    networks = b.networks(heads)
    devices = b.devices()
    equipment = b.equipment()
    b.references(devices, equipment)
    net.update(devices)
    net['INTERMEDIATE NODES'] = b.intermediate()
    sections = {**net, 'SECTION': [r for _, _, rows in networks for r in rows], **equipment}
    b.problems += _check_values(sections)
    b.problems += _check_values({'red': [n for _, n, _ in networks]})
    if b.problems:
        raise ExportError(b.problems)
    for name, rows in sections.items():
        b._note_empty(name, rows)
    return {'net': net, 'networks': networks, 'equipment': equipment, 'omitted': omitted, 'ignored': ignored,
            'warnings': b.warnings, 'empty': b.empty, 'dropped': _dropped(tables)}


def _net_lines(source, plan):
    lines = _header(source)
    for name in ('NODE', 'HEADNODES', 'SOURCE'):
        lines += _section(name, *NET_FORMATS[name], plan['net'][name])
    keyword, fields = NET_FORMATS['SECTION']
    lines += ['[SECTION]', f'FORMAT_{keyword}=' + ','.join(fields)]
    for kind in sorted({k for k, _, _ in plan['networks']}):
        lines.append(f'FORMAT_{kind}=' + ','.join(NETWORK_FORMAT))
    for kind, network, rows in plan['networks']:
        lines.append(f'{kind}=' + ','.join(network[f] for f in NETWORK_FORMAT))
        lines += [','.join(r[f] for f in fields) for r in rows]
    lines.append('')
    for name in ('OVERHEADLINE SETTING', 'BREAKER SETTING', 'TRANSFORMER SETTING',
                 'THREE WINDING TRANSFORMER SETTING', 'INTERMEDIATE NODES'):
        if plan['net'][name]:
            lines += _section(name, *NET_FORMATS[name], plan['net'][name])
    return lines


def _eq_lines(source, plan):
    lines = _header(source)
    for name, (keyword, fields) in EQ_FORMATS.items():
        if plan['equipment'][name]:
            lines += _section(name, keyword, fields, plan['equipment'][name])
    return lines


def _write(path, lines):
    with open(path, 'x', encoding='utf-8', newline='\r\n') as f:
        f.write('\n'.join(lines) + '\n')


def outputs(prefix):
    prefix = Path(prefix)
    return prefix.with_name(prefix.name + '_red.txt'), prefix.with_name(prefix.name + '_equipos.txt')


def export(tables, prefix, source='proyecto'):
    """Escribe <prefijo>_red.txt y <prefijo>_equipos.txt; devuelve el informe."""
    net_path, eq_path = outputs(prefix)
    for path in (net_path, eq_path):
        if path.exists():
            raise FileExistsError(f'El destino ya existe: {path}')
    plan = build(tables)
    _write(net_path, _net_lines(source, plan))
    _write(eq_path, _eq_lines(source, plan))
    net_sections = {k: v for k, v in plan['net'].items() if v or k in ('NODE', 'HEADNODES', 'SOURCE')}
    _verify(net_path, {**net_sections, 'SECTION': [r for _, _, rows in plan['networks'] for r in rows]},
            plan['networks'])
    _verify(eq_path, {k: v for k, v in plan['equipment'].items() if v})
    counts = {**{k: len(v) for k, v in plan['net'].items()},
              'SECTION': sum(len(rows) for _, _, rows in plan['networks']),
              **{f'equipment:{k}': len(v) for k, v in plan['equipment'].items()}}
    return {'format': 'cyme-ascii', 'nandu_version': __version__, 'source': source,
            'network_file': str(net_path), 'equipment_file': str(eq_path),
            'networks': [{'NetworkID': n['NetworkID'], 'kind': k, 'HeadNodeID': n['HeadNodeID'], 'sections': len(r)}
                         for k, n, r in plan['networks']],
            'counts': counts, 'warnings': plan['warnings'], 'empty_values': plan['empty'],
            'omitted_tables': plan['omitted'], 'ignored_tables': plan['ignored'],
            'dropped_columns': plan['dropped'], 'conventions': CONVENTIONS,
            'import_in_cyme': 'Database > Import > CYME ASCII Files (red y equipos), en una base nueva'}
