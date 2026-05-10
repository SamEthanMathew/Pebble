"""Todoist module — add, list, complete tasks via the Todoist REST API v2."""

from __future__ import annotations

import requests

import crab_config
from .base import PebbleModule

_BASE_URL = 'https://api.todoist.com/rest/v2'


class TodoistModule(PebbleModule):
    name         = 'todoist'
    display_name = 'Todoist'
    description  = 'Access your Todoist tasks — add, list, complete tasks across all projects'
    icon         = '🔴'
    config_fields = [
        {'key': 'api_token',       'label': 'Todoist API token (Settings → Integrations → API)', 'type': 'password'},
        {'key': 'default_project', 'label': 'Default project name (optional)',                    'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(self.cfg.get('api_token', '').strip())

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'todoist'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['list', 'add', 'complete', 'projects', 'today'],
                    'description': (
                        '"list" — all active tasks, '
                        '"today" — tasks due today, '
                        '"add" — create a new task, '
                        '"complete" — mark a task done by task_id, '
                        '"projects" — list all projects'
                    ),
                },
                'task_id': {
                    'type': 'string',
                    'description': 'Task ID (used with action="complete")',
                },
                'content': {
                    'type': 'string',
                    'description': 'Task content / title (used with action="add")',
                },
                'project_name': {
                    'type': 'string',
                    'description': 'Project name to add the task to (used with action="add", optional)',
                },
                'due_string': {
                    'type': 'string',
                    'description': 'Due date in natural language, e.g. "tomorrow", "next Monday" (used with action="add")',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        action: str = 'list',
        task_id: str = '',
        content: str = '',
        project_name: str = '',
        due_string: str = '',
        **_,
    ) -> str:
        if not self.is_ready():
            return 'Todoist is not configured — add your API token in Settings → Modules → Todoist.'

        action = action.strip().lower()

        try:
            if action == 'list':
                return self._list_tasks()
            if action == 'today':
                return self._today_tasks()
            if action == 'add':
                return self._add_task(content, project_name, due_string)
            if action == 'complete':
                return self._complete_task(task_id)
            if action == 'projects':
                return self._list_projects()
            return f'Unknown action "{action}". Valid actions: list, today, add, complete, projects.'
        except Exception as e:
            return f'Todoist error: {str(e)}'

    # ── actions ───────────────────────────────────────────────────────────────

    def _list_tasks(self) -> str:
        resp = requests.get(
            f'{_BASE_URL}/tasks',
            params={'filter': '!completed'},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        tasks = resp.json()
        if not tasks:
            return 'No active tasks.'
        return self._format_tasks(tasks)

    def _today_tasks(self) -> str:
        resp = requests.get(
            f'{_BASE_URL}/tasks',
            params={'filter': 'today'},
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        tasks = resp.json()
        if not tasks:
            return 'No tasks due today.'
        return self._format_tasks(tasks)

    def _add_task(self, content: str, project_name: str, due_string: str) -> str:
        if not content.strip():
            return 'No task content provided.'

        payload: dict = {'content': content.strip()}

        # Resolve optional project
        proj_name = project_name.strip() or self.cfg.get('default_project', '').strip()
        if proj_name:
            pid = self._find_project_id(proj_name)
            if pid:
                payload['project_id'] = pid

        if due_string.strip():
            payload['due_string'] = due_string.strip()

        resp = requests.post(
            f'{_BASE_URL}/tasks',
            json=payload,
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return f'Task added: {content.strip()}'

    def _complete_task(self, task_id: str) -> str:
        if not task_id.strip():
            return 'No task_id provided.'
        resp = requests.post(
            f'{_BASE_URL}/tasks/{task_id.strip()}/close',
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        return 'Task completed!'

    def _list_projects(self) -> str:
        resp = requests.get(
            f'{_BASE_URL}/projects',
            headers=self._headers(),
            timeout=10,
        )
        resp.raise_for_status()
        projects = resp.json()
        if not projects:
            return 'No projects found.'
        lines = [f'{i}. {p["name"]} (ID: {p["id"]})' for i, p in enumerate(projects, 1)]
        return '\n'.join(lines)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _find_project_id(self, name: str) -> str | None:
        """Return the project ID matching name (case-insensitive), or None."""
        try:
            resp = requests.get(
                f'{_BASE_URL}/projects',
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            for p in resp.json():
                if p.get('name', '').strip().lower() == name.strip().lower():
                    return p['id']
        except Exception:
            pass
        return None

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self.cfg.get("api_token", "").strip()}'}

    @staticmethod
    def _format_tasks(tasks: list[dict]) -> str:
        lines: list[str] = []
        for i, t in enumerate(tasks, 1):
            content  = t.get('content', '')
            proj_id  = t.get('project_id', '')
            due      = t.get('due', {}) or {}
            due_str  = due.get('string') or due.get('date') or ''
            due_part = f' due:{due_str}' if due_str else ''
            lines.append(f'{i}. {content}{due_part}')
        return '\n'.join(lines)
