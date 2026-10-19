from decimal import Decimal
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nandu.cyme_import import portable
from nandu.model import build_model, validate
from nandu.storage import inspect, save


def fixture():
    return {
        'CYMNETWORK': [{'NetworkId': 'A'}, {'NetworkId': 'B'}],
        'CYMNODE': [{'NetworkId': n, 'NodeId': i, 'X': 0, 'Y': 0}
                    for n in ('A', 'B') for i in ('1', '2')],
        'CYMSECTION': [{'NetworkId': n, 'SectionId': 's', 'FromNodeId': '1',
                        'ToNodeId': '2', 'Phase': 7} for n in ('A', 'B')],
        'CYMSECTIONDEVICE': [{'NetworkId': 'A', 'SectionId': 's', 'DeviceNumber': 'd',
                              'DeviceType': 20, 'Location': 0}],
        'CYMCUSTOMERLOAD': [{'NetworkId': 'A', 'DeviceNumber': 'd', 'DeviceType': 20}],
        'CYMSOURCE': [{'NetworkId': 'A', 'NodeId': '1', 'DeviceNumber': 'source'}],
    }


class ImportTests(unittest.TestCase):
    def test_identifiers_are_scoped_by_network(self):
        self.assertEqual(validate(build_model(fixture()))['issues'], [])

    def test_missing_node_in_one_network_is_not_resolved_in_another(self):
        data = fixture()
        data['CYMNODE'] = [r for r in data['CYMNODE'] if (r['NetworkId'], r['NodeId']) != ('A', '2')]
        issues = validate(build_model(data))['issues']
        self.assertEqual([r['code'] for r in issues], ['boundary_endpoint_requires_mapping'])
        self.assertEqual(issues[0]['candidate_network_ids'], ['B'])

    def test_unassigned_boundary_node_is_preserved_without_inventing_ownership(self):
        data = fixture()
        data['CYMNODE'].append({'NetworkId': None, 'NodeId': 'boundary', 'X': 1, 'Y': 2})
        data['CYMSECTION'][0]['ToNodeId'] = 'boundary'
        report = validate(build_model(data))
        self.assertEqual(report['error_count'], 0)
        self.assertEqual(report['status'], 'review_required')
        self.assertFalse(report['solver_ready'])

    def test_truly_missing_endpoint_is_an_error(self):
        data = fixture()
        data['CYMSECTION'][0]['ToNodeId'] = 'absent'
        report = validate(build_model(data))
        self.assertEqual(report['error_count'], 1)
        self.assertEqual(report['issues'][0]['code'], 'missing_endpoint')

    def test_duplicates_are_reported_and_preserved(self):
        data = fixture()
        data['CYMSECTIONDEVICE'] *= 2
        model = build_model(data)
        self.assertEqual(len(model['attachments']), 2)
        self.assertIn('ambiguous_attachment', [r['code'] for r in validate(model)['issues']])

    def test_load_attachment_requires_device_type(self):
        data = fixture()
        data['CYMCUSTOMERLOAD'][0]['DeviceType'] = 99
        self.assertIn('unattached_load', [r['code'] for r in validate(build_model(data))['issues']])

    def test_decimal_precision_and_binary_are_preserved(self):
        value = Decimal('12345678901234567890.001')
        self.assertEqual(Decimal(portable(value)['$decimal']), value)
        self.assertEqual(portable(b'\x00\xff'), {'$bytes_base64': 'AP8='})

    def test_storage_roundtrip_unicode_and_no_overwrite(self):
        data = fixture()
        data['CYMNETWORK'][0]['Description'] = 'Ñandu — distribución'
        model = build_model(data)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'ñandu.sqlite'
            save(output, {'source': 'fixture'}, {k: [] for k in data}, data, model, validate(model))
            report = inspect(output)
            self.assertEqual(report['counts']['nodes'], 4)
            self.assertFalse(report['validation']['solver_ready'])
            with closing(sqlite3.connect(output)) as db:
                raw = db.execute("SELECT payload_json FROM source_records WHERE table_name='CYMNETWORK' AND ordinal=0").fetchone()[0]
                self.assertIn('Ñandu', raw)
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                save(output, {}, {}, {}, model, {})
            self.assertEqual(before, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
