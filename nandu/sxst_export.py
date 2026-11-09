"""Exportación de un proyecto Ñandu a estudio CYME (.sxst), por ida y vuelta.

Solo para proyectos que vienen de un .sxst. El estudio original es la base: se relee con
sxst_import, se compara con las tablas actuales del proyecto y cada diferencia se escribe sobre
los bytes originales (posiciones de expat). Lo que Ñandu no modela (etiquetas, protecciones,
catálogos, parámetros de estudio, otras redes) queda idéntico byte a byte.

Reglas:
- Un cambio sin correspondencia verificada en el XML hace fallar la exportación con la lista de
  cambios; nunca se escribe un estudio que pierda cambios.
- Un elemento nuevo copia el primer elemento del mismo tipo de la misma red (la plantilla que usa
  la edición de la interfaz) y reemplaza los campos que Ñandu conoce.
- Antes de escribir, la salida se vuelve a importar y debe reproducir las tablas del proyecto.
"""
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.parsers import expat

from . import __version__, sxst_import
from .topology import DEVICES

ABC = 'ABC'
TAG = {1: 'Underground', 2: 'OverheadLine', 4: 'Regulator', 8: 'Breaker', 10: 'Recloser', 13: 'Switch', 14: 'Fuse',
       17: 'ShuntCapacitor', 20: 'SpotLoad', 23: 'OverheadLineUnbalanced'}
TABLE_CODE = {table: code for code, (table, _) in DEVICES.items()}
# Textos CYME de los códigos: los mismos diccionarios del importador, invertidos, más enums de cymEnum.ini.
ENUMS = {'phase': {v: k for k, v in sxst_import.PHASE.items()},
         'location': {v: k for k, v in sxst_import.LOCATION.items()},
         'connection': {v: k for k, v in sxst_import.CONNECTION.items()},
         'setting': {v: k for k, v in sxst_import.SETTING.items()},
         'status': {0: 'Connected', 1: 'Disconnected'},  # ConnectionStatusEnum
         'switch': {1: 'Closed', 0: 'Open'},  # SwitchStatusEnum
         'lock': {1: 'Locked', 0: 'Unlocked'}}  # LALockDuringLoadAllocationEnum (nivel de modelo de carga)
STATUS = ('ConnectionStatus', 'status')
LINE = {'Length': ('Length', 'num'), 'LineId': ('LineID', 'text'), 'Status': STATUS}
SWITCH = {'EquipmentId': ('DeviceID', 'text'), 'ClosedPhase': ('ClosedPhase', 'phase'),
          'NormalStatus': ('NormalStatus', 'switch'), 'NormalFeedingNodeId': ('NormalFeedingNodeID', 'text'),
          'Status': STATUS}
# Columna de la tabla -> (etiqueta del elemento del equipo, tipo); inversa de sxst_import.read.
DEVICE_FIELDS = {
    'CYMOVERHEADLINE': LINE, 'CYMOVERHEADLINEUNBALANCED': LINE,
    'CYMUNDERGROUNDLINE': {'Length': ('Length', 'num'), 'CableId': ('CableID', 'text'),
                           'NumberOfCableInParallel': ('NumberOfCableInParallel', 'num'), 'Status': STATUS},
    'CYMSWITCH': SWITCH, 'CYMBREAKER': SWITCH, 'CYMRECLOSER': SWITCH, 'CYMFUSE': SWITCH,
    'CYMREGULATOR': {'EquipmentId': ('DeviceID', 'text'), 'ConnectionConfiguration': ('ConnectionConfiguration', 'connection'),
                     'SettingOption': ('SettingOption', 'setting'), 'NormalFeedingNodeId': ('NormalFeedingNodeID', 'text'),
                     'LoadCenterId': ('LoadCenterID', 'text'), 'Status': STATUS,
                     **{f'TapPosition{p}': (f'TapPosition{p}', 'num') for p in ABC},
                     **{k: (k, 'num') for k in ('PTRatio', 'CTPrimaryRating', 'BoostPercent', 'BuckPercent')}},
    'CYMSHUNTCAPACITOR': {'EquipmentId': ('DeviceID', 'text'), 'Status': STATUS, 'KVLN': ('KVLN', 'num'),
                          'ConnectionConfiguration': ('ConnectionConfiguration', 'connection'),
                          **{f'KVAR{p}': (f'FixedKVAR{p}', 'num') for p in ABC},
                          **{f'SwitchedKVAR{p}': (f'SwitchedKVAR{p}', 'num') for p in ABC}},
    'CYMLOAD': {'ConnectionConfiguration': ('ConnectionConfiguration', 'connection')},
}
CUSTOMER_FIELDS = {'ConsumerClassId': ('CustomerType', 'text'), 'Status': STATUS}
MODEL_FIELDS = {'LockDuringLoadAllocation': ('LockDuringLoadAllocation', 'lock')}
VALUE_FIELDS = {'Phase': ('Phase', 'phase'), 'ConnectedKVA': ('ConnectedKVA', 'num'), 'KWHUsage': ('KWH', 'num'),
                'NumberOfCustomer': ('NumberOfCustomer', 'num')}
CATALOG_DB = {'CYMEQOVERHEADLINE': 'OverheadLineDB', 'CYMEQCABLE': 'CableDB',
              'CYMEQOVERHEADLINEUNBALANCED': 'OverheadLineUnbalancedDB', 'CYMEQREGULATOR': 'RegulatorDB',
              'CYMEQSHUNTCAPACITOR': 'ShuntCapacitorDB', 'CYMEQSOURCE': 'SubstationDB', 'CYMEQSWITCH': 'SwitchDB',
              'CYMEQRECLOSER': 'RecloserDB', 'CYMEQBREAKER': 'BreakerDB', 'CYMEQFUSE': 'FuseDB'}
SEQUENCE_TAG = {dst: src for src, dst in sxst_import.SEQUENCE_FIELDS.items()}
IDENTITY = {'NetworkId', 'DeviceNumber', 'DeviceType'}
KEYS = {'CYMSECTION': ('SectionId',), 'CYMNODE': ('NodeId',),
        'CYMSECTIONDEVICE': ('SectionId', 'DeviceType', 'DeviceNumber'),
        'CYMNETWORKMETER': ('NetworkId', 'NodeId'), 'CYMCUSTOMERLOAD': ('NetworkId', 'DeviceNumber', 'CustomerNumber'),
        'CYMLDCSETTINGS': ('NetworkId', 'DeviceNumber', 'Forward'),
        **{t: ('NetworkId', 'DeviceNumber') for t in DEVICE_FIELDS}, **{t: ('EquipmentId',) for t in CATALOG_DB}}
# Tablas que la interfaz no edita: cualquier diferencia no tiene escritura definida.
STATIC = ('CYMNETWORK', 'CYMSOURCE', 'CYMHEADNODE', 'CYMEQUIVALENTSOURCE', 'CYMCONSUMERCLASS')
_INT = re.compile(r'^[+-]?\d+$')
# Lectura de la salida: escribir con la precisión del archivo original (6 decimales en CYME) redondea.
TOLERANCE = (1e-5, 1e-6)  # absoluta, relativa


class ExportError(ValueError):
    def __init__(self, message, problems=()):
        self.problems = list(problems)
        shown = ''.join(f'\n- {p}' for p in self.problems[:40])
        more = f'\n- ... y {len(self.problems) - 40} más' if len(self.problems) > 40 else ''
        super().__init__(message + shown + more)


class _Unmapped(Exception):
    pass


class _Doc:
    """XML con la posición en bytes de cada elemento; los cambios se acumulan como empalmes."""

    def __init__(self, raw):
        self.raw = raw
        self.nl = b'\r\n' if b'\r\n' in raw[:4096] else b'\n'
        self.span = {}
        self.splices = []
        self.root = self._parse()

    def _parse(self):
        raw, span, stack, top = self.raw, self.span, [], []
        parser = expat.ParserCreate()
        parser.buffer_text = True

        def start(tag, attrs):
            s = parser.CurrentByteIndex
            gt = raw.index(b'>', s)
            el = ET.Element(tag, attrs)
            parent = stack[-1] if stack else None
            (top if parent is None else parent).append(el)
            span[id(el)] = [s, gt + 1, gt + 1, gt + 1, raw[gt - 1:gt] == b'/', parent]
            stack.append(el)

        def end(_tag):
            el = stack.pop()
            sp = span[id(el)]
            if not sp[4]:
                e = parser.CurrentByteIndex
                sp[2], sp[3] = e, raw.index(b'>', e) + 1

        def chars(data):
            if len(stack[-1]) == 0:
                stack[-1].text = (stack[-1].text or '') + data

        parser.StartElementHandler, parser.EndElementHandler, parser.CharacterDataHandler = start, end, chars
        parser.Parse(raw, True)
        return top[0]

    def parent(self, el):
        return self.span[id(el)][5]

    def _splice(self, start, end, data):
        self.splices.append((start, end, len(self.splices), data))

    def set_text(self, el, value):
        s, cs, ce, e, empty, _ = self.span[id(el)]
        data = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').encode('utf-8')
        if empty:
            self._splice(s, e, self.raw[s:cs - 2].rstrip() + b'>' + data + b'</' + el.tag.encode() + b'>')
        else:
            self._splice(cs, ce, data)
        el.text = value

    def remove(self, el):
        s, _, _, e, _, _ = self.span[id(el)]
        if self.raw[e:e + len(self.nl)] == self.nl:
            e += len(self.nl)
        self._splice(s, e, b'')

    def append(self, parent, data):
        """Insertar `data` (un elemento completo) como último hijo de `parent`, en su propia línea."""
        s, cs, ce, e, empty, _ = self.span[id(parent)]
        if empty:
            self._splice(s, e, self.raw[s:cs - 2].rstrip() + b'>' + self.nl + data + self.nl
                         + b'</' + parent.tag.encode() + b'>')
        else:
            prefix = b'' if self.raw[cs:ce].endswith(self.nl) else self.nl
            self._splice(ce, ce, prefix + data + self.nl)

    def snippet(self, el):
        """Copia independiente de un elemento, para armar uno nuevo."""
        s, _, _, e, _, _ = self.span[id(el)]
        return _Doc(self.raw[s:e])

    def render(self):
        out, pos = [], 0
        for start, end, _, data in sorted(self.splices, key=lambda x: (x[0], x[2])):
            if start < pos:
                raise ExportError('Cambios superpuestos en el XML (error interno del exportador)')
            out += [self.raw[pos:start], data]
            pos = end
        out.append(self.raw[pos:])
        return b''.join(out)


def _number(value, original):
    """Número con el formato del texto original: entero si lo era, si no con sus decimales."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Unmapped(f'valor no numérico {value!r}')
    original = (original or '').strip()
    if _INT.match(original):
        if float(value) != int(value):
            raise _Unmapped(f'{value} no es entero como el valor original {original}')
        return str(int(value))
    decimals = len(original.split('.', 1)[1]) if '.' in original and 'e' not in original.lower() else 6
    return f'{float(value):.{decimals}f}'


def _write(doc, el, tag, value, kind):
    target = el.find(tag)
    if target is None:
        raise _Unmapped(f'<{el.tag}> no tiene <{tag}>')
    current = (target.text or '').strip()
    if kind in ENUMS:
        text = ENUMS[kind].get(value)
        if text is None:
            raise _Unmapped(f'{tag}: código {value!r} sin texto CYME verificado')
    elif kind == 'text' or (kind == 'auto' and isinstance(value, str)):
        text = '' if value is None else str(value)
    else:
        text = _number(value, current)
    if text != current:
        doc.set_text(target, text)


def _write_load(doc, value_el, kw, kvar):
    """kW/kvar en el <LoadValue> con su propio tipo (inversa exacta de sxst_import.load_value)."""
    lv = value_el.find('LoadValue')
    kind = lv.get('Type') if lv is not None else None
    if kind == 'LoadValueKW_KVAR':
        _write(doc, lv, 'KW', kw, 'num')
        _write(doc, lv, 'KVAR', kvar, 'num')
        return
    if kind not in ('LoadValueKVA_PF', 'LoadValueKW_PF'):
        raise _Unmapped(f'tipo de carga {kind!r} sin escritura definida')
    s = math.hypot(kw, kvar)
    if kw < 0 or (s > 0 and kw == 0):
        raise _Unmapped(f'{kw} kW / {kvar} kvar no representable como {kind}')
    _write(doc, lv, 'KVA' if kind == 'LoadValueKVA_PF' else 'KW', s if kind == 'LoadValueKVA_PF' else kw, 'num')
    if s > 0:
        _write(doc, lv, 'PF', 100 * kw / s * (-1 if kvar < 0 else 1), 'num')


def _same(a, b):
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    if numbers and (isinstance(a, float) or isinstance(b, float)):
        return abs(a - b) <= TOLERANCE[0] + TOLERANCE[1] * abs(a)
    return a == b


def _changes(old, new, ignore=()):
    """Columnas cambiadas; una columna que solo tiene el proyecto también cuenta (no tiene lugar en el XML)."""
    return {k: new[k] for k in new if k not in ignore and (k not in old or old[k] != new[k])}


def _group(rows, names):
    out = {}
    for row in rows:
        out.setdefault(tuple(row.get(k) for k in names), []).append(row)
    return out


class _Writer:
    def __init__(self, doc, base, current):
        self.doc, self.base, self.cur = doc, base, current
        self.problems, self.log = [], []
        root = doc.root
        self.sections = {}
        for sec in root.iter('Section'):
            self.sections.setdefault(sxst_import._text(sec, 'SectionID'), []).append(sec)
        self.nodes = {}
        for node in root.iter('Node'):
            self.nodes.setdefault(sxst_import._text(node, 'NodeID'), []).append(node)
        self.topos = {sxst_import._text(t, 'NetworkID'): t for t in root.iter('Topo')}
        dbs = root.find('Equipments/Equipments/EquipmentDBs')
        self.dbs = list(dbs) if dbs is not None else []

    # --- Utilidades -------------------------------------------------------------------------
    def rows(self, which, table):
        return (self.base if which == 'base' else self.cur).get(table, [])

    def index(self, table, which):
        return _group(self.rows(which, table), KEYS[table])

    def fail(self, text):
        self.problems.append(text)

    def guard(self, where, fn, *args):
        try:
            fn(*args)
            return True
        except _Unmapped as exc:
            self.fail(f'{where}: {exc}')
            return False

    def one(self, found, what):
        if len(found or []) != 1:
            raise _Unmapped(f'{what}: {len(found or [])} elementos en el XML (se necesita exactamente uno)')
        return found[0]

    def device(self, section, code, number):
        devices = section.find('Devices')
        found = [d for d in (devices if devices is not None else [])
                 if d.tag == TAG.get(code) and sxst_import._text(d, 'DeviceNumber') == number]
        return self.one(found, f'equipo {number} ({TAG.get(code, code)}) del tramo')

    def write_fields(self, doc, el, fields, changes, where):
        for col, value in changes.items():
            if col not in fields:
                raise _Unmapped(f'{col} sin correspondencia en el XML')
            tag, kind = fields[col]
            _write(doc, el, tag, value, kind)
        if changes:
            self.log.append({'element': where, 'fields': sorted(changes)})

    # --- Proceso ---------------------------------------------------------------------------
    def run(self):
        b_sec, c_sec = self.index('CYMSECTION', 'base'), self.index('CYMSECTION', 'cur')
        self.removed_sections = set(b_sec) - set(c_sec)
        self.added_sections = [k for k in c_sec if k not in b_sec]
        b_sd, c_sd = self.index('CYMSECTIONDEVICE', 'base'), self.index('CYMSECTIONDEVICE', 'cur')
        gone = {(k[1], k[2]) for k in set(b_sd) - set(c_sd)}
        new = {(k[1], k[2]): rows[0] for k, rows in c_sd.items() if k not in b_sd}
        self.gone, self.new = gone, new
        # Equipo -> tramo actual, para ubicar los cambios de equipos que siguen en la red.
        self.where = _group(self.rows('cur', 'CYMSECTIONDEVICE'), ('DeviceType', 'DeviceNumber'))
        self.do_sections(b_sec, c_sec)
        self.do_section_devices(b_sd, c_sd)
        for table in DEVICE_FIELDS:
            self.do_devices(table)
        self.do_customers()
        self.do_meters()
        self.do_nodes()
        self.do_catalogs()
        self.do_static()
        for key in self.added_sections:
            self.guard(f'tramo nuevo {key[0]}', self.add_section, c_sec[key][0])

    def do_sections(self, b_sec, c_sec):
        for (sid,) in sorted(self.removed_sections):
            if self.guard(f'tramo {sid}', lambda: self.doc.remove(self.one(self.sections.get(sid), f'tramo {sid}'))):
                self.log.append({'element': f'Section {sid}', 'removed': True})
        for key in b_sec.keys() & c_sec.keys():
            old, cur = b_sec[key], c_sec[key]
            if len(old) != 1 or len(cur) != 1:
                self.fail(f'tramo {key[0]}: identificador repetido')
                continue
            changes = _changes(old[0], cur[0])
            if changes:
                self.guard(f'tramo {key[0]}', lambda: self.write_fields(
                    self.doc, self.one(self.sections.get(key[0]), f'tramo {key[0]}'),
                    {'Phase': ('Phase', 'phase')}, changes, f'Section {key[0]}'))

    def do_section_devices(self, b_sd, c_sd):
        for key in sorted(set(b_sd) - set(c_sd), key=str):
            sid, code, number = key
            if (sid,) in self.removed_sections:
                continue
            self.guard(f'equipo {number} del tramo {sid}', lambda: (
                self.doc.remove(self.device(self.one(self.sections.get(sid), f'tramo {sid}'), code, number)),
                self.log.append({'element': f'{TAG.get(code)} {number}', 'removed': True})))
        for key, rows in c_sd.items():
            sid, code, number = key
            if key in b_sd or any(k == (sid,) for k in self.added_sections):
                continue

            def add(sid=sid, code=code, row=rows[0]):
                section = self.one(self.sections.get(sid), f'tramo {sid}')
                devices = section.find('Devices')
                if devices is None:
                    raise _Unmapped(f'el tramo {sid} no tiene <Devices>')
                self.doc.append(devices, self.device_snippet(code, row))
                self.log.append({'element': f'{TAG.get(code)} {row["DeviceNumber"]}', 'added_to': sid})
            self.guard(f'equipo nuevo {number} en {sid}', add)
        for key in b_sd.keys() & c_sd.keys():
            changes = _changes(b_sd[key][0], c_sd[key][0])
            if changes:
                sid, code, number = key
                self.guard(f'equipo {number} del tramo {sid}', lambda: self.write_fields(
                    self.doc, self.device(self.one(self.sections.get(sid), f'tramo {sid}'), code, number),
                    {'Location': ('Location', 'location')}, changes, f'{TAG.get(code)} {number}'))

    def locate(self, code, number):
        rows = self.where.get((code, number), [])
        sid = self.one([r['SectionId'] for r in rows], f'tramo del equipo {number}')
        return self.device(self.one(self.sections.get(sid), f'tramo {sid}'), code, number)

    def do_devices(self, table):
        code = TABLE_CODE[table]
        old, cur = self.index(table, 'base'), self.index(table, 'cur')
        for key in old.keys() - cur.keys():
            if (code, key[1]) not in self.gone:
                self.fail(f'{table} {key[1]}: borrado sin borrar su equipo del tramo')
        for key in cur.keys() - old.keys():
            if (code, key[1]) not in self.new:
                self.fail(f'{table} {key[1]}: fila nueva sin equipo en un tramo')
        for key in old.keys() & cur.keys():
            if (code, key[1]) in self.new:
                continue  # reemplazado por uno nuevo con el mismo número: se escribe completo al crearlo
            if len(old[key]) != 1 or len(cur[key]) != 1:
                self.fail(f'{table} {key[1]}: identificador repetido')
                continue
            changes = _changes(old[key][0], cur[key][0], IDENTITY)
            if changes:
                self.guard(f'{table} {key[1]}', lambda: self.write_fields(
                    self.doc, self.locate(code, key[1]), DEVICE_FIELDS[table], changes, f'{TAG[code]} {key[1]}'))

    # --- Clientes -------------------------------------------------------------------------
    @staticmethod
    def customer_slots(spot):
        """(cliente, modelo, valor) en el orden en que sxst_import crea las filas de CYMCUSTOMERLOAD."""
        out = {}
        for cl in spot.iter('CustomerLoad'):
            for model in (m for m in cl.iter('CustomerLoadModel') if sxst_import._text(m, 'LoadModelID') == '1'):
                for value in model.iter('CustomerLoadValue'):
                    try:
                        sxst_import.load_value(value.find('LoadValue'))
                    except (ValueError, AttributeError):
                        continue
                    out.setdefault(sxst_import._text(cl, 'CustomerNumber'), []).append((cl, model, value))
        return out

    def write_customer(self, doc, slot, old, row, where):
        cl, model, value = slot
        changes = _changes(old, row, IDENTITY | {'CustomerNumber', 'LoadValueType', 'LoadModelId'}) if old else {
            k: v for k, v in row.items() if k in CUSTOMER_FIELDS or k in MODEL_FIELDS or k in VALUE_FIELDS
            or k in ('LoadValue1', 'LoadValue2')}
        for col, v in changes.items():
            if col in CUSTOMER_FIELDS:
                _write(doc, cl, *CUSTOMER_FIELDS[col][:1], v, CUSTOMER_FIELDS[col][1])
            elif col in MODEL_FIELDS:
                _write(doc, model, MODEL_FIELDS[col][0], v, MODEL_FIELDS[col][1])
            elif col in VALUE_FIELDS:
                _write(doc, value, VALUE_FIELDS[col][0], v, VALUE_FIELDS[col][1])
            elif col not in ('LoadValue1', 'LoadValue2'):
                raise _Unmapped(f'{col} sin correspondencia en el XML')
        if 'LoadValue1' in changes or 'LoadValue2' in changes:
            _write_load(doc, value, row['LoadValue1'], row['LoadValue2'])
        if changes and old:
            self.log.append({'element': where, 'fields': sorted(changes)})

    def do_customers(self):
        old, cur = self.index('CYMCUSTOMERLOAD', 'base'), self.index('CYMCUSTOMERLOAD', 'cur')
        slots_cache = {}
        for key in old.keys() | cur.keys():
            net, number, customer = key
            if (20, number) in self.gone or (20, number) in self.new:
                continue  # el equipo entero se borra o se escribe al crearlo
            a, b = old.get(key, []), cur.get(key, [])
            if len(a) != len(b):
                self.fail(f'carga {number} cliente {customer}: cambia el número de filas ({len(a)} -> {len(b)})')
                continue
            pairs = [(x, y) for x, y in zip(a, b) if x != y]
            if not pairs:
                continue
            for field in CUSTOMER_FIELDS:
                if len({r.get(field) for r in b}) > 1:
                    self.fail(f'carga {number} cliente {customer}: {field} distinto entre fases de un mismo cliente')

            def apply(number=number, customer=customer, a=a, b=b):
                if number not in slots_cache:
                    slots_cache[number] = self.customer_slots(self.locate(20, number))
                slots = slots_cache[number].get(customer, [])
                if len(slots) != len(a):
                    raise _Unmapped(f'{len(slots)} valores en el XML para {len(a)} filas')
                for slot, x, y in zip(slots, a, b):
                    if x != y:
                        self.write_customer(self.doc, slot, x, y, f'CustomerLoad {number}/{customer}')
            self.guard(f'carga {number} cliente {customer}', apply)

    # --- Medidores, nodos, catálogos y tablas fijas -------------------------------------------
    def do_meters(self):
        old, cur = self.index('CYMNETWORKMETER', 'base'), self.index('CYMNETWORKMETER', 'cur')
        for key in old.keys() ^ cur.keys():
            self.fail(f'medidor {key}: alta o baja de medidores sin escritura definida')
        for key in old.keys() & cur.keys():
            changes = _changes(old[key][0], cur[key][0])
            if changes:
                self.guard(f'medidor de {key[0]}', self.write_meter, key, changes)

    def write_meter(self, key, changes):
        net, node = key
        topo = self.topos.get(net)
        sources = [s for s in (topo.iter('Source') if topo is not None else [])
                   if sxst_import._text(s, 'SourceNodeID') == node]
        meter = self.one(sources, f'fuente {node}').find('Meter/MeterModels/MeterModel')
        if meter is None:
            raise _Unmapped('la fuente no tiene <Meter>')
        for col, value in changes.items():
            match = re.fullmatch(r'Val([12])([ABC])', col)
            if not match:
                raise _Unmapped(f'{col} sin correspondencia en el XML')
            lv = meter.find(f'DemandValue{match[2]}/LoadValue')
            if lv is None or lv.get('Type') != 'LoadValueAMP_PF':
                raise _Unmapped(f'fase {match[2]}: el medidor no es A-FP en el XML')
            _write(self.doc, lv, 'AMP' if match[1] == '1' else 'PF', value, 'num')
        self.log.append({'element': f'Meter {net}', 'fields': sorted(changes)})

    def do_nodes(self):
        old, cur = self.index('CYMNODE', 'base'), self.index('CYMNODE', 'cur')
        # Un nodo que la red importada ya no usa puede seguir en tramos de redes no importadas.
        referenced = set()
        for sid, secs in self.sections.items():
            if (sid,) not in self.removed_sections:
                for sec in secs:
                    referenced |= {sxst_import._text(sec, 'FromNodeID'), sxst_import._text(sec, 'ToNodeID')}
        for (node,) in old.keys() - cur.keys():
            if node in referenced:
                continue
            for el in self.nodes.get(node, []):
                self.doc.remove(el)
            self.log.append({'element': f'Node {node}', 'removed': True})
        for key in old.keys() & cur.keys():
            changes = _changes(old[key][0], cur[key][0], {'NetworkId'})
            if changes:
                def move(node=key[0], changes=changes):
                    point = self.one(self.nodes.get(node), f'nodo {node}').find('Connectors/Point')
                    if point is None:
                        raise _Unmapped('el nodo no tiene <Connectors><Point>')
                    self.write_fields(self.doc, point, {'X': ('X', 'num'), 'Y': ('Y', 'num')}, changes, f'Node {node}')
                self.guard(f'nodo {key[0]}', move)
        for key, rows in cur.items():
            if key not in old:
                self.guard(f'nodo nuevo {key[0]}', self.add_node, rows[0])

    def do_catalogs(self):
        for table, tag in CATALOG_DB.items():
            old, cur = self.index(table, 'base'), self.index(table, 'cur')
            for key in old.keys() ^ cur.keys():
                self.fail(f'{table} {key[0]}: alta o baja de catálogo sin escritura definida')
            for key in old.keys() & cur.keys():
                changes = _changes(old[key][0], cur[key][0], {'EquipmentId'})
                if not changes:
                    continue

                def apply(ident=key[0], changes=changes, tag=tag, table=table):
                    db = self.one([d for d in self.dbs if d.tag == tag and sxst_import._text(d, 'EquipmentID') == ident],
                                  f'{tag} {ident}')
                    names = SEQUENCE_TAG if tag in ('OverheadLineDB', 'CableDB') else {}
                    fields = {col: (names.get(col, col), 'auto') for col in changes}
                    self.write_fields(self.doc, db, fields, changes, f'{tag} {ident}')
                self.guard(f'{table} {key[0]}', apply)

    def do_static(self):
        for table in STATIC:
            canon = lambda rows: sorted(repr(sorted(r.items())) for r in rows)
            if canon(self.rows('base', table)) != canon(self.rows('cur', table)):
                self.fail(f'{table}: cambios en una tabla que no se exporta')
        # Ajustes LDC: solo cambian con altas o bajas de reguladores (se copian con el elemento).
        moved = {n for c, n in self.gone | set(self.new) if c == 4}
        old, cur = self.index('CYMLDCSETTINGS', 'base'), self.index('CYMLDCSETTINGS', 'cur')
        for key in old.keys() | cur.keys():
            if key[1] not in moved and old.get(key) != cur.get(key):
                self.fail(f'CYMLDCSETTINGS {key[1]}: cambio sin escritura definida')

    # --- Elementos nuevos ------------------------------------------------------------------
    def template(self, tag, net):
        """Primer elemento del tipo: de la red y en un tramo que sigue; si no, de cualquier red; si no,
        de un tramo borrado (sus bytes siguen en el original)."""
        ranked = []
        for sid, secs in self.sections.items():
            for sec in secs:
                devices = sec.find('Devices')
                for dev in (devices if devices is not None else []):
                    if dev.tag == tag:
                        rank = ((sid,) in self.removed_sections, sxst_import._text(sec, 'OwnerID') != net)
                        ranked.append((rank, len(ranked), dev))
        if not ranked:
            raise _Unmapped(f'no hay ningún <{tag}> en el estudio para usar como plantilla')
        return min(ranked, key=lambda x: x[:2])[2]

    def device_snippet(self, code, sd_row):
        net, number = sd_row['NetworkId'], sd_row['DeviceNumber']
        table = DEVICES[code][0]
        rows = [r for r in self.rows('cur', table) if r.get('NetworkId') == net and r.get('DeviceNumber') == number]
        row = self.one(rows, f'fila de {table} {number}')
        sub = self.doc.snippet(self.template(TAG[code], net))
        el = sub.root
        _write(sub, el, 'DeviceNumber', number, 'text')
        # Sin <Location> (los conductores), sxst_import lee 0 (Middle): solo se admite ese valor.
        if el.find('Location') is not None or sd_row['Location'] not in (0, None):
            _write(sub, el, 'Location', sd_row['Location'], 'location')
        for col, (tag, kind) in DEVICE_FIELDS[table].items():
            if col in row and el.find(tag) is not None:
                _write(sub, el, tag, row[col], kind)
        if code == 20:
            self.customers_into(sub, el, net, number)
        return sub.render()

    def customers_into(self, sub, spot, net, number):
        """Clientes de una carga nueva: copia del primer cliente de la plantilla, solo el modelo de carga 1."""
        loads = spot.find('CustomerLoads')
        template = loads.find('CustomerLoad') if loads is not None else None
        if template is None:
            raise _Unmapped('la carga plantilla no tiene <CustomerLoads>')
        for child in list(loads):
            sub.remove(child)
        groups = _group([r for r in self.rows('cur', 'CYMCUSTOMERLOAD')
                         if r.get('NetworkId') == net and r.get('DeviceNumber') == number], ('CustomerNumber',))
        for (customer,), rows in groups.items():
            csub = sub.snippet(template)
            cl = csub.root
            _write(csub, cl, 'CustomerNumber', customer, 'text')
            models = list(cl.iter('CustomerLoadModel'))
            keep = next((m for m in models if sxst_import._text(m, 'LoadModelID') == '1'), None)
            if keep is None:
                raise _Unmapped('el cliente plantilla no tiene modelo de carga 1')
            for model in models:
                if model is not keep:
                    csub.remove(model)
            values = keep.find('CustomerLoadValues')
            first = values.find('CustomerLoadValue') if values is not None else None
            if first is None:
                raise _Unmapped('el cliente plantilla no tiene <CustomerLoadValue>')
            for child in list(values):
                csub.remove(child)
            for row in rows:
                vsub = csub.snippet(first)
                self.write_customer(vsub, (vsub.root, vsub.root, vsub.root), None,
                                    {k: v for k, v in row.items() if k in VALUE_FIELDS or k in ('LoadValue1', 'LoadValue2')},
                                    None)
                csub.append(values, vsub.render())
            self.write_customer(csub, (cl, keep, None), None,
                                {k: v for k, v in rows[0].items() if k in CUSTOMER_FIELDS or k in MODEL_FIELDS}, None)
            sub.append(loads, csub.render())

    def add_node(self, row):
        net = row.get('NetworkId')
        base_ids = {r['NodeId'] for r in self.rows('base', 'CYMNODE')}
        candidates = [r['NodeId'] for r in self.rows('cur', 'CYMNODE') if r['NodeId'] in base_ids]
        ident = next((n for n in candidates if
                      next((r for r in self.rows('cur', 'CYMNODE') if r['NodeId'] == n), {}).get('NetworkId') == net),
                     candidates[0] if candidates else None)
        if ident is None:
            raise _Unmapped('no hay ningún nodo en el estudio para usar como plantilla')
        template = self.one(self.nodes.get(ident), f'nodo {ident}')
        sub = self.doc.snippet(template)
        _write(sub, sub.root, 'NodeID', row['NodeId'], 'text')
        points = list(sub.root.iter('Point'))
        if not points:
            raise _Unmapped('el nodo plantilla no tiene <Point>')
        for extra in points[1:]:
            sub.remove(extra)
        _write(sub, points[0], 'X', row['X'], 'num')
        _write(sub, points[0], 'Y', row['Y'], 'num')
        self.doc.append(self.doc.parent(template), sub.render())
        self.log.append({'element': f'Node {row["NodeId"]}', 'added': True, 'template': ident})

    def add_section(self, row):
        net = row['NetworkId']
        old_ids = {r['SectionId'] for r in self.rows('base', 'CYMSECTION')}
        ident = next((r['SectionId'] for r in self.rows('cur', 'CYMSECTION')
                      if r['SectionId'] in old_ids and r.get('NetworkId') == net),
                     next((r['SectionId'] for r in self.rows('cur', 'CYMSECTION') if r['SectionId'] in old_ids), None))
        if ident is None:
            raise _Unmapped('no hay ningún tramo en el estudio para usar como plantilla')
        template = self.one(self.sections.get(ident), f'tramo {ident}')
        sub = self.doc.snippet(template)
        el = sub.root
        for tag, value, kind in (('SectionID', row['SectionId'], 'text'), ('OwnerID', net, 'text'),
                                 ('FromNodeID', row['FromNodeId'], 'text'), ('ToNodeID', row['ToNodeId'], 'text'),
                                 ('Phase', row['Phase'], 'phase'),
                                 ('FromNodeConnectorIndex', row.get('FromNodeConnectorIndex', 0), 'num'),
                                 ('ToNodeConnectorIndex', row.get('ToNodeConnectorIndex', 0), 'num')):
            _write(sub, el, tag, value, kind)
        devices = el.find('Devices')
        if devices is None:
            raise _Unmapped('el tramo plantilla no tiene <Devices>')
        for child in list(devices):
            sub.remove(child)
        for sd in self.rows('cur', 'CYMSECTIONDEVICE'):
            if sd['SectionId'] == row['SectionId']:
                sub.append(devices, self.device_snippet(sd['DeviceType'], sd))
        self.doc.append(self.doc.parent(template), sub.render())
        self.log.append({'element': f'Section {row["SectionId"]}', 'added': True, 'template': ident})


def _sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def base_study(provenance):
    """Procedencia del .sxst de origen, siguiendo la cadena de proyectos guardados con Save As."""
    p = provenance or {}
    while 'derived_from' in p:
        p = p['derived_from'].get('provenance') or {}
    if p.get('source_format') != 'cyme-sxst':
        raise ExportError('El proyecto no proviene de un estudio CYME (.sxst): la exportación por ida y vuelta '
                          'necesita el estudio original (los proyectos importados de Access aún no se exportan)')
    return p


def locate_study(provenance, study=None, search=()):
    """Ruta del estudio original, verificada por SHA-256."""
    p = base_study(provenance)
    sha = p['source_sha256']
    if study:
        candidates = [Path(study)]
    else:
        candidates = [Path(p['source_path'])] if p.get('source_path') else []
        candidates += [f for folder in search for f in sorted(Path(folder).glob(f'*-{sha[:12]}.sxst'))]
    for path in candidates:
        if path.is_file() and _sha256(path) == sha:
            return path.resolve(), p
    raise ExportError(f'No se encontró el estudio original con SHA-256 {sha} '
                      f'({p.get("source_path")}); indicarlo explícitamente si se movió')


def _compare(expected, got):
    """Diferencias entre las tablas del proyecto y las releídas de la salida, por clave."""
    problems, worst = [], 0.0
    for table in sorted(set(expected) | set(got)):
        a, b = expected.get(table, []), got.get(table, [])
        names = KEYS.get(table)
        if names is None:
            if sorted(map(repr, (sorted(r.items()) for r in a))) != sorted(map(repr, (sorted(r.items()) for r in b))):
                problems.append(f'{table}: difiere al releer la salida')
            continue
        ga, gb = _group(a, names), _group(b, names)
        for key in ga.keys() ^ gb.keys():
            problems.append(f'{table} {key}: {"falta" if key in ga else "sobra"} al releer la salida')
        for key in ga.keys() & gb.keys():
            if len(ga[key]) != len(gb[key]):
                problems.append(f'{table} {key}: {len(ga[key])} filas en el proyecto y {len(gb[key])} en la salida')
                continue
            for x, y in zip(ga[key], gb[key]):
                for col in x.keys() & y.keys():
                    if not _same(x[col], y[col]):
                        problems.append(f'{table} {key} {col}: {x[col]!r} en el proyecto, {y[col]!r} en la salida')
                    elif isinstance(x[col], float) and isinstance(y[col], (int, float)):
                        worst = max(worst, abs(x[col] - y[col]))
    return problems, worst


def export(tables, provenance, output, study=None, search=(), journal=None):
    """Escribir `output` (.sxst) desde el estudio original más los cambios de `tables`; devuelve el informe."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f'El destino ya existe: {output}')
    path, origin = locate_study(provenance, study, search)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != origin['source_sha256']:
        raise ExportError('El estudio original cambió durante la lectura')
    networks = origin.get('network_ids')
    base, _ = sxst_import.read_root(ET.fromstring(raw), networks)
    current = {name: tables.get(name, []) for name in set(base) | (set(tables) & set(KEYS) | set(STATIC))}
    doc = _Doc(raw)
    writer = _Writer(doc, base, current)
    writer.run()
    if writer.problems:
        raise ExportError('No se puede exportar sin perder o inventar cambios', writer.problems)
    data = doc.render()
    reread, _ = sxst_import.read_root(ET.fromstring(data), networks)
    problems, worst = _compare(current, reread)
    if problems:
        raise ExportError('La salida no reproduce el proyecto al volver a importarla; no se escribió', problems)
    with open(output, 'xb') as stream:
        stream.write(data)
    return {'report_format': 'nandu-sxst-export-v1', 'nandu_version': __version__,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'base_study': {'path': str(path), 'sha256': origin['source_sha256'],
                           'importer_version': origin.get('importer_version'), 'networks': networks},
            'output': {'path': str(output.resolve()), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)},
            'changes': writer.log, 'change_count': len(writer.log), 'edit_journal_entries': len(journal or []),
            'verification': {'reimport': 'tablas iguales al proyecto', 'max_abs_rounding': worst,
                             'tolerance': {'abs': TOLERANCE[0], 'rel': TOLERANCE[1]},
                             'unchanged_bytes': 'todo lo que no figura en changes se copia sin modificar'},
            'rules': ['Los elementos nuevos copian el primer elemento del mismo tipo de la red y reemplazan '
                      'los campos que Ñandu conoce; el resto de sus datos es el de esa plantilla.',
                      'Una carga nueva conserva solo el modelo de carga 1 y un cliente por número.',
                      'Los números se escriben con los decimales del valor original (6 en CYME).']}
