"""Informes JSON de flujo de carga, estimación de estado y comparación con CYME."""
import csv
import math
from pathlib import Path

from . import __version__, cyme_model, estimator, nodal
from .storage import inspect, load_tables

ABC = 'ABC'


def _c(value):
    return [round(value.real, 6), round(value.imag, 6)]


def _kw(value):
    return {'kw': round(value.real / 1000, 6), 'kvar': round(value.imag / 1000, 6)}


def build_model(path, networks=None, include_line_charging=True):
    tables = load_tables(path, cyme_model.TABLES)
    unknown = set(networks or []) - {r['NetworkId'] for r in tables.get('CYMNETWORK', [])}
    if unknown:
        raise ValueError(f'Redes ausentes en la importación: {sorted(unknown)}')
    return cyme_model.build(tables, networks, include_line_charging)


def _summary(model, res):
    out = {}
    for net in model['networks']:
        volts = [(d['pu'], bus, p) for bus, ph in res['voltages'].items()
                 if model['buses'][bus]['network_id'] == net and not model['buses'][bus]['internal']
                 for p, d in ph.items() if not d.get('deenergized')]
        src = [s for s in res['sources'].values() if s['network_id'] == net]
        out[net] = {
            'source_current_a': {p: round(abs(c), 4) for s in src for p, c in s['current'].items()},
            'source_power': _kw(sum((v for s in src for v in s['power'].values()), 0j)),
            'load_power': _kw(res['load_totals'].get(net, 0j)),
            'line_losses': _kw(res['line_losses'].get(net, 0j)),
            'min_voltage': dict(zip(('pu', 'node', 'phase'), min(volts))) if volts else None,
            'max_voltage': dict(zip(('pu', 'node', 'phase'), max(volts))) if volts else None,
            'deenergized_terminals': sum(1 for bus, ph in res['voltages'].items()
                                         if model['buses'][bus]['network_id'] == net
                                         for d in ph.values() if d.get('deenergized'))}
    return out


def _details(model, sol, res):
    nodes = [{'node': bus, 'network_id': model['buses'][bus]['network_id'], 'phase': p,
              'internal': model['buses'][bus]['internal'],
              'v_ln_kv': None if d.get('deenergized') else round(abs(d['v']) / 1000, 6),
              'pu': round(d['pu'], 6), 'angle_deg': round(d['angle_deg'], 4)}
             for bus, ph in sorted(res['voltages'].items()) for p, d in ph.items()]
    branches = [{'id': ident, 'phases': b['phases'], 'current_from_a': [round(abs(i), 4) for i in b['i_from']],
                 'loss': _kw(b['loss'].sum())} for ident, b in res['branches'].items()]
    regs = []
    for reg in model['regulators']:
        info = sol['system']['regulators'].get(reg['id'])
        if not info:
            continue
        x = sol['x']
        regs.append({'id': reg['id'], 'input_node': info['input_bus'], 'output_node': info['output_bus'],
                     'taps': sol.get('tap_control', {}).get('final_taps', {}).get(reg['id'], reg['taps']),
                     'connection': reg['connection'],
                     'v_in_kv': [round(abs(x[i]) / 1000, 6) for i in info['inputs']],
                     'v_out_kv': [round(abs(x[i]) / 1000, 6) for i in info['outputs']],
                     'v_out_ll_kv': [round(abs(x[info['outputs'][k]] - x[info['outputs'][(k + 1) % 3]]) / 1000, 6)
                                     for k in range(len(info['outputs']))] if len(info['outputs']) == 3 else None,
                     'current_out_a': [round(abs(x[info['aux'] + k]), 4) for k in range(len(info['outputs']))]})
    switches = [{'id': ident, 'current_a': {p: round(abs(c), 4) for p, c in cur.items()}}
                for (kind, ident), cur in res['aux'].items() if kind == 'switch' and '#' not in ident]
    return {'nodes': nodes, 'branches': branches, 'regulators': regs, 'switching_devices': switches}


def _header(path, model, kind):
    provenance = inspect(path)['provenance']
    return {'report_format': f'nandu-{kind}-v1', 'nandu_version': __version__, 'source_file': str(Path(path)),
            'source_sha256': provenance.get('source_sha256'), 'networks': model['networks'],
            'conventions': model['conventions'], 'model_issues': model['issues'],
            'model_counts': {k: len(model[k]) for k in ('buses', 'lines', 'switches', 'regulators', 'loads',
                                                         'capacitors', 'sources')}}


def load_flow(path, networks=None, details=False, include_line_charging=True):
    model = build_model(path, networks, include_line_charging)
    sol = nodal.solve_controlled(model)
    report = {**_header(path, model, 'load-flow'), 'engine': 'nandu.nodal: nodal aumentado + Newton-Raphson',
              'status': sol['status'], 'iterations': sol['iterations'],
              'regulator_tap_control': sol['tap_control'],
              'skipped_elements': sol['system']['skipped']}
    if sol['status'] == 'converged':
        res = nodal.results(sol)
        report['summary'] = _summary(model, res)
        if details:
            report.update(_details(model, sol, res))
    return report


def estimate(path, networks=None, details=False, options=None):
    model = build_model(path, networks)
    est = estimator.estimate(model, options=options)
    report = {**_header(path, model, 'state-estimation'),
              'engine': 'nandu.estimator: WLS con restricciones (Hachtel)', 'status': est['status'],
              'iterations': est['iterations'], 'options': est['options'], 'solution_cost': est['solution_cost'],
              'measurement_count': est['measurements'], 'state_variable_count': est['state_variables'],
              'constraint_count': est['constraints'], 'meter_residuals': est['meter_residuals'],
              'initial_allocation': est['initial_allocation'], 'quality_indices': est['quality_indices'],
              'load_model_note': est['load_model_note'],
              'meter_convention': 'CYMNETWORKMETER DemandType=3 leído como AMP_PF (A, FP %): hipótesis'}
    if est['status'] == 'converged':
        report['summary'] = _summary(model, est['results'])
        totals = {}
        for row in est['loads']:
            t = totals.setdefault(row['network_id'], [0j, 0j])
            t[0] += row['initial_kva']
            t[1] += row['estimated_nominal_kva']
        report['load_totals'] = {net: {'initial': _kw(a * 1000), 'estimated_nominal': _kw(b * 1000)}
                                 for net, (a, b) in totals.items()}
        if details:
            report['loads'] = [{'id': r['id'], 'device_id': r['device_id'], 'network_id': r['network_id'],
                                'phases': r['phases'], 'initial': _kw(r['initial_kva'] * 1000),
                                'estimated_nominal': _kw(r['estimated_nominal_kva'] * 1000)} for r in est['loads']]
            report.update(_details(model, est['flow'], est['results']))
    return report


def allocate(path, networks=None, details=False, method='KVAMethod'):
    from .allocation import allocate as run
    model = build_model(path, networks)
    alloc = run(model, estimator.meters_from_model(model, None), method)
    report = {**_header(path, model, 'load-allocation'), 'engine': 'nandu.allocation: método CYME + flujo de carga',
              'status': alloc['status'], 'method': method, 'iterations': alloc['iterations'],
              'zero_weight_loads': alloc['zero_weight_loads'], 'loads_per_meter_phase': alloc['groups'],
              'targets': {src: {p: _kw(s) for p, s in t.items()} for src, t in alloc['targets'].items()},
              'meter_convention': 'CYMNETWORKMETER DemandType=3 leído como AMP_PF (A, FP %): hipótesis'}
    if alloc['status'] == 'converged':
        sol = alloc['solution']
        res = nodal.results(sol)
        report['summary'] = _summary(model, res)
        if details:
            report['loads'] = [{'id': item[5]['id'], 'device_id': item[5]['device_id'],
                                'network_id': item[5]['network_id'], 'phase': item[5].get('phase'),
                                'initial': _kw(item[1]), 'allocated': _kw(p)}
                               for item, p in zip(alloc['prepared'], alloc['powers'])]
            report.update(_details(model, sol, res))
    return report


def compare_voltages(report, csv_path, top=15):
    """Compara VLNA/B/C (kV) de un CSV exportado por el MCP de CYME con un informe detallado."""
    ours = {(n['node'], n['phase']): n['v_ln_kv'] for n in report.get('nodes', [])}
    if not ours:
        raise ValueError('El informe no tiene tensiones por nodo; generarlo con --details')
    diffs, missing = [], 0
    with open(csv_path, encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            for p in ABC:
                raw = row.get(f'VLN{p}')
                if raw in (None, ''):
                    continue
                cyme = float(raw)
                mine = ours.get((row['node_id'], p))
                if mine is None:
                    missing += 1
                    continue
                if cyme == 0 and mine == 0:
                    continue
                diffs.append({'node': row['node_id'], 'phase': p, 'cyme_kv': cyme, 'nandu_kv': mine,
                              'diff_v': round((mine - cyme) * 1000, 3)})
    if not diffs:
        raise ValueError('No hay nodos comunes con tensión en el CSV')
    errors = sorted(abs(d['diff_v']) for d in diffs)
    pick = lambda q: errors[min(len(errors) - 1, int(q * len(errors)))]
    return {'compared_terminals': len(diffs), 'missing_in_nandu': missing,
            'abs_diff_v': {'max': errors[-1], 'p95': pick(0.95), 'median': pick(0.5),
                           'rms': round(math.sqrt(sum(e * e for e in errors) / len(errors)), 3)},
            'worst': sorted(diffs, key=lambda d: -abs(d['diff_v']))[:top]}
