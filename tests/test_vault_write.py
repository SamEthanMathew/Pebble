"""Vault write-side: chokepoint, provenance invariant, promotion, proposals."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path, monkeypatch) -> Path:
    """A minimal temp vault. Also redirects ~/.pebble/workspace/ so write_log
    and proposals.jsonl go to a per-test tmp dir.
    """
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    root = tmp_path / 'Vault'
    (root / 'Daily').mkdir(parents=True)
    (root / '07 - People').mkdir()

    # Seed an existing user-authored note
    (root / '07 - People' / 'Amber Li.md').write_text('''---
tags: [people, advisor]
role: Research Advisor
---
# Amber Li

Frequent collaborator.
''', encoding='utf-8')

    return root


# ── create_note ──────────────────────────────────────────────────────────────

def test_create_note_stamps_pebble_frontmatter(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    note = v.create_note(
        '40_companies/HALO.md',
        body='# HALO\n\nStub from calendar event.',
        frontmatter={'tags': ['company'], 'status': 'tracking'},
        trigger='calendar_event', confidence=0.6,
    )
    assert note.source == 'pebble'
    assert note.frontmatter['source'] == 'pebble'
    assert note.frontmatter['source_trigger'] == 'calendar_event'
    assert note.frontmatter['source_confidence'] == 0.6
    assert 'company' in note.tags
    # File exists on disk with the markers
    file_text = (temp_vault / '40_companies' / 'HALO.md').read_text(encoding='utf-8')
    assert 'source: pebble' in file_text
    v.stop()


def test_create_note_with_source_user(temp_vault):
    """LLM tool writes (user-driven) get source: user, not pebble."""
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    note = v.create_note(
        'My Note.md', body='Hand-written content',
        frontmatter={'tags': ['note']},
        source='user', trigger='llm_tool_write', confidence=1.0,
    )
    assert note.source == 'user'
    assert note.frontmatter['source'] == 'user'
    assert 'source_trigger' not in note.frontmatter  # only stamped for pebble
    v.stop()


def test_create_note_refuses_path_outside_vault(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    with pytest.raises(ValueError):
        v.create_note('../escape.md', body='nope', trigger='t', confidence=0.5)
    v.stop()


def test_create_note_preserves_existing_source(temp_vault):
    """Calling create_note on a path that's already source: pebble must not
    change the source field (idempotent on the marker)."""
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    n1 = v.create_note('Stub.md', 'first body', frontmatter={'tags': ['x']},
                        trigger='t', confidence=0.4)
    date1 = n1.frontmatter['source_date']

    # Second create with different content — source should be preserved as pebble
    n2 = v.create_note('Stub.md', 'updated body', frontmatter={'tags': ['x']},
                        trigger='t2', confidence=0.6)
    assert n2.source == 'pebble'
    assert n2.frontmatter['source'] == 'pebble'
    # source_date stays from the first create (stamp_frontmatter is no-op when
    # source is already present)
    assert n2.frontmatter['source_date'] == date1
    v.stop()


def test_create_note_refuses_to_clobber_user_content(temp_vault):
    """Writing source: pebble over an existing source: user note must raise."""
    from storage import Vault, ProvenanceViolation
    v = Vault(temp_vault, autostart_watcher=False)
    # Pre-create a user note via Vault (source: user)
    v.create_note('Sacred.md', 'do not touch',
                  source='user', trigger='setup', confidence=1.0)
    # Pebble tries to clobber it
    with pytest.raises(ProvenanceViolation):
        v.create_note('Sacred.md', 'Pebble overwrite attempt',
                      trigger='bad', confidence=0.3)
    v.stop()


# ── append_block ─────────────────────────────────────────────────────────────

def test_append_block_to_user_note(temp_vault):
    """Appending a [!pebble]+ callout to Amber's user-authored note works
    without changing the note's source: user-ness."""
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    note = v.append_block(
        '07 - People/Amber Li',
        'Meeting: discussed PyRoki integration with Sam.\n- Action: try Task 07',
        trigger='meeting_end', confidence=0.85, title='Sync 2026-05-11',
    )
    # Amber's note stays source: user (no source field added) — only the block has provenance
    assert note.source == 'user'
    assert 'source' not in note.frontmatter
    # The block is now parseable
    assert note.pebble_blocks
    b = note.pebble_blocks[0]
    assert b.trigger == 'meeting_end'
    assert b.confidence == 0.85
    assert 'PyRoki integration' in b.body
    v.stop()


def test_append_block_to_missing_note_raises(temp_vault):
    from storage import Vault, NoteNotFound
    v = Vault(temp_vault, autostart_watcher=False)
    with pytest.raises(NoteNotFound):
        v.append_block('does/not/exist', 'x', trigger='t', confidence=0.5)
    v.stop()


def test_append_block_preserves_prior_pebble_blocks(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    v.append_block('07 - People/Amber Li', 'First block',
                   trigger='t1', confidence=0.5)
    v.append_block('07 - People/Amber Li', 'Second block',
                   trigger='t2', confidence=0.5)
    note = v.read('07 - People/Amber Li')
    assert len(note.pebble_blocks) == 2
    triggers = {b.trigger for b in note.pebble_blocks}
    assert triggers == {'t1', 't2'}
    v.stop()


# ── daily_note(create_if_missing=True) actually creates ──────────────────────

def test_daily_note_create_if_missing(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    today = datetime.date.today().isoformat()

    # Pre: no daily note for today
    daily_path = temp_vault / 'Daily' / f'{today}.md'
    assert not daily_path.exists()

    note = v.daily_note('today', create_if_missing=True)
    assert daily_path.exists()
    assert note.frontmatter['source'] == 'pebble'
    assert note.frontmatter['source_trigger'] == 'daily_note_scaffold'
    assert 'daily' in note.tags
    v.stop()


# ── promote_note ─────────────────────────────────────────────────────────────

def test_promote_note_flips_to_user(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    n1 = v.create_note('Promote Me.md', body='content', frontmatter={},
                        trigger='t', confidence=0.5)
    assert n1.source == 'pebble'

    n2 = v.promote_note('Promote Me.md')
    assert n2.source == 'user'
    assert n2.frontmatter['source'] == 'user'
    assert n2.frontmatter['promoted_from_pebble'] is True
    assert 'promoted_at' in n2.frontmatter
    v.stop()


def test_promote_note_idempotent_on_user_note(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    note = v.promote_note('07 - People/Amber Li')
    # Was already user-authored; nothing changes
    assert note.source == 'user'
    assert 'promoted_from_pebble' not in note.frontmatter
    v.stop()


# ── mark_edited_by_user ──────────────────────────────────────────────────────

def test_mark_edited_by_user_makes_note_mixed(temp_vault):
    from storage import Vault, is_user_authored
    v = Vault(temp_vault, autostart_watcher=False)
    v.create_note('Stub.md', 'pebble body', frontmatter={'tags': ['x']},
                  trigger='t', confidence=0.5)
    note = v.mark_edited_by_user('Stub.md')
    assert note is not None
    assert note.source == 'mixed'
    assert 'source_edited_by_user' in note.frontmatter
    # user_only filter EXCLUDES mixed; mixed_ok INCLUDES it
    assert not is_user_authored(note.frontmatter)
    v.stop()


# ── write log ────────────────────────────────────────────────────────────────

def test_write_log_records_every_write(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    v.create_note('A.md', 'body a', trigger='t', confidence=0.5)
    v.append_block('A.md', 'block content', trigger='t2', confidence=0.7)
    v.promote_note('A.md')

    log_path = v._write_log_path
    assert log_path.exists()
    import json as _json
    rows = [_json.loads(l) for l in log_path.read_text(encoding='utf-8').splitlines() if l.strip()]
    actions = [r['action'] for r in rows]
    assert 'create_note' in actions
    assert 'append_block' in actions
    assert 'promote_note' in actions
    v.stop()


# ── ProposalQueue ────────────────────────────────────────────────────────────

def test_proposal_queue_lifecycle(temp_vault):
    from storage import ProposalQueue
    q = ProposalQueue()  # uses ~/.pebble/workspace (redirected by fixture)
    pid = q.add({'kind': 'alias', 'note_id': '07 - People/Amber Li',
                  'note': 'Suggest adding alias "Dr Li"', 'alias': 'Dr Li'})
    pending = q.list_pending()
    assert len(pending) == 1
    assert pending[0].id == pid
    assert pending[0].kind == 'alias'
    assert pending[0].payload['alias'] == 'Dr Li'

    assert q.accept(pid) is True
    assert q.list_pending() == []
    assert q.get(pid).status == 'accepted'

    # Idempotent
    assert q.accept(pid) is False  # already accepted


def test_proposal_queue_dismiss_and_postpone(temp_vault):
    from storage import ProposalQueue
    q = ProposalQueue()
    a = q.add({'kind': 'preference', 'note_id': '', 'topic': 'coffee'})
    b = q.add({'kind': 'preference', 'note_id': '', 'topic': 'sleep'})
    q.dismiss(a)
    q.postpone(b)
    statuses = {p.id: p.status for p in q.list_all()}
    assert statuses[a] == 'dismissed'
    assert statuses[b] == 'postponed'
    assert q.list_pending() == []  # neither is pending now


def test_propose_edit_uses_queue(temp_vault):
    from storage import Vault, ProposalQueue
    v = Vault(temp_vault, autostart_watcher=False)
    pid = v.propose_edit('07 - People/Amber Li',
                          {'add_alias': 'Dr Li', 'rationale': 'seen in 3 emails'})
    # The proposal is queued; the note is unchanged
    pending = ProposalQueue().list_pending()
    assert any(p.id == pid for p in pending)
    note = v.read('07 - People/Amber Li')
    assert note.source == 'user'  # unchanged
    v.stop()
