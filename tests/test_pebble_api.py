"""Local API router over PebbleEngine (Milestone B / P1-6).

The router (PebbleAPI.handle) is transport-agnostic — tested directly with no
sockets. It's the attach point a web/phone client (and the approvals-inbox UI)
uses to talk to the engine instead of sharing files.
"""

from __future__ import annotations


def _api(pebble_home_noop=None):
    import pebble_api
    import pebble_engine
    return pebble_api.PebbleAPI(pebble_engine.PebbleEngine())


def test_health_endpoint(pebble_home):
    import health
    health.beat('calendar')
    status, body = _api().handle('GET', '/health')
    assert status == 200
    assert body['sources']['calendar']['beats'] == 1


def test_status_lists_modules(pebble_home):
    status, body = _api().handle('GET', '/status')
    assert status == 200
    assert isinstance(body['modules'], list)
    assert body['count'] == len(body['modules'])


def test_approvals_empty(pebble_home):
    status, body = _api().handle('GET', '/approvals')
    assert status == 200
    assert body['proposals'] == []
    assert body['sends'] == []


def test_approvals_serializes_pending_proposal(pebble_home):
    import pebble_api
    import pebble_engine
    from planners.base import Proposal

    eng = pebble_engine.PebbleEngine()
    eng.autonomy._pending['abc123'] = Proposal(
        module='gmail', action='send', args={'to': 'x@y'},
        source='comms', target_id='x@y')
    status, body = pebble_api.PebbleAPI(eng).handle('GET', '/approvals')
    assert status == 200
    props = body['proposals']
    assert props and props[0]['id'] == 'abc123'
    assert props[0]['module'] == 'gmail' and props[0]['action'] == 'send'


def test_approve_unknown_proposal_returns_error_status(pebble_home):
    status, body = _api().handle('POST', '/approvals/proposal/nope/approve')
    assert status == 200
    assert body['status'] == 'error'   # engine.approve -> RouteResult(status='error')


def test_send_now_unknown_returns_false(pebble_home):
    status, body = _api().handle('POST', '/approvals/send/nope/now')
    assert status == 200
    assert body['ok'] is False


def test_unknown_route_is_404(pebble_home):
    status, body = _api().handle('GET', '/does/not/exist')
    assert status == 404
    assert 'error' in body


def test_router_is_json_serializable(pebble_home):
    """Every response body must be PURE JSON — no default=str masking a leaked
    QueuedSend.fn callable (that would emit a stringified pointer over the wire)."""
    import json
    api = _api()
    for method, path in [('GET', '/health'), ('GET', '/status'), ('GET', '/approvals')]:
        _, body = api.handle(method, path)
        json.dumps(body)  # NO default= : a non-JSON value must raise, not stringify


def test_queued_send_serialized_without_fn_callable(pebble_home):
    import json
    import pebble_api
    import pebble_engine
    eng = pebble_engine.PebbleEngine()
    sid = eng.approvals.enqueue(lambda: None, label='Gmail send to x@y')
    try:
        status, body = pebble_api.PebbleAPI(eng).handle('GET', '/approvals')
        assert status == 200
        sends = body['sends']
        assert sends and sends[0]['label'] == 'Gmail send to x@y'
        assert 'fn' not in sends[0]        # never leak the callable
        json.dumps(body)                   # pure JSON
    finally:
        eng.approvals.cancel(sid)          # stop the send Timer so pytest can exit


# ── auth / anti-CSRF (only enforced when a token is configured, i.e. serve()) ──

def _tok_api(pebble_home_noop=None, token='sekret'):
    import pebble_api
    import pebble_engine
    return pebble_api.PebbleAPI(pebble_engine.PebbleEngine(), token=token)


def test_no_token_means_open_for_in_process_use(pebble_home):
    status, _ = _api().handle('GET', '/status', headers={})   # no token -> open
    assert status == 200


def test_missing_or_wrong_bearer_is_unauthorized(pebble_home):
    api = _tok_api()
    assert api.handle('GET', '/status', headers={'Host': '127.0.0.1:8765'})[0] == 401
    assert api.handle('GET', '/status',
                      headers={'Host': '127.0.0.1:8765',
                               'Authorization': 'Bearer wrong'})[0] == 401


def test_nonloopback_host_is_forbidden_defeats_dns_rebinding(pebble_home):
    api = _tok_api()
    status, _ = api.handle('POST', '/approvals/send/x/now',
                           headers={'Host': 'attacker.com',
                                    'Authorization': 'Bearer sekret'})
    assert status == 403


def test_valid_bearer_and_loopback_host_is_authorized(pebble_home):
    api = _tok_api()
    status, _ = api.handle('GET', '/status',
                           headers={'Host': 'localhost:8765',
                                    'Authorization': 'Bearer sekret'})
    assert status == 200


def test_bracketed_ipv6_loopback_host_is_authorized(pebble_home):
    """[::1] and [::1]:port must parse to ::1 (a loopback host), not be rejected."""
    api = _tok_api()
    for host in ('[::1]', '[::1]:8765'):
        status, _ = api.handle('GET', '/status',
                               headers={'Host': host, 'Authorization': 'Bearer sekret'})
        assert status == 200, host


def test_server_rejects_malformed_content_length(pebble_home):
    """A bad Content-Length must not crash/hang the handler (Content-Length DoS)."""
    import socket
    import threading
    import pebble_api
    import pebble_engine
    srv = pebble_api.serve(pebble_engine.PebbleEngine(), port=8794)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        s = socket.create_connection(('127.0.0.1', 8794), timeout=3)
        req = (b'POST /approvals/proposal/x/approve HTTP/1.1\r\n'
               b'Host: 127.0.0.1:8794\r\n'
               b'Content-Length: not-a-number\r\n\r\n')
        s.sendall(req)
        s.settimeout(3)
        first_line = s.recv(4096).split(b'\r\n', 1)[0]
        assert b'400' in first_line   # rejected cleanly, no crash/hang
        s.close()
    finally:
        srv.shutdown()
