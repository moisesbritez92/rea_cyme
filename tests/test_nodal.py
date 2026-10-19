from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import unittest

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))
if ENGINE:
    import numpy as np
    from nandu import nodal

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def native(name):
    return json.loads((EXAMPLES / name).read_text(encoding='utf-8'))


def feeder(taps=(11, 12, 9), connection='closed_delta', load_kw=300.0):
    """Fuente ideal 23 kV, regulador y una carga trifásica Yg detrás de una línea corta."""
    vln = 23000 / math.sqrt(3)
    shift = {'A': 0, 'B': -2 * math.pi / 3, 'C': 2 * math.pi / 3}
    bus = lambda: {'phases': list('ABC'), 'network_id': 'N', 'internal': False, 'nominal_kvll': 23.0}
    z = [[complex(0.5, 0.4) if i == j else complex(0.1, 0.15) for j in range(3)] for i in range(3)]
    return {'networks': ['N'], 'buses': {'S': bus(), 'R': bus(), 'L': bus()},
            'sources': [{'id': 'src', 'network_id': 'N', 'bus': 'S', 'nominal_kvll': 23.0,
                         'voltage_v': {p: vln * np.exp(1j * shift[p]) for p in 'ABC'}}],
            'lines': [{'id': 'l1', 'network_id': 'N', 'from': 'R', 'to': 'L', 'phases': list('ABC'),
                       'z': z, 'y': [[0j] * 3 for _ in range(3)]}],
            'switches': [], 'capacitors': [], 'meters': [],
            'regulators': [{'id': 'reg', 'from': 'S', 'to': 'R', 'phases': list('ABC'), 'connection': connection,
                            'taps': dict(zip('ABC', taps)), 'step_pu': 0.00625, 'max_tap': 16}],
            'loads': [{'id': f'c{p}', 'device_id': 'c', 'customer': 'c', 'bus': 'L', 'network_id': 'N',
                       'connection': 'wye_grounded', 'phases': [p], 'p_w': load_kw * 1000 * (1 + 0.2 * k),
                       'q_var': 100e3, 'zip': (0.0, 0.0, 1.0), 'vz_pu': 0.0} for k, p in enumerate('ABC')]}


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class AnalyticTests(unittest.TestCase):
    def test_radial_balanced_matches_closed_form(self):
        sol = nodal.solve(nodal.from_native(native('radial-balanced.json')))
        self.assertEqual(sol['status'], 'converged')
        res = nodal.results(sol)
        expected = (230 + math.sqrt(230 ** 2 - 4 * 0.21 * 1000)) / 2
        for p in 'ABC':
            self.assertAlmostEqual(abs(res['voltages']['carga'][p]['v']), expected, places=8)
        self.assertAlmostEqual(res['branches']['linea-1']['loss'].sum().real, 11.433109901, places=6)

    def test_delta_capacitors_match_opendss_reference(self):
        res = nodal.results(nodal.solve(nodal.from_native(native('delta-compensated.json'))))
        source = sum(res['sources']['fuente']['power'].values())
        self.assertAlmostEqual(source.imag, 607.160440, places=5)
        self.assertAlmostEqual(res['branches']['linea']['loss'].sum().real, 11.901423, places=5)

    @unittest.skipUnless(importlib.util.find_spec('dss'), 'requiere DSS-Python')
    def test_unbalanced_case_agrees_with_opendss(self):
        from nandu import powerflow
        data = native('radial-balanced.json')
        data['lines'][0]['x_ohm'] = [[0.3, 0.1, 0.1], [0.1, 0.3, 0.1], [0.1, 0.1, 0.3]]
        data['lines'][0]['r_ohm'] = [[0.2, 0.05, 0.05], [0.05, 0.2, 0.05], [0.05, 0.05, 0.2]]
        for load, p in zip(data['loads'], (500, 1000, 1500)):
            load['p_w'], load['q_var'] = p, p / 3
        ref = powerflow.solve(deepcopy(data))
        res = nodal.results(nodal.solve(nodal.from_native(data)))
        for row in ref['buses']:
            if row['bus'] == 'carga':
                self.assertAlmostEqual(abs(res['voltages']['carga'][row['phase']]['v']), row['magnitude_v'], places=6)


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class RegulatorTests(unittest.TestCase):
    def test_closed_delta_relation_and_power_conservation(self):
        model = feeder()
        sol = nodal.solve(model)
        self.assertEqual(sol['status'], 'converged')
        info = sol['system']['regulators']['reg']
        x = sol['x']
        vin, vout = x[info['inputs']], x[info['outputs']]
        np.testing.assert_allclose(vout, info['M'] @ vin, atol=1e-6)
        # Serie en X y shunt entre X y la fase siguiente (ajuste a resultados CYME).
        n = 0.00625 * 11
        self.assertAlmostEqual(abs(vout[0] - (vin[0] + n * (vin[0] - vin[1]))), 0, places=6)
        j = x[info['aux']:info['aux'] + 3]
        s_out = np.sum(vout * j.conj())
        s_in = np.sum(vin * (info['M'].T @ j).conj())
        self.assertAlmostEqual(abs(s_in - s_out), 0, places=3)

    def test_wye_regulator_is_per_phase_ratio(self):
        sol = nodal.solve(feeder(taps=(4, 0, -4), connection='wye_grounded'))
        info = sol['system']['regulators']['reg']
        ratio = np.abs(sol['x'][info['outputs']] / sol['x'][info['inputs']])
        np.testing.assert_allclose(ratio, [1.025, 1.0, 0.975], atol=1e-12)

    def test_regulator_orientation_follows_source(self):
        model = feeder()
        reg = model['regulators'][0]
        reg['from'], reg['to'] = reg['to'], reg['from']
        sol = nodal.solve(model)
        self.assertEqual(sol['system']['regulators']['reg']['input_bus'], 'S')


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class TapControlTests(unittest.TestCase):
    def _controlled(self, connection='wye_grounded', set_v=126.0, r=0.0, x=0.0, load_kw=300.0):
        model = feeder(taps=(0, 0, 0), connection=connection, load_kw=load_kw)
        model['regulators'][0]['control'] = {
            'mode': 'terminal' if not (r or x) else 'load_center', 'ct_primary_a': 100.0,
            'set_v': dict.fromkeys('ABC', set_v), 'bandwidth_v': dict.fromkeys('ABC', 2.0),
            'r_v': dict.fromkeys('ABC', r), 'x_v': dict.fromkeys('ABC', x)}
        return model

    def test_terminal_control_brings_output_into_band(self):
        sol = nodal.solve_controlled(self._controlled(set_v=126.0))
        self.assertEqual(sol['status'], 'converged')
        reg = sol['model']['regulators'][0]
        for p, v in nodal.control_voltages(sol, reg).items():
            self.assertLessEqual(abs(v - 126.0), 1.0, p)
        self.assertTrue(all(t > 0 for t in reg['taps'].values()))
        self.assertTrue(sol['tap_control']['history'][0]['tap_changes'])

    def test_closed_delta_controls_line_to_line_voltage(self):
        sol = nodal.solve_controlled(self._controlled(connection='closed_delta', set_v=124.0))
        reg = sol['model']['regulators'][0]
        for v in nodal.control_voltages(sol, reg).values():
            self.assertLessEqual(abs(v - 124.0), 1.0)

    def test_line_drop_compensation_raises_taps(self):
        plain = nodal.solve_controlled(self._controlled(set_v=121.0))
        ldc = nodal.solve_controlled(self._controlled(set_v=121.0, r=6.0, x=6.0))
        taps = lambda s: sum(s['model']['regulators'][0]['taps'].values())
        self.assertGreater(taps(ldc), taps(plain))

    def test_taps_are_clamped_to_equipment_limits(self):
        sol = nodal.solve_controlled(self._controlled(set_v=200.0))
        self.assertEqual(set(sol['model']['regulators'][0]['taps'].values()), {16})

    def test_fixed_regulators_are_not_touched(self):
        sol = nodal.solve_controlled(feeder())
        self.assertEqual(sol['tap_control']['controlled'], [])
        self.assertEqual(sol['model']['regulators'][0]['taps'], {'A': 11, 'B': 12, 'C': 9})


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class SwitchTests(unittest.TestCase):
    def _with_switch(self, closed):
        model = feeder(taps=(0, 0, 0))
        model['buses']['T'] = dict(model['buses']['L'])
        model['switches'] = [{'id': 'sw', 'from': 'L', 'to': 'T', 'phases': list('ABC'), 'closed': closed}]
        model['loads'].append({**model['loads'][0], 'id': 'cola', 'bus': 'T', 'phases': ['B']})
        return model

    def test_closed_switch_reports_downstream_current(self):
        sol = nodal.solve(self._with_switch(list('ABC')))
        res = nodal.results(sol)
        cur = nodal.device_current(res, 'switch', 'sw')
        vb = res['voltages']['T']['B']['v']
        self.assertAlmostEqual(abs(cur['B']), abs(complex(300e3, 100e3) / vb), places=6)
        self.assertAlmostEqual(abs(cur['A']), 0, places=9)

    def test_open_phase_deenergizes_terminal_and_drops_its_load(self):
        sol = nodal.solve(self._with_switch(['A', 'C']))
        res = nodal.results(sol)
        self.assertTrue(res['voltages']['T']['B'].get('deenergized'))
        self.assertNotIn('cola', [l['id'] for l in res['loads']])


if __name__ == '__main__':
    unittest.main()
