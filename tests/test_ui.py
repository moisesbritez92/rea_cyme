import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen

from nandu.model import build_model, validate
from nandu.storage import save

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))
try:  # discover -s tests pone la carpeta en sys.path; un paquete 'tests' ajeno puede taparla
    from test_cyme_model import tables as cyme_tables
except ImportError:
    from tests.test_cyme_model import tables as cyme_tables


def project_file(folder):
    tables = cyme_tables(load_location=2, connection=0, load_phase=7)
    tables['CYMNETWORK'] = [{'NetworkId': 'N', 'Color': 255}]
    tables['CYMNODE'] = [{'NetworkId': 'N', 'NodeId': n, 'X': 1000.0 * i, 'Y': 500.0} for i, n in enumerate(('n0', 'n1', 'n2'))]
    tables['CYMDBPARAMETERS'] = [{'ID': 'CoordinateSystem', 'ValueStr': 'EPSG:32721'}]
    schemas = {name: {'columns': [{'name': k} for k in rows[0]], 'scope': 'test'} for name, rows in tables.items()}
    model = build_model(tables)
    path = Path(folder) / 'prueba.nandu.sqlite'
    save(path, {'excluded_tables': []}, schemas, tables, model, validate(model))
    return path


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = project_file(self.tmp.name)
        from nandu.ui.service import Project
        self.project = Project(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_geometry_uses_network_color_and_device_locations(self):
        geo = self.project.geometry()
        self.assertEqual(geo['networks'], [{'id': 'N', 'color': '#ff0000'}])
        self.assertEqual(geo['crs'], 'EPSG:32721')
        devices = {d['number']: d for s in geo['sections'] for d in s['devices']}
        self.assertEqual((devices['R1']['type'], devices['R1']['location']), ('Regulator', 'From'))
        self.assertEqual(devices['C1']['location'], 'To')

    def test_properties_decode_verified_codes_only(self):
        props = self.project.properties('section', 's1')
        reg = next(g for g in props['groups'] if g['name'] == 'Regulator R1')
        fields = dict(map(tuple, reg['fields']))
        self.assertEqual(fields['ConnectionConfiguration'], 'ClosedDelta')
        self.assertEqual(fields['SettingOption'], 'FixedTap')
        with self.assertRaises(KeyError):
            self.project.properties('node', 'inexistente')

    @unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
    def test_load_flow_summary_balances(self):
        out = self.project.run('loadflow')
        self.assertEqual(out['status'], 'converged')
        rows = {r['item']: r for r in out['summary']['N']}
        src, load = rows['Sources (Swing)']['kw'], rows['Load used (Adjusted)']['kw']
        self.assertAlmostEqual(src, load + rows['Total Losses']['kw'], delta=0.02)
        self.assertAlmostEqual(load, 90.0, delta=0.01)
        self.assertIn('n2', out['nodes'])
        self.assertIn('loading_pct', out['sections']['s2'])


class ServerTests(unittest.TestCase):
    def test_api_serves_files_network_and_rejects_foreign_paths(self):
        from nandu.ui.server import ThreadingHTTPServer, State, handler
        with tempfile.TemporaryDirectory() as tmp:
            path = project_file(tmp)
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler(State(tmp)))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_address[1]}'
            try:
                files = json.load(urlopen(base + '/api/files'))['files']
                self.assertEqual([Path(f).name for f in files], ['prueba.nandu.sqlite'])
                net = json.load(urlopen(base + '/api/network?file=' + str(path)))
                self.assertEqual(len(net['geometry']['sections']), 2)
                self.assertIn('Ñandu', urlopen(base + '/').read().decode('utf-8'))
                with self.assertRaises(Exception) as ctx:
                    urlopen(Request(base + '/api/network?file=' + str(Path(tmp).parent / 'otro.nandu.sqlite')))
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
