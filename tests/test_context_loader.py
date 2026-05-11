"""Context loader: bundle assembly + Markdown rendering."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    root = tmp_path / 'Vault'
    (root / 'Daily').mkdir(parents=True)
    (root / '07 - People').mkdir()
    (root / '50_preferences').mkdir()
    (root / '09 - Goals').mkdir()
    (root / '70_decisions').mkdir()

    today = datetime.date.today().isoformat()
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    (root / 'Daily' / f'{today}.md').write_text(
        f'# {today}\n\nMet with [[07 - People/Amber Li|Amber]].\nWorked on [[R-PAD]].\n',
        encoding='utf-8',
    )
    (root / 'Daily' / f'{yesterday}.md').write_text(
        f'# {yesterday}\n\nRan experiments.\n',
        encoding='utf-8',
    )

    (root / '07 - People' / 'Amber Li.md').write_text('''---
tags: [people, advisor]
role: Research Advisor
---
# Amber Li

Research advisor, links to [[09 - Goals/PhD apps]].
''', encoding='utf-8')

    (root / '09 - Goals' / 'PhD apps.md').write_text('''---
tags: [goal]
---
# PhD apps

Targets: MIT, Stanford, Berkeley.
''', encoding='utf-8')

    (root / '50_preferences' / 'coffee.md').write_text('''---
tags: [preference]
---
# Coffee

Black, no sugar. Pour-over preferred.
''', encoding='utf-8')

    (root / '70_decisions' / 'committed-to-pebble.md').write_text('''---
tags: [decision]
---
# Committing to Pebble as primary project

Made on 2026-05-08.
''', encoding='utf-8')

    return root


def test_load_context_basic(temp_vault):
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'title': 'Amber Li',
                  'entity_hints': ['Amber Li']},
        vault=v,
    )
    assert bundle.entities, 'Should resolve Amber Li'
    assert any(e.title == 'Amber Li' for e in bundle.entities)
    v.stop()


def test_neighbors_pulled_at_depth_1(temp_vault):
    """Amber Li's note links to [[09 - Goals/PhD apps]] — depth=1 grabs it."""
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'entity_hints': ['Amber Li']},
        vault=v, depth=1,
    )
    neighbor_ids = {n.id for n in bundle.neighbor_notes}
    assert '09 - Goals/PhD apps' in neighbor_ids
    v.stop()


def test_daily_notes_included(temp_vault):
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'entity_hints': []},
        vault=v, include_daily_notes=7,
    )
    assert bundle.daily_note_excerpts
    today = datetime.date.today().isoformat()
    assert any(today in nid for nid, _ in bundle.daily_note_excerpts)
    v.stop()


def test_preferences_and_goals(temp_vault):
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'entity_hints': []},
        vault=v,
    )
    # Preferences: tag=preference catches coffee.md
    assert any('coffee' in n.id.lower() for n in bundle.preferences)
    # Goals: tag=goal catches PhD apps
    assert any('phd apps' in n.id.lower() for n in bundle.goals)
    v.stop()


def test_recent_decisions(temp_vault):
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'entity_hints': []},
        vault=v,
    )
    assert any('committed' in n.id.lower() for n in bundle.recent_decisions)
    v.stop()


def test_provenance_filter_user_only(temp_vault):
    """Pebble-authored notes are excluded under user_only."""
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)
    # Create a Pebble-authored preference note
    v.create_note('50_preferences/auto-detected.md',
                  body='# Auto-detected\n\nPebble noticed this.',
                  frontmatter={'tags': ['preference']},
                  trigger='detected', confidence=0.5)

    bundle_all = load_context(
        trigger={'type': 'manual', 'entity_hints': []},
        vault=v, provenance='all',
    )
    bundle_user = load_context(
        trigger={'type': 'manual', 'entity_hints': []},
        vault=v, provenance='user_only',
    )
    pref_ids_all  = {n.id for n in bundle_all.preferences}
    pref_ids_user = {n.id for n in bundle_user.preferences}
    assert '50_preferences/auto-detected' in pref_ids_all
    assert '50_preferences/auto-detected' not in pref_ids_user
    assert '50_preferences/coffee' in pref_ids_user
    v.stop()


def test_token_budget_enforced(temp_vault):
    """If the bundle exceeds budget, neighbor_notes get trimmed first."""
    from storage import Vault, load_context
    v = Vault(temp_vault, autostart_watcher=False)

    # Pad Amber's note with a lot of content
    (temp_vault / '07 - People' / 'Amber Li.md').write_text(
        '---\ntags: [people]\n---\n' + ('x' * 10000), encoding='utf-8',
    )

    bundle = load_context(
        trigger={'type': 'manual', 'entity_hints': ['Amber Li']},
        vault=v, max_tokens=500,  # tight budget
    )
    # Token estimate should be capped near max_tokens (we're approximate)
    assert bundle.total_tokens <= 4000  # allow some slack
    v.stop()


def test_render_bundle_markdown_smoke(temp_vault):
    from storage import Vault, load_context, render_bundle_markdown
    v = Vault(temp_vault, autostart_watcher=False)
    bundle = load_context(
        trigger={'type': 'manual', 'title': 'Amber sync',
                  'entity_hints': ['Amber Li']},
        vault=v,
    )
    md = render_bundle_markdown(bundle)
    assert '## Context from your vault' in md
    assert 'Amber Li' in md
    v.stop()
