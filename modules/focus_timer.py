"""Focus Timer module — Pomodoro sessions with state persisted to disk."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import ActionTier, PebbleModule

_STATE_PATH = Path.home() / '.pebble' / 'focus_state.json'

_DEFAULT_STATE: dict = {
    'active':             False,
    'start_time':         None,
    'session_type':       'work',    # 'work' | 'break' | 'long_break'
    'sessions_completed': 0,
    'task':               '',
}


# ── module-level helper (used by proactive_engine) ────────────────────────────

def get_focus_state() -> dict:
    """Return current focus state dict. Safe to call from outside the module."""
    if not _STATE_PATH.exists():
        return dict(_DEFAULT_STATE)
    try:
        data = json.loads(_STATE_PATH.read_text(encoding='utf-8'))
        return {**_DEFAULT_STATE, **data}
    except Exception:
        return dict(_DEFAULT_STATE)


# ── module ────────────────────────────────────────────────────────────────────

class FocusTimerModule(PebbleModule):
    name         = 'focus'
    display_name = 'Focus Timer'
    description  = 'Pomodoro focus sessions with break reminders — stay in flow'
    icon         = '⏱️'

    _default_tiers = {
        'status':   ActionTier.AUTO,
        'start':    ActionTier.NOTIFY,
        'stop':     ActionTier.NOTIFY,
        'complete': ActionTier.NOTIFY,
    }

    config_fields = [
        {'key': 'work_minutes',       'label': 'Work session length (default: 25)',           'type': 'text'},
        {'key': 'break_minutes',      'label': 'Short break length (default: 5)',             'type': 'text'},
        {'key': 'long_break_minutes', 'label': 'Long break after 4 sessions (default: 15)',   'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'focus'

    def tool_description(self) -> str:
        return (
            'Manage Pomodoro focus sessions. Start a timed work session, '
            'check status, mark it complete (triggers break timer), or stop early.'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['start', 'stop', 'status', 'complete'],
                    'description': (
                        '"start" — begin a focus session, '
                        '"status" — check elapsed/remaining time, '
                        '"complete" — finish session and start break, '
                        '"stop" — cancel session'
                    ),
                },
                'task': {
                    'type': 'string',
                    'description': 'What you are working on (used with action="start")',
                },
                'minutes': {
                    'type': 'integer',
                    'description': 'Override session duration in minutes (optional)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = 'status', task: str = '', minutes=None, **_) -> str:
        action = action.strip().lower()
        try:
            if action == 'start':
                return self._start(task, minutes)
            if action == 'status':
                return self._status()
            if action == 'stop':
                return self._stop()
            if action == 'complete':
                return self._complete()
            return f'Unknown action "{action}". Valid actions: start, stop, status, complete.'
        except Exception as e:
            return f'Focus timer error: {str(e)}'

    # ── actions ───────────────────────────────────────────────────────────────

    def _start(self, task: str, minutes) -> str:
        work_mins = self._parse_minutes(minutes, 'work_minutes', 25)
        state = get_focus_state()
        completed = state.get('sessions_completed', 0)

        new_state = {
            'active':             True,
            'start_time':         datetime.now(tz=timezone.utc).isoformat(),
            'session_type':       'work',
            'sessions_completed': completed,
            'task':               task.strip() or 'Focus session',
        }
        self._save_state(new_state)
        task_label = task.strip() or 'your task'
        return (
            f'🎯 Focus session started! Working on: {task_label}\n'
            f'{work_mins} minutes — Pebble will remind you when it\'s time for a break.'
        )

    def _status(self) -> str:
        state = get_focus_state()
        if not state.get('active'):
            return "No active focus session. Say 'start focus' to begin."

        task         = state.get('task', 'Focus session')
        session_type = state.get('session_type', 'work')
        start_str    = state.get('start_time')

        if not start_str:
            return 'Focus session active (start time unknown).'

        try:
            start_dt = datetime.fromisoformat(start_str)
            now      = datetime.now(tz=timezone.utc)
            elapsed  = int((now - start_dt).total_seconds() // 60)
        except Exception:
            return 'Focus session active (could not calculate time).'

        total_mins = self._session_duration(session_type)
        remaining  = max(0, total_mins - elapsed)

        label = {'work': 'Working on', 'break': 'Break — was working on', 'long_break': 'Long break — was working on'}.get(session_type, 'Focusing on')
        return (
            f'⏱ {label}: {task}\n'
            f'{elapsed}m elapsed, {remaining}m remaining'
        )

    def _stop(self) -> str:
        state = get_focus_state()
        completed = state.get('sessions_completed', 0)
        self._save_state({**_DEFAULT_STATE, 'sessions_completed': completed})
        return 'Focus session ended. Good work!'

    def _complete(self) -> str:
        state     = get_focus_state()
        completed = state.get('sessions_completed', 0) + 1
        task      = state.get('task', 'Focus session')

        # Determine next break type
        long_break  = (completed % 4 == 0)
        break_type  = 'long_break' if long_break else 'break'
        break_mins  = self._parse_minutes(None, 'long_break_minutes' if long_break else 'break_minutes', 15 if long_break else 5)
        break_label = 'long ' if long_break else ''

        new_state = {
            'active':             True,
            'start_time':         datetime.now(tz=timezone.utc).isoformat(),
            'session_type':       break_type,
            'sessions_completed': completed,
            'task':               task,
        }
        self._save_state(new_state)

        return (
            f'🎉 Session complete! Sessions today: {completed}\n'
            f'Time for a {break_label}break ({break_mins} min).'
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _parse_minutes(self, override, cfg_key: str, default: int) -> int:
        if override is not None:
            try:
                return max(1, int(override))
            except (TypeError, ValueError):
                pass
        raw = self.cfg.get(cfg_key, '').strip()
        try:
            return max(1, int(raw))
        except (ValueError, AttributeError):
            return default

    def _session_duration(self, session_type: str) -> int:
        mapping = {
            'work':       ('work_minutes',       25),
            'break':      ('break_minutes',        5),
            'long_break': ('long_break_minutes',  15),
        }
        cfg_key, default = mapping.get(session_type, ('work_minutes', 25))
        return self._parse_minutes(None, cfg_key, default)

    @staticmethod
    def _save_state(state: dict):
        try:
            _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _STATE_PATH.write_text(
                json.dumps(state, indent=2, default=str),
                encoding='utf-8',
            )
        except Exception:
            pass
