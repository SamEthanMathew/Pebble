"""Local API over PebbleEngine — Milestone B / P1-6.

Exposes the engine to out-of-process clients as a small JSON API over localhost,
so the chat surface (today a separate process sharing files) and a future
web/phone client attach through ONE interface instead of the filesystem. The
router (`PebbleAPI.handle`) is transport-agnostic and fully unit-testable;
`serve()` is a thin stdlib http.server wrapper bound to loopback only.

Headless: imports no GUI toolkit (guard-tested).
"""

from __future__ import annotations

import time
from typing import Any


class PebbleAPI:
    """Transport-agnostic request router. `handle(method, path, body, headers) ->
    (status, dict)`.

    When constructed with a `token`, every request must present
    `Authorization: Bearer <token>` and a loopback `Host` header — this defeats
    CSRF/DNS-rebinding from a browser and restricts to same-user local processes
    that can read the token file. With no token (in-process/tests) the router is
    open. `serve()` always sets a token, so the network-exposed path is never open.
    """

    _LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}

    def __init__(self, engine, *, token: str | None = None) -> None:
        self._engine = engine
        self._token = token

    def handle(self, method: str, path: str, body: dict | None = None,
               headers: dict | None = None) -> tuple[int, dict]:
        method = (method or 'GET').upper()

        denied = self._authorize(headers or {})
        if denied is not None:
            return denied

        parts = [p for p in path.split('?', 1)[0].strip('/').split('/') if p]

        if method == 'GET' and parts == ['health']:
            import health
            return 200, {'sources': health.snapshot()}

        if method == 'GET' and parts == ['status']:
            mods = self._engine.modules()
            return 200, {'modules': [m.tool_name() for m in mods], 'count': len(mods)}

        if method == 'GET' and parts == ['approvals']:
            return 200, {
                'proposals': [self._proposal_json(pid, p)
                              for pid, p in self._engine.pending_approvals().items()],
                'sends':     [self._send_json(s) for s in self._engine.queued_sends()],
            }

        if method == 'POST' and len(parts) == 4 and parts[0] == 'approvals':
            kind, ident, action = parts[1], parts[2], parts[3]
            if kind == 'proposal' and action == 'approve':
                return 200, self._route_json(self._engine.approve(ident))
            if kind == 'proposal' and action == 'deny':
                return 200, self._route_json(self._engine.deny(ident))
            if kind == 'send' and action == 'cancel':
                return 200, {'ok': bool(self._engine.cancel_send(ident))}
            if kind == 'send' and action == 'now':
                return 200, {'ok': bool(self._engine.send_now(ident))}

        return 404, {'error': 'not found', 'method': method, 'path': path}

    # ── auth / anti-CSRF ────────────────────────────────────────────────────────

    def _authorize(self, headers: dict) -> tuple[int, dict] | None:
        if not self._token:
            return None  # no token configured -> trusted in-process use
        # DNS-rebinding defense: a rebound request carries Host: attacker.com.
        if (hostname := self._hostname(headers)) and hostname not in self._LOOPBACK_HOSTS:
            return 403, {'error': 'forbidden host'}
        # Bearer token (constant-time compare): a browser page cannot read the
        # token file, so this defeats CSRF; only same-user local processes can.
        import secrets
        got = self._header(headers, 'Authorization') or ''
        if not secrets.compare_digest(got, f'Bearer {self._token}'):
            return 401, {'error': 'unauthorized'}
        return None

    @classmethod
    def _hostname(cls, headers: dict) -> str:
        """Extract the bare hostname from the Host header, handling bracketed
        IPv6 (`[::1]` / `[::1]:8765`) and `host:port`."""
        host = (cls._header(headers, 'Host') or '').strip()
        if not host:
            return ''
        if host.startswith('['):            # [::1] or [::1]:port
            end = host.find(']')
            return host[1:end].lower() if end != -1 else host.lower()
        return host.rsplit(':', 1)[0].lower()

    @staticmethod
    def _header(headers: dict, name: str) -> str | None:
        low = name.lower()
        for k, v in headers.items():
            if str(k).lower() == low:
                return v
        return None

    # ── serialization (JSON-safe; never leak the QueuedSend.fn callable) ────────

    @staticmethod
    def _proposal_json(pid: str, p: Any) -> dict:
        d = p.to_dict() if hasattr(p, 'to_dict') else dict(vars(p))
        d['id'] = pid
        return d

    @staticmethod
    def _send_json(s: Any) -> dict:
        fire_at = getattr(s, 'fire_at', 0) or 0
        return {
            'id':           getattr(s, 'id', None),
            'label':        getattr(s, 'label', ''),
            'fire_at':      fire_at,
            'seconds_left': max(0, int(fire_at - time.time())) if fire_at else 0,
            'metadata':     getattr(s, 'metadata', {}) or {},
            'canceled':     bool(getattr(s, 'canceled', False)),
            'fired':        bool(getattr(s, 'fired', False)),
        }

    @staticmethod
    def _route_json(r: Any) -> dict:
        # RouteResult.result may be non-serializable — expose only safe fields.
        return {
            'status':      getattr(r, 'status', None),
            'proposal_id': getattr(r, 'proposal_id', None),
            'module':      getattr(r, 'module', None),
            'action':      getattr(r, 'action', None),
            'tier':        getattr(r, 'tier', None),
            'error':       getattr(r, 'error', None),
        }


_MAX_BODY = 1 << 20  # 1 MiB cap on request bodies


def token_path():
    import paths
    return paths.data_dir() / 'api_token'


def _write_token_file(token: str) -> None:
    import os
    p = token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding='utf-8')
    try:
        os.chmod(p, 0o600)  # best-effort (Windows ACLs already scope to the user)
    except OSError:
        pass


def serve(engine, host: str = '127.0.0.1', port: int = 8765, token: str | None = None):
    """Return a ThreadingHTTPServer serving the API — bound to loopback ONLY, and
    authenticated: a per-session bearer token is generated (unless supplied) and
    written to ~/.pebble/api_token for the trusted local client. The token is also
    on `server.pebble_token`. Caller runs `.serve_forever()` then `.shutdown()`."""
    import json
    import secrets
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    if token is None:
        token = secrets.token_urlsafe(32)
        _write_token_file(token)
    api = PebbleAPI(engine, token=token)

    class _Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict) -> None:
            try:
                data = json.dumps(payload).encode('utf-8')  # no default=: fail loud
            except TypeError:
                status, data = 500, b'{"error":"serialization"}'
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method: str) -> None:
            body = None
            if method == 'POST':
                try:
                    n = int(self.headers.get('Content-Length', 0) or 0)
                except (TypeError, ValueError):
                    return self._reply(400, {'error': 'bad content-length'})
                if n < 0:
                    return self._reply(400, {'error': 'bad content-length'})
                raw = self.rfile.read(min(n, _MAX_BODY)) if n else b''
                if raw:
                    try:
                        body = json.loads(raw)
                    except Exception:
                        body = None
            headers = {k: v for k, v in self.headers.items()}
            try:
                status, payload = api.handle(method, self.path, body, headers)
            except Exception as e:
                status, payload = 500, {'error': str(e)}
            self._reply(status, payload)

        def do_GET(self):  self._dispatch('GET')
        def do_POST(self): self._dispatch('POST')
        def log_message(self, *a):  # keep stdout/stderr quiet
            pass

    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.pebble_token = token  # expose for the trusted in-process client
    return srv
