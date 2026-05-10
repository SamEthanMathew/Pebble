"""First-time-action ledger — persists what the user has approved before.

Per docs/contracts.md §6. Two key conventions:
- Most actions: `<module>.<action>`              e.g. `gmail.draft`, `tasks.complete`
- Outbound sends: `<module>.<action>:<target>`  e.g. `gmail.send:professor@cmu.edu`

Autonomy layer asks the user on first-time keys regardless of the declared tier;
once approved, the ledger remembers and the declared tier applies on subsequent runs.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from atomic_io import read_json, write_json

_LEDGER_PATH = Path.home() / '.pebble' / 'first_time_seen.json'


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def make_key(module: str, action: str, target_id: str | None = None) -> str:
    base = f'{module}.{action}'
    if target_id:
        return f'{base}:{target_id}'
    return base


def _load() -> dict[str, dict[str, Any]]:
    return read_json(_LEDGER_PATH, default={}) or {}


def is_first_time(key: str) -> bool:
    return key not in _load()


def record(key: str) -> None:
    """Insert (first time) or increment count."""
    data = _load()
    if key in data:
        data[key]['count'] = int(data[key].get('count', 0)) + 1
        data[key]['last_seen'] = _now_iso()
    else:
        data[key] = {'first_seen': _now_iso(), 'last_seen': _now_iso(), 'count': 1}
    write_json(_LEDGER_PATH, data)


def forget(key: str) -> bool:
    """Remove a key (so it asks again next time). Returns True if removed."""
    data = _load()
    if key in data:
        del data[key]
        write_json(_LEDGER_PATH, data)
        return True
    return False


def keys() -> list[str]:
    return sorted(_load().keys())


def info(key: str) -> dict[str, Any] | None:
    return _load().get(key)


def path() -> Path:
    return _LEDGER_PATH
