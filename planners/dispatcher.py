"""Notification dispatcher — the only thing that decides "popup or no popup, now or queued."

Phase 2: the dispatcher is wired up but does NOT yet replace the default subscribers in
proactive_engine. To activate, call `start()` on a dispatcher instance and either disable
proactive_engine's auto-subscribe OR call `dispatcher.takeover(proactive_engine)` to swap.

Rules (per docs/contracts.md and the plan):
- Max 1 notification per 10min unless urgency=critical (config: notifications.max_per_10min).
- Priority: time-sensitive > action-required > informational.
- Dedup overlapping (meeting prep + meeting email → one keyed by dedup_key).
- Quiet hours suppress (config.schedule.quiet_hours_*) — only critical pass through.
- Focus session: queue all non-critical; on FOCUS_SESSION_ENDED fire one catch-up summary.
- Emits notification.fired/.dismissed/.acted/.suppressed metrics.
"""

from __future__ import annotations

import datetime
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import metrics
from events import (
    bus,
    CALENDAR_EVENT_APPROACHING,
    TASK_DUE_SOON,
    REMINDER_DUE,
    FOCUS_SESSION_STARTED,
    FOCUS_SESSION_ENDED,
    FOCUS_ENDING_SOON,
    PLANNER_COMPLETED,
    MORNING_BRIEFING_DUE,
    MEETING_PREP_DUE,
)


URGENCY_RANK = {'critical': 3, 'high': 2, 'normal': 1, 'low': 0}

# Button specs are label/action/style dicts — NO Tk callables, so the dispatcher
# stays headless. The platform shell's popup_fn maps `action` to a real command
# ('open_chat' -> open the chat window, 'dismiss' -> close).
_ASK_PEBBLE  = {'label': 'Ask Pebble', 'action': 'open_chat', 'style': 'primary'}
_DISMISS     = {'label': 'Dismiss',    'action': 'dismiss',   'style': 'default'}


@dataclass(order=False)
class Notification:
    title:     str
    body:      str
    urgency:   str = 'normal'
    kind:      str = 'misc'      # for metrics + dedup
    dedup_key: str | None = None  # if set, suppresses any later notif with same key
    buttons:   list[dict[str, Any]] = field(default_factory=list)
    metadata:  dict[str, Any] = field(default_factory=dict)

    def rank(self) -> int:
        return URGENCY_RANK.get(self.urgency, 1)


class NotificationDispatcher:
    """Wraps NotificationPopup with a queue and gating rules.

    `popup_fn` is the callable that actually shows a popup; signature matches
    NotificationPopup-like APIs:
        popup_fn(title=..., body=..., buttons=..., metadata=...) -> None

    Pass a real notification renderer in production; tests inject a stub.
    """

    def __init__(self, *, popup_fn: Callable[..., None] | None = None,
                 max_per_10min: int = 1,
                 quiet_hours_start: str = '22:00',
                 quiet_hours_end: str = '07:00',
                 clock: Callable[[], float] = time.time,
                 now_fn: Callable[[], datetime.datetime] = datetime.datetime.now):
        self._popup_fn         = popup_fn or self._default_popup
        self._max_per_10min    = max(1, int(max_per_10min))
        self._quiet_start_str  = quiet_hours_start
        self._quiet_end_str    = quiet_hours_end
        self._clock            = clock
        self._now_fn           = now_fn

        self._queue:            deque[Notification] = deque()
        self._fire_log:         deque[float] = deque()  # timestamps of last fires for rate calc
        self._dedup_seen:       set[str] = set()
        self._focus_active:     bool = False
        self._suppressed_focus: list[Notification] = []
        self._fired_count:      int = 0

    # ── public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Subscribe to events. Idempotent — calling twice double-subscribes; don't."""
        bus.subscribe(CALENDAR_EVENT_APPROACHING, self._on_calendar)
        bus.subscribe(TASK_DUE_SOON,              self._on_tasks)
        bus.subscribe(REMINDER_DUE,               self._on_reminder)
        bus.subscribe(FOCUS_SESSION_STARTED,      self._on_focus_start)
        bus.subscribe(FOCUS_SESSION_ENDED,        self._on_focus_end)
        bus.subscribe(FOCUS_ENDING_SOON,          self._on_focus_soon)
        bus.subscribe(MORNING_BRIEFING_DUE,       self._on_morning)
        bus.subscribe(MEETING_PREP_DUE,           self._on_meeting_prep)
        bus.subscribe(PLANNER_COMPLETED,          self._on_planner_completed)

    def submit(self, notif: Notification) -> str:
        """Submit a notification for routing. Returns one of:
            'fired', 'queued', 'suppressed:dedup',
            'suppressed:quiet_hours', 'suppressed:focus_session',
            'suppressed:rate_limit'  (queued for later)
        """
        if notif.dedup_key and notif.dedup_key in self._dedup_seen:
            metrics.emit('notification.suppressed', {'reason': 'dedup', 'kind': notif.kind})
            return 'suppressed:dedup'

        if self._is_quiet_hours() and notif.urgency != 'critical':
            metrics.emit('notification.suppressed', {'reason': 'quiet_hours', 'kind': notif.kind})
            return 'suppressed:quiet_hours'

        if self._focus_active and notif.urgency != 'critical':
            self._suppressed_focus.append(notif)
            metrics.emit('notification.suppressed', {'reason': 'focus_session', 'kind': notif.kind})
            return 'suppressed:focus_session'

        if notif.urgency != 'critical' and not self._under_rate_limit():
            self._queue.append(notif)
            metrics.emit('notification.suppressed', {'reason': 'rate_limit', 'kind': notif.kind})
            return 'suppressed:rate_limit'

        self._fire(notif)
        return 'fired'

    def fired_count(self) -> int:
        return self._fired_count

    def queue_size(self) -> int:
        return len(self._queue)

    # ── event handlers ──────────────────────────────────────────────────────

    def _on_calendar(self, payload: dict[str, Any]) -> None:
        title = payload.get('title', 'Untitled event')
        mins  = int(payload.get('minutes_away', 0))
        loc   = payload.get('location', '') or ''
        body  = ('Starting now' if mins == 0
                 else (f'In {mins} min' + (f' · {loc[:40]}' if loc else '')))
        urgency = 'critical' if mins <= 2 else 'high'
        self.submit(Notification(
            title=f'📅 {title}', body=body, urgency=urgency, kind='meeting',
            dedup_key=f'event:{payload.get("event_id", "")}',
            buttons=[dict(_ASK_PEBBLE), dict(_DISMISS)],
            metadata={'event_id': payload.get('event_id', ''), 'minutes_away': mins},
        ))

    def _on_tasks(self, payload: dict[str, Any]) -> None:
        kind  = payload.get('kind', 'today')
        tasks = payload.get('tasks', []) or []
        if not tasks:
            return
        title = (f'⚠️ {len(tasks)} overdue task{"s" if len(tasks) != 1 else ""}'
                 if kind == 'overdue'
                 else f'✅ {len(tasks)} due today')
        body = tasks[0][:60] + (f' +{len(tasks)-1} more' if len(tasks) > 1 else '')
        self.submit(Notification(
            title=title, body=body, urgency='high' if kind == 'overdue' else 'normal',
            kind='tasks', dedup_key=f'tasks:{kind}:{datetime.date.today().isoformat()}',
            buttons=[dict(_ASK_PEBBLE), dict(_DISMISS)],
        ))

    def _on_reminder(self, payload: dict[str, Any]) -> None:
        rem = payload.get('reminder', {}) or {}
        text = (rem.get('text') or 'Reminder')[:80]
        self.submit(Notification(
            title='🔔 Reminder', body=text, urgency='high', kind='reminder',
            dedup_key=f'reminder:{rem.get("id", "")}',
            buttons=[{'label': 'Got it', 'action': 'dismiss', 'style': 'primary'},
                     {'label': 'Ask Pebble', 'action': 'open_chat', 'style': 'default'}],
        ))

    def _on_morning(self, payload: dict[str, Any]) -> None:
        self.submit(Notification(
            title='🌅 Good morning!', body='Ready to plan your day?',
            urgency='normal', kind='morning',
            dedup_key=f'morning:{datetime.date.today().isoformat()}',
            buttons=[{'label': 'Plan my day', 'action': 'open_chat', 'style': 'primary'},
                     {'label': 'Later', 'action': 'dismiss', 'style': 'default'}],
        ))

    def _on_meeting_prep(self, payload: dict[str, Any]) -> None:
        title = payload.get('title', 'Untitled')
        mins  = int(payload.get('minutes_away', 0))
        num   = int(payload.get('num_attendees', 0))
        loc   = payload.get('location', '') or ''
        parts = [f'In {mins} minutes']
        if num > 0:
            parts.append(f'{num} attendee{"s" if num != 1 else ""}')
        if loc:
            parts.append(loc[:30])
        self.submit(Notification(
            title=f'📋 Prep: {title[:45]}', body=' · '.join(parts),
            urgency='high', kind='meeting_prep',
            dedup_key=f'prep:{payload.get("event_id", title)}',
            buttons=[{'label': 'Get briefed', 'action': 'open_chat', 'style': 'primary'},
                     dict(_DISMISS)],
        ))

    def _on_focus_start(self, payload: dict[str, Any]) -> None:
        self._focus_active = True

    def _on_focus_soon(self, payload: dict[str, Any]) -> None:
        session_type = payload.get('session_type', 'work')
        label = 'break' if session_type == 'work' else 'session'
        self.submit(Notification(
            title='⏱ 1 minute left', body=f'Wrapping up your {label}…',
            urgency='high', kind='focus_soon',
            buttons=[{'label': 'OK', 'action': 'dismiss', 'style': 'default'}],
        ))

    def _on_focus_end(self, payload: dict[str, Any]) -> None:
        self._focus_active = False
        # Session-complete popup (fires directly — it's a response to the user's
        # own timer ending, so it should always show, like the catch-up).
        session_type = payload.get('session_type', 'work')
        task = (payload.get('task') or 'Focus session')
        if session_type == 'work':
            title, body = '🎉 Focus session complete!', f'Nice work on: {task[:50]} — time for a break!'
        else:
            title, body = '⏱ Break over — back to work!', f'Ready to continue: {task[:50]}'
        self._fire(Notification(
            title=title, body=body, urgency='high', kind='focus_end',
            buttons=[dict(_ASK_PEBBLE), dict(_DISMISS)],
        ))
        # Catch-up summary for anything suppressed during the session
        if self._suppressed_focus:
            n = len(self._suppressed_focus)
            self._suppressed_focus.clear()
            self._fire(Notification(
                title='⏱ Focus over — catch-up',
                body=f'{n} notification{"s" if n != 1 else ""} arrived during your session.',
                urgency='normal', kind='focus_catchup',
            ))

    # User-facing planners get a completion popup; internal state-doc planners
    # (schedule/wrapup) stay silent so we don't spam the user with plumbing.
    _PLANNER_NOTIFY = {
        'morning':   ('☀️ Morning briefing ready', 'Your day is planned — open to see it.'),
        'comms':     ('✉️ Draft replies ready',    'Pebble drafted replies for you to review.'),
        'exam_prep': ('📚 Study plan ready',        'Your exam-prep plan is ready.'),
        'school':    ('🎓 School update',           'Deadlines and study plan updated.'),
    }

    def _on_planner_completed(self, payload: dict[str, Any]) -> None:
        if payload.get('was_skipped'):
            return
        name = payload.get('planner', '')
        spec = self._PLANNER_NOTIFY.get(name)
        if not spec:
            return
        title, body = spec
        self.submit(Notification(
            title=title, body=body, urgency='normal', kind=f'planner:{name}',
            dedup_key=f'planner:{name}:{datetime.date.today().isoformat()}',
            buttons=[{'label': 'Open', 'action': 'open_chat', 'style': 'primary'},
                     dict(_DISMISS)],
        ))

    # ── gating helpers ──────────────────────────────────────────────────────

    def _under_rate_limit(self) -> bool:
        """True if a non-critical notification can fire now (10-min window)."""
        now = self._clock()
        # Drop fires older than 10min
        while self._fire_log and now - self._fire_log[0] > 600:
            self._fire_log.popleft()
        return len(self._fire_log) < self._max_per_10min

    def _is_quiet_hours(self) -> bool:
        try:
            start_h, start_m = (int(x) for x in self._quiet_start_str.split(':'))
            end_h,   end_m   = (int(x) for x in self._quiet_end_str.split(':'))
        except (ValueError, AttributeError):
            return False
        now   = self._now_fn()
        cur   = now.hour * 60 + now.minute
        start = start_h * 60 + start_m
        end   = end_h * 60 + end_m
        # Wraps midnight when start > end
        return (start <= cur or cur < end) if start > end else (start <= cur < end)

    def _fire(self, notif: Notification) -> None:
        if notif.dedup_key:
            self._dedup_seen.add(notif.dedup_key)
        self._fire_log.append(self._clock())
        self._fired_count += 1
        metrics.emit('notification.fired', {
            'kind':      notif.kind,
            'urgency':   notif.urgency,
            'dedup_key': notif.dedup_key,
        })
        try:
            self._popup_fn(title=notif.title, body=notif.body,
                           buttons=notif.buttons, metadata=notif.metadata)
        except Exception as e:
            metrics.emit('notification.fire_failed', {'kind': notif.kind, 'error': str(e)})

    def _default_popup(self, *, title: str, body: str, buttons: list, metadata: dict) -> None:
        """Stub used when no real popup renderer is injected. Logs to stderr."""
        import sys
        print(f'[notification] {title} :: {body}', file=sys.stderr)

    # ── periodic flush (caller drives this from a Tk after-loop or thread) ──

    def flush_queue(self) -> int:
        """Try to fire queued notifications respecting rate limit. Returns count fired."""
        fired = 0
        while self._queue and self._under_rate_limit():
            self._fire(self._queue.popleft())
            fired += 1
        return fired
