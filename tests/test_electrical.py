from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from nandu.electrical import compact, load_profile, normalized_line, pq, prepare

PROFILE = {'id': 'test', 'authority': 'analyst-assumption', 'length_unit': 'm',
           'series_impedance_unit': 'ohm/km', 'shunt_admittance_unit': 'uS/km',
           'source_voltage_unit': 'kV_LN', 'load_value_types': {'0': 'kw_kvar'},
           'multiphase_load_value_scope': 'unverified'}


def equipment():
    return {'EquipmentId': 'cable', 'Frequency': 50, 'UserDefinedImpedances': 1,
            'PositiveSequenceResistance': 0.2, 'PositiveSequenceReactance': 0.4,
            'ZeroSequenceResistance': 0.6, 'ZeroSequenceReactance': 0.8,
            'PosSeqShuntConductance': 0, 'PosSeqShuntSusceptance': 10,
            'ZeroSequenceShuntConductance': 0, 'ZeroSequenceShuntSusceptance': 5}


def fixture():
    return {
        'CYMSECTION': [{'NetworkId': 'N', 'SectionId': 's', 'Phase': 7}],
        'CYMSECTIONDEVICE': [
            {'NetworkId': 'N', 'SectionId': 's', 'DeviceNumber': 'line', 'DeviceType': 1},
            {'NetworkId': 'N', 'SectionId': 's', 'DeviceNumber': 'load', 'DeviceType': 20}],
        'CYMUNDERGROUNDLINE': [{'NetworkId': 'N', 'DeviceNumber': 'line', 'CableId': 'cable', 'Length': 500}],
        'CYMEQCABLE': [equipment()],
        'CYMLOAD': [{'NetworkId': 'N', 'DeviceNumber': 'load', 'ConnectionConfiguration': 0}],
        'CYMCUSTOMERLOAD': [{'NetworkId': 'N', 'DeviceNumber': 'load', 'DeviceType': 20,
                             'CustomerNumber': 'c', 'Phase': 1, 'LoadValueType': 0,
                             'LoadValue1': 30, 'LoadValue2': 10, 'Status': 0,
                             'ConsumerClassId': 'residential', 'LoadModelId': 1}],
        'CYMCONSUMERCLASS': [{'ConsumerClassId': 'residential', 'ConstantPower': 100}],
        'CYMLOADMODEL': [{'LoadModelId': 1}],
        'CYMSOURCE': [{'NetworkId': 'N', 'DeviceNumber': 'src', 'EquipmentId': 'eqsrc',
                       'OperatingVoltageA': 13.2, 'OperatingVoltageB': 13.2, 'OperatingVoltageC': 13.2}],
        'CYMEQSOURCE': [{'EquipmentId': 'eqsrc'}],
    }


class ElectricalTests(unittest.TestCase):
    def test_sequence_scales_length_and_microsiemens(self):
        result = normalized_line(equipment(), 500, 'sequence', {'A', 'B', 'C'}, PROFILE)
        self.assertEqual(result['sequence_z_ohm']['positive'], [0.1, 0.2])
        self.assertAlmostEqual(result['sequence_y_siemens']['positive'][1], 5e-6)
        self.assertIsNone(result['phase_matrix'])

    def test_miles_and_feet_use_consistent_base(self):
        profile = {**PROFILE, 'length_unit': 'ft', 'series_impedance_unit': 'ohm/mi',
                   'shunt_admittance_unit': 'uS/mi'}
        result = normalized_line(equipment(), 5280, 'sequence', {'A'}, profile)
        self.assertAlmostEqual(result['length_m'], 1609.344)
        self.assertAlmostEqual(result['sequence_z_ohm']['positive'][0], 0.2)

    def test_single_phase_c_uses_c_parameters_not_ab(self):
        eq = {'Frequency': 50, 'SelfResistanceC': 2, 'SelfReactanceC': 3,
              'ShuntConductanceC': 0, 'ShuntSusceptanceC': 4}
        result = normalized_line(eq, 1000, 'phase', {'C'}, PROFILE)
        self.assertEqual(result['z_phase_ohm'], [[[2, 3]]])

    def test_nonfinite_or_nonpositive_line_values_fail(self):
        for length in (0, -1, float('nan'), True):
            with self.assertRaises(ValueError):
                normalized_line(equipment(), length, 'sequence', {'A'}, PROFILE)

    def test_kw_kvar_preserves_capacitive_sign(self):
        self.assertEqual(pq(10, -3, 'kw_kvar'), {'p_w': 10000, 'q_var': -3000})

    def test_power_factor_conversion(self):
        self.assertAlmostEqual(pq(80, 80, 'kw_pf_percent')['q_var'], 60000)
        self.assertEqual(pq(100, 80, 'kva_pf_percent')['p_w'], 80000)
        for pf in (0, -95, 101):
            with self.assertRaises(ValueError):
                pq(100, pf, 'kw_pf_percent')

    def test_no_profile_means_no_implicit_conversion(self):
        report = prepare(fixture())
        self.assertIsNone(report['lines'][0]['normalized_candidate'])
        self.assertIsNone(report['loads'][0]['pq_record_candidate'])
        self.assertFalse(report['solver_ready'])

    def test_catalog_and_class_references_resolved(self):
        data = fixture()
        before = deepcopy(data)
        report = prepare(data, PROFILE)
        self.assertEqual(report['summary']['issue_counts'], {})
        self.assertEqual(report['sources'][0]['voltage_ln_v_candidate']['A'], 13200)
        self.assertEqual(report['loads'][0]['consumer_class']['row']['ConstantPower'], 100)
        self.assertIsNone(report['loads'][0]['nodal_injection'])
        self.assertEqual(data, before)

    def test_missing_and_duplicate_equipment_are_not_silently_chosen(self):
        for rows, code in (([], 'missing_reference'), ([equipment(), equipment()], 'ambiguous_reference')):
            data = fixture()
            data['CYMEQCABLE'] = rows
            report = prepare(data, PROFILE)
            self.assertIn(code, report['summary']['issue_counts'])
            self.assertIsNone(report['lines'][0]['normalized_candidate'])

    def test_multiphase_power_is_not_divided_or_tripled(self):
        data = fixture()
        data['CYMCUSTOMERLOAD'][0]['Phase'] = 7
        report = prepare(data, PROFILE)
        load = report['loads'][0]
        self.assertEqual(load['pq_record_candidate']['p_w'], 30000)
        self.assertIsNone(load['power_by_phase_label_candidate'])
        self.assertIn('multiphase_load_scope_unverified', report['summary']['issue_counts'])

    def test_com_code_is_not_reused_for_access(self):
        data = fixture()
        data['CYMCUSTOMERLOAD'][0]['LoadValueType'] = 2
        report = prepare(data, PROFILE)
        self.assertIn('unmapped_load_value_type', report['summary']['issue_counts'])

    def test_derived_impedance_is_flagged_despite_numeric_values(self):
        data = fixture()
        data['CYMEQCABLE'][0]['UserDefinedImpedances'] = 0
        report = prepare(data, PROFILE)
        self.assertIn('derived_impedance_requires_recomputation_or_validation', report['summary']['issue_counts'])
        self.assertFalse(report['solver_ready'])

    def test_unknown_units_are_rejected(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'profile.json'
            path.write_text(json.dumps({**PROFILE, 'length_unit': 'yards'}), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_profile(path)

    def test_load_phase_mismatch_is_reported(self):
        data = fixture()
        data['CYMSECTION'][0]['Phase'] = 2
        report = prepare(data, PROFILE)
        self.assertEqual(report['summary']['issue_counts']['load_phases_not_in_section'], 1)

    def test_compaction_retains_resolvable_references_and_does_not_mutate(self):
        report = prepare(fixture(), PROFILE)
        original = deepcopy(report)
        result = compact(report)
        key = result['lines'][0]['equipment']['reference_id']
        self.assertEqual(result['references'][key]['row']['EquipmentId'], 'cable')
        self.assertEqual(report, original)

    def test_conflicting_reference_cannot_overwrite_another(self):
        report = {'summary': {}, 'items': [{'table': 'EQ', 'ordinal': 0, 'row': {'x': 1}},
                                          {'table': 'EQ', 'ordinal': 0, 'row': {'x': 2}}]}
        with self.assertRaises(ValueError):
            compact(report)


if __name__ == '__main__':
    unittest.main()
