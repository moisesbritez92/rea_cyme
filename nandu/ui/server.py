"""Servidor local de la interfaz. Solo escucha en loopback (127.0.0.1 y ::1) y lee archivos .nandu.sqlite."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
from pathlib import Path
import re
import socket
import threading
import traceback
from urllib.parse import parse_qs, urlparse
import webbrowser

from ..storage import save_json
from .service import Project

STATIC = Path(__file__).with_name('static')
NAME = re.compile(r'^[\w.\-]{1,120}\.nandu\.sqlite$')
STUDY = re.compile(r'^[\w.\-]{1,140}\.sxst$')
MAX_UPLOAD = 1 << 30  # 1 GiB: los estudios completos de CYME rondan las decenas de MB


class State:
    def __init__(self, data_dir, initial=None):
        self.data_dir = Path(data_dir).resolve()
        self.projects = {}
        self.lock = threading.Lock()
        self.initial = str(Path(initial).resolve()) if initial else None
        # Copias inmutables de los estudios subidos (base de la exportación) y estudios exportados.
        self.studies = self.data_dir / 'studies'
        self.exports = self.data_dir / 'exports'

    def files(self):
        found = sorted(str(p.resolve()) for p in self.data_dir.glob('*.nandu.sqlite'))
        if self.initial and self.initial not in found:
            found.insert(0, self.initial)
        return found

    def project(self, path):
        path = str(Path(path).resolve())
        if path not in self.files():
            raise PermissionError('Archivo fuera de la carpeta de datos')
        with self.lock:
            if path not in self.projects:
                self.projects[path] = Project(path)
            return self.projects[path]

    def save_as(self, project, name):
        """Guardar la red editada como archivo nuevo de la carpeta de datos; el original queda intacto."""
        name = str(name).strip()
        if not name.endswith('.nandu.sqlite'):
            name += '.nandu.sqlite'
        if not NAME.match(name):
            raise ValueError(f'Nombre de archivo no válido: {name}')
        output = project.save_as(self.data_dir / name)
        with self.lock:
            # Las ediciones quedan en el archivo nuevo; el original vuelve a su estado en disco.
            self.projects.pop(str(project.path), None)
        return output

    def _project_name(self, name):
        name = str(name).strip()
        if not name.endswith('.nandu.sqlite'):
            name += '.nandu.sqlite'
        if not NAME.match(name):
            raise ValueError(f'Nombre de archivo no válido: {name}')
        return self.data_dir / name

    def upload_study(self, filename, data):
        """Guardar un .sxst subido como copia inmutable (nombre con su SHA-256) y listar sus redes."""
        from ..sxst_import import networks
        if not data:
            raise ValueError('Archivo vacío')
        digest = hashlib.sha256(data).hexdigest()
        stem = re.sub(r'[^\w.\-]', '_', Path(str(filename or 'estudio')).stem)[:80] or 'estudio'
        self.studies.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.studies.glob(f'*-{digest[:12]}.sxst'))
        target = existing[0] if existing else self.studies / f'{stem}-{digest[:12]}.sxst'
        created = not existing
        if created:
            with open(target, 'xb') as stream:
                stream.write(data)
        try:
            nets = networks(target)
        except Exception as exc:
            if created:
                target.unlink()
            raise ValueError(f'El archivo no es un estudio CYME legible: {exc}') from None
        return {'study': target.name, 'sha256': digest, 'bytes': len(data), 'networks': nets,
                'suggested_name': stem}

    def import_study(self, study, networks, name):
        from ..sxst_import import import_study
        if not STUDY.match(str(study)) or not (self.studies / study).is_file():
            raise ValueError(f'Estudio no encontrado: {study}')
        output = self._project_name(name)
        if output.exists():
            raise FileExistsError(f'Ya existe {output.name}')
        result = import_study(self.studies / study, output, networks or None)
        return {**result, 'file': str(output.resolve())}

    def export_study(self, project, name):
        name = str(name).strip()
        if not name.endswith('.sxst'):
            name += '.sxst'
        if not STUDY.match(name):
            raise ValueError(f'Nombre de archivo no válido: {name}')
        self.exports.mkdir(parents=True, exist_ok=True)
        output = self.exports / name
        report = project.export_sxst(output, [self.studies])
        save_json(output.with_name(name + '.json'), report)
        return {**report, 'name': name}

    def download(self, name):
        if not STUDY.match(str(name)) or not (self.exports / name).is_file():
            raise FileNotFoundError(f'Exportación no encontrada: {name}')
        return self.exports / name


def network(project):
    return {'geometry': project.geometry(), 'explorer': project.explorer(), 'edits': project.edit_state(),
            'provenance': project.info['provenance'], 'last': project.last}


def handler(state):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC), **kwargs)

        def log_message(self, fmt, *args):
            pass

        def end_headers(self):
            # Los estáticos cambian durante el desarrollo; no mezclar HTML nuevo con JS/CSS cacheados.
            if not self.path.startswith('/api/'):
                self.send_header('Cache-Control', 'no-store')
            super().end_headers()

        def _send(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def _api(self, method):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            route = url.path[len('/api/'):]
            body = None
            if method == 'POST':
                length = int(self.headers.get('Content-Length') or 0)
                if length > MAX_UPLOAD:
                    return self._send({'error': 'Archivo demasiado grande'}, 413)
                body = self.rfile.read(length)
                if route != 'sxst-upload':  # el estudio llega como bytes; el resto, como JSON
                    q.update(json.loads(body or b'{}'))
            try:
                if route == 'sxst-upload' and method == 'POST':
                    return self._send(state.upload_study(q.get('name'), body))
                if route == 'sxst-import' and method == 'POST':
                    return self._send(state.import_study(q['study'], q.get('networks'), q['name']))
                if route == 'download':
                    return self._file(state.download(q['name']))
                if route == 'files':
                    return self._send({'files': state.files(), 'initial': state.initial})
                project = state.project(q['file'])
                if route == 'network':
                    return self._send(network(project))
                if route == 'catalog':
                    return self._send(project.catalog())
                if route in ('edit', 'undo') and method == 'POST':
                    entry = project.edit(q['edit']) if route == 'edit' else project.undo()
                    return self._send({**network(project), 'entry': entry})
                if route == 'save-as' and method == 'POST':
                    return self._send({'file': state.save_as(project, q['name'])})
                if route == 'sxst-export-info':
                    return self._send(project.export_info([state.studies]))
                if route == 'sxst-export' and method == 'POST':
                    return self._send(state.export_study(project, q['name']))
                if route == 'properties':
                    return self._send(project.properties(q['kind'], q['id']))
                if route == 'run' and method == 'POST':
                    return self._send(project.run(q['analysis'], q.get('options')))
                return self._send({'error': f'Ruta desconocida: {route}'}, 404)
            except (KeyError, ValueError, PermissionError, FileNotFoundError, FileExistsError) as exc:
                payload = {'error': str(exc)}
                if getattr(exc, 'problems', None):
                    payload['problems'] = exc.problems
                return self._send(payload, 400)
            except Exception as exc:  # el detalle queda en la consola del servidor
                traceback.print_exc()
                return self._send({'error': f'{type(exc).__name__}: {exc}'}, 500)

        def _file(self, path):
            data = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'application/xml')
            self.send_header('Content-Disposition', f'attachment; filename="{path.name}"')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith('/api/'):
                return self._api('GET')
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith('/api/'):
                return self._api('POST')
            self.send_error(404)

    return Handler


class LoopbackV6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def serve(data_dir, initial=None, port=8765, open_browser=True):
    state = State(data_dir, initial)
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(state))
    port = server.server_address[1]
    # «localhost» suele resolver primero a ::1 en Windows (p. ej. el reenvío de puertos de VS Code):
    # también se escucha en el loopback IPv6, nunca en otras interfaces.
    try:
        v6 = LoopbackV6Server(('::1', port), handler(state))
        threading.Thread(target=v6.serve_forever, daemon=True).start()
    except OSError:
        v6 = None
    url = f'http://127.0.0.1:{port}/'
    print(f'Ñandu UI en {url} (Ctrl+C para detener)')
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if v6:
            v6.shutdown()
            v6.server_close()
