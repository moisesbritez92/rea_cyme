from copy import deepcopy
import importlib.util
import json
import math
from pathlib import Path
import unittest

from nandu.powerflow import solve, validate_model


def model():
    return json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'radial-balanced.json').read_text(encoding='utf-8'))


class InputTests(unittest.TestCase):
    def test_open_line_cannot_silently_feed_load(self):
        data = model()
        data['lines'][0]['enabled'] = False
        with self.assertRaisesRegex(ValueError, 'sin camino'):
            validate_model(data)

    def test_invalid_matrix_is_rejected(self):
        for resistance in ([[0.2]], [[1, 2, 0], [2, 1, 0], [0, 0, 1]],
                           [[1, 0.1, 0], [0, 1, 0], [0, 0, 1]]):
            data = model()
            data['lines'][0]['r_ohm'] = resistance
            with self.assertRaises(ValueError):
                validate_model(data)

    def test_prepared_candidates_are_not_accepted_as_native_network(self):
        with self.assertRaises(ValueError):
            validate_model({'analysis': 'electrical_preparation', 'solver_ready': False})

    def test_unknown_fields_or_unsupported_connections_rejected(self):
        data = model()
        data['regulators'] = []
        with self.assertRaises(ValueError):
            validate_model(data)
        data = model()
        data['loads'][0]['connection'] = 'delta'
        with self.assertRaises(ValueError):
            validate_model(data)

    def test_duplicate_ids_and_absent_phase_rejected(self):
        data = model()
        data['loads'].append(deepcopy(data['loads'][0]))
        with self.assertRaises(ValueError):
            validate_model(data)
        data = model()
        data['buses'][1]['phases'] = ['C']
        with self.assertRaises(ValueError):
            validate_model(data)


@unittest.skipUnless(importlib.util.find_spec('dss'), 'Motor opcional: ejecutar con .venv\\Scripts\\python.exe')
class EngineTests(unittest.TestCase):
    def test_balanced_constant_power_matches_closed_form(self):
        data = model()
        before = deepcopy(data)
        report = solve(data)
        expected = (230 + math.sqrt(230 ** 2 - 4 * 0.21 * 1000)) / 2
        self.assertEqual(report['status'], 'completed')
        self.assertTrue(report['verification']['passed'])
        for bus in report['buses']:
            if bus['bus'] == 'carga':
                self.assertAlmostEqual(bus['magnitude_v'], expected, places=6)
        current = 1000 / expected
        self.assertAlmostEqual(report['network_losses_w_var'][0], 3 * 0.2 * current ** 2, places=5)
        self.assertLess(abs(report['power_balance']['residual_w_var'][0]), 1e-4)
        self.assertEqual(data, before)

    def test_unbalanced_phases_match_three_independent_analytical_solutions(self):
        data = model()
        powers = {'A': 500, 'B': 1000, 'C': 1500}
        for load in data['loads']:
            load['p_w'] = powers[load['phase']]
        report = solve(data)
        self.assertTrue(report['verification']['passed'])
        for bus in report['buses']:
            if bus['bus'] == 'carga':
                power = powers[bus['phase']]
                expected = (230 + math.sqrt(230 ** 2 - 4 * 0.21 * power)) / 2
                self.assertAlmostEqual(bus['magnitude_v'], expected, places=6)

    def test_single_phase_c_keeps_its_node_number(self):
        data = model()
        data['buses'][1]['phases'] = ['C']
        line = data['lines'][0]
        line.update(phases=['C'], r_ohm=[[0.2]], x_ohm=[[0]], c_nf=[[0]])
        data['loads'] = [data['loads'][2]]
        report = solve(data)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['lines'][0]['terminals'][0]['conductors'][0]['node_number'], 3)
        self.assertEqual([b['phase'] for b in report['buses'] if b['bus'] == 'carga'], ['C'])

    def test_capacitive_power_sign_and_coupled_line_balance(self):
        data = model()
        data['lines'][0]['x_ohm'] = [[0.3, 0.05, 0.05], [0.05, 0.3, 0.05], [0.05, 0.05, 0.3]]
        for load in data['loads']:
            load['q_var'] = -100
        report = solve(data)
        self.assertEqual(report['status'], 'completed')
        self.assertLess(report['power_balance']['source_to_network_w_var'][1], 0)
        self.assertLess(abs(report['power_balance']['residual_w_var'][1]), 1e-4)

    def test_fresh_context_does_not_reuse_previous_loads(self):
        solve(model())
        data = model()
        data['loads'] = []
        report = solve(data)
        self.assertEqual(report['loads'], [])
        self.assertLess(abs(report['network_losses_w_var'][0]), 1e-6)

    def test_unconverged_run_does_not_return_usable_voltages(self):
        data = model()
        data['solver']['max_iterations'] = 1
        report = solve(data)
        self.assertEqual(report['status'], 'not_converged')
        self.assertNotIn('buses', report)

    def test_names_are_not_interpreted_as_dss_commands(self):
        data = model()
        name = 'barra nueva\nclear\nsolve'
        data['buses'][1]['id'] = name
        data['lines'][0]['to_bus'] = name
        for load in data['loads']:
            load['bus'] = name
        report = solve(data)
        self.assertEqual(report['status'], 'completed')
        self.assertFalse(any(name in command for command in report['commands']))


if __name__ == '__main__':
    unittest.main()
