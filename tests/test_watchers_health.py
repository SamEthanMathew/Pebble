"""Gmail + Slack watcher health wiring (P1-5 completeness).

The pre-merge review found these two headline watchers were the silently-dead
surfaces health.py's docstring names, yet they imported no health and masked
auth failures as empty results. These tests drive one real poll cycle and assert
that a benign state beats cleanly while a real auth/API failure is recorded.
"""

from __future__ import annotations


# ── Gmail ────────────────────────────────────────────────────────────────────

def test_gmail_poll_beats_when_disconnected(pebble_home, monkeypatch):
    import health
    import modules.google_auth as ga
    from modules.gmail import GmailWatcherThread

    monkeypatch.setattr(ga, 'is_google_connected', lambda: False)
    w = GmailWatcherThread(important_senders=['a@b.com'], on_notification=lambda i: None)
    w._poll_once(first_run=True)

    row = health.snapshot()['gmail']
    assert row['beats'] == 1
    assert row.get('errors', 0) == 0


def test_gmail_poll_records_error_on_auth_failure(pebble_home, monkeypatch):
    import health
    import modules.google_auth as ga
    from modules.gmail import GmailWatcherThread

    monkeypatch.setattr(ga, 'is_google_connected', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('token expired')

    monkeypatch.setattr(ga, 'GoogleServices', _boom)
    w = GmailWatcherThread(important_senders=['a@b.com'], on_notification=lambda i: None)
    w._poll_once(first_run=False)

    row = health.snapshot()['gmail']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


# ── Slack ────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


def test_slack_poll_records_error_on_auth_failure(pebble_home, monkeypatch):
    import health
    import modules.slack_module as sm
    from modules.slack_module import SlackWatcherThread

    # conversations.list returns an auth error (revoked token)
    monkeypatch.setattr(sm.requests, 'get',
                        lambda *a, **k: _Resp({'ok': False, 'error': 'invalid_auth'}))
    w = SlackWatcherThread(token='xoxb-bad', channels=['general'],
                           on_notification=lambda i: None, workspace_label='acme')
    w._poll_once(first_run=True)

    row = health.snapshot()['slack:acme']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


def test_slack_poll_beats_on_success(pebble_home, monkeypatch):
    import health
    import modules.slack_module as sm
    from modules.slack_module import SlackWatcherThread

    def _get(url, *a, **k):
        if 'conversations.list' in url:
            return _Resp({'ok': True, 'channels': [{'name': 'general', 'id': 'C1'}]})
        return _Resp({'ok': True, 'messages': []})

    monkeypatch.setattr(sm.requests, 'get', _get)
    w = SlackWatcherThread(token='xoxb', channels=['general'],
                           on_notification=lambda i: None, workspace_label='acme')
    w._poll_once(first_run=True)

    row = health.snapshot()['slack:acme']
    assert row['beats'] == 1
    assert row.get('errors', 0) == 0
