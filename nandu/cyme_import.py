"""Importación de datos CYME/Access sin cargar las DLL ni ejecutar CYME."""
import base64
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import math
from pathlib import Path

from .model import build_model, validate
from .storage import save
from . import __version__

REQUIRED = {'CYMNETWORK', 'CYMNODE', 'CYMSECTION', 'CYMSECTIONDEVICE', 'CYMCUSTOMERLOAD', 'CYMSOURCE'}
GLOBAL = {'CYMDBPARAMETERS', 'CYMSCHEMAVERSION', 'CYMCONSUMERCLASS', 'CYMLOADMODEL'}
REQUIRED_COLUMNS = {
    'CYMNETWORK': {'NetworkId'}, 'CYMNODE': {'NetworkId', 'NodeId', 'X', 'Y'},
    'CYMSECTION': {'NetworkId', 'SectionId', 'FromNodeId', 'ToNodeId', 'Phase'},
    'CYMSECTIONDEVICE': {'NetworkId', 'DeviceNumber', 'DeviceType', 'SectionId', 'Location'},
    'CYMCUSTOMERLOAD': {'NetworkId', 'DeviceNumber', 'DeviceType'},
    'CYMSOURCE': {'NetworkId', 'NodeId', 'DeviceNumber'},
}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def portable(value):
    if isinstance(value, Decimal):
        return {'$decimal': str(value)}
    if isinstance(value, (datetime, date)):
        return {'$datetime': value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {'$bytes_base64': base64.b64encode(value).decode('ascii')}
    if isinstance(value, float) and not math.isfinite(value):
        return {'$nonfinite': repr(value)}
    return value


def connect(path):
    try:
        import pyodbc
    except ImportError as exc:
        raise RuntimeError('Instala el extra Access: py -3.11 -m pip install -e ".[access]"') from exc
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() not in ('.mdb', '.accdb'):
        raise ValueError('Se requiere un archivo .mdb o .accdb')
    if ';' in str(path) or '}' in str(path):
        raise ValueError('La ruta contiene caracteres no admitidos en la conexión ODBC')
    return pyodbc.connect('DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=' + str(path)
                          + ';ReadOnly=1;', readonly=True, timeout=20)


def list_networks(path):
    db = connect(path)
    try:
        return [r[0] for r in db.cursor().execute('SELECT [NetworkId] FROM [CYMNETWORK] ORDER BY [NetworkId]')]
    finally:
        db.close()


def import_database(source, network_ids, output):
    source = Path(source).resolve(strict=True)
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f'El destino ya existe: {output}')
    ids = list(dict.fromkeys(network_ids))
    if not ids or len(ids) > 100 or any(not isinstance(x, str) or not x.strip() for x in ids):
        raise ValueError('Selecciona entre 1 y 100 identificadores de red no vacíos')
    before = digest(source)
    started = datetime.now(timezone.utc).isoformat()
    db = connect(source)
    tables, schemas, excluded = {}, {}, []
    try:
        cursor = db.cursor()
        names = sorted({r.table_name for r in cursor.tables(tableType='TABLE')
                        if r.table_name.startswith('CYM')})
        missing = REQUIRED - set(names)
        if missing:
            raise ValueError(f'Faltan tablas requeridas: {sorted(missing)}')
        available = {r[0] for r in cursor.execute('SELECT [NetworkId] FROM [CYMNETWORK]')}
        if set(ids) - available:
            raise ValueError(f'Redes inexistentes: {sorted(set(ids) - available)}')
        for name in names:
            columns = [{'name': r.column_name, 'type': r.type_name} for r in cursor.columns(table=name)]
            fields = {c['name'] for c in columns}
            if name in REQUIRED_COLUMNS and not REQUIRED_COLUMNS[name].issubset(fields):
                raise ValueError(f'Esquema incompatible en {name}: {sorted(REQUIRED_COLUMNS[name] - fields)}')
            if 'NetworkId' in fields:
                scope = 'selected_networks'
            elif name.startswith('CYMEQ') or name in GLOBAL:
                scope = 'global_catalog'
            else:
                excluded.append({'table': name, 'reason': 'no_network_scope_or_catalog_rule'})
                continue
            if ']' in name:
                raise ValueError('Identificador de tabla no compatible')
            query = f'SELECT * FROM [{name}]'
            params = []
            if scope == 'selected_networks':
                query += ' WHERE [NetworkId] IN (' + ','.join('?' for _ in ids) + ')'
                params = ids
            result = cursor.execute(query, *params)
            labels = [c[0] for c in result.description]
            tables[name] = [dict(zip(labels, map(portable, row))) for row in result]
            schemas[name] = {'scope': scope, 'columns': columns}
        # Los nodos frontera de esta base pueden tener NetworkId NULL. Recuperar
        # todas las ocurrencias de los extremos, sin asignarles una red inventada.
        endpoints = sorted({r[k] for r in tables['CYMSECTION']
                            for k in ('FromNodeId', 'ToNodeId') if r[k] is not None})
        for offset in range(0, len(endpoints), 100):
            batch = endpoints[offset:offset + 100]
            result = cursor.execute('SELECT * FROM [CYMNODE] WHERE [NodeId] IN ('
                                    + ','.join('?' for _ in batch) + ')', *batch)
            labels = [c[0] for c in result.description]
            for row in result:
                record = dict(zip(labels, map(portable, row)))
                if record['NetworkId'] not in ids:
                    tables['CYMNODE'].append(record)
        schemas['CYMNODE']['scope'] = 'selected_networks_and_referenced_boundary_nodes'
    finally:
        db.close()
    after = digest(source)
    if before != after:
        raise RuntimeError('La base cambió durante la lectura; repetir con una copia estable. No se publicó la importación.')
    model = build_model(tables)
    validation = validate(model)
    provenance = {'source_path': str(source), 'source_sha256': before,
                  'source_hash_unchanged': True, 'started_at': started,
                  'completed_at': datetime.now(timezone.utc).isoformat(),
                  'network_ids': ids, 'importer_version': __version__,
                  'source_read_only': True, 'excluded_tables': excluded,
                  'capture_consistency': 'hash_before_after_not_transactional_snapshot',
                  'catalog_scope': 'all_rows_of_equipment_tables',
                  'coverage': 'selected_network_tables_and_catalogs_not_full_database'}
    save(output, provenance, schemas, tables, model, validation)
    return {'file': str(output), 'networks': ids,
            'counts': {k: len(v) for k, v in model.items() if isinstance(v, list)},
            'imported_tables': len(tables), 'excluded_tables': len(excluded),
            'source_sha256': before, 'validation_status': validation['status'],
            'issue_count': len(validation['issues']), 'solver_ready': False}
