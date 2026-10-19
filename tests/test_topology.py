import unittest

from nandu.topology import analyze, phases


def fixture():
    return {
        'CYMNODE': [{'NetworkId': 'N', 'NodeId': n} for n in ('s', 'm', 't')],
        'CYMSECTION': [{'NetworkId': 'N', 'SectionId': str(i), 'FromNodeId': a,
                        'ToNodeId': b, 'Phase': 7} for i, (a, b) in enumerate([('s', 'm'), ('m', 't')])],
        'CYMSECTIONDEVICE': [{'NetworkId': 'N', 'SectionId': str(i), 'DeviceNumber': str(i),
                              'DeviceType': 13} for i in range(2)],
        'CYMSWITCH': [{'NetworkId': 'N', 'DeviceNumber': str(i), 'ClosedPhase': 7,
                       'Status': 0, 'NormalStatus': 1} for i in range(2)],
        'CYMSOURCE': [{'NetworkId': 'N', 'NodeId': 's', 'DeviceNumber': 'source',
                       'OperatingVoltageA': 13.2, 'OperatingVoltageB': 13.2, 'OperatingVoltageC': 13.2}],
    }


class TopologyTests(unittest.TestCase):
    def test_cyme_phase_codes_are_not_bitmasks(self):
        self.assertEqual(phases(3), {'C'})
        self.assertEqual(phases(4), {'A', 'B'})
        with self.assertRaises(ValueError):
            phases(8)
        with self.assertRaises(ValueError):
            phases(True)

    def test_empty_input_cannot_claim_complete_coverage(self):
        report = analyze({})
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(len(report['issues']), 4)

    def test_nonfinite_voltage_does_not_create_a_source(self):
        data = fixture()
        data['CYMSOURCE'][0]['OperatingVoltageA'] = float('nan')
        report = analyze(data)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['summary']['phases']['A']['reached_nodes'], 0)

    def test_open_device_disconnects_even_when_normal_status_closed(self):
        data = fixture()
        data['CYMSWITCH'][0]['ClosedPhase'] = 0
        report = analyze(data)
        for phase in 'ABC':
            self.assertEqual(report['summary']['phases'][phase]['reached_nodes'], 1)
        self.assertEqual(report['summary']['fully_open_devices'], 1)

    def test_single_phase_switch_is_not_three_phase_connection(self):
        data = fixture()
        data['CYMSWITCH'][0]['ClosedPhase'] = 3
        report = analyze(data)['summary']['phases']
        self.assertEqual(report['C']['reached_nodes'], 3)
        self.assertEqual(report['A']['reached_nodes'], 1)

    def test_unknown_status_excludes_section_instead_of_assuming_closed(self):
        data = fixture()
        data['CYMSWITCH'][0]['Status'] = 99
        report = analyze(data)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['summary']['excluded_sections'], 1)
        self.assertEqual(report['summary']['phases']['A']['reached_nodes'], 1)

    def test_boundary_requires_membership_and_does_not_merge_by_name(self):
        data = fixture()
        data['CYMNODE'][1]['NetworkId'] = None
        self.assertEqual(analyze(data)['summary']['excluded_sections'], 2)
        data['CYMNODEINTER'] = [{'NetworkId': 'N', 'NodeId': 'm'}]
        report = analyze(data)
        self.assertEqual(report['summary']['excluded_sections'], 0)
        self.assertEqual(report['summary']['boundary_memberships_resolved'], 1)
        self.assertEqual(report['summary']['phases']['A']['reached_nodes'], 3)

    def test_parallel_sections_produce_cycle_rank(self):
        data = fixture()
        data['CYMSECTION'][1].update(FromNodeId='s', ToNodeId='m')
        self.assertEqual(analyze(data)['summary']['phases']['A']['cycle_rank'], 1)

    def test_two_sources_are_reported_in_same_component(self):
        data = fixture()
        data['CYMSOURCE'].append({**data['CYMSOURCE'][0], 'DeviceNumber': 'second', 'NodeId': 't'})
        self.assertEqual(analyze(data)['summary']['phases']['A']['multi_source_components'], 1)

    def test_series_devices_intersect_closed_phases(self):
        data = fixture()
        data['CYMSECTIONDEVICE'].append({'NetworkId': 'N', 'SectionId': '0', 'DeviceNumber': 'extra', 'DeviceType': 13})
        data['CYMSWITCH'].append({'NetworkId': 'N', 'DeviceNumber': 'extra', 'Status': 0, 'ClosedPhase': 2})
        data['CYMSWITCH'][0]['ClosedPhase'] = 1
        self.assertEqual(analyze(data)['summary']['phases']['A']['reached_nodes'], 1)
        self.assertEqual(analyze(data)['summary']['phases']['B']['reached_nodes'], 1)

    def test_unknown_device_and_nonzero_connector_are_not_silently_ignored(self):
        for change in ('device', 'connector'):
            data = fixture()
            if change == 'device':
                data['CYMSECTIONDEVICE'][0]['DeviceType'] = 999
            else:
                data['CYMSECTION'][0]['FromNodeConnectorIndex'] = 1
            self.assertEqual(analyze(data)['summary']['excluded_sections'], 1)


if __name__ == '__main__':
    unittest.main()
