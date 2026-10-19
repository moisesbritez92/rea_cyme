"""Estimador de estado de distribución (WLS con restricciones, formulación de Hachtel).

Reproduce la formulación documentada en la ayuda de CYME (StateEstimator.chm):

    min 1/2 r' R^-1 r   sujeto a   r - z + h(x) = 0,   c(x) = 0

    [ R    H   0  ] [  mu ]   [ z - h(x) ]
    [ H'   0   C' ] [ dx  ] = [    0     ]
    [ 0    C   0  ] [ -lam]   [  -c(x)   ]

Estado x: tensiones de terminales, corrientes auxiliares del modelo nodal y potencia
(kW, kvar) de cada carga. c(x) son las ecuaciones nodales de nodal.py. Las cargas
son pseudomedidas; los medidores aportan |I| y factor de potencia. La precisión
declarada equivale a 3 desviaciones estándar. Durante la estimación las cargas son
de potencia constante; el modelo de sensibilidad se aplica después (ayuda CYME).
"""
import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from . import nodal

DEFAULTS = {'kw_load_precision': 50.0, 'kvar_load_precision': 50.0, 'amp_meter_precision': 3.0,
            'pf_meter_precision': 3.0, 'max_iterations': 60, 'tolerance_pu': 1e-7,
            'min_load_sigma_kw': 1e-3,
            # InitializationMode de CYME: 'None' usa las cargas del modelo; 'LoadAllocation' las reparte antes.
            'initialization': 'None', 'allocation_method': 'KVAMethod',
            # Índices de calidad de CYME: residuos normalizados (requiere diag de la inversa) y χ² del costo.
            'quality_indices': False, 'chi2_confidence': 0.95,
            'normalized_residual_levels': (3.0, 4.0),
            # Cómo entra un medidor A-FP: 'amp_pf' (|I| y FP), 'pq' (P y Q = V·I·FP, V·I·sen φ)
            # o 'amp_pq' (|I|, P y Q). 'amp_pq' con load_precision_is_sigma reproduce el estimador de
            # CYME en CZU06 (325 cargas, RMS 0,23 kW / 0,03 kvar; docs/cyme-ingenieria-inversa.md).
            'meter_model': 'amp_pq', 'kw_meter_precision': 3.0, 'kvar_meter_precision': 3.0,
            # Una variable por fase (CYME escribe la suma en las filas ABC) y precisión de carga = σ.
            'group_customer_rows': False, 'load_precision_is_sigma': True,
            # Cargas bloqueadas (LockDuringLoadAllocation): CYME no las modifica en el estimador.
            'respect_locks': True, 'locked_sigma_kw': 1e-6}


def _normalized_residuals(K, q, mu, chunk=256):
    """r_N = |r| / sqrt(Ω_ii), con Ω = R E11 R y R = I (medidas normalizadas por σ).

    E11 es el bloque de medidas de K^-1 (sistema de Hachtel); se obtiene por columnas
    con una única factorización LU.
    """
    lu = spla.splu(K)
    diag = np.empty(q)
    for start in range(0, q, chunk):
        stop = min(q, start + chunk)
        rhs = np.zeros((K.shape[0], stop - start))
        rhs[np.arange(start, stop), np.arange(stop - start)] = 1.0
        diag[start:stop] = lu.solve(rhs)[np.arange(start, stop), np.arange(stop - start)]
    omega = np.clip(diag, 1e-15, None)
    return np.abs(mu) / np.sqrt(omega), omega


def meters_from_model(model, net):
    """Medidores de red (CYMNETWORKMETER) en la barra de la fuente."""
    meters = []
    for meter in model['meters']:
        sources = [s for s in model['sources'] if s['bus'] == meter['node']]
        if len(sources) != 1:
            raise ValueError(f'medidor de {meter["network_id"]} sin fuente única en {meter["node"]}')
        if meter['demand_type_raw'] != 3 or meter['is_total']:
            raise ValueError(f'medidor de {meter["network_id"]}: DemandType {meter["demand_type_raw"]} no admitido')
        meters.append({'id': f'meter:{meter["network_id"]}', 'element': ('source', sources[0]['id']),
                       'bus': meter['node'], 'type': 'AMP_PF',
                       'phases': {p: v for p, v in meter['values'].items() if v[0]}})
    return meters


def _locate(net, sys_, meter):
    """Índices de la corriente auxiliar y del terminal de tensión, con signo fuente->carga."""
    kind, ident = meter['element']
    out = {}
    for k, a in enumerate(sys_['aux']):
        if (a['kind'], a['id']) == (kind, ident) and a['phase'] in meter['phases']:
            out[a['phase']] = [sys_['n'] + k, None, 1.0]
    if kind == 'source':
        for p in out:
            out[p][1] = net.pos[(meter['bus'], p)]
    else:
        sw = next(s for s in net.model['switches'] if s['id'] == ident)
        for p in out:
            df = net.distance.get((sw['from'], p), math.inf)
            dt = net.distance.get((sw['to'], p), math.inf)
            near = sw['from'] if df <= dt else sw['to']
            # La corriente auxiliar va de 'from' a 'to'; la fuente de la medida es 'near'.
            out[p][1] = net.pos[(near, p)]
            out[p][2] = 1.0 if near == sw['from'] else -1.0
    missing = set(meter['phases']) - set(out)
    if missing:
        raise ValueError(f'{meter["id"]}: fases sin elemento energizado {sorted(missing)}')
    return out


def _meter_terms(z, where, size, kind='all'):
    """|I|, FP, P y Q (kW, kvar) con gradientes respecto de [Re z, Im z] (dict disperso)."""
    ia, vi, sign = where
    I, V = sign * z[ia], z[vi]
    ir, ii, vr, vim = I.real, I.imag, V.real, V.imag
    mi, mv = abs(I), abs(V)
    p = vr * ir + vim * ii
    q = vim * ir - vr * ii
    pf = p / (mv * mi)
    g_i = {ia: sign * ir / mi, size + ia: sign * ii / mi}
    dp = {vi: ir, size + vi: ii}
    dp[ia] = dp.get(ia, 0) + sign * vr
    dp[size + ia] = dp.get(size + ia, 0) + sign * vim
    dq = {vi: -ii, size + vi: ir}
    dq[ia] = dq.get(ia, 0) + sign * vim
    dq[size + ia] = dq.get(size + ia, 0) - sign * vr
    dden = {vi: mi * vr / mv, size + vi: mi * vim / mv, ia: sign * mv * ir / mi, size + ia: sign * mv * ii / mi}
    g_pf = {k: dp.get(k, 0) / (mv * mi) - p / (mv * mi) ** 2 * dden.get(k, 0) for k in set(dp) | set(dden)}
    return {'amp': (mi, g_i), 'pf': (pf, g_pf), 'kw': (p / 1000, {k: v / 1000 for k, v in dp.items()}),
            'kvar': (q / 1000, {k: v / 1000 for k, v in dq.items()})}


def estimate(model, meters=None, options=None):
    opt = {**DEFAULTS, **(options or {})}
    flow = nodal.solve(model)
    if flow['status'] != 'converged':
        raise RuntimeError(f'el flujo inicial no convergió: {flow["status"]}')
    net, sys_ = flow['net'], flow['system']
    A, b, n = sys_['A'], sys_['b'], sys_['n']
    size = A.shape[0]
    meters = meters if meters is not None else meters_from_model(model, net)
    allocation_report = None
    if opt['initialization'] == 'LoadAllocation':
        from .allocation import allocate
        alloc = allocate(model, meters, opt['allocation_method'])
        if alloc['status'] != 'converged':
            raise RuntimeError(f'la distribución de carga inicial no convergió: {alloc["status"]}')
        flow = alloc['solution']
        allocation_report = {k: alloc[k] for k in ('status', 'method', 'iterations', 'zero_weight_loads')}
    elif opt['initialization'] != 'None':
        raise ValueError(f'InitializationMode no admitido: {opt["initialization"]}')
    # Cargas como potencia constante durante la estimación; S0 guardado para el posproceso.
    start = flow['powers'] if flow['powers'] is not None else [s0 for _, s0, *_ in flow['loads']]
    prepared = [(idx, s0, (0.0, 0.0, 1.0), 0.0, base, load) for (idx, _, _, _, base, load), s0
                in zip(flow['loads'], start)]
    s_elem0 = np.array([s0 for _, s0, *_ in prepared]) / 1000
    # Variables de estado por fila de cliente: una fila ABC de CYME tiene un solo valor kW/kvar,
    # repartido en partes fijas entre sus fases (evidencia: historial de modificaciones del estimador).
    keys = [(item[5].get('row') if opt['group_customer_rows'] else None) or item[5]['id'] for item in prepared]
    order = list(dict.fromkeys(keys))
    gi = np.array([order.index(k) for k in keys]) if len(order) < 2000 else np.array(
        [{k: i for i, k in enumerate(order)}[k] for k in keys])
    G = len(order)
    weights = np.array([item[5].get('share', 1.0) for item in prepared])
    shares = weights / np.bincount(gi, weights=weights, minlength=G)[gi]
    s_start = np.bincount(gi, weights=s_elem0.real, minlength=G) + 1j * np.bincount(gi, weights=s_elem0.imag, minlength=G)
    L = G
    x_c = flow['x'].copy()
    s_g = s_start.copy()
    s_k = shares * s_g[gi]
    nstate = 2 * size + 2 * L

    # Medidas: pseudomedidas de carga (una por fila de cliente) y medidores.
    load_div = 1.0 if opt['load_precision_is_sigma'] else 3.0
    sig_p = np.maximum(opt['kw_load_precision'] / 100 * np.abs(s_start.real) / load_div, opt['min_load_sigma_kw'])
    sig_q = np.maximum(opt['kvar_load_precision'] / 100 * np.abs(s_start.imag) / load_div, opt['min_load_sigma_kw'])
    if opt['respect_locks']:
        locked = np.bincount(gi, weights=[1.0 if item[5].get('locked') else 0.0 for item in prepared], minlength=G) > 0
        sig_p[locked] = sig_q[locked] = opt['locked_sigma_kw']
    located = [(m, _locate(net, sys_, m)) for m in meters]
    meas = []
    model_ = opt['meter_model']
    if model_ not in ('amp_pf', 'pq', 'amp_pq'):
        raise ValueError(f'meter_model no admitido: {model_}')
    for m, where in located:
        for p, (amps, pf_pct) in m['phases'].items():
            if model_ in ('amp_pf', 'amp_pq'):
                meas.append(('amp', m['id'], p, where[p], amps, opt['amp_meter_precision'] / 100 * amps / 3))
            if model_ == 'amp_pf':
                meas.append(('pf', m['id'], p, where[p], pf_pct / 100,
                             opt['pf_meter_precision'] / 100 * pf_pct / 100 / 3))
            else:
                # P y Q de la medida con la tensión del punto de medición (fuente: fija).
                pf = pf_pct / 100
                s_kva = abs(x_c[where[p][1]]) * amps / 1000
                kw, kvar = s_kva * abs(pf), s_kva * math.sqrt(max(0.0, 1 - pf * pf)) * (1 if pf > 0 else -1)
                meas.append(('kw', m['id'], p, where[p], kw, opt['kw_meter_precision'] / 100 * abs(kw) / 3))
                meas.append(('kvar', m['id'], p, where[p], kvar, opt['kvar_meter_precision'] / 100 * abs(kvar) / 3))
    q = 2 * L + len(meas)
    Ar, Ai = A.real.tocsr(), A.imag.tocsr()
    J_lin = sp.bmat([[Ar, -Ai], [Ai, Ar]], format='csr')
    history, status = [], 'not_converged'
    for it in range(1, opt['max_iterations'] + 1):
        currents = nodal.load_currents(x_c, prepared, s_k * 1000)
        F = nodal.add_load_currents(A @ x_c - b, prepared, currents)
        rows, cols, vals = nodal.load_jacobian(prepared, currents, size)
        # dI/dP_g = 1000 share g, dI/dQ_g = -1000 j share g (S en kVA, por fila de cliente).
        for k, ((idx, *_), (_, _, _, g)) in enumerate(zip(prepared, currents)):
            col = gi[k]
            for r, sr in zip(idx, [1, -1][:len(idx)]):
                dp, dq = 1000 * sr * shares[k] * g, -1000j * sr * shares[k] * g
                rows += [r, size + r, r, size + r]
                cols += [2 * size + col, 2 * size + col, 2 * size + L + col, 2 * size + L + col]
                vals += [dp.real, dp.imag, dq.real, dq.imag]
        C = sp.hstack([J_lin, sp.csr_matrix((2 * size, 2 * L))]).tocsr() + \
            sp.csr_matrix((vals, (rows, cols)), shape=(2 * size, nstate))
        c = np.concatenate([F.real, F.imag])
        # h(x) y H normalizados por sigma (R = I).
        h = np.concatenate([s_g.real / sig_p, s_g.imag / sig_q])
        zz = np.concatenate([s_start.real / sig_p, s_start.imag / sig_q])
        hr = list(range(2 * L))
        hc = [2 * size + k for k in range(2 * L)]
        hv = list(np.concatenate([1 / sig_p, 1 / sig_q]))
        h_extra, z_extra = [], []
        for j, (kind, _, _, where, value, sigma) in enumerate(meas):
            val, grad = _meter_terms(x_c, where, size)[kind]
            h_extra.append(val / sigma)
            z_extra.append(value / sigma)
            for col, d in grad.items():
                hr.append(2 * L + j)
                hc.append(col)
                hv.append(d / sigma)
        h = np.concatenate([h, h_extra])
        zz = np.concatenate([zz, z_extra])
        H = sp.csr_matrix((hv, (hr, hc)), shape=(q, nstate))
        r_ = C.shape[0]
        K = sp.bmat([[sp.identity(q), H, None], [H.T, None, C.T], [None, C, None]], format='csc')
        rhs = np.concatenate([zz - h, np.zeros(nstate), -c])
        try:
            sol = spla.spsolve(K, rhs)
        except RuntimeError as exc:
            status = f'singular: {exc}'
            break
        if not np.all(np.isfinite(sol)):
            status = 'singular_or_nonfinite'
            break
        dx = sol[q:q + nstate]
        x_c += dx[:size] + 1j * dx[size:2 * size]
        s_g = s_g + dx[2 * size:2 * size + L] + 1j * dx[2 * size + L:]
        s_k = shares * s_g[gi]
        dv = float(np.max(np.abs(dx[:n] + 1j * dx[size:size + n]) / flow['bases']))
        cost = float(np.sum((zz - h) ** 2))
        history.append({'iteration': it, 'max_dv_pu': dv, 'objective_before_step': cost})
        K_last, mu_last = K, sol[:q]
        if dv < opt['tolerance_pu']:
            status = 'converged'
            break
    # Posproceso: medidas estimadas y potencia nominal por modelo ZIP.
    flow_out = {**flow, 'x': x_c, 'loads': prepared, 'powers': s_k * 1000}
    res = nodal.results(flow_out)
    residuals = []
    names = {'amp': 'current_a', 'pf': 'power_factor', 'kw': 'active_power_kw', 'kvar': 'reactive_power_kvar'}
    for kind, ident, p, where, value, sigma in meas:
        est = _meter_terms(x_c, where, size)[kind][0]
        residuals.append({'meter': ident, 'phase': p, 'quantity': names[kind],
                          'measured': value, 'estimated': est, 'sigma': sigma, 'weighted_residual': (value - est) / sigma})
    load_rows = []
    nominal = []
    for k, (idx, _, _, _, base, load) in enumerate(prepared):
        v = x_c[idx[0]] - (x_c[idx[1]] if len(idx) == 2 else 0)
        kz, ki, kp = load['zip']
        m_ = abs(v) / base
        factor = kz * m_ ** 2 + ki * m_ + kp
        s_nom = s_k[k] / factor if factor else s_k[k]
        nominal.append(s_nom)
        load_rows.append({'id': load['id'], 'device_id': load['device_id'], 'network_id': load['network_id'],
                          'phases': load['phases'], 'row': order[gi[k]], 'initial_kva': s_elem0[k],
                          'estimated_kva': s_k[k], 'estimated_nominal_kva': s_nom})
    cost = float(sum(((s_g.real - s_start.real) / sig_p) ** 2) + sum(((s_g.imag - s_start.imag) / sig_q) ** 2)
                 + sum(r['weighted_residual'] ** 2 for r in residuals))
    quality = None
    if opt['quality_indices'] and status == 'converged':
        from scipy.stats import chi2
        dof = q - nstate + 2 * size
        rn, omega = _normalized_residuals(K_last, q, mu_last)
        lvl1, lvl2 = opt['normalized_residual_levels']
        for j, r in enumerate(residuals):
            r['normalized_residual'] = float(rn[2 * L + j])
        names = [(key, 'kW') for key in order] + [(key, 'kvar') for key in order]
        worst = sorted(range(2 * L), key=lambda i: -rn[i])[:20]
        quality = {
            'degrees_of_freedom': dof,
            'chi2_threshold': float(chi2.ppf(opt['chi2_confidence'], dof)) if dof > 0 else None,
            'chi2_confidence': opt['chi2_confidence'],
            'solution_cost_passes': bool(dof > 0 and cost <= chi2.ppf(opt['chi2_confidence'], dof)),
            'normalized_residual_levels': [lvl1, lvl2],
            'suspicious_measurements': int(np.sum((rn > lvl1) & (rn <= lvl2))),
            'erroneous_measurements': int(np.sum(rn > lvl2)),
            'largest_load_residuals': [{'load': names[i][0], 'quantity': names[i][1], 'normalized_residual': float(rn[i])}
                                       for i in worst],
            'note': 'r_N = |r| / sqrt(diag(R E11 R)); E11 del sistema de Hachtel en la última iteración'}
    return {'status': status, 'iterations': history, 'solution_cost': cost, 'quality_indices': quality,
            'measurements': len(meas) + 2 * L,
            'state_variables': nstate, 'constraints': 2 * size, 'meter_residuals': residuals,
            'loads': load_rows, 'results': res, 'flow': flow_out, 'options': opt,
            'initial_allocation': allocation_report,
            'load_model_note': 'potencia constante en la estimación; nominal por ZIP en el posproceso'}
