"""Entity store — SQLite-backed resolution of strings ("15-122", "Pranav") to structured records.

Schema per docs/contracts.md (v0.1 will codify entity types). For now:
    Course / Person / Project / RecurringEvent / Account.

Used by:
- Planners (lookup) — resolve calendar entries, email senders, etc.
- Chat (/add-course, /add-person, /entities, /resolve)
- Autonomy (target_id resolution may consult the store)
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import audit

_DB_PATH = Path.home() / '.pebble' / 'entities.db'

ENTITY_TYPES = ('course', 'person', 'project', 'recurring', 'account')


@dataclass
class Entity:
    id:         str
    type:       str
    name:       str
    aliases:    list[str]
    payload:    dict[str, Any]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS entities (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            name        TEXT NOT NULL,
            name_lower  TEXT NOT NULL,
            aliases     TEXT NOT NULL DEFAULT '[]',
            payload     TEXT NOT NULL DEFAULT '{}',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
        CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name_lower);
    ''')
    conn.commit()


def _row_to_entity(row: sqlite3.Row | tuple) -> Entity:
    return Entity(
        id         = row[0],
        type       = row[1],
        name       = row[2],
        aliases    = json.loads(row[4]),
        payload    = json.loads(row[5]),
        created_at = row[6],
        updated_at = row[7],
    )


def init() -> None:
    """Idempotent. Safe to call on every Pebble startup."""
    with _connect() as conn:
        _migrate(conn)


def add(type: str, name: str, aliases: list[str] | None = None,
        payload: dict | None = None) -> Entity:
    """Insert a new entity. Returns the created Entity."""
    if type not in ENTITY_TYPES:
        raise ValueError(f'Unknown entity type: {type!r}. Valid: {ENTITY_TYPES}')
    if not name or not name.strip():
        raise ValueError('name is required')

    eid = str(uuid.uuid4())
    now = _now_iso()
    aliases = aliases or []
    payload = payload or {}

    with _connect() as conn:
        _migrate(conn)
        conn.execute(
            'INSERT INTO entities (id, type, name, name_lower, aliases, payload, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (eid, type, name, name.lower(), json.dumps(aliases), json.dumps(payload), now, now)
        )
        conn.commit()

    audit.append({
        'module':  'entity_store',
        'action':  'add',
        'args':    {'type': type, 'name': name, 'aliases': aliases},
        'result':  {'id': eid},
        'tier':    'notify',
        'source':  'user',
        'reversible': True,
    })

    return Entity(id=eid, type=type, name=name, aliases=aliases,
                  payload=payload, created_at=now, updated_at=now)


def lookup(query: str, type: str | None = None) -> Entity | None:
    """Resolve a string to an Entity, or None.

    Order:
        1. exact name (case-insensitive)
        2. exact alias (case-insensitive)
        3. substring on name
        4. substring on alias
    """
    if not query or not query.strip():
        return None
    q = query.strip().lower()

    with _connect() as conn:
        _migrate(conn)
        type_clause = 'AND type = ?' if type else ''
        params: tuple[Any, ...] = (type,) if type else ()

        # 1. exact name
        row = conn.execute(
            f'SELECT * FROM entities WHERE name_lower = ? {type_clause} LIMIT 1',
            (q, *params)
        ).fetchone()
        if row:
            return _row_to_entity(row)

        # 2. exact alias — scan and JSON-match
        rows = conn.execute(
            f'SELECT * FROM entities WHERE 1=1 {type_clause}',
            params
        ).fetchall()
        for r in rows:
            try:
                aliases = json.loads(r[4])
                if any(a.lower() == q for a in aliases):
                    return _row_to_entity(r)
            except (json.JSONDecodeError, AttributeError):
                continue

        # 3. substring on name
        row = conn.execute(
            f'SELECT * FROM entities WHERE name_lower LIKE ? {type_clause} LIMIT 1',
            (f'%{q}%', *params)
        ).fetchone()
        if row:
            return _row_to_entity(row)

        # 4. substring on alias (slower path)
        for r in rows:
            try:
                aliases = json.loads(r[4])
                if any(q in a.lower() for a in aliases):
                    return _row_to_entity(r)
            except (json.JSONDecodeError, AttributeError):
                continue

    return None


def list_entities(type: str | None = None) -> list[Entity]:
    with _connect() as conn:
        _migrate(conn)
        if type:
            rows = conn.execute(
                'SELECT * FROM entities WHERE type = ? ORDER BY name', (type,)
            ).fetchall()
        else:
            rows = conn.execute('SELECT * FROM entities ORDER BY type, name').fetchall()
    return [_row_to_entity(r) for r in rows]


def update(id: str, fields: dict[str, Any]) -> Entity | None:
    """Update name/aliases/payload by id. Returns updated entity or None if not found."""
    allowed = {'name', 'aliases', 'payload'}
    bad = set(fields) - allowed
    if bad:
        raise ValueError(f'Cannot update fields: {bad}. Allowed: {allowed}')

    now = _now_iso()
    sets = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k == 'name':
            sets.append('name = ?'); vals.append(v)
            sets.append('name_lower = ?'); vals.append(v.lower())
        elif k == 'aliases':
            sets.append('aliases = ?'); vals.append(json.dumps(v))
        elif k == 'payload':
            sets.append('payload = ?'); vals.append(json.dumps(v))
    sets.append('updated_at = ?'); vals.append(now)
    vals.append(id)

    with _connect() as conn:
        _migrate(conn)
        cur = conn.execute(f'UPDATE entities SET {", ".join(sets)} WHERE id = ?', vals)
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute('SELECT * FROM entities WHERE id = ?', (id,)).fetchone()

    audit.append({
        'module': 'entity_store', 'action': 'update',
        'args': {'id': id, 'fields': list(fields)}, 'result': {'updated': True},
        'tier': 'notify', 'source': 'user', 'reversible': True,
    })
    return _row_to_entity(row) if row else None


def delete(id: str) -> bool:
    with _connect() as conn:
        _migrate(conn)
        cur = conn.execute('DELETE FROM entities WHERE id = ?', (id,))
        conn.commit()
        deleted = cur.rowcount > 0

    if deleted:
        audit.append({
            'module': 'entity_store', 'action': 'delete',
            'args': {'id': id}, 'result': {'deleted': True},
            'tier': 'notify', 'source': 'user', 'reversible': False,
        })
    return deleted


def db_path() -> Path:
    return _DB_PATH


# ── Bootstrap scaffolding (full implementation in pebble-integrations Phase 1.5) ──

def bootstrap_from_gmail(scan_days: int = 30, top_n: int = 20) -> list[dict]:
    """First-run scan of recent Gmail to propose top senders for classification.

    Phase 1: stub. Returns empty list with a note in audit. Actual implementation
    lands when pebble-integrations wires it up via GoogleServices.

    Output (when implemented): list of proposals
        [{"email": "...", "name": "...", "thread_count": N, "first_seen": "...", "last_seen": "..."}]
    """
    audit.append({
        'module': 'entity_store', 'action': 'bootstrap_from_gmail',
        'args': {'scan_days': scan_days, 'top_n': top_n},
        'result': {'status': 'stub — awaiting integrations wiring'},
        'tier': 'auto', 'source': 'bootstrap',
    })
    return []
