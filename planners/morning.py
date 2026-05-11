"""Morning briefing v2 — LLM-driven, reads all three state docs.

Phase 2: exposes generate_briefing() as a synchronous one-shot. Wiring it to
trigger automatically at wake_time is Phase 3 work; for now the user invokes it
via /briefing in chat or via a debug button.

Cloud-only: returns None when no planner_model is configured.
"""

from __future__ import annotations

import datetime
import json
import sys
from typing import Any

import audit
import metrics
import prompts as prompt_lib
from planners.base import read_state_doc
from planners.schedule import SchedulePlanner
from planners.comms    import CommsPlanner
from planners.school   import SchoolPlanner


def _get_planner_backend():
    try:
        import crab_config
        from model_backend import ModelBackend
        cfg = crab_config.get('model', {}) or {}
        planner_model = cfg.get('planner_model') or cfg.get('primary')
        if not planner_model:
            return None
        return ModelBackend.for_id(planner_model)
    except Exception:
        return None


def _now_human() -> str:
    now = datetime.datetime.now()
    return now.strftime('%A, %B %d %Y · %H:%M')


def _safe_run(planner_cls) -> dict[str, Any] | None:
    try:
        return planner_cls().run()
    except Exception as e:
        audit.append({
            'module': f'morning_briefing', 'action': f'planner_failed:{planner_cls.__name__}',
            'result': {'error': str(e)}, 'tier': 'auto', 'source': 'morning_briefing',
        })
        return None


def _load_vault_context(schedule_env: dict, comms_env: dict, school_env: dict) -> str:
    """Pull a ContextBundle from the Obsidian vault and render to Markdown.

    Best-effort: any failure (no vault configured, parsing error, etc.) silently
    returns an empty string so the briefing still works without vault content.
    """
    try:
        import crab_config
        from storage import Vault, EntityResolver, load_context, render_bundle_markdown
        vault_path = (crab_config.get_module_config('obsidian') or {}).get('vault_path')
        if not vault_path:
            return ''

        # Build a trigger from today's schedule entity titles so the resolver
        # surfaces the people/projects/courses you'll be dealing with
        entity_hints: list[str] = []
        for block in (schedule_env.get('payload', {}) or {}).get('blocks', [])[:8]:
            title = (block.get('title') or '').strip()
            if title:
                entity_hints.append(title)
        for item in (comms_env.get('payload', {}) or {}).get('action_required', [])[:5]:
            fr = item.get('from') or {}
            if fr.get('name'):
                entity_hints.append(fr['name'])

        vault = Vault(vault_path, autostart_watcher=False)
        try:
            bundle = load_context(
                trigger={'type': 'morning_briefing', 'title': 'Daily briefing',
                          'entity_hints': entity_hints},
                vault=vault,
                resolver=EntityResolver(vault),
                depth=1,
                max_tokens=3000,
                include_daily_notes=3,
                provenance='all',
            )
            return render_bundle_markdown(bundle, max_chars=3500)
        finally:
            vault.stop()
    except Exception as e:
        try:
            audit.append({
                'module': 'morning_briefing', 'action': 'vault_context_failed',
                'result': {'error': str(e)}, 'tier': 'auto', 'source': 'morning_briefing',
            })
        except Exception:
            pass
        return ''


def generate_briefing(*, refresh_planners: bool = True,
                      weather_summary: str = '(unavailable)',
                      overdue_tasks: str = '(unavailable)') -> str | None:
    """Generate the morning briefing text.

    If `refresh_planners` is True, runs schedule/comms/school planners first so
    state docs are fresh. Reads all three state docs into the briefing prompt.
    Returns the briefing text, or None when planner_model isn't configured.
    """
    backend = _get_planner_backend()
    if backend is None:
        print('[morning_briefing] disabled — no planner_model configured', file=sys.stderr)
        metrics.emit('planner.skipped',
                     {'planner': 'morning_briefing', 'gate_reason': 'no_planner_model'})
        return None

    metrics.emit('planner.started', {'planner': 'morning_briefing'})

    # Refresh upstream state docs (each respects its own re-run gate)
    if refresh_planners:
        _safe_run(SchedulePlanner)
        _safe_run(CommsPlanner)
        _safe_run(SchoolPlanner)

    schedule_env = read_state_doc('schedule_today.json') or {}
    comms_env    = read_state_doc('comms_pending.json')  or {}
    school_env   = read_state_doc('school_status.json')  or {}

    # ── Vault context: pull entities, preferences, goals from Obsidian ──
    vault_context = _load_vault_context(schedule_env, comms_env, school_env)

    # Compose slot values
    schedule_str = json.dumps(schedule_env.get('payload', {}), indent=2, default=str)[:4000]
    if vault_context:
        # Prepend vault context to the schedule slot — keeps the prompt template stable
        schedule_str = f'{vault_context}\n\n---\n\n## Schedule state\n{schedule_str}'

    system_prompt = prompt_lib.render('morning_briefing', {
        'datetime':       _now_human(),
        'schedule_state': schedule_str,
        'comms_state':    json.dumps(comms_env.get('payload', {}),    indent=2, default=str)[:4000],
        'school_state':   json.dumps(school_env.get('payload', {}),   indent=2, default=str)[:4000],
        'weather':        weather_summary,
        'overdue_tasks':  overdue_tasks,
    })

    try:
        text = backend.chat(
            [{'role': 'user', 'content': 'Deliver the briefing now.'}],
            system=system_prompt,
        )
    except Exception as e:
        audit.append({
            'module': 'morning_briefing', 'action': 'llm_call_failed',
            'result': {'error': str(e)}, 'tier': 'auto', 'source': 'morning_briefing',
        })
        metrics.emit('planner.skipped',
                     {'planner': 'morning_briefing', 'gate_reason': 'llm_failed'})
        return None

    audit.append({
        'module': 'morning_briefing', 'action': 'generated',
        'args':   {'preview_chars': len(text)},
        'result': {'ok': True}, 'tier': 'auto', 'source': 'morning_briefing',
    })
    metrics.emit('planner.finished', {'planner': 'morning_briefing'})
    return text
