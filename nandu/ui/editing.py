"""Edición de red sobre las tablas CYME en memoria: diario de cambios y deshacer.

Las ediciones trabajan sobre filas con las mismas columnas que Access, para que la red editada
siga siendo un proyecto Ñandu válido y se recalcule con `cyme_model.build`. Las filas nuevas se
clonan de una fila existente del mismo tipo (columnas y valores por defecto de la propia base), y
el diario registra qué plantilla se usó.
"""
from decimal import Decimal, InvalidOperation
import math
import re

from ..cyme_model import METER_AMP_PF, METER_FIELDS, check_meter_reading
from ..topology import DEVICES, PHASES

LINE_TYPES = {1: ('CYMUNDERGROUNDLINE', 'CableId', 'CYMEQCABLE'),
              2: ('CYMOVERHEADLINE', 'LineId', 'CYMEQOVERHEADLINE'),
              23: ('CYMOVERHEADLINEUNBALANCED', 'LineId', 'CYMEQOVERHEADLINEUNBALANCED')}
ADDABLE = {13: 'Switch', 14: 'Fuse', 8: 'Breaker', 10: 'Recloser', 4: 'Regulator', 17: 'ShuntCapacitor', 20: 'SpotLoad'}
SWITCHING = {8, 10, 13, 14}
# Columna de catálogo de cada tabla de instancias (las mismas que lee cyme_model).
REFERENCES = {
    **{(table, field): (catalog, 'EquipmentId') for table, field, catalog in LINE_TYPES.values()},
    ('CYMSWITCH', 'EquipmentId'): ('CYMEQSWITCH', 'EquipmentId'),
    ('CYMFUSE', 'EquipmentId'): ('CYMEQFUSE', 'EquipmentId'),
    ('CYMBREAKER', 'EquipmentId'): ('CYMEQBREAKER', 'EquipmentId'),
    ('CYMRECLOSER', 'EquipmentId'): ('CYMEQRECLOSER', 'EquipmentId'),
    ('CYMREGULATOR', 'EquipmentId'): ('CYMEQREGULATOR', 'EquipmentId'),
    ('CYMSHUNTCAPACITOR', 'EquipmentId'): ('CYMEQSHUNTCAPACITOR', 'EquipmentId'),
    ('CYMCUSTOMERLOAD', 'ConsumerClassId'): ('CYMCONSUMERCLASS', 'ConsumerClassId'),
}
# Códigos verificados (docs/cyme-ingenieria-inversa.md); el resto de enteros se edita como número.
ENUMS = {
    'Phase': [(k, v) for k, v in PHASES.items() if k],
    'ClosedPhase': [(0, 'None')] + [(k, v) for k, v in PHASES.items() if k],
    'Location': [(0, 'Middle'), (1, 'From'), (2, 'To')],
    'ConnectionConfiguration': [(0, 'Yg'), (1, 'Y'), (2, 'D'), (3, 'OpenDelta'), (4, 'ClosedDelta')],
    'SettingOption': [(0, 'LoadCenter'), (1, 'RXSettings'), (2, 'FixedTap'), (3, 'Terminal'), (4, 'FixedTapPrimary'),
                      (5, 'PythonScript')],
    'LoadValueType': [(0, 'KW_KVAR')],
}
INSTANCE_TABLES = {table for table, _ in DEVICES.values()}
CATALOG_TABLES = {catalog for catalog, _ in REFERENCES.values()}
KEYS = {'CYMSECTION': ('SectionId',), 'CYMNODE': ('NodeId',), 'CYMSECTIONDEVICE': ('SectionId', 'DeviceType', 'DeviceNumber'),
        'CYMCUSTOMERLOAD': ('NetworkId', 'DeviceNumber', 'CustomerNumber'),
        'CYMNETWORKMETER': ('NetworkId', 'NodeId'),
        **{t: ('NetworkId', 'DeviceNumber') for t in INSTANCE_TABLES},
        **{t: ('ConsumerClassId' if t == 'CYMCONSUMERCLASS' else 'EquipmentId',) for t in CATALOG_TABLES}}
# Identidad y conectividad no se cambian con «set»: tienen operaciones propias.
LOCKED = {'NetworkId', 'SectionId', 'NodeId', 'DeviceNumber', 'DeviceType', 'CustomerNumber', 'FromNodeId',
          'ToNodeId', 'ComponentMask', 'Flags', 'LastChange', 'FromNodeConnectorIndex', 'ToNodeConnectorIndex'}
NAME = re.compile(r'^[^\s\\/"\'<>]{1,64}$')
METER_EDITABLE = {f for fields in METER_FIELDS.values() for f in fields}


class EditError(ValueError):
    pass


def editable(table, field):
    if table == 'CYMSECTIONDEVICE':
        return field == 'Location'
    if table == 'CYMNETWORKMETER':
        # Solo las lecturas A-FP que usan el estimador y Load Allocation; el resto no se modela.
        return field in METER_EDITABLE
    if field in KEYS.get(table, ()):
        return False
    return table in KEYS and field not in LOCKED


def options(tables, table, field):
    """Valores admitidos de un campo con dominio conocido: enumeración verificada o catálogo."""
    if (table, field) in REFERENCES:
        catalog, key = REFERENCES[(table, field)]
        return [(r[key], r[key]) for r in sorted(tables.get(catalog, []), key=lambda r: str(r[key]))]
    return ENUMS.get(field)


def _sample(rows, field):
    return next((r[field] for r in rows if r.get(field) is not None), None)


def coerce(value, old, sample=None):
    """Convertir lo escrito en la interfaz al tipo que ya tiene la columna."""
    kind = old if old is not None else sample
    if value is None or (isinstance(value, str) and value.strip() == '' and not isinstance(kind, str)):
        return None
    if isinstance(kind, bool):
        raise EditError('Campo booleano no editable')
    if isinstance(kind, dict) and set(kind) == {'$decimal'}:
        try:
            return {'$decimal': str(Decimal(str(value).strip().replace(',', '.')))}
        except InvalidOperation:
            raise EditError(f'Número no válido: {value!r}') from None
    if isinstance(kind, (int, float)):
        try:
            number = float(str(value).strip().replace(',', '.'))
        except ValueError:
            raise EditError(f'Número no válido: {value!r}') from None
        if not math.isfinite(number):
            raise EditError(f'Número no válido: {value!r}')
        if isinstance(kind, int):
            if number != int(number):
                raise EditError(f'Se esperaba un entero: {value!r}')
            return int(number)
        return number
    return str(value)


class Editor:
    """Aplica operaciones atómicas sobre `tables`; cada una se puede deshacer."""

    def __init__(self, tables):
        self.tables = tables
        self.journal = []
        self._undo = []
        self._snap = None

    # --- Infraestructura --------------------------------------------------------------------
    def apply(self, op):
        handler = getattr(self, '_op_' + str(op.get('op')), None)
        if handler is None:
            raise EditError(f'Operación de edición desconocida: {op.get("op")}')
        self._snap = {}
        try:
            entry = handler(op)
        except Exception:
            self._restore(self._snap)
            raise
        finally:
            snap, self._snap = self._snap, None
        self._undo.append(snap)
        self.journal.append(entry)
        return entry

    def undo(self):
        if not self._undo:
            raise EditError('No hay cambios para deshacer')
        self._restore(self._undo.pop())
        return self.journal.pop()

    def _restore(self, snap):
        for table, rows in snap.items():
            if rows is None:
                self.tables.pop(table, None)
            else:
                self.tables[table] = rows

    def _rows(self, table):
        """Filas de una tabla para modificar; la primera vez en la operación guarda una copia."""
        if table not in self._snap:
            self._snap[table] = [dict(r) for r in self.tables[table]] if table in self.tables else None
        return self.tables.setdefault(table, [])

    def _find(self, table, key):
        names = KEYS.get(table)
        if names is None or set(key) != set(names):
            raise EditError(f'Clave no válida para {table}: {sorted(key)}')
        rows = [r for r in self.tables.get(table, []) if all(r.get(k) == key[k] for k in names)]
        if not rows:
            raise EditError(f'Fila inexistente en {table}: {key}')
        return rows

    def _section(self, sid):
        sec = next((r for r in self.tables.get('CYMSECTION', []) if r['SectionId'] == sid), None)
        if sec is None:
            raise EditError(f'Tramo inexistente: {sid}')
        return sec

    def _attachments(self, sid):
        return [r for r in self.tables.get('CYMSECTIONDEVICE', []) if r['SectionId'] == sid]

    def _template(self, table, network=None, where=None):
        rows = [r for r in self.tables.get(table, []) if where is None or where(r)]
        row = next((r for r in rows if r.get('NetworkId') == network), rows[0] if rows else None)
        if row is None:
            raise EditError(f'No hay ninguna fila de {table} para usar como plantilla')
        return row

    def _check_new_id(self, ident, used, what):
        if not isinstance(ident, str) or not NAME.match(ident):
            raise EditError(f'Identificador de {what} no válido: {ident!r}')
        if ident in used:
            raise EditError(f'Ya existe {what} {ident}')
        return ident

    def _auto_id(self, prefix, used):
        n = 1
        while f'{prefix}{n}' in used:
            n += 1
        return f'{prefix}{n}'

    def _device_numbers(self, code):
        table = DEVICES[code][0]
        return ({r['DeviceNumber'] for r in self.tables.get('CYMSECTIONDEVICE', []) if r['DeviceType'] == code}
                | {r['DeviceNumber'] for r in self.tables.get(table, [])})

    def _check_reference(self, table, field, value):
        if (table, field) in REFERENCES and value is not None:
            catalog, key = REFERENCES[(table, field)]
            if not any(r.get(key) == value for r in self.tables.get(catalog, [])):
                raise EditError(f'{value!r} no existe en el catálogo {catalog}')
        allowed = ENUMS.get(field)
        if allowed and table not in CATALOG_TABLES and value is not None and value not in {k for k, _ in allowed}:
            raise EditError(f'Código no admitido para {field}: {value!r}')

    def _check_meter(self, row):
        """La fila resultante debe seguir siendo una lectura A-FP que el cálculo admite."""
        if row.get('DemandType') != METER_AMP_PF or row.get('IsTotalDemand'):
            raise EditError(f'Medidor de {row.get("NetworkId")}: DemandType {row.get("DemandType")} no editable '
                            '(solo A-FP por fase)')
        for p, (f_amp, f_pf) in METER_FIELDS.items():
            amps, pf = row.get(f_amp), row.get(f_pf)
            try:
                check_meter_reading(f'Medidor de {row.get("NetworkId")}', p, 0.0 if amps is None else amps,
                                    0.0 if pf is None else pf)
            except ValueError as exc:
                raise EditError(str(exc)) from None

    # --- Operaciones ------------------------------------------------------------------------
    def _op_batch(self, op):
        """Varias operaciones que se aplican y se deshacen juntas (p. ej. un grupo de propiedades)."""
        steps = op.get('ops') or []
        if not steps:
            raise EditError('No hay cambios')
        entries = []
        for step in steps:
            handler = getattr(self, '_op_' + str(step.get('op')), None)
            if handler is None or step.get('op') == 'batch':
                raise EditError(f'Operación de edición desconocida: {step.get("op")}')
            entries.append(handler(step))
        return entries[0] if len(entries) == 1 else {'op': 'batch', 'ops': entries}

    def _op_set(self, op):
        table, key, changes = op['table'], op['key'], op.get('changes') or {}
        if not changes:
            raise EditError('No hay cambios')
        rows = self._find(table, key)
        all_rows = self.tables[table]
        clean = {}
        for field, value in changes.items():
            if not editable(table, field) or field not in rows[0]:
                raise EditError(f'Campo no editable: {table}.{field}')
            new = coerce(value, rows[0][field], _sample(all_rows, field))
            self._check_reference(table, field, new)
            clean[field] = new
        if table == 'CYMNETWORKMETER':
            self._check_meter({**rows[0], **clean})
        before = {f: rows[0][f] for f in clean}
        live = self._rows(table)
        for row in live:
            if all(row.get(k) == key[k] for k in key):
                row.update(clean)
        return {'op': 'set', 'table': table, 'key': key, 'before': before, 'after': clean}

    def _op_move_node(self, op):
        node, x, y = op['node'], float(op['x']), float(op['y'])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise EditError('Coordenadas no válidas')
        self._find('CYMNODE', {'NodeId': node})
        before = None
        for row in self._rows('CYMNODE'):
            if row['NodeId'] == node:
                before = before or [row.get('X'), row.get('Y')]
                row['X'], row['Y'] = x, y
        # La longitud eléctrica es la del tramo (Length), no la del dibujo: no se recalcula.
        return {'op': 'move_node', 'node': node, 'before': before, 'after': [x, y]}

    def _remove_device(self, net, code, number):
        rows = self._rows(DEVICES[code][0])
        rows[:] = [r for r in rows if not (r.get('NetworkId') == net and r.get('DeviceNumber') == number)]
        dependents = ['CYMCUSTOMERLOAD'] if code == 20 else ['CYMLDCSETTINGS'] if code == 4 else []
        for dep in dependents:
            if dep in self.tables:
                rows = self._rows(dep)
                rows[:] = [r for r in rows if not (r.get('NetworkId') == net and r.get('DeviceNumber') == number)]

    def _op_delete_device(self, op):
        sid, code, number = op['section'], int(op['type']), op['device']
        if code in LINE_TYPES:
            raise EditError('El conductor es parte del tramo: para quitarlo, borre el tramo')
        sec = self._section(sid)
        if not any(r['DeviceType'] == code and r['DeviceNumber'] == number for r in self._attachments(sid)):
            raise EditError(f'El tramo {sid} no tiene el equipo {number}')
        rows = self._rows('CYMSECTIONDEVICE')
        rows[:] = [r for r in rows if not (r['SectionId'] == sid and r['DeviceType'] == code and r['DeviceNumber'] == number)]
        self._remove_device(sec['NetworkId'], code, number)
        return {'op': 'delete_device', 'section': sid, 'type': code, 'device': number}

    def _op_delete_section(self, op):
        sid = op['section']
        sec = self._section(sid)
        net = sec['NetworkId']
        for att in self._attachments(sid):
            self._remove_device(net, att['DeviceType'], att['DeviceNumber'])
        for table in ('CYMSECTIONDEVICE', 'CYMINTERMEDIATEPOINT'):
            if table in self.tables:
                rows = self._rows(table)
                rows[:] = [r for r in rows if r.get('SectionId') != sid]
        rows = self._rows('CYMSECTION')
        rows[:] = [r for r in rows if r['SectionId'] != sid]
        removed = self._drop_orphans({sec['FromNodeId'], sec['ToNodeId']})
        return {'op': 'delete_section', 'section': sid, 'removed_nodes': removed}

    def _drop_orphans(self, candidates):
        """Quitar nodos que quedan sin tramos, fuente ni medidor (CYME no deja nodos sueltos)."""
        used = {n for r in self.tables.get('CYMSECTION', []) for n in (r['FromNodeId'], r['ToNodeId'])}
        for table in ('CYMSOURCE', 'CYMNETWORKMETER', 'CYMHEADNODE', 'CYMEQUIVALENTSOURCE'):
            used |= {r.get('NodeId') for r in self.tables.get(table, [])}
        orphans = sorted(n for n in candidates if n not in used)
        if orphans:
            rows = self._rows('CYMNODE')
            rows[:] = [r for r in rows if r['NodeId'] not in orphans]
        return orphans

    def _op_add_device(self, op):
        sid, code = op['section'], int(op['type'])
        if code not in ADDABLE:
            raise EditError(f'Tipo de equipo no admitido: {code}')
        sec = self._section(sid)
        net = sec['NetworkId']
        table = DEVICES[code][0]
        used = self._device_numbers(code)
        number = op.get('number') or self._auto_id(f'{ADDABLE[code].upper()}_', used)
        self._check_new_id(number, used, 'el equipo')
        location = int(op.get('location', 1 if code in SWITCHING or code == 4 else 2))
        if location not in (0, 1, 2):
            raise EditError(f'Ubicación no válida: {location}')
        if code in SWITCHING and not self.tables.get(table):
            # Sin plantilla en la base: solo las columnas que lee cyme_model, maniobra cerrada y en servicio.
            catalog = REFERENCES[(table, 'EquipmentId')][0]
            equipment = next((r['EquipmentId'] for r in self.tables.get(catalog, [])), 'DEFAULT')
            template = {'NetworkId': net, 'DeviceNumber': None, 'EquipmentId': equipment, 'Status': 0,
                        'NormalStatus': 1, 'NormalFeedingNodeId': None}
        else:
            template = self._template(table, net)
        row = {**template, 'NetworkId': net, 'DeviceNumber': number}
        if code in SWITCHING:
            row['ClosedPhase'] = sec['Phase']
        if 'NormalFeedingNodeId' in row:
            row['NormalFeedingNodeId'] = sec['FromNodeId']
        if op.get('equipment'):
            field = 'EquipmentId'
            self._check_reference(table, field, op['equipment'])
            row[field] = op['equipment']
        same = any(r['DeviceType'] == code for r in self.tables.get('CYMSECTIONDEVICE', []))
        att_template = self._template('CYMSECTIONDEVICE', net, (lambda r: r['DeviceType'] == code) if same else None)
        self._rows('CYMSECTIONDEVICE').append({**att_template, 'NetworkId': net, 'SectionId': sid,
                                               'DeviceNumber': number, 'DeviceType': code, 'Location': location})
        self._rows(table).append(row)
        if code == 20:
            customer = self._template('CYMCUSTOMERLOAD', net)
            self._rows('CYMCUSTOMERLOAD').append({
                **customer, 'NetworkId': net, 'DeviceNumber': number, 'CustomerNumber': number,
                'Phase': sec['Phase'], 'LoadValueType': 0, 'LoadValue1': 0.0, 'LoadValue2': 0.0,
                'ConnectedKVA': 0.0, 'KWHUsage': 0.0, 'LockDuringLoadAllocation': 0, 'Status': 0})
        if code == 4:
            for ldc in [r for r in self.tables.get('CYMLDCSETTINGS', [])
                        if r.get('NetworkId') == template['NetworkId'] and r.get('DeviceNumber') == template['DeviceNumber']]:
                self._rows('CYMLDCSETTINGS').append({**ldc, 'NetworkId': net, 'DeviceNumber': number})
        return {'op': 'add_device', 'section': sid, 'type': code, 'device': number, 'location': location,
                'template': template['DeviceNumber']}

    def _op_add_section(self, op):
        start = op['from']
        start_rows = self._find('CYMNODE', {'NodeId': start})
        node_ids = {r['NodeId'] for r in self.tables.get('CYMNODE', [])}
        incident = [r for r in self.tables.get('CYMSECTION', []) if start in (r['FromNodeId'], r['ToNodeId'])]
        net = op.get('network') or (incident[0]['NetworkId'] if incident else start_rows[0].get('NetworkId'))
        if not any(r['NetworkId'] == net for r in self.tables.get('CYMNETWORK', [])):
            raise EditError(f'Red inexistente: {net}')
        new_node = None
        if op.get('to'):
            end = op['to']
            self._find('CYMNODE', {'NodeId': end})
            if end == start:
                raise EditError('Un tramo necesita dos nodos distintos')
            end_row = self._find('CYMNODE', {'NodeId': end})[0]
            x, y = end_row['X'], end_row['Y']
        else:
            x, y = float(op['x']), float(op['y'])
            end = op.get('node') or self._auto_id('NODE_', node_ids)
            self._check_new_id(end, node_ids, 'el nodo')
            new_node = {**self._template('CYMNODE', net), 'NodeId': end, 'NetworkId': net, 'X': x, 'Y': y}
        section_ids = {r['SectionId'] for r in self.tables.get('CYMSECTION', [])}
        sid = op.get('section') or self._auto_id('SECTION_', section_ids)
        self._check_new_id(sid, section_ids, 'el tramo')
        code = int(op.get('line_type', 2))
        if code not in LINE_TYPES:
            raise EditError(f'Tipo de conductor no admitido: {code}')
        table, field, _ = LINE_TYPES[code]
        if sid in self._device_numbers(code):
            raise EditError(f'Ya existe un conductor {sid}')
        phase = int(op.get('phase') or (incident[0]['Phase'] if incident else 7))
        self._check_reference('CYMSECTION', 'Phase', phase)
        x0, y0 = start_rows[0]['X'], start_rows[0]['Y']
        length = op.get('length')
        length = float(length) if length not in (None, '') else math.hypot(x - x0, y - y0)
        if not (math.isfinite(length) and length > 0):
            raise EditError('La longitud del tramo debe ser positiva')
        line = {**self._template(table, net), 'NetworkId': net, 'DeviceNumber': sid, 'Length': length, 'Status': 0}
        if op.get('equipment'):
            self._check_reference(table, field, op['equipment'])
            line[field] = op['equipment']
        att = self._template('CYMSECTIONDEVICE', net, lambda r: r['DeviceType'] == code)
        if new_node:
            self._rows('CYMNODE').append(new_node)
        self._rows('CYMSECTION').append({**self._template('CYMSECTION', net), 'NetworkId': net, 'SectionId': sid,
                                         'FromNodeId': start, 'ToNodeId': end, 'Phase': phase})
        self._rows('CYMSECTIONDEVICE').append({**att, 'NetworkId': net, 'SectionId': sid, 'DeviceNumber': sid,
                                               'DeviceType': code, 'Location': att['Location']})
        self._rows(table).append(line)
        return {'op': 'add_section', 'section': sid, 'from': start, 'to': end, 'new_node': bool(new_node),
                'phase': phase, 'line_type': code, 'equipment': line.get(field), 'length_m': round(length, 3),
                'network': net}
