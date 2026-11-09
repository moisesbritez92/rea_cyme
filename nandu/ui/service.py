"""Datos para la interfaz: geometría, explorador, propiedades y resultados con formato CYME."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import threading

from ..storage import inspect, load_source, save
from ..topology import DEVICES, PHASES
from .. import cyme_model
from . import editing

ABC = 'ABC'
DEVICE_NAMES = {1: 'Underground', 2: 'OverheadLine', 4: 'Regulator', 5: 'Transformer', 8: 'Breaker', 10: 'Recloser',
                13: 'Switch', 14: 'Fuse', 17: 'ShuntCapacitor', 20: 'SpotLoad', 23: 'OverheadLineUnbalanced'}
LOCATIONS = {0: 'Middle', 1: 'From', 2: 'To'}
# Textos de CYME para las conexiones y modos internos del modelo (mismos nombres que DECODE).
CONNECTION_TEXT = {'wye_grounded': 'Yg', 'delta': 'D', 'closed_delta': 'ClosedDelta'}
TAP_MODE_TEXT = {'fixed': 'FixedTap', 'load_center': 'LoadCenter', 'rx': 'RXSettings', 'terminal': 'Terminal'}
VOLTAGE_LIMITS = (0.95, 1.05)  # Informe CYME «Buses outside limits»: alguna fase fuera de la banda.
EXTRA_TABLES = {'CYMNODE', 'CYMNETWORK', 'CYMDBPARAMETERS', 'CYMEQOVERHEADLINE', 'CYMEQCABLE', 'CYMEQOVERHEADLINEUNBALANCED',
                'CYMEQREGULATOR', 'CYMEQSHUNTCAPACITOR', 'CYMEQSWITCH', 'CYMEQRECLOSER', 'CYMEQBREAKER',
                'CYMEQFUSE', 'CYMEQSOURCE', 'CYMEQTRANSFORMER'}
EQUIPMENT_TABLE = {1: ('CableId', 'CYMEQCABLE'), 2: ('LineId', 'CYMEQOVERHEADLINE'),
                   23: ('LineId', 'CYMEQOVERHEADLINEUNBALANCED'), 4: ('EquipmentId', 'CYMEQREGULATOR'),
                   5: ('EquipmentId', 'CYMEQTRANSFORMER'), 8: ('EquipmentId', 'CYMEQBREAKER'), 10: ('EquipmentId', 'CYMEQRECLOSER'),
                   13: ('EquipmentId', 'CYMEQSWITCH'), 14: ('EquipmentId', 'CYMEQFUSE'),
                   17: ('EquipmentId', 'CYMEQSHUNTCAPACITOR')}


def colorref(value):
    """COLORREF de Windows (0x00BBGGRR) a #rrggbb."""
    if not isinstance(value, int) or value < 0:
        return '#1f3a93'
    return '#{:02x}{:02x}{:02x}'.format(value & 255, (value >> 8) & 255, (value >> 16) & 255)


def _r(value, digits=3):
    return None if value is None else round(float(value), digits)


def _pf(s):
    return _r(100 * abs(s.real) / abs(s), 2) if abs(s) > 1e-9 else 0.0


def _utm_to_mercator(x, y, zone, south):
    """Inversión WGS84 UTM y proyección esférica EPSG:3857 (metros)."""
    a = 6378137.0
    e2 = 0.0066943799901413165
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    m = (y - (10000000 if south else 0)) / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
           + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
           + 151 * e1 ** 3 / 96 * math.sin(6 * mu) + 1097 * e1 ** 4 / 512 * math.sin(8 * mu))
    c = ep2 * math.cos(phi) ** 2
    t = math.tan(phi) ** 2
    n = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    r = a * (1 - e2) / (1 - e2 * math.sin(phi) ** 2) ** 1.5
    d = (x - 500000) / (n * k0)
    lat = phi - n * math.tan(phi) / r * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c ** 2 - 9 * ep2) * d ** 4 / 24
                                          + (61 + 90 * t + 298 * c + 45 * t ** 2 - 252 * ep2 - 3 * c ** 2) * d ** 6 / 720)
    lon = math.radians(zone * 6 - 183) + (d - (1 + 2 * t + c) * d ** 3 / 6
                                             + (5 - 2 * c + 28 * t - 3 * c ** 2 + 8 * ep2 + 24 * t ** 2) * d ** 5 / 120) / math.cos(phi)
    if not -math.radians(85.05112878) < lat < math.radians(85.05112878):
        raise ValueError('Latitud fuera de la cobertura del mapa')
    return a * lon, a * math.log(math.tan(math.pi / 4 + lat / 2))


def _mercator_to_utm(mx, my, zone, south):
    """Inversa de _utm_to_mercator: EPSG:3857 (metros) a WGS84 UTM, serie de Snyder."""
    a = 6378137.0
    e2 = 0.0066943799901413165
    ep2 = e2 / (1 - e2)
    k0 = 0.9996
    lon = mx / a
    lat = 2 * math.atan(math.exp(my / a)) - math.pi / 2
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    aa = math.cos(lat) * (lon - math.radians(zone * 6 - 183))
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat)
             - 35 * e2 ** 3 / 3072 * math.sin(6 * lat))
    x = k0 * n * (aa + (1 - t + c) * aa ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * aa ** 5 / 120) + 500000
    y = k0 * (m + n * math.tan(lat) * (aa ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * aa ** 4 / 24
                                       + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return x, y + (10000000 if south else 0)


class Project:
    """Un archivo .nandu.sqlite abierto en la interfaz, con su último resultado."""

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.info = inspect(self.path)
        self.schemas, self.tables = load_source(self.path)
        self.editor = editing.Editor(self.tables)
        # Lecturas de la base al abrir: referencia para avisar cuando un cálculo usa medidores editados.
        self.meter_base = [dict(r) for r in self.tables.get('CYMNETWORKMETER', [])]
        self.lock = threading.Lock()
        self.model = None
        self.last = None
        self._index()

    def _index(self):
        t = self.tables
        self.rows = {}
        for table in {v[0] for v in DEVICES.values()} | {'CYMREGULATOR', 'CYMSHUNTCAPACITOR'}:
            for row in t.get(table, []):
                self.rows[(table, row.get('NetworkId'), row.get('DeviceNumber'))] = row
        self.equipment = {(table, r.get('EquipmentId')): r for table in EXTRA_TABLES if table.startswith('CYMEQ')
                          for r in t.get(table, [])}
        self.sections = {r['SectionId']: r for r in t.get('CYMSECTION', [])}
        self.devices = defaultdict(list)
        for row in t.get('CYMSECTIONDEVICE', []):
            self.devices[row['SectionId']].append(row)
        # Forma del tramo: CYMINTERMEDIATEPOINT, PointIndex desde 0 de From a To, sin los extremos.
        self.points = defaultdict(list)
        for row in t.get('CYMINTERMEDIATEPOINT', []):
            if row.get('X') is not None and row.get('Y') is not None:
                self.points[(row.get('NetworkId'), row['SectionId'])].append(row)
        self.nodes = {}
        for row in t.get('CYMNODE', []):
            self.nodes.setdefault(row['NodeId'], row)

    # --- Geometría y explorador -------------------------------------------------------------
    def geometry(self):
        networks = [{'id': r['NetworkId'], 'color': colorref(r.get('Color'))} for r in self.tables.get('CYMNETWORK', [])]
        nodes = [{'id': n, 'x': r['X'], 'y': r['Y'], 'network': r.get('NetworkId')}
                 for n, r in self.nodes.items() if r.get('X') is not None and r.get('Y') is not None]
        sections = []
        for sid, sec in self.sections.items():
            devs = [{'type': DEVICE_NAMES.get(d['DeviceType'], d.get('DeviceTypeText') or str(d['DeviceType'])),
                     'number': d['DeviceNumber'],
                     'location': LOCATIONS.get(d['Location'], 'To'), **self._device_state(d)}
                    for d in self.devices.get(sid, [])]
            points = sorted(self.points.get((sec['NetworkId'], sid), []) or self.points.get((None, sid), []),
                            key=lambda r: r['PointIndex'])
            sections.append({'id': sid, 'network': sec['NetworkId'], 'from': sec['FromNodeId'], 'to': sec['ToNodeId'],
                             'phases': PHASES.get(sec['Phase'], ''), 'devices': devs,
                             'points': [{'x': r['X'], 'y': r['Y']} for r in points]})
        sources = [{'id': r['DeviceNumber'], 'network': r['NetworkId'], 'node': r['NodeId']}
                   for r in self.tables.get('CYMSOURCE', [])]
        xs = [n['x'] for n in nodes] or [0]
        ys = [n['y'] for n in nodes] or [0]
        crs = next((r['ValueStr'] for r in self.tables.get('CYMDBPARAMETERS', [])
                    if r.get('ID') == 'CoordinateSystem'), None)
        map_bounds = None
        if isinstance(crs, str) and crs.upper().startswith('EPSG:'):
            try:
                epsg = int(crs.split(':', 1)[1])
                if 32601 <= epsg <= 32660 or 32701 <= epsg <= 32760:
                    zone = epsg % 100
                    projected = [_utm_to_mercator(n['x'], n['y'], zone, epsg >= 32700) for n in nodes]
                    for n, (mx, my) in zip(nodes, projected):
                        n['mx'], n['my'] = mx, my
                    for sec in sections:
                        for q in sec['points']:
                            q['mx'], q['my'] = _utm_to_mercator(q['x'], q['y'], zone, epsg >= 32700)
                    if projected:
                        map_bounds = [min(p[0] for p in projected), min(p[1] for p in projected),
                                      max(p[0] for p in projected), max(p[1] for p in projected)]
            except (ValueError, OverflowError, TypeError):
                pass  # CRS o posiciones no utilizables: conservar el diagrama sin fondo.
        return {'file': str(self.path), 'networks': networks, 'nodes': nodes, 'sections': sections,
                 'sources': sources, 'meters': self.meters(), 'bounds': [min(xs), min(ys), max(xs), max(ys)],
                 'crs': crs, 'map_bounds': map_bounds}

    def meters(self):
        """Lecturas actuales de CYMNETWORKMETER y si difieren de las del archivo abierto."""
        changed = {(d['network'], d['phase']) for d in cyme_model.meter_differences(
            self.meter_base, self.tables.get('CYMNETWORKMETER', []))}
        return [{'network': r.get('NetworkId'), 'node': r.get('NodeId'), 'demand_type': r.get('DemandType'),
                 'phases': {p: {'amps': r.get(fa), 'pf_pct': r.get(fp), 'modified': (r.get('NetworkId'), p) in changed}
                            for p, (fa, fp) in cyme_model.METER_FIELDS.items()}}
                for r in self.tables.get('CYMNETWORKMETER', [])]

    def _device_state(self, attachment):
        code = attachment['DeviceType']
        if code not in (8, 10, 13, 14):
            return {}
        row = self.rows.get((DEVICES[code][0], attachment['NetworkId'], attachment['DeviceNumber']), {})
        closed = PHASES.get(row.get('ClosedPhase'), '')
        return {'closed': closed}

    def explorer(self):
        tree = {}
        for net in self.tables.get('CYMNETWORK', []):
            tree[net['NetworkId']] = defaultdict(list)
        for sid, devs in self.devices.items():
            for d in devs:
                name = DEVICE_NAMES.get(d['DeviceType'])
                if name and name not in ('OverheadLine', 'OverheadLineUnbalanced', 'Underground', 'SpotLoad'):
                    tree.setdefault(d['NetworkId'], defaultdict(list))[name].append(
                        {'number': d['DeviceNumber'], 'section': sid})
        counts = defaultdict(lambda: defaultdict(int))
        for sid, devs in self.devices.items():
            for d in devs:
                counts[d['NetworkId']][DEVICE_NAMES.get(d['DeviceType'], '?')] += 1
        for r in self.tables.get('CYMSOURCE', []):
            tree.setdefault(r['NetworkId'], defaultdict(list))['Source'].append(
                {'number': r['DeviceNumber'], 'node': r['NodeId']})
        for r in self.tables.get('CYMNETWORKMETER', []):
            tree.setdefault(r['NetworkId'], defaultdict(list))['Meter'].append(
                {'number': f'{r["NetworkId"]} @ {r["NodeId"]}', 'node': r['NodeId']})
        return [{'network': net, 'groups': {k: sorted(v, key=lambda x: x['number']) for k, v in groups.items()},
                 'counts': dict(counts[net])} for net, groups in sorted(tree.items())]

    # --- Propiedades ----------------------------------------------------------------------
    def _fields(self, row, table=None, labels=None, only=None):
        """Campos [nombre, valor mostrado, edición]; la edición indica tabla, clave, valor crudo y opciones."""
        out = []
        key = {k: row.get(k) for k in editing.KEYS.get(table, ())}
        for k, v in sorted(row.items()) if only is None else ((k, row.get(k)) for k in only if k in row):
            if k in ('LastChange', 'ComponentMask', 'Flags'):
                continue
            shown = next(iter(v.values())) if isinstance(v, dict) else v
            shown = DECODE[k](shown) if k in DECODE and shown is not None else shown
            meta = None
            if table and editing.editable(table, k) and not isinstance(v, bool):
                meta = {'table': table, 'key': key, 'field': k, 'raw': shown if isinstance(v, dict) else v,
                        'options': editing.options(self.tables, table, k)}
            out.append([(labels or {}).get(k, k), shown, meta])
        return out

    def properties(self, kind, ident):
        if kind == 'node':
            row = self.nodes.get(ident)
            if row is None:
                raise KeyError(f'Nodo inexistente: {ident}')
            meters = [{'name': f'Meter {m["NetworkId"]}' + (' (A-PF)' if m.get('DemandType') == cyme_model.METER_AMP_PF else ''),
                       'fields': self._fields(m, 'CYMNETWORKMETER', METER_LABELS, list(METER_LABELS))}
                      for m in self.tables.get('CYMNETWORKMETER', []) if m.get('NodeId') == ident]
            out = {'title': f'Node {ident}',
                   'groups': meters + [{'name': 'Information', 'fields': self._fields(row, 'CYMNODE')}]}
            if self.last and ident in self.last['nodes']:
                out['groups'].insert(0, {'name': 'Load Flow', 'fields': [
                    [f'V{p} (kVLN)', v['kv']] for p, v in self.last['nodes'][ident].items()] + [
                    [f'V{p} (%)', v['pct']] for p, v in self.last['nodes'][ident].items()]})
            return out
        sec = self.sections.get(ident)
        if sec is None:
            raise KeyError(f'Tramo inexistente: {ident}')
        groups = [{'name': 'Section', 'fields': self._fields(sec, 'CYMSECTION')}]
        if self.last and ident in self.last['sections']:
            r = self.last['sections'][ident]
            groups.insert(0, {'name': 'Load Flow', 'fields': [[f'I{p} (A)', v] for p, v in r['current_a'].items()]
                              + [['Loading (%)', r['loading_pct']], ['Thru Power (kW)', r['p_kw']],
                                 ['Thru Power (kvar)', r['q_kvar']], ['Losses (kW)', r['loss_kw']]]})
        for d in self.devices.get(ident, []):
            code = d['DeviceType']
            table = DEVICES.get(code, (None,))[0]
            row = self.rows.get((table, d['NetworkId'], d['DeviceNumber']), {})
            name = DEVICE_NAMES.get(code, str(code))
            groups.append({'name': f'{name} {d["DeviceNumber"]}', 'device': {'type': code, 'number': d['DeviceNumber']},
                           'fields': self._fields(d, 'CYMSECTIONDEVICE', only=['Location'])
                           + self._fields(row, table)})
            if code in EQUIPMENT_TABLE:
                field, eq_table = EQUIPMENT_TABLE[code]
                eq = self.equipment.get((eq_table, row.get(field)))
                if eq:
                    users = sum(1 for r in self.tables.get(table, []) if r.get(field) == row.get(field))
                    groups.append({'name': f'Equipment {row.get(field)} (catalog, {users} devices)',
                                   'fields': self._fields(eq, eq_table), 'collapsed': True})
            if code == 20:
                customers = [c for c in self.tables.get('CYMCUSTOMERLOAD', [])
                             if c['NetworkId'] == d['NetworkId'] and c['DeviceNumber'] == d['DeviceNumber']]
                for c in customers:
                    groups.append({'name': f'Customer {c["CustomerNumber"]} ({PHASES.get(c["Phase"])})',
                                   'fields': self._fields(c, 'CYMCUSTOMERLOAD', CUSTOMER_LABELS, list(CUSTOMER_LABELS))})
        return {'title': f'Section {ident}', 'groups': groups}

    # --- Edición --------------------------------------------------------------------------
    def _map_zone(self):
        crs = next((r['ValueStr'] for r in self.tables.get('CYMDBPARAMETERS', []) if r.get('ID') == 'CoordinateSystem'), '')
        epsg = int(crs.split(':', 1)[1]) if isinstance(crs, str) and crs.upper().startswith('EPSG:') else 0
        if not (32601 <= epsg <= 32660 or 32701 <= epsg <= 32760):
            raise ValueError('El sistema de coordenadas no admite edición sobre el mapa')
        return epsg % 100, epsg >= 32700

    def _native(self, op):
        """Las coordenadas llegan en EPSG:3857 cuando la vista tiene fondo de mapa."""
        if op.get('map') and 'x' in op:
            x, y = _mercator_to_utm(float(op['x']), float(op['y']), *self._map_zone())
            op = {**op, 'x': x, 'y': y}
        op.pop('map', None)
        return op

    def edit(self, op):
        with self.lock:
            entry = self.editor.apply(self._native(dict(op)))
            self._changed()
            return entry

    def undo(self):
        with self.lock:
            entry = self.editor.undo()
            self._changed()
            return entry

    def _changed(self):
        # Un resultado calculado sobre la red anterior ya no corresponde a la red editada.
        self.model = None
        self.last = None
        self._index()

    def edit_state(self):
        journal = self.editor.journal
        return {'count': len(journal), 'last': journal[-1] if journal else None}

    def catalog(self):
        """Opciones para los diálogos de alta de tramos y equipos."""
        lines = {code: {'name': DEVICE_NAMES[code], 'equipment': [k for k, _ in editing.options(self.tables, table, field)]}
                 for code, (table, field, _) in editing.LINE_TYPES.items()}
        devices = {}
        for code, name in editing.ADDABLE.items():
            table = DEVICES[code][0]
            ref = (table, 'EquipmentId')
            devices[code] = {'name': name, 'available': bool(self.tables.get(table)),
                             'equipment': [k for k, _ in editing.options(self.tables, *ref)] if ref in editing.REFERENCES else []}
        return {'lines': lines, 'devices': devices, 'phases': editing.ENUMS['Phase'],
                'locations': editing.ENUMS['Location'], 'networks': [r['NetworkId'] for r in self.tables.get('CYMNETWORK', [])]}

    def save_as(self, output):
        """Publicar la red editada como proyecto nuevo; el original no se modifica."""
        from ..model import build_model, validate
        with self.lock:
            if not self.editor.journal:
                raise ValueError('No hay cambios que guardar')
            digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
            provenance = {'derived_from': {'file': str(self.path), 'sha256': digest,
                                           'provenance': self.info['provenance']},
                          'edits': self.editor.journal, 'created_at': datetime.now(timezone.utc).isoformat(),
                          'excluded_tables': self.info['provenance'].get('excluded_tables', [])}
            schemas = {name: self.schemas.get(name) or {'columns': [{'name': k} for k in (rows[0] if rows else {})],
                                                        'scope': 'edited'}
                       for name, rows in self.tables.items()}
            model = build_model(self.tables)
            save(output, provenance, schemas, self.tables, model, validate(model))
        return str(Path(output).resolve())

    # --- Estudios CYME (.sxst) ------------------------------------------------------------
    def export_info(self, search=()):
        """Si el proyecto se puede exportar por ida y vuelta y desde qué estudio."""
        from ..sxst_export import ExportError, locate_study
        try:
            path, origin = locate_study(self.info['provenance'], search=search)
        except ExportError as exc:
            return {'available': False, 'reason': str(exc)}
        return {'available': True, 'study': str(path), 'networks': origin.get('network_ids'),
                'pending_edits': len(self.editor.journal), 'suggested_name': self.path.name.replace('.nandu.sqlite', '')}

    def export_sxst(self, output, search=()):
        """Estudio CYME con la red tal como está (ediciones sin guardar incluidas); el original no cambia."""
        from ..sxst_export import export
        with self.lock:
            return export(self.tables, self.info['provenance'], output, search=search, journal=self.editor.journal)

    # --- Cálculos -------------------------------------------------------------------------
    def _model(self):
        if self.model is None:
            self.model = cyme_model.build(self.tables)
        return self.model

    def run(self, analysis, options=None):
        from .. import allocation, estimator, nodal  # numpy/scipy solo al calcular
        options = options or {}
        with self.lock:
            model = self._model()
            if analysis == 'loadflow':
                sol = nodal.solve_controlled(model)
                res = nodal.results(sol) if sol['status'] == 'converged' else None
                extra = {'tap_control': sol['tap_control']}
            elif analysis == 'allocate':
                method = options.get('method', 'KVAMethod')
                alloc = allocation.allocate(model, estimator.meters_from_model(model, None), method)
                sol = alloc['solution'] or {'status': alloc['status'], 'iterations': []}
                sol = {**sol, 'status': alloc['status'],
                       'iterations': [{'iteration': h['iteration'], 'max_dv_pu': 0.0,
                                       'mismatch_pct': h['max_mismatch_pct']} for h in alloc['iterations']]}
                res = nodal.results(sol) if alloc['status'] == 'converged' else None
                extra = {'method': method, 'zero_weight_loads': alloc['zero_weight_loads'],
                         'loads': [{'id': item[5]['id'], 'network': item[5]['network_id'],
                                    'initial_kw': _r(item[1].real / 1000), 'initial_kvar': _r(item[1].imag / 1000),
                                    'estimated_kw': _r(p.real / 1000), 'estimated_kvar': _r(p.imag / 1000)}
                                   for item, p in zip(alloc['prepared'], alloc['powers'])]}
            elif analysis == 'estimate':
                est = estimator.estimate(model, options={
                    'initialization': options.get('initialization', 'None'),
                    'allocation_method': options.get('method', 'KVAMethod'),
                    'quality_indices': bool(options.get('quality'))})
                sol, res = est['flow'], est['results'] if est['status'] == 'converged' else None
                sol = {**sol, 'status': est['status'], 'iterations': est['iterations']}
                extra = {'meter_residuals': [{k: (float(v) if hasattr(v, 'dtype') else v) for k, v in r.items()}
                                             for r in est['meter_residuals']],
                         'solution_cost': float(est['solution_cost']),
                         'quality_indices': est['quality_indices'],
                         'loads': [{'id': r['id'], 'network': r['network_id'],
                                    'initial_kw': _r(r['initial_kva'].real), 'initial_kvar': _r(r['initial_kva'].imag),
                                    'estimated_kw': _r(r['estimated_nominal_kva'].real),
                                    'estimated_kvar': _r(r['estimated_nominal_kva'].imag)} for r in est['loads']]}
            else:
                raise ValueError(f'Análisis desconocido: {analysis}')
            self.last = self._format(analysis, model, sol, res, extra)
            if analysis in ('estimate', 'allocate'):
                diffs = cyme_model.meter_differences(self.meter_base, self.tables.get('CYMNETWORKMETER', []))
                self.last['meter_overrides'] = diffs
                if diffs:
                    text = ', '.join(f'{d["network"]} {d["phase"]}: {d["before"]["amps"]} A/{d["before"]["pf_pct"]} % '
                                     f'-> {d["after"]["amps"]} A/{d["after"]["pf_pct"]} %' for d in diffs)
                    self.last['messages'].insert(0, {'level': 'warning', 'text':
                        f'Medidores modificados respecto del archivo ({text}): no comparable con CYME sobre la base'})
            return self.last

    def _format(self, analysis, model, sol, res, extra):
        out = {'analysis': analysis, 'status': sol['status'], 'iterations': sol['iterations'],
               'messages': _messages(model, sol), 'nodes': {}, 'sections': {}, **extra}
        for step in (extra.get('tap_control') or {}).get('history', []):
            for c in step['tap_changes']:
                out['messages'].append({'level': 'info', 'text': f'Iter({step["iteration"]}) - Regulator({c["regulator"]}) '
                                        f'fase {c["phase"]}: Old Tap:{c["old_tap"]:.1f}  New Tap:{c["new_tap"]:.1f} '
                                        f'(control {c["control_v"]} V)'})
        if extra.get('zero_weight_loads'):
            out['messages'].insert(0, {'level': 'warning', 'text':
                f'{extra["zero_weight_loads"]} cargas sin base para {extra["method"]} (p. ej. sin kVA conectado): reciben 0'})
        if res is None:
            return out
        for bus, phases in res['voltages'].items():
            if model['buses'][bus]['internal']:
                continue
            base = (model['buses'][bus]['nominal_kvll'] or 0) * 1000 / math.sqrt(3)
            out['nodes'][bus] = {p: {'kv': _r(abs(d['v']) / 1000, 4), 'pct': _r(100 * d['pu'], 2),
                                     'angle': _r(d['angle_deg'], 2)}
                                 for p, d in phases.items() if not d.get('deenergized')}
        by_section = defaultdict(list)
        for line in model['lines']:
            by_section[line['section_id']].append(line)
        for sec in model['sections']:
            lines = by_section.get(sec['id'], [])
            branch = res['branches'].get(lines[0]['id']) if lines else None
            if branch is not None:
                cur = dict(zip(branch['phases'], (abs(i) for i in branch['i_from'])))
                s = sum(branch['s_from'])
                loss = sum(res['branches'][l['id']]['loss'].sum() for l in lines if l['id'] in res['branches'])
                rating = lines[0].get('rating_a') or 0
            else:
                aux = next((v for (kind, ident), v in res['aux'].items()
                            if kind in ('switch', 'regulator') and ident in sec['chain']), None)
                if aux is None:
                    continue
                cur, s, loss, rating = {p: abs(i) for p, i in aux.items()}, 0j, 0j, 0
            # Utilización por fase; el máximo del equipo se informa aparte. La capacidad del catálogo es
            # una sola por conductor de fase (cyme_model), no hay capacidades distintas por fase.
            phase_loading = {p: _r(100 * v / rating, 1) for p, v in cur.items()} if rating else {}
            out['sections'][sec['id']] = {
                'current_a': {p: _r(v, 2) for p, v in cur.items()},
                'loading_pct': max(phase_loading.values()) if phase_loading else None,
                'loading_pct_phase': phase_loading,
                # Signo conservado: positivo en el sentido From -> To del tramo.
                'p_kw': _r(s.real / 1000, 2), 'q_kvar': _r(s.imag / 1000, 2),
                'loss_kw': _r(loss.real / 1000, 3)}
        out['summary'] = _summary(model, res)
        out['abnormal'] = _abnormal(out)
        out['regulators'] = _regulators(sol.get('model') or model, sol, res)
        out['capacitors'] = _capacitors(model, res)
        out['profile'] = _profile(model, out)
        return out


# Solo códigos verificados (ver docs/cyme-ingenieria-inversa.md); el resto se muestra crudo.
DECODE = {
    'Phase': lambda v: PHASES.get(v, v), 'ClosedPhase': lambda v: PHASES.get(v, v) or 'None',
    'ControlStatus': lambda v: PHASES.get(v, v),
    'ConnectionConfiguration': lambda v: {0: 'Yg', 1: 'Y', 2: 'D', 3: 'OpenDelta', 4: 'ClosedDelta'}.get(v, v),
    'SettingOption': lambda v: {0: 'LoadCenter', 1: 'RXSettings', 2: 'FixedTap', 3: 'Terminal',
                                4: 'FixedTapPrimary', 5: 'PythonScript'}.get(v, v),
    'LoadValueType': lambda v: {0: 'KW_KVAR'}.get(v, v),
    'DeviceType': lambda v: DEVICE_NAMES.get(v, v), 'Location': lambda v: LOCATIONS.get(v, v),
}
METER_LABELS = {**{f: f'{"Current" if f.startswith("Val1") else "PF"} {f[-1]} ({"A" if f.startswith("Val1") else "%"})'
                    for f in ('Val1A', 'Val1B', 'Val1C', 'Val2A', 'Val2B', 'Val2C')}, 'DemandType': 'Demand Type'}
CUSTOMER_LABELS = {'LoadValue1': 'kW', 'LoadValue2': 'kvar', 'Phase': 'Phase', 'ConsumerClassId': 'Customer Type',
                   'ConnectedKVA': 'Connected kVA', 'KWHUsage': 'kWh', 'LockDuringLoadAllocation': 'Locked',
                   'Status': 'Status'}


def _messages(model, sol):
    msgs = [{'level': 'warning', 'text': f'{i["code"]}: {i.get("reason", "")} {i.get("device_id", i.get("section_id", ""))}'.strip()}
            for i in model['issues']]
    for kind, items in sol.get('system', {}).get('skipped', {}).items():
        msgs.append({'level': 'warning', 'text': f'{len(items)} {kind} sin energía o sin conexión'})
    last = sol['iterations'][-1] if sol['iterations'] else {}
    detail = (f'desajuste final {last["mismatch_pct"]:.3f} % en los medidores' if 'mismatch_pct' in last
              else f'dV final {last.get("max_dv_pu", 0):.2e} pu')
    msgs.append({'level': 'info' if sol['status'] == 'converged' else 'error',
                 'text': f'{sol["status"]} en {len(sol["iterations"])} iteraciones; {detail}'})
    return msgs


def _summary(model, res):
    """Equivalente al «Load Flow - Summary Report» de CYME, por red y total."""
    rows = {}
    shunt_line, shunt_cable, loss_line, loss_cable = (defaultdict(complex) for _ in range(4))
    for line in model['lines']:
        b = res['branches'].get(line['id'])
        if b is None:
            continue
        v_from = [res['voltages'][line['from']][p]['v'] for p in b['phases']]
        v_to = [res['voltages'][line['to']][p]['v'] for p in b['phases']]
        net = line['network_id']
        y = [[c / 2 for c in r] for r in line['y']]
        idx = [line['phases'].index(p) for p in b['phases']]
        shunt = 0j
        for vs in (v_from, v_to):
            for a, ia in enumerate(idx):
                i = sum(y[ia][ib] * vs[k] for k, ib in enumerate(idx))
                shunt += vs[a] * i.conjugate()
        series = b['loss'].sum() - shunt
        (shunt_cable if line.get('device_code') == 1 else shunt_line)[net] += shunt
        (loss_cable if line.get('device_code') == 1 else loss_line)[net] += series
    caps = defaultdict(complex)
    for cap in model['capacitors']:
        v = res['voltages'].get(cap['bus'], {})
        vals = [v.get(p, {}).get('v') for p in cap['phases']]
        if None in vals or any(v.get(p, {}).get('deenergized') for p in cap['phases']):
            continue
        branch = vals[0] - (vals[1] if len(vals) == 2 else 0)
        caps[model['buses'][cap['bus']]['network_id']] += -1j * cap['rated_q_var'] * abs(branch) ** 2 / cap['rated_v'] ** 2
    for net in model['networks'] + ['Total']:
        pick = (lambda d: sum(d.values(), 0j)) if net == 'Total' else (lambda d: d.get(net, 0j))
        src = sum((sum(s['power'].values(), 0j) for s in res['sources'].values()
                   if net == 'Total' or s['network_id'] == net), 0j)
        load = pick(res['load_totals'])
        items = [('Sources (Swing)', src), ('Total Generation', src),
                 ('Load used (Adjusted)', load), ('Shunt capacitors (Adjusted)', pick(caps)),
                 ('Total Loads', load + pick(caps)),
                 ('Cable Capacitance', pick(shunt_cable)), ('Line Capacitance', pick(shunt_line)),
                 ('Total Shunt Capacitance', pick(shunt_cable) + pick(shunt_line)),
                 ('Line Losses', pick(loss_line)), ('Cable Losses', pick(loss_cable)),
                 ('Total Losses', pick(loss_line) + pick(loss_cable))]
        rows[net] = [{'item': k, 'kw': _r(v.real / 1000, 2), 'kvar': _r(v.imag / 1000, 2),
                      'kva': _r(abs(v) / 1000, 2), 'pf': _pf(v)} for k, v in items]
    return rows


def _bus_voltage(res, bus, p):
    d = res['voltages'].get(bus, {}).get(p)
    return None if d is None or d.get('deenergized') else d


def _regulators(model, sol, res):
    """Informe «Regulators» por fase: tap final, tensiones de entrada y salida, corriente y control.

    Entrada y salida son las que orientó el motor (`input_bus`/`output_bus`), no el sentido del tramo.
    La corriente auxiliar es la del lado de salida; la de entrada es Mᵀ·I_salida (transformador ideal).
    CYME informa la de entrada (contraste del 27 oct. 2026: I_CYME/I_salida = Vout/Vin).
    """
    from .. import nodal  # solo al calcular
    rows = []
    infos = sol.get('system', {}).get('regulators', {})
    for reg in model['regulators']:
        info = infos.get(reg['id'])
        control = nodal.control_voltages(sol, reg) if info and 'net' in sol else {}
        current = res['aux'].get(('regulator', reg['id']), {})
        current_in = {}
        if info and len(current) == len(reg['phases']):
            out_vec = [current[p] for p in reg['phases']]
            current_in = {p: sum(info['M'][i, j] * out_vec[i] for i in range(len(out_vec)))
                          for j, p in enumerate(reg['phases'])}
        bus_in, bus_out = (info['input_bus'], info['output_bus']) if info else (reg['from'], reg['to'])
        ctrl = reg.get('control') or {}
        for p in reg['phases']:
            v_in, v_out = _bus_voltage(res, bus_in, p), _bus_voltage(res, bus_out, p)
            rows.append({'id': reg['id'], 'section_id': reg['section_id'], 'network': reg['network_id'],
                         'equipment': reg.get('equipment_id'),
                         'connection': CONNECTION_TEXT.get(reg['connection'], reg['connection']),
                         'mode': TAP_MODE_TEXT.get(ctrl.get('mode', 'fixed'), ctrl.get('mode')),
                         'phase': p, 'tap': reg['taps'].get(p), 'max_tap': reg['max_tap'],
                         'v_in_kv': _r(abs(v_in['v']) / 1000, 4) if v_in else None,
                         'v_out_kv': _r(abs(v_out['v']) / 1000, 4) if v_out else None,
                         'v_out_pct': _r(100 * v_out['pu'], 2) if v_out else None,
                         'i_a': _r(abs(current_in[p]), 2) if p in current_in else None,
                         'i_out_a': _r(abs(current[p]), 2) if p in current else None,
                         'input_node': bus_in, 'output_node': bus_out,
                         'control_v': _r(control[p], 2) if p in control else None,
                         'set_v': ctrl.get('set_v', {}).get(p), 'bandwidth_v': ctrl.get('bandwidth_v', {}).get(p)})
    return rows


def _capacitors(model, res):
    """Informe «Shunt Capacitors»: kvar nominal y entregado a la tensión calculada (Q·|V|²/Vn²)."""
    rows = []
    for cap in model['capacitors']:
        vals = [_bus_voltage(res, cap['bus'], p) for p in cap['phases']]
        v = None if None in vals else abs(vals[0]['v'] - (vals[1]['v'] if len(vals) == 2 else 0))
        rows.append({'id': cap['device_id'], 'section_id': cap['section_id'],
                     'network': model['buses'][cap['bus']]['network_id'], 'node': cap['bus'],
                     'connection': CONNECTION_TEXT.get(cap['connection'], cap['connection']), 'phase': ''.join(cap['phases']),
                     'rated_kvar': _r(cap['rated_q_var'] / 1000, 2), 'rated_kv': _r(cap['rated_v'] / 1000, 3),
                     'v_kv': _r(v / 1000, 4) if v is not None else None,
                     'v_pct': _r(100 * v / cap['rated_v'], 2) if v is not None else None,
                     'kvar': _r(cap['rated_q_var'] * v ** 2 / cap['rated_v'] ** 2 / 1000, 2) if v is not None else None,
                     # «Loading» de CYME para capacitores = kvar entregados / nominales (contraste 27 oct. 2026).
                     'loading_pct': _r(100 * v ** 2 / cap['rated_v'] ** 2, 1) if v is not None else None})
    return rows


def _profile(model, out):
    """Distancia eléctrica a la fuente por nodo (suma de longitudes de línea, Dijkstra desde las fuentes).

    Solo recorre tramos con resultado y sin maniobras abiertas en todas sus fases, para no cruzar
    enlaces normalmente abiertos. `parent` permite trazar el camino fuente → nodo del perfil.
    """
    import heapq
    # Las mitades '#1'/'#2' de una línea partida por cargas al medio conservan la longitud completa.
    pieces = {}
    for line in model['lines']:
        pieces[(line['section_id'], line['id'].split('#')[0])] = line.get('length_m') or 0.0
    length = defaultdict(float)
    for (sid, _), meters in pieces.items():
        length[sid] += meters
    open_sections = {s['section_id'] for s in model['switches'] if not s['closed']}
    adj = defaultdict(list)
    for sec in model['sections']:
        if sec['id'] in out['sections'] and sec['id'] not in open_sections:
            adj[sec['from']].append((sec['to'], sec['id']))
            adj[sec['to']].append((sec['from'], sec['id']))
    best = {}
    heap = [(0.0, src['bus'], None, None) for src in model['sources']]
    heapq.heapify(heap)
    while heap:
        dist, node, parent, sid = heapq.heappop(heap)
        if node in best:
            continue
        best[node] = {'distance_m': _r(dist, 1), 'parent': parent, 'section': sid}
        for nxt, sec_id in adj[node]:
            if nxt not in best:
                heapq.heappush(heap, (dist + length[sec_id], nxt, node, sec_id))
    return {node: d for node, d in best.items() if node in out['nodes']}


def _abnormal(out):
    lo, hi = VOLTAGE_LIMITS
    rows = []
    for label, test in (('Under-Voltage', lambda v: v < 100 * lo), ('Over-Voltage', lambda v: v > 100 * hi)):
        for p in ABC:
            hits = [(v[p]['pct'], node) for node, v in out['nodes'].items() if p in v and test(v[p]['pct'])]
            worst = (min if label == 'Under-Voltage' else max)(hits) if hits else None
            rows.append({'condition': label, 'phase': p, 'count': len(hits),
                         'worst': worst[1] if worst else '', 'value': worst[0] if worst else None})
    for p in ABC:
        hits = []
        for sid, r in out['sections'].items():
            value = r.get('loading_pct_phase', {}).get(p)
            if value is not None and value > 100:
                hits.append((value, sid))
        worst = max(hits) if hits else None
        rows.append({'condition': 'Overload', 'phase': p, 'count': len(hits), 'worst': worst[1] if worst else '',
                     'value': worst[0] if worst else None})
    buses = sum(1 for v in out['nodes'].values() if any(not 100 * lo <= x['pct'] <= 100 * hi for x in v.values()))
    return {'rows': rows, 'buses_outside_limits': buses, 'limits_pct': [100 * lo, 100 * hi]}
