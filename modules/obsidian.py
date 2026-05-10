"""Obsidian module — search, read, write, and manage notes in a local vault."""

from __future__ import annotations
import re
from datetime import date
from pathlib import Path

from .base import PebbleModule


class ObsidianModule(PebbleModule):
    name         = 'obsidian'
    display_name = 'Obsidian'
    description  = 'Search, read, write, and manage notes in your Obsidian vault'
    icon         = '🟣'
    config_fields = [
        {'key': 'vault_path', 'label': 'Vault folder path', 'type': 'path'},
    ]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._vault = Path(cfg.get('vault_path', ''))

    def is_ready(self) -> bool:
        return self._vault.is_dir()

    def tool_name(self) -> str:
        return 'obsidian'

    def tool_description(self) -> str:
        return (
            'Interact with your Obsidian vault. Supports: '
            'search (keyword search across all notes), '
            'read (read a specific note by path or fuzzy name match), '
            'write (create or overwrite a note), '
            'append_daily (append content to today\'s daily note, creating it if needed), '
            'list_folder (list all notes in a given folder within the vault).'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['search', 'read', 'write', 'append_daily', 'list_folder'],
                    'description': 'Action to perform on the vault',
                },
                'query': {
                    'type': 'string',
                    'description': 'Keywords or phrase to search for across all notes (used with search)',
                },
                'path': {
                    'type': 'string',
                    'description': (
                        'Relative path within the vault, e.g. "Daily/2024-01-15.md" '
                        '(used with read, write, list_folder)'
                    ),
                },
                'content': {
                    'type': 'string',
                    'description': 'Content to write or append to a note (used with write, append_daily)',
                },
                'title': {
                    'type': 'string',
                    'description': 'Title for a new note (used with write when no path given)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = '', query: str = '', path: str = '',
                content: str = '', title: str = '', **_) -> str:
        if not self.is_ready():
            return 'Obsidian vault not found. Set the vault path in Settings → Modules.'

        if action == 'search':
            return self._action_search(query)
        elif action == 'read':
            return self._action_read(path)
        elif action == 'write':
            return self._action_write(path, content, title)
        elif action == 'append_daily':
            return self._action_append_daily(content)
        elif action == 'list_folder':
            return self._action_list_folder(path)
        else:
            return f'Unknown action "{action}". Valid actions: search, read, write, append_daily, list_folder.'

    # ── actions ────────────────────────────────────────────────────────────────

    def _action_search(self, query: str) -> str:
        if not query.strip():
            return 'No query provided for search.'
        results = self._search(query.strip())
        if not results:
            return f'No notes found matching "{query}".'
        parts = []
        for note_path, excerpt in results[:4]:
            title = Path(note_path).stem
            parts.append(f'**{title}**\n{excerpt}')
        return '\n\n---\n\n'.join(parts)

    def _action_read(self, path: str) -> str:
        if not path.strip():
            return 'No path provided for read.'
        # If it looks like a .md path, try direct read first
        if path.endswith('.md'):
            target = self._vault / path
            if target.exists():
                try:
                    return target.read_text(encoding='utf-8', errors='ignore')
                except Exception as e:
                    return f'Error reading note: {e}'
        # Fuzzy match: find a .md file whose stem contains path (case-insensitive)
        needle = path.lower().replace('.md', '')
        try:
            for md in self._vault.rglob('*.md'):
                if needle in md.stem.lower():
                    try:
                        return md.read_text(encoding='utf-8', errors='ignore')
                    except Exception as e:
                        return f'Error reading note: {e}'
        except Exception as e:
            return f'Error scanning vault: {e}'
        return f'No note found matching "{path}".'

    def _action_write(self, path: str, content: str, title: str) -> str:
        if not content.strip():
            return 'No content provided for write.'
        # Determine target path
        if path.strip():
            target = self._vault / path
            if not path.endswith('.md'):
                target = target.with_suffix('.md')
        elif title.strip():
            safe_title = re.sub(r'[<>:"/\\|?*]', '-', title.strip())
            target = self._vault / 'Pebble' / f'{safe_title}.md'
        else:
            return 'Provide a path or title for the note to write.'
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            return f'Note saved to {target}'
        except Exception as e:
            return f'Error writing note: {e}'

    def _action_append_daily(self, content: str) -> str:
        if not content.strip():
            return 'No content provided to append.'
        today = date.today().isoformat()  # YYYY-MM-DD
        daily_path = self._vault / 'Daily' / f'{today}.md'
        try:
            daily_path.parent.mkdir(parents=True, exist_ok=True)
            if daily_path.exists():
                existing = daily_path.read_text(encoding='utf-8', errors='ignore')
                new_text = existing.rstrip('\n') + '\n\n' + content.strip() + '\n'
            else:
                new_text = f'# {today}\n\n{content.strip()}\n'
            daily_path.write_text(new_text, encoding='utf-8')
            return f'Appended to daily note: {daily_path}'
        except Exception as e:
            return f'Error updating daily note: {e}'

    def _action_list_folder(self, path: str) -> str:
        folder = self._vault / path if path.strip() else self._vault
        if not folder.is_dir():
            return f'Folder not found: "{path}".'
        try:
            notes = sorted(folder.glob('*.md'))
            if not notes:
                return 'No notes found.'
            lines = [f'{i + 1}. {md.stem}' for i, md in enumerate(notes)]
            return '\n'.join(lines)
        except Exception as e:
            return f'Error listing folder: {e}'

    # ── internal helpers ───────────────────────────────────────────────────────

    def _search(self, query: str) -> list[tuple[str, str]]:
        terms = query.lower().split()
        hits  = []
        try:
            for md in self._vault.rglob('*.md'):
                try:
                    text  = md.read_text(encoding='utf-8', errors='ignore')
                    lower = text.lower()
                    score = sum(lower.count(t) for t in terms)
                    if score:
                        hits.append((str(md), self._excerpt(text, terms), score))
                except Exception:
                    continue
        except Exception:
            pass
        hits.sort(key=lambda x: x[2], reverse=True)
        return [(h[0], h[1]) for h in hits]

    def _excerpt(self, text: str, terms: list[str], window: int = 420) -> str:
        lower = text.lower()
        best_pos, best_score = 0, 0
        for i in range(0, len(text), 60):
            chunk = lower[i:i + window]
            score = sum(chunk.count(t) for t in terms)
            if score > best_score:
                best_score, best_pos = score, i
        snippet = text[best_pos:best_pos + window].strip()
        snippet = re.sub(r'!\[.*?\]\(.*?\)', '', snippet)
        snippet = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', snippet)
        snippet = re.sub(r'```.*?```', '[code block]', snippet, flags=re.S)
        return snippet.strip()
