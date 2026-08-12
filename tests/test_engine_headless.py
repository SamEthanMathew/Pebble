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
