"""Setup wizard first-run defaults: dry_run=true + opinionated tier set."""

from __future__ import annotations


def test_first_run_seeds_dry_run_and_tiers(pebble_home):
    import crab_config
    from setup_wizard import _mark_setup_complete

    assert not crab_config.get('setup_complete')
    assert crab_config.get('dry_run') is None

    _mark_setup_complete()

    assert crab_config.get('setup_complete') is True
    assert crab_config.get('dry_run') is True
    tiers = crab_config.get('tiers')
    assert tiers and tiers.get('gmail', {}).get('send') == 'ask'
    assert tiers.get('gmail', {}).get('draft') == 'notify'
    assert tiers.get('memory', {}).get('recall') == 'auto'


def test_first_run_defaults_idempotent(pebble_home):
    import crab_config
    from setup_wizard import _mark_setup_complete

    _mark_setup_complete()
    # User goes and tweaks dry_run off
    crab_config.set_value('dry_run', False)
    _mark_setup_complete()  # should NOT re-seed dry_run=True
    assert crab_config.get('dry_run') is False


def test_existing_user_not_overwritten(pebble_home):
    """If user has tiers already (returning user), defaults must not clobber."""
    import crab_config
    custom = {'gmail': {'send': 'auto'}}  # very permissive — user's choice
    crab_config.set_value('tiers', custom)

    from setup_wizard import _apply_first_run_defaults
    _apply_first_run_defaults()
    assert crab_config.get('tiers') == custom
