"""Tests for paths.py — the single Pebble data-directory resolver.

paths.py replaces ~/.pebble hardcoded across ~39 modules with one overridable
source, so the data dir can be relocated per-OS or for tests without
monkeypatching Path.home().
"""

from __future__ import annotations

from pathlib import Path


def test_data_dir_defaults_to_dot_pebble_under_home(monkeypatch, tmp_path):
    """With no override, data_dir() is <home>/.pebble."""
    monkeypatch.delenv('PEBBLE_HOME', raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    import paths
    assert paths.data_dir() == tmp_path / '.pebble'


def test_pebble_home_env_overrides_data_dir(monkeypatch, tmp_path):
    """PEBBLE_HOME env var relocates the whole data dir (per-OS / sync / tests)."""
    override = tmp_path / 'custom_location'
    monkeypatch.setenv('PEBBLE_HOME', str(override))
    import paths
    assert paths.data_dir() == override


def test_subdir_helpers_live_under_data_dir(monkeypatch, tmp_path):
    """state/secrets/errors dirs and config.json all resolve under data_dir()."""
    monkeypatch.delenv('PEBBLE_HOME', raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    import paths
    base = paths.data_dir()
    assert paths.state_dir() == base / 'state'
    assert paths.secrets_dir() == base / 'secrets'
    assert paths.errors_dir() == base / 'errors'
    assert paths.workspace_dir() == base / 'workspace'
    assert paths.config_path() == base / 'config.json'
