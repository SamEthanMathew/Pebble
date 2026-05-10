"""Atomic JSON file writes.

All Pebble state docs, ledgers, and config writes go through write_json()
so concurrent readers never see a torn file.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# Windows: os.replace can transiently fail with PermissionError if another
# process/thread is holding the destination open. Retry a few times.
_REPLACE_RETRIES = 5
_REPLACE_BACKOFF = 0.02  # seconds, doubled each retry


def write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    """Write JSON atomically.

    Serialize to <path>.tmp, fsync, then os.replace() onto <path>.
    On Windows os.replace is atomic; on POSIX it's atomic within a filesystem.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # NamedTemporaryFile in the same dir so os.replace stays atomic
    fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + '.',
        suffix='.tmp',
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=indent, default=str, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass

        # Retry os.replace on Windows transient PermissionError
        delay = _REPLACE_BACKOFF
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp_path, path)
                break
            except PermissionError:
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_json(path: Path | str, default: Any = None) -> Any:
    """Read JSON, returning default on missing/invalid file."""
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return default
