"""Tests for the robust extract_json_object helper.

Regression coverage for real-world LLM quirks seen during single-user testing:
- LLM wraps JSON in ```json ... ``` then adds prose afterward
- LLM omits closing fence
- LLM adds preamble before opening fence
- LLM returns plain JSON with no fence at all
"""

from __future__ import annotations

import pytest


def test_bare_json():
    from planners.base import extract_json_object
    assert extract_json_object('{"a": 1}') == {'a': 1}


def test_fenced_json_block():
    from planners.base import extract_json_object
    txt = '```json\n{"a": 1, "b": [2, 3]}\n```'
    assert extract_json_object(txt) == {'a': 1, 'b': [2, 3]}


def test_fenced_json_without_language():
    from planners.base import extract_json_object
    txt = '```\n{"x": "y"}\n```'
    assert extract_json_object(txt) == {'x': 'y'}


def test_fenced_with_trailing_prose():
    """The actual failure mode from the audit log: prose after the closing fence."""
    from planners.base import extract_json_object
    txt = '```json\n{"courses": [], "exam_plans": []}\n```\n\n**Note:** No data found.'
    assert extract_json_object(txt) == {'courses': [], 'exam_plans': []}


def test_fenced_with_leading_prose():
    from planners.base import extract_json_object
    txt = 'Here is the result:\n\n```json\n{"k": 1}\n```'
    assert extract_json_object(txt) == {'k': 1}


def test_fenced_with_both_prose_sides():
    from planners.base import extract_json_object
    txt = 'Sure! Here you go:\n```json\n{"answer": 42}\n```\nLet me know if that helps.'
    assert extract_json_object(txt) == {'answer': 42}


def test_no_fence_with_preamble():
    """LLM forgets the fence but pads with prose: brace-match extraction kicks in."""
    from planners.base import extract_json_object
    txt = 'I think the schedule is:\n{"date": "2026-05-11", "blocks": []}\nDoes that work?'
    assert extract_json_object(txt) == {'date': '2026-05-11', 'blocks': []}


def test_nested_braces_in_string():
    """Brace matcher must ignore { and } inside JSON string values."""
    from planners.base import extract_json_object
    txt = '{"note": "use {curly} carefully", "n": 1}'
    out = extract_json_object(txt)
    assert out == {'note': 'use {curly} carefully', 'n': 1}


def test_nested_objects():
    from planners.base import extract_json_object
    txt = 'Result: {"outer": {"inner": {"deep": 1}}, "x": [1, 2]} ok?'
    assert extract_json_object(txt) == {'outer': {'inner': {'deep': 1}}, 'x': [1, 2]}


def test_first_valid_fence_wins_when_multiple():
    """Multiple fences — first parseable one is returned."""
    from planners.base import extract_json_object
    txt = '```\n{"first": 1}\n```\n\nThen:\n\n```\n{"second": 2}\n```'
    assert extract_json_object(txt) == {'first': 1}


def test_skips_unparseable_first_fence():
    """If the first fence contains invalid JSON, fall back to the next."""
    from planners.base import extract_json_object
    txt = '```\nnot json at all\n```\n\n```json\n{"real": true}\n```'
    assert extract_json_object(txt) == {'real': True}


def test_empty_response_raises():
    import json
    from planners.base import extract_json_object
    with pytest.raises(json.JSONDecodeError):
        extract_json_object('')


def test_no_json_at_all_raises():
    import json
    from planners.base import extract_json_object
    with pytest.raises(json.JSONDecodeError):
        extract_json_object('I cannot help with that.')


def test_unbalanced_braces_raises():
    import json
    from planners.base import extract_json_object
    with pytest.raises(json.JSONDecodeError):
        extract_json_object('{"a": 1, "b": ')
