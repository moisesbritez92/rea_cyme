"""Inspección estática de enumeraciones COM; no crea objetos ni registra bibliotecas."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    from comtypes.typeinfo import LoadTypeLibEx
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+')
    parser.add_argument('--output')
    args = parser.parse_args()
    reports = []
    for filename in args.files:
        path = Path(filename).resolve(strict=True)
        library = LoadTypeLibEx(str(path), regkind=2)  # REGKIND_NONE
        enums = {}
        for i in range(library.GetTypeInfoCount()):
            if library.GetTypeInfoType(i) != 0:  # TKIND_ENUM
                continue
            name = library.GetDocumentation(i)[0]
            info = library.GetTypeInfo(i)
            values = {}
            for j in range(info.GetTypeAttr().cVars):
                desc = info.GetVarDesc(j)
                values[info.GetDocumentation(desc.memid)[0]] = desc._.lpvarValue.contents.value
            enums[name] = values
        with path.open('rb') as stream:
            sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
        reports.append({'path': str(path), 'sha256': sha256, 'enums': enums})
    text = json.dumps(reports, indent=2, ensure_ascii=False)
    if args.output:
        with Path(args.output).open('x', encoding='utf-8') as stream:
            stream.write(text + '\n')
        print(json.dumps({'output': args.output, 'enum_counts': [len(r['enums']) for r in reports]}))
    else:
        print(text)


if __name__ == '__main__':
    main()
