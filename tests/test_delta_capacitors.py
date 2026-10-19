from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import unittest

from nandu.powerflow import solve, validate_model


def model():
    return json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'radial-balanced.json').read_text(encoding='utf-8'))


def delta_loads(data):
    data['loads'] = [{'id': pair, 'bus': 'carga', 'phases': list(pair), 'connection': 'delta',
                      'model': 'constant_power', 'p_w': 1000, 'q_var': 0} for pair in ('AB', 'BC', 'CA')]


def cap(connection='wye_grounded'):
    result = {'id': 'cap', 'bus': 'carga', 'connection': connection, 'rated_q_var': 300, 'enabled': True}
    if connection == 'wye_grounded':
        result.update(phase='A', rated_voltage_v=230)
    else:
        result.update(phases=['A', 'B'], rated_voltage_v=230 * math.sqrt(3))
    return result


class InputTests(unittest.TestCase):
    def test_delta_requires_two_distinct_existing_phases(self):
        for pair in (['A'], ['A', 'A'], ['A', 'B', 'C'], ['A', 'D']):
            data = model()
            delta_loads(data)
            data['loads'][0]['phases'] = pair
            with self.assertRaises(ValueError):
                validate_model(data)

    def test_delta_requires_a_path_for_both_phases(self):
        data = model()
        delta_loads(data)
        line = data['lines'][0]
        line.update(phases=['A'], r_ohm=[[0.2]], x_ohm=[[0]], c_nf=[[0]])
        with self.assertRaisesRegex(ValueError, 'sin camino'):
            validate_model(data)

    def test_capacitor_ratings_and_boolean_are_checked(self):
        for field, value in [('rated_q_var', -1), ('rated_voltage_v', 0), ('enabled', 1)]:
            data = model()
            data['capacitors'] = [{**cap(), field: value}]
            with self.assertRaises(ValueError):
                validate_model(data)


@unittest.skipUnless(importlib.util.find_spec('dss'), 'Motor opcional: ejecutar con .venv\\Scripts\\python.exe')
class EngineTests(unittest.TestCase):
    def test_balanced_delta_matches_equal_total_power_wye(self):
        baseline = solve(model())
        data = model()
        delta_loads(data)
        result = solve(data)
        self.assertEqual(result['status'], 'completed')
        for a, b in zip(baseline['buses'], result['buses']):
            self.assertAlmostEqual(a['magnitude_v'], b['magnitude_v'], places=5)
        self.assertAlmostEqual(result['power_balance']['loads_w_var'][0], 3000, places=4)

    def test_one_ab_load_has_no_c_current_and_opposite_terminal_currents(self):
        data = model()
        delta_loads(data)
        data['loads'] = data['loads'][:1]
        result = solve(data)
        self.assertEqual(result['status'], 'completed')
        branch = result['loads'][0]['terminals'][0]['conductors']
        self.assertEqual([v['node_number'] for v in branch], [1, 2])
        self.assertLess(abs(sum(complex(*c['current_a']) for c in branch)), 1e-6)
        line_c = result['lines'][0]['terminals'][0]['conductors'][2]['current_magnitude_a']
        self.assertLess(line_c, 1e-6)

    def test_wye_and_delta_capacitor_follow_voltage_squared(self):
        for connection in ('wye_grounded', 'delta'):
            data = model()
            data['capacitors'] = [cap(connection)]
            result = solve(data)
            self.assertEqual(result['status'], 'completed')
            item = result['capacitors'][0]
            self.assertAlmostEqual(item['net_power_absorbed_w_var'][1], item['expected_q_absorbed_var'], places=5)
            self.assertLess(result['power_balance']['capacitors_absorbed_w_var'][1], 0)
            self.assertLess(abs(result['power_balance']['residual_w_var'][1]), 1e-4)

    def test_disabled_capacitor_matches_baseline(self):
        baseline = solve(model())
        data = model()
        data['capacitors'] = [{**cap('delta'), 'enabled': False}]
        result = solve(data)
        self.assertEqual(result['capacitors'], [{'id': 'cap', 'enabled': False}])
        for a, b in zip(baseline['buses'], result['buses']):
            self.assertAlmostEqual(a['magnitude_v'], b['magnitude_v'], places=6)

    def test_compensation_reduces_source_reactive_power(self):
        data = model()
        delta_loads(data)
        for load in data['loads']:
            load['q_var'] = 500
        baseline = solve(data)
        data['capacitors'] = [{**cap('delta'), 'id': pair, 'phases': list(pair)} for pair in ('AB', 'BC', 'CA')]
        original = deepcopy(data)
        result = solve(data)
        self.assertEqual(result['status'], 'completed')
        self.assertLess(result['power_balance']['source_to_network_w_var'][1], baseline['power_balance']['source_to_network_w_var'][1])
        self.assertEqual(data, original)


if __name__ == '__main__':
    unittest.main()
