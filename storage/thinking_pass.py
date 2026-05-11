"""Thinking passes — scheduled and reactive reasoning over the vault.

Per spec §7: each pass loads context with a specific provenance filter, calls
the planner_model with a reasoning template, and produces output. Pebble
NEVER reasons over its own writeback (user_only / mixed_ok filters keep the
thinking passes honest).

The seven passes:

| name      | provenance  | output destination                                |
|-----------|-------------|---------------------------------------------------|
| close     | all         | [!reflection]+ callout in today's Daily note      |
| connect   | user_only   | _pebble_imports/digests/<date>-connect.md         |
| challenge | user_only   | [!challenge]+ callout in the target decision note |
| emerge    | user_only   | _pebble_imports/digests/<date>-emerge.md          |
| drift     | mixed_ok    | _pebble_imports/digests/<date>-drift.md           |
| ideas     | user_only   | _pebble_imports/digests/<date>-ideas.md           |
| ghost     | user_only   | (returned directly to chat — no vault write)      |

Notes get `source: pebble` provenance. The user can promote, edit, or delete
freely.
"""

from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import audit
import metrics
import prompts as prompt_lib

from .context_loader import load_context, render_bundle_markdown
from .entity_resolver import EntityResolver
from .note import Note
from .vault import Provenance, Vault


# ── Pass specs ────────────────────────────────────────────────────────────────

OutputKind = Literal['chat', 'digest_note', 'callout_in_note', 'daily_note_block']


@dataclass(frozen=True)
class PassSpec:
    name:          str
    prompt_name:   str
    provenance:    Provenance
    output_kind:   OutputKind
    digest_label:  str = ''  # filename suffix for digest_note ('emerge'/'drift'/etc.)
    callout_label: str = 'pebble'  # for callout_in_note kind


PASS_REGISTRY: dict[str, PassSpec] = {
    'close':     PassSpec('close',     'close',     'all',       'daily_note_block',
                          callout_label='reflection'),
    'connect':   PassSpec('connect',   'connect',   'user_only', 'digest_note',
                          digest_label='connect'),
    'challenge': PassSpec('challenge', 'challenge', 'user_only', 'callout_in_note',
                          callout_label='challenge'),
    'emerge':    PassSpec('emerge',    'emerge',    'user_only', 'digest_note',
                          digest_label='emerge'),
    'drift':     PassSpec('drift',     'drift',     'mixed_ok',  'digest_note',
                          digest_label='drift'),
    'ideas':     PassSpec('ideas',     'ideas',     'user_only', 'digest_note',
                          digest_label='ideas'),
    'ghost':     PassSpec('ghost',     'ghost',     'user_only', 'chat'),
}


# ── Result shape ──────────────────────────────────────────────────────────────

@dataclass
class PassResult:
    pass_name:        str
    success:          bool
    text:             str = ''
    output_note_id:   str | None = None
    output_path:      str | None = None
    error:            str | None = None
    metadata:         dict[str, Any] = field(default_factory=dict)


# ── Planner backend helper (matches what other planners do) ──────────────────

def _planner_backend():
    try:
        import crab_config
        from model_backend import ModelBackend
        cfg = crab_config.get('model', {}) or {}
        mid = cfg.get('planner_model') or cfg.get('primary')
        if not mid:
            return None
        return ModelBackend.for_id(mid)
    except Exception:
        return None


def _audit_today_text() -> str:
    """Build a short audit-of-today log used by the /close template."""
    try:
        import audit_reader
        cutoff = datetime.datetime.combine(
            datetime.date.today(), datetime.time.min,
            tzinfo=datetime.timezone.utc,
        )
        rows = audit_reader.audit_since(cutoff)
    except Exception:
        return '(audit log unavailable)'
    if not rows:
        return '(no audited actions today)'
    lines = []
    for r in rows[-25:]:
        ts = (r.get('timestamp') or '?')[11:16]
        suffix = ' [dry-run]' if r.get('was_dry_run') else ''
        lines.append(f'{ts}  {r.get("module", "?")}.{r.get("action", "?")} '
                     f'← {r.get("source", "?")}{suffix}')
    return '\n'.join(lines)


def _render_goals_for_drift(bundle) -> str:
    """Render goal notes into Markdown for the /drift template's `goals` slot."""
    if not bundle.goals:
        return '_(no goal notes found — add some to `09 - Goals/` or tag with `goal`)_'
    lines = []
    for n in bundle.goals[:10]:
        first = (n.body.strip().split('\n\n', 1) + [''])[0][:300]
        lines.append(f'### {n.title}\n{first}')
    return '\n\n'.join(lines)


def _vault() -> Vault | None:
    try:
        import crab_config
        path = (crab_config.get_module_config('obsidian') or {}).get('vault_path')
        if not path:
            return None
        return Vault(path, autostart_watcher=False)
    except Exception:
        return None


# ── Main entry point ──────────────────────────────────────────────────────────

def run_pass(
    pass_name: str,
    *,
    entity_hints: list[str] | None = None,
    extra_slots:  dict[str, str] | None = None,
    target_note_id: str | None = None,
    max_tokens:   int = 8000,
    include_daily_notes: int = 7,
) -> PassResult:
    """Run one thinking pass.

    `entity_hints`     — feeds the ContextBundle resolver
    `extra_slots`      — additional template slots beyond {context} (e.g. for
                          /challenge: {decision_text}; for /ghost: {question})
    `target_note_id`   — required for callout_in_note kind (which note gets
                          the [!challenge]+ block)
    """
    spec = PASS_REGISTRY.get(pass_name)
    if spec is None:
        return PassResult(pass_name, False, error=f'Unknown pass: {pass_name}')

    backend = _planner_backend()
    if backend is None:
        msg = ('thinking pass disabled — no planner_model configured. '
               'Set it via /connect, then retry.')
        metrics.emit('planner.skipped',
                     {'planner': f'thinking_pass.{pass_name}',
                      'gate_reason': 'no_planner_model'})
        return PassResult(pass_name, False, error=msg)

    vault = _vault()
    if vault is None:
        return PassResult(pass_name, False,
                          error='No vault configured; thinking passes need one.')

    metrics.emit('planner.started', {'planner': f'thinking_pass.{pass_name}'})

    try:
        # ── Build the ContextBundle ─────────────────────────────────────────
        bundle = load_context(
            trigger={
                'type':         f'thinking_pass.{pass_name}',
                'title':        pass_name,
                'entity_hints': entity_hints or [],
            },
            vault=vault,
            resolver=EntityResolver(vault),
            depth=1,
            max_tokens=max_tokens,
            include_daily_notes=include_daily_notes,
            provenance=spec.provenance,
        )
        context_md = render_bundle_markdown(bundle, max_chars=max_tokens * 3)

        # ── Render the reasoning template ────────────────────────────────────
        slots: dict[str, Any] = {'context': context_md}

        # Auto-fill commonly-requested slots if the pass declares them
        declared = set(prompt_lib.load(spec.prompt_name).slots)
        if 'audit_today' in declared and 'audit_today' not in (extra_slots or {}):
            slots['audit_today'] = _audit_today_text()
        if 'goals' in declared and 'goals' not in (extra_slots or {}):
            slots['goals'] = _render_goals_for_drift(bundle)

        if extra_slots:
            slots.update(extra_slots)
        try:
            system_prompt = prompt_lib.render(spec.prompt_name, slots)
        except KeyError as e:
            return PassResult(pass_name, False,
                              error=f'template missing slot: {e}')

        # ── Special case: /close just generates prompts, doesn't ask LLM
        # to write a final answer. It still uses the LLM; we just produce
        # questions for the user, not assertions.
        try:
            text = backend.chat(
                [{'role': 'user',
                  'content': f'Run the {pass_name.upper()} pass now. Follow the system prompt.'}],
                system=system_prompt,
                max_tokens=4096,
            )
        except Exception as e:
            audit.append({
                'module': f'thinking_pass.{pass_name}', 'action': 'llm_failed',
                'result': {'error': str(e)}, 'tier': 'auto',
                'source': f'thinking_pass.{pass_name}',
            })
            metrics.emit('planner.skipped',
                         {'planner': f'thinking_pass.{pass_name}',
                          'gate_reason': 'llm_failed'})
            return PassResult(pass_name, False, error=str(e))

        # ── Route the output ─────────────────────────────────────────────────
        result = _route_output(spec, text, vault=vault, target_note_id=target_note_id)
        audit.append({
            'module': f'thinking_pass.{pass_name}', 'action': 'completed',
            'args':   {'output_kind': spec.output_kind,
                       'output_note_id': result.output_note_id,
                       'provenance':  spec.provenance,
                       'tokens_in':   bundle.total_tokens,
                       'chars_out':   len(text)},
            'result': {'ok': True},
            'tier':   'auto', 'source': f'thinking_pass.{pass_name}',
        })
        metrics.emit('planner.finished',
                     {'planner': f'thinking_pass.{pass_name}'})
        result.metadata['tokens_in']     = bundle.total_tokens
        result.metadata['provenance']    = spec.provenance
        result.metadata['warnings']      = bundle.warnings
        return result
    finally:
        try: vault.stop()
        except Exception: pass


def _route_output(
    spec: PassSpec, text: str, *,
    vault: Vault, target_note_id: str | None,
) -> PassResult:
    """Place the LLM output where the pass spec wants it."""
    text = text.strip() or '_(empty result — LLM returned nothing)_'

    if spec.output_kind == 'chat':
        return PassResult(spec.name, True, text=text)

    if spec.output_kind == 'digest_note':
        today = datetime.date.today().isoformat()
        rel = f'_pebble_imports/digests/{today}-{spec.digest_label}.md'
        body = (f'# {spec.digest_label.capitalize()} pass — {today}\n\n'
                f'_Provenance filter: `{spec.provenance}`_\n\n'
                f'---\n\n{text}\n')
        note = vault.create_note(
            rel, body=body,
            frontmatter={'tags': ['digest', spec.name, 'pebble-context-always'],
                          'pass': spec.name},
            trigger=f'thinking_pass.{spec.name}',
            confidence=0.7,
            note=f'{spec.name} pass digest',
        )
        return PassResult(spec.name, True, text=text,
                          output_note_id=note.id, output_path=str(note.path))

    if spec.output_kind == 'callout_in_note':
        if not target_note_id:
            return PassResult(spec.name, False,
                              error=f'{spec.name} requires target_note_id')
        from .vault import NoteNotFound
        try:
            today = datetime.date.today().isoformat()
            note = vault.append_block(
                target_note_id, text,
                trigger=f'thinking_pass.{spec.name}', confidence=0.8,
                label=spec.callout_label, title=f'{spec.name} — {today}',
            )
            return PassResult(spec.name, True, text=text,
                              output_note_id=note.id, output_path=str(note.path))
        except NoteNotFound as e:
            return PassResult(spec.name, False, error=str(e))

    if spec.output_kind == 'daily_note_block':
        today = datetime.date.today().isoformat()
        # Ensure today's daily note exists; we'll append a [!reflection]+ block
        from .vault import NoteNotFound
        try:
            daily = vault.daily_note(today, create_if_missing=True)
            note  = vault.append_block(
                daily.id, text,
                trigger=f'thinking_pass.{spec.name}', confidence=0.9,
                label=spec.callout_label, title=f'Evening reflection prompts — {today}',
            )
            return PassResult(spec.name, True, text=text,
                              output_note_id=note.id, output_path=str(note.path))
        except NoteNotFound as e:
            return PassResult(spec.name, False, error=str(e))

    return PassResult(spec.name, False, error=f'Unknown output_kind: {spec.output_kind}')


# ── Helper: extract decision text from a note for /challenge ─────────────────

def is_decision_note(note: Note) -> bool:
    """True if this note looks like a decision (by tag OR folder convention)."""
    return ('decision' in note.tags
            or note.id.startswith('70_decisions/')
            or 'decision' in (note.frontmatter.get('type') or ''))


class ChallengeOnDecisionEdit:
    """Watchdog callback that fires `/challenge` when a decision note is edited.

    Includes a per-note debounce: won't re-fire for the same note within
    `cooldown_seconds` (default 5min) so rapid saves don't loop.
    """

    def __init__(self, *, cooldown_seconds: int = 300):
        self._cooldown   = cooldown_seconds
        self._last_fired: dict[str, float] = {}
        self._lock       = __import__('threading').Lock()

    def __call__(self, note: Note) -> None:
        if not is_decision_note(note):
            return
        # Don't challenge our own thinking-pass writes
        if note.source == 'pebble':
            return

        import time
        now = time.time()
        with self._lock:
            last = self._last_fired.get(note.id, 0.0)
            if now - last < self._cooldown:
                return
            self._last_fired[note.id] = now

        # Fire the pass on a background thread (we're already in a watchdog
        # thread but the pass takes ~10s — don't block more watchdog events)
        import threading
        def _go():
            try:
                decision_text = extract_decision_text(note)
                run_pass('challenge',
                          extra_slots={'decision_text': decision_text},
                          entity_hints=[note.title],
                          target_note_id=note.id)
            except Exception:
                pass
        t = threading.Thread(target=_go, daemon=True,
                              name=f'challenge-{note.id[:20]}')
        t.start()


def extract_decision_text(note: Note) -> str:
    """Best-effort: pull the "decision" content from a user-authored note.

    Heuristic:
      - If a `decision:` field is in frontmatter, use that.
      - Else: first non-heading paragraph of body that contains decisive
        language ("decided", "going to", "committing to", "will").
      - Else: the first non-empty paragraph.
    """
    fm_decision = note.frontmatter.get('decision')
    if isinstance(fm_decision, str) and fm_decision.strip():
        return fm_decision.strip()

    import re
    decisive = re.compile(
        r'\b(decid(?:ed|ing)|committing|going to|will|chose|picked|settled)\b',
        re.IGNORECASE,
    )
    for chunk in note.body.split('\n\n'):
        c = chunk.strip()
        if not c or c.startswith('#') or c.startswith('---'):
            continue
        if decisive.search(c):
            return c[:600]

    # Fallback: first non-empty paragraph
    for chunk in note.body.split('\n\n'):
        c = chunk.strip()
        if c and not c.startswith('#') and not c.startswith('---'):
            return c[:600]
    return note.title
