"""
Proactive engine — background polling threads for Pebble.
Checks calendar, tasks, and triggers morning briefing.

Phase 1 refactor: polling loops PUBLISH events to the bus (events.py)
instead of calling notification helpers directly. The existing _notify_*
methods stay and are wired up as default subscribers, so user-visible
behavior is unchanged. Phase 2 planners + dispatcher will subscribe to
the same events and gradually take over routing decisions.
"""

from __future__ import annotations

import threading
import datetime
from datetime import date
from typing import Any, Callable
import tkinter as tk

import paths
from events import (
    bus,
    CALENDAR_EVENT_APPROACHING,
    TASK_DUE_SOON,
    REMINDER_DUE,
    FOCUS_SESSION_ENDED,
)


class ProactiveEngine:
    def __init__(self, root: tk.Tk, on_open_chat: Callable[[], None],
                 on_open_chat_with_prompt: Callable[[str], None] | None = None):
        """
        root: the main tkinter Tk() window (for root.after scheduling)
        on_open_chat: callable to open the Pebble chat window (passed from CrabPet._on_click)
        """
        self._root = root
        self._on_open_chat = on_open_chat
        self._on_open_chat_with_prompt = on_open_chat_with_prompt
        self._stop = threading.Event()

        self._notified_events: set[str] = set()  # event IDs we've already notified about
        self._last_task_check: datetime.datetime | None = None
        self._morning_briefing_done_date: date | None = None
        self._meeting_prep_notified: set[str] = set()

        self._threads: list[threading.Thread] = []

        # Subscribe default notify handlers so user-visible behavior persists
        # while planners are not yet in place. Each handler unpacks the payload
        # and calls the existing _notify_* method.
        bus.subscribe(CALENDAR_EVENT_APPROACHING, self._on_calendar_event)
        bus.subscribe(TASK_DUE_SOON,              self._on_tasks_due)
        bus.subscribe(REMINDER_DUE,               self._on_reminder_due)
        bus.subscribe(FOCUS_SESSION_ENDED,        self._on_focus_end)

    # ── default subscribers (translate payload → existing notify method) ─────

    def _on_calendar_event(self, payload: dict[str, Any]) -> None:
        self._notify_event(
            payload.get('title', 'Untitled event'),
            int(payload.get('minutes_away', 0)),
            payload.get('location', '') or '',
        )

    def _on_tasks_due(self, payload: dict[str, Any]) -> None:
        kind  = payload.get('kind', 'today')
        tasks = payload.get('tasks', [])
        if tasks:
            self._notify_tasks(kind, tasks)

    def _on_reminder_due(self, payload: dict[str, Any]) -> None:
        self._notify_reminder(payload.get('reminder', {}))

    def _on_focus_end(self, payload: dict[str, Any]) -> None:
        self._notify_focus_end(
            payload.get('session_type', 'work'),
            payload.get('task', 'Focus session'),
        )

    def start(self):
        """Start all background polling threads."""
        self._threads = [
            threading.Thread(target=self._calendar_loop,   daemon=True, name='pebble-calendar'),
            threading.Thread(target=self._task_loop,       daemon=True, name='pebble-tasks'),
            threading.Thread(target=self._morning_loop,    daemon=True, name='pebble-morning'),
            threading.Thread(target=self._reminder_loop,   daemon=True, name='pebble-reminders'),
            threading.Thread(target=self._focus_loop,      daemon=True, name='pebble-focus'),
            threading.Thread(target=self._meeting_prep_loop, daemon=True, name='pebble-meeting-prep'),
        ]
        for t in self._threads:
            t.start()

    def stop(self):
        """Signal all threads to stop."""
        self._stop.set()

    # ── Calendar ───────────────────────────────────────────────────────────────

    def _calendar_loop(self):
        # Wait 10 seconds on first iteration to allow app to fully load
        self._stop.wait(timeout=10)
        while not self._stop.is_set():
            try:
                self._check_calendar()
            except Exception:
                pass
            self._stop.wait(timeout=300)  # re-check every 5 minutes

    def _check_calendar(self):
        from modules.google_auth import GoogleServices, is_google_connected

        if not is_google_connected():
            return

        try:
            svc = GoogleServices()
            import datetime as dt
            now  = dt.datetime.now().astimezone()
            in20 = now + dt.timedelta(minutes=20)

            # Get events starting in the next 0-20 minutes
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

                # Parse start time and check if within 15 minutes
                try:
                    start_dt = dt.datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                    mins_away = int((start_dt.astimezone() - now).total_seconds() / 60)
                    if 0 <= mins_away <= 15:
                        self._notified_events.add(eid)
                        location = event.get('location', '')
                        bus.publish(CALENDAR_EVENT_APPROACHING, {
                            'event_id':     eid,
                            'title':        summary,
                            'start_iso':    start_str,
                            'minutes_away': mins_away,
                            'location':     location,
                            'attendees':    event.get('attendees', []),
                        })
                except Exception:
                    continue
        except Exception:
            pass

    def _notify_event(self, title: str, mins_away: int, location: str):
        def _show():
            from notification_popup import NotificationPopup
            if mins_away == 0:
                time_str = 'Starting now'
            elif mins_away == 1:
                time_str = 'In 1 minute'
            else:
                time_str = f'In {mins_away} minutes'

            body = time_str
            if location:
                body += f' · {location[:40]}'

            popup = NotificationPopup(
                self._root,
                title=f'📅 {title}',
                body=body,
                buttons=[
                    {'label': 'Ask Pebble', 'command': self._on_open_chat, 'style': 'primary'},
                    {'label': 'Dismiss', 'command': lambda: None, 'style': 'default'},
                ],
                auto_dismiss_ms=20000,
            )
            popup.show()

        self._root.after(0, _show)

    # ── Tasks ──────────────────────────────────────────────────────────────────

    def _task_loop(self):
        # Wait 15 seconds on first iteration
        self._stop.wait(timeout=15)
        while not self._stop.is_set():
            try:
                self._check_tasks()
            except Exception:
                pass
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
            bus.publish(TASK_DUE_SOON, {'kind': 'today',   'tasks': due_today})

    def _notify_tasks(self, kind: str, tasks: list[str]):
        def _show():
            from notification_popup import NotificationPopup
            if kind == 'overdue':
                title = f'⚠️ {len(tasks)} overdue task{"s" if len(tasks) > 1 else ""}'
                body = tasks[0][:60] + (f' +{len(tasks)-1} more' if len(tasks) > 1 else '')
            else:
                title = f'✅ {len(tasks)} task{"s" if len(tasks) > 1 else ""} due today'
                body = tasks[0][:60] + (f' +{len(tasks)-1} more' if len(tasks) > 1 else '')

            popup = NotificationPopup(
                self._root,
                title=title,
                body=body,
                buttons=[
                    {'label': 'Ask Pebble', 'command': self._on_open_chat, 'style': 'primary'},
                    {'label': 'Dismiss',    'command': lambda: None, 'style': 'default'},
                ],
                auto_dismiss_ms=12000,
            )
            popup.show()
        self._root.after(0, _show)

    # ── Morning briefing ───────────────────────────────────────────────────────

    def _morning_loop(self):
        # Wait 30 seconds on first iteration
        self._stop.wait(timeout=30)
        while not self._stop.is_set():
            try:
                self._check_morning_briefing()
            except Exception:
                pass
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

        # Check if today's journal already exists
        journal_path = paths.data_dir() / 'journal' / f'{today.isoformat()}.md'
        if journal_path.exists():
            self._morning_briefing_done_date = today
            return

        self._morning_briefing_done_date = today
        self._notify_morning()

    def _notify_morning(self):
        def _show():
            from notification_popup import NotificationPopup
            popup = NotificationPopup(
                self._root,
                title='🌅 Good morning!',
                body="Ready to plan your day?",
                buttons=[
                    {'label': 'Plan my day', 'command': self._on_open_chat, 'style': 'primary'},
                    {'label': 'Later',       'command': lambda: None, 'style': 'default'},
                ],
                auto_dismiss_ms=30000,
            )
            popup.show()
        self._root.after(0, _show)

    # ── Reminders ──────────────────────────────────────────────────────────────

    def _reminder_loop(self):
        self._stop.wait(timeout=20)
        while not self._stop.is_set():
            try:
                self._check_reminders()
            except Exception:
                pass
            self._stop.wait(timeout=60)  # check every minute

    def _check_reminders(self):
        try:
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
        except Exception:
            pass

    def _notify_reminder(self, reminder: dict):
        def _show():
            from notification_popup import NotificationPopup
            text = reminder.get('text', 'Reminder')
            popup = NotificationPopup(
                self._root,
                title='🔔 Reminder',
                body=text[:80],
                buttons=[
                    {'label': 'Got it',      'command': lambda: None,        'style': 'primary'},
                    {'label': 'Ask Pebble',  'command': self._on_open_chat,  'style': 'default'},
                ],
                auto_dismiss_ms=0,  # don't auto-dismiss reminders
            )
            popup.show()
        self._root.after(0, _show)

    # ── Focus timer ────────────────────────────────────────────────────────────

    def _focus_loop(self):
        self._stop.wait(timeout=25)
        while not self._stop.is_set():
            try:
                self._check_focus()
            except Exception:
                pass
            self._stop.wait(timeout=60)

    def _check_focus(self):
        try:
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

            # Get duration from state or default
            duration_map = {'work': 25, 'break': 5, 'long_break': 15}
            duration_min = state.get('duration_minutes') or duration_map.get(session_type, 25)

            start_dt = dt.datetime.fromisoformat(start_str)
            elapsed = (dt.datetime.now() - start_dt).total_seconds() / 60
            remaining = duration_min - elapsed

            # Notify at 1 minute remaining and at session end
            notify_key = f"{state.get('start_time')}_{session_type}"
            if not hasattr(self, '_notified_focus'):
                self._notified_focus: set = set()

            if remaining <= 0 and notify_key not in self._notified_focus:
                self._notified_focus.add(notify_key)
                bus.publish(FOCUS_SESSION_ENDED, {'session_type': session_type, 'task': task})
            elif 0 < remaining <= 1 and f'{notify_key}_1min' not in self._notified_focus:
                self._notified_focus.add(f'{notify_key}_1min')
                self._notify_focus_1min(session_type, task)
        except Exception:
            pass

    def _notify_focus_end(self, session_type: str, task: str):
        def _show():
            from notification_popup import NotificationPopup
            if session_type == 'work':
                title = '🎉 Focus session complete!'
                body  = f'Nice work on: {task[:50]}\nTime for a break!'
            else:
                title = '⏱ Break over — back to work!'
                body  = f'Ready to continue: {task[:50]}'
            popup = NotificationPopup(
                self._root,
                title=title,
                body=body,
                buttons=[
                    {'label': 'Ask Pebble', 'command': self._on_open_chat, 'style': 'primary'},
                    {'label': 'Dismiss',    'command': lambda: None,       'style': 'default'},
                ],
                auto_dismiss_ms=0,
            )
            popup.show()
        self._root.after(0, _show)

    def _notify_focus_1min(self, session_type: str, task: str):
        def _show():
            from notification_popup import NotificationPopup
            label = 'break' if session_type == 'work' else 'session'
            popup = NotificationPopup(
                self._root,
                title='⏱ 1 minute left',
                body=f'Wrapping up your {label}...',
                buttons=[{'label': 'OK', 'command': lambda: None, 'style': 'default'}],
                auto_dismiss_ms=8000,
            )
            popup.show()
        self._root.after(0, _show)

    # ── Meeting prep ───────────────────────────────────────────────────────────

    def _meeting_prep_loop(self):
        self._stop.wait(timeout=45)
        while not self._stop.is_set():
            try:
                self._check_meeting_prep()
            except Exception:
                pass
            self._stop.wait(timeout=120)  # check every 2 minutes

    def _check_meeting_prep(self):
        from modules.google_auth import GoogleServices, is_google_connected
        if not is_google_connected():
            return

        try:
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
                        self._notify_meeting_prep(summary, mins_away, num_attendees, location)
                except Exception:
                    continue
        except Exception:
            pass

    def _notify_meeting_prep(self, title: str, mins: int, num_attendees: int, location: str):
        def _show():
            from notification_popup import NotificationPopup
            body_parts = [f'In {mins} minutes']
            if num_attendees > 0:
                body_parts.append(f'{num_attendees} attendee{"s" if num_attendees > 1 else ""}')
            if location:
                body_parts.append(location[:30])
            body = ' · '.join(body_parts)

            def _prep():
                self._on_open_chat()

            popup = NotificationPopup(
                self._root,
                title=f'📋 Prep: {title[:45]}',
                body=body,
                buttons=[
                    {'label': 'Get briefed', 'command': _prep, 'style': 'primary'},
                    {'label': 'Dismiss',     'command': lambda: None, 'style': 'default'},
                ],
                auto_dismiss_ms=25000,
            )
            popup.show()
        self._root.after(0, _show)
