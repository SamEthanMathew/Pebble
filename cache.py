"""Per-source TTL cache for external content (scrapes, API reads).

Storage: ~/.pebble/cache/<source>/<sha256(key)>.json with an envelope
{ source, key, fetched_at, ttl_hours, data }.

TTLs come from config.scraping.cache_ttl_hours (per-source). Default 24h.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

import paths
from atomic_io import write_json

_CACHE_DIR = paths.data_dir() / 'cache'
DEFAULT_TTL_HOURS = 24
DEFAULT_SOURCE_TTLS = {
    'syllabus': 168,   # 7 days
    'news':     24,
    'canvas':   2,
    'weather':  0.5,
    'generic':  24,
}


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _ttl_hours_for(source: str) -> float:
    try:
        import crab_config
        cfg = crab_config.get('scraping', {}) or {}
        per_source = cfg.get('cache_ttl_hours', {}) or {}
        return float(per_source.get(source, DEFAULT_SOURCE_TTLS.get(source, DEFAULT_TTL_HOURS)))
    except Exception:
        return DEFAULT_SOURCE_TTLS.get(source, DEFAULT_TTL_HOURS)


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]


def _path_for(source: str, key: str) -> Path:
    return _CACHE_DIR / source / f'{_hash_key(key)}.json'


def get(source: str, key: str) -> Any | None:
    """Return cached data if present and not expired, else None."""
    p = _path_for(source, key)
    if not p.exists():
        return None
    try:
        env = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None
    try:
        fetched = datetime.datetime.fromisoformat(env['fetched_at'].replace('Z', '+00:00'))
        ttl_h   = float(env.get('ttl_hours', DEFAULT_TTL_HOURS))
        if (_now() - fetched).total_seconds() > ttl_h * 3600:
            return None
        return env.get('data')
    except Exception:
        return None


def set(source: str, key: str, data: Any, *, ttl_hours: float | None = None) -> Path:
    """Write data to cache. Returns the cache file path."""
    p = _path_for(source, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    if ttl_hours is None:
        ttl_hours = _ttl_hours_for(source)
    env = {
        'source':     source,
        'key':        key,
        'fetched_at': _now().isoformat(timespec='seconds').replace('+00:00', 'Z'),
        'ttl_hours':  ttl_hours,
        'data':       data,
    }
    write_json(p, env)
    return p


def invalidate(source: str, key: str) -> bool:
    p = _path_for(source, key)
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False


def clear_source(source: str) -> int:
    """Delete all cache entries for a source. Returns count removed."""
    sd = _CACHE_DIR / source
    if not sd.exists():
        return 0
    n = 0
    for p in sd.glob('*.json'):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n
