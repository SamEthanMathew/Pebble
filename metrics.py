"""Metrics — JSONL emission for behavior signals.

Schema per docs/contracts.md §4. Phase 1 ships the writer only; read side
(audit_reader, /how-am-i-doing) waits until Phase 4 so data accumulates first.
Emission failure must never crash callers.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any

import paths

_METRICS_PATH = paths.data_dir() / 'metrics.jsonl'


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def emit(event: str, props: dict[str, Any] | None = None) -> None:
    """Append one metrics event. Schema:
    {"timestamp": ISO8601Z, "event": str, "props": dict}
    """
    try:
        record = {
            'timestamp': _now_iso(),
            'event':     event,
            'props':     props or {},
        }
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _METRICS_PATH.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False))
            f.write('\n')
    except Exception as e:
        print(f'[metrics] emit failed: {e}', file=sys.stderr)


def path() -> Path:
    return _METRICS_PATH
