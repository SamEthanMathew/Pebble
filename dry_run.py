"""Dry-run mode — when enabled, planners and autonomy write previews instead
of calling external APIs.

Schema per docs/contracts.md §5. Controlled by config.dry_run.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import crab_config
import paths
from atomic_io import write_json

_PREVIEW_DIR = paths.data_dir() / 'dry_run_previews'


def is_enabled() -> bool:
    """True if dry-run mode is active per config."""
    return bool(crab_config.get('dry_run', False))


def set_enabled(enabled: bool) -> None:
    crab_config.set_value('dry_run', bool(enabled))


def _sanitize(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', s)[:40]


def _now_compact() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def write_preview(action: dict[str, Any]) -> Path:
    """Write a dry-run preview file for an action that would have run.

    Required keys in `action`: module, action, args, source, tier.
    Optional: note.
    Returns the path to the written preview.
    """
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    ts      = _now_compact()
    module  = _sanitize(action.get('module', 'unknown'))
    act     = _sanitize(action.get('action', 'unknown'))
    fname   = f'{ts}_{module}_{act}.json'
    path    = _PREVIEW_DIR / fname

    payload = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'module':    action.get('module'),
        'action':    action.get('action'),
        'args':      action.get('args', {}),
        'source':    action.get('source', 'unknown'),
        'tier':      action.get('tier'),
        'note':      action.get('note', f'Would have run {action.get("module")}.{action.get("action")}.'),
    }
    write_json(path, payload)
    return path


def list_previews(limit: int = 50) -> list[dict[str, Any]]:
    """Return the most recent previews, newest first."""
    if not _PREVIEW_DIR.exists():
        return []
    files = sorted(_PREVIEW_DIR.glob('*.json'), key=lambda p: p.name, reverse=True)
    out: list[dict[str, Any]] = []
    for p in files[:limit]:
        try:
            import json
            data = json.loads(p.read_text(encoding='utf-8'))
            data['_preview_path'] = str(p)
            out.append(data)
        except Exception:
            continue
    return out


def clear_previews() -> int:
    """Delete all previews. Returns count removed."""
    if not _PREVIEW_DIR.exists():
        return 0
    n = 0
    for p in _PREVIEW_DIR.glob('*.json'):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def preview_dir() -> Path:
    return _PREVIEW_DIR
