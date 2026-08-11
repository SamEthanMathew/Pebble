"""Proposal queue — holds proposed edits to user-authored content.

Per spec §2.5: Pebble never silently rewrites user-authored notes. Aliases,
canonical-name fixes, link suggestions, preference additions — all go through
this queue. The user accepts/dismisses each via the chat UI (Phase F).

Persistence: append-only JSONL at `~/.pebble/workspace/proposals.jsonl`.
Each line is a Proposal dict; `accept` and `dismiss` are state changes
recorded by appending a status-update row keyed by `proposal_id`.
"""

from __future__ import annotations

import datetime
import json
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paths


Status = str  # 'pending' | 'accepted' | 'dismissed' | 'postponed'


@dataclass
class Proposal:
    id:         str
    kind:       str                 # 'edit' | 'alias' | 'preference' | …
    note_id:    str                 # which vault note this proposal touches
    payload:    dict[str, Any]      # kind-specific data
    created_at: str
    status:     Status = 'pending'
    decided_at: str | None = None
    note:       str = ''             # free-text rationale

    def to_dict(self) -> dict[str, Any]:
        return {
            'id':         self.id,
            'kind':       self.kind,
            'note_id':    self.note_id,
            'payload':    self.payload,
            'created_at': self.created_at,
            'status':     self.status,
            'decided_at': self.decided_at,
            'note':       self.note,
        }


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


class ProposalQueue:
    """Append-only JSONL queue with in-memory replay for the current view.

    Concurrency model: each instance can be safely shared across threads; the
    file itself is the ordered log (the queue reconstructs state by replay).
    """

    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = paths.workspace_dir() / 'proposals.jsonl'
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── core ─────────────────────────────────────────────────────────────────

    def add(self, payload: dict[str, Any]) -> str:
        """Add a new proposal. Returns its ID.

        `payload` is treated as kind-flexible; expected keys:
            kind:    str          (default 'generic')
            note_id: str          (default '')
            note:    str          (optional rationale)
            plus whatever else the caller wants stashed in payload.
        """
        pid = uuid.uuid4().hex[:12]
        row = {
            'type':       'proposal',
            'id':         pid,
            'kind':       payload.get('kind', 'generic'),
            'note_id':    payload.get('note_id', ''),
            'payload':    {k: v for k, v in payload.items()
                            if k not in ('kind', 'note_id', 'note')},
            'created_at': _now_iso(),
            'status':     'pending',
            'note':       payload.get('note', ''),
        }
        self._append(row)
        return pid

    def _append_unlocked(self, row: dict[str, Any]) -> None:
        with self._path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, default=str, ensure_ascii=False))
            f.write('\n')

    def _append(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._append_unlocked(row)

    def _replay_unlocked(self) -> dict[str, Proposal]:
        """Reconstruct current state by replaying the log. Caller holds _lock."""
        out: dict[str, Proposal] = {}
        if not self._path.exists():
            return out
        try:
            for line in self._path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = row.get('type', 'proposal')
                if rtype == 'proposal':
                    pid = row.get('id') or ''
                    if not pid:
                        continue
                    out[pid] = Proposal(
                        id         = pid,
                        kind       = row.get('kind', 'generic'),
                        note_id    = row.get('note_id', ''),
                        payload    = row.get('payload', {}) or {},
                        created_at = row.get('created_at', ''),
                        status     = row.get('status', 'pending'),
                        decided_at = row.get('decided_at'),
                        note       = row.get('note', ''),
                    )
                elif rtype == 'status':
                    pid = row.get('id') or ''
                    if pid in out:
                        out[pid].status     = row.get('status', out[pid].status)
                        out[pid].decided_at = row.get('decided_at')
        except OSError:
            pass
        return out

    # ── public queries ───────────────────────────────────────────────────────

    def list_pending(self) -> list[Proposal]:
        with self._lock:
            return [p for p in self._replay_unlocked().values() if p.status == 'pending']

    def list_all(self) -> list[Proposal]:
        with self._lock:
            return list(self._replay_unlocked().values())

    def get(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            return self._replay_unlocked().get(proposal_id)

    # ── state changes ────────────────────────────────────────────────────────

    def accept(self, proposal_id: str) -> bool:
        return self._set_status(proposal_id, 'accepted')

    def dismiss(self, proposal_id: str) -> bool:
        return self._set_status(proposal_id, 'dismissed')

    def postpone(self, proposal_id: str) -> bool:
        return self._set_status(proposal_id, 'postponed')

    def _set_status(self, proposal_id: str, status: Status) -> bool:
        # Hold the lock across the whole read-modify-write so two concurrent
        # accept/dismiss calls on the same id can't both succeed.
        with self._lock:
            current = self._replay_unlocked().get(proposal_id)
            if current is None:
                return False
            if current.status == status:
                return False  # idempotent — already in target state
            self._append_unlocked({
                'type':       'status',
                'id':         proposal_id,
                'status':     status,
                'decided_at': _now_iso(),
            })
            return True

    def path(self) -> Path:
        return self._path
