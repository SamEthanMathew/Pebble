"""Phase E: thinking passes (run_pass, routing) + schedule predicate."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest


@pytest.fixture
def vault_with_planner(tmp_path, monkeypatch, mock_backend):
    """Build a temp vault, configure obsidian + a fake planner_model, return vault root."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble').mkdir(parents=True, exist_ok=True)

    vault = tmp_path / 'Vault'
    (vault / 'Daily').mkdir(parents=True)
    (vault / '70_decisions').mkdir()
    (vault / '07 - People').mkdir()

    # Seed some user-authored content
    today = datetime.date.today().isoformat()
    (vault / 'Daily' / f'{today}.md').write_text(
        f'# {today}\n\nWorked on Plynko3.\nDecided to commit to PyRoki integration.\n',
        encoding='utf-8',
    )
    (vault / '07 - People' / 'Amber Li.md').write_text(
        '---\ntags: [people, advisor]\nrole: Research Advisor\n---\n# Amber Li\n',
        encoding='utf-8',
    )

    # Configure crab_config
    import crab_config, importlib
    importlib.reload(crab_config)
    crab_config.set_module_config('obsidian', {'enabled': True,
                                                 'vault_path': str(vault)})
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                       'model_name': 'claude', 'api_key': 'fake',
                                       'enabled': True, 'display_name': 'Fake'}])
    # Reload storage modules so they see the redirected ~/.pebble
    for m in ('storage.vault', 'storage.proposal_queue',
              'storage.entity_resolver', 'storage.context_loader',
              'storage.thinking_pass', 'storage.thinking_schedule',
              'storage'):
        if m in __import__('sys').modules:
            importlib.reload(__import__('sys').modules[m])

    return vault


# ── run_pass routing tests (with mocked LLM) ─────────────────────────────────

def test_emerge_pass_writes_digest_note(vault_with_planner, mock_backend):
    mock_backend.set_response('Pattern 1: You write about robotics a lot.')
    from storage import run_pass
    r = run_pass('emerge')
    assert r.success
    assert r.output_note_id is not None
    assert 'digests' in r.output_note_id
    assert 'emerge' in r.output_note_id

    # File exists with source: pebble
    text = Path(r.output_path).read_text(encoding='utf-8')
    assert 'source: pebble' in text
    assert 'Pattern 1' in text


def test_drift_pass_uses_mixed_ok_provenance(vault_with_planner, mock_backend):
    mock_backend.set_response('Goal X: aligned. Goal Y: drifting.')
    from storage import run_pass, PASS_REGISTRY
    assert PASS_REGISTRY['drift'].provenance == 'mixed_ok'
    r = run_pass('drift')
    assert r.success
    assert r.metadata['provenance'] == 'mixed_ok'


def test_ghost_returns_text_no_vault_write(vault_with_planner, mock_backend):
    """ghost returns text directly; doesn't write a digest."""
    mock_backend.set_response('Probably: I think Rust is great for systems.')
    from storage import run_pass
    r = run_pass('ghost',
                  extra_slots={'question': 'what do you think about Rust?'})
    assert r.success
    assert r.text
    assert r.output_note_id is None     # no vault write for chat-kind passes


def test_close_appends_reflection_block_to_daily(vault_with_planner, mock_backend):
    mock_backend.set_response('- Was the meeting actually productive?\n- Why did you skip the gym?')
    from storage import run_pass
    r = run_pass('close')
    assert r.success
    assert r.output_note_id is not None
    assert r.output_note_id.startswith('Daily/')

    # Daily note now has a [!reflection]+ callout
    text = Path(r.output_path).read_text(encoding='utf-8')
    assert '[!reflection]+' in text
    assert 'pebble:source=pebble' in text


def test_challenge_requires_target_note(vault_with_planner, mock_backend):
    from storage import run_pass
    mock_backend.set_response('Pushback: have you actually validated demand?')
    # No target → error
    r = run_pass('challenge', extra_slots={'decision_text': 'I will build X'})
    assert not r.success
    assert 'target_note_id' in (r.error or '')


def test_challenge_appends_to_target_note(vault_with_planner, mock_backend):
    """challenge against an existing decision note appends a [!challenge]+ block."""
    # Create a decision note
    vault = vault_with_planner
    (vault / '70_decisions' / 'commit-pyroki.md').write_text(
        '---\ntags: [decision]\n---\n# Commit to PyRoki\n\nDecided this week.\n',
        encoding='utf-8',
    )
    mock_backend.set_response('What if MoveIt is better for your workload?')
    from storage import run_pass
    r = run_pass('challenge',
                  extra_slots={'decision_text': 'Decided this week.'},
                  target_note_id='70_decisions/commit-pyroki')
    assert r.success
    text = (vault / '70_decisions' / 'commit-pyroki.md').read_text(encoding='utf-8')
    assert '[!challenge]+' in text
    assert 'MoveIt' in text


def test_unknown_pass_returns_error(vault_with_planner):
    from storage import run_pass
    r = run_pass('nonexistent')
    assert not r.success
    assert 'Unknown pass' in (r.error or '')


def test_pass_disabled_without_planner_model(tmp_path, monkeypatch):
    """No planner_model configured → pass returns error, no LLM call attempted."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble').mkdir(parents=True, exist_ok=True)
    vault = tmp_path / 'Vault'; vault.mkdir()

    import crab_config, importlib
    importlib.reload(crab_config)
    crab_config.set_module_config('obsidian', {'enabled': True, 'vault_path': str(vault)})
    # NO planner_model set

    for m in ('storage.thinking_pass', 'storage'):
        if m in __import__('sys').modules:
            importlib.reload(__import__('sys').modules[m])

    from storage import run_pass
    r = run_pass('emerge')
    assert not r.success
    assert 'no planner_model' in (r.error or '').lower()


# ── Schedule predicate ────────────────────────────────────────────────────────

def test_schedule_disabled_pass_never_fires(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    now = datetime.datetime(2026, 5, 11, 21, 0)  # 9pm Monday
    cfg = {'enabled': False, 'hour': 21, 'minute': 0, 'days': 'daily'}
    assert ts.should_fire('close', now=now, cfg=cfg, last_fired_ledger={}) is False


def test_schedule_daily_fires_in_window(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    now = datetime.datetime(2026, 5, 11, 21, 5)  # 5 min after target
    cfg = {'enabled': True, 'hour': 21, 'minute': 0, 'days': 'daily'}
    assert ts.should_fire('close', now=now, cfg=cfg, last_fired_ledger={}) is True


def test_schedule_daily_not_fired_before_window(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    now = datetime.datetime(2026, 5, 11, 14, 0)  # too early
    cfg = {'enabled': True, 'hour': 21, 'minute': 0, 'days': 'daily'}
    assert ts.should_fire('close', now=now, cfg=cfg, last_fired_ledger={}) is False


def test_schedule_skips_after_window_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    # 90 minutes after the 21:00 target — past the 60-min slack window
    now = datetime.datetime(2026, 5, 11, 22, 30)
    cfg = {'enabled': True, 'hour': 21, 'minute': 0, 'days': 'daily'}
    assert ts.should_fire('close', now=now, cfg=cfg, last_fired_ledger={}) is False


def test_schedule_weekly_only_on_target_day(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    monday = datetime.datetime(2026, 5, 11, 20, 0)  # Mon
    sunday = datetime.datetime(2026, 5, 10, 20, 0)  # Sun
    cfg = {'enabled': True, 'hour': 20, 'minute': 0, 'days': 'sun'}
    assert ts.should_fire('connect', now=monday, cfg=cfg, last_fired_ledger={}) is False
    assert ts.should_fire('connect', now=sunday, cfg=cfg, last_fired_ledger={}) is True


def test_schedule_first_sunday_of_month(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    # 2026-05-03 is the first Sunday of May
    first_sun = datetime.datetime(2026, 5, 3, 19, 0)
    # 2026-05-10 is the second Sunday
    second_sun = datetime.datetime(2026, 5, 10, 19, 0)
    cfg = {'enabled': True, 'hour': 19, 'minute': 0, 'days': 'first-sun-of-month'}
    assert ts.should_fire('emerge', now=first_sun, cfg=cfg, last_fired_ledger={}) is True
    assert ts.should_fire('emerge', now=second_sun, cfg=cfg, last_fired_ledger={}) is False


def test_schedule_ledger_prevents_same_day_refire(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    (tmp_path / 'home' / '.pebble' / 'workspace').mkdir(parents=True, exist_ok=True)
    import importlib, storage.thinking_schedule as ts
    importlib.reload(ts)

    now = datetime.datetime(2026, 5, 11, 21, 5)
    cfg = {'enabled': True, 'hour': 21, 'minute': 0, 'days': 'daily'}
    ledger = {'close': '2026-05-11'}
    assert ts.should_fire('close', now=now, cfg=cfg, last_fired_ledger=ledger) is False


# ── Decision-note detection ──────────────────────────────────────────────────

def test_is_decision_note_via_tag(vault_with_planner):
    from storage import Vault, is_decision_note
    v = Vault(vault_with_planner, autostart_watcher=False)
    # Create a note tagged 'decision'
    v.create_note('Some Decision.md', body='deciding',
                  frontmatter={'tags': ['decision']},
                  source='user', trigger='setup', confidence=1.0)
    n = v.read('Some Decision')
    assert is_decision_note(n) is True
    v.stop()


def test_is_decision_note_via_folder(vault_with_planner):
    from storage import Vault, is_decision_note
    v = Vault(vault_with_planner, autostart_watcher=False)
    v.create_note('70_decisions/My Choice.md', body='choosing',
                  frontmatter={'tags': []},
                  source='user', trigger='setup', confidence=1.0)
    n = v.read('70_decisions/My Choice')
    assert is_decision_note(n) is True
    v.stop()


def test_is_decision_note_false_for_random(vault_with_planner):
    from storage import Vault, is_decision_note
    v = Vault(vault_with_planner, autostart_watcher=False)
    v.create_note('Random.md', body='hi',
                  frontmatter={'tags': ['random']},
                  source='user', trigger='setup', confidence=1.0)
    n = v.read('Random')
    assert is_decision_note(n) is False
    v.stop()


def test_extract_decision_text_finds_decisive_paragraph(vault_with_planner):
    from storage import Vault, extract_decision_text
    v = Vault(vault_with_planner, autostart_watcher=False)
    v.create_note('70_decisions/X.md',
                  body='# X\n\nBackground info.\n\nDecided to go with option A on Tuesday.\n',
                  frontmatter={'tags': ['decision']},
                  source='user', trigger='setup', confidence=1.0)
    n = v.read('70_decisions/X')
    text = extract_decision_text(n)
    assert 'Decided to go with option A' in text
    v.stop()
