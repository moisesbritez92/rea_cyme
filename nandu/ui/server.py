"""Servidor local de la interfaz. Solo escucha en 127.0.0.1 y lee archivos .nandu.sqlite."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import traceback
from urllib.parse import parse_qs, urlparse
import webbrowser

from .service import Project

STATIC = Path(__file__).with_name('static')


class State:
    def __init__(self, data_dir, initial=None):
        self.data_dir = Path(data_dir).resolve()
        self.projects = {}
        self.lock = threading.Lock()
        self.initial = str(Path(initial).resolve()) if initial else None

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


def handler(state):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(STATIC), **kwargs)

        def log_message(self, fmt, *args):
            pass

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
            if method == 'POST':
                length = int(self.headers.get('Content-Length') or 0)
                q.update(json.loads(self.rfile.read(length) or b'{}'))
            route = url.path[len('/api/'):]
            try:
                if route == 'files':
                    return self._send({'files': state.files(), 'initial': state.initial})
                project = state.project(q['file'])
                if route == 'network':
                    return self._send({'geometry': project.geometry(), 'explorer': project.explorer(),
                                       'provenance': project.info['provenance'], 'last': project.last})
                if route == 'properties':
                    return self._send(project.properties(q['kind'], q['id']))
                if route == 'run' and method == 'POST':
                    return self._send(project.run(q['analysis'], q.get('options')))
                return self._send({'error': f'Ruta desconocida: {route}'}, 404)
            except (KeyError, ValueError, PermissionError, FileNotFoundError) as exc:
                return self._send({'error': str(exc)}, 400)
            except Exception as exc:  # el detalle queda en la consola del servidor
                traceback.print_exc()
                return self._send({'error': f'{type(exc).__name__}: {exc}'}, 500)

        def do_GET(self):
            if self.path.startswith('/api/'):
                return self._api('GET')
            return super().do_GET()

        def do_POST(self):
            if self.path.startswith('/api/'):
                return self._api('POST')
            self.send_error(404)

    return Handler


def serve(data_dir, initial=None, port=8765, open_browser=True):
    state = State(data_dir, initial)
    server = ThreadingHTTPServer(('127.0.0.1', port), handler(state))
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    print(f'Ñandu UI en {url} (Ctrl+C para detener)')
    if open_browser:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
