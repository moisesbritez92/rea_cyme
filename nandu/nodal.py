"""Flujo de carga desbalanceado por análisis nodal aumentado (estilo MANA) y Newton-Raphson.

Incógnitas: tensión compleja de cada terminal (barra, fase) energizado y corrientes
auxiliares de fuentes ideales, maniobras cerradas y reguladores. Las ecuaciones
lineales forman una matriz compleja dispersa; las cargas ZIP aportan la parte no
lineal. El sistema real equivalente se resuelve con LU disperso en cada iteración.
"""
from collections import defaultdict, deque
import math

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

ABC = 'ABC'
NEXT = {'A': 'B', 'B': 'C', 'C': 'A'}
SQRT3 = math.sqrt(3)


def regulator_matrix(reg):
    """V_salida = M V_entrada en las fases del regulador (tipo A, ver CONVENTIONS)."""
    phases = reg['phases']
    n = {p: reg['step_pu'] * reg['taps'][p] for p in phases}
    M = np.eye(len(phases))
    if reg['connection'] == 'closed_delta':
        if phases != list(ABC):
            raise ValueError(f'{reg["id"]}: delta cerrado requiere ABC')
        for i, p in enumerate(phases):
            M[i, i] += n[p]
            M[i, phases.index(NEXT[p])] -= n[p]
    else:
        for i, p in enumerate(phases):
            M[i, i] += n[p]
    return M


class Network:
    """Indexación de terminales, energización y ensamblado de la parte lineal."""

    def __init__(self, model):
        self.model = model
        self.terminals, self.index = [], {}
        for name, entry in model['buses'].items():
            for p in entry['phases']:
                self.index[(name, p)] = len(self.terminals)
                self.terminals.append((name, p))
        self._energize()

    def _edges(self):
        m = self.model
        for line in m['lines']:
            for p in line['phases']:
                yield (line['from'], p), (line['to'], p)
        for sw in m['switches']:
            for p in sw['closed']:
                yield (sw['from'], p), (sw['to'], p)
        for reg in m['regulators']:
            for p in reg['phases']:
                yield (reg['from'], p), (reg['to'], p)

    def _energize(self):
        adjacency = defaultdict(list)
        for a, b in self._edges():
            adjacency[a].append(b)
            adjacency[b].append(a)
        self.distance = {}
        queue = deque()
        for src in self.model['sources']:
            for p in ABC:
                key = (src['bus'], p)
                if key in self.index and abs(src['voltage_v'][p]) > 0:
                    self.distance[key] = 0
                    queue.append(key)
        while queue:
            cur = queue.popleft()
            for nxt in adjacency[cur]:
                if nxt not in self.distance:
                    self.distance[nxt] = self.distance[cur] + 1
                    queue.append(nxt)
        self.live = [t for t in self.terminals if t in self.distance]
        self.pos = {t: i for i, t in enumerate(self.live)}

    def orient(self, reg):
        """Entrada = extremo más cercano a la fuente; el sentido del tramo no se presupone."""
        d_from = min(self.distance.get((reg['from'], p), math.inf) for p in reg['phases'])
        d_to = min(self.distance.get((reg['to'], p), math.inf) for p in reg['phases'])
        if math.isinf(min(d_from, d_to)):
            return None
        return (reg['from'], reg['to']) if d_from <= d_to else (reg['to'], reg['from'])


def _base_ln(model, bus):
    kvll = model['buses'][bus].get('nominal_kvll')
    return kvll * 1000 / SQRT3 if kvll else None


def assemble(net):
    m = net.model
    n = len(net.live)
    rows, cols, vals = [], [], []
    rhs = {}
    aux = []

    def add(r, c, v):
        rows.append(r)
        cols.append(c)
        vals.append(v)

    def term(bus, p):
        return net.pos.get((bus, p))

    skipped = defaultdict(list)
    branch_y = {}
    for line in m['lines']:
        idx_f = [term(line['from'], p) for p in line['phases']]
        idx_t = [term(line['to'], p) for p in line['phases']]
        live = [i for i, (a, b) in enumerate(zip(idx_f, idx_t)) if a is not None and b is not None]
        if not live:
            skipped['lines'].append(line['id'])
            continue
        z = np.array(line['z'], dtype=complex)[np.ix_(live, live)]
        y = np.array(line['y'], dtype=complex)[np.ix_(live, live)] / 2
        yser = np.linalg.inv(z)
        f = [idx_f[i] for i in live]
        t = [idx_t[i] for i in live]
        branch_y[line['id']] = (f, t, yser, y, [line['phases'][i] for i in live])
        for i in range(len(f)):
            for j in range(len(f)):
                add(f[i], f[j], yser[i, j] + y[i, j])
                add(t[i], t[j], yser[i, j] + y[i, j])
                add(f[i], t[j], -yser[i, j])
                add(t[i], f[j], -yser[i, j])
    for cap in m['capacitors']:
        idx = [term(cap['bus'], p) for p in cap['phases']]
        if None in idx:
            skipped['capacitors'].append(cap['id'])
            continue
        y = 1j * cap['rated_q_var'] / cap['rated_v'] ** 2
        if len(idx) == 1:
            add(idx[0], idx[0], y)
        else:
            a, b = idx
            add(a, a, y), add(b, b, y), add(a, b, -y), add(b, a, -y)

    def constraint(kind, ident, phase, coeffs, value=0j, injections=None):
        r = n + len(aux)
        aux.append({'kind': kind, 'id': ident, 'phase': phase})
        for c, v in coeffs:
            add(r, c, v)
        for c, v in (injections or []):
            add(c, r, v)
        if value:
            rhs[r] = value

    for src in m['sources']:
        for p in ABC:
            t = term(src['bus'], p)
            if t is not None and abs(src['voltage_v'][p]) > 0:
                constraint('source', src['id'], p, [(t, 1)], src['voltage_v'][p], [(t, -1)])
    for sw in m['switches']:
        for p in sw['closed']:
            a, b = term(sw['from'], p), term(sw['to'], p)
            if a is None or b is None:
                continue
            # Corriente auxiliar del extremo 'from' al 'to'.
            constraint('switch', sw['id'], p, [(a, 1), (b, -1)], 0j, [(a, 1), (b, -1)])
    reg_info = {}
    for reg in m['regulators']:
        sides = net.orient(reg)
        if sides is None:
            skipped['regulators'].append(reg['id'])
            continue
        src_bus, out_bus = sides
        M = regulator_matrix(reg)
        ins = [term(src_bus, p) for p in reg['phases']]
        outs = [term(out_bus, p) for p in reg['phases']]
        if None in ins or None in outs:
            skipped['regulators'].append(reg['id'])
            continue
        first = n + len(aux)
        for i, p in enumerate(reg['phases']):
            coeffs = [(outs[i], 1)] + [(ins[j], -M[i, j]) for j in range(len(ins)) if M[i, j]]
            injections = [(outs[i], -1)] + [(ins[j], M[i, j]) for j in range(len(ins)) if M[i, j]]
            constraint('regulator', reg['id'], p, coeffs, 0j, injections)
        reg_info[reg['id']] = {'input_bus': src_bus, 'output_bus': out_bus, 'M': M, 'aux': first,
                               'inputs': ins, 'outputs': outs}
    size = n + len(aux)
    A = sp.csr_matrix((np.array(vals, dtype=complex), (rows, cols)), shape=(size, size))
    b = np.zeros(size, dtype=complex)
    for r, v in rhs.items():
        b[r] = v
    return {'A': A, 'b': b, 'aux': aux, 'n': n, 'branch_y': branch_y, 'regulators': reg_info,
            'skipped': {k: v for k, v in skipped.items()}}


def prepare_loads(net, loads=None):
    """Vectoriza cargas: terminal(es), potencia nominal, ZIP y base de tensión."""
    m = net.model
    out = []
    for load in (loads if loads is not None else m['loads']):
        idx = [net.pos.get((load['bus'], p)) for p in load['phases']]
        if None in idx:
            continue
        base = _base_ln(m, load['bus'])
        if base is None:
            continue
        if load['connection'] == 'delta':
            base *= SQRT3
        out.append((idx, complex(load['p_w'], load['q_var']), load['zip'], load['vz_pu'], base, load))
    return out


def load_currents(V, prepared, powers=None):
    """Corriente absorbida por cada carga, derivadas de Wirtinger (a, b) y g = i / conj(S0).

    powers reemplaza la potencia nominal S0 (VA) de cada carga, p. ej. en el estimador.
    """
    results = []
    for k, (idx, s0, zip_, vz, base, _) in enumerate(prepared):
        s = powers[k] if powers is not None else s0
        v = V[idx[0]] - (V[idx[1]] if len(idx) == 2 else 0)
        mag = abs(v)
        if mag < 1e-9:
            results.append((0j, 0j, 0j, 0j))
            continue
        kz, ki, kp = zip_
        if vz and mag < vz * base:
            kz, kp = kz + kp / vz ** 2, 0.0
        g = kz * v / base ** 2 + ki / base * v / mag + kp / v.conjugate()
        ga = kz / base ** 2 + ki / base / (2 * mag)
        gb = -ki / base * v * v / (2 * mag ** 3) - kp / v.conjugate() ** 2
        cs = s.conjugate()
        results.append((cs * g, cs * ga, cs * gb, g))
    return results


def load_jacobian(prepared, currents, size):
    """Bloques reales de dI_carga/dV para el sistema [Re; Im] de tamaño 2*size."""
    rows, cols, vals = [], [], []
    for (idx, *_), (_, a, bb, _) in zip(prepared, currents):
        signs = [1] if len(idx) == 1 else [1, -1]
        for r, sr in zip(idx, signs):
            for c, sc in zip(idx, signs):
                g = sr * sc
                rows += [r, r, size + r, size + r]
                cols += [c, size + c, c, size + c]
                vals += [g * (a + bb).real, -g * (a - bb).imag, g * (a + bb).imag, g * (a - bb).real]
    return rows, cols, vals


def add_load_currents(F, prepared, currents):
    for (idx, *_), (i, *_) in zip(prepared, currents):
        F[idx[0]] += i
        if len(idx) == 2:
            F[idx[1]] -= i
    return F


def solve(model, tolerance=1e-9, max_iterations=40, prepared_loads=None, powers=None, initial=None):
    net = Network(model)
    sys_ = assemble(net)
    A, b, n = sys_['A'], sys_['b'], sys_['n']
    size = A.shape[0]
    loads = prepared_loads if prepared_loads is not None else prepare_loads(net)
    bases = np.array([_base_ln(model, bus) or 1.0 for bus, _ in net.live])
    angle = {'A': 0, 'B': -2 * math.pi / 3, 'C': 2 * math.pi / 3}
    x = np.zeros(size, dtype=complex)
    if initial is not None:
        x[:len(initial)] = initial
    else:
        x[:n] = [bases[i] * np.exp(1j * angle[p]) for i, (_, p) in enumerate(net.live)]
        # Arranque: salida de reguladores con su relación de taps.
        for info in sys_['regulators'].values():
            x[info['outputs']] = info['M'] @ x[info['inputs']]
    Ar, Ai = A.real.tocsr(), A.imag.tocsr()
    J_lin = sp.bmat([[Ar, -Ai], [Ai, Ar]], format='csr')
    history, status = [], 'not_converged'
    for it in range(1, max_iterations + 1):
        currents = load_currents(x, loads, powers)
        F = add_load_currents(A @ x - b, loads, currents)
        rows, cols, vals = load_jacobian(loads, currents, size)
        J = J_lin + sp.csr_matrix((vals, (rows, cols)), shape=(2 * size, 2 * size))
        R = np.concatenate([F.real, F.imag])
        try:
            dx = spla.spsolve(J.tocsc(), -R)
        except RuntimeError as exc:
            status = f'singular: {exc}'
            break
        if not np.all(np.isfinite(dx)):
            status = 'singular_or_nonfinite'
            break
        step = dx[:size] + 1j * dx[size:]
        x += step
        dv = float(np.max(np.abs(step[:n]) / bases)) if n else 0.0
        mismatch = float(np.max(np.abs(F[:n]))) if n else 0.0
        history.append({'iteration': it, 'max_dv_pu': dv, 'max_current_mismatch_a': mismatch})
        if dv < tolerance:
            status = 'converged'
            break
    return {'status': status, 'iterations': history, 'x': x, 'net': net, 'system': sys_, 'loads': loads,
            'powers': powers, 'bases': bases}


CONTROL_BASE_V = 120.0  # BaseVoltageCustomerSide del estudio CYME


def control_voltages(sol, reg):
    """Tensión de control por fase (V sobre base 120 V), con compensación R+jX si la hay."""
    info = sol['system']['regulators'].get(reg['id'])
    ctrl = reg.get('control')
    if not info or not ctrl:
        return {}
    x, phases = sol['x'], reg['phases']
    model = sol['net'].model
    base = _base_ln(model, info['output_bus'])
    out = {}
    for i, p in enumerate(phases):
        v = x[info['outputs'][i]]
        scale = base
        if reg['connection'] == 'closed_delta':
            v = v - x[info['outputs'][phases.index(NEXT[p])]]
            scale = base * SQRT3
        current = x[info['aux'] + i]
        comp = complex(ctrl['r_v'][p], ctrl['x_v'][p]) * current / ctrl['ct_primary_a']
        out[p] = abs(CONTROL_BASE_V * v / scale - comp)
    return out


def solve_controlled(model, max_outer=25, **kwargs):
    """Flujo con «Normal Tap Operation»: taps discretos dentro de la banda de cada regulador."""
    regs = [dict(r, taps=dict(r['taps'])) for r in model['regulators']]
    work = {**model, 'regulators': regs}
    controlled = [r for r in regs if r.get('control')]
    history, seen = [], set()
    initial = kwargs.pop('initial', None)
    for outer in range(1, max_outer + 1):
        sol = solve(work, initial=initial, **kwargs)
        if sol['status'] != 'converged' or not controlled:
            break
        initial = sol['x']
        changes = []
        for reg in controlled:
            ctrl = reg['control']
            for p, v in control_voltages(sol, reg).items():
                target, band = ctrl['set_v'][p], ctrl['bandwidth_v'][p] / 2
                if abs(v - target) <= band:
                    continue
                delta = round((target - v) / (reg['step_pu'] * CONTROL_BASE_V)) or (1 if target > v else -1)
                new = max(-reg['max_tap'], min(reg['max_tap'], reg['taps'][p] + delta))
                if new != reg['taps'][p]:
                    changes.append({'regulator': reg['id'], 'phase': p, 'old_tap': reg['taps'][p], 'new_tap': new,
                                    'control_v': round(v, 3)})
                    reg['taps'][p] = new
        history.append({'iteration': outer, 'tap_changes': changes})
        if not changes:
            break
        state = tuple((r['id'], tuple(sorted(r['taps'].items()))) for r in controlled)
        if state in seen:
            sol = {**sol, 'status': 'tap_oscillation'}
            break
        seen.add(state)
    else:
        sol = {**sol, 'status': 'tap_control_not_converged'}
    sol['tap_control'] = {'history': history, 'final_taps': {r['id']: r['taps'] for r in regs},
                          'controlled': [r['id'] for r in controlled]}
    sol['model'] = work
    return sol


def results(sol):
    """Tensiones, corrientes de rama, potencias y balance a partir del vector solución."""
    net, sys_, x = sol['net'], sol['system'], sol['x']
    model, n = net.model, sys_['n']
    V = x[:n]
    voltages = defaultdict(dict)
    for i, (bus, p) in enumerate(net.live):
        base = sol['bases'][i]
        voltages[bus][p] = {'v': V[i], 'pu': abs(V[i]) / base, 'angle_deg': math.degrees(np.angle(V[i]))}
    for bus, p in net.terminals:
        if (bus, p) not in net.pos:
            voltages[bus][p] = {'v': 0j, 'pu': 0.0, 'angle_deg': 0.0, 'deenergized': True}
    branches, losses = {}, defaultdict(complex)
    for ident, (f, t, yser, ysh, phases) in sys_['branch_y'].items():
        vf, vt = V[f], V[t]
        i_f = yser @ (vf - vt) + ysh @ vf
        i_t = yser @ (vt - vf) + ysh @ vt
        s = vf * i_f.conj() + vt * i_t.conj()
        branches[ident] = {'phases': phases, 'i_from': i_f, 'i_to': -i_t, 's_from': vf * i_f.conj(),
                           'loss': s}
    for line in model['lines']:
        if line['id'] in branches:
            losses[line['network_id']] += branches[line['id']]['loss'].sum()
    aux = {}
    for k, a in enumerate(sys_['aux']):
        aux.setdefault((a['kind'], a['id']), {})[a['phase']] = x[n + k]
    sources = {}
    for src in model['sources']:
        cur = aux.get(('source', src['id']), {})
        sources[src['id']] = {'network_id': src['network_id'], 'bus': src['bus'],
                              'current': cur,
                              'power': {p: src['voltage_v'][p] * c.conjugate() for p, c in cur.items()}}
    load_out = []
    currents = load_currents(x, sol['loads'], sol['powers'])
    for k, ((idx, s0, zip_, vz, base, load), (i, *_)) in enumerate(zip(sol['loads'], currents)):
        v = V[idx[0]] - (V[idx[1]] if len(idx) == 2 else 0)
        load_out.append({'id': load['id'], 'device_id': load['device_id'], 'network_id': load['network_id'],
                         'phases': load['phases'], 's': v * i.conjugate(),
                         's_nominal': sol['powers'][k] if sol['powers'] is not None else s0})
    total_load = defaultdict(complex)
    for item in load_out:
        total_load[item['network_id']] += item['s']
    return {'voltages': voltages, 'branches': branches, 'line_losses': dict(losses), 'aux': aux,
            'sources': sources, 'loads': load_out, 'load_totals': dict(total_load)}


def device_current(res, kind, ident):
    """Corriente por fase de una maniobra o regulador (desde la fuente hacia la carga)."""
    return res['aux'].get((kind, ident), {})


def _seq_phase(z0, z1):
    s, m = (z0 + 2 * z1) / 3, (z0 - z1) / 3
    return [[s if i == j else m for j in range(3)] for i in range(3)]


def from_native(data):
    """Convierte nandu-electrical-network-v1 (powerflow.py) al modelo nodal.

    La impedancia de Thévenin de la fuente se representa con una barra interna ideal
    y una rama trifásica de secuencias, como el Vsource de OpenDSS.
    """
    src = data['source']
    vll = src['voltage_ln_v'] * SQRT3 / 1000
    angle = math.radians(src.get('angle_deg', 0))
    buses = {b['id']: {'phases': list(b['phases']), 'network_id': 'native', 'internal': False,
                       'nominal_kvll': vll} for b in data['buses']}
    internal = src['bus'] + '#fuente'
    buses[internal] = {'phases': list(ABC), 'network_id': 'native', 'internal': True, 'nominal_kvll': vll}
    shift = {'A': 0, 'B': -2 * math.pi / 3, 'C': 2 * math.pi / 3}
    sources = [{'id': 'fuente', 'network_id': 'native', 'bus': internal, 'nominal_kvll': vll,
                'voltage_v': {p: src['voltage_ln_v'] * np.exp(1j * (angle + shift[p])) for p in ABC}}]
    z1, z0 = complex(src['r1_ohm'], src['x1_ohm']), complex(src['r0_ohm'], src['x0_ohm'])
    lines = [{'id': 'fuente#thevenin', 'network_id': 'native', 'from': internal, 'to': src['bus'],
              'phases': list(ABC), 'z': _seq_phase(z0, z1), 'y': [[0j] * 3 for _ in range(3)]}]
    omega = 2 * math.pi * data['frequency_hz']
    for line in data['lines']:
        if not line.get('enabled', True):
            continue
        k = len(line['phases'])
        lines.append({'id': line['id'], 'network_id': 'native', 'from': line['from_bus'], 'to': line['to_bus'],
                      'phases': list(line['phases']),
                      'z': [[complex(line['r_ohm'][i][j], line['x_ohm'][i][j]) for j in range(k)] for i in range(k)],
                      'y': [[1j * omega * line['c_nf'][i][j] * 1e-9 for j in range(k)] for i in range(k)]})
    loads = []
    for load in data['loads']:
        phases = [load['phase']] if load.get('connection', 'wye_grounded') == 'wye_grounded' else list(load['phases'])
        loads.append({'id': load['id'], 'device_id': load['id'], 'customer': load['id'], 'bus': load['bus'],
                      'network_id': 'native', 'connection': load.get('connection', 'wye_grounded'),
                      'phases': phases, 'p_w': load['p_w'], 'q_var': load['q_var'], 'zip': (0.0, 0.0, 1.0),
                      'vz_pu': 0.0})
    capacitors = [{'id': c['id'], 'device_id': c['id'], 'bus': c['bus'], 'connection': c['connection'],
                   'phases': [c['phase']] if c['connection'] == 'wye_grounded' else list(c['phases']),
                   'rated_q_var': c['rated_q_var'], 'rated_v': c['rated_voltage_v']}
                  for c in data.get('capacitors', []) if c.get('enabled', True)]
    return {'format': 'nandu-cyme-model-v1', 'networks': ['native'], 'buses': buses, 'sources': sources,
            'lines': lines, 'switches': [], 'regulators': [], 'loads': loads, 'capacitors': capacitors,
            'sections': [], 'meters': [], 'conventions': {}, 'issues': []}
