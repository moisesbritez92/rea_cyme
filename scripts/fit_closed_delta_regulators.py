"""Contrasta modelos de regulador delta cerrado con resultados CYME guardados por el MCP."""
import glob
import json
import os

import numpy as np
from scipy.optimize import least_squares


def num(s):
    return float(s.replace(',', '.'))


def matrix(n, conn):
    if conn == 'lead':
        return np.array([[n[0], -n[0], 0], [0, n[1], -n[1]], [-n[2], 0, n[2]]])
    return np.array([[n[0], 0, -n[0]], [-n[1], n[1], 0], [0, -n[2], n[2]]])


seen = set()
for path in sorted(glob.glob(os.path.join(r'C:\Users\moise\Documents\020_CYM_MCP\runs', '*', 'result.json'))):
    try:
        d = json.load(open(path, encoding='utf-8'))
    except Exception:
        continue
    for reg in d.get('final_regulators') or []:
        r = reg.get('results') or {}
        if 'VLNAinput' not in r or reg['device'].get('ConnectionConfiguration') != 'ClosedDelta':
            continue
        taps = np.array([num(r['RegTap' + p]) for p in 'ABC'])
        key = (reg['device_number'], tuple(taps))
        if key in seen:
            continue
        seen.add(key)
        vin = np.array([num(r['VLN' + p + 'input']) for p in 'ABC'])
        vout = np.array([num(r['VLN' + p]) for p in 'ABC'])
        vll = np.array([num(r['VLL' + p]) for p in 'ABC'])
        eq = reg['equipment']
        step = num(eq['MaximumBoost']) / 100 / (int(eq['NumberOfTaps']) / 2)
        out = []
        for typ in ('A', 'B'):
            ratio = 1 + step * taps if typ == 'A' else 1 / (1 - step * taps)
            for conn in ('lead', 'lag'):
                M = np.eye(3) + matrix(ratio - 1, conn)

                def f(dd):
                    v = M @ (vin * np.exp(1j * np.radians([0, -120 + dd[0], 120 + dd[1]])))
                    return np.r_[np.abs(v) - vout, np.abs(v - np.roll(v, -1)) - vll]
                s = least_squares(f, [0, 0])
                out.append(f'{typ}-{conn} {1000 * np.abs(s.fun).max():.1f}V')
        print(os.path.basename(os.path.dirname(path))[:38], reg['device_number'], taps.astype(int).tolist(), out)
        if len(seen) > 14:
            raise SystemExit
