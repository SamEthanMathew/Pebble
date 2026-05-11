"""Smoke tests for crab_config — get/set roundtrip, defaults."""

from __future__ import annotations



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
