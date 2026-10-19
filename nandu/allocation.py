"""Distribución de carga (Load Allocation) según los métodos documentados por CYME.

LoadAllocation.chm, «Methods Summary», para cada fase k de un medidor:

    peso(s,k)        = base(s,k) x factor de utilización
    kW_asig(s,k)     = kW_dem(k) x peso(s,k) / sum_s peso(s,k)
    kvar_asig(s,k)   = kW_asig(s,k) x sqrt(1/FP(k)^2 - 1)

base = kVA conectado (KVAMethod), kVA actual (ActualKVAMethod) o kWh (KWHMethod).
Con «Adjust loads using load flow calculations» (RunVoltageDrop, activo por defecto)
kW_dem(k) y kvar_dem(k) se corrigen con el flujo de carga hasta que la potencia
calculada en el medidor coincide con la medida dentro de la tolerancia. Las cargas
bloqueadas (LockDuringLoadAllocation) conservan su valor.
"""
import math

import numpy as np

from . import nodal

METHODS = {'KVAMethod', 'ActualKVAMethod', 'KWHMethod'}


def _weight(load, method):
    util = load.get('utilization_pct', 100.0) / 100
    if method == 'KVAMethod':
        base = (load.get('connected_kva') or 0.0) * load.get('share', 1.0)
    elif method == 'ActualKVAMethod':
        base = abs(complex(load['p_w'], load['q_var'])) / 1000
    else:
        base = (load.get('kwh') or 0.0) * load.get('share', 1.0)
    return max(base, 0.0) * util


def demand_targets(meter, voltage_v):
    """Potencia objetivo por fase (VA) desde un medidor AMP_PF; FP negativo = adelanto."""
    out = {}
    for p, (amps, pf_pct) in meter['phases'].items():
        pf = pf_pct / 100
        if not 0 < abs(pf) <= 1:
            raise ValueError(f'{meter["id"]} fase {p}: factor de potencia no admitido {pf_pct}')
        s = abs(voltage_v[p]) * amps
        q = s * math.sqrt(1 - pf * pf) * (1 if pf > 0 else -1)
        out[p] = complex(s * abs(pf), q)
    return out


def _balance_rows(powers, loads):
    """Una fila ABC de CYME guarda un único valor: todas sus fases toman la asignación de menor módulo.

    Evidencia (CYME 19 oct. 2026, CZU06): con esta regla el error por carga frente al informe
    «Load Flow - Loads» baja de 6,0 kVA RMS (promedio) a 0,9 kVA RMS; el promedio y la media
    armónica no reproducen el factor uniforme de las cargas ABC.
    """
    rows = {}
    for k, load in enumerate(loads):
        if load.get('row') and len(load.get('row', '').rsplit(':', 1)[-1]) > 1:
            rows.setdefault(load['row'], []).append(k)
    for members in rows.values():
        values = powers[members]
        powers[members] = values[np.argmin(np.abs(values))]
    return powers


def allocate(model, meters, method='KVAMethod', tolerance_pct=0.5, max_iterations=30, use_load_flow=True,
             balanced_rows=True):
    """Ajusta las potencias de las cargas a los medidores de cabecera (fuentes).

    Con balanced_rows las filas ABC quedan equilibradas, como las guarda CYME.
    Devuelve las potencias asignadas alineadas con las cargas preparadas del motor nodal.
    """
    if method not in METHODS:
        raise ValueError(f'Método no admitido: {method}')
    first = nodal.solve(model)
    if first['status'] != 'converged':
        raise RuntimeError(f'el flujo inicial no convergió: {first["status"]}')
    prepared = first['loads']
    loads = [item[5] for item in prepared]
    powers = np.array([item[1] for item in prepared], dtype=complex)
    sources = {s['id']: s for s in model['sources']}
    groups, targets = {}, {}
    for meter in meters:
        kind, ident = meter['element']
        if kind != 'source':
            raise ValueError(f'{meter["id"]}: solo se admiten medidores de cabecera en esta versión')
        src = sources[ident]
        targets[ident] = demand_targets(meter, src['voltage_v'])
        for p in meter['phases']:
            members = [k for k, load in enumerate(loads)
                       if load['network_id'] == src['network_id'] and load.get('phase', load['phases'][0]) == p
                       and not load.get('locked')]
            weights = np.array([_weight(loads[k], method) for k in members])
            if not members or weights.sum() <= 0:
                raise ValueError(f'{meter["id"]} fase {p}: sin cargas con base para el método {method}')
            groups[(ident, p)] = (members, weights / weights.sum())
    # Primera asignación: demanda del medidor menos las cargas bloqueadas de esa fase.
    demand = {}
    for (ident, p), (members, share) in groups.items():
        locked = sum((powers[k] for k, l in enumerate(loads) if l.get('locked')
                      and l['network_id'] == sources[ident]['network_id'] and l.get('phase') == p), 0j)
        demand[(ident, p)] = targets[ident][p] - locked
        powers[members] = demand[(ident, p)] * share
    if balanced_rows:
        _balance_rows(powers, loads)
    history, status, sol = [], 'converged' if not use_load_flow else 'not_converged', None
    previous = {}
    initial = None
    for it in range(1, (max_iterations if use_load_flow else 0) + 1):
        sol = nodal.solve(model, powers=powers, initial=initial)
        if sol['status'] != 'converged':
            status = f'load_flow_{sol["status"]}'
            break
        initial = sol['x']
        res = nodal.results(sol)
        worst = 0.0
        for key, (members, share) in groups.items():
            ident, p = key
            got = res['sources'][ident]['power'][p]
            error = targets[ident][p] - got
            worst = max(worst, abs(error) / abs(targets[ident][p]))
            # Secante compleja: d(cabecera)/d(demanda) ~ 1 + pérdidas incrementales.
            slope = 1.0
            if key in previous:
                d_dem, d_got = demand[key] - previous[key][0], got - previous[key][1]
                if abs(d_dem) > 1e-6 * abs(targets[ident][p]):
                    slope = d_got / d_dem
                    if not 0.5 <= abs(slope) <= 3.0:
                        slope = 1.0
            previous[key] = (demand[key], got)
            demand[key] += error / slope
            powers[members] = demand[key] * share
        if balanced_rows:
            _balance_rows(powers, loads)
        history.append({'iteration': it, 'max_mismatch_pct': 100 * worst})
        if 100 * worst <= tolerance_pct:
            status = 'converged'
            break
    zero = sum(1 for members, share in groups.values() for s in share if s == 0)
    return {'status': status, 'method': method, 'iterations': history, 'powers': powers, 'prepared': prepared,
            'targets': targets, 'groups': {f'{k[0]}:{k[1]}': len(v[0]) for k, v in groups.items()},
            'zero_weight_loads': zero, 'solution': sol}
