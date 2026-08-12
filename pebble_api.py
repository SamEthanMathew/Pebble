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
    """Transport-agnostic request router. `handle(method, path, body) -> (status, dict)`."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        method = (method or 'GET').upper()
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


def serve(engine, host: str = '127.0.0.1', port: int = 8765):
    """Return a ThreadingHTTPServer serving the API — bound to loopback ONLY.

    Caller runs `.serve_forever()` (typically on a daemon thread) and `.shutdown()`.
    """
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    api = PebbleAPI(engine)

    class _Handler(BaseHTTPRequestHandler):
        def _dispatch(self, method: str) -> None:
            body = None
            if method == 'POST':
                n = int(self.headers.get('Content-Length', 0) or 0)
                raw = self.rfile.read(n) if n else b''
                try:
                    body = json.loads(raw) if raw else None
                except Exception:
                    body = None
            try:
                status, payload = api.handle(method, self.path, body)
            except Exception as e:
                status, payload = 500, {'error': str(e)}
            data = json.dumps(payload, default=str).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  self._dispatch('GET')
        def do_POST(self): self._dispatch('POST')
        def log_message(self, *a):  # keep stdout/stderr quiet
            pass

    return ThreadingHTTPServer((host, port), _Handler)
