"""Idle/active detection: deterministic tick semantics."""

from __future__ import annotations


def test_get_idle_seconds_non_windows_returns_zero(monkeypatch):
    """On non-Windows platforms, get_idle_seconds returns 0.0 gracefully."""
    import idle_detect, sys
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert idle_detect.get_idle_seconds() == 0.0


def test_idle_transition_publishes_event(pebble_home):
    """When idle threshold crosses, USER_IDLE event fires once."""
    from events import bus, USER_IDLE
    from idle_detect import IdleWatcher

    captured = []
    bus.subscribe(USER_IDLE, lambda p: captured.append(p))

    seconds = [0.0]
    w = IdleWatcher(idle_after_seconds=60, poll_interval=999,
                    idle_seconds_fn=lambda: seconds[0])
    w.tick()
    assert captured == []  # below threshold
    seconds[0] = 90.0
    w.tick()
    assert len(captured) == 1
    seconds[0] = 120.0
    w.tick()  # already idle — should NOT re-publish
    assert len(captured) == 1


def test_active_transition_publishes_event(pebble_home):
    from events import bus, USER_ACTIVE
    from idle_detect import IdleWatcher

    captured = []
    bus.subscribe(USER_ACTIVE, lambda p: captured.append(p))

    seconds = [120.0]  # start idle
    w = IdleWatcher(idle_after_seconds=60, poll_interval=999,
                    idle_seconds_fn=lambda: seconds[0])
    w.tick()  # establishes idle
    seconds[0] = 5.0
    w.tick()
    assert len(captured) == 1
    assert captured[0]['idle_seconds_at_resume'] == 5.0


def test_no_event_when_already_active(pebble_home):
    from events import bus, USER_IDLE, USER_ACTIVE
    from idle_detect import IdleWatcher

    idle_calls, active_calls = [], []
    bus.subscribe(USER_IDLE,   lambda p: idle_calls.append(p))
    bus.subscribe(USER_ACTIVE, lambda p: active_calls.append(p))

    seconds = [10.0]
    w = IdleWatcher(idle_after_seconds=60, poll_interval=999,
                    idle_seconds_fn=lambda: seconds[0])
    for _ in range(5):
        w.tick()
    assert idle_calls == []
    assert active_calls == []  # no transition
