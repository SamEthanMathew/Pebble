"""Tasks & Reminders module — create, list, complete, and manage tasks."""

from __future__ import annotations
import json
from datetime import date, timedelta
from pathlib import Path

from .base import PebbleModule

_TASKS_PATH = Path.home() / '.pebble' / 'tasks.json'


class TaskModule(PebbleModule):
    name         = 'tasks'
    display_name = 'Tasks & Reminders'
    description  = 'Create, list, and complete personal tasks and reminders'
    icon         = '✅'
    config_fields: list[dict] = []

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'task_manage'

    def tool_description(self) -> str:
        return (
            'Manage personal tasks and reminders. Supports: '
            'create (add a new task, optionally with a due date), '
            'list (show all tasks with their status), '
            'pending (show only incomplete tasks), '
            'complete (mark a task done by id), '
            'set_due (set or update the due date on a task), '
            'carry_forward (return pending tasks as a markdown checklist for journaling).'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['create', 'list', 'pending', 'complete', 'set_due', 'carry_forward'],
                    'description': 'Action to perform',
                },
                'text': {
                    'type': 'string',
                    'description': 'Task description (used with create)',
                },
                'task_id': {
                    'type': 'integer',
                    'description': 'Task ID (used with complete, set_due)',
                },
                'due': {
                    'type': 'string',
                    'description': 'Due date — "today", "tomorrow", or ISO date like "2024-01-15" (used with create, set_due)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = '', text: str = '', task_id=None,
                due=None, **_) -> str:
        if action == 'create':
            return self._action_create(text, due)
        elif action == 'list':
            return self._action_list()
        elif action == 'pending':
            return self._action_pending()
        elif action == 'complete':
            return self._action_complete(task_id)
        elif action == 'set_due':
            return self._action_set_due(task_id, due)
        elif action == 'carry_forward':
            return self._action_carry_forward()
        else:
            return (
                f'Unknown action "{action}". '
                'Valid actions: create, list, pending, complete, set_due, carry_forward.'
            )

    # ── actions ────────────────────────────────────────────────────────────────

    def _action_create(self, text: str, due) -> str:
        if not text.strip():
            return 'No task text provided.'
        tasks = self._load()
        next_id = max((t['id'] for t in tasks), default=0) + 1
        due_str = self._parse_due(due) if due else None
        task = {
            'id':      next_id,
            'text':    text.strip(),
            'done':    False,
            'created': date.today().isoformat(),
            'due':     due_str,
            'tags':    [],
        }
        tasks.append(task)
        self._save(tasks)
        due_note = f', due {due_str}' if due_str else ''
        return f'Task created: {text.strip()} (id: {next_id}{due_note})'

    def _action_list(self) -> str:
        tasks = self._load()
        if not tasks:
            return 'No tasks yet.'
        return self._format_tasks(tasks)

    def _action_pending(self) -> str:
        tasks = [t for t in self._load() if not t.get('done')]
        if not tasks:
            return 'No pending tasks.'
        return self._format_tasks(tasks)

    def _action_complete(self, task_id) -> str:
        if task_id is None:
            return 'No task_id provided.'
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return f'Invalid task_id: "{task_id}".'
        tasks = self._load()
        for task in tasks:
            if task['id'] == task_id:
                task['done'] = True
                self._save(tasks)
                return f'Task {task_id} marked as done: {task["text"]}'
        return f'Task not found: id {task_id}'

    def _action_set_due(self, task_id, due) -> str:
        if task_id is None:
            return 'No task_id provided.'
        if due is None:
            return 'No due date provided.'
        try:
            task_id = int(task_id)
        except (TypeError, ValueError):
            return f'Invalid task_id: "{task_id}".'
        due_str = self._parse_due(due)
        if due_str is None:
            return f'Could not parse due date: "{due}". Use "today", "tomorrow", or YYYY-MM-DD.'
        tasks = self._load()
        for task in tasks:
            if task['id'] == task_id:
                task['due'] = due_str
                self._save(tasks)
                return f'Due date for task {task_id} set to {due_str}: {task["text"]}'
        return f'Task not found: id {task_id}'

    def _action_carry_forward(self) -> str:
        tasks = [t for t in self._load() if not t.get('done')]
        if not tasks:
            return 'No pending tasks to carry forward.'
        lines = [f'- [ ] {t["text"]}' + (f' (due {t["due"]})' if t.get('due') else '')
                 for t in tasks]
        return '\n'.join(lines)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if _TASKS_PATH.exists():
            try:
                return json.loads(_TASKS_PATH.read_text(encoding='utf-8'))
            except Exception:
                return []
        return []

    def _save(self, tasks: list[dict]):
        try:
            _TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _TASKS_PATH.write_text(
                json.dumps(tasks, indent=2, default=str),
                encoding='utf-8',
            )
        except Exception as e:
            # Best-effort; caller will see stale data on next load
            pass

    def _parse_due(self, due_str: str) -> str | None:
        if not due_str:
            return None
        normalized = due_str.strip().lower()
        if normalized == 'today':
            return date.today().isoformat()
        if normalized == 'tomorrow':
            return (date.today() + timedelta(days=1)).isoformat()
        # Try ISO date
        try:
            parsed = date.fromisoformat(due_str.strip())
            return parsed.isoformat()
        except ValueError:
            return None

    def _format_tasks(self, tasks: list[dict]) -> str:
        lines = []
        for t in tasks:
            checkbox = '[x]' if t.get('done') else '[ ]'
            due_note = f' — due {t["due"]}' if t.get('due') else ''
            lines.append(f'{checkbox} #{t["id"]} {t["text"]}{due_note}')
        return '\n'.join(lines)
