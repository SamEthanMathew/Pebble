"""Audit log — append-only JSONL of every non-read action Pebble takes.

Schema per docs/contracts.md §3. Used by Observability (read side, Phase 4)
and User-Feedback (weekly reports). Append failure must never crash callers.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

_AUDIT_PATH = Path.home() / '.pebble' / 'audit.jsonl'


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def append(record: dict[str, Any]) -> None:
    """Append one audit record. Fills in defaults; never raises."""
    try:
        full = {
            'timestamp':      record.get('timestamp', _now_iso()),
            'module':         record.get('module', 'unknown'),
            'action':         record.get('action', 'unknown'),
            'args':           record.get('args', {}),
            'result':         record.get('result', {}),
            'tier':           record.get('tier'),
            'user_approved':  record.get('user_approved'),
            'was_first_time': record.get('was_first_time', False),
            'first_time_key': record.get('first_time_key'),
            'reversible':     record.get('reversible'),
            'source':         record.get('source', 'unknown'),
            'was_dry_run':    record.get('was_dry_run', False),
            'preview_path':   record.get('preview_path'),
        }
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(full, default=str, ensure_ascii=False))
            f.write('\n')
    except Exception as e:
        # Audit failure must not crash callers
        print(f'[audit] append failed: {e}', file=sys.stderr)


def tail(n: int = 50) -> list[dict[str, Any]]:
    """Return the last n audit records. Tolerates malformed lines."""
    if not _AUDIT_PATH.exists():
        return []
    try:
        lines = _AUDIT_PATH.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def path() -> Path:
    return _AUDIT_PATH
