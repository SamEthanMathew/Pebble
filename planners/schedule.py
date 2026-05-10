"""Schedule planner — reasons about today's time allocation, not just lists events.

Cloud-only: disabled when no planner_model is configured.
Output: ~/.pebble/state/schedule_today.json (envelope per contracts.md §7).
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

import audit
import prompts as prompt_lib
import entity_store
from planners.base import BasePlanner


_TASKS_PATH = Path.home() / '.pebble' / 'tasks.json'


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _strip_code_fence(text: str) -> str:
    """LLMs often wrap JSON in ```json ... ``` — peel it off."""
    text = text.strip()
    m = re.match(r'^```(?:json)?\s*\n(.*)\n```\s*$', text, re.DOTALL)
    if m:
        return m.group(1)
    return text


class SchedulePlanner(BasePlanner):
    name        = 'schedule'
    state_doc   = 'schedule_today.json'
    ttl_seconds = 60 * 60  # 1 hour; calendar diff fires re-runs sooner

    # ── input collection ──────────────────────────────────────────────────────

    def collect_inputs(self) -> dict[str, Any]:
        return {
            'date':                 _today_iso(),
            'events':               self._read_gcal_events_today(),
            'tasks_with_deadlines': self._read_tasks_with_deadlines(),
            'entity_context':       self._read_entity_context(),
        }

    def _read_gcal_events_today(self) -> list[dict[str, Any]]:
        try:
            from modules.google_auth import GoogleServices, is_google_connected
            if not is_google_connected():
                return []
            svc = GoogleServices()
            today = datetime.date.today()
            time_min = datetime.datetime.combine(today, datetime.time.min).astimezone().isoformat()
            time_max = datetime.datetime.combine(today, datetime.time.max).astimezone().isoformat()
            events = svc.calendar.events().list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime',
                maxResults=25,
            ).execute().get('items', [])
            return [
                {
                    'id':       e.get('id', ''),
                    'title':    e.get('summary', '(no title)'),
                    'start':    (e.get('start') or {}).get('dateTime') or (e.get('start') or {}).get('date', ''),
                    'end':      (e.get('end') or {}).get('dateTime') or (e.get('end') or {}).get('date', ''),
                    'location': e.get('location', ''),
                    'attendees_count': len(e.get('attendees', []) or []),
                }
                for e in events
            ]
        except Exception as exc:
            audit.append({
                'module': 'planner.schedule', 'action': 'gcal_read_failed',
                'result': {'error': str(exc)}, 'tier': 'auto', 'source': 'planner.schedule',
            })
            return []

    def _read_tasks_with_deadlines(self) -> list[dict[str, Any]]:
        if not _TASKS_PATH.exists():
            return []
        try:
            tasks = json.loads(_TASKS_PATH.read_text(encoding='utf-8'))
        except Exception:
            return []
        cutoff = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
        out = []
        for t in tasks:
            if t.get('done'):
                continue
            due = t.get('due') or ''
            if not due:
                continue
            if due <= cutoff:
                out.append({
                    'id':   t.get('id'),
                    'text': t.get('text', ''),
                    'due':  due,
                })
        return out

    def _read_entity_context(self) -> list[dict[str, Any]]:
        """Pull current-semester courses and recurring entities."""
        try:
            entity_store.init()
            ents = entity_store.list_entities(type='course') + entity_store.list_entities(type='recurring')
            return [{'type': e.type, 'name': e.name, 'aliases': e.aliases,
                     'payload': e.payload} for e in ents]
        except Exception:
            return []

    # ── prompt rendering ──────────────────────────────────────────────────────

    def render_prompt(self, inputs: dict[str, Any]) -> tuple[str, str]:
        prev = ''
        try:
            from planners.base import read_state_doc
            env = read_state_doc(self.state_doc)
            if env:
                prev = json.dumps(env.get('payload', {}), indent=2, default=str)[:1500]
        except Exception:
            prev = ''

        system_prompt = prompt_lib.render('schedule_planner', {
            'date':                 inputs['date'],
            'events':               json.dumps(inputs['events'], indent=2, default=str),
            'tasks_with_deadlines': json.dumps(inputs['tasks_with_deadlines'], indent=2, default=str),
            'entity_context':       json.dumps(inputs['entity_context'], indent=2, default=str),
            'prev_state':           prev or '(none)',
        })
        user_msg = 'Generate the schedule_today.json payload now.'
        return system_prompt, user_msg

    # ── output parsing ────────────────────────────────────────────────────────

    def parse_output(self, llm_text: str, inputs: dict[str, Any]) -> dict[str, Any]:
        cleaned = _strip_code_fence(llm_text)
        data = json.loads(cleaned)

        # Light shape validation — per contracts.md §7a payload schema
        if not isinstance(data, dict):
            raise ValueError('schedule planner output must be a JSON object')
        data.setdefault('date',          inputs['date'])
        data.setdefault('blocks',        [])
        data.setdefault('free_windows', [])
        data.setdefault('conflicts',    [])
        data.setdefault('transitions', [])
        return data
