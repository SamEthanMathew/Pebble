"""Send-with-delay queue.

For user-approved ASK-tier outbound actions (gmail.send, slack.send, etc.):
the actual API call is delayed by config.send_delay_seconds (default 60s).
A toast/notification shows a Cancel button. If the user cancels before the
deadline, no API call fires.

NOTE: Drafts (NOTIFY tier) do not go through this queue — they fire immediately.
Only ASK-tier user-approved outbound passes through.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import audit
import metrics


DEFAULT_DELAY_SECONDS = 60


@dataclass
class QueuedSend:
    id:           str
    fire_at:      float                          # epoch seconds
    fn:           Callable[[], Any]              # the actual send
    label:        str                            # human-readable, e.g. "Gmail send to professor@cmu.edu"
    metadata:     dict[str, Any] = field(default_factory=dict)
    canceled:     bool = False
    fired:        bool = False
    fire_result:  Any = None
    fire_error:   str | None = None


class ApprovalQueue:
    """Fire-after-delay queue with cancellation."""

    def __init__(self, *, delay_seconds: int = DEFAULT_DELAY_SECONDS,
                 clock: Callable[[], float] = time.time):
        self._delay = max(1, int(delay_seconds))
        self._clock = clock
        self._items: dict[str, QueuedSend] = {}
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}

    def enqueue(self, fn: Callable[[], Any], *, label: str,
                metadata: dict[str, Any] | None = None) -> str:
        item_id = uuid.uuid4().hex[:12]
        item = QueuedSend(
            id=item_id, fire_at=self._clock() + self._delay,
            fn=fn, label=label, metadata=dict(metadata or {}),
        )
        with self._lock:
            self._items[item_id] = item

        # Schedule the actual fire on a Timer
        timer = threading.Timer(self._delay, self._fire, args=(item_id,))
        timer.daemon = True
        timer.start()
        with self._lock:
            self._timers[item_id] = timer

        audit.append({
            'module':  'approval_queue', 'action': 'enqueue',
            'args':    {'label': label, 'delay_s': self._delay},
            'result':  {'id': item_id},
            'tier':    'auto', 'source': 'autonomy',
        })
        metrics.emit('send.queued',
                     {'id': item_id, 'label': label, 'delay_s': self._delay})
        return item_id

    def cancel(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            timer = self._timers.pop(item_id, None)
        if item is None or item.fired or item.canceled:
            return False
        if timer is not None:
            timer.cancel()
        item.canceled = True
        audit.append({
            'module':  'approval_queue', 'action': 'cancel',
            'args':    {'id': item_id, 'label': item.label},
            'result':  {'canceled': True},
            'tier':    'auto', 'source': 'user',
        })
        metrics.emit('send.canceled', {'id': item_id, 'label': item.label})
        return True

    def status(self, item_id: str) -> str:
        """Returns 'pending' | 'fired' | 'canceled' | 'error' | 'unknown'."""
        with self._lock:
            item = self._items.get(item_id)
        if item is None:
            return 'unknown'
        if item.canceled:
            return 'canceled'
        if item.fire_error:
            return 'error'
        if item.fired:
            return 'fired'
        return 'pending'

    def get(self, item_id: str) -> QueuedSend | None:
        with self._lock:
            return self._items.get(item_id)

    def pending(self) -> list[QueuedSend]:
        with self._lock:
            return [i for i in self._items.values() if not i.fired and not i.canceled]

    def fire_now(self, item_id: str) -> bool:
        """Fire immediately, bypassing the delay. Used for tests and "send now" UX."""
        with self._lock:
            timer = self._timers.pop(item_id, None)
        if timer is not None:
            timer.cancel()
        return self._fire(item_id)

    # ── internal ──────────────────────────────────────────────────────────────

    def _fire(self, item_id: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
        if item is None or item.canceled or item.fired:
            return False
        try:
            item.fire_result = item.fn()
            item.fired = True
            audit.append({
                'module':  'approval_queue', 'action': 'fired',
                'args':    {'id': item_id, 'label': item.label},
                'result':  {'output_preview': str(item.fire_result)[:300] if item.fire_result is not None else None},
                'tier':    'auto', 'source': 'approval_queue',
            })
            metrics.emit('send.fired', {'id': item_id, 'label': item.label})
            return True
        except Exception as e:
            item.fire_error = str(e)
            audit.append({
                'module':  'approval_queue', 'action': 'fire_failed',
                'args':    {'id': item_id, 'label': item.label},
                'result':  {'error': str(e)},
                'tier':    'auto', 'source': 'approval_queue',
            })
            metrics.emit('send.fire_failed', {'id': item_id, 'label': item.label, 'error': str(e)})
            return False
