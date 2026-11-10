from pathlib import Path
import tempfile
import unittest

from nandu.ui import cyme_symbols

SYMBOLS = """<?xml version="1.0" encoding="UTF-8"?>
<Cyme><Symbols>
<Symbol><SymbolID>50070</SymbolID><SymbolType>BreakerSymbol</SymbolType><Description>Breaker (Closed)</Description>
 <InputPorts><Point><X>0.0</X><Y>0.5</Y></Point></InputPorts><OutputPorts><Point><X>1.0</X><Y>0.5</Y></Point></OutputPorts>
 <Elements>
  <GraphicalPolygon><Width>0</Width><IsVisible>1</IsVisible><FillType>Transparent</FillType>
   <Points><Point><X>0.1</X><Y>0.1</Y></Point><Point><X>0.9</X><Y>0.1</Y></Point><Point><X>0.9</X><Y>0.9</Y></Point></Points></GraphicalPolygon>
  <GraphicalLine><Width>1</Width><IsVisible>1</IsVisible><PointA><X>0.0</X><Y>0.5</Y></PointA><PointB><X>0.1</X><Y>0.5</Y></PointB></GraphicalLine>
  <GraphicalLine><Width>1</Width><IsVisible>0</IsVisible><PointA><X>0</X><Y>0</Y></PointA><PointB><X>1</X><Y>1</Y></PointB></GraphicalLine>
 </Elements></Symbol>
<Symbol><SymbolID>50069</SymbolID><SymbolType>BreakerSymbol</SymbolType><Description>Breaker (Open)</Description>
 <InputPorts/><OutputPorts/><Elements/></Symbol>
<Symbol><SymbolID>50034</SymbolID><SymbolType>SynchronousGeneratorSymbol</SymbolType><Description>Synchronous Generator (On)</Description>
 <InputPorts><Point><X>0.5</X><Y>1.0</Y></Point></InputPorts><OutputPorts><Point><X>0.5</X><Y>1.0</Y></Point></OutputPorts>
 <Elements>
  <GraphicalEllipse><Width>0</Width><IsVisible>1</IsVisible><FillType>Opaque</FillType><UR><X>0.9</X><Y>0.05</Y></UR><LL><X>0.1</X><Y>0.85</Y></LL></GraphicalEllipse>
  <GraphicalText><Width>0</Width><IsVisible>1</IsVisible><Text>G</Text><Height>4.0</Height><Position><X>0.5</X><Y>0.75</Y></Position></GraphicalText>
  <GraphicalSpline><Width>1</Width><IsVisible>1</IsVisible>
   <Points><Point><X>0</X><Y>0</Y></Point><Point><X>0.2</X><Y>0</Y></Point><Point><X>0.2</X><Y>0.2</Y></Point><Point><X>0</X><Y>0.2</Y></Point></Points></GraphicalSpline>
 </Elements></Symbol>
</Symbols></Cyme>
"""


def item(kind, device, status, symbol, color='44544', style='Solid', size='1.000000', width='1.000000', shown='1'):
    return (f'<GUIDisplayItem><Type>{kind}</Type><DeviceType>{device}</DeviceType><DeviceStatus>{status}</DeviceStatus>'
            f'<Displayed>{shown}</Displayed><SymbolID>{symbol}</SymbolID><LineStyle>{style}</LineStyle><Color>{color}</Color>'
            f'<Size>{size}</Size><LineWidth>{width}</LineWidth></GUIDisplayItem>')


DISPLAY = ('<?xml version="1.0" encoding="UTF-8"?><Cyme><GUIDisplayItemLayers>'
           '<GUIDisplayItemLayer><LayerDescription>IEC Symbols</LayerDescription><GUIDisplayItems/></GUIDisplayItemLayer>'
           '<GUIDisplayItemLayer><LayerDescription>Default Symbols</LayerDescription><GUIDisplayItems>'
           + item('Device', 'Breaker', 'Normal', '39') + item('Device', 'Breaker', 'Open', '38')
           + item('Device', 'SynchronousGenerator', 'Normal', '50034')
           + item('Device', 'Fuse', 'Normal', '14')
           + item('OHPhaseTri', 'UnknownDevice', 'Normal', '50089', width='2.000000')
           + item('OHPhaseA', 'UnknownDevice', 'Normal', '50089', color='128', style='Dot')
           + item('Multipoint', 'UnknownDevice', 'Normal', '0', color='255', size='0.200000')
           + item('FlowDirection', 'UnknownDevice', 'Normal', '0')
           + '</GUIDisplayItems></GUIDisplayItemLayer></GUIDisplayItemLayers></Cyme>')


class CymeSymbolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self):
        (self.dir / 'BaseSymbols.cfg').write_text(SYMBOLS, encoding='utf-8')
        (self.dir / 'DisplayOptionsDefault.cfg').write_text(DISPLAY, encoding='utf-8')

    def test_missing_installation_returns_none(self):
        self.assertIsNone(cyme_symbols.load(self.dir))
        (self.dir / 'BaseSymbols.cfg').write_text('<no-xml', encoding='utf-8')
        (self.dir / 'DisplayOptionsDefault.cfg').write_text(DISPLAY, encoding='utf-8')
        self.assertIsNone(cyme_symbols.load(self.dir))

    def test_colorref_is_bgr(self):
        self.assertEqual(cyme_symbols.colorref('44544'), '#00ae00')
        self.assertEqual(cyme_symbols.colorref('255'), '#ff0000')
        self.assertEqual(cyme_symbols.colorref('128'), '#800000')

    def test_library_symbols_and_default_layer(self):
        self.install()
        data = cyme_symbols.load(self.dir)
        gen = data['devices']['SynchronousGenerator']['Normal']
        self.assertEqual((gen['symbol'], gen['symbol_basis'], gen['color']), ('50034', 'library', '#00ae00'))
        sym = data['symbols']['50034']
        self.assertEqual((sym['in'], sym['out']), ([0.5, 1.0], [0.5, 1.0]))
        self.assertEqual([e['k'] for e in sym['elements']], ['ellipse', 'text', 'spline'])
        self.assertTrue(sym['elements'][0]['fill'])
        self.assertEqual((sym['elements'][1]['text'], sym['elements'][1]['h']), ('G', 4.0))
        # Elementos no visibles fuera; solo se envían los símbolos que la capa usa.
        self.assertEqual([e['k'] for e in data['symbols']['50070']['elements']], ['polygon', 'line'])
        self.assertEqual(sorted(data['symbols']), ['50034', '50069', '50070'])
        self.assertEqual(sorted(data['items']), ['Multipoint', 'OHPhaseA', 'OHPhaseTri'])
        self.assertEqual((data['items']['OHPhaseA']['dash'], data['items']['OHPhaseA']['color']), ([2, 3], '#800000'))
        self.assertEqual(data['items']['OHPhaseTri']['width'], 2.0)

    def test_builtin_ids_resolve_by_type_and_state_as_hypothesis(self):
        # 38/39 son símbolos internos de CYME que no están en BaseSymbols.cfg: se declara la correspondencia.
        self.install()
        breaker = cyme_symbols.load(self.dir)['devices']['Breaker']
        self.assertEqual((breaker['Normal']['symbol'], breaker['Open']['symbol']), ('50070', '50069'))
        self.assertEqual(breaker['Normal']['cyme_symbol_id'], '39')
        self.assertEqual(breaker['Normal']['symbol_basis'], 'hypothesis_same_type_and_state')
        # Sin símbolo de su tipo en la biblioteca: sin símbolo, la interfaz usa el propio.
        fuse = cyme_symbols.load(self.dir)['devices']['Fuse']['Normal']
        self.assertEqual((fuse['symbol'], fuse['symbol_basis']), (None, None))


if __name__ == '__main__':
    unittest.main()
