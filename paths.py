"""Single source of truth for where Pebble keeps its data on disk.

Historically ~/.pebble was hardcoded in ~39 modules as
``Path.home() / '.pebble' / ...``. That blocks (a) relocating the data dir
per-OS convention, (b) a future sync layer that needs one place that knows
"where does Pebble state live", and (c) tests overriding the location without
monkeypatching ``Path.home()``.

All resolvers compute lazily (at call time), so they honour both the
``PEBBLE_HOME`` environment override and a monkeypatched ``Path.home()`` used
by the existing ``pebble_home`` test fixture — no import-time capture.

Set ``PEBBLE_HOME`` to relocate everything (e.g. to
``%APPDATA%/Pebble`` on Windows or ``$XDG_DATA_HOME/pebble`` on Linux).
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = 'PEBBLE_HOME'


def data_dir() -> Path:
    """The root Pebble data directory. ``PEBBLE_HOME`` overrides; else ~/.pebble."""
    override = os.environ.get(_ENV_VAR)
    return Path(override) if override else Path.home() / '.pebble'


def state_dir() -> Path:
    """Planner state-doc envelopes (~/.pebble/state)."""
    return data_dir() / 'state'


def secrets_dir() -> Path:
    """OAuth client configs and API tokens (~/.pebble/secrets)."""
    return data_dir() / 'secrets'


def errors_dir() -> Path:
    """Daily structured error reports (~/.pebble/errors)."""
    return data_dir() / 'errors'


def workspace_dir() -> Path:
    """Vault-adjacent working area — aliases, proposals, migration reports,
    write log, tmp (~/.pebble/workspace)."""
    return data_dir() / 'workspace'


def config_path() -> Path:
    """Top-level config.json."""
    return data_dir() / 'config.json'
