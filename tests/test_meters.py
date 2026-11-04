"""Reemplazo de lecturas de medidor: CLI (--meter), análisis y edición en la interfaz."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

from nandu import cyme_model
from nandu.model import build_model, validate
from nandu.storage import save

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))
try:
    from test_cyme_model import tables as cyme_tables
except ImportError:
    from tests.test_cyme_model import tables as cyme_tables


def meter_row(**changes):
    row = {'NetworkId': 'N', 'NodeId': 'n0', 'DemandType': 3, 'IsTotalDemand': 0, 'Val1A': 50.0, 'Val1B': 50.0,
           'Val1C': 50.0, 'Val2A': 95.0, 'Val2B': 95.0, 'Val2C': 95.0, 'Val1Total': 0.0, 'Val2Total': 0.0}
    return {**row, **changes}


def meter_tables(**changes):
    tables = cyme_tables(load_location=2, connection=0, load_phase=7)
    tables['CYMCUSTOMERLOAD'][0]['ConnectedKVA'] = 150.0  # base de KVAMethod
    # Taps neutros: la fila ABC equilibrada de Load Allocation puede cumplir un medidor equilibrado.
    tables['CYMREGULATOR'][0].update(TapPositionA=0.0, TapPositionB=0.0, TapPositionC=0.0)
    tables['CYMNETWORKMETER'] = [meter_row(**changes)]
    return tables


def project_file(folder):
    tables = meter_tables()
    tables['CYMNETWORK'] = [{'NetworkId': 'N', 'Color': 255}]
    tables['CYMNODE'] = [{'NetworkId': 'N', 'NodeId': n, 'X': 1000.0 * i, 'Y': 500.0} for i, n in enumerate(('n0', 'n1', 'n2'))]
    schemas = {name: {'columns': [{'name': k} for k in rows[0]], 'scope': 'test'} for name, rows in tables.items()}
    model = build_model(tables)
    path = Path(folder) / 'medidor.nandu.sqlite'
    save(path, {'excluded_tables': []}, schemas, tables, model, validate(model))
    return path


class ParseTests(unittest.TestCase):
    def test_parses_phases_amps_and_optional_pf(self):
        self.assertEqual(cyme_model.parse_meter_override('CZU06:A=150/95'),
                         {'network': 'CZU06', 'phases': 'A', 'amps': 150.0, 'pf_pct': 95.0})
        self.assertEqual(cyme_model.parse_meter_override('czu08:abc=180,5'),
                         {'network': 'czu08', 'phases': 'ABC', 'amps': 180.5, 'pf_pct': None})
        self.assertEqual(cyme_model.parse_meter_override('N:B=10/-90')['pf_pct'], -90.0)

    def test_rejects_ambiguous_text(self):
        for text in ('CZU06=150', 'CZU06:A', ':A=1', 'CZU06:D=1', 'CZU06:AA=1', 'CZU06:A=1/2/3', 'CZU06:A=x'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                cyme_model.parse_meter_override(text)


class OverrideTests(unittest.TestCase):
    def override(self, *texts, tables=None, networks=None):
        tables = tables or meter_tables()
        return tables, cyme_model.override_meters(tables, [cyme_model.parse_meter_override(t) for t in texts], networks)

    def test_replaces_readings_without_touching_the_input(self):
        tables, (new, changes) = self.override('N:AB=3/90', 'N:C=4')
        self.assertEqual(tables['CYMNETWORKMETER'][0]['Val1A'], 50.0)
        row = new['CYMNETWORKMETER'][0]
        self.assertEqual((row['Val1A'], row['Val2A'], row['Val1B'], row['Val1C'], row['Val2C']), (3.0, 90.0, 3.0, 4.0, 95.0))
        self.assertEqual([c['phase'] for c in changes], ['A', 'B', 'C'])
        self.assertEqual(changes[0]['before'], {'amps': 50.0, 'pf_pct': 95.0})
        self.assertEqual(cyme_model.meter_differences(tables['CYMNETWORKMETER'], new['CYMNETWORKMETER']), changes)

    def test_model_uses_overridden_values(self):
        _, (new, _) = self.override('N:A=7/80')
        meter = cyme_model.build(new)['meters'][0]
        self.assertEqual(meter['values']['A'], (7.0, 80.0))

    def test_rejects_instead_of_choosing(self):
        cases = [('X:A=1',), ('N:A=1', 'N:A=2'), ('N:A=1/0',), ('N:A=1/101',), ('N:A=-1',)]
        for texts in cases:
            with self.subTest(texts=texts), self.assertRaises(ValueError):
                self.override(*texts)
        with self.assertRaises(ValueError):
            self.override('N:A=1', networks=['OTRA'])
        with self.assertRaises(ValueError):
            self.override('N:A=1', tables=meter_tables(DemandType=1))
        two = meter_tables()
        two['CYMNETWORKMETER'].append(meter_row(NodeId='n1'))
        with self.assertRaises(ValueError):
            self.override('N:A=1', tables=two)

    def test_zero_current_marks_phase_unmeasured(self):
        _, (new, _) = self.override('N:C=0')
        self.assertEqual(cyme_model.build(new)['meters'][0]['values']['C'], (0.0, 95.0))


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = project_file(self.tmp.name)
        from nandu.ui.service import Project
        self.project = Project(self.path)
        self.key = {'NetworkId': 'N', 'NodeId': 'n0'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_meter_node_shows_editable_readings_only(self):
        props = self.project.properties('node', 'n0')
        group = next(g for g in props['groups'] if g['name'].startswith('Meter N'))
        fields = {f[0]: f for f in group['fields']}
        self.assertEqual(fields['Current A (A)'][2]['key'], self.key)
        self.assertEqual(fields['PF C (%)'][2]['field'], 'Val2C')
        self.assertIsNone(fields['Demand Type'][2])
        explorer = self.project.explorer()[0]['groups']
        self.assertEqual(explorer['Meter'], [{'number': 'N @ n0', 'node': 'n0'}])

    def test_edit_marks_meter_as_modified_and_undoes(self):
        self.project.edit({'op': 'set', 'table': 'CYMNETWORKMETER', 'key': self.key,
                           'changes': {'Val1A': '3,5', 'Val2A': '-90'}})
        self.assertEqual(self.project._model()['meters'][0]['values']['A'], (3.5, -90.0))
        phases = self.project.geometry()['meters'][0]['phases']
        self.assertEqual((phases['A']['modified'], phases['B']['modified']), (True, False))
        self.project.undo()
        self.assertFalse(self.project.geometry()['meters'][0]['phases']['A']['modified'])

    def test_invalid_readings_and_fields_are_rejected(self):
        from nandu.ui.editing import EditError
        before = [dict(r) for r in self.project.tables['CYMNETWORKMETER']]
        for changes in ({'Val2A': 0}, {'Val2B': 150}, {'Val1C': -2}, {'Val1A': 'x'}, {'DemandType': 1},
                        {'Precision1A': 1.0}):
            with self.subTest(changes=changes), self.assertRaises(EditError):
                self.project.edit({'op': 'set', 'table': 'CYMNETWORKMETER', 'key': self.key, 'changes': changes})
        self.assertEqual(self.project.tables['CYMNETWORKMETER'], before)
        self.assertEqual(self.project.edit_state()['count'], 0)

    @unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
    def test_results_warn_when_meters_differ_from_file(self):
        result = self.project.run('estimate')
        self.assertEqual(result['meter_overrides'], [])
        self.project.edit({'op': 'set', 'table': 'CYMNETWORKMETER', 'key': self.key,
                           'changes': {'Val1A': 60.0, 'Val1B': 60.0, 'Val1C': 60.0}})
        result = self.project.run('allocate')
        self.assertEqual(result['status'], 'converged')
        self.assertEqual([(d['phase'], d['after']['amps']) for d in result['meter_overrides']],
                         [('A', 60.0), ('B', 60.0), ('C', 60.0)])
        self.assertIn('Medidores modificados', result['messages'][0]['text'])


@unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = project_file(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        from nandu.cli import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([*args])
        return code, out.getvalue(), err.getvalue()

    def test_override_changes_result_and_is_reported(self):
        from nandu import analysis
        base = analysis.allocate(str(self.path))
        self.assertNotIn('meter_overrides', base)
        meters = [cyme_model.parse_meter_override('N:ABC=60')]
        report = analysis.allocate(str(self.path), meter_overrides=meters)
        self.assertEqual(len(report['meter_overrides']), 3)
        self.assertIn('no es comparable', report['meter_overrides_note'])
        p = lambda r: r['summary']['N']['source_power']['kw']
        self.assertEqual((base['status'], report['status']), ('converged', 'converged'))
        self.assertAlmostEqual(p(report) / p(base), 60 / 50, delta=0.02)

    def test_cli_option_for_estimate_and_errors(self):
        code, out, _ = self.run_cli('estimate', str(self.path), '--meter', 'N:A=52/94')
        self.assertIn(code, (0, 3))
        self.assertEqual(json.loads(out)['meter_overrides'][0]['after'], {'amps': 52.0, 'pf_pct': 94.0})
        code, _, err = self.run_cli('allocate', str(self.path), '--meter', 'N:A=52/0')
        self.assertEqual(code, 1)
        self.assertIn('factor de potencia', err)


if __name__ == '__main__':
    unittest.main()
