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
    """Every response body must be JSON-serializable (no QueuedSend.fn callable)."""
    import json
    api = _api()
    for method, path in [('GET', '/health'), ('GET', '/status'), ('GET', '/approvals')]:
        _, body = api.handle(method, path)
        json.dumps(body, default=str)  # must not raise
