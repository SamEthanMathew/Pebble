"""Prompt loader tests — parse, render, slot validation."""

from __future__ import annotations

import pytest


def test_list_prompts_returns_seeded_set():
    from prompts import list_prompts
    names = list_prompts()
    expected = {
        'morning_briefing', 'email_draft', 'exam_prep',
        'meeting_prep', 'daily_wrapup', 'comms_triage',
        'schedule_planner', 'school_status',
    }
    assert expected.issubset(set(names))


def test_load_returns_metadata_and_body():
    from prompts import load
    tpl = load('morning_briefing')
    assert tpl.name == 'morning_briefing'
    assert tpl.version == 1
    assert tpl.model_tier == 'planner'
    assert 'datetime' in tpl.slots
    assert 'overdue_tasks' in tpl.slots
    assert tpl.body  # non-empty


def test_render_substitutes_slots():
    from prompts import render
    out = render('email_draft', {
        'style_profile':  '<style>',
        'relationship':   'professor',
        'thread':         '<thread text>',
        'intent':         'reschedule office hours',
        'memory_context': '<none>',
    })
    assert '<style>' in out
    assert 'professor' in out
    assert 'reschedule office hours' in out


def test_render_missing_slot_raises_key_error_with_useful_message():
    from prompts import render
    with pytest.raises(KeyError) as exc:
        render('email_draft', {'style_profile': 'x'})  # missing several slots
    msg = str(exc.value)
    assert 'email_draft' in msg
    assert 'thread' in msg or 'relationship' in msg or 'Missing slots' in msg


def test_load_missing_file_raises():
    from prompts import load
    with pytest.raises(FileNotFoundError):
        load('does_not_exist')


def test_render_preserves_literal_braces_in_body():
    """exam_prep.md uses {{...}} for JSON examples — they should render as {...}."""
    from prompts import render
    out = render('exam_prep', {
        'course_name':            'Calc',
        'course_code':            '21-122',
        'exam_date':              '2026-05-15',
        'days_remaining':         3,
        'topics':                 'limits, derivatives',
        'obsidian_notes_summary': '<none>',
        'available_blocks':       '14:00-16:00',
    })
    # The output should contain literal `{` (from `{{`) and not `{{`
    assert '{' in out
    assert '{{' not in out
    assert '"course"' in out
