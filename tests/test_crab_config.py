"""Smoke tests for crab_config — get/set roundtrip, defaults."""

from __future__ import annotations

from pathlib import Path


def test_config_save_uses_atomic_writer(pebble_home, monkeypatch):
    """crab_config writes MUST go through atomic_io.write_json (contracts.md §11).

    config.json holds API keys and per-action tier overrides and is written by
    more than one process; a plain (non-atomic) write_text risks a torn read that
    silently drops a tier override (ASK -> its weaker default). This test fails if
    _save() ever reverts to Path.write_text instead of the atomic writer.
    """
    import atomic_io
    import crab_config

    calls = []
    real = atomic_io.write_json

    def spy(path, data, **kw):
        calls.append(Path(path).name)
        return real(path, data, **kw)

    monkeypatch.setattr(atomic_io, 'write_json', spy)

    crab_config.set_value('tiers', {'gmail': {'send': 'ask'}})

    # contract: the atomic writer was used ...
    assert calls, 'config save did not go through atomic_io.write_json'
    # ... and the value actually persisted (real behavior, via a real atomic write)
    assert crab_config.get('tiers') == {'gmail': {'send': 'ask'}}


def test_get_default_returns_default(pebble_home):
    import crab_config
    assert crab_config.get('nonexistent_key', 'default') == 'default'


def test_set_and_get_roundtrip(pebble_home):
    import crab_config
    crab_config.set_value('test_key', {'nested': 'value'})
    assert crab_config.get('test_key') == {'nested': 'value'}


def test_get_all_returns_dict(pebble_home):
    import crab_config
    crab_config.set_value('a', 1)
    crab_config.set_value('b', 2)
    all_ = crab_config.get_all()
    assert all_.get('a') == 1
    assert all_.get('b') == 2
