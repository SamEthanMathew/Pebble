"""EntityResolver: precedence (exact → alias → frontmatter → fuzzy) + stub creation."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def temp_vault(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    root = tmp_path / 'Vault'
    (root / '07 - People').mkdir(parents=True)
    (root / '02 - Academia' / '15-122').mkdir(parents=True)
    (root / '05 - Projects' / 'Plynko3').mkdir(parents=True)

    (root / '07 - People' / 'Amber Li.md').write_text('''---
tags: [people, advisor]
role: Research Advisor
aliases: [Dr Li, Amber]
---
# Amber Li
''', encoding='utf-8')

    (root / '02 - Academia' / '15-122' / '_Index.md').write_text('''---
tags: [academia, course]
course_id: 15-122
aliases: [PIC, Imperative]
---
# 15-122 Principles of Imperative Computation
''', encoding='utf-8')

    (root / '05 - Projects' / 'Plynko3' / '_Index.md').write_text('''---
tags: [project]
status: active
---
# Plynko3
''', encoding='utf-8')

    return root


def test_exact_basename_match(temp_vault):
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('Amber Li')
    assert res and res[0].note.id == '07 - People/Amber Li'
    assert res[0].source == 'exact'
    assert res[0].confidence == 1.0
    v.stop()


def test_case_insensitive_exact(temp_vault):
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('AMBER LI')
    assert res and res[0].note.title == 'Amber Li'
    v.stop()


def test_frontmatter_alias_match(temp_vault):
    """`aliases: [Dr Li, Amber]` on Amber's note → 'Dr Li' resolves to her."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('Dr Li')
    assert res and res[0].note.title == 'Amber Li'
    assert res[0].source == 'frontmatter'
    v.stop()


def test_course_code_alias(temp_vault):
    """'PIC' is in 15-122's aliases frontmatter."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('PIC')
    assert res
    assert '15-122' in res[0].note.id
    v.stop()


def test_aliases_yml_overrides_fuzzy(temp_vault, monkeypatch):
    """An alias entry in workspace/aliases.yml resolves before fuzzy."""
    # Write aliases.yml under the redirected home
    home = Path.home() / '.pebble' / 'workspace'
    home.mkdir(parents=True, exist_ok=True)
    (home / 'aliases.yml').write_text('''
aliases:
  Plynko: "05 - Projects/Plynko3/_Index"
''', encoding='utf-8')

    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('Plynko')
    assert res
    assert res[0].source == 'alias'
    assert 'Plynko3' in res[0].note.id
    v.stop()


def test_fuzzy_match_for_typos(temp_vault):
    """'Amer Li' (typo) should still find Amber Li via rapidfuzz."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('Amer Li')
    assert res
    assert res[0].note.title == 'Amber Li'
    assert res[0].source == 'fuzzy'
    assert 0.85 <= res[0].confidence <= 1.0
    v.stop()


def test_no_match(temp_vault):
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    res = er.resolve('completely-unrelated-string-xyz')
    assert res == []
    v.stop()


def test_resolve_or_create_returns_existing_match(temp_vault):
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    r = er.resolve_or_create('Amber Li')
    assert r.note is not None
    assert r.source == 'exact'
    # No stub created — file count unchanged
    assert v.note_count() == 3
    v.stop()


def test_resolve_or_create_creates_stub_for_course_code(temp_vault):
    """Unknown course code '21-127' looks like an entity → stub gets created."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    r = er.resolve_or_create('21-127', trigger='manual')
    assert r.note is not None
    assert r.source == 'fuzzy'  # via stub-creation path
    assert r.suggested_creation is not None
    assert r.suggested_creation['entity_type'] == 'course'
    # The created note IS source: pebble
    assert r.note.source == 'pebble'
    assert r.note.frontmatter['source_trigger'] == 'manual'
    # File is on disk in 02 - Academia
    assert '02 - Academia' in str(r.note.path)
    v.stop()


def test_resolve_or_create_skips_garbage(temp_vault):
    """A random short string isn't a real entity — no stub created."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    r = er.resolve_or_create('x')
    assert r.note is None
    assert r.source == 'none'
    v.stop()


def test_resolve_or_create_with_force_create(temp_vault):
    """force_create=True bypasses the looks_like_entity check."""
    from storage import Vault, EntityResolver
    v = Vault(temp_vault, autostart_watcher=False)
    er = EntityResolver(v)
    r = er.resolve_or_create('lowercase thing', force_create=True, trigger='test')
    assert r.note is not None
    v.stop()
