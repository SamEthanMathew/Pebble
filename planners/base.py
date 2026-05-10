"""Planner base class + state-doc envelope helpers."""

from __future__ import annotations

import datetime
import hashlib
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any

import audit
import metrics
from atomic_io import write_json, read_json
from events import bus, PLANNER_COMPLETED

_STATE_DIR = Path.home() / '.pebble' / 'state'

ENVELOPE_SCHEMA_VERSION = 1


# ── Proposal (planner → autonomy) per contracts.md §9 ─────────────────────────

@dataclass
class Proposal:
    module:        str
    action:        str
    args:          dict[str, Any]
    source:        str                 # planner name
    urgency:       str = 'normal'      # 'critical' | 'high' | 'normal' | 'low'
    reversible:    bool = True
    target_id:     str | None = None
    rationale:     str = ''

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def state_doc_path(name: str) -> Path:
    """name = bare filename without dir, e.g. 'schedule_today.json'."""
    return _STATE_DIR / name


def input_hash(inputs: dict[str, Any]) -> str:
    """Stable sha256 hex of canonicalized JSON."""
    canon = json.dumps(inputs, sort_keys=True, default=str, ensure_ascii=False).encode('utf-8')
    return 'sha256:' + hashlib.sha256(canon).hexdigest()[:32]


def read_state_doc(name: str) -> dict[str, Any] | None:
    """Return the full envelope (with schema_version, generated_at, etc.) or None."""
    return read_json(state_doc_path(name), default=None)


def is_fresh(envelope: dict[str, Any]) -> bool:
    """True if generated_at + ttl_seconds is still in the future."""
    if not envelope:
        return False
    try:
        gen = datetime.datetime.fromisoformat(envelope['generated_at'].replace('Z', '+00:00'))
        ttl = int(envelope.get('ttl_seconds', 0))
        return (datetime.datetime.now(datetime.timezone.utc) - gen).total_seconds() < ttl
    except Exception:
        return False


def write_state_doc(*, name: str, generated_by: str, ttl_seconds: int,
                    input_hash_str: str, payload: dict[str, Any]) -> Path:
    """Write a state doc with envelope metadata via atomic_io."""
    envelope = {
        'schema_version': ENVELOPE_SCHEMA_VERSION,
        'generated_at':   _now_iso(),
        'generated_by':   generated_by,
        'ttl_seconds':    ttl_seconds,
        'input_hash':     input_hash_str,
        'payload':        payload,
    }
    p = state_doc_path(name)
    write_json(p, envelope)
    return p


# ── BasePlanner ABC ────────────────────────────────────────────────────────────

class BasePlanner(ABC):
    """Subclass and implement collect_inputs + render_prompt + parse_output.

    Override class attrs:
        name         — short identifier ('schedule', 'comms', 'school')
        state_doc    — filename written, e.g. 'schedule_today.json'
        ttl_seconds  — freshness window for the state doc
    """

    name:        str = ''
    state_doc:   str = ''
    ttl_seconds: int = 1800

    # ── abstract surface ─────────────────────────────────────────────────────

    @abstractmethod
    def collect_inputs(self) -> dict[str, Any]:
        """Read everything we need to make a decision. Determines the re-run gate hash."""

    @abstractmethod
    def render_prompt(self, inputs: dict[str, Any]) -> tuple[str, str]:
        """Return (system_prompt_text, user_message_text). Either may be empty string."""

    @abstractmethod
    def parse_output(self, llm_text: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Convert LLM response into the state-doc payload."""

    # ── helpers subclasses can override ──────────────────────────────────────

    def planner_backend(self):
        """Return a ModelBackend for the configured planner_model.

        Returns None if no planner_model is configured — the planner is then
        DISABLED (not silently downgraded to a local model). Per contracts.md.
        """
        try:
            import crab_config
            from model_backend import ModelBackend
            cfg = crab_config.get('model', {}) or {}
            planner_model = cfg.get('planner_model') or cfg.get('primary')
            if not planner_model:
                return None
            return ModelBackend.for_id(planner_model)
        except Exception as e:
            audit.append({
                'module': f'planner.{self.name}',
                'action': 'backend_lookup_failed',
                'result': {'error': str(e)},
                'tier':   'auto',
                'source': f'planner.{self.name}',
            })
            return None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def run(self, *, force: bool = False) -> dict[str, Any] | None:
        """Gate → LLM → parse → write → publish.

        Returns the new payload on success, or None if skipped (re-run gate, no
        backend configured, or parse failure).
        """
        if not self.name or not self.state_doc:
            raise ValueError(f'{type(self).__name__} must set class attrs `name` and `state_doc`')

        metrics.emit('planner.started', {'planner': self.name})

        # Collect inputs and compute hash
        try:
            inputs = self.collect_inputs()
        except Exception as e:
            audit.append({
                'module': f'planner.{self.name}', 'action': 'collect_inputs_failed',
                'result': {'error': str(e)}, 'tier': 'auto', 'source': f'planner.{self.name}',
            })
            metrics.emit('planner.skipped', {'planner': self.name, 'gate_reason': 'collect_failed'})
            return None

        new_hash = input_hash(inputs)

        # Re-run gate
        if not force:
            existing = read_state_doc(self.state_doc)
            if existing and existing.get('input_hash') == new_hash:
                metrics.emit('planner.skipped', {'planner': self.name, 'gate_reason': 'input_unchanged'})
                return None

        # Cloud-only — no backend means disabled, not degraded
        backend = self.planner_backend()
        if backend is None:
            print(f'[planner.{self.name}] disabled — no planner_model configured', file=sys.stderr)
            metrics.emit('planner.skipped', {'planner': self.name, 'gate_reason': 'no_planner_model'})
            return None

        # Call LLM
        system_prompt, user_msg = self.render_prompt(inputs)
        try:
            text = backend.chat(
                [{'role': 'user', 'content': user_msg or 'Generate the state doc.'}],
                system=system_prompt,
            )
        except Exception as e:
            audit.append({
                'module': f'planner.{self.name}', 'action': 'llm_call_failed',
                'result': {'error': str(e)}, 'tier': 'auto', 'source': f'planner.{self.name}',
            })
            metrics.emit('planner.skipped', {'planner': self.name, 'gate_reason': 'llm_failed'})
            return None

        # Parse
        try:
            payload = self.parse_output(text, inputs)
        except Exception as e:
            audit.append({
                'module': f'planner.{self.name}', 'action': 'parse_failed',
                'result': {'error': str(e), 'raw_preview': text[:300]},
                'tier':   'auto', 'source': f'planner.{self.name}',
            })
            metrics.emit('planner.skipped', {'planner': self.name, 'gate_reason': 'parse_failed'})
            return None

        # Write atomically
        path = write_state_doc(
            name=self.state_doc, generated_by=self.name,
            ttl_seconds=self.ttl_seconds, input_hash_str=new_hash, payload=payload,
        )
        audit.append({
            'module': f'planner.{self.name}', 'action': 'state_doc_written',
            'args':   {'state_doc': self.state_doc},
            'result': {'path': str(path)},
            'tier':   'auto', 'source': f'planner.{self.name}',
        })
        metrics.emit('planner.finished', {'planner': self.name})

        # Notify subscribers
        bus.publish(PLANNER_COMPLETED, {
            'planner':     self.name,
            'state_doc':   self.state_doc,
            'was_skipped': False,
        })

        return payload
