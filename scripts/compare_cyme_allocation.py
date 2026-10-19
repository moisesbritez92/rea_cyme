"""Compara la distribución de carga de Ñandu con el informe «Load Flow - Loads» de CYME.

CYME reporta potencia por fase de línea (kVA y FP con un decimal); las cargas delta de
Ñandu se convierten igual (V_fase · I_línea*). Uso:
  .venv\\Scripts\\python.exe scripts\\compare_cyme_allocation.py data\\red.nandu.sqlite informe_loads.csv RED [método]
"""
import collections
import csv
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nandu import allocation, cyme_model, estimator, nodal  # noqa: E402
from nandu.storage import load_tables  # noqa: E402


def cyme_loads(path):
    out = {}
    with open(path, encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            row = {k.strip(): v for k, v in row.items()}
            for p in 'ABC':
                kva, pf = row[f'Spot Adj {p} (kVA)'], row[f'Spot PF Adj {p} (%)']
                if kva not in ('', None):
                    out[(row['Equipment No'], p)] = (float(kva), float(pf))
    return out


def line_phase_powers(model, res):
    bus_of = {l['id']: l['bus'] for l in model['loads']}
    s = collections.defaultdict(complex)
    for l in res['loads']:
        if len(l['phases']) == 1:
            s[(l['device_id'], l['phases'][0])] += l['s']
            continue
        x, y = l['phases']
        v = res['voltages'][bus_of[l['id']]]
        i = (l['s'] / (v[x]['v'] - v[y]['v'])).conjugate()
        s[(l['device_id'], x)] += v[x]['v'] * i.conjugate()
        s[(l['device_id'], y)] -= v[y]['v'] * i.conjugate()
    return s


def compare(model, reference, network, method='KVAMethod', **kwargs):
    meters = [m for m in estimator.meters_from_model(model, None) if m['id'] == f'meter:{network}']
    alloc = allocation.allocate(model, meters, method, **kwargs)
    res = nodal.results(alloc['solution'])
    ours = line_phase_powers(model, res)
    err_kva, err_pf = [], []
    for (dev, p), (kva, pf) in reference.items():
        s = ours.get((dev, p))
        if s is None or kva == 0:
            continue
        err_kva.append(abs(s) / 1000 - kva)
        err_pf.append(100 * s.real / abs(s) - pf)
    return {'status': alloc['status'], 'iterations': len(alloc['iterations']), 'compared': len(err_kva),
            'kva_rms': float(np.sqrt(np.mean(np.square(err_kva)))), 'kva_sum': float(np.sum(err_kva)),
            'kva_max_abs': float(np.max(np.abs(err_kva))), 'pf_rms': float(np.sqrt(np.mean(np.square(err_pf))))}


if __name__ == '__main__':
    tables = load_tables(sys.argv[1], cyme_model.TABLES)
    network = sys.argv[3]
    model = cyme_model.build(tables, [network])
    method = sys.argv[4] if len(sys.argv) > 4 else 'KVAMethod'
    reference = cyme_loads(sys.argv[2])
    for balanced in (False, True):
        print('filas ABC equilibradas' if balanced else 'por fase', compare(model, reference, network, method,
                                                                          balanced_rows=balanced))
