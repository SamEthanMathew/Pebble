"""Memory module (vault-backed): remember routes by category, recall uses
vault.search, forget queues proposals (never auto-deletes)."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def vault_memory(tmp_path, monkeypatch):
    """Configure a temp vault and redirect ~/.pebble to a per-test home."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    home = tmp_path / 'home' / '.pebble'
    home.mkdir(parents=True, exist_ok=True)

    vault = tmp_path / 'Vault'
    (vault / '.obsidian').mkdir(parents=True)

    # Configure crab_config to know about the vault BEFORE importing the
    # memory module (since modules are reloaded by conftest's pebble_home
    # fixture in other tests, but here we're standalone)
    import crab_config, importlib
    importlib.reload(crab_config)
    crab_config.set_module_config('obsidian',
                                   {'enabled': True, 'vault_path': str(vault)})
    import modules.memory as mem
    importlib.reload(mem)
    return vault


def test_remember_fact_creates_aggregate_note(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    out = m.execute(action='remember', text='I prefer Python over Java',
                    category='fact')
    assert 'Got it' in out
    facts = vault_memory / '_pebble_imports' / 'facts.md'
    assert facts.exists()
    text = facts.read_text(encoding='utf-8')
    assert 'I prefer Python over Java' in text
    assert 'source: pebble' in text


def test_remember_person_creates_single_note(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember',
              text='Sarah Chen leads the data team at OpenAI',
              category='person')
    found = list((vault_memory / '07 - People').glob('*.md'))
    assert found
    text = found[0].read_text(encoding='utf-8')
    assert 'Sarah Chen' in text
    assert 'source: pebble' in text


def test_remember_preference_groups_by_topic(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='I prefer dark mode',
              category='preference')
    found = list((vault_memory / '50_preferences').glob('*.md'))
    assert found  # at least one preference note created


def test_recall_finds_remembered_content(vault_memory):
    from modules.memory import MemoryModule, search
    m = MemoryModule({'enabled': True})
    m.execute(action='remember',
              text='Sarah Chen leads the data team',
              category='person')
    hits = search('Sarah')
    assert hits
    assert any('Sarah' in h['text'] for h in hits)


def test_recall_returns_empty_for_nothing_matching(vault_memory):
    from modules.memory import search
    assert search('quantum chromodynamics zzz') == []


def test_recall_via_execute(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='Pebble uses Obsidian as memory',
              category='fact')
    out = m.execute(action='recall', text='Obsidian')
    assert 'Obsidian' in out


def test_list_returns_memory_notes(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='Some fact', category='fact')
    m.execute(action='remember', text='Some pref', category='preference')
    out = m.execute(action='list')
    assert 'Memory' in out or 'note' in out.lower()


def test_forget_queues_proposal_not_delete(vault_memory):
    """forget should NEVER auto-delete — it queues a proposal."""
    from modules.memory import MemoryModule
    from storage import ProposalQueue
    m = MemoryModule({'enabled': True})
    m.execute(action='remember', text='Forget about this', category='fact')

    out = m.execute(action='forget', text='Forget')
    # No file deleted; proposals queued
    assert 'Queued' in out or 'proposal' in out.lower()
    pending = ProposalQueue().list_pending()
    assert pending
    # The note still exists on disk
    facts = vault_memory / '_pebble_imports' / 'facts.md'
    assert facts.exists()


def test_forget_with_no_match(vault_memory):
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    out = m.execute(action='forget', text='nothing-like-this')
    assert 'Nothing' in out or 'No' in out


def test_no_vault_gives_helpful_message(tmp_path, monkeypatch):
    """If Obsidian isn't configured, memory writes return a useful message
    instead of crashing."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble').mkdir(parents=True, exist_ok=True)

    import crab_config, importlib
    importlib.reload(crab_config)
    # Explicitly NO obsidian vault path configured
    crab_config.set_module_config('obsidian', {'enabled': False, 'vault_path': ''})
    import modules.memory as mem
    importlib.reload(mem)

    out = mem.MemoryModule({'enabled': True}).execute(
        action='remember', text='something', category='fact')
    assert 'vault' in out.lower()


def test_default_tiers_match_spec(vault_memory):
    from modules.base import ActionTier
    from modules.memory import MemoryModule
    m = MemoryModule({'enabled': True})
    assert m.action_tier('recall')   == ActionTier.AUTO
    assert m.action_tier('remember') == ActionTier.NOTIFY
    # forget tightened to ASK in vault-backed rewrite (never silently delete)
    assert m.action_tier('forget')   == ActionTier.ASK
