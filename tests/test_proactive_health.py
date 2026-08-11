"""Proactive-engine health wiring (P1-5).

Regression guard for the review finding that the six polling loops' new
health.record_error() calls were unreachable dead code: 4 of the 6 _check_*
helpers swallowed every exception internally, so _check_x() never raised, the
loop always beat(), and a dead watcher (expired token) showed 🟢 forever.
"""

from __future__ import annotations


def _engine():
    from proactive_engine import ProactiveEngine
    # __init__ only stores root + subscribes to the bus; it never calls root
    # methods, so a bare sentinel is enough (no real Tk window needed).
    return ProactiveEngine(root=object(), on_open_chat=lambda: None)


def test_run_check_beats_on_success(pebble_home):
    import health
    _engine()._run_check('tasks', lambda: None)
    row = health.snapshot()['tasks']
    assert row['beats'] == 1
    assert row.get('errors', 0) == 0


def test_run_check_records_error_and_skips_beat(pebble_home):
    import health

    def _boom():
        raise RuntimeError('api down')

    _engine()._run_check('calendar', _boom)
    row = health.snapshot()['calendar']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


def test_failing_calendar_check_surfaces_as_error(pebble_home, monkeypatch):
    """A real _check_calendar failure (GoogleServices raising) must be recorded
    as an error, not silently swallowed and mis-reported as a healthy beat."""
    import health
    import modules.google_auth as ga

    monkeypatch.setattr(ga, 'is_google_connected', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('token expired')

    monkeypatch.setattr(ga, 'GoogleServices', _boom)

    eng = _engine()
    eng._run_check('calendar', eng._check_calendar)
    row = health.snapshot()['calendar']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


def test_failing_reminders_check_surfaces_as_error(pebble_home, monkeypatch):
    import health
    import modules.reminders as rm

    def _boom():
        raise RuntimeError('reminders store unreadable')

    monkeypatch.setattr(rm, 'get_due_reminders', _boom)
    eng = _engine()
    eng._run_check('reminders', eng._check_reminders)
    row = health.snapshot()['reminders']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


def test_failing_focus_check_surfaces_as_error(pebble_home, monkeypatch):
    import health
    import modules.focus_timer as ft

    def _boom():
        raise RuntimeError('focus state corrupt')

    monkeypatch.setattr(ft, 'get_focus_state', _boom)
    eng = _engine()
    eng._run_check('focus', eng._check_focus)
    row = health.snapshot()['focus']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0


def test_failing_meeting_prep_check_surfaces_as_error(pebble_home, monkeypatch):
    import health
    import modules.google_auth as ga

    monkeypatch.setattr(ga, 'is_google_connected', lambda: True)

    def _boom(*a, **k):
        raise RuntimeError('calendar api down')

    monkeypatch.setattr(ga, 'GoogleServices', _boom)
    eng = _engine()
    eng._run_check('meeting_prep', eng._check_meeting_prep)
    row = health.snapshot()['meeting_prep']
    assert row['errors'] == 1
    assert row.get('beats', 0) == 0
