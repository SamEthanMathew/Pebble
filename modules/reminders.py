"""Reminders module — set timed reminders that the proactive engine can fire."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import crab_config
from .base import PebbleModule

_REMINDERS_PATH = Path.home() / '.pebble' / 'reminders.json'


# ── module-level helper (used by proactive_engine) ────────────────────────────

def get_due_reminders() -> list[dict]:
    """Return reminders where remind_at <= now and not done."""
    reminders = _load_reminders()
    now = datetime.now(tz=timezone.utc)
    due = []
    for r in reminders:
        if r.get('done'):
            continue
        remind_at = _parse_iso(r.get('remind_at', ''))
        if remind_at and remind_at <= now:
            due.append(r)
    return due


# ── module ────────────────────────────────────────────────────────────────────

class RemindersModule(PebbleModule):
    name         = 'reminders'
    display_name = 'Reminders'
    description  = 'Set timed reminders — Pebble will ping you at the right moment'
    icon         = '🔔'
    config_fields: list[dict] = []

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'reminder'

    def tool_description(self) -> str:
        return (
            'Set, list, or cancel timed reminders. '
            'Supports natural language times like "in 30 minutes", "at 3pm", "tomorrow morning".'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['set', 'list', 'cancel'],
                    'description': '"set" — create a reminder, "list" — upcoming reminders, "cancel" — delete one',
                },
                'text': {
                    'type': 'string',
                    'description': 'Reminder message (used with set / cancel)',
                },
                'when': {
                    'type': 'string',
                    'description': 'When to remind — e.g. "in 30 minutes", "at 3pm", "tomorrow morning"',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = 'list', text: str = '', when: str = '', **_) -> str:
        action = action.strip().lower()
        try:
            if action == 'set':
                return self._set(text, when)
            if action == 'list':
                return self._list()
            if action == 'cancel':
                return self._cancel(text)
            return f'Unknown action "{action}". Valid actions: set, list, cancel.'
        except Exception as e:
            return f'Reminders error: {str(e)}'

    # ── actions ───────────────────────────────────────────────────────────────

    def _set(self, text: str, when: str) -> str:
        if not text.strip():
            return 'No reminder text provided.'
        if not when.strip():
            return 'No time provided — try "in 30 minutes", "at 3pm", "tomorrow morning".'

        remind_at = _parse_when(when)
        if remind_at is None:
            return (
                f'I didn\'t understand "{when}" — '
                'try "in 30 minutes", "at 3pm", "tomorrow morning".'
            )

        reminders = _load_reminders()
        reminder  = {
            'id':        str(uuid.uuid4()),
            'text':      text.strip(),
            'remind_at': remind_at.isoformat(),
            'done':      False,
            'created':   datetime.now(tz=timezone.utc).isoformat(),
        }
        reminders.append(reminder)
        _save_reminders(reminders)

        formatted = _format_remind_at(remind_at)
        return f'🔔 Reminder set! I\'ll ping you {formatted}: {text.strip()}'

    def _list(self) -> str:
        reminders = _load_reminders()
        now       = datetime.now(tz=timezone.utc)
        upcoming  = []
        for r in reminders:
            if r.get('done'):
                continue
            remind_at = _parse_iso(r.get('remind_at', ''))
            if remind_at and remind_at > now:
                upcoming.append((remind_at, r))

        if not upcoming:
            return 'No upcoming reminders.'

        upcoming.sort(key=lambda x: x[0])
        lines = ['Upcoming reminders:']
        for i, (remind_at, r) in enumerate(upcoming, 1):
            time_label = _format_remind_at(remind_at)
            wall_clock = remind_at.astimezone().strftime('%-I:%M %p') if hasattr(remind_at, 'strftime') else ''
            try:
                # Windows-safe strftime (no %-I)
                wall_clock = remind_at.astimezone().strftime('%I:%M %p').lstrip('0')
            except Exception:
                wall_clock = remind_at.isoformat()
            lines.append(f'{i}. {r["text"]} — {time_label} ({wall_clock})')
        return '\n'.join(lines)

    def _cancel(self, text: str) -> str:
        if not text.strip():
            return 'Provide the reminder text to cancel.'

        reminders    = _load_reminders()
        query        = text.strip().lower()
        original_len = len(reminders)

        # Try exact match first, then partial
        match_exact   = [r for r in reminders if r.get('text', '').lower() == query]
        match_partial = [r for r in reminders if query in r.get('text', '').lower()]
        candidates    = match_exact or match_partial

        if not candidates:
            return f'Reminder not found: "{text.strip()}"'

        # Cancel the first (soonest) match
        target_id = candidates[0]['id']
        cancelled_text = candidates[0]['text']
        reminders = [r for r in reminders if r['id'] != target_id]
        _save_reminders(reminders)
        return f'Reminder cancelled: {cancelled_text}'


# ── private helpers ──────────────────────────────────────────────────────────

def _load_reminders() -> list[dict]:
    if not _REMINDERS_PATH.exists():
        return []
    try:
        return json.loads(_REMINDERS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return []


def _save_reminders(reminders: list[dict]):
    try:
        _REMINDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REMINDERS_PATH.write_text(
            json.dumps(reminders, indent=2, default=str),
            encoding='utf-8',
        )
    except Exception:
        pass


def _parse_iso(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _parse_when(when_str: str) -> datetime | None:
    """Parse natural language time expressions into an aware datetime."""
    now    = datetime.now(tz=timezone.utc)
    s      = when_str.strip().lower()

    # ── relative: "in N minutes/hours/days" ──────────────────────────────────
    if s.startswith('in '):
        parts = s[3:].split()
        if len(parts) >= 2:
            try:
                n    = int(parts[0])
                unit = parts[1].rstrip('s')  # strip plural
                if unit in ('minute', 'min'):
                    return now + timedelta(minutes=n)
                if unit in ('hour', 'hr'):
                    return now + timedelta(hours=n)
                if unit == 'day':
                    return now + timedelta(days=n)
            except ValueError:
                pass

    # ── "at HH:MM" or "at Xpm/Xam" ──────────────────────────────────────────
    if s.startswith('at '):
        time_str = s[3:].strip()
        parsed   = _parse_time_str(time_str, now)
        if parsed:
            # If the time is already past today, schedule for tomorrow
            if parsed <= now:
                parsed += timedelta(days=1)
            return parsed

    # ── "tonight at X" ───────────────────────────────────────────────────────
    if s.startswith('tonight at '):
        time_str = s[len('tonight at '):].strip()
        parsed   = _parse_time_str(time_str, now)
        if parsed:
            if parsed <= now:
                parsed += timedelta(days=1)
            return parsed

    # ── "tonight" alone → 8pm today ──────────────────────────────────────────
    if s == 'tonight':
        return _set_time(now, 20, 0)

    # ── "tomorrow at X" or "tomorrow morning/afternoon/evening/night" ─────────
    if s.startswith('tomorrow'):
        tomorrow = now + timedelta(days=1)
        rest     = s[len('tomorrow'):].strip()
        if rest.startswith('at '):
            parsed = _parse_time_str(rest[3:].strip(), now)
            if parsed:
                return _set_time(tomorrow, parsed.hour, parsed.minute)
        if 'morning' in rest:
            return _set_time(tomorrow, 9, 0)
        if 'afternoon' in rest:
            return _set_time(tomorrow, 14, 0)
        if 'evening' in rest or 'night' in rest:
            return _set_time(tomorrow, 19, 0)
        # plain "tomorrow"
        return _set_time(tomorrow, 9, 0)

    # ── "morning" / "this morning" etc. ──────────────────────────────────────
    if 'morning' in s:
        candidate = _set_time(now, 9, 0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if 'afternoon' in s:
        candidate = _set_time(now, 14, 0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if 'evening' in s or 'tonight' in s:
        candidate = _set_time(now, 19, 0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    return None


def _parse_time_str(time_str: str, reference: datetime) -> datetime | None:
    """Parse '3pm', '3:30pm', '15:00', '3:30 pm' → datetime on reference's date."""
    import re
    time_str = time_str.strip().lower().replace(' ', '')

    # Pattern: digits with optional colon+minutes, optional am/pm
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', time_str)
    if not m:
        return None

    hour    = int(m.group(1))
    minute  = int(m.group(2)) if m.group(2) else 0
    period  = m.group(3)

    if period == 'pm' and hour != 12:
        hour += 12
    elif period == 'am' and hour == 12:
        hour = 0

    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None

    return _set_time(reference, hour, minute)


def _set_time(base: datetime, hour: int, minute: int) -> datetime:
    """Return base datetime with time replaced, preserving timezone."""
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _format_remind_at(dt: datetime) -> str:
    """Return a human-friendly relative label like 'in 2 hours' or 'tomorrow at 9am'."""
    now   = datetime.now(tz=timezone.utc)
    delta = dt - now
    secs  = int(delta.total_seconds())

    if secs < 0:
        return 'now'
    if secs < 3600:
        m = max(1, secs // 60)
        return f'in {m} minute{"s" if m != 1 else ""}'
    if secs < 86400:
        h = secs // 3600
        return f'in {h} hour{"s" if h != 1 else ""}'

    days = secs // 86400
    if days == 1:
        try:
            local_dt = dt.astimezone()
            time_str = local_dt.strftime('%I:%M %p').lstrip('0')
            return f'tomorrow at {time_str}'
        except Exception:
            return 'tomorrow'
    return f'in {days} days'
