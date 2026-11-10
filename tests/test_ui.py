import importlib.util
import json
import math
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

    def test_map_projection_uses_southern_utm_and_requires_supported_crs(self):
        from nandu.ui.service import _utm_to_mercator
        x, y = _utm_to_mercator(500000, 10000000, 21, True)
        self.assertAlmostEqual(x, -57 * 6378137 * math.pi / 180, delta=0.01)
        self.assertAlmostEqual(y, 0, delta=0.01)
        for row in self.project.nodes.values():
            row['X'], row['Y'] = 500000 + row['X'], 7200000 + row['Y']
        geo = self.project.geometry()
        self.assertIsNotNone(geo['map_bounds'])
        self.assertTrue(all('mx' in n and 'my' in n for n in geo['nodes']))
        self.project.tables['CYMDBPARAMETERS'][0]['ValueStr'] = 'LOCAL:unknown'
        geo = self.project.geometry()
        self.assertIsNone(geo['map_bounds'])
        self.assertTrue(all('mx' not in n for n in geo['nodes']))

    def test_buses_connectors_and_tertiary_are_drawn_like_cyme(self):
        # CYMBUSDISPLAY (barra), CYMNODECONNECTOR (punto de salida del tramo) y CYMSECTIONNODE (terciario).
        for row in self.project.nodes.values():
            row['X'], row['Y'] = 500000 + row['X'], 7200000 + row['Y']
        t = self.project.tables
        t['CYMBUSDISPLAY'] = [{'NetworkId': 'N', 'NodeId': 'n1', 'X1': 500900.0, 'Y1': 7200500.0,
                               'X2': 501100.0, 'Y2': 7200500.0, 'Width': 200.0}]
        t['CYMNODECONNECTOR'] = [{'NetworkId': 'N', 'NodeId': 'n1', 'ConnectorIndex': i, 'X': 500950.0 + 50 * i,
                                  'Y': 7200500.0} for i in range(3)]
        t['CYMSECTION'][1]['FromNodeConnectorIndex'] = 2
        t['CYMSECTIONNODE'] = [{'SectionId': 's2', 'NodeId': 'n0', 'NodeConnectorIndex': 0}]
        t['CYMSECTIONDEVICE'].append({'NetworkId': 'N', 'SectionId': 's2', 'DeviceNumber': 'T3', 'DeviceType': 39,
                                      'Location': 0})
        self.project._index()
        geo = self.project.geometry()
        self.assertEqual([b['node'] for b in geo['buses']], ['n1'])
        self.assertTrue(all(k in geo['buses'][0] for k in ('mx1', 'my1', 'mx2', 'my2')))
        node = next(n for n in geo['nodes'] if n['id'] == 'n1')
        self.assertEqual(node['connectors']['2']['x'], 501050.0)
        self.assertIn('mx', node['connectors']['2'])
        sec = next(s for s in geo['sections'] if s['id'] == 's2')
        self.assertEqual((sec['from_connector'], sec['tertiary'], sec['tertiary_connector']), ('2', 'n0', '0'))
        self.assertIn('ThreeWindingTransformer', [d['type'] for d in sec['devices']])
        groups = self.project.explorer()[0]['groups']
        self.assertEqual([g['number'] for g in groups['ThreeWindingTransformer']], ['T3'])

    def test_source_drawing_wires_routes_labels_and_voltage_class(self):
        # ESQ_TRAZO: conductores por nodo (mapa y unifilar) y los que no están en el modelo; ESQ_TRAMO_PUNTO:
        # recorrido de una línea; tensión nominal propagada por tramos sin transformador; rótulos de estación.
        for row in self.project.nodes.values():
            row['X'], row['Y'] = 500000 + row['X'], 7200000 + row['Y']
        t = self.project.tables
        t['ESQ_NODO'] = [{'NodeId': 'n0', 'XPdf': 10.0, 'YPdf': -20.0, 'KV': '220', 'Estacion': 'E1', 'Ubicacion': 'estación'},
                         {'NodeId': 'n1', 'XPdf': 30.0, 'YPdf': -20.0, 'KV': None, 'Estacion': 'E1', 'Ubicacion': 'estación'},
                         {'NodeId': 'n2', 'XPdf': 60.0, 'YPdf': -20.0, 'KV': None}]
        t['ESQ_TRAZO'] = [
            {'Tipo': 'nodo', 'Elemento': 'n1', 'X1Pdf': 30.0, 'Y1Pdf': -20.0, 'X2Pdf': 30.0, 'Y2Pdf': -25.0, 'Barra': False,
             'X1': 501000.0, 'Y1': 7200500.0, 'X2': 501000.0, 'Y2': 7200490.0},
            {'Tipo': 'nodo', 'Elemento': 'n2', 'X1Pdf': 60.0, 'Y1Pdf': -20.0, 'X2Pdf': 62.0, 'Y2Pdf': -20.0, 'Barra': True},
            {'Tipo': 'sin modelo', 'Elemento': 'N9', 'X1Pdf': 0.0, 'Y1Pdf': 0.0, 'X2Pdf': 5.0, 'Y2Pdf': 0.0, 'Barra': False}]
        t['ESQ_TRAMO_PUNTO'] = [{'SectionId': 's2', 'PointIndex': 1, 'XPdf': 45.0, 'YPdf': -30.0},
                                {'SectionId': 's2', 'PointIndex': 0, 'XPdf': 30.0, 'YPdf': -30.0}]
        t['ESQ_ESTACION'] = [{'Estacion': 'E1', 'X': 500500.0, 'Y': 7200400.0}]
        t['ESQ_ROTULO'] = [{'Texto': 'E1', 'XPdf': 20.0, 'YPdf': -10.0}]
        geo = self.project.geometry()
        nodes = {n['id']: n for n in geo['nodes']}
        self.assertEqual(nodes['n1']['uw'], [[30.0, -20.0, 30.0, -25.0, 0]])
        self.assertEqual(nodes['n1']['gw'], [[501000.0, 7200500.0, 501000.0, 7200490.0, 0]])
        self.assertEqual(len(nodes['n1']['mw'][0]), 5)
        self.assertNotIn('gw', nodes['n2'])
        self.assertEqual(geo['unifilar']['extra'], [[0.0, 0.0, 5.0, 0.0, 0]])
        sec = {s['id']: s for s in geo['sections']}
        self.assertEqual(sec['s2']['upoints'], [{'x': 30.0, 'y': -30.0}, {'x': 45.0, 'y': -30.0}])
        self.assertTrue(sec['s1'].get('uguess'))  # línea sin recorrido en el dibujo
        self.assertEqual(geo['unifilar']['lines_without_route'], 1)
        # s1 tiene un regulador: no propaga; s2 (línea y carga) sí, de n1 a n2 no hay dato, así que nada se inventa.
        self.assertEqual((nodes['n0']['kv'], nodes['n1']['kv'], nodes['n2']['kv']), (220.0, None, None))
        self.assertEqual(geo['kv_levels'], [220.0])
        # Rótulo del mapa: centrado sobre el borde superior de los nodos de la estación.
        self.assertEqual([(l['t'], l['x'], l['y']) for l in geo['labels']], [('E1', 500500.0, 7200500.0)])
        self.assertIn('mx', geo['labels'][0])
        self.assertEqual(geo['unifilar']['labels'], [{'t': 'E1', 'x': 20.0, 'y': -10.0}])

    def test_schematic_view_uses_source_drawing_of_the_same_project(self):
        # Vista unifilar: ESQ_NODO / ESQ_EQUIPO del mismo .nandu.sqlite; un nodo sin lugar va entre sus vecinos.
        self.assertIsNone(self.project.geometry()['unifilar'])
        t = self.project.tables
        t['ESQ_NODO'] = [{'NodeId': 'n0', 'XPdf': 10.0, 'YPdf': -20.0},
                         {'NodeId': 'n1', 'XPdf': 30.0, 'YPdf': -20.0, 'BarraX1Pdf': 25.0, 'BarraY1Pdf': -20.0,
                          'BarraX2Pdf': 35.0, 'BarraY2Pdf': -20.0},
                         {'NodeId': 'n2', 'XPdf': None, 'YPdf': None}]
        t['ESQ_EQUIPO'] = [{'DeviceNumber': 's1', 'XPdf': 20.0, 'YPdf': -25.0}]
        geo = self.project.geometry()
        nodes = {n['id']: n for n in geo['nodes']}
        self.assertEqual((nodes['n0']['ux'], nodes['n0']['uy']), (10.0, -20.0))
        self.assertEqual((nodes['n2']['ux'], nodes['n2']['uy']), (30.0, -20.0))  # vecino n1
        self.assertEqual(geo['unifilar']['estimated_nodes'], 1)
        self.assertEqual(geo['unifilar']['buses'], [{'node': 'n1', 'x1': 25.0, 'y1': -20.0, 'x2': 35.0, 'y2': -20.0}])
        sec = {s['id']: s for s in geo['sections']}
        self.assertEqual(sec['s1']['upoints'], [{'x': 20.0, 'y': -25.0}])
        self.assertEqual(geo['unifilar']['bounds'], [10.0, -20.0, 30.0, -20.0])
        # La vista no toca la red: CYMNODE conserva sus coordenadas.
        self.assertEqual((nodes['n0']['x'], nodes['n0']['y']), (0.0, 500.0))

    def test_section_points_follow_point_index_and_project_to_map(self):
        # CYMINTERMEDIATEPOINT: forma del tramo de From a To; se ordena por PointIndex y se filtra por red.
        for row in self.project.nodes.values():
            row['X'], row['Y'] = 500000 + row['X'], 7200000 + row['Y']
        self.project.tables['CYMINTERMEDIATEPOINT'] = [
            {'SectionId': 's1', 'PointIndex': 1, 'NetworkId': 'N', 'X': 500600.0, 'Y': 7200100.0},
            {'SectionId': 's1', 'PointIndex': 0, 'NetworkId': 'N', 'X': 500300.0, 'Y': 7200050.0},
            {'SectionId': 's1', 'PointIndex': 0, 'NetworkId': 'OTRA', 'X': 1.0, 'Y': 1.0},
            {'SectionId': 's2', 'PointIndex': 0, 'NetworkId': 'N', 'X': None, 'Y': 7200000.0}]
        self.project._index()
        geo = self.project.geometry()
        points = {s['id']: s['points'] for s in geo['sections']}
        self.assertEqual([(q['x'], q['y']) for q in points['s1']], [(500300.0, 7200050.0), (500600.0, 7200100.0)])
        self.assertEqual(points['s2'], [])
        self.assertTrue(all('mx' in q and 'my' in q for q in points['s1']))

    def test_properties_decode_verified_codes_only(self):
        props = self.project.properties('section', 's1')
        reg = next(g for g in props['groups'] if g['name'] == 'Regulator R1')
        fields = {f[0]: f[1] for f in reg['fields']}
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
        self.assertIn('loading_pct_phase', out['sections']['s2'])  # vacío: el catálogo de prueba no tiene capacidad

    @unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
    def test_load_flow_reports_profile_regulators_and_capacitors(self):
        out = self.project.run('loadflow')
        model = self.project._model()
        total = sum(l['length_m'] for l in model['lines'])
        self.assertEqual(out['profile']['n0']['distance_m'], 0.0)
        self.assertAlmostEqual(out['profile']['n2']['distance_m'], total, places=1)
        self.assertEqual(out['profile']['n2']['parent'], 'n1')
        reg = model['regulators'][0]
        rows = [r for r in out['regulators'] if r['id'] == reg['id']]
        self.assertEqual([r['phase'] for r in rows], reg['phases'])
        self.assertEqual({r['phase']: r['tap'] for r in rows}, {p: reg['taps'][p] for p in reg['phases']})
        self.assertTrue(all(r['v_in_kv'] and r['v_out_kv'] and r['i_a'] is not None for r in rows))
        # Lado de entrada = el que orienta el motor; la potencia aparente pasa sin pérdidas (transformador ideal).
        s_in = sum(r['v_in_kv'] * r['i_a'] for r in rows)
        s_out = sum(r['v_out_kv'] * r['i_out_a'] for r in rows)
        self.assertGreater(s_in, 0)
        self.assertEqual(rows[0]['input_node'], reg['from'])  # en la red de prueba el tramo apunta aguas abajo
        self.assertAlmostEqual(s_in, s_out, delta=0.03 * s_out)
        self.assertEqual(out['capacitors'], [])

    def test_capacitor_delivered_kvar_and_cyme_loading(self):
        # Impedancia constante: a 1,05 Vn entrega 1,05² = 110,25 % de su Q nominal («Loading» de CYME).
        from nandu.ui.service import _capacitors
        model = {'buses': {'b': {'network_id': 'N'}},
                 'capacitors': [{'device_id': 'C1', 'section_id': 's', 'bus': 'b', 'connection': 'wye_grounded',
                                 'phases': ['A'], 'rated_q_var': 100e3, 'rated_v': 13200.0}]}
        res = {'voltages': {'b': {'A': {'v': complex(13860.0, 0.0), 'pu': 1.0}}}}
        row = _capacitors(model, res)[0]
        self.assertAlmostEqual(row['kvar'], 110.25, places=2)
        self.assertAlmostEqual(row['loading_pct'], 110.2, places=1)
        self.assertEqual((row['connection'], row['v_pct']), ('Yg', 105.0))

    def test_profile_does_not_cross_open_switches(self):
        # Un enlace normalmente abierto no debe acortar la distancia a la fuente del nodo del otro lado.
        from nandu.ui.service import _profile
        model = {'sources': [{'bus': 'a'}],
                 'sections': [{'id': 's1', 'from': 'a', 'to': 'b'}, {'id': 's2', 'from': 'b', 'to': 'c'},
                              {'id': 'tie', 'from': 'a', 'to': 'c'}],
                 # s2 está partida por una carga al medio: sus mitades conservan la longitud completa.
                 'lines': [{'id': 'l1', 'section_id': 's1', 'length_m': 100.0},
                           {'id': 'l2#1', 'section_id': 's2', 'length_m': 50.0},
                           {'id': 'l2#2', 'section_id': 's2', 'length_m': 50.0}],
                 'switches': [{'section_id': 'tie', 'closed': []}]}
        out = {'nodes': {n: {} for n in 'abc'}, 'sections': {'s1': {}, 's2': {}, 'tie': {}}}
        profile = _profile(model, out)
        self.assertEqual(profile['c'], {'distance_m': 150.0, 'parent': 'b', 'section': 's2'})

    def test_overload_is_reported_only_on_overloaded_phases(self):
        # Revisión técnica 0.9.0, H8: 120/50/50 A con 100 A de capacidad es sobrecarga solo en A.
        from nandu.ui.service import _abnormal
        out = {'nodes': {}, 'sections': {'s': {'current_a': {'A': 120.0, 'B': 50.0, 'C': 50.0}, 'loading_pct': 120.0,
                                               'loading_pct_phase': {'A': 120.0, 'B': 50.0, 'C': 50.0}}}}
        rows = {r['phase']: r['count'] for r in _abnormal(out)['rows'] if r['condition'] == 'Overload'}
        self.assertEqual(rows, {'A': 1, 'B': 0, 'C': 0})


class EditingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = project_file(self.tmp.name)
        from nandu.ui.service import Project
        self.project = Project(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def field(self, props, group, name):
        g = next(g for g in props['groups'] if g['name'].startswith(group))
        return next(f for f in g['fields'] if f[0] == name)

    def test_properties_carry_edit_targets_and_verified_options(self):
        props = self.project.properties('section', 's2')
        phase = self.field(props, 'Section', 'Phase')
        self.assertEqual((phase[1], phase[2]['raw'], phase[2]['key']), ('ABC', 7, {'SectionId': 's2'}))
        self.assertIn([4, 'AB'], [list(o) for o in phase[2]['options']])
        self.assertIsNone(self.field(props, 'Section', 'FromNodeId')[2])  # la conectividad tiene operaciones propias
        kw = self.field(props, 'Customer', 'kW')
        self.assertEqual(kw[2]['table'], 'CYMCUSTOMERLOAD')
        line = self.field(props, 'OverheadLine', 'LineId')
        self.assertEqual([o[0] for o in line[2]['options']], ['L1'])

    def test_set_rebuilds_model_invalidates_results_and_undoes(self):
        key = {'NetworkId': 'N', 'DeviceNumber': 'C1', 'CustomerNumber': 'C1'}
        self.project.last = {'stale': True}
        self.project.edit({'op': 'set', 'table': 'CYMCUSTOMERLOAD', 'key': key, 'changes': {'LoadValue1': '45,5'}})
        self.assertIsNone(self.project.last)
        self.assertEqual(self.project.tables['CYMCUSTOMERLOAD'][0]['LoadValue1'], 45.5)
        self.assertAlmostEqual(sum(l['p_w'] for l in self.project._model()['loads']) / 1000, 45.5, places=6)
        self.project.undo()
        self.assertEqual(self.project.tables['CYMCUSTOMERLOAD'][0]['LoadValue1'], 90.0)
        self.assertEqual(self.project.edit_state()['count'], 0)

    def test_invalid_values_leave_tables_untouched(self):
        from nandu.ui.editing import EditError
        before = [dict(r) for r in self.project.tables['CYMSECTION']]
        cases = [{'op': 'set', 'table': 'CYMSECTION', 'key': {'SectionId': 's2'}, 'changes': {'Phase': 9}},
                 {'op': 'set', 'table': 'CYMSECTION', 'key': {'SectionId': 's2'}, 'changes': {'ToNodeId': 'n0'}},
                 {'op': 'set', 'table': 'CYMOVERHEADLINE', 'key': {'NetworkId': 'N', 'DeviceNumber': 's2'},
                  'changes': {'LineId': 'NO_EXISTE'}},
                 {'op': 'batch', 'ops': [{'op': 'set', 'table': 'CYMSECTION', 'key': {'SectionId': 's2'}, 'changes': {'Phase': 4}},
                                         {'op': 'set', 'table': 'CYMSECTION', 'key': {'SectionId': 's2'}, 'changes': {'Phase': 'x'}}]}]
        for op in cases:
            with self.assertRaises(EditError):
                self.project.edit(op)
        self.assertEqual(self.project.tables['CYMSECTION'], before)
        self.assertEqual(self.project.edit_state()['count'], 0)

    def test_add_section_device_and_delete_section(self):
        entry = self.project.edit({'op': 'add_section', 'from': 'n2', 'x': 2000.0, 'y': 1000.0, 'phase': 1})
        self.assertEqual((entry['length_m'], entry['network']), (500.0, 'N'))
        sid, node = entry['section'], entry['to']
        self.project.edit({'op': 'add_device', 'section': sid, 'type': 20})
        self.project.edit({'op': 'add_device', 'section': sid, 'type': 13})
        model = self.project._model()
        self.assertFalse([i for i in model['issues'] if i.get('section_id') == sid])
        self.assertIn(node, model['buses'])
        props = self.project.properties('section', sid)
        self.assertEqual(self.field(props, 'Switch', 'ClosedPhase')[1], 'A')
        self.project.edit({'op': 'delete_section', 'section': sid})
        self.assertNotIn(node, self.project.nodes)
        self.assertFalse([r for r in self.project.tables['CYMCUSTOMERLOAD'] if r['DeviceNumber'].startswith('SPOTLOAD')])
        for _ in range(4):
            self.project.undo()
        self.assertEqual(len(self.project.tables['CYMSECTION']), 2)

    def test_conductor_cannot_be_deleted_alone(self):
        from nandu.ui.editing import EditError
        with self.assertRaises(EditError):
            self.project.edit({'op': 'delete_device', 'section': 's2', 'type': 2, 'device': 's2'})

    def test_map_coordinates_are_stored_in_the_project_crs(self):
        from nandu.ui.service import _utm_to_mercator
        mx, my = _utm_to_mercator(512345.0, 7200123.0, 21, True)
        self.project.edit({'op': 'move_node', 'node': 'n1', 'x': mx, 'y': my, 'map': True})
        row = self.project.nodes['n1']
        self.assertAlmostEqual(row['X'], 512345.0, places=3)
        self.assertAlmostEqual(row['Y'], 7200123.0, places=3)

    def test_save_as_writes_new_project_with_edit_journal(self):
        from nandu.storage import inspect
        from nandu.ui.service import Project
        with self.assertRaises(ValueError):
            self.project.save_as(Path(self.tmp.name) / 'sin-cambios.nandu.sqlite')
        self.project.edit({'op': 'set', 'table': 'CYMSECTION', 'key': {'SectionId': 's2'}, 'changes': {'Phase': 4}})
        out = Path(self.tmp.name) / 'editada.nandu.sqlite'
        self.project.save_as(out)
        info = inspect(out)
        self.assertEqual(info['provenance']['edits'][0]['after'], {'Phase': 4})
        self.assertEqual(Project(out).sections['s2']['Phase'], 4)
        self.assertEqual(Project(self.path).sections['s2']['Phase'], 7)  # el original no cambia
        with self.assertRaises(FileExistsError):
            self.project.save_as(out)


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
                with urlopen(base + '/') as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertIn('Ñandu', response.read().decode('utf-8'))
                with urlopen(base + '/app.js?v=background-selector-2') as response:
                    self.assertEqual(response.headers['Cache-Control'], 'no-store')
                    self.assertIn('satellite', response.read().decode('utf-8'))
                with self.assertRaises(Exception) as ctx:
                    urlopen(Request(base + '/api/network?file=' + str(Path(tmp).parent / 'otro.nandu.sqlite')))
                self.assertEqual(ctx.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
