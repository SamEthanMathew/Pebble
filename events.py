"""Pebble event bus — lightweight in-process pub/sub.

Per docs/contracts.md §8. Replaces direct cross-module coupling in proactive_engine.py:
modules publish; planners + dispatcher subscribe. Subscriber failure must not
crash the publisher.
"""

from __future__ import annotations

import sys
import threading
from collections import defaultdict
from typing import Any, Callable

import audit

# ── Event type catalog (per contracts.md §8) ──────────────────────────────────

CALENDAR_EVENT_APPROACHING = 'calendar.event_approaching'
EMAIL_RECEIVED_IMPORTANT   = 'email.received_important'
EMAIL_RECEIVED_UNKNOWN     = 'email.received_unknown'
TASK_DUE_SOON              = 'task.due_soon'
REMINDER_DUE               = 'reminder.due'
FOCUS_SESSION_STARTED      = 'focus.started'
FOCUS_SESSION_ENDED        = 'focus.ended'
FOCUS_ENDING_SOON          = 'focus.ending_soon'      # ~1 min left
USER_ACTIVE                = 'user.active'
USER_IDLE                  = 'user.idle'
ENTITY_UPDATED             = 'entity.updated'
PLANNER_COMPLETED          = 'planner.completed'
STATE_DOC_UPDATED          = 'state_doc.updated'
MORNING_BRIEFING_DUE       = 'morning.briefing_due'
MEETING_PREP_DUE           = 'meeting.prep_due'

ALL_EVENT_TYPES = {
    CALENDAR_EVENT_APPROACHING,
    EMAIL_RECEIVED_IMPORTANT,
    EMAIL_RECEIVED_UNKNOWN,
    TASK_DUE_SOON,
    REMINDER_DUE,
    FOCUS_SESSION_STARTED,
    FOCUS_SESSION_ENDED,
    FOCUS_ENDING_SOON,
    USER_ACTIVE,
    USER_IDLE,
    ENTITY_UPDATED,
    PLANNER_COMPLETED,
    STATE_DOC_UPDATED,
    MORNING_BRIEFING_DUE,
    MEETING_PREP_DUE,
}


# ── Bus ───────────────────────────────────────────────────────────────────────

class PebbleEventBus:
    """Synchronous in-process pub/sub. Handler exceptions are isolated."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Callable[[dict[str, Any]], None]]] = defaultdict(list)
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a callback. Unknown event types are still allowed (logged to stderr)."""
        if event_type not in ALL_EVENT_TYPES:
            print(f'[events] subscribe to unknown event type: {event_type!r}', file=sys.stderr)
        with self._lock:
            self._subs[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            try:
                self._subs[event_type].remove(callback)
            except ValueError:
                pass

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Fire an event. Each subscriber runs in a try/except; failures audit."""
        payload = data or {}
        with self._lock:
            handlers = list(self._subs.get(event_type, ()))
        for handler in handlers:
            try:
                handler(payload)
            except Exception as e:
                audit.append({
                    'module':  'events',
                    'action':  'handler_error',
                    'args':    {'event_type': event_type, 'handler': getattr(handler, '__name__', repr(handler))},
                    'result':  {'error': str(e), 'error_type': type(e).__name__},
                    'tier':    'auto',
                    'source':  'event_bus',
                })

    def subscriber_count(self, event_type: str) -> int:
        with self._lock:
            return len(self._subs.get(event_type, ()))

    def clear(self) -> None:
        """Drop all subscriptions. Intended for tests."""
        with self._lock:
            self._subs.clear()


# Process-wide singleton; modules `from events import bus, EMAIL_RECEIVED_IMPORTANT`
bus = PebbleEventBus()
