from pathlib import Path
import tempfile
import unittest

from nandu import ascii_export
from nandu.ascii_export import EQ_FORMATS, NET_FORMATS, NETWORK_FORMAT, ExportError, export, read

# Líneas FORMAT_* tal como las escribe CYME 9.0 r4 (Database > Export > CYME ASCII, 17 nov. 2026):
# BD_Centro0926.mdb para red; bd_nuevo-1.mdb para tres devanados, SUBSTATION y equipos.
REFERENCE = '''
FORMAT_NODE=NodeID,CoordX,CoordY,TagText,TagProperties,TagDeltaX,TagDeltaY,TagAngle,TagAlignment,TagBorder,TagBackground,TagTextColor,TagBorderColor,TagBackgroundColor,TagLocation,TagFont,TagTextSize,TagOffset,ZoneID,ExposedCircuitType,BusGap,WorkingDistance,UseUserDefinedFaultCurrent,UserDefinedFaultCurrent,ActivatePPECategoryMethod,PPECategory,Boundary,OpeningTimeMode,UserDefinedOpeningTime,EnclosureWidth,EnclosureHeight,EnclosureDepth,ElectrodeConfiguration,EnclosureType,CoefficientA,CoefficientK,UserDefinedTimeConstant,TimeConstant,OverrideLFVoltageLimit,HighVoltageLimit,LowVoltageLimit,LoadSheddingActive,MaximumLoadShed,ShedLoadCost,UserDefinedBaseVoltage,Installation,RatedVoltage,RatedCurrent,ANSISymCurrent,ANSIAsymCurrent,PeakCurrent,Standard,TestCircuitPowerFactor
FORMAT_HEADNODES=NodeID,NetworkID,ConnectorIndex,StructureID,HarmonicEnveloppe,EquivalentSourceConfiguration,EquivalentSourceSinglePhaseCT,EquivSourceCenterTapPhase,BackgroundHarmonicVoltage,NominalVoltage,PhaseAngle,Configuration,Phase,CenterTapPhase
FORMAT_SOURCE=SourceID,DeviceNumber,NodeID,NetworkID,OperatingVoltageA,OperatingVoltageB,OperatingVoltageC,UseSecondLevelImpedance,SinglePhaseCenterTap,CenterTapPhase
FORMAT_SECTION=SectionID,FromNodeID,FromNodeIndex,ToNodeID,ToNodeIndex,Phase,ZoneID,SubNetworkId
FORMAT_FEEDER=NetworkID,HeadNodeID,CoordSet,Year,Description,Color,LoadFactor,LossLoadFactorK,Group1,Group2,Group3,North,South,East,West,AreaFilter,TagText,TagProperties,TagDeltaX,TagDeltaY,TagAngle,TagAlignment,TagBorder,TagBackground,TagTextColor,TagBorderColor,TagBackgroundColor,TagLocation,TagFont,TagTextSize,TagOffset,Version
FORMAT_SUBSTATION=NetworkID,HeadNodeID,CoordSet,Year,Description,Color,LoadFactor,LossLoadFactorK,Group1,Group2,Group3,North,South,East,West,AreaFilter,TagText,TagProperties,TagDeltaX,TagDeltaY,TagAngle,TagAlignment,TagBorder,TagBackground,TagTextColor,TagBorderColor,TagBackgroundColor,TagLocation,TagFont,TagTextSize,TagOffset,Version
FORMAT_OVERHEADLINESETTING=SectionID,DeviceNumber,DeviceStage,Flags,InitFromEquipFlags,LineCableID,Length,ConnectionStatus,NominalRating,FirstRating,SecondRating,ThirdRating,FourthRating,AmpacityDeratingFactor,CoordX,CoordY,HarmonicModel,TCCRepositoryID,FlowConstraintActive,FlowConstraintUnit,MaximumFlow,SeriesCompensationActive,MaxReactanceMultiplier,SeriesCompensationCost
FORMAT_BREAKERSETTING=SectionID,Location,EqID,DeviceNumber,DeviceStage,Flags,InitFromEquipFlags,CoordX,CoordY,ClosedPhase,Locked,RC,NStatus,TCCID,PhPickup,GrdPickup,Alternate,PhAltPickup,GrdAltPickup,FromNodeID,EnableReclosing,FaultIndicator,EnableFuseSaving,MinRatedCurrentForFuseSaving,Automated,SensorMode,Strategic,RestorationMode,ConnectionStatus,ByPassOnRestoration,Speed,SeqOpFirstPhase,SeqOpFirstGround,SeqOpLockoutPhase,SeqOpLockoutGround,SeqResetTime,SeqReclosingTime1,SeqReclosingTime2,SeqReclosingTime3,Reversible
FORMAT_TRANSFORMERSETTING=SectionID,Location,EqID,DeviceNumber,DeviceStage,Flags,InitFromEquipFlags,CoordX,CoordY,Conn,PrimTap,SecondaryTap,RgPrim,XgPrim,RgSec,XgSec,ODPrimPh,PrimaryBaseVoltage,SecondaryBaseVoltage,FromNodeID,SettingOption,SetPoint,ControlType,LowerBandWidth,UpperBandWidth,TapLocation,InitialTapPosition,InitialTapPositionMode,Tap,MaxBuck,MaxBoost,CT,PT,Rset,Xset,EnableFHP,FirstHouseHigh,FirstHouseLow,FirstHouseDeadBand,PhaseON,AtSectionID,MasterID,FaultIndicator,PhaseShiftType,GammaPhaseShift,CTPhase,PrimaryCornerGroundedPhase,SecondaryCornerGroundedPhase,ConnectionStatus,TCCRepositoryID,Reversible
FORMAT_THREEWINDINGTRANSFORMERSETTING=SectionID,Location,EqID,DeviceNumber,DeviceStage,Flags,InitFromEquipFlags,CoordX,CoordY,TertiaryNodeID,TertiaryNodeIndex,PrimaryFixedTapSetting,SecondaryFixedTapSetting,TertiaryFixedTapSetting,PrimaryBaseVoltage,SecondaryBaseVoltage,TertiaryBaseVoltage,LTC1_RSetting,LTC1_XSetting,LTC1_CTPrimaryRating,LTC1_PTRatio,LTC1_ControlStatusPhase,LTC1_TapSetting,LTC1_MasterID,LTC1_LoadCenterID,LTC1_SetPoint,LTC1_ControlType,LTC1_UpperBandwidth,LTC1_LowerBandwidth,LTC1_Boost,LTC1_Buck,LTC1_EnableFHP,LTC1_FirstHouseHigh,LTC1_FirstHouseLow,LTC1_FirstHouseDeadBand,LTC1_TapLocation,LTC1_SettingOption,LTC1_InitialTapPosition,LTC1_InitialTapPositionMode,LTC2_RSetting,LTC2_XSetting,LTC2_CTPrimaryRating,LTC2_PTRatio,LTC2_ControlStatusPhase,LTC2_TapSetting,LTC2_MasterID,LTC2_LoadCenterID,LTC2_SetPoint,LTC2_ControlType,LTC2_UpperBandwidth,LTC2_LowerBandwidth,LTC2_Boost,LTC2_Buck,LTC2_EnableFHP,LTC2_FirstHouseHigh,LTC2_FirstHouseLow,LTC2_FirstHouseDeadBand,LTC2_TapLocation,LTC2_SettingOption,LTC2_InitialTapPosition,LTC2_InitialTapPositionMode,ConnectionStatus
FORMAT_INTERMEDIATENODE=SectionID,SeqNumber,CoordX,CoordY,IsBreakPoint,BreakPointLocation
FORMAT_BREAKER=ID,Amps,Amps_1,Amps_2,Amps_3,Amps_4,KVLL,Reversible,InterruptingRating,FailRate,TmpFailRate,MajorRepairTime,MinorRepairTime,MajorFailureProportion,StuckProbability,SwitchTime,SymbolOpenID,SymbolCloseID,SinglePhaseLocking,SinglePhaseTripping,RemoteControlled,Automated,Standard,Manufacturer,Model,ANSIMaxRatedVoltage,ANSIRatedRangeKFactor,ANSIMaxSymetricalRMS,ANSIClosingLatchingRMS,ANSIClosingLatchingCrest,IECMakingCurrent,InterruptingTime,Favorite,ModifiedByUser,Flags,Comments
FORMAT_TRANSFORMER=ID,Type,WindingType,KVA,VoltageUnit,KVLLprim,KVLLsec,Z1,Z0,Z0PrimSec,Z0PrimMag,Z0SecMag,XR,XR0,XR0PrimSec,XR0PrimMag,XR0SecMag,MagnetizingCurrent,Conn,Rg_prim,Xg_prim,Rg_sec,Xg_sec,IsLTC,Taps,LowerBandwidth,UpperBandwidth,MinReg_Range,MaxReg_Range,Reversible,SelfCooledKVA,SelfCooledKVA_2,SelfCooledKVA_3,SelfCooledKVA_4,NoLoadLosses,FailRate,TmpFailRate,MajorRepairTime,MinorRepairTime,MajorFailureProportion,SymbolID,PhaseShiftType,InsulationType,Favorite,ModifiedByUser,Flags,Comments
FORMAT_THREEWINDINGTRANSFORMER=ID,PrimaryRatedCapacity,PrimaryCapacityLimit1,PrimaryCapacityLimit2,PrimaryCapacityLimit3,PrimaryCapacityLimit4,PrimaryVoltage,PrimaryConnection,PrimaryRg,PrimaryXg,PrimaryToSecondaryZ1,PrimaryToSecondaryZ0,PrimaryToSecondaryXR1,PrimaryToSecondaryXR0,PrimaryToSecondaryPhaseShiftType,PrimaryToTertiaryZ1,PrimaryToTertiaryZ0,PrimaryToTertiaryXR1,PrimaryToTertiaryXR0,PrimaryToTertiaryPhaseShiftType,SecondaryToTertiaryZ1,SecondaryToTertiaryZ0,SecondaryToTertiaryXR1,SecondaryToTertiaryXR0,SecondaryRatedCapacity,SecondaryCapacityLimit1,SecondaryCapacityLimit2,SecondaryCapacityLimit3,SecondaryCapacityLimit4,SecondaryVoltage,SecondaryConnection,SecondaryRg,SecondaryXg,TertiaryRatedCapacity,TertiaryCapacityLimit1,TertiaryCapacityLimit2,TertiaryCapacityLimit3,TertiaryCapacityLimit4,TertiaryVoltage,TertiaryConnection,TertiaryRg,TertiaryXg,MagnetizingCurrent,LTC1_NumberOfTaps,LTC1_UpperBandwidth,LTC1_LowerBandwidth,LTC1_MaximumRegulationRange,LTC1_MinimumRegulationRange,LTC2_NumberOfTaps,LTC2_UpperBandwidth,LTC2_LowerBandwidth,LTC2_MaximumRegulationRange,LTC2_MinimumRegulationRange,NoLoadLosses,FailRate,TmpFailRate,MajorRepairTime,MinorRepairTime,MajorFailureProportion,SymbolID,Favorite,ModifiedByUser,Flags,Comments
'''
REF = {line.split('=', 1)[0][7:]: line.split('=', 1)[1].split(',') for line in REFERENCE.split() if line}


def tables(**changes):
    """Barra de 66 kV con dos fuentes, línea, interruptor abierto, transformador y tres devanados."""
    t = {
        'CYMNETWORK': [{'NetworkId': 'SIN', 'NetworkType': 0}],
        'CYMNODE': [{'NodeId': n, 'NetworkId': 'SIN', 'X': 600000.0 + i, 'Y': 7100000.5}
                    for i, n in enumerate(('B66', 'J', 'B23', 'AL01', 'T', 'T23', 'X'))],
        'CYMSECTION': [{'SectionId': s, 'NetworkId': 'SIN', 'FromNodeId': a, 'FromNodeConnectorIndex': 0,
                        'ToNodeId': b, 'ToNodeConnectorIndex': 0, 'Phase': 7}
                       for s, a, b in (('L1', 'X', 'B66'), ('I73', 'B66', 'J'), ('TR01', 'J', 'B23'),
                                       ('I1', 'B23', 'AL01'), ('TR3', 'B66', 'T'))],
        'CYMSECTIONDEVICE': [{'DeviceNumber': s, 'DeviceType': c, 'NetworkId': 'SIN', 'SectionId': s,
                              'Location': loc}
                             for s, c, loc in (('L1', 2, 0), ('I73', 8, 1), ('TR01', 5, 1), ('I1', 8, 2),
                                               ('TR3', 39, 1))],
        'CYMOVERHEADLINE': [{'DeviceNumber': 'L1', 'NetworkId': 'SIN', 'LineId': None, 'Length': 1500.25,
                             'Status': 0}],
        'CYMBREAKER': [{'DeviceNumber': 'I73', 'NetworkId': 'SIN', 'EquipmentId': 'INT_66', 'ClosedPhase': 7,
                        'NormalStatus': 1, 'Status': 0},
                       {'DeviceNumber': 'I1', 'NetworkId': 'SIN', 'EquipmentId': 'INT_66', 'ClosedPhase': 0,
                        'NormalStatus': 0, 'Status': 0, 'NormalFeedingNodeId': 'B23'}],
        'CYMTRANSFORMER': [{'DeviceNumber': 'TR01', 'DeviceType': 5, 'NetworkId': 'SIN', 'EquipmentId': 'TR_66_23',
                            'Status': 0}],
        'CYMTHREEWINDINGTRANSFORMER': [{'DeviceNumber': 'TR3', 'DeviceType': 39, 'NetworkId': 'SIN',
                                        'EquipmentId': 'TR3_220_66_23', 'Status': 0}],
        'CYMSECTIONNODE': [{'SectionId': 'TR3', 'NodeId': 'T23', 'NodeConnectorIndex': 0}],
        'CYMINTERMEDIATEPOINT': [{'NetworkId': 'SIN', 'SectionId': 'L1', 'PointIndex': i, 'X': 600000.5 + i,
                                  'Y': 7100001.0} for i in (1, 0)],
        'CYMEQBREAKER': [{'EquipmentId': 'INT_66', 'RatedVoltage': 66}],
        'CYMEQTRANSFORMER': [{'EquipmentId': 'TR_66_23', 'PrimaryVoltageKVLL': 66.0, 'SecondaryVoltageKVLL': 23.0,
                              'NominalRatingKVA': None}],
        'CYMEQTHREEWINDINGTRANSFORMER': [{'EquipmentId': 'TR3_220_66_23', 'PrimaryRatedCapacity': None,
                                          'PrimaryVoltage': 220, 'SecondaryVoltage': 66, 'TertiaryVoltage': 23,
                                          'Comments': '30 MVA'}],
        'CYMSOURCE': [{'DeviceNumber': f'F{i}', 'NodeId': n, 'ConnectorIndex': 0, 'NetworkId': 'SIN',
                       'EquipmentId': None, **{f'OperatingVoltage{p}': 38.105118 for p in 'ABC'}}
                      for i, n in enumerate(('B66', 'X'))],
        'CYMBUSDISPLAY': [{'NetworkId': 'SIN', 'NodeId': 'B66', 'X1': 0, 'X2': 1, 'Y1': 0, 'Y2': 0, 'Width': 1}],
        'CYMDBPARAMETERS': [{'ID': 'CoordinateSystem', 'ValueStr': 'EPSG:32721'}],
        'ESQ_NODO': [{'NodeId': 'B66'}],
    }
    t.update(changes)
    return t


class AsciiExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.prefix = Path(self.dir.name) / 'sin'

    def tearDown(self):
        self.dir.cleanup()

    def test_field_names_come_from_cyme_exports(self):
        for keyword, fields in [*NET_FORMATS.values(), *EQ_FORMATS.values(), ('FEEDER', NETWORK_FORMAT),
                                ('SUBSTATION', NETWORK_FORMAT)]:
            with self.subTest(keyword=keyword):
                self.assertTrue(set(fields) <= set(REF[keyword]), set(fields) - set(REF[keyword]))
                # Mismo orden relativo que CYME.
                self.assertEqual([f for f in REF[keyword] if f in fields], list(fields))

    def test_exports_network_and_equipment(self):
        report = export(tables(), self.prefix, source='prueba')
        net = read(report['network_file'])
        eq = read(report['equipment_file'])
        self.assertEqual(net['__networks__'], [{'__kind__': 'FEEDER', 'NetworkID': 'SIN', 'HeadNodeID': '',
                                                'CoordSet': '1'}])
        self.assertEqual([s['Phase'] for s in net['SECTION']], ['ABC'] * 5)
        self.assertEqual(net['HEADNODES'], [{'NodeID': 'B66', 'NetworkID': 'SIN', 'ConnectorIndex': '0'},
                                            {'NodeID': 'X', 'NetworkID': 'SIN', 'ConnectorIndex': '0'}])
        self.assertEqual(net['SOURCE'][0]['SourceID'], '')
        self.assertEqual(net['SOURCE'][0]['OperatingVoltageA'], '38.105118')
        breakers = {b['DeviceNumber']: b for b in net['BREAKER SETTING']}
        self.assertEqual((breakers['I73']['Location'], breakers['I73']['ClosedPhase'], breakers['I73']['NStatus']),
                         ('S', 'ABC', '0'))
        self.assertEqual((breakers['I1']['Location'], breakers['I1']['ClosedPhase'], breakers['I1']['NStatus'],
                          breakers['I1']['FromNodeID']), ('L', 'NONE', '1', 'B23'))
        self.assertEqual(net['TRANSFORMER SETTING'][0]['Location'], 'S')
        three = net['THREE WINDING TRANSFORMER SETTING'][0]
        self.assertEqual((three['Location'], three['TertiaryNodeID'], three['TertiaryNodeIndex']), ('1', 'T23', '0'))
        self.assertEqual(net['OVERHEADLINE SETTING'][0],
                         {'SectionID': 'L1', 'DeviceNumber': 'L1', 'LineCableID': '', 'Length': '1500.250000',
                          'ConnectionStatus': '0'})
        self.assertEqual([p['SeqNumber'] for p in net['INTERMEDIATE NODES']], ['0', '1'])
        self.assertEqual(eq['TRANSFORMER'], [{'ID': 'TR_66_23', 'KVA': '', 'KVLLprim': '66.000000',
                                              'KVLLsec': '23.000000'}])
        self.assertEqual(eq['THREE WINDING TRANSFORMER'][0]['Comments'], '30 MVA')
        self.assertEqual(eq['BREAKER'], [{'ID': 'INT_66', 'Amps': '', 'KVLL': '66.000000'}])
        codes = {w['code'] for w in report['warnings']}
        self.assertEqual(codes, {'head_nodes_from_sources', 'source_without_equipment', 'feeder_head_node_ambiguous'})
        self.assertEqual(set(report['omitted_tables']), {'CYMBUSDISPLAY', 'CYMDBPARAMETERS'})
        self.assertEqual(report['ignored_tables'], ['ESQ_NODO'])
        self.assertEqual(report['empty_values']['OVERHEADLINE SETTING'], {'LineCableID': 1})

    def test_file_layout_matches_cyme(self):
        report = export(tables(), self.prefix)
        data = Path(report['network_file']).read_bytes()
        self.assertTrue(data.startswith(b'[GENERAL]\r\nDATE='))
        self.assertEqual(data.count(b'\n'), data.count(b'\r\n'))
        text = data.decode('utf-8')
        self.assertIn('\r\n[SI]\r\n', text)
        self.assertIn('[SECTION]\r\nFORMAT_SECTION=SectionID,FromNodeID,FromNodeIndex,ToNodeID,ToNodeIndex,Phase\r\n'
                      'FORMAT_FEEDER=NetworkID,HeadNodeID,CoordSet\r\nFEEDER=SIN,,1\r\nL1,X,0,B66,0,ABC\r\n', text)

    def test_single_head_node_goes_to_feeder_line(self):
        t = tables()
        t['CYMSOURCE'] = t['CYMSOURCE'][:1]
        report = export(t, self.prefix)
        self.assertEqual(report['networks'][0]['HeadNodeID'], 'B66')
        self.assertNotIn('feeder_head_node_ambiguous', {w['code'] for w in report['warnings']})

    def test_substation_has_empty_head_node(self):
        t = tables(CYMNETWORK=[{'NetworkId': 'SIN', 'NetworkType': 1}])
        t['CYMSOURCE'] = t['CYMSOURCE'][:1]
        report = export(t, self.prefix)
        self.assertEqual((report['networks'][0]['kind'], report['networks'][0]['HeadNodeID']), ('SUBSTATION', ''))

    def test_refuses_to_overwrite(self):
        export(tables(), self.prefix)
        with self.assertRaises(FileExistsError):
            export(tables(), self.prefix)

    def assertFails(self, t, text):
        with self.assertRaises(ExportError) as ctx:
            export(t, self.prefix)
        self.assertTrue(any(text in p for p in ctx.exception.problems), ctx.exception.problems)
        self.assertFalse(ascii_export.outputs(self.prefix)[0].exists())

    def test_unverified_codes_fail(self):
        t = tables()
        t['CYMSECTION'][0]['Phase'] = 4
        self.assertFails(t, "código 4")
        t = tables()
        t['CYMBREAKER'][0]['Status'] = 1
        self.assertFails(t, "código 1")
        self.assertFails(tables(CYMNETWORK=[{'NetworkId': 'SIN', 'NetworkType': 2}]), 'red SIN')

    def test_unsupported_device_and_table_fail(self):
        t = tables()
        t['CYMSECTIONDEVICE'].append({'DeviceNumber': 'G1', 'DeviceType': 36, 'NetworkId': 'SIN',
                                      'SectionId': 'L1', 'Location': 0})
        self.assertFails(t, 'tipo de equipo sin formato')
        self.assertFails(tables(CYMLOAD=[{'DeviceNumber': 'C'}]), 'tabla CYMLOAD')

    def test_missing_rows_and_tertiary_fail(self):
        self.assertFails(tables(CYMBREAKER=[]), 'falta su fila en CYMBREAKER')
        self.assertFails(tables(CYMSECTIONNODE=[]), 'sin nodo terciario')

    def test_connector_index_and_commas_fail(self):
        t = tables()
        t['CYMSECTION'][1]['ToNodeConnectorIndex'] = 2
        self.assertFails(t, 'índice de conector')
        t = tables()
        t['CYMEQTHREEWINDINGTRANSFORMER'][0]['Comments'] = '30 MVA, ONAN'
        self.assertFails(t, 'contiene coma')

    @unittest.skipUnless((Path(__file__).parents[1] / 'data/cyme-ascii-ref/ref_bd_nuevo_net.txt').exists(),
                         'sin las exportaciones de CYME en data/cyme-ascii-ref')
    def test_reader_parses_real_cyme_export(self):
        net = read(Path(__file__).parents[1] / 'data/cyme-ascii-ref/ref_bd_nuevo_net.txt')
        self.assertEqual(net['__networks__'][0]['__kind__'], 'SUBSTATION')
        self.assertEqual(net['__networks__'][0]['HeadNodeID'], '')
        self.assertEqual([s['SectionID'] for s in net['SECTION']][:2], ['TRANSFO', '8'])
        three = net['THREE WINDING TRANSFORMER SETTING'][0]
        self.assertEqual((three['Location'], three['TertiaryNodeID']), ('0', '5'))

    def test_reports_dropped_columns(self):
        t = tables()
        t['CYMNETWORK'][0]['Description'] = 'Sistema'
        report = export(t, self.prefix)
        self.assertEqual(report['dropped_columns'], {'CYMNETWORK': ['Description']})


if __name__ == '__main__':
    unittest.main()
