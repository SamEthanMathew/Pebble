"""Notion module — search pages, read content, create pages and tasks."""

from __future__ import annotations

import requests

from .base import PebbleModule

_BASE_URL = 'https://api.notion.com/v1'
_NOTION_VERSION = '2022-06-28'


class NotionModule(PebbleModule):
    name         = 'notion'
    display_name = 'Notion'
    icon         = '⬛'
    config_fields = [
        {'key': 'notion_api_key',      'label': 'Integration token (secret_...)', 'type': 'password'},
        {'key': 'default_database_id', 'label': 'Default database ID',            'type': 'text'},
    ]

    def __init__(self, cfg: dict):
        super().__init__(cfg)

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(self.cfg.get('notion_api_key', '').strip())

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'notion'

    def tool_description(self) -> str:
        return (
            'Interact with Notion: search pages and databases, read the content of a '
            'page, create new pages inside a database or under a parent page, and add '
            'tasks to a Notion database. Use this when the user asks about their notes, '
            'documents, tasks, or anything stored in Notion.'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['search', 'read_page', 'create_page', 'add_task'],
                    'description': (
                        'Action to perform: "search" to find pages by keyword, '
                        '"read_page" to get the text content of a page, '
                        '"create_page" to create a new page, '
                        '"add_task" to add a task/to-do to the default database.'
                    ),
                },
                'query': {
                    'type': 'string',
                    'description': 'Search query text (used with action="search").',
                },
                'page_id': {
                    'type': 'string',
                    'description': (
                        'Notion page or block ID (used with action="read_page" or as '
                        'the parent for action="create_page" when no database is set).'
                    ),
                },
                'title': {
                    'type': 'string',
                    'description': 'Title for a new page or task (used with create_page / add_task).',
                },
                'content': {
                    'type': 'string',
                    'description': 'Body text for a new page (used with action="create_page").',
                },
            },
            'required': ['action'],
        }

    # ── dispatch ──────────────────────────────────────────────────────────────

    def execute(
        self,
        action: str,
        query: str = '',
        page_id: str = '',
        title: str = '',
        content: str = '',
        **_,
    ) -> str:
        if not self.is_ready():
            return (
                'Notion is not configured. '
                'Add your integration token in Settings → Modules → Notion.'
            )

        action = action.strip().lower()

        if action == 'search':
            return self._search(query)
        if action == 'read_page':
            return self._read_page(page_id)
        if action == 'create_page':
            return self._create_page(title=title, content=content, page_id=page_id)
        if action == 'add_task':
            return self._add_task(title=title)

        return f'Unknown action "{action}". Valid actions: search, read_page, create_page, add_task.'

    # ── actions ───────────────────────────────────────────────────────────────

    def _search(self, query: str) -> str:
        payload: dict = {'page_size': 10 if query.strip() else 5}
        if query.strip():
            payload['query'] = query.strip()

        try:
            resp = requests.post(
                f'{_BASE_URL}/search',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
        except requests.RequestException:
            return 'Notion unreachable — check your API key and internet connection.'

        if not resp.ok:
            return self._api_error(resp)

        results = resp.json().get('results', [])
        pages = [r for r in results if r.get('object') == 'page']

        if not pages:
            if query.strip():
                return f'No Notion pages found for "{query}".'
            return 'No Notion pages found.'

        lines = []
        for i, page in enumerate(pages, 1):
            t = self._extract_title(page)
            pid = page.get('id', '?')
            lines.append(f'{i}. {t} (ID: {pid})')
        return '\n'.join(lines)

    def _read_page(self, page_id: str) -> str:
        if not page_id.strip():
            return 'No page_id provided.'

        try:
            resp = requests.get(
                f'{_BASE_URL}/blocks/{page_id.strip()}/children',
                headers=self._headers(),
                params={'page_size': 50},
                timeout=10,
            )
        except requests.RequestException:
            return 'Notion unreachable — check your API key and internet connection.'

        if not resp.ok:
            return self._api_error(resp)

        blocks = resp.json().get('results', [])
        text_parts = []

        text_block_types = {
            'paragraph', 'heading_1', 'heading_2', 'heading_3',
            'bulleted_list_item', 'numbered_list_item', 'to_do', 'quote', 'code',
        }

        for block in blocks:
            btype = block.get('type', '')
            if btype not in text_block_types:
                continue
            rich_texts = block.get(btype, {}).get('rich_text', [])
            chunk = ''.join(rt.get('plain_text', '') for rt in rich_texts)
            if chunk.strip():
                text_parts.append(chunk)

        if not text_parts:
            return 'Page not found or empty.'

        full_text = '\n'.join(text_parts)
        if len(full_text) > 3000:
            full_text = full_text[:3000] + '…'
        return full_text

    def _create_page(self, title: str, content: str, page_id: str) -> str:
        default_db = self.cfg.get('default_database_id', '').strip()

        if page_id.strip():
            parent = {'page_id': page_id.strip()}
        elif default_db:
            parent = {'database_id': default_db}
        else:
            return (
                'No default database configured. '
                'Set it in Settings → Modules → Notion.'
            )

        page_title = title.strip() or 'Untitled'

        properties: dict = {
            'title': {
                'title': [{'text': {'content': page_title}}]
            }
        }

        children: list[dict] = []
        if content.strip():
            children.append({
                'object': 'block',
                'type': 'paragraph',
                'paragraph': {
                    'rich_text': [{'type': 'text', 'text': {'content': content.strip()}}]
                },
            })

        payload: dict = {
            'parent': parent,
            'properties': properties,
        }
        if children:
            payload['children'] = children

        try:
            resp = requests.post(
                f'{_BASE_URL}/pages',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
        except requests.RequestException:
            return 'Notion unreachable — check your API key and internet connection.'

        if not resp.ok:
            return self._api_error(resp)

        new_page = resp.json()
        new_id = new_page.get('id', '?')
        return f'Page created: {page_title} (ID: {new_id})'

    def _add_task(self, title: str) -> str:
        default_db = self.cfg.get('default_database_id', '').strip()
        if not default_db:
            return (
                'No default database configured. '
                'Set it in Settings → Modules → Notion.'
            )

        task_title = title.strip() or 'New Task'

        # Build minimal properties — title is always required.
        # We'll attempt to set a checkbox "Done" or "Status" field if present
        # by introspecting the database schema first.
        extra_props = self._task_extra_properties(default_db)

        properties: dict = {
            'title': {
                'title': [{'text': {'content': task_title}}]
            },
            **extra_props,
        }

        payload = {
            'parent': {'database_id': default_db},
            'properties': properties,
        }

        try:
            resp = requests.post(
                f'{_BASE_URL}/pages',
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
        except requests.RequestException:
            return 'Notion unreachable — check your API key and internet connection.'

        if not resp.ok:
            return self._api_error(resp)

        return f'Task added to Notion: {task_title}'

    # ── helpers ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        api_key = self.cfg.get('notion_api_key', '').strip()
        return {
            'Authorization':  f'Bearer {api_key}',
            'Notion-Version': _NOTION_VERSION,
            'Content-Type':   'application/json',
        }

    @staticmethod
    def _extract_title(page: dict) -> str:
        props = page.get('properties', {})
        # Try 'title' property (default Notion name)
        try:
            return props['title']['title'][0]['plain_text']
        except (KeyError, IndexError, TypeError):
            pass
        # Try 'Name' property (common in databases)
        try:
            return props['Name']['title'][0]['plain_text']
        except (KeyError, IndexError, TypeError):
            pass
        # Fallback to page ID
        return page.get('id', 'Untitled')

    @staticmethod
    def _api_error(resp: requests.Response) -> str:
        try:
            msg = resp.json().get('message', resp.text)
        except Exception:
            msg = resp.text
        return f'Notion error: {resp.status_code} — {msg}'

    def _task_extra_properties(self, database_id: str) -> dict:
        """Introspect the database schema and return any task-relevant properties."""
        try:
            resp = requests.get(
                f'{_BASE_URL}/databases/{database_id}',
                headers=self._headers(),
                timeout=8,
            )
            if not resp.ok:
                return {}
            schema = resp.json().get('properties', {})
            extra: dict = {}
            for prop_name, prop_def in schema.items():
                ptype = prop_def.get('type', '')
                # Set checkbox props named "Done" or "Completed" to unchecked (False)
                if ptype == 'checkbox' and prop_name.lower() in ('done', 'completed'):
                    extra[prop_name] = {'checkbox': False}
                # Set status props to their first available option if present
                elif ptype == 'status' and prop_name.lower() in ('status',):
                    options = (
                        prop_def.get('status', {}).get('options', [])
                    )
                    if options:
                        extra[prop_name] = {'status': {'name': options[0]['name']}}
            return extra
        except Exception:
            return {}
