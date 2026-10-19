"""Extrae los cambios exactos de cargas que escribe el estimador de CYME en el historial de modificaciones.

Entrada: JSON de `cyme_list_modifications` (MCP de CYME) tras correr el estimador.
Salida: lista JSON {network, device, customer, phase, kw0, kw1, kvar0, kvar1} con los
valores nominales antes y después (kW/kvar, tres decimales como los guarda CYME).

Uso: py -3.11 scripts\\cyme_dse_changes.py modificaciones.json salida.json
"""
import json
from pathlib import Path
import re
import sys

PATTERNS = {
    'spot': re.compile(r"^-? ?Modify 'Spot Load (.+)'\.$"),
    'customer': re.compile(r"^- Modify 'Customer Load (?!Model )(.+)'\.$"),
    'phase': re.compile(r"^- Modify 'Phase (\w+)'\.$"),
    'kw': re.compile(r"^- Change 'Real Power' from (-?[\d.,]+) kW to (-?[\d.,]+) kW\.?$"),
    'kvar': re.compile(r"^- Change 'Reactive Power' from (-?[\d.,]+) kvar to (-?[\d.,]+) kvar\.?$"),
}


def number(text):
    return float(text.replace('.', '').replace(',', '.')) if text.count(',') == 1 else float(text)


def parse(modifications):
    rows = {}
    for mod in modifications:
        network = (mod.get('InvolvedNetworks') or [None])[0]
        spot = customer = phase = None
        for raw in mod.get('Details', '').splitlines():
            line = raw.strip()
            for kind, pattern in PATTERNS.items():
                found = pattern.match(line)
                if not found:
                    continue
                if kind == 'spot':
                    spot, customer, phase = found.group(1), None, None
                elif kind == 'customer':
                    customer, phase = found.group(1), None
                elif kind == 'phase':
                    phase = found.group(1)
                else:
                    key = (network, spot, customer, phase)
                    row = rows.setdefault(key, {'network': network, 'device': spot, 'customer': customer, 'phase': phase})
                    row[kind + '0'], row[kind + '1'] = number(found.group(1)), number(found.group(2))
                break
    return list(rows.values())


if __name__ == '__main__':
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(source.read_text(encoding='utf-8'))
    result = parse(data['modifications'])
    with target.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=1, ensure_ascii=False)
    print(json.dumps({'rows': len(result), 'output': str(target)}))
