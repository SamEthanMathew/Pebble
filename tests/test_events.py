"""Event bus contract tests."""

from __future__ import annotations



def test_event_types_are_strings():
    from events import (
        CALENDAR_EVENT_APPROACHING, EMAIL_RECEIVED_IMPORTANT,
        TASK_DUE_SOON, REMINDER_DUE, FOCUS_SESSION_ENDED,
        USER_ACTIVE, PLANNER_COMPLETED, ALL_EVENT_TYPES,
    )
    for t in (CALENDAR_EVENT_APPROACHING, EMAIL_RECEIVED_IMPORTANT, TASK_DUE_SOON,
              REMINDER_DUE, FOCUS_SESSION_ENDED, USER_ACTIVE, PLANNER_COMPLETED):
        assert isinstance(t, str)
        assert t in ALL_EVENT_TYPES


def test_pub_sub_roundtrip():
    from events import PebbleEventBus, TASK_DUE_SOON
    bus = PebbleEventBus()
    received = []

    def handler(payload):
        received.append(payload)

    bus.subscribe(TASK_DUE_SOON, handler)
    bus.publish(TASK_DUE_SOON, {'kind': 'overdue', 'tasks': ['x']})
    assert received == [{'kind': 'overdue', 'tasks': ['x']}]


def test_publish_with_no_subscribers_does_not_raise():
    from events import PebbleEventBus, REMINDER_DUE
    bus = PebbleEventBus()
    bus.publish(REMINDER_DUE, {'reminder': {}})  # must not raise


def test_handler_exception_isolated(pebble_home):
    """A throwing subscriber must not affect other subscribers or crash publish."""
    from events import PebbleEventBus, TASK_DUE_SOON
    bus = PebbleEventBus()

    received = []

    def bad(payload):
        raise RuntimeError('boom')

    def good(payload):
        received.append(payload)

    bus.subscribe(TASK_DUE_SOON, bad)
    bus.subscribe(TASK_DUE_SOON, good)
    bus.publish(TASK_DUE_SOON, {'x': 1})  # must not raise
    assert received == [{'x': 1}]

    # And the failure must be audited
    import audit
    rows = audit.tail(10)
    assert any(r['module'] == 'events' and r['action'] == 'handler_error' for r in rows)


def test_unsubscribe():
    from events import PebbleEventBus, USER_ACTIVE
    bus = PebbleEventBus()
    received = []

    def handler(p):
        received.append(p)

    bus.subscribe(USER_ACTIVE, handler)
    bus.publish(USER_ACTIVE, {'a': 1})
    bus.unsubscribe(USER_ACTIVE, handler)
    bus.publish(USER_ACTIVE, {'b': 2})

    assert received == [{'a': 1}]


def test_subscriber_count():
    from events import PebbleEventBus, PLANNER_COMPLETED
    bus = PebbleEventBus()
    assert bus.subscriber_count(PLANNER_COMPLETED) == 0
    bus.subscribe(PLANNER_COMPLETED, lambda p: None)
    bus.subscribe(PLANNER_COMPLETED, lambda p: None)
    assert bus.subscriber_count(PLANNER_COMPLETED) == 2


def test_unknown_event_type_logs_but_succeeds(capsys):
    from events import PebbleEventBus
    bus = PebbleEventBus()
    bus.subscribe('totally.fake.event', lambda p: None)  # warning to stderr
    err = capsys.readouterr().err
    assert 'unknown event type' in err
    bus.publish('totally.fake.event', {})  # still works, just no-op
