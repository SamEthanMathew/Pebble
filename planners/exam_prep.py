"""Exam-prep chain — generates a day-by-day study plan for one upcoming exam.

Not a BasePlanner subclass — invoked with explicit context (course + exam date),
not auto-discovered. The school planner (or /exam-prep chat command) decides
when to call this.

Pipeline:
  Course entity + exam date
  → resolve topics (entity payload OR scraped course site)
  → resolve Obsidian notes per topic
  → call exam_prep.md
  → parse & store the plan
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from typing import Any

import audit
import metrics
import prompts as prompt_lib
from atomic_io import write_json, read_json
from pathlib import Path

_PLANS_PATH = Path.home() / '.pebble' / 'state' / 'exam_plans.json'


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*\n(.*)\n```\s*$', text, re.DOTALL)
    return m.group(1) if m else text


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


def _resolve_topics(course_payload: dict[str, Any]) -> str:
    """Pull topics from entity payload, or scrape course site if URL given."""
    if course_payload.get('topics'):
        return str(course_payload['topics'])
    url = course_payload.get('course_site_url') or course_payload.get('url')
    if url:
        try:
            import scraper
            data = scraper.fetch(url, source='syllabus')
            if data:
                return data['text'][:6000]
        except Exception:
            pass
    return '(topics not available)'


def _resolve_obsidian_summary(course_name: str) -> str:
    """Best-effort Obsidian search for course-named notes."""
    try:
        from modules.obsidian import ObsidianModule
        import crab_config
        cfg = crab_config.get_module_config('obsidian') or {}
        if not cfg.get('vault_path'):
            return '(no Obsidian vault configured)'
        mod = ObsidianModule(cfg)
        if not mod.is_ready():
            return '(Obsidian not ready)'
        try:
            return str(mod.execute(action='search', query=course_name))[:3000]
        except Exception:
            return '(search failed)'
    except Exception:
        return '(unavailable)'


def _available_blocks(days: int) -> str:
    """Sketch from schedule_today.json + reasonable evening blocks. Phase 4 will refine."""
    today = datetime.date.today()
    return ', '.join(
        f'{(today + datetime.timedelta(days=i)).isoformat()} 14:00-16:00, 19:00-21:00'
        for i in range(days)
    )


def generate_for_course(course_entity, exam_date: str) -> dict[str, Any] | None:
    """Generate an exam plan for one course's exam.

    `course_entity` is a `entity_store.Entity` (or any object with .name and .payload).
    `exam_date` is ISO YYYY-MM-DD.

    Returns the plan payload, or None if disabled/parse-failed/no-backend.
    """
    backend = _planner_backend()
    if backend is None:
        print('[exam_prep] disabled — no planner_model configured', file=sys.stderr)
        metrics.emit('planner.skipped',
                     {'planner': 'exam_prep', 'gate_reason': 'no_planner_model'})
        return None

    try:
        exam_dt = datetime.date.fromisoformat(exam_date[:10])
    except Exception:
        return None

    days_remaining = (exam_dt - datetime.date.today()).days
    if days_remaining < 0:
        return None  # past exam

    payload = course_entity.payload or {}
    topics  = _resolve_topics(payload)
    notes   = _resolve_obsidian_summary(course_entity.name)
    blocks  = _available_blocks(min(days_remaining, 7))

    metrics.emit('planner.started', {'planner': 'exam_prep'})

    system_prompt = prompt_lib.render('exam_prep', {
        'course_name':            course_entity.name,
        'course_code':            payload.get('code', course_entity.name),
        'exam_date':              exam_date,
        'days_remaining':         days_remaining,
        'topics':                 topics,
        'obsidian_notes_summary': notes,
        'available_blocks':       blocks,
    })

    try:
        text = backend.chat(
            [{'role': 'user', 'content': 'Generate the study plan now as JSON.'}],
            system=system_prompt,
        )
    except Exception as e:
        audit.append({
            'module': 'exam_prep', 'action': 'llm_failed',
            'result': {'error': str(e)}, 'tier': 'auto', 'source': 'exam_prep',
        })
        metrics.emit('planner.skipped',
                     {'planner': 'exam_prep', 'gate_reason': 'llm_failed'})
        return None

    try:
        plan = json.loads(_strip_code_fence(text))
    except Exception as e:
        audit.append({
            'module': 'exam_prep', 'action': 'parse_failed',
            'result': {'error': str(e), 'raw_preview': text[:300]},
            'tier':   'auto', 'source': 'exam_prep',
        })
        metrics.emit('planner.skipped',
                     {'planner': 'exam_prep', 'gate_reason': 'parse_failed'})
        return None

    plan.setdefault('course',     course_entity.name)
    plan.setdefault('exam_date',  exam_date)
    plan.setdefault('plan',       [])

    _save_plan(plan)
    audit.append({
        'module': 'exam_prep', 'action': 'plan_generated',
        'args':   {'course': course_entity.name, 'exam_date': exam_date,
                   'days_remaining': days_remaining},
        'result': {'block_count': len(plan.get('plan', []))},
        'tier':   'auto', 'source': 'exam_prep',
    })
    metrics.emit('planner.finished', {'planner': 'exam_prep'})
    return plan


def _save_plan(plan: dict[str, Any]) -> None:
    """Append/replace plan in exam_plans.json keyed by (course, exam_date)."""
    current = read_json(_PLANS_PATH, default={'plans': []}) or {'plans': []}
    plans = current.get('plans', [])
    plans = [p for p in plans
             if not (p.get('course') == plan.get('course') and
                     p.get('exam_date') == plan.get('exam_date'))]
    plans.append(plan)
    write_json(_PLANS_PATH, {'plans': plans})


def list_plans() -> list[dict[str, Any]]:
    data = read_json(_PLANS_PATH, default={'plans': []}) or {'plans': []}
    return data.get('plans', [])


def plans_path() -> Path:
    return _PLANS_PATH
