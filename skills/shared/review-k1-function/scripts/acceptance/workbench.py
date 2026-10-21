"""Loopback-only workbench with same-origin write protection and no arbitrary paths."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from pathlib import PureWindowsPath
import json
import mimetypes
import re
import secrets
import socket
from .core import WorkflowError, require, owned_path, file_hash
from . import tasks

STATIC = Path(__file__).resolve().parents[2] / 'assets/acceptance/workbench'
MAX_BODY = 2 * 1024 * 1024


def safe_payload(value):
    if isinstance(value, dict):
        for k, v in value.items():
            require(k.lower() not in {'root', 'executable', 'script', 'command', 'output', 'arguments', 'exportdirectory', 'sourcepath'}, 'ARBITRARY_INPUT_FORBIDDEN')
            if k.lower() == 'code': require(isinstance(v, list), 'ARBITRARY_INPUT_FORBIDDEN')
            if k.lower() == 'path': require(isinstance(v, str) and not Path(v).is_absolute() and not PureWindowsPath(v).drive and '..' not in v.replace('\\','/').split('/'), 'ARBITRARY_INPUT_FORBIDDEN')
            safe_payload(v)
    elif isinstance(value, list):
        for v in value: safe_payload(v)


def create_server(root, host='127.0.0.1', port=0):
    require(host == '127.0.0.1', 'LOOPBACK_ONLY')
    root = Path(root).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def response(self, status, value, mime='application/json'):
            data = json.dumps(value, ensure_ascii=False).encode() if mime == 'application/json' else value
            self.send_response(status); self.send_header('Content-Type', mime); self.send_header('Content-Length', str(len(data)))
            self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self'; frame-ancestors 'none'; object-src 'none'")
            self.end_headers(); self.wfile.write(data)

        def valid_origin(self, write=False):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host: return False
            if self.headers.get('Origin') not in [None, 'http://' + host]: return False
            return not write or (self.headers.get('Origin') == 'http://' + host and secrets.compare_digest(self.headers.get('X-FunctionPreview-Token', ''), self.server.token))

        def do_GET(self):
            if not self.valid_origin(): return self.response(403, {'error': 'ORIGIN_OR_HOST'})
            try:
                if self.path == '/api/session': return self.response(200, {'token': self.server.token})
                if self.path == '/api/runs': return self.response(200, tasks.list_runs(root))
                match = re.fullmatch(r'/api/runs/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})(?:/evidence/([a-f0-9]+))?', self.path)
                if match:
                    run_id, eid = match.groups(); task = tasks.view(root, run_id)
                    if not eid: return self.response(200, task)
                    entry = task['evidence'].get(eid); require(entry, 'EVIDENCE_NOT_REGISTERED')
                    path = owned_path(tasks._root(root, run_id), entry['path']); require(file_hash(path) == entry['sha256'], 'EVIDENCE_HASH')
                    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'webp': 'image/webp', 'gif': 'image/gif'}.get(path.suffix.lstrip('.').lower(), 'text/plain; charset=utf-8')
                    return self.response(200, path.read_bytes(), mime)
                allowed = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
                if self.path in allowed:
                    filename, mime = allowed[self.path]; return self.response(200, (STATIC / filename).read_bytes(), mime)
                self.response(404, {'error': 'NOT_FOUND'})
            except (WorkflowError, OSError, ValueError, KeyError, TypeError): self.response(404, {'error': 'NOT_FOUND'})

        def do_POST(self):
            if not self.valid_origin(True): return self.response(403, {'error': 'ORIGIN_TOKEN_OR_HOST'})
            try:
                length = int(self.headers.get('Content-Length', '-1'))
                if length > MAX_BODY:
                    self.close_connection = True
                    # Drain only a bounded amount; never allocate the declared body length.
                    self.connection.settimeout(0.5)
                    remaining = min(length, MAX_BODY + 65536)
                    try:
                        while remaining:
                            chunk = self.rfile.read(min(remaining, 65536))
                            if not chunk: break
                            remaining -= len(chunk)
                    except (TimeoutError, OSError): pass
                    self.response(413, {'error': 'BODY_TOO_LARGE'})
                    # Deliver the rejection before closing a Windows socket with unread input.
                    self.connection.shutdown(socket.SHUT_WR)
                    return
                require(length >= 0, 'CONTENT_LENGTH_REQUIRED')
                data = json.loads(self.rfile.read(length)); safe_payload(data)
                if self.path == '/api/runs': return self.response(200, tasks.register(root, data))
                if self.path == '/api/events': return self.response(200, tasks.trigger(root, data))
                match = re.fullmatch(r'/api/runs/([A-Za-z0-9][A-Za-z0-9_.-]{0,99})/commands', self.path)
                if match: return self.response(200, tasks.command(root, match[1], data['name'], data.get('payload', {})))
                return self.response(404, {'error': 'NOT_FOUND'})
            except (WorkflowError, OSError, ValueError, KeyError, TypeError) as e:
                self.response(400, {'error': e.code if isinstance(e, WorkflowError) else 'INVALID_REQUEST'})

    server = ThreadingHTTPServer((host, port), Handler); server.token = secrets.token_urlsafe(32)
    return server


def serve(root, host='127.0.0.1', port=0):
    server = create_server(root, host, port)
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'stop': 'Ctrl+C'}, ensure_ascii=False), flush=True)
    try: server.serve_forever()
    finally: server.server_close()
