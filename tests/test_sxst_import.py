from pathlib import Path
import tempfile
import unittest

from nandu import cyme_model
from nandu.storage import inspect, load_tables
from nandu.sxst_import import import_study, load_value, read

STUDY = """<?xml version="1.0" encoding="UTF-8"?>
<Cyme xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" Version="9.0" Revision="4">
<Version>9.0</Version><Revision>4</Revision>
<Networks><Network>
<Topos><Topo><NetworkID>N</NetworkID><NetworkType>Feeder</NetworkType><Color>255</Color>
 <Sources><Source><SourceNodeID>n0</SourceNodeID>
  <SourceSettings><DeviceNumber>S</DeviceNumber><SourceID>SUB</SourceID>
   <OperatingVoltageA>13.5</OperatingVoltageA><OperatingVoltageB>13.5</OperatingVoltageB><OperatingVoltageC>13.5</OperatingVoltageC></SourceSettings>
  <Meter><MeterModels><MeterModel><LoadModelID>1</LoadModelID>
   <DemandValueA><LoadValue xsi:type="LoadValueAMP_PF" Type="LoadValueAMP_PF"><AMP>10</AMP><PF>95</PF></LoadValue></DemandValueA>
  </MeterModel></MeterModels></Meter>
  <EquivalentSourceModels><EquivalentSourceModel><EquivalentSource><KVLL>23</KVLL>
   <OperatingAngle1>0</OperatingAngle1><OperatingAngle2>-120</OperatingAngle2><OperatingAngle3>120</OperatingAngle3>
  </EquivalentSource></EquivalentSourceModel></EquivalentSourceModels>
 </Source></Sources></Topo></Topos>
<Sections>
 <Section><SectionID>s1</SectionID><OwnerID>N</OwnerID><FromNodeID>n0</FromNodeID><ToNodeID>n1</ToNodeID><Phase>ABC</Phase>
  <Devices>
   <Regulator><DeviceNumber>R1</DeviceNumber><DeviceID>REG</DeviceID><Location>From</Location><ConnectionStatus>Connected</ConnectionStatus>
    <ConnectionConfiguration>ClosedDelta</ConnectionConfiguration><SettingOption>Terminal</SettingOption>
    <TapPositionA>2</TapPositionA><TapPositionB>2</TapPositionB><TapPositionC>2</TapPositionC><PTRatio>200</PTRatio><CTPrimaryRating>100</CTPrimaryRating>
    <ForwardSettings><RsetA>0</RsetA><RsetB>0</RsetB><RsetC>0</RsetC><XsetA>0</XsetA><XsetB>0</XsetB><XsetC>0</XsetC>
     <SetVoltageA>121</SetVoltageA><SetVoltageB>121</SetVoltageB><SetVoltageC>121</SetVoltageC>
     <BandwidthA>2</BandwidthA><BandwidthB>2</BandwidthB><BandwidthC>2</BandwidthC></ForwardSettings></Regulator>
   <OverheadLine><DeviceNumber>s1</DeviceNumber><Length>1000</Length><ConnectionStatus>Connected</ConnectionStatus><LineID>2035</LineID></OverheadLine>
  </Devices></Section>
 <Section><SectionID>s2</SectionID><OwnerID>N</OwnerID><FromNodeID>n1</FromNodeID><ToNodeID>n2</ToNodeID><Phase>B</Phase>
  <Devices>
   <Switch><DeviceNumber>SW</DeviceNumber><DeviceID>SC</DeviceID><Location>From</Location><ClosedPhase>B</ClosedPhase>
    <ConnectionStatus>Connected</ConnectionStatus><NormalStatus>Closed</NormalStatus></Switch>
   <OverheadLine><DeviceNumber>s2</DeviceNumber><Length>500</Length><ConnectionStatus>Connected</ConnectionStatus><LineID>2035</LineID></OverheadLine>
   <SpotLoad><DeviceNumber>C1</DeviceNumber><ConnectionConfiguration>Yg</ConnectionConfiguration><Location>Middle</Location>
    <CustomerLoads><CustomerLoad><CustomerNumber>C1</CustomerNumber><CustomerType>Residential</CustomerType><ConnectionStatus>Connected</ConnectionStatus>
     <CustomerLoadModels><CustomerLoadModel><LoadModelID>1</LoadModelID><LockDuringLoadAllocation>Locked</LockDuringLoadAllocation>
      <CustomerLoadValues><CustomerLoadValue><Phase>B</Phase>
       <LoadValue xsi:type="LoadValueKVA_PF" Type="LoadValueKVA_PF"><KVA>50</KVA><PF>80</PF></LoadValue>
       <ConnectedKVA>75</ConnectedKVA><KWH>0</KWH></CustomerLoadValue></CustomerLoadValues>
     </CustomerLoadModel></CustomerLoadModels></CustomerLoad></CustomerLoads></SpotLoad>
   <Transformer><DeviceNumber>T1</DeviceNumber></Transformer>
  </Devices></Section>
</Sections>
<Nodes>
 <Node><NodeID>n0</NodeID><Connectors><Point><X>0</X><Y>0</Y></Point></Connectors></Node>
 <Node><NodeID>n1</NodeID><Connectors><Point><X>1000</X><Y>0</Y></Point></Connectors></Node>
 <Node><NodeID>n2</NodeID><Connectors><Point><X>1500</X><Y>0</Y></Point></Connectors></Node>
</Nodes>
</Network></Networks>
<Equipments><Equipments><EquipmentDBs>
 <OverheadLineDB><EquipmentID>2035</EquipmentID><PositiveSequenceResistance>0.3</PositiveSequenceResistance>
  <PositiveSequenceReactance>0.4</PositiveSequenceReactance><ZeroSequenceResistance>0.6</ZeroSequenceResistance>
  <ZeroSequenceReactance>1.3</ZeroSequenceReactance><PositiveSequenceShuntSusceptance>3</PositiveSequenceShuntSusceptance>
  <ZeroSequenceShuntSusceptance>1.5</ZeroSequenceShuntSusceptance><PositiveSequenceShuntConductance>0</PositiveSequenceShuntConductance>
  <ZeroSequenceShuntConductance>0</ZeroSequenceShuntConductance><Frequency>50</Frequency><NominalRating>140</NominalRating></OverheadLineDB>
 <RegulatorDB><EquipmentID>REG</EquipmentID><MaximumBoost>10</MaximumBoost><NumberOfTaps>32</NumberOfTaps><ConstructionType>A</ConstructionType></RegulatorDB>
 <SubstationDB><EquipmentID>SUB</EquipmentID><NominalKVLL>23</NominalKVLL><FirstLevelR1>0.1</FirstLevelR1><FirstLevelX1>1</FirstLevelX1>
  <FirstLevelR0>0.1</FirstLevelR0><FirstLevelX0>1</FirstLevelX0></SubstationDB>
</EquipmentDBs></Equipments></Equipments>
<ConsumerClasses><ConsumerClass><Name>Residential</Name><ConstantPower>100</ConstantPower><ConstantCurrent>0</ConstantCurrent>
 <ConstantImpedance>0</ConstantImpedance><UtilizationFactor>100</UtilizationFactor><IsExponentialModel>0</IsExponentialModel>
 <LoadFlowVoltagePercentOfNominal>80</LoadFlowVoltagePercentOfNominal></ConsumerClass></ConsumerClasses>
</Cyme>
"""


class SxstTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.study = Path(self.tmp.name) / 'estudio.sxst'
        self.study.write_text(STUDY, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_text_enums_map_to_verified_access_codes(self):
        tables, report = read(self.study)
        self.assertEqual(tables['CYMSECTION'][0]['Phase'], 7)
        self.assertEqual(tables['CYMSECTION'][1]['Phase'], 2)
        reg = tables['CYMREGULATOR'][0]
        self.assertEqual((reg['ConnectionConfiguration'], reg['SettingOption']), (4, 3))
        locations = {r['DeviceNumber']: r['Location'] for r in tables['CYMSECTIONDEVICE']}
        self.assertEqual((locations['R1'], locations['C1'], locations['s1']), (1, 0, 0))
        self.assertEqual(tables['CYMSWITCH'][0]['ClosedPhase'], 2)
        self.assertEqual(tables['CYMEQOVERHEADLINE'][0]['EquipmentId'], '2035')
        self.assertEqual(tables['CYMNETWORKMETER'][0]['DemandType'], 3)
        self.assertEqual(report['unsupported_devices'], {'Transformer': 1})
        ldc = [r for r in tables['CYMLDCSETTINGS'] if r['Forward'] == 1]
        self.assertEqual(ldc[0]['SetVoltageA'], 121.0)

    def test_typed_load_values_become_kw_kvar(self):
        tables, _ = read(self.study)
        load = tables['CYMCUSTOMERLOAD'][0]
        self.assertAlmostEqual(load['LoadValue1'], 40.0)
        self.assertAlmostEqual(load['LoadValue2'], 30.0)
        self.assertEqual((load['LoadValueType'], load['LockDuringLoadAllocation'], load['ConnectedKVA']), (0, 1, 75.0))

    def test_leading_power_factor_is_negative_kvar(self):
        import xml.etree.ElementTree as ET
        el = ET.fromstring('<LoadValue Type="LoadValueKW_PF"><KW>10</KW><PF>-80</PF></LoadValue>')
        kw, kvar = load_value(el)
        self.assertEqual(kw, 10.0)
        self.assertAlmostEqual(kvar, -7.5)

    def test_imported_study_builds_a_complete_model(self):
        output = Path(self.tmp.name) / 'estudio.nandu.sqlite'
        result = import_study(self.study, output)
        self.assertEqual(result['networks'], ['N'])
        self.assertEqual(inspect(output)['provenance']['source_format'], 'cyme-sxst')
        model = cyme_model.build(load_tables(output, cyme_model.TABLES))
        self.assertEqual([i['code'] for i in model['issues']], [])
        self.assertEqual(model['regulators'][0]['control']['mode'], 'terminal')
        self.assertEqual(model['loads'][0]['phases'], ['B'])
        self.assertTrue(model['loads'][0]['locked'])
        with self.assertRaises(FileExistsError):
            import_study(self.study, output)


if __name__ == '__main__':
    unittest.main()
