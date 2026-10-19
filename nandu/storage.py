"""Formato SQLite portable: filas originales, modelo y procedencia."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile

FORMAT = 'nandu-network-v1'


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def save(path, metadata, schemas, tables, model, validation):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'El destino ya existe: {path}')
    handle, temporary = tempfile.mkstemp(prefix='.nandu-', suffix='.sqlite', dir=path.parent)
    os.close(handle)
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.executescript('''
                CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE TABLE source_schema (table_name TEXT PRIMARY KEY, columns_json TEXT NOT NULL);
                CREATE TABLE source_records (table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(table_name, ordinal));
                CREATE TABLE model_objects (kind TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    payload_json TEXT NOT NULL, PRIMARY KEY(kind, ordinal));
            ''')
            entries = {'format': FORMAT, 'provenance': metadata, 'validation': validation,
                       'semantics': model['semantics']}
            db.executemany('INSERT INTO metadata VALUES (?, ?)', [(k, encode(v)) for k, v in entries.items()])
            for name, rows in tables.items():
                db.execute('INSERT INTO source_schema VALUES (?, ?)', (name, encode(schemas[name])))
                db.executemany('INSERT INTO source_records VALUES (?, ?, ?)',
                               ((name, i, encode(row)) for i, row in enumerate(rows)))
            for kind, rows in model.items():
                if kind != 'semantics':
                    db.executemany('INSERT INTO model_objects VALUES (?, ?, ?)',
                                   ((kind, i, encode(row)) for i, row in enumerate(rows)))
        # El enlace publica el archivo completo sin sustituir un destino concurrente.
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def inspect(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        metadata = {k: json.loads(v) for k, v in db.execute('SELECT key, value_json FROM metadata')}
        if metadata.get('format') != FORMAT:
            raise ValueError('Formato de proyecto Ñandu no compatible')
        counts = dict(db.execute('SELECT kind, COUNT(*) FROM model_objects GROUP BY kind'))
        table_counts = dict(db.execute('SELECT table_name, COUNT(*) FROM source_records GROUP BY table_name'))
    return {'file': str(path), 'format': FORMAT, 'counts': counts, 'source_row_counts': table_counts,
             **{k: metadata[k] for k in ('provenance', 'validation', 'semantics')}}


def load_tables(path, names):
    """Leer las tablas solicitadas sin depender de ODBC ni de la instalación CYME."""
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        version = db.execute("SELECT value_json FROM metadata WHERE key='format'").fetchone()
        if not version or json.loads(version[0]) != FORMAT:
            raise ValueError('Formato de proyecto Ñandu no compatible')
        return {name: [json.loads(row[0]) for row in db.execute(
            'SELECT payload_json FROM source_records WHERE table_name=? ORDER BY ordinal', (name,))]
                for name in sorted(names)}


def save_json(path, value):
    """Publicar informe sin reemplazar archivos existentes."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.nandu-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
