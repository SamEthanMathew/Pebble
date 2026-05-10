"""Canvas LMS module — assignments, grades, announcements via Canvas REST API."""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
import datetime
from .base import PebbleModule


class CanvasModule(PebbleModule):
    name         = 'canvas'
    display_name = 'Canvas LMS'
    description  = 'Check assignments, grades, courses, and announcements from Canvas'
    icon         = '🎓'
    config_fields = [
        {'key': 'base_url',      'label': 'Canvas URL (e.g. https://canvas.cmu.edu)', 'type': 'text'},
        {'key': 'access_token',  'label': 'Access Token',  'type': 'password'},
    ]

    def is_ready(self) -> bool:
        return bool(self.cfg.get('base_url') and self.cfg.get('access_token'))

    def tool_name(self) -> str:
        return 'canvas'

    def tool_description(self) -> str:
        return ('Access Canvas LMS: get courses, upcoming assignments, missing work, '
                'announcements, and grades.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['courses', 'assignments', 'upcoming', 'missing', 'announcements', 'grades'],
                    'description': 'What to retrieve from Canvas',
                },
                'course_id': {
                    'type': 'string',
                    'description': 'Course ID (optional — omit to query all courses)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'upcoming', course_id: str = '', **_) -> str:
        base = self.cfg.get('base_url', '').rstrip('/')
        token = self.cfg.get('access_token', '')

        def _get(path: str, params: dict | None = None) -> list | dict:
            url = f'{base}/api/v1/{path}'
            if params:
                url += '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())

        try:
            if action == 'courses':
                return self._courses(_get)

            elif action == 'assignments':
                return self._assignments(_get, course_id)

            elif action == 'upcoming':
                return self._upcoming(_get, course_id)

            elif action == 'missing':
                return self._missing(_get, course_id)

            elif action == 'announcements':
                return self._announcements(_get, course_id)

            elif action == 'grades':
                return self._grades(_get, course_id)

            else:
                return f'Unknown action: {action}'

        except Exception as e:
            return f'Canvas error: {e}'

    # ── helpers ────────────────────────────────────────────────────────────────

    def _courses(self, get) -> str:
        courses = get('courses', {'enrollment_state': 'active', 'per_page': 20})
        if not courses:
            return 'No active courses found.'
        lines = ['Active courses:']
        for c in courses:
            lines.append(f"  [{c['id']}] {c.get('name', 'Unnamed')} — {c.get('course_code', '')}")
        return '\n'.join(lines)

    def _assignments(self, get, course_id: str) -> str:
        if course_id:
            cids = [course_id]
        else:
            cids = [str(c['id']) for c in get('courses', {'enrollment_state': 'active', 'per_page': 20})]

        lines = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for cid in cids[:5]:
            try:
                asmts = get(f'courses/{cid}/assignments', {'per_page': 10, 'order_by': 'due_at'})
                course_name = next((c.get('name','') for c in get('courses', {'per_page': 20}) if str(c['id']) == str(cid)), cid)
                for a in asmts:
                    due = a.get('due_at', '')
                    due_str = ''
                    if due:
                        dt = datetime.datetime.fromisoformat(due.replace('Z', '+00:00'))
                        days_away = (dt - now).days
                        due_str = f" (due {dt.strftime('%b %d')}{',' if days_away < 0 else f', in {days_away+1}d' if days_away >= 0 else ''})"
                    lines.append(f"  [{course_name[:20]}] {a.get('name', '?')}{due_str}")
            except Exception:
                continue

        return ('Assignments:\n' + '\n'.join(lines)) if lines else 'No assignments found.'

    def _upcoming(self, get, course_id: str) -> str:
        now = datetime.datetime.now(datetime.timezone.utc)
        week_out = now + datetime.timedelta(days=7)

        if course_id:
            cids = [course_id]
        else:
            cids = [str(c['id']) for c in get('courses', {'enrollment_state': 'active', 'per_page': 20})]

        upcoming = []
        for cid in cids[:8]:
            try:
                asmts = get(f'courses/{cid}/assignments', {
                    'per_page': 10, 'order_by': 'due_at',
                    'bucket': 'upcoming',
                })
                for a in asmts:
                    due = a.get('due_at', '')
                    if not due:
                        continue
                    dt = datetime.datetime.fromisoformat(due.replace('Z', '+00:00'))
                    if now <= dt <= week_out:
                        upcoming.append((dt, a.get('name', '?'), cid))
            except Exception:
                continue

        if not upcoming:
            return 'No assignments due in the next 7 days.'

        upcoming.sort(key=lambda x: x[0])
        lines = ['Upcoming this week:']
        for dt, name, cid in upcoming:
            days = (dt - now).days
            lines.append(f"  {'TODAY' if days == 0 else 'TOMORROW' if days == 1 else dt.strftime('%a %b %d')}: {name}")
        return '\n'.join(lines)

    def _missing(self, get, course_id: str) -> str:
        if course_id:
            cids = [course_id]
        else:
            cids = [str(c['id']) for c in get('courses', {'enrollment_state': 'active', 'per_page': 20})]

        missing = []
        for cid in cids[:8]:
            try:
                asmts = get(f'courses/{cid}/assignments', {'per_page': 10, 'bucket': 'missing'})
                for a in asmts:
                    missing.append(f"  {a.get('name', '?')} (course {cid})")
            except Exception:
                continue

        if not missing:
            return 'No missing assignments.'
        return 'Missing assignments:\n' + '\n'.join(missing)

    def _announcements(self, get, course_id: str) -> str:
        try:
            if course_id:
                params: dict = {'context_codes[]': f'course_{course_id}', 'per_page': 5}
            else:
                courses = get('courses', {'enrollment_state': 'active', 'per_page': 20})
                ctx = [f'course_{c["id"]}' for c in courses[:6]]
                params = {'per_page': 8}
                for c in ctx:
                    params.setdefault('context_codes[]', c)

            announcements = get('announcements', params)
            if not announcements:
                return 'No recent announcements.'
            lines = ['Recent announcements:']
            for a in announcements[:6]:
                posted = a.get('posted_at', '')[:10]
                lines.append(f"  [{posted}] {a.get('title', '?')} ({a.get('context_name', '')})")
            return '\n'.join(lines)
        except Exception as e:
            return f'Could not fetch announcements: {e}'

    def _grades(self, get, course_id: str) -> str:
        try:
            enrollments = get('users/self/enrollments', {
                'type[]': 'StudentEnrollment',
                'state[]': 'active',
                'per_page': 20,
            })
            if not enrollments:
                return 'No grade data found.'
            lines = ['Current grades:']
            for e in enrollments:
                if course_id and str(e.get('course_id', '')) != str(course_id):
                    continue
                grade = e.get('grades', {})
                final = grade.get('final_grade') or grade.get('final_score', 'N/A')
                cname = e.get('course', {}).get('name', f"Course {e.get('course_id', '?')}")
                lines.append(f"  {cname}: {final}")
            return '\n'.join(lines)
        except Exception as e:
            return f'Could not fetch grades: {e}'
