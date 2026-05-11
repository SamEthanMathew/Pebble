"""Vault read-side tests against a synthetic temp vault (so they're reproducible
regardless of the real Obsidian vault on this machine)."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path) -> Path:
    """Build a tiny realistic vault for tests, mirroring Sam's folder convention."""
    root = tmp_path / 'Vault'
    (root / '07 - People').mkdir(parents=True)
    (root / '05 - Projects' / 'Plynko3').mkdir(parents=True)
    (root / 'Daily').mkdir(parents=True)
    (root / '.obsidian').mkdir(parents=True)
    (root / '.obsidian' / 'app.json').write_text('{}', encoding='utf-8')

    # Person note with Sam's actual frontmatter shape
    (root / '07 - People' / 'Amber Li.md').write_text('''---
tags: [people, advisor]
role: Research Advisor
lab: R-PAD Lab
---
# Amber Li

Research advisor at [[03 - Research/R-PAD Lab|R-PAD]].
Frequent collaborator on PyRoki integration.
''', encoding='utf-8')

    # Project note
    (root / '05 - Projects' / 'Plynko3' / '_Index.md').write_text('''---
date: 2026-05-09
tags: [project]
status: active
context: Jump Trading
---
# Plynko3

Built [[03 - Research/Robotsmith|Robotsmith]] integration.
Status #open.
''', encoding='utf-8')

    # Today's daily note
    today = datetime.date.today().isoformat()
    (root / 'Daily' / f'{today}.md').write_text(f'''# {today}

- Ran R-PAD evals
- Met with [[07 - People/Amber Li|Amber]]
''', encoding='utf-8')

    # Pebble-authored stub (provenance markers)
    (root / 'Pebble Stub.md').write_text('''---
tags: [company]
source: pebble
source_date: 2026-05-11T14:00:00-04:00
source_trigger: calendar_event
source_confidence: 0.6
---
# HALO

Stub created from a calendar event.
''', encoding='utf-8')

    # Mixed-provenance: Pebble-authored but user has since edited
    (root / 'Mixed Note.md').write_text('''---
tags: [project]
source: pebble
source_date: 2026-05-10T09:00:00-04:00
source_trigger: detected
source_confidence: 0.7
source_edited_by_user: 2026-05-11T09:30:00-04:00
---
# Mixed

User edited this one.
''', encoding='utf-8')

    return root


def test_vault_loads_all_md_skipping_obsidian_dir(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    # 4 .md files; .obsidian/app.json is .json (skipped); none should slip in
    assert v.note_count() == 5
    v.stop()


def test_read_by_id(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    amber = v.read('07 - People/Amber Li')
    assert amber.frontmatter['role'] == 'Research Advisor'
    assert 'people' in amber.tags and 'advisor' in amber.tags
    assert amber.source == 'user'  # no source field → defaults to user
    v.stop()


def test_read_by_path_with_md_extension(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    n = v.read('07 - People/Amber Li.md')
    assert n.title == 'Amber Li'
    v.stop()


def test_read_missing_raises(temp_vault):
    from storage import Vault, NoteNotFound
    v = Vault(temp_vault, autostart_watcher=False)
    with pytest.raises(NoteNotFound):
        v.read('does/not/exist')
    v.stop()


def test_wikilinks_extracted(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    amber = v.read('07 - People/Amber Li')
    assert '03 - Research/R-PAD Lab' in amber.wikilinks


def test_find_by_frontmatter_tag(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    people = v.find_by_frontmatter(tag='people')
    assert len(people) == 1
    assert people[0].id == '07 - People/Amber Li'
    v.stop()


def test_find_by_frontmatter_status_value(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    active = v.find_by_frontmatter(status='active')
    assert any(n.id.endswith('Plynko3/_Index') for n in active)
    v.stop()


def test_search_ranks_by_term_frequency(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    hits = v.search('R-PAD', k=10)
    assert hits  # at least one
    # Top hit should mention R-PAD
    assert 'r-pad' in hits[0].excerpt.lower() or 'r-pad' in hits[0].note.body.lower()
    v.stop()


def test_daily_note_resolves_today(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    today = v.daily_note('today')
    assert today.id.startswith('Daily/')
    v.stop()


def test_daily_note_missing_raises_without_create(temp_vault):
    """create_if_missing=False (default) raises NoteNotFound for missing daily notes."""
    from storage import Vault, NoteNotFound
    v = Vault(temp_vault, autostart_watcher=False)
    with pytest.raises(NoteNotFound):
        v.daily_note('2020-01-01', create_if_missing=False)
    v.stop()


def test_recent_daily_notes(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    recent = v.recent_daily_notes(days=7)
    assert len(recent) == 1
    assert recent[0].id.startswith('Daily/')
    v.stop()


def test_provenance_filter_user_only_is_strict(temp_vault):
    """Per spec §2.3, user_only is STRICT — excludes BOTH pebble AND mixed."""
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    # All notes
    all_notes = v.list()
    assert len(all_notes) == 5

    # user_only excludes Pebble Stub AND Mixed Note (strict)
    user_only_ids = {n.id for n in v.list(provenance='user_only')}
    assert 'Pebble Stub' not in user_only_ids
    assert 'Mixed Note' not in user_only_ids
    assert '07 - People/Amber Li' in user_only_ids
    assert '05 - Projects/Plynko3/_Index' in user_only_ids

    # mixed_ok adds the user-edited Pebble note back in
    mixed_ok_ids = {n.id for n in v.list(provenance='mixed_ok')}
    assert 'Pebble Stub' not in mixed_ok_ids       # still excluded (pure pebble)
    assert 'Mixed Note' in mixed_ok_ids            # included (user has edited it)
    assert '07 - People/Amber Li' in mixed_ok_ids

    # pebble_only is the inverse — pure pebble + mixed
    pebble_only_ids = {n.id for n in v.list(provenance='pebble_only')}
    assert pebble_only_ids == {'Pebble Stub', 'Mixed Note'}
    v.stop()


def test_refresh_picks_up_external_edit(temp_vault):
    """If a file is edited on disk between reads, the next read sees the new content."""
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    n1 = v.read('07 - People/Amber Li')
    original = n1.body

    # Modify the file externally with a NEW mtime
    p = temp_vault / '07 - People' / 'Amber Li.md'
    import time
    time.sleep(0.05)  # ensure mtime resolution
    p.write_text(p.read_text(encoding='utf-8') + '\n\nNew line!\n', encoding='utf-8')

    n2 = v.read('07 - People/Amber Li')
    assert 'New line' in n2.body
    assert n2.body != original
    v.stop()


def test_note_title_uses_frontmatter_then_basename(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    plynko = v.read('05 - Projects/Plynko3/_Index')
    # No `title:` field, falls back to basename
    assert plynko.title == '_Index'
    v.stop()


def test_inline_tags_in_body_added_to_tag_list(temp_vault):
    from storage import Vault
    v = Vault(temp_vault, autostart_watcher=False)
    plynko = v.read('05 - Projects/Plynko3/_Index')
    # frontmatter has [project], body has #open
    assert 'project' in plynko.tags
    assert 'open' in plynko.tags
    v.stop()
