"""Scheduler for thinking passes.

Each pass has a default cron-like schedule (when it WOULD fire) plus a
config-level enabled flag (off by default, so nothing fires until the user
explicitly opts in). The schedule loop wakes every ~30 min and checks each
pass against the current time + a last-fired ledger that prevents duplicate
firings within the same window.

Configuration shape (in config.json):
    {
      "thinking_passes": {
        "close":     {"enabled": true,  "hour": 21, "minute": 0,
                       "days": "daily"},
        "connect":   {"enabled": false, "hour": 20, "minute": 0,
                       "days": "sun"},
        "drift":     {"enabled": false, "hour": 19, "minute": 0,
                       "days": "sun"},
        "ideas":     {"enabled": false, "hour": 18, "minute": 0,
                       "days": "sun"},
        "emerge":    {"enabled": false, "hour": 19, "minute": 0,
                       "days": "first-sun-of-month"},
        "challenge_on_edit": {"enabled": false}
      }
    }

`days` values: "daily" | "mon"|"tue"|...|"sun" | "first-sun-of-month".

The fire-ledger lives at ~/.pebble/workspace/thinking_schedule.jsonl with a
single key per pass storing the last YYYY-MM-DD it actually fired. Coarse but
enough to prevent same-day double-fire.
"""

from __future__ import annotations

import datetime
import json
import threading
import time
from pathlib import Path
from typing import Any


_LEDGER_PATH = Path.home() / '.pebble' / 'workspace' / 'thinking_schedule.jsonl'

# Defaults (used when config has no entry for a pass)
_DEFAULTS: dict[str, dict[str, Any]] = {
    'close':   {'enabled': False, 'hour': 21, 'minute': 0,  'days': 'daily'},
    'connect': {'enabled': False, 'hour': 20, 'minute': 0,  'days': 'sun'},
    'drift':   {'enabled': False, 'hour': 19, 'minute': 0,  'days': 'sun'},
    'ideas':   {'enabled': False, 'hour': 18, 'minute': 0,  'days': 'sun'},
    'emerge':  {'enabled': False, 'hour': 19, 'minute': 0,  'days': 'first-sun-of-month'},
}

_DAY_NAMES = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


# ── Ledger helpers (last-fire dates per pass) ────────────────────────────────

def _read_ledger() -> dict[str, str]:
    """Return {pass_name: last_fired_date_iso}."""
    if not _LEDGER_PATH.exists():
        return {}
    out: dict[str, str] = {}
    try:
        for line in _LEDGER_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get('pass')
            date = row.get('date')
            if name and date:
                out[name] = date  # last-wins via replay
    except OSError:
        pass
    return out


def _record_fire(pass_name: str, date_iso: str) -> None:
    _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LEDGER_PATH.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'pass': pass_name, 'date': date_iso,
                             'fired_at': datetime.datetime.now()
                                          .astimezone().isoformat(timespec='seconds')},
                            default=str, ensure_ascii=False))
        f.write('\n')


# ── Cron-style predicate ─────────────────────────────────────────────────────

def _is_first_sunday(d: datetime.date) -> bool:
    return d.weekday() == 6 and d.day <= 7


def should_fire(
    pass_name: str,
    *,
    now: datetime.datetime | None = None,
    cfg: dict[str, Any] | None = None,
    last_fired_ledger: dict[str, str] | None = None,
) -> bool:
    """Decide whether `pass_name` should fire now.

    Pure function — `now`, `cfg`, and `last_fired_ledger` are all overridable
    so tests can pin the time and the ledger state.
    """
    if cfg is None:
        cfg = _DEFAULTS.get(pass_name, {})
    if not cfg.get('enabled'):
        return False

    if now is None:
        now = datetime.datetime.now()
    today_iso = now.date().isoformat()

    # Already fired today?
    if last_fired_ledger is None:
        last_fired_ledger = _read_ledger()
    if last_fired_ledger.get(pass_name) == today_iso:
        return False

    # Day-of-week / month rule
    days_rule = cfg.get('days', 'daily').lower()
    if days_rule == 'daily':
        day_ok = True
    elif days_rule in _DAY_NAMES:
        day_ok = (now.weekday() == _DAY_NAMES.index(days_rule))
    elif days_rule == 'first-sun-of-month':
        day_ok = _is_first_sunday(now.date())
    else:
        day_ok = False

    if not day_ok:
        return False

    # Time window: fire if we're within the same hour as scheduled,
    # AT-OR-AFTER the scheduled minute. Allow up to 60min of slack so an
    # off-by-a-bit poll still catches the window.
    target_h = int(cfg.get('hour', 9))
    target_m = int(cfg.get('minute', 0))
    target   = now.replace(hour=target_h, minute=target_m,
                            second=0, microsecond=0)
    delta_min = (now - target).total_seconds() / 60.0
    return 0 <= delta_min <= 60


# ── Loop runner ───────────────────────────────────────────────────────────────

class ThinkingScheduler:
    """Background thread that wakes every `poll_interval` seconds and fires
    enabled thinking passes per their cron rules.

    Off by default — caller must `start()` it. Each pass's `enabled` flag is
    checked at fire time so the user can flip a flag mid-run.
    """

    DEFAULT_POLL_SECONDS = 30 * 60  # 30 min

    def __init__(self, *, poll_seconds: int | None = None):
        self._poll       = poll_seconds or self.DEFAULT_POLL_SECONDS
        self._stop_event = threading.Event()
        self._thread:    threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                          name='thinking-scheduler')
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        # Fire-once-on-start? No — first tick is after the initial wait so a
        # restart at 21:01 doesn't double-fire close on top of a manual /close.
        while not self._stop_event.wait(self._poll):
            try:
                self.tick()
            except Exception:
                # Schedule loop must never crash silently mid-session
                pass

    def tick(self) -> list[str]:
        """One scheduler check. Returns names of passes that fired this tick.

        Public for deterministic tests + scheduled call from other loops.
        """
        try:
            import crab_config
            cfg_root = crab_config.get('thinking_passes', {}) or {}
        except Exception:
            cfg_root = {}

        ledger = _read_ledger()
        fired:  list[str] = []
        now = datetime.datetime.now()
        today_iso = now.date().isoformat()

        for pass_name in ('close', 'connect', 'drift', 'ideas', 'emerge'):
            cfg = {**_DEFAULTS.get(pass_name, {}),
                    **(cfg_root.get(pass_name, {}) or {})}
            if not should_fire(pass_name, now=now, cfg=cfg,
                                last_fired_ledger=ledger):
                continue
            try:
                from .thinking_pass import run_pass
                r = run_pass(pass_name)
                _record_fire(pass_name, today_iso)
                fired.append(pass_name)
                # Update the in-memory ledger so a second pass in the same
                # tick can't fire the same one
                ledger[pass_name] = today_iso
            except Exception:
                # Swallow — failed pass is logged inside run_pass
                pass
        return fired
