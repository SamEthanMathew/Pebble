"""Brave Search module — search the web from Pebble."""

from __future__ import annotations

import requests

from .base import PebbleModule

_SEARCH_URL = 'https://api.search.brave.com/res/v1/web/search'


class BraveSearchModule(PebbleModule):
    name         = 'brave_search'
    display_name = 'Web Search'
    description  = 'Search the web for current information. Get your free API key at brave.com/search/api'
    icon         = '🔍'
    config_fields = [
        {'key': 'brave_api_key', 'label': 'Brave Search API key', 'type': 'password'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return bool(self.cfg.get('brave_api_key', '').strip())

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'web_search'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'query': {
                    'type': 'string',
                    'description': 'Search query',
                },
                'count': {
                    'type': 'integer',
                    'description': 'Number of results (1-10, default 5)',
                },
            },
            'required': ['query'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, query: str = '', count: int = 5, **_) -> str:
        api_key = self.cfg.get('brave_api_key', '').strip()
        if not api_key:
            return 'Brave Search not configured — add your API key in Settings.'

        try:
            response = requests.get(
                _SEARCH_URL,
                params={'q': query, 'count': count},
                headers={
                    'X-Subscription-Token': api_key,
                    'Accept': 'application/json',
                },
                timeout=10,
            )
            response.raise_for_status()
            results = response.json()

            web_results = results.get('web', {}).get('results', [])
            if not web_results:
                return f"No results found for '{query}'."

            lines: list[str] = []
            for i, item in enumerate(web_results, 1):
                title       = item.get('title', '(no title)')
                url         = item.get('url', '')
                description = item.get('description', '')
                if len(description) > 200:
                    description = description[:200] + '…'
                lines.append(f'[{i}] {title}\n{url}\n{description}\n')

            return '\n'.join(lines)

        except Exception as e:
            return f'Search error: {str(e)}'
