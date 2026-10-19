import unittest

from nandu import cyme_model


def tables(load_location=0, connection=2, load_phase=1, section_phase=7):
    """Red mínima CYME: fuente, línea de secuencia, regulador y una carga."""
    line_eq = {'EquipmentId': 'L1', 'PositiveSequenceResistance': 0.3, 'PositiveSequenceReactance': 0.4,
               'ZeroSequenceResistance': 0.6, 'ZeroSequenceReactance': 1.3, 'PosSeqShuntConductance': 0.0,
               'PosSeqShuntSusceptance': 3.0, 'ZeroSequenceShuntConductance': 0.0,
               'ZeroSequenceShuntSusceptance': 1.5, 'Frequency': 50.0}
    return {
        'CYMNETWORK': [{'NetworkId': 'N'}],
        'CYMSOURCE': [{'NetworkId': 'N', 'DeviceNumber': 'S', 'NodeId': 'n0', 'EquipmentId': 'SRC',
                       'OperatingVoltageA': 13.5, 'OperatingVoltageB': 13.5, 'OperatingVoltageC': 13.5}],
        'CYMEQSOURCE': [{'EquipmentId': 'SRC', 'NominalKVLL': 23.0, 'FirstLevelR1': 0.1, 'FirstLevelX1': 1.0,
                         'FirstLevelR0': 0.1, 'FirstLevelX0': 1.0}],
        'CYMEQUIVALENTSOURCE': [{'NodeId': 'n0', 'KVLL': 23.0, 'OperatingAngle1': 0.0,
                                 'OperatingAngle2': -120.0, 'OperatingAngle3': 120.0}],
        'CYMSECTION': [
            {'NetworkId': 'N', 'SectionId': 's1', 'FromNodeId': 'n0', 'ToNodeId': 'n1', 'Phase': 7},
            {'NetworkId': 'N', 'SectionId': 's2', 'FromNodeId': 'n1', 'ToNodeId': 'n2', 'Phase': section_phase}],
        'CYMSECTIONDEVICE': [
            {'NetworkId': 'N', 'SectionId': 's1', 'DeviceNumber': 'R1', 'DeviceType': 4, 'Location': 1},
            {'NetworkId': 'N', 'SectionId': 's1', 'DeviceNumber': 's1', 'DeviceType': 2, 'Location': 0},
            {'NetworkId': 'N', 'SectionId': 's2', 'DeviceNumber': 's2', 'DeviceType': 2, 'Location': 0},
            {'NetworkId': 'N', 'SectionId': 's2', 'DeviceNumber': 'C1', 'DeviceType': 20, 'Location': load_location}],
        'CYMOVERHEADLINE': [
            {'NetworkId': 'N', 'DeviceNumber': 's1', 'LineId': 'L1', 'Length': 1000.0, 'Status': 0},
            {'NetworkId': 'N', 'DeviceNumber': 's2', 'LineId': 'L1', 'Length': 2000.0, 'Status': 0}],
        'CYMEQOVERHEADLINE': [line_eq],
        'CYMREGULATOR': [{'NetworkId': 'N', 'DeviceNumber': 'R1', 'EquipmentId': 'REG', 'ConnectionConfiguration': 4,
                          'SettingOption': 2, 'TapPositionA': 3.0, 'TapPositionB': 2.0, 'TapPositionC': 1.0,
                          'NormalFeedingNodeId': 'n0'}],
        'CYMEQREGULATOR': [{'EquipmentId': 'REG', 'MaximumBoost': 10.0, 'NumberOfTaps': 32}],
        'CYMLOAD': [{'NetworkId': 'N', 'DeviceNumber': 'C1', 'ConnectionConfiguration': connection}],
        'CYMCUSTOMERLOAD': [{'NetworkId': 'N', 'DeviceNumber': 'C1', 'CustomerNumber': 'C1', 'Status': 0,
                             'LoadValueType': 0, 'Phase': load_phase, 'LoadValue1': 90.0, 'LoadValue2': 30.0,
                             'ConsumerClassId': 'Res', 'LockDuringLoadAllocation': 0}],
        'CYMCONSUMERCLASS': [{'ConsumerClassId': 'Res', 'ConstantImpedance': 0.0, 'ConstantCurrent': 0.0,
                              'ConstantPower': 100.0, 'IsExponentialModel': 0, 'LoadFlowVoltPercentOfNominal': 80.0}],
    }


class ModelTests(unittest.TestCase):
    def test_middle_load_splits_the_line(self):
        model = cyme_model.build(tables(load_location=0))
        ids = [l['id'] for l in model['lines']]
        self.assertIn('s2#1', ids)
        self.assertIn('s2#2', ids)
        half = next(l for l in model['lines'] if l['id'] == 's2#1')
        self.assertEqual(half['to'], model['loads'][0]['bus'])
        # Mitad de 2 km con Zs=(Z0+2Z1)/3 por km.
        self.assertAlmostEqual(half['z'][0][0], (complex(0.6, 1.3) + 2 * complex(0.3, 0.4)) / 3, places=12)
        self.assertAlmostEqual(half['z'][0][1], (complex(0.6, 1.3) - complex(0.3, 0.4)) / 3, places=12)
        self.assertAlmostEqual(half['y'][0][0], 1j * (1.5 + 2 * 3.0) / 3 * 1e-6, places=15)

    def test_to_load_stays_on_section_end(self):
        model = cyme_model.build(tables(load_location=2))
        self.assertEqual(model['loads'][0]['bus'], 'n2')
        self.assertEqual([l['id'] for l in model['lines']], ['s1', 's2'])

    def test_regulator_at_from_creates_internal_bus_before_line(self):
        model = cyme_model.build(tables())
        reg = model['regulators'][0]
        self.assertEqual((reg['from'], reg['connection']), ('n0', 'closed_delta'))
        line = next(l for l in model['lines'] if l['id'] == 's1')
        self.assertEqual(line['from'], reg['to'])
        self.assertEqual(line['to'], 'n1')
        self.assertAlmostEqual(reg['step_pu'], 0.00625)

    def test_delta_single_phase_uses_next_phase(self):
        model = cyme_model.build(tables(connection=2, load_phase=2))
        self.assertEqual(model['loads'][0]['phases'], ['B', 'C'])

    def test_abc_record_splits_in_thirds(self):
        model = cyme_model.build(tables(connection=0, load_phase=7))
        self.assertEqual([l['phases'] for l in model['loads']], [['A'], ['B'], ['C']])
        self.assertAlmostEqual(sum(l['p_w'] for l in model['loads']), 90e3)
        self.assertAlmostEqual(model['loads'][0]['q_var'], 10e3)

    def test_load_outside_section_phases_is_reported(self):
        model = cyme_model.build(tables(connection=0, load_phase=7, section_phase=2))
        self.assertFalse(model['loads'])
        self.assertEqual(model['issues'][0]['code'], 'customer_load_excluded')

    def test_source_phasors_and_conventions(self):
        model = cyme_model.build(tables())
        src = model['sources'][0]
        self.assertAlmostEqual(abs(src['voltage_v']['B']), 13500, places=6)
        self.assertEqual(model['conventions']['regulator']['authority'], 'fitted_to_cyme_results')
        self.assertEqual(model['conventions']['abc_load_split']['authority'], 'verified')
        for entry in model['conventions'].values():
            self.assertTrue(entry['evidence'])


if __name__ == '__main__':
    unittest.main()
