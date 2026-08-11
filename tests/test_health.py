"""Tests for health.py — the background-loop heartbeat/error registry.

Proactive polling loops beat() each successful cycle and record_error() on
failure instead of `except Exception: pass`, so a silently-dead watcher is
visible via /health instead of the app appearing to work while noticing nothing.
Persisted to disk (atomic) so the chat subprocess can read what the tray-process
loops wrote.
"""

from __future__ import annotations


def test_beat_records_heartbeat(pebble_home):
    import health
    health.beat('calendar')
    snap = health.snapshot()
    assert snap['calendar']['beats'] == 1
    assert 'last_beat' in snap['calendar']


def test_beats_increment(pebble_home):
    import health
    health.beat('tasks')
    health.beat('tasks')
    health.beat('tasks')
    assert health.snapshot()['tasks']['beats'] == 3


def test_record_error_tracks_type_and_message(pebble_home):
    import health
    health.record_error('gmail', ValueError('token expired'))
    row = health.snapshot()['gmail']
    assert row['errors'] == 1
    assert row['last_error'] == 'token expired'
    assert row['last_error_type'] == 'ValueError'
    assert 'last_error_at' in row


def test_snapshot_is_isolated_copy(pebble_home):
    import health
    health.beat('x')
    snap = health.snapshot()
    snap['x']['beats'] = 999
    assert health.snapshot()['x']['beats'] == 1  # mutation did not leak into state


def test_persisted_across_module_reload(pebble_home):
    """State lives on disk so a second reader (chat subprocess) sees loop beats."""
    import importlib
    import health
    health.beat('reminders')
    health.record_error('reminders', RuntimeError('boom'))
    importlib.reload(health)  # simulate a fresh process/import
    row = health.snapshot()['reminders']
    assert row['beats'] == 1
    assert row['errors'] == 1


def test_health_slash_command_reports_status(pebble_home):
    """/health surfaces per-loop beats and the last error to the user."""
    import health
    import chat_commands
    health.beat('calendar')
    health.record_error('gmail', ValueError('token expired'))
    out = chat_commands.handle('/health')
    assert isinstance(out, str)
    assert 'calendar' in out and 'gmail' in out
    assert 'token expired' in out or 'ValueError' in out
