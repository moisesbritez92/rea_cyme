import json
from pathlib import Path
import tempfile
import unittest

from nandu.storage import inspect, load_tables
from nandu.tables_import import import_tables, read
from nandu.topology import TABLES, analyze


def document(**changes):
    """Barra de 66 kV con fuente, interruptor, transformador a 23 kV y un interruptor abierto."""
    tables = {
        'CYMNETWORK': [{'NetworkId': 'SIN', 'NetworkType': 0}],
        'CYMNODE': [{'NodeId': n, 'NetworkId': 'SIN', 'X': i * 10.0, 'Y': 0.0}
                    for i, n in enumerate(('B66', 'J', 'B23', 'AL01', 'T', 'T23'))],
        'CYMSECTION': [{'SectionId': s, 'NetworkId': 'SIN', 'FromNodeId': a, 'FromNodeConnectorIndex': 0,
                        'ToNodeId': b, 'ToNodeConnectorIndex': 0, 'Phase': 7}
                       for s, a, b in (('I73', 'B66', 'J'), ('TR01', 'J', 'B23'), ('I1', 'B23', 'AL01'),
                                       ('TR3', 'B66', 'T'))],
        'CYMSECTIONDEVICE': [{'DeviceNumber': s, 'DeviceType': c, 'NetworkId': 'SIN', 'SectionId': s, 'Location': 1}
                             for s, c in (('I73', 8), ('TR01', 5), ('I1', 8))]
                            + [{'DeviceNumber': 'TR3', 'DeviceType': None, 'DeviceTypeText': 'ThreeWindingTransformer',
                                'NetworkId': 'SIN', 'SectionId': 'TR3', 'Location': 1}],
        'CYMBREAKER': [{'DeviceNumber': 'I73', 'NetworkId': 'SIN', 'ClosedPhase': 7, 'NormalStatus': 1, 'Status': 0},
                       {'DeviceNumber': 'I1', 'NetworkId': 'SIN', 'ClosedPhase': 0, 'NormalStatus': 0, 'Status': 0}],
        'CYMTRANSFORMER': [{'DeviceNumber': 'TR01', 'DeviceType': 5, 'NetworkId': 'SIN', 'EquipmentId': 'TR_66_23',
                            'Status': 0}],
        'CYMEQTRANSFORMER': [{'EquipmentId': 'TR_66_23', 'PrimaryVoltageKVLL': 66.0, 'SecondaryVoltageKVLL': 23.0,
                              'NominalRatingKVA': None}],
        'CYMSOURCE': [{'DeviceNumber': 'F', 'NodeId': 'B66', 'ConnectorIndex': 0, 'NetworkId': 'SIN',
                       **{f'OperatingVoltage{p}': 38.105 for p in 'ABC'}}],
        'ESQ_PROBLEMA': [],
    }
    doc = {'format': 'nandu-cyme-tables-v1', 'source': {'pdf': 'x.pdf'},
           'semantics': {'coordinates': 'pdf_points_y_up_not_geographic'},
           'conventions': [{'item': 'DeviceType 5', 'authority': 'verified', 'evidence': 'prueba'}], 'tables': tables}
    doc.update(changes)
    return doc


class TablesImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, doc):
        path = self.dir / 'tablas.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        return path

    def test_import_keeps_tables_provenance_and_declared_semantics(self):
        output = self.dir / 'sin.nandu.sqlite'
        result = import_tables(self.write(document()), output)
        self.assertEqual(result['networks'], ['SIN'])
        self.assertNotIn('ESQ_PROBLEMA', result['counts'])            # las tablas vacías no se guardan
        info = inspect(output)
        self.assertEqual(info['provenance']['source_format'], 'nandu-cyme-tables-v1')
        self.assertEqual(info['provenance']['conventions'][0]['authority'], 'verified')
        self.assertEqual(info['semantics']['coordinates'], 'pdf_points_y_up_not_geographic')
        self.assertEqual(info['source_row_counts']['CYMTRANSFORMER'], 1)
        # el transformador de tres devanados sin código Access queda como dispositivo no admitido
        self.assertEqual([i['code'] for i in info['validation']['issues']], ['unsupported_device'])
        with self.assertRaises(FileExistsError):
            import_tables(self.write(document()), output)

    def test_transformer_conducts_and_open_breaker_blocks(self):
        output = self.dir / 'sin.nandu.sqlite'
        import_tables(self.write(document()), output)
        report = analyze(load_tables(output, TABLES))
        reached = {n['node_id'] for n in report['nodes'] if n['phase'] == 'A' and n['reached_from_source']}
        self.assertEqual(reached, {'B66', 'J', 'B23'})
        excluded = {s['section_id']: s['reason'] for s in report['sections'] if s['state'] == 'excluded'}
        self.assertEqual(excluded, {'TR3': 'unsupported_device_type:None'})
        self.assertEqual(report['summary']['fully_open_devices'], 1)

    def test_transformer_with_other_status_is_excluded(self):
        doc = document()
        doc['tables']['CYMTRANSFORMER'][0]['Status'] = 1
        output = self.dir / 'sin.nandu.sqlite'
        import_tables(self.write(doc), output)
        report = analyze(load_tables(output, TABLES))
        reasons = {s['section_id']: s.get('reason') for s in report['sections']}
        self.assertTrue(reasons['TR01'].startswith('unverified_device_status:CYMTRANSFORMER'))

    def test_rejects_wrong_format_values_and_conventions(self):
        with self.assertRaisesRegex(ValueError, 'nandu-cyme-tables-v1'):
            read(self.write(document(format='otro')))
        doc = document()
        doc['tables']['CYMNODE'][0]['X'] = [1, 2]
        with self.assertRaisesRegex(ValueError, r'CYMNODE\[0\]\.X'):
            read(self.write(doc))
        with self.assertRaisesRegex(ValueError, 'autoridad'):
            read(self.write(document(conventions=[{'item': 'x', 'authority': 'creo'}])))
        doc = document()
        doc['tables']['CYMNETWORK'] = []
        with self.assertRaisesRegex(ValueError, 'CYMNETWORK'):
            read(self.write(doc))

    def test_rejects_non_finite_numbers(self):
        path = self.dir / 'tablas.json'
        path.write_text(json.dumps(document()).replace('38.105', 'NaN', 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'OperatingVoltage'):
            read(path)


if __name__ == '__main__':
    unittest.main()
