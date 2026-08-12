"""Proactive-engine health wiring (P1-5).

Regression guard for the review finding that the six polling loops' new
health.record_error() calls were unreachable dead code: 4 of the 6 _check_*
helpers swallowed every exception internally, so _check_x() never raised, the
loop always beat(), and a dead watcher (expired token) showed 🟢 forever.
"""

from __future__ import annotations


def _engine():
    from proactive_engine import ProactiveEngine
    # Publish-only + headless: no root, no callbacks.
    return ProactiveEngine()


def test_proactive_engine_is_publish_only_no_self_subscribe(pebble_home):
    """P0-2: constructing the engine must add NO bus subscribers (the dispatcher
    is the sole subscriber now). Otherwise events would fire twice."""
    from events import bus, CALENDAR_EVENT_APPROACHING, TASK_DUE_SOON, REMINDER_DUE
    before = {e: bus.subscriber_count(e)
              for e in (CALENDAR_EVENT_APPROACHING, TASK_DUE_SOON, REMINDER_DUE)}
    _engine()
    for e, n in before.items():
        assert bus.subscriber_count(e) == n, f'engine self-subscribed to {e}'


def test_check_focus_publishes_ending_soon(pebble_home, monkeypatch):
    """The focus loop publishes an event; it does not render a popup itself."""
    import datetime as dt
    import modules.focus_timer as ft
    from events import bus, FOCUS_ENDING_SOON

    start = (dt.datetime.now() - dt.timedelta(minutes=24)).isoformat()  # ~1 min left of 25
    monkeypatch.setattr(ft, 'get_focus_state', lambda: {
        'active': True, 'start_time': start, 'session_type': 'work',
        'task': 'thesis', 'duration_minutes': 25,
    })
    got = []
    bus.subscribe(FOCUS_ENDING_SOON, lambda p: got.append(p))
    _engine()._check_focus()
    assert got and got[-1]['session_type'] == 'work'
    bus.clear()


def test_engine_and_dispatcher_fire_exactly_one_popup(pebble_home):
    """End-to-end P0-2: publish a calendar event with only the dispatcher wired,
    and exactly one popup fires (no double from a lingering engine subscriber)."""
    from events import bus, CALENDAR_EVENT_APPROACHING
    from planners.dispatcher import NotificationDispatcher

    fired = []
    d = NotificationDispatcher(popup_fn=lambda **kw: fired.append(kw),
                               max_per_10min=10,
                               quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.start()
    _engine()  # must not add a second subscriber
    bus.publish(CALENDAR_EVENT_APPROACHING,
                {'event_id': 'e1', 'title': 'Lecture', 'minutes_away': 5})
    assert len(fired) == 1
    bus.clear()


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
