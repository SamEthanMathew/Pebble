"""Milestone B / P0-1 — the headless PebbleEngine seam.

The engine is where all lifecycle + business logic moves so that each platform
shell (Windows tray today; web/mac later) is a thin client. Its defining
invariant: importing it must pull in NO GUI/OS toolkit. This guard makes that
permanent — if someone imports tkinter/pywebview/pynput from the engine (or a
module it depends on), a phone/web/headless client can no longer reuse it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def test_importing_engine_pulls_in_no_gui_toolkit():
    """A fresh interpreter importing pebble_engine must not load tkinter /
    pywebview / pynput (the GUI/input toolkits the Windows shell owns)."""
    code = (
        "import sys; import pebble_engine; "
        "bad=[m for m in ('tkinter','_tkinter','pywebview','webview','pynput') "
        "if m in sys.modules]; "
        "print('LOADED:'+','.join(bad)); "
        "sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, cwd=str(_REPO))
    assert r.returncode == 0, f'engine imported GUI toolkit: {r.stdout}\n{r.stderr}'


def test_pebble_api_pulls_in_no_gui_toolkit():
    """The local API must be headless — it's what a web/phone client imports."""
    code = (
        "import sys; import pebble_api; "
        "bad=[m for m in ('tkinter','_tkinter','pywebview','webview','pynput') "
        "if m in sys.modules]; "
        "print('LOADED:'+','.join(bad)); "
        "sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, cwd=str(_REPO))
    assert r.returncode == 0, f'pebble_api imported GUI toolkit: {r.stdout}\n{r.stderr}'


def test_proactive_engine_pulls_in_no_gui_toolkit():
    """proactive_engine is now publish-only + headless — importing it must not
    load any GUI toolkit (it used to import tkinter for the popup rendering)."""
    code = (
        "import sys; import proactive_engine; "
        "bad=[m for m in ('tkinter','_tkinter','pywebview','webview','pynput') "
        "if m in sys.modules]; "
        "print('LOADED:'+','.join(bad)); "
        "sys.exit(1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, '-c', code],
                       capture_output=True, text=True, cwd=str(_REPO))
    assert r.returncode == 0, f'proactive_engine imported GUI toolkit: {r.stdout}\n{r.stderr}'


def test_engine_routes_bus_events_to_injected_notifier(pebble_home):
    """The engine wires NotificationDispatcher as the notification consumer with
    a shell-injected popup_fn — proving notifications flow headlessly (the P0-2
    seam), no Tk required. A critical calendar event bypasses gating and fires."""
    import pebble_engine
    from events import bus, CALENDAR_EVENT_APPROACHING

    fired = []
    eng = pebble_engine.PebbleEngine(notifier=lambda **kw: fired.append(kw))
    eng.start()
    try:
        bus.publish(CALENDAR_EVENT_APPROACHING,
                    {'title': 'Standup', 'minutes_away': 1, 'event_id': 'e1'})
        assert fired, 'engine did not route the event to the injected notifier'
        assert 'Standup' in fired[-1].get('title', '')
    finally:
        eng.stop()


def test_engine_exposes_active_modules_and_autonomy(pebble_home):
    import pebble_engine
    from autonomy import Autonomy

    eng = pebble_engine.PebbleEngine()
    assert isinstance(eng.modules(), list)
    assert isinstance(eng.make_autonomy(), Autonomy)


def test_engine_owns_single_autonomy_and_approvals_funnel(pebble_home):
    """P0-3: one shared Autonomy + ApprovalQueue own the write funnel, and the
    engine exposes the approvals-inbox API a UI will drive."""
    import pebble_engine
    from autonomy import Autonomy
    from approval_queue import ApprovalQueue

    eng = pebble_engine.PebbleEngine()
    assert isinstance(eng.autonomy, Autonomy)
    assert eng.autonomy is eng.autonomy          # single shared instance
    assert isinstance(eng.approvals, ApprovalQueue)
    assert eng.approvals is eng.approvals
    # inbox API starts empty
    assert eng.pending_approvals() == {}
    assert eng.queued_sends() == []


def test_engine_approve_unknown_proposal_is_safe(pebble_home):
    import pebble_engine
    eng = pebble_engine.PebbleEngine()
    res = eng.approve('does-not-exist')
    assert res.status == 'error'


def test_engine_start_drains_the_rate_limit_queue(pebble_home, monkeypatch):
    """The engine must drive flush_queue so rate-limited (queued) notifications
    eventually fire — otherwise the dispatcher silently drops everything past the
    first per-10-min slot (a merge-blocking regression the review caught)."""
    import time
    import pebble_engine

    eng = pebble_engine.PebbleEngine()
    eng._flush_interval = 0.05
    calls = []
    monkeypatch.setattr(eng.notifications, 'flush_queue', lambda: calls.append(1))
    eng.start()
    try:
        time.sleep(0.25)
        assert calls, 'engine did not drain the dispatcher queue'
    finally:
        eng.stop()


def test_start_services_headless_with_no_config(pebble_home):
    """start_services() brings up dispatcher + watchers + proactive without a GUI
    and without crashing when nothing is configured."""
    from events import bus, CALENDAR_EVENT_APPROACHING
    import pebble_engine
    eng = pebble_engine.PebbleEngine()
    eng.start_services()
    try:
        assert eng._started
        assert bus.subscriber_count(CALENDAR_EVENT_APPROACHING) >= 1  # dispatcher wired
    finally:
        eng.stop()


def test_start_services_starts_gmail_watcher_when_configured(pebble_home, monkeypatch):
    import pebble_engine
    import modules
    import modules.gmail as gmail_mod

    started = []

    class _FakeWatcher:
        def __init__(self, **kw):
            self.kw = kw
        def start(self):
            started.append(self.kw)
        def stop(self):
            pass

    class _GmailMod:
        name = 'gmail'
        cfg = {'important_senders': 'boss@x.com, prof@y.edu'}

    monkeypatch.setattr(gmail_mod, 'GmailWatcherThread', _FakeWatcher)
    monkeypatch.setattr(modules, 'get_active_modules', lambda: [_GmailMod()])

    eng = pebble_engine.PebbleEngine()
    eng._start_gmail_watcher(eng._active_modules())
    assert started
    assert 'boss@x.com' in started[0]['important_senders']
    assert eng._watchers and callable(started[0]['on_notification'])


def test_stop_stops_watchers(pebble_home):
    import pebble_engine
    stopped = []

    class _W:
        def start(self): pass
        def stop(self): stopped.append(True)

    eng = pebble_engine.PebbleEngine()
    eng._watchers.append(_W())
    eng.start()
    eng.stop()
    assert stopped == [True]


def test_engine_restart_does_not_double_subscribe(pebble_home):
    """stop()/start() must not re-subscribe the dispatcher (which would double
    every notification)."""
    from events import bus, CALENDAR_EVENT_APPROACHING
    import pebble_engine
    eng = pebble_engine.PebbleEngine()
    eng.start()
    n1 = bus.subscriber_count(CALENDAR_EVENT_APPROACHING)
    eng.stop()
    eng.start()
    n2 = bus.subscriber_count(CALENDAR_EVENT_APPROACHING)
    eng.stop()
    assert n1 == n2, 'restart double-subscribed the dispatcher'
