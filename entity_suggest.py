"""Entity auto-suggestion — proposes adding people the user keeps interacting with.

Phase 4 deliverable. Given a stream of recent emails (or any sender stream),
detects senders that appear N+ times AND aren't already in the entity store,
returns suggestions for the user to confirm. Surfacing happens via dispatcher
or chat command.

Sender events are accumulated in ~/.pebble/entity_unknown_seen.json so we
remember across runs without needing to re-scan the inbox each time.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Iterable

import audit
import entity_store
from atomic_io import read_json, write_json

_LEDGER_PATH = Path.home() / '.pebble' / 'entity_unknown_seen.json'
DEFAULT_THRESHOLD = 3


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _load_ledger() -> dict[str, dict[str, Any]]:
    return read_json(_LEDGER_PATH, default={}) or {}


def _save_ledger(data: dict[str, dict[str, Any]]) -> None:
    write_json(_LEDGER_PATH, data)


def _is_known(email: str, name: str = '') -> bool:
    """True if email or name resolves to a Person entity."""
    try:
        entity_store.init()
    except Exception:
        pass
    if email:
        if entity_store.lookup(email, type='person'):
            return True
    if name:
        if entity_store.lookup(name, type='person'):
            return True
    return False


def observe(messages: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Update the ledger with senders from a batch of recent messages.

    Each message must have at least `from_email`. `from_name` optional.
    Returns the updated ledger snapshot.
    """
    ledger = _load_ledger()
    now = _now_iso()
    for m in messages:
        email = (m.get('from_email') or '').strip().lower()
        if not email or '@' not in email:
            continue
        name = (m.get('from_name') or '').strip()
        if _is_known(email, name):
            # Drop from ledger if user added them since last scan
            ledger.pop(email, None)
            continue
        entry = ledger.get(email)
        if entry is None:
            ledger[email] = {
                'email':      email,
                'name':       name,
                'count':      1,
                'first_seen': now,
                'last_seen':  now,
            }
        else:
            entry['count'] = int(entry.get('count', 0)) + 1
            entry['last_seen'] = now
            if name and not entry.get('name'):
                entry['name'] = name
    _save_ledger(ledger)
    return ledger


def find_suggestions(threshold: int = DEFAULT_THRESHOLD) -> list[dict[str, Any]]:
    """Return ledger entries with count >= threshold."""
    return sorted(
        (e for e in _load_ledger().values() if int(e.get('count', 0)) >= threshold),
        key=lambda e: e['count'], reverse=True,
    )


def accept(email: str, *, kind: str = 'person', payload: dict[str, Any] | None = None) -> bool:
    """User confirms a suggestion → add to entity store and clear from ledger."""
    ledger = _load_ledger()
    entry = ledger.get(email)
    if entry is None:
        return False
    name = entry.get('name') or email
    try:
        entity_store.add(type=kind, name=name, aliases=[email],
                         payload=payload or {'email': email})
    except Exception:
        return False
    ledger.pop(email, None)
    _save_ledger(ledger)
    audit.append({
        'module':  'entity_suggest', 'action': 'accepted',
        'args':    {'email': email, 'name': name, 'kind': kind},
        'result':  {'ok': True}, 'tier': 'notify', 'source': 'user',
    })
    return True


def dismiss(email: str) -> bool:
    """User says 'don't suggest again' → remove from ledger (will resurface only after threshold reset)."""
    ledger = _load_ledger()
    if email not in ledger:
        return False
    ledger.pop(email)
    _save_ledger(ledger)
    audit.append({
        'module':  'entity_suggest', 'action': 'dismissed',
        'args':    {'email': email},
        'result':  {'ok': True}, 'tier': 'auto', 'source': 'user',
    })
    return True


def ledger_path() -> Path:
    return _LEDGER_PATH
