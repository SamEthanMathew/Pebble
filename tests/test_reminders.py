"""Reminders module regression tests.

Catches Windows-strftime issues — '%-I' is POSIX-only and crashes on Windows
with 'Invalid format string'.
"""

from __future__ import annotations

import datetime as dt
import json


def test_list_with_empty_reminders(pebble_home):
    from modules.reminders import RemindersModule
    out = RemindersModule({'enabled': True}).execute(action='list')
    assert isinstance(out, str)
    assert not out.lower().startswith('reminders error')


def test_list_with_upcoming_reminder_formats_time(pebble_home):
    """The actual bug: strftime('%-I:%M %p') raises on Windows.
    A reminder in the future should list without 'Invalid format string'.
    """
    from pathlib import Path
    from atomic_io import write_json

    later = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)).isoformat()
    write_json(Path.home() / '.pebble' / 'reminders.json', [
        {'id': 'r1', 'text': 'pay rent', 'remind_at': later,
         'done': False, 'created': dt.datetime.now(dt.timezone.utc).isoformat()},
    ])

    from modules.reminders import RemindersModule
    out = RemindersModule({'enabled': True}).execute(action='list')
    assert isinstance(out, str)
    assert 'Invalid format string' not in out
    assert 'pay rent' in out
    # Should include a wall-clock time
    assert ':' in out  # like '2:42 PM'


def test_list_with_past_reminders_excluded(pebble_home):
    from pathlib import Path
    from atomic_io import write_json

    earlier = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    write_json(Path.home() / '.pebble' / 'reminders.json', [
        {'id': 'r1', 'text': 'past thing', 'remind_at': earlier,
         'done': False, 'created': dt.datetime.now(dt.timezone.utc).isoformat()},
    ])

    from modules.reminders import RemindersModule
    out = RemindersModule({'enabled': True}).execute(action='list')
    assert 'past thing' not in out  # filtered out as past
