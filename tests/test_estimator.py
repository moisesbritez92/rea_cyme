import importlib.util
import math
import unittest

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))
if ENGINE:
    import numpy as np
    from nandu import estimator, nodal
    try:  # discover -s tests pone la carpeta en sys.path; un paquete 'tests' ajeno puede taparla
        from test_nodal import feeder
    except ImportError:
        from tests.test_nodal import feeder


def head_meter(model, factor=1.0):
    """Medidor AMP-PF en la fuente con la corriente y el FP del flujo, escalada por factor."""
    res = nodal.results(nodal.solve(model))
    src = res['sources']['src']
    phases = {}
    for p, cur in src['current'].items():
        s = src['power'][p]
        phases[p] = (abs(cur) * factor, 100 * s.real / abs(s))
    return {'id': 'meter:N', 'element': ('source', 'src'), 'bus': 'S', 'type': 'AMP_PF', 'phases': phases}


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class EstimatorTests(unittest.TestCase):
    def test_consistent_meter_keeps_pseudo_measurements(self):
        model = feeder()
        est = estimator.estimate(model, [head_meter(model)])
        self.assertEqual(est['status'], 'converged')
        self.assertLess(est['solution_cost'], 1e-8)
        for row in est['loads']:
            self.assertAlmostEqual(abs(row['estimated_kva'] - row['initial_kva']), 0, places=5)

    def test_meter_pulls_loads_and_respects_network_equations(self):
        model = feeder()
        meter = head_meter(model, factor=1.10)
        est = estimator.estimate(model, [meter])
        self.assertEqual(est['status'], 'converged')
        before = sum(r['initial_kva'] for r in est['loads']).real
        after = sum(r['estimated_kva'] for r in est['loads']).real
        self.assertGreater(after, before * 1.08)
        for r in est['meter_residuals']:
            if r['quantity'] == 'current_a':
                self.assertLess(abs(r['measured'] - r['estimated']) / r['measured'], 0.01)
        # La solución estimada cumple el flujo de carga con las cargas estimadas.
        powers = np.array([r['estimated_kva'] for r in est['loads']]) * 1000
        flow = nodal.solve(model, powers=powers)
        res = nodal.results(flow)
        for p, cur in res['sources']['src']['current'].items():
            self.assertAlmostEqual(abs(cur), abs(est['results']['sources']['src']['current'][p]), places=6)

    def test_larger_loads_absorb_more_correction(self):
        # Con precisión relativa, sigma ~ P y la corrección de cada carga ~ P^2 (ayuda de CYME).
        model = feeder(taps=(0, 0, 0))
        base = model['loads'][0]
        model['loads'] = [{**base, 'id': 'chica', 'p_w': 100e3, 'q_var': 0.0},
                          {**base, 'id': 'grande', 'p_w': 200e3, 'q_var': 0.0}]
        meter = head_meter(model, factor=1.05)
        meter['phases'] = {'A': meter['phases']['A']}
        est = estimator.estimate(model, [meter], {'pf_meter_precision': 1e6})
        delta = {r['id']: (r['estimated_kva'] - r['initial_kva']).real for r in est['loads']}
        self.assertAlmostEqual(delta['grande'] / delta['chica'], 4.0, delta=0.15)


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class CymeConventionTests(unittest.TestCase):
    def test_locked_loads_are_not_estimated(self):
        model = feeder()
        model['loads'][1]['locked'] = True
        est = estimator.estimate(model, [head_meter(model, factor=1.1)])
        rows = {r['id']: r for r in est['loads']}
        self.assertAlmostEqual(abs(rows['cB']['estimated_kva'] - rows['cB']['initial_kva']), 0, places=4)
        self.assertGreater(abs(rows['cA']['estimated_kva'] - rows['cA']['initial_kva']), 1.0)

    def test_amp_pf_meter_enters_as_current_and_powers(self):
        model = feeder()
        est = estimator.estimate(model, [head_meter(model)])
        kinds = {r['quantity'] for r in est['meter_residuals']}
        self.assertEqual(kinds, {'current_a', 'active_power_kw', 'reactive_power_kvar'})
        legacy = estimator.estimate(model, [head_meter(model)], {'meter_model': 'amp_pf'})
        self.assertEqual({r['quantity'] for r in legacy['meter_residuals']}, {'current_a', 'power_factor'})

    def test_customer_row_grouping_keeps_abc_balanced(self):
        model = feeder()
        for load in model['loads']:
            load.update(row='abc', share=1 / 3, p_w=100e3, q_var=30e3)
        meter = head_meter(model, factor=1.0)
        meter['phases']['B'] = (meter['phases']['B'][0] * 1.2, meter['phases']['B'][1])
        est = estimator.estimate(model, [meter], {'group_customer_rows': True})
        values = [r['estimated_kva'] for r in est['loads']]
        self.assertAlmostEqual(abs(values[0] - values[1]), 0, places=9)
        self.assertAlmostEqual(abs(values[1] - values[2]), 0, places=9)


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class QualityIndexTests(unittest.TestCase):
    def test_diagonal_matches_dense_inverse(self):
        import scipy.sparse as sp
        rng = np.random.default_rng(7)
        q, p, r = 5, 4, 2
        H = rng.normal(size=(q, p))
        C = rng.normal(size=(r, p))
        K = sp.bmat([[sp.identity(q), sp.csr_matrix(H), None], [sp.csr_matrix(H.T), None, sp.csr_matrix(C.T)],
                     [None, sp.csr_matrix(C), None]], format='csc')
        mu = rng.normal(size=q)
        rn, omega = estimator._normalized_residuals(K, q, mu, chunk=2)
        dense = np.linalg.inv(K.toarray())[:q, :q]
        np.testing.assert_allclose(omega, np.diag(dense), atol=1e-12)
        np.testing.assert_allclose(rn, np.abs(mu) / np.sqrt(np.diag(dense)), atol=1e-12)
        # Con R = I, la covarianza de residuos cumple 0 <= Ω_ii <= 1.
        self.assertTrue(np.all((omega > -1e-12) & (omega <= 1 + 1e-12)))

    def test_consistent_case_passes_chi_square(self):
        model = feeder()
        est = estimator.estimate(model, [head_meter(model)], {'quality_indices': True})
        qi = est['quality_indices']
        self.assertTrue(qi['solution_cost_passes'])
        self.assertEqual(qi['degrees_of_freedom'], est['measurements'] - est['state_variables'] + est['constraints'])
        self.assertEqual(qi['erroneous_measurements'], 0)

    def test_inconsistent_meter_is_flagged(self):
        model = feeder()
        est = estimator.estimate(model, [head_meter(model, factor=2.0)], {'quality_indices': True})
        self.assertFalse(est['quality_indices']['solution_cost_passes'])
        self.assertTrue(all('normalized_residual' in r for r in est['meter_residuals']))


if __name__ == '__main__':
    unittest.main()
