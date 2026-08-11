"""Health registry for Pebble's background loops and watchers.

A proactive assistant that silently swallows loop exceptions is worse than one
that crashes: the user trusts it's watching Gmail/Calendar while a dead watcher
notices nothing. Each polling loop calls `beat()` on a successful cycle and
`record_error()` on failure (instead of `except Exception: pass`); `/health`
renders the snapshot.

State is persisted atomically to ~/.pebble/health.json so the chat surface
(a separate process today) can read what the tray-process loops recorded.
"""

from __future__ import annotations

import datetime
import threading
from typing import Any

import paths
from atomic_io import read_json, write_json

_lock = threading.Lock()


def _path():
    return paths.data_dir() / 'health.json'


def _now_iso() -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec='seconds').replace('+00:00', 'Z'))


def _load() -> dict[str, dict[str, Any]]:
    return read_json(_path(), default={}) or {}


def beat(source: str) -> None:
    """Record a successful cycle of a named loop/watcher."""
    with _lock:
        data = _load()
        row = data.setdefault(source, {})
        row['last_beat'] = _now_iso()
        row['beats'] = int(row.get('beats', 0)) + 1
        write_json(_path(), data)


def record_error(source: str, err: BaseException | str) -> None:
    """Record a failure of a named loop/watcher (replaces `except: pass`)."""
    with _lock:
        data = _load()
        row = data.setdefault(source, {})
        row['last_error'] = str(err)
        row['last_error_type'] = (type(err).__name__
                                  if isinstance(err, BaseException) else 'str')
        row['last_error_at'] = _now_iso()
        row['errors'] = int(row.get('errors', 0)) + 1
        write_json(_path(), data)


def snapshot() -> dict[str, dict[str, Any]]:
    """A read-only copy of the current health state, keyed by source name."""
    with _lock:
        return _load()


def reset() -> None:
    """Clear all health state (tests / a fresh session)."""
    with _lock:
        p = _path()
        try:
            p.unlink()
        except FileNotFoundError:
            pass
