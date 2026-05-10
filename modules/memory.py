"""Persistent memory module — Pebble remembers facts across conversations.

v1.5 (Phase 3): word-overlap recall with recency weighting + last_accessed
tracking. Migration: existing entries gain `last_accessed` on first load.
v2 (later): sqlite-vec semantic memory once embedding infra exists.
"""

from __future__ import annotations

import datetime
import math
import re
from pathlib import Path
from typing import Any

from atomic_io import write_json, read_json
from .base import PebbleModule, ActionTier

_MEMORY_PATH = Path.home() / '.pebble' / 'memory.json'

# Recall scoring constants (tunable)
RECENCY_HALF_LIFE_DAYS = 30   # entries half as relevant after 30 days at equal overlap
DEFAULT_TOP_N = 8


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _tokens(text: str) -> set[str]:
    """Lowercase, alphanumeric tokens of length ≥ 2."""
    return {t for t in re.findall(r'[a-z0-9]+', (text or '').lower()) if len(t) >= 2}


def _days_ago(iso_date: str) -> float:
    """Return days between today and the given date string. Inf on parse failure."""
    try:
        d = datetime.date.fromisoformat(iso_date[:10])
        return max(0.0, (datetime.date.today() - d).days)
    except Exception:
        return 9999.0


def _migrate_entry(e: dict[str, Any]) -> dict[str, Any]:
    """Add last_accessed if missing (back-compat)."""
    if 'last_accessed' not in e:
        e['last_accessed'] = e.get('created', _today_iso())
    return e


def _load() -> list[dict[str, Any]]:
    data = read_json(_MEMORY_PATH, default=[]) or []
    return [_migrate_entry(e) for e in data]


def _save(items: list[dict[str, Any]]) -> None:
    write_json(_MEMORY_PATH, items)


def _score(query_tokens: set[str], entry: dict[str, Any]) -> float:
    """Word overlap × recency factor.

    overlap = |query ∩ entry_tokens| / max(1, |query|)
    recency = 0.5 ** (days_since_access / HALF_LIFE)
    score   = overlap * recency
    """
    if not query_tokens:
        return 0.0
    entry_tokens = _tokens(f'{entry.get("text", "")} {entry.get("category", "")}')
    if not entry_tokens:
        return 0.0
    overlap = len(query_tokens & entry_tokens) / max(1, len(query_tokens))
    if overlap == 0:
        return 0.0
    days = _days_ago(entry.get('last_accessed') or entry.get('created') or _today_iso())
    recency = 0.5 ** (days / RECENCY_HALF_LIFE_DAYS)
    return overlap * recency


def search(query: str, *, top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Public scoring API used by chat / planners."""
    qt = _tokens(query)
    if not qt:
        return []
    items = _load()
    scored = sorted(
        ((_score(qt, e), e) for e in items),
        key=lambda x: x[0], reverse=True,
    )
    out = []
    for s, e in scored:
        if s <= 0:
            break
        out.append(dict(e, _score=round(s, 4)))
        if len(out) >= top_n:
            break
    # Touch last_accessed on returned entries (recency reinforcement)
    if out:
        ids = {e['id'] for e in out}
        now = _today_iso()
        for it in items:
            if it.get('id') in ids:
                it['last_accessed'] = now
        _save(items)
    return out


# ── PebbleModule wrapper ──────────────────────────────────────────────────────


class MemoryModule(PebbleModule):
    name         = 'memory'
    display_name = 'Pebble Memory'
    description  = 'Remember and recall facts, preferences, and context across conversations'
    icon         = '🧠'
    config_fields: list[dict] = []

    _default_tiers = {
        'remember': ActionTier.NOTIFY,
        'recall':   ActionTier.AUTO,
        'list':     ActionTier.AUTO,
        'forget':   ActionTier.NOTIFY,
    }

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'memory'

    def tool_description(self) -> str:
        return ('Store and retrieve persistent facts about the user, preferences, '
                'context, and anything Pebble should remember across sessions. '
                'Use action="remember" to save, "recall" to search (semantic word '
                'overlap + recency), "list" to show all, "forget" to delete by query.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['remember', 'recall', 'list', 'forget'],
                    'description': 'remember=save, recall=search, list=show all, forget=delete by keyword',
                },
                'text':     {'type': 'string', 'description': 'For remember: the fact. For recall/forget: the query.'},
                'category': {'type': 'string', 'description': 'preference | fact | goal | person | place | other'},
                'top_n':    {'type': 'integer', 'description': 'recall: max results (default 8)'},
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'list', text: str = '', category: str = 'fact',
                top_n: int = DEFAULT_TOP_N, **_) -> str:
        try:
            if action == 'remember':
                return self._remember(text, category)
            if action == 'recall':
                return self._recall(text, top_n=top_n)
            if action == 'list':
                return self._list()
            if action == 'forget':
                return self._forget(text)
            return f'Unknown action: {action}'
        except Exception as e:
            return f'Memory error: {e}'

    def _remember(self, text: str, category: str) -> str:
        if not text:
            return 'Nothing to remember.'
        items = _load()
        next_id = (max((int(i.get('id', 0)) for i in items), default=0) + 1)
        entry = {
            'id':            next_id,
            'text':          text,
            'category':      category or 'fact',
            'created':       _today_iso(),
            'last_accessed': _today_iso(),
        }
        items.append(entry)
        _save(items)
        return f"Got it, I'll remember: {text}"

    def _recall(self, query: str, *, top_n: int) -> str:
        if not query:
            return self._list()
        matches = search(query, top_n=top_n)
        if not matches:
            return f'Nothing in memory matching "{query}".'
        lines = [f'Top {len(matches)} relevant memor{"y" if len(matches) == 1 else "ies"} for "{query}":']
        for m in matches:
            lines.append(f'  [{m.get("category", "?")}] {m.get("text", "")} '
                         f'(score {m.get("_score", 0):.2f}, saved {m.get("created", "")})')
        return '\n'.join(lines)

    def _list(self) -> str:
        items = _load()
        if not items:
            return "I don't have anything saved in memory yet."
        # Show most-recently-accessed first
        items.sort(key=lambda i: i.get('last_accessed', i.get('created', '')), reverse=True)
        lines = [f'Memory ({len(items)} entries, most recent first):']
        for m in items[:20]:
            lines.append(f'  [{m.get("category", "?")}] {m.get("text", "")}')
        if len(items) > 20:
            lines.append(f'  ... and {len(items) - 20} more.')
        return '\n'.join(lines)

    def _forget(self, text: str) -> str:
        if not text:
            return 'Specify what to forget.'
        items = _load()
        q = text.lower()
        before = len(items)
        items = [i for i in items if q not in i.get('text', '').lower()]
        if len(items) == before:
            return f'No memory entries matching "{text}".'
        _save(items)
        removed = before - len(items)
        return f'Removed {removed} memory {"entry" if removed == 1 else "entries"} matching "{text}".'
