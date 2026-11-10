"""Símbolos y opciones de dibujo de la instalación local de CYME, leídos en el momento.

CYME guarda sus símbolos vectoriales en `BaseSymbols.cfg` y la capa «Default Symbols» (símbolo, color,
tamaño y grosor por tipo de equipo y estado) en `DisplayOptionsDefault.cfg`. Son archivos XML del
fabricante: Ñandu no los copia ni los distribuye. Si CYME no está instalado, `load` devuelve None y la
interfaz usa sus propios símbolos.

La carpeta se toma de la variable de entorno NANDU_CYME_DIR o de la instalación habitual.
"""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

DEFAULT_DIR = r'C:\Program Files\CYME\CYME'
LAYER = 'Default Symbols'
KINDS = {'GraphicalLine': 'line', 'GraphicalPolyline': 'polyline', 'GraphicalPolygon': 'polygon',
         'GraphicalSpline': 'spline', 'GraphicalEllipse': 'ellipse', 'GraphicalText': 'text'}
# Elementos de red (no equipos) que la interfaz dibuja con las opciones de CYME.
ITEMS = ('Node', 'SourceNode', 'Bus', 'Connector', 'Multipoint', 'OHPhaseA', 'OHPhaseB', 'OHPhaseC', 'OHPhaseAB',
         'OHPhaseAC', 'OHPhaseBC', 'OHPhaseTri', 'UGPhaseA', 'UGPhaseB', 'UGPhaseC', 'UGPhaseAB', 'UGPhaseAC',
         'UGPhaseBC', 'UGPhaseTri', 'DefaultSymbol')
DASHES = {'Solid': [], 'Dot': [2, 3], 'Dash': [8, 4], 'DashDot': [8, 3, 2, 3], 'DashDotDot': [8, 3, 2, 3, 2, 3]}


def colorref(value):
    """COLORREF de Windows (0x00BBGGRR) a #rrggbb."""
    v = int(value or 0)
    return '#{:02x}{:02x}{:02x}'.format(v & 255, (v >> 8) & 255, (v >> 16) & 255)


def _xy(el):
    return [round(float(el.findtext('X')), 4), round(float(el.findtext('Y')), 4)]


def _element(el):
    kind = KINDS.get(el.tag)
    if kind is None or el.findtext('IsVisible') == '0':
        return None
    item = {'k': kind, 'w': float(el.findtext('Width') or 0)}
    if kind == 'line':
        item['p'] = [_xy(el.find('PointA')), _xy(el.find('PointB'))]
    elif kind == 'ellipse':
        item['p'] = [_xy(el.find('UR')), _xy(el.find('LL'))]
    elif kind == 'text':
        item['p'] = [_xy(el.find('Position'))]
        item['text'] = el.findtext('Text') or ''
        item['h'] = float(el.findtext('Height') or 1)
    else:
        item['p'] = [_xy(q) for q in el.find('Points')]
    if kind in ('polygon', 'ellipse'):
        item['fill'] = el.findtext('FillType') == 'Opaque'
    return item


def read_symbols(path):
    symbols = {}
    for sym in ET.parse(path).getroot().find('Symbols'):
        port = lambda tag: next((_xy(q) for q in sym.find(tag) or []), None)
        symbols[sym.findtext('SymbolID')] = {
            'type': sym.findtext('SymbolType'), 'description': sym.findtext('Description'),
            'in': port('InputPorts'), 'out': port('OutputPorts'),
            'connectors': [_xy(c.find('Point')) for c in sym.find('SymbolConnectors') or []],
            'elements': [e for e in map(_element, sym.find('Elements') or []) if e]}
    return symbols


def _resolve(symbol_id, device_type, status, symbols):
    """Símbolo de la biblioteca para un equipo. Los identificadores menores de 50000 son símbolos internos de CYME
    que no están en BaseSymbols.cfg: se usa el de la biblioteca del mismo tipo y estado («(Open)», «(Closed)»,
    «(Connected)»). Es una correspondencia por nombre, sin contraste con CYME: authority = hypothesis."""
    if symbol_id in symbols:
        return symbol_id, 'library'
    wanted = f'{device_type}Symbol'
    same = [(k, s) for k, s in sorted(symbols.items()) if s['type'] == wanted]
    marks = ['(Open)'] if status == 'Open' else ['(Closed)', '(Connected)', '(On)']
    for mark in marks:
        for k, s in same:
            if mark in (s['description'] or ''):
                return k, 'hypothesis_same_type_and_state'
    return (same[0][0], 'hypothesis_same_type') if same else (None, None)


def read_display(path, symbols):
    root = ET.parse(path).getroot()
    layer = next((x for x in root.find('GUIDisplayItemLayers') if x.findtext('LayerDescription') == LAYER), None)
    if layer is None:
        return None
    devices, items = {}, {}
    for it in layer.find('GUIDisplayItems'):
        get = lambda tag: it.findtext(tag)
        style = {'color': colorref(get('Color')), 'size': float(get('Size') or 1), 'width': float(get('LineWidth') or 1),
                 'dash': DASHES.get(get('LineStyle'), []), 'displayed': get('Displayed') == '1'}
        if get('Type') == 'Device':
            symbol, basis = _resolve(get('SymbolID'), get('DeviceType'), get('DeviceStatus'), symbols)
            devices.setdefault(get('DeviceType'), {})[get('DeviceStatus')] = {
                **style, 'symbol': symbol, 'cyme_symbol_id': get('SymbolID'), 'symbol_basis': basis}
        elif get('Type') in ITEMS:
            items[get('Type')] = {**style, 'symbol': get('SymbolID') if get('SymbolID') in symbols else None}
    return {'devices': devices, 'items': items}


def load(install=None):
    """Biblioteca de símbolos y capa por defecto de la instalación de CYME, o None si no está."""
    base = Path(install or os.environ.get('NANDU_CYME_DIR') or DEFAULT_DIR)
    sym_path, opt_path = base / 'BaseSymbols.cfg', base / 'DisplayOptionsDefault.cfg'
    if not sym_path.is_file() or not opt_path.is_file():
        return None
    try:
        symbols = read_symbols(sym_path)
        display = read_display(opt_path, symbols)
    except (ET.ParseError, ValueError, AttributeError, TypeError, OSError):
        return None
    if not display:
        return None
    used = {d['symbol'] for states in display['devices'].values() for d in states.values() if d['symbol']}
    used |= {i['symbol'] for i in display['items'].values() if i['symbol']}
    return {'source': str(base), 'layer': LAYER, **display, 'symbols': {k: symbols[k] for k in sorted(used)}}
