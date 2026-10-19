"""Compara las cargas estimadas por Ñandu con los valores exactos del estimador de CYME.

Entradas:
  1. archivo .nandu.sqlite con el mismo estado de base que usó CYME;
  2. JSON de cambios de cargas generado por scripts/cyme_dse_changes.py.

Las filas ABC se comparan con la suma de las fases estimadas (CYME guarda un único
valor por fila). Uso:
  .venv\\Scripts\\python.exe scripts\\compare_cyme_dse.py data\\red.nandu.sqlite cambios.json [opciones JSON]
"""
import collections
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nandu import cyme_model, estimator  # noqa: E402
from nandu.storage import load_tables  # noqa: E402


def compare(model, reference, options=None):
    est = estimator.estimate(model, options=options)
    ours, start = collections.defaultdict(complex), collections.defaultdict(complex)
    for row in est['loads']:
        dev, cust, ph = row['id'].rsplit(':', 2)
        for key in ((dev, cust, ph), (dev, cust, 'ABC')):
            ours[key] += row['estimated_nominal_kva']
            start[key] += row['initial_kva']
    report = {}
    for net in sorted({r['network'] for r in reference}):
        kw, kvar, initial = [], [], []
        for r in reference:
            key = (r['device'], r['customer'], r['phase'])
            if r['network'] != net or 'kw1' not in r or key not in ours:
                continue
            initial.append(abs(start[key].real - r['kw0']))
            kw.append(ours[key].real - r['kw1'])
            if 'kvar1' in r:
                kvar.append(ours[key].imag - r['kvar1'])
        report[net] = {'rows': len(kw), 'initial_max_abs_kw': float(max(initial)),
                       'kw_rms': float(np.sqrt(np.mean(np.square(kw)))), 'kw_sum': float(np.sum(kw)),
                       'kvar_rms': float(np.sqrt(np.mean(np.square(kvar)))), 'kvar_sum': float(np.sum(kvar))}
    return {'status': est['status'], 'options': {k: v for k, v in est['options'].items() if not isinstance(v, tuple)},
            'networks': report}


if __name__ == '__main__':
    model = cyme_model.build(load_tables(sys.argv[1], cyme_model.TABLES))
    reference = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
    options = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    print(json.dumps(compare(model, reference, options), indent=2, ensure_ascii=False))
