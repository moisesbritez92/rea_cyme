"""Exportación .sxst por ida y vuelta: bytes intactos, cambios escritos y rechazos."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from nandu import sxst_import
from nandu.storage import inspect, load_source
from nandu.sxst_export import ExportError, export

ENGINE = all(importlib.util.find_spec(m) for m in ('numpy', 'scipy'))

LINE_EQ = ''.join(f'<{k}>{v}</{k}>' for k, v in (
    ('PositiveSequenceResistance', '0.300000'), ('PositiveSequenceReactance', '0.400000'),
    ('ZeroSequenceResistance', '0.600000'), ('ZeroSequenceReactance', '1.300000'),
    ('PositiveSequenceShuntSusceptance', '3.000000'), ('PositiveSequenceShuntConductance', '0.000000'),
    ('ZeroSequenceShuntSusceptance', '1.500000'), ('ZeroSequenceShuntConductance', '0.000000'),
    ('Frequency', '50.000000'), ('NominalRating', '300.000000'), ('FirstRating', '300.000000')))


def line(number, length, extra=''):
    return (f'<OverheadLine><DeviceNumber>{number}</DeviceNumber><DeviceStage/><Length>{length}</Length>'
            f'<ConnectionStatus>Connected</ConnectionStatus><LineID>L1</LineID>{extra}</OverheadLine>')


def load(number, value, phase='ABC', location='To'):
    return (f'<SpotLoad><DeviceNumber>{number}</DeviceNumber><ConnectionConfiguration>Yg</ConnectionConfiguration>'
            f'<CustomerLoads><CustomerLoad><CustomerNumber>{number}</CustomerNumber><CustomerType>Res</CustomerType>'
            '<ConnectionStatus>Connected</ConnectionStatus><LockDuringLoadAllocation>DefinedByLoadModel</LockDuringLoadAllocation>'
            '<CustomerLoadModels><CustomerLoadModel><LoadModelID>1</LoadModelID>'
            '<LockDuringLoadAllocation>Unlocked</LockDuringLoadAllocation><CustomerLoadValues>'
            f'<CustomerLoadValue><Phase>{phase}</Phase>{value}<ConnectedKVA>100.000000</ConnectedKVA>'
            '<KWH>0.000000</KWH><NumberOfCustomer>1.000000</NumberOfCustomer></CustomerLoadValue>'
            '</CustomerLoadValues></CustomerLoadModel>'
            '<CustomerLoadModel><LoadModelID>2</LoadModelID><CustomerLoadValues><CustomerLoadValue><Phase>ABC</Phase>'
            '<LoadValue xsi:type="LoadValueKW_KVAR" Type="LoadValueKW_KVAR"><KW>1.000000</KW><KVAR>0.000000</KVAR></LoadValue>'
            '</CustomerLoadValue></CustomerLoadValues></CustomerLoadModel></CustomerLoadModels></CustomerLoad></CustomerLoads>'
            f'<Location>{location}</Location></SpotLoad>')


KW_KVAR = '<LoadValue xsi:type="LoadValueKW_KVAR" Type="LoadValueKW_KVAR"><KW>90.000000</KW><KVAR>30.000000</KVAR></LoadValue>'
KVA_PF = '<LoadValue xsi:type="LoadValueKVA_PF" Type="LoadValueKVA_PF"><KVA>50.000000</KVA><PF>-90.000000</PF></LoadValue>'


def section(sid, start, end, devices, phase='ABC', owner='N'):
    return (f'<Section><SectionID>{sid}</SectionID><OwnerID>{owner}</OwnerID><FromNodeID>{start}</FromNodeID>'
            f'<FromNodeConnectorIndex>0</FromNodeConnectorIndex><ToNodeID>{end}</ToNodeID>'
            f'<ToNodeConnectorIndex>0</ToNodeConnectorIndex><Phase>{phase}</Phase><Devices>{devices}</Devices>'
            '<StructureID>DEFAULT</StructureID></Section>')


def node(ident, x, y):
    return (f'<Node><NodeID>{ident}</NodeID><ZoneID/><Connectors><Point><X>{x:.6f}</X><Y>{y:.6f}</Y></Point></Connectors>'
            '<OptimalPowerFlowNode><HighVoltageLimit>105.000000</HighVoltageLimit></OptimalPowerFlowNode></Node>')


def study():
    meter = ''.join(f'<DemandValue{p}><LoadValue xsi:type="LoadValueAMP_PF" Type="LoadValueAMP_PF"><AMP>{a}</AMP>'
                    f'<PF>95.000000</PF></LoadValue><Precision1>0.000000</Precision1></DemandValue{p}>'
                    for p, a in zip('ABC', ('2.500000', '2.600000', '2.700000')))
    regulator = ('<Regulator><DeviceNumber>R1</DeviceNumber><Location>From</Location>'
                 '<ConnectionConfiguration>ClosedDelta</ConnectionConfiguration><SettingOption>FixedTap</SettingOption>'
                 '<TapPositionA>3.000000</TapPositionA><TapPositionB>2.000000</TapPositionB><TapPositionC>1.000000</TapPositionC>'
                 '<LoadCenterID/><NormalFeedingNodeID>n0</NormalFeedingNodeID><ConnectionStatus>Connected</ConnectionStatus>'
                 '<ForwardSettings><SetVoltageA>120.000000</SetVoltageA></ForwardSettings><DeviceID>REG</DeviceID>'
                 '<CTPrimaryRating>100.000000</CTPrimaryRating><PTRatio>200.000000</PTRatio>'
                 '<BoostPercent>10.000000</BoostPercent><BuckPercent>10.000000</BuckPercent></Regulator>')
    switch = ('<Switch><DeviceNumber>SW1</DeviceNumber><DeviceID>SC</DeviceID><Location>From</Location>'
              '<ClosedPhase>ABC</ClosedPhase><ConnectionStatus>Connected</ConnectionStatus>'
              '<NormalFeedingNodeID>n1</NormalFeedingNodeID><NormalStatus>Closed</NormalStatus></Switch>')
    sections = [section('s1', 'n0', 'n1', regulator + line('s1', '1000.000000')),
                section('s2', 'n1', 'n2', line('s2', '2000.000000') + load('C1', KW_KVAR)),
                section('s3', 'n2', 'n3', switch + line('s3', '500.000000') + load('C2', KVA_PF, location='To')),
                section('x1', 'm0', 'm1', line('x1', '10.000000'), owner='OTRA')]
    nodes = [node(n, 1000.0 * i, 500.0) for i, n in enumerate(('n0', 'n1', 'n2', 'n3'))] + [node('m0', 0, 0), node('m1', 1, 1)]
    source = ('<Source><SourceNodeID>n0</SourceNodeID><SourceSettings><DeviceNumber>S</DeviceNumber><SourceID>SRC</SourceID>'
              '<OperatingVoltageA>13.500000</OperatingVoltageA><OperatingVoltageB>13.500000</OperatingVoltageB>'
              '<OperatingVoltageC>13.500000</OperatingVoltageC></SourceSettings>'
              '<EquivalentSourceModels><EquivalentSourceModel><LoadModelID>1</LoadModelID><EquivalentSource>'
              '<KVLL>23.000000</KVLL><OperatingAngle1>0.000000</OperatingAngle1><OperatingAngle2>-120.000000</OperatingAngle2>'
              '<OperatingAngle3>120.000000</OperatingAngle3><FirstLevelR1>0.100000</FirstLevelR1><FirstLevelX1>1.000000</FirstLevelX1>'
              '<FirstLevelR0>0.100000</FirstLevelR0><FirstLevelX0>1.000000</FirstLevelX0></EquivalentSource>'
              '</EquivalentSourceModel></EquivalentSourceModels>'
              f'<Meter><MeterLocation>Secondary</MeterLocation><MeterModels><MeterModel><LoadModelID>1</LoadModelID>{meter}'
              '</MeterModel></MeterModels></Meter></Source>')
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>', '<!--Copyright (c) 1986-2018, CYME International T&D-->',
        '<Cyme xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="9.0" Revision="4">',
        '<Version>9.0</Version>', '<Revision>4</Revision>', '<Networks>', '<Network>', '<Description/>',
        f'<Topos><Topo><NetworkID>N</NetworkID><NetworkType>Feeder</NetworkType><Sources>{source}</Sources>'
        '<Color>255</Color></Topo><Topo><NetworkID>OTRA</NetworkID><Color>0</Color></Topo></Topos>',
        '<Sections>', *sections, '</Sections>', '<Nodes>', *nodes, '</Nodes>', '</Network>', '</Networks>',
        '<Equipments><Equipments><EquipmentDBs>',
        '<SubstationDB><EquipmentID>SRC</EquipmentID><NominalKVLL>23.000000</NominalKVLL><FirstLevelR1>0.100000</FirstLevelR1>'
        '<FirstLevelX1>1.000000</FirstLevelX1><FirstLevelR0>0.100000</FirstLevelR0><FirstLevelX0>1.000000</FirstLevelX0></SubstationDB>',
        f'<OverheadLineDB><EquipmentID>L1</EquipmentID>{LINE_EQ}</OverheadLineDB>',
        '<RegulatorDB><EquipmentID>REG</EquipmentID><MaximumBoost>10.000000</MaximumBoost><NumberOfTaps>32</NumberOfTaps></RegulatorDB>',
        '<SwitchDB><EquipmentID>SC</EquipmentID><Amps>400.000000</Amps></SwitchDB>',
        '</EquipmentDBs></Equipments></Equipments>',
        '<ConsumerClasses><ConsumerClass><Name>Res</Name><ConstantPower>100.000000</ConstantPower>'
        '<ConstantCurrent>0.000000</ConstantCurrent><ConstantImpedance>0.000000</ConstantImpedance>'
        '<UtilizationFactor>100.000000</UtilizationFactor><PowerFactor>95.000000</PowerFactor>'
        '<IsExponentialModel>0</IsExponentialModel><LoadFlowVoltagePercentOfNominal>80.000000</LoadFlowVoltagePercentOfNominal>'
        '</ConsumerClass></ConsumerClasses>', '</Cyme>', '']
    return '\r\n'.join(parts).encode('utf-8')


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.study = self.dir / 'estudio.sxst'
        self.study.write_bytes(study())
        self.project = self.dir / 'estudio.nandu.sqlite'
        sxst_import.import_study(self.study, self.project, ['N'])
        self.provenance = inspect(self.project)['provenance']
        self.tables = load_source(self.project)[1]

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, name='salida.sxst', **kwargs):
        out = self.dir / name
        report = export(self.tables, self.provenance, out, **kwargs)
        return out, report

    def row(self, table, **match):
        found = [r for r in self.tables[table] if all(r.get(k) == v for k, v in match.items())]
        self.assertEqual(len(found), 1, (table, match))
        return found[0]

    def reread(self, path):
        return sxst_import.read(path, ['N'])[0]

    def test_unchanged_project_is_byte_identical(self):
        out, report = self.export()
        self.assertEqual(out.read_bytes(), self.study.read_bytes())
        self.assertEqual(report['changes'], [])
        self.assertEqual(report['base_study']['sha256'], hashlib.sha256(study()).hexdigest())

    def test_field_edits_are_written_in_place(self):
        self.row('CYMSECTION', SectionId='s2')['Phase'] = 4
        self.row('CYMNODE', NodeId='n3').update(X=3210.5, Y=-7.25)
        self.row('CYMOVERHEADLINE', DeviceNumber='s3')['Length'] = 750.0
        self.row('CYMSWITCH', DeviceNumber='SW1').update(NormalStatus=0, ClosedPhase=0)
        self.row('CYMREGULATOR', DeviceNumber='R1')['TapPositionB'] = -4.0
        self.row('CYMCUSTOMERLOAD', DeviceNumber='C1').update(LoadValue1=45.5, LockDuringLoadAllocation=1)
        self.row('CYMCUSTOMERLOAD', DeviceNumber='C2').update(LoadValue1=40.0, LoadValue2=-30.0)
        self.row('CYMNETWORKMETER', NetworkId='N').update(Val1B=3.5, Val2C=-90.0)
        self.row('CYMEQOVERHEADLINE', EquipmentId='L1')['PositiveSequenceResistance'] = 0.35
        self.row('CYMSECTIONDEVICE', SectionId='s3', DeviceNumber='C2')['Location'] = 0
        out, report = self.export()
        text = out.read_text(encoding='utf-8')
        for fragment in ('<Phase>AB</Phase>', '<X>3210.500000</X>', '<Length>750.000000</Length>',
                         '<NormalStatus>Open</NormalStatus>', '<ClosedPhase>None</ClosedPhase>',
                         '<TapPositionB>-4.000000</TapPositionB>', '<KW>45.500000</KW>',
                         '<LockDuringLoadAllocation>Locked</LockDuringLoadAllocation>', '<KVA>50.000000</KVA>',
                         '<PF>-80.000000</PF>', '<AMP>3.500000</AMP>', '<PositiveSequenceResistance>0.350000<',
                         '<Location>Middle</Location>'):
            self.assertIn(fragment, text)
        # Lo que no cambió queda como estaba: encabezado, CRLF, la red no importada y el modelo de carga 2.
        self.assertTrue(out.read_bytes().startswith(study()[:200]))
        self.assertNotIn(b'\n<', out.read_bytes().replace(b'\r\n', b''))
        self.assertIn(section('x1', 'm0', 'm1', line('x1', '10.000000'), owner='OTRA'), text)
        self.assertEqual(text.count('<KW>1.000000</KW>'), 2)
        self.assertGreaterEqual(report['change_count'], 10)
        reread = self.reread(out)
        self.assertAlmostEqual(next(r for r in reread['CYMCUSTOMERLOAD'] if r['DeviceNumber'] == 'C2')['LoadValue2'],
                               -30.0, places=5)

    def test_structural_edits_clone_templates(self):
        from nandu.ui.editing import Editor
        editor = Editor(self.tables)
        editor.apply({'op': 'delete_section', 'section': 's3'})
        entry = editor.apply({'op': 'add_section', 'from': 'n2', 'x': 2500.0, 'y': 900.0, 'phase': 1, 'section': 'NUEVO',
                              'node': 'nN'})
        editor.apply({'op': 'add_device', 'section': 'NUEVO', 'type': 20, 'number': 'CARGA'})
        editor.apply({'op': 'add_device', 'section': 'NUEVO', 'type': 13, 'number': 'SECC'})
        editor.apply({'op': 'add_device', 'section': 's2', 'type': 13, 'number': 'SECC2'})
        editor.apply({'op': 'set', 'table': 'CYMCUSTOMERLOAD',
                      'key': {'NetworkId': 'N', 'DeviceNumber': 'CARGA', 'CustomerNumber': 'CARGA'},
                      'changes': {'LoadValue1': 12.0, 'LoadValue2': 4.0}})
        self.assertEqual(entry['to'], 'nN')
        out, report = self.export()
        text = out.read_text(encoding='utf-8')
        self.assertNotIn('<SectionID>s3</SectionID>', text)
        self.assertNotIn('<NodeID>n3</NodeID>', text)  # nodo que quedó sin tramos
        self.assertIn('<NodeID>m1</NodeID>', text)  # nodos de la red no importada, intactos
        reread = self.reread(out)
        self.assertEqual({r['SectionId'] for r in reread['CYMSECTION']}, {'s1', 's2', 'NUEVO'})
        carga = [r for r in reread['CYMCUSTOMERLOAD'] if r['DeviceNumber'] == 'CARGA']
        self.assertEqual([(r['Phase'], r['LoadValue1'], r['LoadValue2']) for r in carga], [(1, 12.0, 4.0)])
        self.assertEqual(text.count('<LoadModelID>2</LoadModelID>'), 1)  # la carga nueva no copia el modelo 2
        self.assertTrue(any(c.get('added') for c in report['changes']))

    @unittest.skipUnless(ENGINE, 'requiere numpy y scipy')
    def test_exported_study_solves_like_the_project(self):
        from nandu import cyme_model, nodal
        self.row('CYMCUSTOMERLOAD', DeviceNumber='C1')['LoadValue1'] = 60.0
        out, _ = self.export()
        flows = [nodal.solve(cyme_model.build(t)) for t in (self.tables, self.reread(out))]
        self.assertEqual([f['status'] for f in flows], ['converged', 'converged'])
        self.assertAlmostEqual(abs(flows[0]['x'] - flows[1]['x']).max(), 0.0, places=6)

    def test_changes_without_xml_mapping_are_rejected(self):
        cases = [('CYMSOURCE', {'DeviceNumber': 'S'}, {'OperatingVoltageA': 14.0}),
                 ('CYMSWITCH', {'DeviceNumber': 'SW1'}, {'NormalStatus': 7}),
                 ('CYMREGULATOR', {'DeviceNumber': 'R1'}, {'TapPositionA': 1.5, 'Inventado': 1})]
        for table, match, changes in cases:
            with self.subTest(table=table):
                row = self.row(table, **match)
                before = dict(row)
                row.update(changes)
                with self.assertRaises(ExportError):
                    self.export(f'{table}.sxst')
                self.assertFalse((self.dir / f'{table}.sxst').exists())
                row.clear()
                row.update(before)

    def test_base_study_must_match_and_project_must_come_from_sxst(self):
        with self.assertRaises(ExportError):
            export(self.tables, {'source_path': 'x.mdb', 'source_sha256': '0'}, self.dir / 'a.sxst')
        moved = self.dir / 'otra' / 'copia.sxst'
        moved.parent.mkdir()
        moved.write_bytes(study())
        self.study.write_bytes(study() + b'\r\n')  # el original cambió
        with self.assertRaises(ExportError):
            self.export('b.sxst')
        out, report = self.export('c.sxst', study=moved)
        self.assertEqual(Path(report['base_study']['path']), moved.resolve())
        # Proyecto guardado con Save As: se sigue la cadena y se busca la copia por su SHA-256 en el nombre.
        (moved.parent / f'copia-{self.provenance["source_sha256"][:12]}.sxst').write_bytes(study())
        derived = {'derived_from': {'provenance': self.provenance}}
        report = export(self.tables, derived, self.dir / 'd.sxst', search=[moved.parent])
        self.assertTrue(report['base_study']['path'].endswith('.sxst'))
        with self.assertRaises(FileExistsError):
            self.export('c.sxst', study=moved)


class ServerTests(unittest.TestCase):
    """Botones Import/Export de la interfaz: subir, importar, editar, exportar y descargar por HTTP."""

    def setUp(self):
        import threading
        from nandu.ui.server import State, ThreadingHTTPServer, handler
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(self.tmp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}/api/'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def call(self, route, body=None, raw=None):
        import json
        from urllib.error import HTTPError
        from urllib.request import Request, urlopen
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = Request(self.base + route, data=data, method='POST' if data is not None else 'GET')
        try:
            with urlopen(request) as response:
                payload = response.read()
                return 200, (json.loads(payload) if response.headers['Content-Type'].startswith('application/json')
                             else payload)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_upload_import_edit_export_download(self):
        code, up = self.call('sxst-upload?name=Mi%20estudio.sxst', raw=study())
        self.assertEqual(code, 200)
        self.assertEqual(up['networks'], [{'id': 'N', 'sections': 3}, {'id': 'OTRA', 'sections': 1}])
        self.assertTrue(up['study'].startswith('Mi_estudio-'))
        self.assertEqual(self.call('sxst-upload?name=otra.sxst', raw=study())[1]['study'], up['study'])  # misma copia
        code, imported = self.call('sxst-import', {'study': up['study'], 'networks': ['N'], 'name': 'importado'})
        self.assertEqual((code, imported['networks']), (200, ['N']))
        project = imported['file']
        self.assertEqual(self.call('sxst-import', {'study': up['study'], 'networks': ['N'], 'name': 'importado'})[0], 400)
        info = self.call('sxst-export-info?file=' + project)[1]
        self.assertTrue(info['available'])
        key = {'NetworkId': 'N', 'DeviceNumber': 'C1', 'CustomerNumber': 'C1'}
        self.call('edit', {'file': project, 'edit': {'op': 'set', 'table': 'CYMCUSTOMERLOAD', 'key': key,
                                                     'changes': {'LoadValue1': '61,5'}}})
        code, report = self.call('sxst-export', {'file': project, 'name': 'salida'})
        self.assertEqual((code, report['change_count'], report['edit_journal_entries']), (200, 1, 1))
        code, data = self.call('download?name=salida.sxst')
        self.assertEqual(code, 200)
        self.assertIn(b'<KW>61.500000</KW>', data)
        self.assertEqual(len(data), len(study()) + 0)  # mismo largo: 90.000000 -> 61.500000
        self.assertTrue((Path(self.tmp.name) / 'exports' / 'salida.sxst.json').is_file())
        self.assertEqual(self.call('sxst-export', {'file': project, 'name': 'salida'})[0], 400)  # no reemplaza
        self.assertEqual(self.call('download?name=..%2Fimportado.nandu.sqlite')[0], 400)

    def test_rejections_carry_the_list_of_problems(self):
        code, error = self.call('sxst-upload?name=x.sxst', raw=b'<?xml version="1.0"?><Otra/>')
        self.assertEqual(code, 400)
        self.assertFalse(list((Path(self.tmp.name) / 'studies').glob('*.sxst')))
        up = self.call('sxst-upload?name=e.sxst', raw=study())[1]
        project = self.call('sxst-import', {'study': up['study'], 'networks': ['N'], 'name': 'p'})[1]['file']
        code, error = self.call('sxst-export', {'file': project, 'name': '../fuera'})
        self.assertEqual(code, 400)
        state_project = self.state.project(project)
        state_project.tables['CYMSOURCE'][0]['OperatingVoltageA'] = 14.0
        code, error = self.call('sxst-export', {'file': project, 'name': 'rechazada'})
        self.assertEqual(code, 400)
        self.assertTrue(any('CYMSOURCE' in p for p in error['problems']))
        self.assertFalse((Path(self.tmp.name) / 'exports' / 'rechazada.sxst').exists())


if __name__ == '__main__':
    unittest.main()
