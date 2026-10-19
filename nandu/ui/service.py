"""Datos para la interfaz: geometría, explorador, propiedades y resultados con formato CYME."""
from collections import defaultdict
import math
from pathlib import Path
import threading

from ..storage import inspect, load_tables
from ..topology import DEVICES, PHASES
from .. import cyme_model

ABC = 'ABC'
DEVICE_NAMES = {1: 'Underground', 2: 'OverheadLine', 4: 'Regulator', 8: 'Breaker', 10: 'Recloser',
                13: 'Switch', 14: 'Fuse', 17: 'ShuntCapacitor', 20: 'SpotLoad', 23: 'OverheadLineUnbalanced'}
LOCATIONS = {0: 'Middle', 1: 'From', 2: 'To'}
VOLTAGE_LIMITS = (0.95, 1.05)  # Informe CYME «Buses outside limits»: alguna fase fuera de la banda.
EXTRA_TABLES = {'CYMNODE', 'CYMNETWORK', 'CYMDBPARAMETERS', 'CYMEQOVERHEADLINE', 'CYMEQCABLE', 'CYMEQOVERHEADLINEUNBALANCED',
                'CYMEQREGULATOR', 'CYMEQSHUNTCAPACITOR', 'CYMEQSWITCH', 'CYMEQRECLOSER', 'CYMEQBREAKER',
                'CYMEQFUSE', 'CYMEQSOURCE'}
EQUIPMENT_TABLE = {1: ('CableId', 'CYMEQCABLE'), 2: ('LineId', 'CYMEQOVERHEADLINE'),
                   23: ('LineId', 'CYMEQOVERHEADLINEUNBALANCED'), 4: ('EquipmentId', 'CYMEQREGULATOR'),
                   8: ('EquipmentId', 'CYMEQBREAKER'), 10: ('EquipmentId', 'CYMEQRECLOSER'),
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


class Project:
    """Un archivo .nandu.sqlite abierto en la interfaz, con su último resultado."""

    def __init__(self, path):
        self.path = Path(path).resolve(strict=True)
        self.info = inspect(self.path)
        self.tables = load_tables(self.path, cyme_model.TABLES | EXTRA_TABLES)
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
            devs = [{'type': DEVICE_NAMES.get(d['DeviceType'], str(d['DeviceType'])), 'number': d['DeviceNumber'],
                     'location': LOCATIONS.get(d['Location'], 'To'), **self._device_state(d)}
                    for d in self.devices.get(sid, [])]
            sections.append({'id': sid, 'network': sec['NetworkId'], 'from': sec['FromNodeId'], 'to': sec['ToNodeId'],
                             'phases': PHASES.get(sec['Phase'], ''), 'devices': devs})
        sources = [{'id': r['DeviceNumber'], 'network': r['NetworkId'], 'node': r['NodeId']}
                   for r in self.tables.get('CYMSOURCE', [])]
        xs = [n['x'] for n in nodes] or [0]
        ys = [n['y'] for n in nodes] or [0]
        return {'file': str(self.path), 'networks': networks, 'nodes': nodes, 'sections': sections,
                'sources': sources, 'bounds': [min(xs), min(ys), max(xs), max(ys)],
                'crs': next((r['ValueStr'] for r in self.tables.get('CYMDBPARAMETERS', [])
                             if r.get('ID') == 'CoordinateSystem'), None)}

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
        return [{'network': net, 'groups': {k: sorted(v, key=lambda x: x['number']) for k, v in groups.items()},
                 'counts': dict(counts[net])} for net, groups in sorted(tree.items())]

    # --- Propiedades ----------------------------------------------------------------------
    def properties(self, kind, ident):
        if kind == 'node':
            row = self.nodes.get(ident)
            if row is None:
                raise KeyError(f'Nodo inexistente: {ident}')
            out = {'title': f'Node {ident}', 'groups': [{'name': 'Information', 'fields': _fields(row)}]}
            if self.last and ident in self.last['nodes']:
                out['groups'].insert(0, {'name': 'Load Flow', 'fields': [
                    [f'V{p} (kVLN)', v['kv']] for p, v in self.last['nodes'][ident].items()] + [
                    [f'V{p} (%)', v['pct']] for p, v in self.last['nodes'][ident].items()]})
            return out
        sec = self.sections.get(ident)
        if sec is None:
            raise KeyError(f'Tramo inexistente: {ident}')
        groups = [{'name': 'Section', 'fields': _fields(sec)}]
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
            groups.append({'name': f'{name} {d["DeviceNumber"]}', 'fields': [['Location', LOCATIONS.get(d['Location'])]]
                           + _fields(row)})
            if code in EQUIPMENT_TABLE:
                field, eq_table = EQUIPMENT_TABLE[code]
                eq = self.equipment.get((eq_table, row.get(field)))
                if eq:
                    groups.append({'name': f'Equipment {row.get(field)}', 'fields': _fields(eq), 'collapsed': True})
            if code == 20:
                customers = [c for c in self.tables.get('CYMCUSTOMERLOAD', [])
                             if c['NetworkId'] == d['NetworkId'] and c['DeviceNumber'] == d['DeviceNumber']]
                for c in customers:
                    groups.append({'name': f'Customer {c["CustomerNumber"]} ({PHASES.get(c["Phase"])})',
                                   'fields': [['kW', c['LoadValue1']], ['kvar', c['LoadValue2']],
                                              ['Customer Type', c['ConsumerClassId']],
                                              ['Connected kVA', c['ConnectedKVA']]]})
        return {'title': f'Section {ident}', 'groups': groups}

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
            out['sections'][sec['id']] = {
                'current_a': {p: _r(v, 2) for p, v in cur.items()},
                'loading_pct': _r(100 * max(cur.values()) / rating, 1) if rating and cur else None,
                'p_kw': _r(abs(s.real) / 1000, 2), 'q_kvar': _r(s.imag / 1000 * (1 if s.real >= 0 else -1), 2),
                'loss_kw': _r(loss.real / 1000, 3)}
        out['summary'] = _summary(model, res)
        out['abnormal'] = _abnormal(out)
        return out


# Solo códigos verificados (ver docs/cyme-ingenieria-inversa.md); el resto se muestra crudo.
DECODE = {
    'Phase': lambda v: PHASES.get(v, v), 'ClosedPhase': lambda v: PHASES.get(v, v) or 'None',
    'ControlStatus': lambda v: PHASES.get(v, v),
    'ConnectionConfiguration': lambda v: {0: 'Yg', 1: 'Y', 2: 'D', 3: 'OpenDelta', 4: 'ClosedDelta'}.get(v, v),
    'SettingOption': lambda v: {0: 'LoadCenter', 1: 'RXSettings', 2: 'FixedTap', 3: 'Terminal',
                                4: 'FixedTapPrimary', 5: 'PythonScript'}.get(v, v),
    'LoadValueType': lambda v: {0: 'KW_KVAR'}.get(v, v),
    'DeviceType': lambda v: DEVICE_NAMES.get(v, v),
}


def _fields(row):
    out = []
    for k, v in sorted(row.items()):
        if k in ('LastChange', 'ComponentMask', 'Flags'):
            continue
        if isinstance(v, dict):
            v = next(iter(v.values()))
        out.append([k, DECODE[k](v) if k in DECODE and v is not None else v])
    return out


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
            if r['loading_pct'] is not None and p in r['current_a'] and r['loading_pct'] > 100:
                hits.append((r['loading_pct'], sid))
        worst = max(hits) if hits else None
        rows.append({'condition': 'Overload', 'phase': p, 'count': len(hits), 'worst': worst[1] if worst else '',
                     'value': worst[0] if worst else None})
    buses = sum(1 for v in out['nodes'].values() if any(not 100 * lo <= x['pct'] <= 100 * hi for x in v.values()))
    return {'rows': rows, 'buses_outside_limits': buses, 'limits_pct': [100 * lo, 100 * hi]}
