"""Persistent memory module — Pebble remembers facts across conversations."""

from __future__ import annotations

import json
import datetime
from pathlib import Path
from .base import PebbleModule

_MEMORY_PATH = Path.home() / '.pebble' / 'memory.json'


def _load() -> list[dict]:
    try:
        return json.loads(_MEMORY_PATH.read_text(encoding='utf-8'))
    except Exception:
        return []


def _save(items: list[dict]):
    _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MEMORY_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding='utf-8')


class MemoryModule(PebbleModule):
    name         = 'memory'
    display_name = 'Pebble Memory'
    description  = 'Remember and recall facts, preferences, and context across conversations'
    icon         = '🧠'
    config_fields: list[dict] = []

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'memory'

    def tool_description(self) -> str:
        return ('Store and retrieve persistent facts about the user, preferences, '
                'context, and anything Pebble should remember across sessions. '
                'Use "remember" when user says to remember something, "recall" to look something up, '
                '"list" to show all memories, "forget" to delete a specific memory.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['remember', 'recall', 'list', 'forget'],
                    'description': 'remember=save a fact, recall=search memories, list=show all, forget=delete by keyword',
                },
                'text': {
                    'type': 'string',
                    'description': 'The fact to remember, or search/delete query',
                },
                'category': {
                    'type': 'string',
                    'description': 'Optional category: preference, fact, goal, person, place, other',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'list', text: str = '', category: str = 'fact', **_) -> str:
        try:
            if action == 'remember':
                return self._remember(text, category)
            elif action == 'recall':
                return self._recall(text)
            elif action == 'list':
                return self._list()
            elif action == 'forget':
                return self._forget(text)
            else:
                return f'Unknown action: {action}'
        except Exception as e:
            return f'Memory error: {e}'

    def _remember(self, text: str, category: str) -> str:
        if not text:
            return 'Nothing to remember.'
        items = _load()
        entry = {
            'id':       len(items) + 1,
            'text':     text,
            'category': category or 'fact',
            'created':  datetime.date.today().isoformat(),
        }
        items.append(entry)
        _save(items)
        return f"Got it, I'll remember: {text}"

    def _recall(self, query: str) -> str:
        if not query:
            return self._list()
        items = _load()
        q = query.lower()
        matches = [i for i in items if q in i.get('text', '').lower() or q in i.get('category', '').lower()]
        if not matches:
            return f'Nothing in memory matching "{query}".'
        lines = [f'Found {len(matches)} memory entries:']
        for m in matches:
            lines.append(f'  [{m.get("category","?")}] {m.get("text","")} (saved {m.get("created","")})')
        return '\n'.join(lines)

    def _list(self) -> str:
        items = _load()
        if not items:
            return "I don't have anything saved in memory yet."
        lines = [f'Memory ({len(items)} entries):']
        for m in items[-20:]:
            lines.append(f'  [{m.get("category","?")}] {m.get("text","")}')
        if len(items) > 20:
            lines.append(f'  ... and {len(items)-20} more.')
        return '\n'.join(lines)

    def _forget(self, text: str) -> str:
        if not text:
            return 'Specify what to forget.'
        items = _load()
        q = text.lower()
        before = len(items)
        items = [i for i in items if q not in i.get('text', '').lower()]
        if len(items) == before:
            return f'No memory entries matching "{text}".'
        _save(items)
        removed = before - len(items)
        return f'Removed {removed} memory {"entry" if removed == 1 else "entries"} matching "{text}".'
