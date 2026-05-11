"""Slash-command dispatcher tests."""

from __future__ import annotations

import pytest


# Commands that are safe to smoke-test with no args / safe defaults.
# Each must return either a string or an AsyncCommand — never None
# (None means "not a slash command at all") and never crash.
_NO_ARG_SMOKE_COMMANDS = [
    '/help', '/status', '/tasks', '/reminders', '/gmail',
    '/calendar', '/notes', '/entities', '/entity-suggestions',
    '/proposals', '/my-world', '/audit', '/errors',
    '/review-drafts', '/how-am-i-doing', '/slack-workspaces',
]


@pytest.mark.parametrize('cmd', _NO_ARG_SMOKE_COMMANDS)
def test_slash_command_does_not_crash(pebble_home, cmd):
    """Each documented command returns a string or AsyncCommand, not None,
    and does not raise even when modules are unconfigured."""
    from chat_commands import handle, AsyncCommand
    result = handle(cmd)
    assert result is not None, f'{cmd} returned None (not recognized as command)'
    assert isinstance(result, (str, AsyncCommand)), \
        f'{cmd} returned unexpected type {type(result).__name__}'


def test_unknown_returns_friendly_error(pebble_home):
    from chat_commands import handle
    out = handle('/bogus')
    assert isinstance(out, str)
    assert 'Unknown command' in out


def test_help_lists_known_commands(pebble_home):
    from chat_commands import handle
    out = handle('/help')
    assert '/briefing' in out
    assert '/add-course' in out
    assert '/entity-suggestions' in out


def test_non_slash_returns_none(pebble_home):
    from chat_commands import handle
    assert handle('hello there') is None


def test_dry_run_toggle(pebble_home):
    from chat_commands import handle
    import dry_run
    out = handle('/dry-run on')
    assert 'enabled' in out.lower()
    assert dry_run.is_enabled()
    out = handle('/dry-run off')
    assert 'disabled' in out.lower() or 'live' in out.lower()
    assert not dry_run.is_enabled()


def test_dry_run_status(pebble_home):
    from chat_commands import handle
    import dry_run
    dry_run.set_enabled(True)
    out = handle('/dry-run')
    assert 'ON' in out


def test_review_drafts_empty(pebble_home):
    from chat_commands import handle
    out = handle('/review-drafts')
    assert 'No dry-run previews' in out


def test_review_drafts_lists_previews(pebble_home):
    import dry_run
    from chat_commands import handle
    dry_run.write_preview({'module': 'gmail', 'action': 'draft',
                            'args': {'to': 'a@b'}, 'source': 'test', 'tier': 'notify'})
    out = handle('/review-drafts')
    assert 'gmail.draft' in out


def test_add_course_then_entities_lists(pebble_home):
    from chat_commands import handle
    out = handle('/add-course 15-122 "Principles of IC"')
    assert '15-122' in out and 'Added course' in out
    out = handle('/entities course')
    assert '15-122' in out


def test_add_person(pebble_home):
    from chat_commands import handle
    out = handle('/add-person sarah@cmu.edu "Sarah Chen"')
    assert 'Sarah Chen' in out


def test_entity_suggestions_empty(pebble_home):
    from chat_commands import handle
    out = handle('/entity-suggestions')
    assert 'No suggestions' in out


def test_entity_suggestions_accept(pebble_home):
    import entity_suggest, entity_store
    entity_suggest.observe([{'from_email': 'pal@cmu.edu', 'from_name': 'Pal'}] * 4)
    from chat_commands import handle
    out = handle('/entity-suggestions --accept pal@cmu.edu')
    assert 'Accepted' in out
    assert entity_store.lookup('pal@cmu.edu', type='person') is not None


def test_audit_command_shows_rows(pebble_home):
    import audit
    audit.append({'module': 'x', 'action': 'y', 'tier': 'auto', 'source': 'test'})
    from chat_commands import handle
    out = handle('/audit 5')
    assert 'x.y' in out


def test_errors_command_empty(pebble_home):
    from chat_commands import handle
    out = handle('/errors')
    assert 'No errors' in out


def test_briefing_returns_async_command(pebble_home):
    from chat_commands import handle, AsyncCommand
    out = handle('/briefing')
    assert isinstance(out, AsyncCommand)
    assert 'briefing' in out.label.lower()


def test_wrapup_returns_async_command(pebble_home):
    from chat_commands import handle, AsyncCommand
    out = handle('/wrapup')
    assert isinstance(out, AsyncCommand)


def test_exam_prep_validates_args(pebble_home):
    from chat_commands import handle
    out = handle('/exam-prep')  # missing args
    assert 'Usage' in out


def test_forget_returns_helpful_message_with_no_vault(pebble_home):
    """Without a vault configured, /forget gracefully says so.
    (The vault-backed forget queues a proposal — covered in test_memory.py.)"""
    from chat_commands import handle
    out = handle('/forget zzz')
    assert isinstance(out, str)
    # Either "No vault" or a normal "no matches" / "Queued" depending on whether
    # a vault is configured for this test run
    assert ('vault' in out.lower() or 'Nothing' in out or 'Queued' in out)
