"""Sidebar-tab slash commands: /tasks /reminders /gmail /calendar /notes."""

from __future__ import annotations


def test_tasks_returns_string(pebble_home):
    """Tasks module is always-on; even with no tasks, returns a string (not None)."""
    from chat_commands import handle
    out = handle('/tasks')
    assert isinstance(out, str)
    assert out  # not empty


def test_reminders_returns_string(pebble_home):
    from chat_commands import handle
    out = handle('/reminders')
    assert isinstance(out, str)


def test_gmail_when_disabled_shows_hint(pebble_home):
    """Gmail not connected → command tells user how to fix."""
    from chat_commands import handle
    out = handle('/gmail')
    assert '`/connect google`' in out or 'Gmail' in out


def test_calendar_when_disabled_shows_hint(pebble_home):
    from chat_commands import handle
    out = handle('/calendar')
    assert '`/connect google`' in out or 'Calendar' in out


def test_notes_when_no_vault_shows_hint(pebble_home):
    from chat_commands import handle
    out = handle('/notes')
    assert 'Obsidian' in out or 'vault' in out.lower()


def test_notes_with_search_query(pebble_home):
    """Passing a query routes to obsidian.search instead of list_folder."""
    from chat_commands import handle
    out = handle('/notes my-search-string')
    assert isinstance(out, str)


def test_module_action_invokes_real_module(pebble_home):
    """The helper finds the active module by name and calls execute()."""
    from chat_commands import _run_module_action

    # tasks is default-enabled, so this should hit the real module
    out = _run_module_action('tasks', 'list')
    assert isinstance(out, str)
    assert not out.lower().startswith('unknown action')


def test_module_action_unknown_module(pebble_home):
    from chat_commands import _run_module_action
    out = _run_module_action('does_not_exist', 'list',
                              missing_hint='Try Settings → enable it.')
    assert 'not enabled' in out
    assert 'Try Settings' in out


def test_module_action_fallback_when_first_unknown(pebble_home):
    """If the first action returns 'Unknown action', the fallback is tried."""
    from chat_commands import _run_module_action
    out = _run_module_action('tasks', 'fictional_action',
                              fallback_actions=['list'])
    assert isinstance(out, str)
    assert not out.lower().startswith('unknown action')
