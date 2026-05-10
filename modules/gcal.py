"""Google Calendar module — view events, find free time, and create events."""

from __future__ import annotations
import datetime
import re
from pathlib import Path

from .base import PebbleModule


class GCalModule(PebbleModule):
    name         = 'gcal'
    display_name = 'Google Calendar'
    description  = 'View upcoming events, find free time, and create new events on Google Calendar'
    icon         = '📅'
    config_fields = [
        {'key': '_google_status', 'label': 'Google Account', 'type': 'google_status'},
        {'key': 'calendar_id',    'label': 'Calendar ID (leave blank for primary)', 'type': 'text'},
    ]

    def is_ready(self) -> bool:
        return True

    # ── Google API service ─────────────────────────────────────────────────────

    def _get_service(self):
        from .google_auth import GoogleServices
        return GoogleServices()

    @property
    def _calendar_id(self) -> str:
        return self.cfg.get('calendar_id') or 'primary'

    # ── PebbleModule interface ─────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'gcal'

    def tool_description(self) -> str:
        return (
            'Interact with Google Calendar. Supports: '
            'get_events (list all events for a given day), '
            'find_free_time (find open windows on a given day), '
            'create_event (add a new event to the calendar).'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['get_events', 'find_free_time', 'create_event'],
                    'description': (
                        'Action to perform: '
                        '"get_events" lists all events on a day; '
                        '"find_free_time" finds open time slots; '
                        '"create_event" adds a new event.'
                    ),
                },
                'date': {
                    'type': 'string',
                    'description': "Date to operate on: 'today', 'tomorrow', or YYYY-MM-DD (default: today)",
                },
                'duration_minutes': {
                    'type': 'integer',
                    'description': 'Duration needed for free time search in minutes (default: 60)',
                },
                'title': {
                    'type': 'string',
                    'description': 'Event title / summary (used with create_event)',
                },
                'start_time': {
                    'type': 'string',
                    'description': "Start time for the event, e.g. '2:00 PM', '14:00', '2pm' (used with create_event)",
                },
                'end_time': {
                    'type': 'string',
                    'description': "End time for the event (optional; defaults to start + 1 hour)",
                },
                'description': {
                    'type': 'string',
                    'description': 'Event description / notes (used with create_event)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = '', date: str = 'today',
                duration_minutes: int = 60, title: str = '',
                start_time: str = '', end_time: str = '',
                description: str = '', **_) -> str:
        if action == 'get_events':
            return self._action_get_events(date)
        elif action == 'find_free_time':
            return self._action_find_free_time(date, duration_minutes)
        elif action == 'create_event':
            return self._action_create_event(date, title, start_time, end_time, description)
        else:
            return (
                f'Unknown action "{action}". '
                'Valid actions: get_events, find_free_time, create_event.'
            )

    # ── actions ────────────────────────────────────────────────────────────────

    def _action_get_events(self, date_str: str) -> str:
        try:
            services = self._get_service()
        except Exception:
            return 'Google Calendar not connected — sign in via Settings.'

        try:
            target_date = self._parse_date(date_str)
            events = services.get_events_for_day(target_date, self._calendar_id)

            # Windows strftime does not support %-d; fall back gracefully
            try:
                header = f'**{target_date.strftime("%A, %B %-d")}**\n'
            except ValueError:
                header = f'**{target_date.strftime("%A, %B %d").replace(" 0", " ")}**\n'

            if not events:
                return header + f'No events scheduled for {target_date.strftime("%B %d")}.'.replace(' 0', ' ')

            lines = [header]
            for ev in events:
                lines.append(self._format_event_dict(ev))
            return '\n'.join(lines)

        except Exception as e:
            return f'Google Calendar error: {str(e)}'

    def _action_find_free_time(self, date_str: str, duration_minutes: int) -> str:
        try:
            services = self._get_service()
        except Exception:
            return 'Google Calendar not connected — sign in via Settings.'

        try:
            target_date = self._parse_date(date_str)
            time_min, time_max = self._day_bounds(target_date)

            result = services.calendar.events().list(
                calendarId=self._calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime',
            ).execute()

            events = result.get('items', [])

            # Work window: 8 AM – 10 PM local time
            tz = datetime.datetime.now().astimezone().tzinfo
            work_start = datetime.datetime.combine(target_date, datetime.time(8, 0), tzinfo=tz)
            work_end   = datetime.datetime.combine(target_date, datetime.time(22, 0), tzinfo=tz)

            date_label = target_date.strftime('%B %d').replace(' 0', ' ')

            if not events:
                return f'Plenty of free time — no events on {date_label}.'

            # Build list of (start, end) datetimes for timed events only
            busy: list[tuple[datetime.datetime, datetime.datetime]] = []
            for ev in events:
                ev_start = self._event_dt(ev, 'start', tz)
                ev_end   = self._event_dt(ev, 'end',   tz)
                if ev_start is None or ev_end is None:
                    continue  # all-day event with no datetime — skip for free-time calc
                # Clamp to work window
                ev_start = max(ev_start, work_start)
                ev_end   = min(ev_end,   work_end)
                if ev_end > ev_start:
                    busy.append((ev_start, ev_end))

            # Merge overlapping busy blocks
            busy.sort()
            merged: list[tuple[datetime.datetime, datetime.datetime]] = []
            for block in busy:
                if merged and block[0] < merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], block[1]))
                else:
                    merged.append(list(block))  # type: ignore[arg-type]
            merged = [tuple(b) for b in merged]  # type: ignore[assignment]

            # Find gaps
            free_windows: list[str] = []
            cursor = work_start
            for block_start, block_end in merged:
                gap_end = block_start
                gap_minutes = int((gap_end - cursor).total_seconds() / 60)
                if gap_minutes >= duration_minutes:
                    free_windows.append(
                        f'{self._format_time(cursor)} – {self._format_time(gap_end)} '
                        f'({gap_minutes} min free)'
                    )
                cursor = max(cursor, block_end)

            # Gap after last event
            gap_minutes = int((work_end - cursor).total_seconds() / 60)
            if gap_minutes >= duration_minutes:
                free_windows.append(
                    f'{self._format_time(cursor)} – {self._format_time(work_end)} '
                    f'({gap_minutes} min free)'
                )

            if not free_windows:
                return 'Your day is fully booked.'

            header = f'Free windows on {date_label} (≥{duration_minutes} min):\n'
            return header + '\n'.join(f'• {w}' for w in free_windows)

        except Exception as e:
            return f'Google Calendar error: {str(e)}'

    def _action_create_event(self, date_str: str, title: str,
                             start_time: str, end_time: str,
                             description: str) -> str:
        if not title.strip():
            return 'Please provide an event title.'
        if not start_time.strip():
            return 'Please provide a start time.'

        try:
            services = self._get_service()
        except Exception:
            return 'Google Calendar not connected — sign in via Settings.'

        try:
            target_date = self._parse_date(date_str)
            tz          = datetime.datetime.now().astimezone().tzinfo

            dt_start = self._parse_time_str(start_time, target_date)

            if end_time.strip():
                dt_end = self._parse_time_str(end_time, target_date)
            else:
                dt_end = dt_start + datetime.timedelta(hours=1)

            # RFC3339 format: 2024-01-15T14:00:00-05:00
            start_rfc = dt_start.isoformat()
            end_rfc   = dt_end.isoformat()

            tz_name = datetime.datetime.now().astimezone().strftime('%Z')
            result = services.create_event(
                summary=title.strip(),
                start_dt=start_rfc,
                end_dt=end_rfc,
                description=description.strip() if description else '',
                calendar_id=self._calendar_id,
                timezone=str(tz),
            )

            date_label = target_date.strftime('%B %d').replace(' 0', ' ')
            return (
                f"Event created: '{title.strip()}' on {date_label} "
                f'from {self._format_time(dt_start)} to {self._format_time(dt_end)}. '
                'Check Google Calendar to confirm.'
            )

        except Exception as e:
            return f'Google Calendar error: {str(e)}'

    # ── date / time helpers ────────────────────────────────────────────────────

    def _parse_date(self, date_str: str) -> datetime.date:
        """Parse 'today', 'tomorrow', or ISO YYYY-MM-DD into a date object."""
        today = datetime.date.today()
        ds = date_str.strip().lower()
        if ds in ('today', ''):
            return today
        if ds == 'tomorrow':
            return today + datetime.timedelta(days=1)
        # Try ISO format
        return datetime.date.fromisoformat(date_str.strip())

    def _day_bounds(self, d: datetime.date) -> tuple[str, str]:
        """Return RFC3339 timeMin/timeMax covering the whole day in local time."""
        tz = datetime.datetime.now().astimezone().tzinfo
        midnight_start = datetime.datetime(d.year, d.month, d.day, 0,  0,  0,  tzinfo=tz)
        midnight_end   = datetime.datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=tz)
        return midnight_start.isoformat(), midnight_end.isoformat()

    def _format_time(self, dt: datetime.datetime) -> str:
        """Return time formatted as '10:00 AM'."""
        return dt.strftime('%I:%M %p').lstrip('0') or '12:00 AM'

    def _parse_time_str(self, time_str: str, base_date: datetime.date) -> datetime.datetime:
        """
        Parse a time string like '2:00 PM', '14:00', '2pm', '9:30am' into a
        timezone-aware datetime on base_date.
        """
        tz  = datetime.datetime.now().astimezone().tzinfo
        raw = time_str.strip()

        # Normalise: lowercase, remove spaces before am/pm
        cleaned = raw.lower().replace(' ', '')

        # Patterns: 14:00 or 2:00pm or 2pm or 14:30
        m = re.match(
            r'^(\d{1,2})(?::(\d{2}))?(?:\s*(am|pm))?$',
            cleaned,
        )
        if not m:
            raise ValueError(f'Cannot parse time: "{time_str}"')

        hour   = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        ampm   = m.group(3)

        if ampm == 'pm' and hour != 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        # If no am/pm and hour < 8, assume PM (e.g. "3" → 3 PM for scheduling sanity)
        elif ampm is None and 1 <= hour <= 7:
            hour += 12

        return datetime.datetime(
            base_date.year, base_date.month, base_date.day,
            hour, minute, 0,
            tzinfo=tz,
        )

    # ── event formatting helpers ───────────────────────────────────────────────

    def _event_dt(self, event: dict, which: str,
                  tz: datetime.timezone) -> datetime.datetime | None:
        """Extract start or end datetime from an event dict. Returns None for all-day events."""
        data = event.get(which, {})
        dt_str = data.get('dateTime')
        if not dt_str:
            return None  # all-day event
        # Parse RFC3339 — Python 3.7+ fromisoformat handles offsets since 3.11;
        # use a manual fallback for older runtimes.
        try:
            return datetime.datetime.fromisoformat(dt_str).astimezone(tz)
        except Exception:
            # Strip trailing Z and treat as UTC
            dt_str_clean = dt_str.rstrip('Z')
            dt = datetime.datetime.fromisoformat(dt_str_clean).replace(
                tzinfo=datetime.timezone.utc
            )
            return dt.astimezone(tz)

    def _format_event_dict(self, ev: dict) -> str:
        """Format an event dict (from GoogleServices.get_events_for_day) into a display line."""
        tz    = datetime.datetime.now().astimezone().tzinfo
        title = ev.get('summary', '(No title)')

        if ev.get('all_day'):
            return f'All day: {title}'

        start_str = ev.get('start', '')
        end_str   = ev.get('end', '')

        if not start_str:
            return f'All day: {title}'

        try:
            dt_start = datetime.datetime.fromisoformat(start_str).astimezone(tz)
            start_fmt = self._format_time(dt_start)
        except Exception:
            start_fmt = start_str

        if end_str:
            try:
                dt_end = datetime.datetime.fromisoformat(end_str).astimezone(tz)
                end_fmt = self._format_time(dt_end)
            except Exception:
                end_fmt = end_str
        else:
            end_fmt = '?'

        location = ev.get('location', '')
        suffix   = f' ({location})' if location else ''

        return f'{start_fmt} – {end_fmt}  {title}{suffix}'

    def _format_event(self, event: dict) -> str:
        """Format a raw Google Calendar API event dict into a display line."""
        tz         = datetime.datetime.now().astimezone().tzinfo
        data_start = event.get('start', {})
        data_end   = event.get('end',   {})
        title      = event.get('summary', '(No title)')

        # All-day event
        if 'date' in data_start and 'dateTime' not in data_start:
            return f'All day: {title}'

        dt_start = self._event_dt(event, 'start', tz)
        dt_end   = self._event_dt(event, 'end',   tz)

        if dt_start is None:
            return f'All day: {title}'

        start_str = self._format_time(dt_start)
        end_str   = self._format_time(dt_end) if dt_end else '?'

        location = event.get('location', '')
        suffix   = f' ({location})' if location else ''

        return f'{start_str} – {end_str}  {title}{suffix}'
