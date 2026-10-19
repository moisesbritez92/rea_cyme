import importlib.util
import math
import unittest

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))
if ENGINE:
    from nandu import allocation, estimator, nodal
    try:  # discover -s tests pone la carpeta en sys.path; un paquete 'tests' ajeno puede taparla
        from test_nodal import feeder
    except ImportError:
        from tests.test_nodal import feeder


def two_loads(locked=False):
    model = feeder(taps=(0, 0, 0))
    base = model['loads'][0]
    model['loads'] = [{**base, 'id': 'chica', 'phase': 'A', 'share': 1.0, 'connected_kva': 25.0, 'p_w': 1e3, 'q_var': 0.0},
                      {**base, 'id': 'grande', 'phase': 'A', 'share': 1.0, 'connected_kva': 75.0, 'p_w': 1e3, 'q_var': 0.0,
                       'utilization_pct': 100.0, 'locked': locked}]
    return model


def meter(amps=40.0, pf=95.0):
    return {'id': 'meter:N', 'element': ('source', 'src'), 'bus': 'S', 'type': 'AMP_PF', 'phases': {'A': (amps, pf)}}


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class AllocationTests(unittest.TestCase):
    def _by_id(self, result):
        return {item[5]['id']: p for item, p in zip(result['prepared'], result['powers'])}

    def test_formula_without_load_flow_matches_cyme_definition(self):
        model = two_loads()
        res = allocation.allocate(model, [meter()], use_load_flow=False)
        p = self._by_id(res)
        target = res['targets']['src']['A']
        self.assertAlmostEqual(abs(target), 40 * 23000 / math.sqrt(3), delta=1e-6)
        self.assertAlmostEqual(p['grande'] / p['chica'], 3.0, places=12)
        # kvar = kW * sqrt(1/FP^2 - 1)
        self.assertAlmostEqual(p['chica'].imag, p['chica'].real * math.sqrt(1 / 0.95 ** 2 - 1), places=6)

    def test_load_flow_adjustment_reaches_meter_within_tolerance(self):
        model = two_loads()
        res = allocation.allocate(model, [meter(amps=120.0)], tolerance_pct=0.01)
        self.assertEqual(res['status'], 'converged')
        flow = nodal.results(res['solution'])
        got = flow['sources']['src']
        self.assertAlmostEqual(abs(got['current']['A']), 120.0, delta=120.0 * 2e-4)
        s = got['power']['A']
        self.assertAlmostEqual(s.real / abs(s), 0.95, delta=2e-4)
        p = self._by_id(res)
        self.assertAlmostEqual(p['grande'] / p['chica'], 3.0, places=9)

    def test_locked_load_keeps_its_value(self):
        model = two_loads(locked=True)
        res = allocation.allocate(model, [meter(amps=60.0)])
        p = self._by_id(res)
        self.assertEqual(p['grande'], complex(1e3, 0.0))
        self.assertGreater(p['chica'].real, 100e3)

    def test_leading_power_factor_gives_negative_reactive(self):
        res = allocation.allocate(two_loads(), [meter(pf=-98.0)], use_load_flow=False)
        self.assertLess(res['targets']['src']['A'].imag, 0)

    def test_abc_rows_stay_balanced_with_smallest_phase_allocation(self):
        model = feeder(taps=(0, 0, 0))
        base = model['loads'][0]
        model['loads'] = [{**base, 'id': f'abc:{p}', 'phases': [p], 'phase': p, 'share': 1 / 3, 'row': 'X:X:ABC',
                           'connected_kva': 300.0, 'p_w': 1e3, 'q_var': 0.0} for p in 'ABC']
        unbalanced = {**meter(), 'phases': {'A': (30.0, 95.0), 'B': (20.0, 95.0), 'C': (40.0, 95.0)}}
        res = allocation.allocate(model, [unbalanced], use_load_flow=False)
        p = self._by_id(res)
        self.assertEqual(len({p['abc:A'], p['abc:B'], p['abc:C']}), 1)
        self.assertAlmostEqual(abs(p['abc:A']), abs(res['targets']['src']['B']), places=6)
        free = allocation.allocate(model, [unbalanced], use_load_flow=False, balanced_rows=False)
        self.assertNotEqual(self._by_id(free)['abc:A'], self._by_id(free)['abc:C'])

    def test_estimator_can_start_from_allocation(self):
        model = two_loads()
        est = estimator.estimate(model, [meter(amps=80.0)], {'initialization': 'LoadAllocation'})
        self.assertEqual(est['status'], 'converged')
        self.assertEqual(est['initial_allocation']['status'], 'converged')
        self.assertLess(est['solution_cost'], 1.0)


if __name__ == '__main__':
    unittest.main()
