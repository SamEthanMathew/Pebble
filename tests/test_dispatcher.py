"""Notification dispatcher: rate limit, dedup, quiet hours, focus, catch-up."""

from __future__ import annotations

import datetime


class _FakePopup:
    def __init__(self):
        self.calls = []

    def __call__(self, *, title, body, buttons, metadata):
        self.calls.append({'title': title, 'body': body, 'metadata': metadata})


def test_basic_submit_fires_popup(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    result = d.submit(Notification(title='X', body='Y'))
    assert result == 'fired'
    assert popup.calls and popup.calls[0]['title'] == 'X'


def test_dedup_suppresses_same_key(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.submit(Notification(title='A', body='1', dedup_key='evt:1'))
    res = d.submit(Notification(title='B', body='2', dedup_key='evt:1'))
    assert res == 'suppressed:dedup'
    assert len(popup.calls) == 1


def test_rate_limit_queues_excess(pebble_home):
    """With max_per_10min=1, only the first non-critical fires; rest queue."""
    from planners.dispatcher import NotificationDispatcher, Notification

    # Use a controllable clock
    t = [1000.0]
    def clock(): return t[0]

    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=1,
                                quiet_hours_start='99:99', quiet_hours_end='99:99',
                                clock=clock)

    assert d.submit(Notification(title='A', body='1', kind='m')) == 'fired'
    assert d.submit(Notification(title='B', body='2', kind='m')) == 'suppressed:rate_limit'
    assert d.submit(Notification(title='C', body='3', kind='m')) == 'suppressed:rate_limit'
    assert d.fired_count() == 1
    assert d.queue_size() == 2

    # Advance time past 10min — flush_queue can now fire one
    t[0] += 700
    n = d.flush_queue()
    assert n == 1
    assert d.fired_count() == 2


def test_critical_bypasses_rate_limit(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=1,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.submit(Notification(title='A', body='', urgency='normal'))
    res = d.submit(Notification(title='B', body='', urgency='critical'))
    assert res == 'fired'
    assert d.fired_count() == 2


def test_quiet_hours_suppresses_non_critical(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification

    fake_now = datetime.datetime(2026, 5, 10, 23, 30)  # 11:30pm — quiet
    popup = _FakePopup()
    d = NotificationDispatcher(
        popup_fn=popup, max_per_10min=10,
        quiet_hours_start='22:00', quiet_hours_end='07:00',
        now_fn=lambda: fake_now,
    )

    res = d.submit(Notification(title='X', body='', urgency='normal'))
    assert res == 'suppressed:quiet_hours'

    res = d.submit(Notification(title='Y', body='', urgency='critical'))
    assert res == 'fired'


def test_focus_session_suppresses_then_catchup(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')

    # Start focus session
    d._on_focus_start({})
    res = d.submit(Notification(title='A', body='', urgency='normal'))
    assert res == 'suppressed:focus_session'
    res = d.submit(Notification(title='B', body='', urgency='normal'))
    assert res == 'suppressed:focus_session'

    # Critical still fires during focus
    res = d.submit(Notification(title='URGENT', body='', urgency='critical'))
    assert res == 'fired'
    assert popup.calls[-1]['title'] == 'URGENT'

    # End focus → catch-up summary fires
    d._on_focus_end({})
    catchup = popup.calls[-1]
    assert 'catch-up' in catchup['title'].lower() or 'focus over' in catchup['title'].lower()
    assert '2' in catchup['body']  # 2 suppressed during the session


def test_calendar_event_payload_routed(pebble_home):
    """Bus → dispatcher: a CALENDAR_EVENT_APPROACHING event becomes a popup."""
    from events import bus, CALENDAR_EVENT_APPROACHING
    from planners.dispatcher import NotificationDispatcher
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.start()
    bus.publish(CALENDAR_EVENT_APPROACHING, {
        'event_id': 'e123',
        'title':    'Lecture',
        'minutes_away': 5,
        'location': 'GHC 4307',
    })
    assert popup.calls
    assert 'Lecture' in popup.calls[-1]['title']
    bus.clear()


class _CapturePopup:
    """Captures buttons too, so we can assert action specs the shell will render."""
    def __init__(self):
        self.calls = []

    def __call__(self, *, title, body, buttons, metadata):
        self.calls.append({'title': title, 'body': body,
                           'buttons': buttons, 'metadata': metadata})


def _open_dispatcher(popup):
    from planners.dispatcher import NotificationDispatcher
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                               quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.start()
    return d


def test_morning_briefing_event_routed(pebble_home):
    from events import bus, MORNING_BRIEFING_DUE
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(MORNING_BRIEFING_DUE, {})
    assert popup.calls and 'morning' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_meeting_prep_event_routed(pebble_home):
    from events import bus, MEETING_PREP_DUE
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(MEETING_PREP_DUE, {
        'title': '1:1 with Sam', 'minutes_away': 10,
        'num_attendees': 2, 'location': 'Zoom',
    })
    assert popup.calls
    assert '1:1 with Sam' in popup.calls[-1]['title']
    bus.clear()


def test_focus_ending_soon_event_routed(pebble_home):
    from events import bus, FOCUS_ENDING_SOON
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(FOCUS_ENDING_SOON, {'session_type': 'work', 'task': 'thesis'})
    assert popup.calls and '1 min' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_focus_end_fires_completion_popup(pebble_home):
    from events import bus, FOCUS_SESSION_ENDED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(FOCUS_SESSION_ENDED, {'session_type': 'work', 'task': 'thesis'})
    assert popup.calls
    assert 'complete' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_notifications_carry_button_action_specs(pebble_home):
    """Notifications carry label/action/style button specs (no Tk callbacks) so a
    shell can render them; the dispatcher stays headless."""
    from events import bus, CALENDAR_EVENT_APPROACHING
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(CALENDAR_EVENT_APPROACHING,
                {'event_id': 'e1', 'title': 'Lecture', 'minutes_away': 5})
    btns = popup.calls[-1]['buttons']
    assert any(b.get('action') == 'open_chat' for b in btns)
    assert all('command' not in b for b in btns)   # headless: no Tk callables
    bus.clear()


def test_planner_completed_surfaces_user_facing_notification(pebble_home):
    from events import bus, PLANNER_COMPLETED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(PLANNER_COMPLETED,
                {'planner': 'morning', 'state_doc': 'x.json', 'was_skipped': False})
    assert popup.calls and 'briefing' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_planner_completed_skipped_is_silent(pebble_home):
    from events import bus, PLANNER_COMPLETED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(PLANNER_COMPLETED, {'planner': 'morning', 'was_skipped': True})
    assert not popup.calls
    bus.clear()


def test_internal_planner_completion_is_silent(pebble_home):
    """Internal planners (schedule/school state docs) shouldn't pop up."""
    from events import bus, PLANNER_COMPLETED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(PLANNER_COMPLETED, {'planner': 'schedule', 'was_skipped': False})
    assert not popup.calls
    bus.clear()


def test_dispatcher_emits_metrics(pebble_home):
    """Submitting a fired notification writes a metrics row."""
    import metrics
    from planners.dispatcher import NotificationDispatcher, Notification
    popup = _FakePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.submit(Notification(title='A', body=''))

    lines = metrics.path().read_text(encoding='utf-8').strip().splitlines()
    import json
    events = [json.loads(l) for l in lines]
    assert any(e['event'] == 'notification.fired' for e in events)
