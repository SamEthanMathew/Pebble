"""Provenance helpers: stamp, parse, invariant enforcement."""

from __future__ import annotations

import pytest


def test_stamp_frontmatter_adds_all_source_fields():
    from storage.provenance import stamp_frontmatter
    out = stamp_frontmatter({'tags': ['project']},
                             trigger='calendar_event', confidence=0.82,
                             note='drafted from event title')
    assert out['source'] == 'pebble'
    assert out['source_trigger'] == 'calendar_event'
    assert out['source_confidence'] == 0.82
    assert out['source_note'] == 'drafted from event title'
    assert 'source_date' in out
    # input not mutated
    assert 'source' not in {'tags': ['project']}


def test_stamp_frontmatter_preserves_existing_source():
    """If user already wrote `source: user`, never overwrite."""
    from storage.provenance import stamp_frontmatter
    out = stamp_frontmatter({'source': 'user', 'tags': []},
                             trigger='x', confidence=0.9)
    assert out['source'] == 'user'
    assert 'source_trigger' not in out  # didn't add any new pebble fields


def test_effective_source_resolution():
    from storage.provenance import effective_source
    assert effective_source({}) == 'user'
    assert effective_source({'source': 'user'}) == 'user'
    assert effective_source({'source': 'pebble'}) == 'pebble'
    assert effective_source({'source': 'pebble',
                             'source_edited_by_user': '2026-05-11T10:00'}) == 'mixed'


def test_mark_user_edited_is_idempotent():
    from storage.provenance import mark_user_edited
    fm = {'source': 'pebble', 'source_date': 'x'}
    once = mark_user_edited(fm)
    assert 'source_edited_by_user' in once
    twice = mark_user_edited(once)
    assert twice['source_edited_by_user'] == once['source_edited_by_user']


def test_mark_user_edited_skips_non_pebble():
    from storage.provenance import mark_user_edited
    out = mark_user_edited({'source': 'user'})
    assert 'source_edited_by_user' not in out


def test_promote_to_user_flips_and_records():
    from storage.provenance import promote_to_user
    out = promote_to_user({'source': 'pebble', 'source_trigger': 't'})
    assert out['source'] == 'user'
    assert out['promoted_from_pebble'] is True
    assert 'promoted_at' in out
    # original fields preserved
    assert out['source_trigger'] == 't'


def test_is_user_authored():
    """Per spec §2.3: `user_only` is STRICT — only source: user passes.
    Mixed (user-edited Pebble) notes pass `mixed_ok` but NOT `user_only`.
    """
    from storage.provenance import is_user_authored
    assert is_user_authored({})                                          # no source → user
    assert is_user_authored({'source': 'user'})
    assert not is_user_authored({'source': 'pebble'})
    # Mixed is NOT user-authored — spec calls user_only "strict"
    assert not is_user_authored({'source': 'pebble',
                                  'source_edited_by_user': '2026-05-11T10:00'})


def test_stamp_block_format():
    from storage.provenance import stamp_block, extract_pebble_blocks
    out = stamp_block(
        'line one\nline two',
        trigger='meeting_end', confidence=0.91, title='Sync notes',
    )
    assert out.startswith('> [!pebble]+ Sync notes')
    assert '<!-- pebble:source=pebble' in out
    assert '> line one' in out
    assert '> line two' in out

    # Roundtrip through the parser
    blocks = extract_pebble_blocks(out)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.label == 'pebble'
    assert b.title == 'Sync notes'
    assert b.trigger == 'meeting_end'
    assert b.confidence == 0.91
    assert 'line one' in b.body
    assert 'line two' in b.body


def test_extract_skips_non_pebble_callouts():
    """A user-written [!note] callout without the pebble comment isn't parsed."""
    from storage.provenance import extract_pebble_blocks
    body = '''> [!note] My own note
> just a regular callout
> no pebble marker here'''
    assert extract_pebble_blocks(body) == []


def test_assert_preserves_provenance_blocks_source_strip():
    from storage.provenance import (assert_preserves_provenance,
                                     ProvenanceViolation)
    with pytest.raises(ProvenanceViolation):
        assert_preserves_provenance(
            old_frontmatter={'source': 'pebble'},
            new_frontmatter={},
            old_body=None, new_body=None,
        )


def test_assert_preserves_provenance_allows_promotion():
    """source: pebble → source: user IS allowed when promoted_from_pebble is set."""
    from storage.provenance import assert_preserves_provenance
    # Must not raise
    assert_preserves_provenance(
        old_frontmatter={'source': 'pebble'},
        new_frontmatter={'source': 'user', 'promoted_from_pebble': True,
                          'promoted_at': '2026-05-11T10:00'},
        old_body=None, new_body=None,
    )


def test_assert_preserves_provenance_blocks_pebble_block_removal():
    from storage.provenance import (assert_preserves_provenance,
                                     stamp_block, ProvenanceViolation)
    old_body = stamp_block('hi', trigger='t', confidence=0.5)
    with pytest.raises(ProvenanceViolation):
        assert_preserves_provenance(
            old_frontmatter=None, new_frontmatter=None,
            old_body=old_body, new_body='just plain content',
        )


def test_assert_preserves_provenance_allows_unrelated_writes():
    """Replacing body content that has no pebble blocks is fine."""
    from storage.provenance import assert_preserves_provenance
    assert_preserves_provenance(
        old_frontmatter={}, new_frontmatter={},
        old_body='hello', new_body='hello world',
    )
