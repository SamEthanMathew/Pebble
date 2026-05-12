"""Obsidian module — LLM-facing tool surface.

The actual vault operations live in `storage.vault.Vault`. This module is a
thin adapter that preserves the existing tool API (search/read/write/
append_daily/list_folder) and returns the same Markdown-shaped strings the
LLM has been getting.

Phase A: search/read/list_folder delegate to Vault.
Phase B will route write + append_daily through Vault's write chokepoint so
they pick up provenance stamping automatically.
"""

from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path

from .base import ActionTier, PebbleModule


# One Vault instance per vault path, shared across module re-instantiations
_VAULT_CACHE: dict[str, object] = {}
_VAULT_LOCK = threading.Lock()


def _path_inside(target, root) -> bool:
    """True iff resolved `target` equals or is contained in resolved `root`."""
    try:
        t = Path(target).resolve()
        r = Path(root).resolve()
        return t == r or t.is_relative_to(r)
    except Exception:
        return False


def _vault_for(path: str):
    """Return a cached Vault for `path`, creating one if needed.
    Returns None if the path isn't a directory (so callers can short-circuit
    with the legacy "vault not found" string).
    """
    if not path:
        return None
    key = str(Path(path).resolve())
    with _VAULT_LOCK:
        existing = _VAULT_CACHE.get(key)
        if existing is not None:
            return existing
        try:
            from storage import Vault
            v = Vault(key, autostart_watcher=True)
        except Exception:
            return None
        _VAULT_CACHE[key] = v
        return v


class ObsidianModule(PebbleModule):
    name         = 'obsidian'
    display_name = 'Obsidian'
    description  = 'Search, read, write, and manage notes in your Obsidian vault'
    icon         = '🟣'

    _default_tiers = {
        'search':       ActionTier.AUTO,
        'read':         ActionTier.AUTO,
        'list_folder':  ActionTier.AUTO,
        'write':        ActionTier.NOTIFY,
        'append_daily': ActionTier.NOTIFY,
    }

    config_fields = [
        {'key': 'vault_path', 'label': 'Vault folder path', 'type': 'path'},
    ]

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        self._vault_path = Path(cfg.get('vault_path', ''))

    def _safe_join(self, rel: str) -> Path | None:
        """Resolve `rel` against the vault and confirm it stays inside.
        Returns None when the path escapes the vault root."""
        try:
            root = self._vault_path.resolve()
            target = (self._vault_path / rel).resolve() if rel else root
        except Exception:
            return None
        try:
            if target == root or target.is_relative_to(root):
                return target
        except Exception:
            return None
        return None

    def is_ready(self) -> bool:
        return self._vault_path.is_dir()

    def tool_name(self) -> str:
        return 'obsidian'

    def tool_description(self) -> str:
        return (
            'Interact with your Obsidian vault. Supports: '
            'search (keyword search across all notes), '
            'read (read a specific note by path or fuzzy name match), '
            'write (create or overwrite a note), '
            "append_daily (append content to today's daily note, creating it if needed), "
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
        if action == 'read':
            return self._action_read(path)
        if action == 'write':
            return self._action_write(path, content, title)
        if action == 'append_daily':
            return self._action_append_daily(content)
        if action == 'list_folder':
            return self._action_list_folder(path)
        return f'Unknown action "{action}". Valid actions: search, read, write, append_daily, list_folder.'

    # ── actions (delegate to Vault when possible) ─────────────────────────────

    def _action_search(self, query: str) -> str:
        if not query.strip():
            return 'No query provided for search.'
        vault = _vault_for(str(self._vault_path))
        if vault is None:
            return f'No notes found matching "{query}".'
        hits = vault.search(query.strip(), k=4)
        if not hits:
            return f'No notes found matching "{query}".'
        parts = []
        for h in hits:
            title = h.note.title
            parts.append(f'**{title}**\n{h.excerpt}')
        return '\n\n---\n\n'.join(parts)

    def _action_read(self, path: str) -> str:
        if not path.strip():
            return 'No path provided for read.'
        vault = _vault_for(str(self._vault_path))
        if vault is None:
            return f'No note found matching "{path}".'

        # Reject any direct path that escapes the vault root.
        if self._safe_join(path) is None:
            return 'Path outside vault.'

        # Try id / path lookup first
        from storage import NoteNotFound
        try:
            note = vault.read(path)
            if self._safe_join(str(note.path)) is None and not _path_inside(note.path, self._vault_path):
                return 'Path outside vault.'
            return note.path.read_text(encoding='utf-8', errors='ignore')
        except NoteNotFound:
            pass

        # Fuzzy match by stem: find any note whose basename contains the query
        needle = path.lower().removesuffix('.md').strip()
        for n in vault.list():
            if needle in n.id.rsplit('/', 1)[-1].lower():
                if not _path_inside(n.path, self._vault_path):
                    continue
                return n.path.read_text(encoding='utf-8', errors='ignore')
        return f'No note found matching "{path}".'

    def _action_write(self, path: str, content: str, title: str) -> str:
        """LLM-initiated note write — the user told their LLM to write this,
        so the resulting note carries `source: user` (not pebble)."""
        if not content.strip():
            return 'No content provided for write.'
        vault = _vault_for(str(self._vault_path))
        if vault is None:
            return 'Obsidian vault not available.'

        if path.strip():
            rel = path.strip()
        elif title.strip():
            safe_title = re.sub(r'[<>:"/\\|?*]', '-', title.strip())
            rel = f'Pebble/{safe_title}.md'
        else:
            return 'Provide a path or title for the note to write.'

        if self._safe_join(rel) is None:
            return 'Path outside vault.'

        try:
            note = vault.create_note(
                rel, body=content, frontmatter={},
                source='user', trigger='llm_tool_write', confidence=1.0,
            )
            if not _path_inside(note.path, self._vault_path):
                return 'Path outside vault.'
            return f'Note saved to {note.path}'
        except Exception as e:
            return f'Error writing note: {e}'

    def _action_append_daily(self, content: str) -> str:
        """Append to today's Daily/YYYY-MM-DD.md via Vault, wrapped in a
        [!pebble]+ callout with provenance markers."""
        if not content.strip():
            return 'No content provided to append.'
        vault = _vault_for(str(self._vault_path))
        if vault is None:
            return 'Obsidian vault not available.'

        try:
            daily = vault.daily_note('today', create_if_missing=True)
            if not _path_inside(daily.path, self._vault_path):
                return 'Path outside vault.'
            note  = vault.append_block(
                daily.id, content,
                trigger='append_daily', confidence=1.0,
                label='pebble', title=date.today().isoformat(),
            )
            if not _path_inside(note.path, self._vault_path):
                return 'Path outside vault.'
            return f'Appended to daily note: {note.path}'
        except Exception as e:
            return f'Error updating daily note: {e}'

    def _action_list_folder(self, path: str) -> str:
        folder = self._safe_join(path)
        if folder is None:
            return 'Path outside vault.'
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
