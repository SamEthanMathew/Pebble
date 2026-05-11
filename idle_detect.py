"""Idle / active detection for Windows.

Wraps Win32 GetLastInputInfo() to discover how long since the last keyboard
or mouse input. A background poll loop publishes USER_IDLE and USER_ACTIVE
events so dispatcher / planners can react.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
import threading
from typing import Callable

import audit
from events import bus, USER_ACTIVE, USER_IDLE


# ── platform probe ────────────────────────────────────────────────────────────

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', ctypes.wintypes.UINT),
        ('dwTime', ctypes.wintypes.DWORD),
    ]


def get_idle_seconds() -> float:
    """Seconds since the last user input. Returns 0.0 on non-Windows or failure."""
    if sys.platform != 'win32':
        return 0.0
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime
        return max(0.0, millis / 1000.0)
    except Exception:
        return 0.0


# ── poll loop ────────────────────────────────────────────────────────────────

class IdleWatcher:
    """Background thread that publishes USER_IDLE / USER_ACTIVE events.

    Args:
        idle_after_seconds: how long without input before declaring idle.
        poll_interval:      how often to check.
        idle_seconds_fn:    override for tests; defaults to get_idle_seconds.
    """

    def __init__(self, *, idle_after_seconds: int = 15 * 60,
                 poll_interval: int = 30,
                 idle_seconds_fn: Callable[[], float] | None = None):
        self._idle_after = max(30, int(idle_after_seconds))
        self._interval   = max(5,  int(poll_interval))
        self._fn         = idle_seconds_fn or get_idle_seconds
        self._stop       = threading.Event()
        self._thread:    threading.Thread | None = None
        self._is_idle:   bool = False

    @property
    def is_idle(self) -> bool:
        return self._is_idle

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name='pebble-idle')
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> None:
        """One poll iteration. Public for deterministic tests."""
        idle_s = self._fn()
        was_idle = self._is_idle
        now_idle = idle_s >= self._idle_after

        if now_idle and not was_idle:
            self._is_idle = True
            bus.publish(USER_IDLE, {'idle_seconds': idle_s, 'threshold_seconds': self._idle_after})
            audit.append({
                'module':  'idle_detect', 'action': 'user_idle',
                'args':    {'idle_seconds': idle_s},
                'result':  {'ok': True}, 'tier': 'auto', 'source': 'idle_detect',
            })
        elif not now_idle and was_idle:
            self._is_idle = False
            bus.publish(USER_ACTIVE, {'idle_seconds_at_resume': idle_s})
            audit.append({
                'module':  'idle_detect', 'action': 'user_active',
                'args':    {'resume_after_seconds': idle_s},
                'result':  {'ok': True}, 'tier': 'auto', 'source': 'idle_detect',
            })

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                pass
            self._stop.wait(self._interval)
