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


def test_critical_fire_does_not_consume_noncritical_rate_budget(pebble_home):
    """A critical notification must NOT poison the non-critical rate window —
    otherwise (with reminders/meeting-prep/calendar all critical now) queued
    non-critical notifications would never drain and be lost."""
    from planners.dispatcher import NotificationDispatcher, Notification
    t = [1000.0]
    popup = _CapturePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=1,
                               quiet_hours_start='99:99', quiet_hours_end='99:99',
                               clock=lambda: t[0])
    # a critical fires but must not consume the single non-critical slot
    assert d.submit(Notification(title='C', body='', urgency='critical')) == 'fired'
    # ... so a following normal notification still fires immediately
    assert d.submit(Notification(title='N', body='', urgency='normal')) == 'fired'
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
                {'planner': 'comms', 'state_doc': 'x.json', 'was_skipped': False})
    assert popup.calls and 'draft' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_planner_completed_skipped_is_silent(pebble_home):
    from events import bus, PLANNER_COMPLETED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(PLANNER_COMPLETED, {'planner': 'comms', 'was_skipped': True})
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


def _quiet_dispatcher(popup):
    """A dispatcher whose clock is fixed at 23:30 (inside default quiet hours),
    so only critical notifications fire — deterministic, no real-time dependence."""
    from planners.dispatcher import NotificationDispatcher
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=1,
                               quiet_hours_start='22:00', quiet_hours_end='07:00',
                               now_fn=lambda: datetime.datetime(2026, 5, 10, 23, 30))
    d.start()
    return d


def test_reminder_fires_even_in_quiet_hours(pebble_home):
    """Reminders are user-set and time-critical — they must NOT be suppressed by
    quiet hours/rate-limit (which would lose them permanently, since the watcher
    marks them done on publish)."""
    from events import bus, REMINDER_DUE
    popup = _CapturePopup()
    _quiet_dispatcher(popup)
    bus.publish(REMINDER_DUE, {'reminder': {'id': 'r1', 'text': 'meds'}})
    assert popup.calls and 'meds' in popup.calls[-1]['body']
    bus.clear()


def test_meeting_prep_and_focus_warning_fire_in_quiet_hours(pebble_home):
    """Time-critical, user-driven warnings bypass quiet hours."""
    from events import bus, MEETING_PREP_DUE, FOCUS_ENDING_SOON
    popup = _CapturePopup()
    _quiet_dispatcher(popup)
    bus.publish(MEETING_PREP_DUE, {'title': 'Standup', 'minutes_away': 10})
    bus.publish(FOCUS_ENDING_SOON, {'session_type': 'work'})
    titles = ' '.join(c['title'] for c in popup.calls)
    assert 'Standup' in titles and '1 min' in titles.lower()
    bus.clear()


def test_reminder_and_focus_end_popups_persist(pebble_home):
    """auto_dismiss_ms=0 (persist until dismissed) is plumbed through for
    reminders and focus-end, restoring the pre-cutover behavior."""
    from events import bus, REMINDER_DUE, FOCUS_SESSION_ENDED
    popup = _CapturePopup()
    _quiet_dispatcher(popup)   # reminder is critical -> fires; focus-end uses _fire
    bus.publish(REMINDER_DUE, {'reminder': {'id': 'r1', 'text': 'meds'}})
    assert popup.calls[-1]['metadata'].get('auto_dismiss_ms') == 0
    bus.publish(FOCUS_SESSION_ENDED, {'session_type': 'work', 'task': 't'})
    assert popup.calls[-1]['metadata'].get('auto_dismiss_ms') == 0
    bus.clear()


def test_calendar_body_keeps_pluralization_and_location(pebble_home):
    from planners.dispatcher import NotificationDispatcher, Notification  # noqa
    from events import bus, CALENDAR_EVENT_APPROACHING
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(CALENDAR_EVENT_APPROACHING,
                {'event_id': 'e1', 'title': 'Lecture', 'minutes_away': 1, 'location': 'Room 4B'})
    body = popup.calls[-1]['body']
    assert 'In 1 minute' in body and 'Room 4B' in body   # not 'In 1 min', location kept
    bus.clear()


def test_dead_planner_keys_are_silent_real_ones_fire(pebble_home):
    """morning/exam_prep are not BasePlanners (never publish PLANNER_COMPLETED);
    schedule is internal. Only comms/school should pop up."""
    from events import bus, PLANNER_COMPLETED
    popup = _CapturePopup()
    _open_dispatcher(popup)
    for name in ('schedule', 'morning', 'exam_prep'):
        bus.publish(PLANNER_COMPLETED, {'planner': name, 'was_skipped': False})
    assert not popup.calls
    bus.publish(PLANNER_COMPLETED, {'planner': 'comms', 'was_skipped': False})
    assert popup.calls and 'draft' in popup.calls[-1]['title'].lower()
    bus.clear()


def test_quiet_hours_defers_then_catches_up(pebble_home):
    """A non-critical notification arriving in quiet hours is DEFERRED (not
    dropped), and a single catch-up fires when quiet hours end — so an opted-in
    email/Slack toast is never silently lost."""
    import datetime
    from planners.dispatcher import NotificationDispatcher, Notification

    state = {'quiet': True}

    def now_fn():
        return datetime.datetime(2026, 5, 10, 23 if state['quiet'] else 9, 30)

    popup = _CapturePopup()
    d = NotificationDispatcher(popup_fn=popup, max_per_10min=10,
                              quiet_hours_start='22:00', quiet_hours_end='07:00',
                              now_fn=now_fn)
    # during quiet hours: deferred, no popup yet
    assert d.submit(Notification(title='📧 boss', body='urgent', urgency='high')) == 'suppressed:quiet_hours'
    assert not popup.calls
    d.flush_queue()               # prime the was-quiet state (still quiet -> no catch-up)
    assert not popup.calls
    # quiet hours end
    state['quiet'] = False
    d.flush_queue()               # transition -> fire one catch-up
    assert popup.calls and 'away' in popup.calls[-1]['title'].lower()


def test_email_event_routed_to_popup(pebble_home):
    from events import bus, EMAIL_RECEIVED_IMPORTANT
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(EMAIL_RECEIVED_IMPORTANT,
                {'sender': 'Prof Smith', 'subject': 'HW3 office hours', 'message_id': 'm1'})
    assert popup.calls
    assert 'Prof Smith' in popup.calls[-1]['title']
    assert 'HW3' in popup.calls[-1]['body']
    bus.clear()


def test_slack_event_routed_to_popup(pebble_home):
    from events import bus, SLACK_MESSAGE_IMPORTANT
    popup = _CapturePopup()
    _open_dispatcher(popup)
    bus.publish(SLACK_MESSAGE_IMPORTANT,
                {'workspace': 'acme', 'channel_name': 'general',
                 'user_name': 'Sam', 'text': 'ship it', 'ts': '123.45'})
    assert popup.calls
    assert 'general' in popup.calls[-1]['title'] and 'Sam' in popup.calls[-1]['title']
    assert 'ship it' in popup.calls[-1]['body']
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
