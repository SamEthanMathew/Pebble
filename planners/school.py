"""School planner — academic strategist, status doc only (Phase 2).

Phase 3 will add the exam-prep chain on top (planners/exam_prep.py).

Cloud-only: disabled when no planner_model is configured.
Output: ~/.pebble/state/school_status.json (envelope per contracts.md §7c).
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

import audit
import entity_store
import prompts as prompt_lib
from planners.base import BasePlanner


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*\n(.*)\n```\s*$', text, re.DOTALL)
    if m:
        return m.group(1)
    return text


class SchoolPlanner(BasePlanner):
    name        = 'school'
    state_doc   = 'school_status.json'
    ttl_seconds = 60 * 60 * 4  # 4 hours; Canvas polls every 2hr

    # ── input collection ──────────────────────────────────────────────────────

    def collect_inputs(self) -> dict[str, Any]:
        return {
            'today':                  _today_iso(),
            'courses':                self._read_courses(),
            'canvas_assignments':     self._read_canvas(),
            'obsidian_notes_summary': self._read_obsidian_summary(),
            'upcoming_exams':         self._read_upcoming_exams(),
        }

    def _read_courses(self) -> list[dict[str, Any]]:
        try:
            entity_store.init()
            return [{'name': e.name, 'aliases': e.aliases, 'payload': e.payload}
                    for e in entity_store.list_entities(type='course')]
        except Exception:
            return []

    def _read_canvas(self) -> list[dict[str, Any]]:
        """Pull current assignments from the Canvas module if it's configured."""
        try:
            from modules.canvas import CanvasModule
            import crab_config
            cfg = crab_config.get_module_config('canvas') or {}
            if not cfg.get('access_token') or not cfg.get('base_url'):
                return []
            mod = CanvasModule(cfg)
            if not mod.is_ready():
                return []
            # Best-effort: ask Canvas for upcoming assignments via the module's tool surface
            try:
                raw = mod.execute(action='upcoming')  # if module supports it
            except Exception:
                raw = mod.execute(action='list')
            # Module returns text; structured result lives in cache. For now expose raw text.
            return [{'raw': str(raw)[:4000]}]
        except Exception as exc:
            audit.append({
                'module': 'planner.school', 'action': 'canvas_read_failed',
                'result': {'error': str(exc)}, 'tier': 'auto', 'source': 'planner.school',
            })
            return []

    def _read_obsidian_summary(self) -> dict[str, str]:
        """Per-course Obsidian notes summary. Stub for Phase 2 — full integration in Phase 3."""
        return {}

    def _read_upcoming_exams(self) -> list[dict[str, Any]]:
        """Exam dates from course entity payloads (per-course `exam_dates` field)."""
        out = []
        try:
            entity_store.init()
            today = datetime.date.today()
            cutoff = today + datetime.timedelta(days=21)
            for course in entity_store.list_entities(type='course'):
                exam_dates = (course.payload or {}).get('exam_dates') or []
                for ed in exam_dates:
                    try:
                        d = datetime.date.fromisoformat(str(ed)[:10])
                        if today <= d <= cutoff:
                            out.append({'course': course.name, 'date': d.isoformat()})
                    except Exception:
                        continue
        except Exception:
            pass
        return out

    # ── prompt rendering ──────────────────────────────────────────────────────

    def render_prompt(self, inputs: dict[str, Any]) -> tuple[str, str]:
        system_prompt = prompt_lib.render('school_status', {
            'today':                  inputs['today'],
            'courses':                json.dumps(inputs['courses'], indent=2, default=str),
            'canvas_assignments':     json.dumps(inputs['canvas_assignments'], indent=2, default=str),
            'obsidian_notes_summary': json.dumps(inputs['obsidian_notes_summary'], indent=2, default=str),
            'upcoming_exams':         json.dumps(inputs['upcoming_exams'], indent=2, default=str),
        })
        user_msg = 'Produce school_status.json now.'
        return system_prompt, user_msg

    # ── output parsing ────────────────────────────────────────────────────────

    def parse_output(self, llm_text: str, inputs: dict[str, Any]) -> dict[str, Any]:
        cleaned = _strip_code_fence(llm_text)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError('school planner output must be a JSON object')
        data.setdefault('courses',    [])
        data.setdefault('exam_plans', [])
        return data
