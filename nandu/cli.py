import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .cyme_import import import_database, list_networks
from .storage import inspect, load_tables, save_json
from .topology import TABLES, analyze, summarize
from . import electrical
from . import powerflow


def main(argv=None):
    parser = argparse.ArgumentParser(prog='nandu', description='Ñandu — redes eléctricas de distribución')
    parser.add_argument('--version', action='version', version=f'Ñandu {__version__}')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('networks', help='Listar redes de una base Access (solo lectura)')
    listing.add_argument('--database', required=True)
    importing = commands.add_parser('import-cyme', help='Importar redes a un archivo SQLite de Ñandu')
    importing.add_argument('--database', required=True)
    importing.add_argument('--networks', nargs='+', required=True)
    importing.add_argument('--output', required=True)
    study = commands.add_parser('import-sxst', help='Importar un estudio CYME autónomo (.sxst) a SQLite de Ñandu')
    study.add_argument('--study', required=True)
    study.add_argument('--networks', nargs='+')
    study.add_argument('--output', required=True)
    inspection = commands.add_parser('inspect', help='Consultar una importación sin CYME ni Access')
    inspection.add_argument('file')
    topology = commands.add_parser('topology', help='Analizar conectividad condicional por fase')
    topology.add_argument('file')
    topology.add_argument('--output', help='Guardar informe completo en un JSON nuevo')
    topology.add_argument('--details', action='store_true', help='Mostrar nodos, tramos y componentes')
    electric = commands.add_parser('electrical', help='Resolver equipos y preparar parámetros eléctricos')
    electric.add_argument('file')
    electric.add_argument('--profile', help='Perfil JSON explícito para conversiones candidatas')
    electric.add_argument('--output', help='Guardar informe completo en un JSON nuevo')
    electric.add_argument('--details', action='store_true')
    flow = commands.add_parser('solve', help='Flujo de carga de una red eléctrica nativa JSON')
    flow.add_argument('file')
    flow.add_argument('--output', help='Guardar resultados completos en un JSON nuevo')
    flow.add_argument('--details', action='store_true')
    for name, text in (('loadflow', 'Flujo de carga desbalanceado de una importación CYME'),
                       ('estimate', 'Estimador de estado (WLS) con los medidores de la importación'),
                       ('allocate', 'Distribución de carga a la demanda de los medidores (Load Allocation)')):
        cmd = commands.add_parser(name, help=text)
        if name == 'allocate':
            cmd.add_argument('--method', default='KVAMethod', choices=['KVAMethod', 'ActualKVAMethod', 'KWHMethod'])
        if name == 'estimate':
            cmd.add_argument('--initialization', default='None', choices=['None', 'LoadAllocation'],
                             help='InitializationMode de CYME')
            cmd.add_argument('--quality', action='store_true',
                             help='Índices de calidad: residuos normalizados y prueba χ² del costo')
        cmd.add_argument('file')
        cmd.add_argument('--networks', nargs='+', help='Subconjunto de redes (por defecto, todas)')
        cmd.add_argument('--output', help='Guardar el informe completo (con nodos) en un JSON nuevo')
        cmd.add_argument('--details', action='store_true', help='Mostrar nodos, ramas y reguladores')
        cmd.add_argument('--compare-cyme', help='CSV de tensiones por nodo exportado por el MCP de CYME')
    ui = commands.add_parser('ui', help='Abrir la interfaz gráfica local en el navegador')
    ui.add_argument('file', nargs='?', help='Archivo .nandu.sqlite inicial')
    ui.add_argument('--data', default='data', help='Carpeta con archivos .nandu.sqlite (por defecto: data)')
    ui.add_argument('--port', type=int, default=8765)
    ui.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'ui':
            from .ui.server import serve  # numpy y scipy se cargan al calcular
            serve(args.data, args.file, args.port, not args.no_browser)
            return 0
        if args.command in ('loadflow', 'estimate', 'allocate'):
            from . import analysis  # numpy y scipy solo para el motor nodal
            if args.output and Path(args.output).exists():
                raise FileExistsError(f'El destino ya existe: {args.output}')
            full = bool(args.details or args.output or args.compare_cyme)
            if args.command == 'loadflow':
                report = analysis.load_flow(args.file, args.networks, details=full)
            elif args.command == 'estimate':
                report = analysis.estimate(args.file, args.networks, details=full,
                                           options={'initialization': args.initialization,
                                                    'quality_indices': args.quality})
            else:
                report = analysis.allocate(args.file, args.networks, details=full, method=args.method)
            if args.compare_cyme:
                report['cyme_comparison'] = analysis.compare_voltages(report, args.compare_cyme)
            if args.output:
                save_json(args.output, report)
            result = report if args.details else {k: v for k, v in report.items() if k not in (
                'nodes', 'branches', 'regulators', 'switching_devices', 'loads', 'conventions')}
            if args.output:
                result['report_file'] = args.output
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False, default=str))
            return 0 if report['status'] == 'converged' else 2
        if args.command == 'import-sxst':
            from .sxst_import import import_study
            result = import_study(args.study, args.output, args.networks)
            print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
            return 0
        if args.command == 'networks':
            result = {'networks': list_networks(args.database)}
        elif args.command == 'import-cyme':
            result = import_database(args.database, args.networks, args.output)
        elif args.command == 'solve':
            if args.output and Path(args.output).exists():
                raise FileExistsError(f'El destino ya existe: {args.output}')
            model = json.loads(Path(args.file).read_text(encoding='utf-8'))
            report = powerflow.solve(model)
            if args.output:
                save_json(args.output, report)
            result = report if args.details else powerflow.summarize(report)
            if args.output:
                result['report_file'] = args.output
        elif args.command == 'electrical':
            profile = electrical.load_profile(args.profile) if args.profile else None
            report = electrical.compact(electrical.prepare(load_tables(args.file, electrical.TABLES), profile))
            provenance = inspect(args.file)['provenance']
            report['source_provenance'] = {k: v for k, v in provenance.items() if k != 'excluded_tables'}
            if args.output:
                save_json(args.output, report)
            result = report if args.details else electrical.summarize(report)
            if args.output:
                result['report_file'] = args.output
        elif args.command == 'topology':
            report = analyze(load_tables(args.file, TABLES))
            report['source_provenance'] = inspect(args.file)['provenance']
            if args.output:
                save_json(args.output, report)
            result = report if args.details else summarize(report)
            if args.output:
                result['report_file'] = args.output
        else:
            result = inspect(args.file)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        if args.command == 'solve' and result['status'] != 'completed':
            return 2
        return 0
    except Exception as exc:
        print(f'Ñandu: {exc}', file=sys.stderr)
        return 1
