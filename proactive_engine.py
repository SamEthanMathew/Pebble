"""
Proactive engine — background polling threads for Pebble.

PUBLISH-ONLY (Milestone B / P0-2): the polling loops watch calendar / tasks /
reminders / focus / morning / meeting-prep and PUBLISH events to the bus. They do
NOT render notifications and hold NO GUI reference — the NotificationDispatcher is
the single subscriber that decides popup/no-popup (rate-limit, quiet hours, dedup,
focus gating) and the platform shell supplies the actual renderer via popup_fn.

This module is headless: it imports no tkinter/GUI toolkit.
"""

from __future__ import annotations

import threading
import datetime
from datetime import date

import health
import paths
from events import (
    bus,
    CALENDAR_EVENT_APPROACHING,
    TASK_DUE_SOON,
    REMINDER_DUE,
    FOCUS_SESSION_ENDED,
    FOCUS_ENDING_SOON,
    MORNING_BRIEFING_DUE,
    MEETING_PREP_DUE,
)


class ProactiveEngine:
    """Background watcher supervisor. Publishes events only; never renders UI."""

    def __init__(self) -> None:
        self._stop = threading.Event()

        self._notified_events: set[str] = set()  # event IDs we've already published
        self._last_task_check: datetime.datetime | None = None
        self._morning_briefing_done_date: date | None = None
        self._meeting_prep_notified: set[str] = set()
        self._notified_focus: set[str] = set()

        self._threads: list[threading.Thread] = []

    def start(self):
        """Start all background polling threads."""
        self._threads = [
            threading.Thread(target=self._calendar_loop,     daemon=True, name='pebble-calendar'),
            threading.Thread(target=self._task_loop,         daemon=True, name='pebble-tasks'),
            threading.Thread(target=self._morning_loop,      daemon=True, name='pebble-morning'),
            threading.Thread(target=self._reminder_loop,     daemon=True, name='pebble-reminders'),
            threading.Thread(target=self._focus_loop,        daemon=True, name='pebble-focus'),
            threading.Thread(target=self._meeting_prep_loop, daemon=True, name='pebble-meeting-prep'),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        """Signal all threads to stop."""
        self._stop.set()

    def _run_check(self, name: str, check) -> None:
        """Run one poll cycle: beat() on success, record_error() on failure.

        The single place loops record health. Because health.beat/record_error
        are best-effort, this never raises — a watcher thread cannot die here.
        For this to be meaningful, the _check_* helpers must let real failures
        propagate (no blanket inner `except: pass`)."""
        try:
            check()
            health.beat(name)
        except Exception as e:
            health.record_error(name, e)

    # ── Calendar ───────────────────────────────────────────────────────────────

    def _calendar_loop(self):
        # Wait 10 seconds on first iteration to allow app to fully load
        self._stop.wait(timeout=10)
        while not self._stop.is_set():
            self._run_check('calendar', self._check_calendar)
            self._stop.wait(timeout=300)  # re-check every 5 minutes

    def _check_calendar(self):
        from modules.google_auth import GoogleServices, is_google_connected

        if not is_google_connected():
            return

        # NOTE: no blanket try/except here — a real failure (expired token, API
        # change) must propagate to _run_check so /health records a dead watcher
        # instead of showing it healthy forever. Per-event parsing is still guarded.
        svc = GoogleServices()
        import datetime as dt
        now  = dt.datetime.now().astimezone()
        in20 = now + dt.timedelta(minutes=20)

        events = svc.calendar.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=in20.isoformat(),
            singleEvents=True,
            orderBy='startTime',
            maxResults=5,
        ).execute().get('items', [])

        for event in events:
            eid     = event.get('id', '')
            summary = event.get('summary', 'Untitled event')
            start   = event.get('start', {})
            start_str = start.get('dateTime') or start.get('date')

            if eid in self._notified_events:
                continue

            try:
                start_dt = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                mins_away = int((start_dt.astimezone() - now).total_seconds() / 60)
                if 0 <= mins_away <= 15:
                    self._notified_events.add(eid)
                    bus.publish(CALENDAR_EVENT_APPROACHING, {
                        'event_id':     eid,
                        'title':        summary,
                        'start_iso':    start_str,
                        'minutes_away': mins_away,
                        'location':     event.get('location', ''),
                        'attendees':    event.get('attendees', []),
                    })
            except Exception:
                continue

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def _task_loop(self):
        self._stop.wait(timeout=15)
        while not self._stop.is_set():
            self._run_check('tasks', self._check_tasks)
            self._stop.wait(timeout=3600)  # re-check every hour

    def _check_tasks(self):
        import json
        import datetime as dt

        tasks_path = paths.data_dir() / 'tasks.json'
        if not tasks_path.exists():
            return

        try:
            tasks = json.loads(tasks_path.read_text(encoding='utf-8'))
        except Exception:
            return

        today = dt.date.today().isoformat()
        overdue = []
        due_today = []

        for task in tasks:
            if task.get('done'):
                continue
            due = task.get('due', '')
            if not due:
                continue
            if due < today:
                overdue.append(task['text'])
            elif due == today:
                due_today.append(task['text'])

        if overdue:
            bus.publish(TASK_DUE_SOON, {'kind': 'overdue', 'tasks': overdue})
        elif due_today:
            bus.publish(TASK_DUE_SOON, {'kind': 'today', 'tasks': due_today})

    # ── Morning briefing ───────────────────────────────────────────────────────

    def _morning_loop(self):
        self._stop.wait(timeout=30)
        while not self._stop.is_set():
            self._run_check('morning', self._check_morning_briefing)
            self._stop.wait(timeout=600)  # re-check every 10 minutes

    def _check_morning_briefing(self):
        import datetime as dt
        now   = dt.datetime.now()
        today = now.date()

        # Only fire between 8:30 and 10:00 AM
        if not (8 <= now.hour < 10 and (now.hour != 8 or now.minute >= 30)):
            return

        # Only fire once per day
        if self._morning_briefing_done_date == today:
            return

        # Skip if today's journal already exists
        journal_path = paths.data_dir() / 'journal' / f'{today.isoformat()}.md'
        if journal_path.exists():
            self._morning_briefing_done_date = today
            return

        self._morning_briefing_done_date = today
        bus.publish(MORNING_BRIEFING_DUE, {})

    # ── Reminders ──────────────────────────────────────────────────────────────

    def _reminder_loop(self):
        self._stop.wait(timeout=20)
        while not self._stop.is_set():
            self._run_check('reminders', self._check_reminders)
            self._stop.wait(timeout=60)  # check every minute

    def _check_reminders(self):
        # No blanket swallow: a failure here is recorded by _run_check via /health.
        from modules.reminders import get_due_reminders
        due = get_due_reminders()
        for r in due:
            bus.publish(REMINDER_DUE, {'reminder': r})
            # Mark done immediately to avoid repeat
            import json
            path = paths.data_dir() / 'reminders.json'
            reminders = json.loads(path.read_text(encoding='utf-8'))
            for rem in reminders:
                if rem.get('id') == r.get('id'):
                    rem['done'] = True
            path.write_text(json.dumps(reminders, indent=2), encoding='utf-8')

    # ── Focus timer ────────────────────────────────────────────────────────────

    def _focus_loop(self):
        self._stop.wait(timeout=25)
        while not self._stop.is_set():
            self._run_check('focus', self._check_focus)
            self._stop.wait(timeout=60)

    def _check_focus(self):
        # No blanket swallow: a failure here is recorded by _run_check via /health.
        from modules.focus_timer import get_focus_state
        import datetime as dt
        state = get_focus_state()
        if not state.get('active'):
            return
        start_str = state.get('start_time', '')
        if not start_str:
            return
        session_type = state.get('session_type', 'work')
        task = state.get('task', 'Focus session')

        duration_map = {'work': 25, 'break': 5, 'long_break': 15}
        duration_min = state.get('duration_minutes') or duration_map.get(session_type, 25)

        start_dt = dt.datetime.fromisoformat(start_str)
        elapsed = (dt.datetime.now() - start_dt).total_seconds() / 60
        remaining = duration_min - elapsed

        notify_key = f"{state.get('start_time')}_{session_type}"

        if remaining <= 0 and notify_key not in self._notified_focus:
            self._notified_focus.add(notify_key)
            bus.publish(FOCUS_SESSION_ENDED, {'session_type': session_type, 'task': task})
        elif 0 < remaining <= 1 and f'{notify_key}_1min' not in self._notified_focus:
            self._notified_focus.add(f'{notify_key}_1min')
            bus.publish(FOCUS_ENDING_SOON, {'session_type': session_type, 'task': task})

    # ── Meeting prep ───────────────────────────────────────────────────────────

    def _meeting_prep_loop(self):
        self._stop.wait(timeout=45)
        while not self._stop.is_set():
            self._run_check('meeting_prep', self._check_meeting_prep)
            self._stop.wait(timeout=120)  # check every 2 minutes

    def _check_meeting_prep(self):
        from modules.google_auth import GoogleServices, is_google_connected
        if not is_google_connected():
            return

        # No blanket swallow: a failure here is recorded by _run_check via /health.
        # Per-event parsing is still guarded so one bad event doesn't skip the rest.
        import datetime as dt
        svc = GoogleServices()
        now    = dt.datetime.now().astimezone()
        in_15  = now + dt.timedelta(minutes=15)

        events = svc.calendar.events().list(
            calendarId='primary',
            timeMin=now.isoformat(),
            timeMax=in_15.isoformat(),
            singleEvents=True,
            orderBy='startTime',
            maxResults=3,
        ).execute().get('items', [])

        for event in events:
            eid     = event.get('id', '')
            summary = event.get('summary', 'Untitled')
            start   = event.get('start', {})
            start_str = start.get('dateTime') or start.get('date', '')
            attendees = event.get('attendees', [])
            location  = event.get('location', '')

            prep_key = f'prep_{eid}'
            if prep_key in self._meeting_prep_notified:
                continue

            try:
                start_dt  = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                mins_away = int((start_dt.astimezone() - now).total_seconds() / 60)
                if 8 <= mins_away <= 14:
                    self._meeting_prep_notified.add(prep_key)
                    num_attendees = len([a for a in attendees if a.get('email') != 'me'])
                    bus.publish(MEETING_PREP_DUE, {
                        'event_id':      eid,
                        'title':         summary,
                        'minutes_away':  mins_away,
                        'num_attendees': num_attendees,
                        'location':      location,
                    })
            except Exception:
                continue
