"""Importación de tablas con el formato de CYME desde un JSON (nandu-cyme-tables-v1).

Sirve para redes que no vienen de una base Access ni de un estudio .sxst, como el esquema
del SIN armado a partir del unifilar (eventos_ens/esquema/exportar_cyme.py). El JSON trae las
tablas ya traducidas a los códigos Access y declara su procedencia y sus convenciones; acá
solo se comprueba la forma, se audita la estructura y se guarda sin reinterpretar nada.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from . import __version__
from .model import build_model, validate
from .storage import save

FORMAT = 'nandu-cyme-tables-v1'
AUTHORITIES = {'verified', 'documented', 'fitted_to_cyme_results', 'standard_transposed_model', 'hypothesis'}


def _scalar(value, where):
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError(f'{where}: valor no admitido ({type(value).__name__})')


def read(path):
    """Lee y comprueba el documento; devuelve (tablas, documento)."""
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(document, dict) or document.get('format') != FORMAT:
        raise ValueError(f'No es un documento {FORMAT}')
    tables = document.get('tables')
    if not isinstance(tables, dict) or not tables.get('CYMNETWORK'):
        raise ValueError('El documento no trae tablas o le falta CYMNETWORK')
    for name, rows in tables.items():
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError(f'{name}: se esperaba una lista de filas')
        for i, row in enumerate(rows):
            for column, value in row.items():
                _scalar(value, f'{name}[{i}].{column}')
    for item in document.get('conventions') or []:
        if item.get('authority') not in AUTHORITIES:
            raise ValueError(f'Convención sin autoridad admitida: {item.get("item")!r}')
    return {name: rows for name, rows in tables.items() if rows}, document


def import_tables(path, output):
    path = Path(path).resolve(strict=True)
    sha_before = hashlib.sha256(path.read_bytes()).hexdigest()
    started = datetime.now(timezone.utc).isoformat()
    tables, document = read(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha_before:
        raise RuntimeError('El documento cambió durante la lectura')
    schemas = {name: {'columns': [{'name': c, 'type': 'VARIANT'} for c in sorted({k for r in rows for k in r})],
                      'scope': 'cyme_tables_json'} for name, rows in tables.items()}
    model = build_model(tables)
    # La semántica declarada por el documento reemplaza a la genérica de una base Access.
    model['semantics'] = {**model['semantics'], **(document.get('semantics') or {})}
    validation = validate(model)
    networks = [r.get('NetworkId') for r in tables['CYMNETWORK']]
    provenance = {'importer_version': __version__, 'source_format': FORMAT, 'source_path': str(path),
                  'source_sha256': sha_before, 'source_hash_unchanged': True, 'source_read_only': True,
                  'network_ids': networks, 'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
                  'source': document.get('source') or {}, 'conventions': document.get('conventions') or [],
                  'excluded_tables': []}
    save(output, provenance, schemas, tables, model, validation)
    return {'file': str(Path(output).resolve()), 'networks': networks,
            'counts': {k: len(v) for k, v in tables.items()}, 'source_sha256': sha_before,
            'validation_status': validation['status'], 'error_count': validation['error_count'],
            'warning_count': validation['warning_count']}
