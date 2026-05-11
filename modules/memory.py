"""Persistent memory module — vault-backed (Phase D rewrite).

Replaces the legacy substring + recency JSON memory with the Obsidian vault as
the source of truth. Public API (`remember/recall/list/forget`) preserved so
chat tools and planners keep working.

How each action maps to vault operations:

  remember(text, category):
    → Routes to a category-appropriate note. Pebble writes get
      `source: pebble` provenance. Categories:
        person     → 07 - People/<safe_text>.md          (create stub)
        place      → _pebble_imports/places.md            (append bullet)
        goal       → 09 - Goals/<safe_topic>.md           (create stub)
        preference → 50_preferences/<safe_topic>.md       (append bullet)
        fact|other → _pebble_imports/facts.md             (append bullet)

  recall(query):
    → vault.search(query) — substring + term-frequency, same scoring as the
      LLM tool surface. Notes tagged `#pebble-context-always` get a boost.

  list():
    → All notes carrying memory-style tags (preference, goal, fact, place)
      or in the `_pebble_imports/` folder.

  forget(pattern):
    → Searches for matching notes/lines. NEVER auto-deletes user content.
      Queues a ProposalQueue entry for the user to accept the deletion.

If a legacy `~/.pebble/memory.json` is still present, the module logs a
warning at first call telling the user to run the migration script.
"""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path
from typing import Any

from .base import PebbleModule, ActionTier


# ── Helpers ───────────────────────────────────────────────────────────────────

_LEGACY_MEMORY_PATH = Path.home() / '.pebble' / 'memory.json'
_DEFAULT_TOP_N      = 8


def _vault():
    """Return the active Vault instance, or None if Obsidian isn't configured."""
    try:
        import crab_config
        from storage import Vault
        cfg = crab_config.get_module_config('obsidian') or {}
        path = cfg.get('vault_path', '')
        if not path:
            return None
        return Vault(path, autostart_watcher=False)
    except Exception:
        return None


def _today() -> str:
    return datetime.date.today().isoformat()


def _safe_filename(s: str, *, max_len: int = 60) -> str:
    """Convert free-form text into a filesystem-safe basename."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '-', s)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return (cleaned[:max_len] or 'unsorted').rstrip(' .-')


# Topic derivation for preference/goal categories. Picks the first capitalized
# multi-word noun-phrase-ish chunk; falls back to first 4 words.
def _derive_topic(text: str) -> str:
    t = text.strip()
    if not t:
        return 'general'
    m = re.match(r'^(?:i\s+(?:prefer|like|love|hate|use|always|never)\s+)?(.+?)[.,;]?\s*$',
                  t, re.IGNORECASE)
    snippet = (m.group(1) if m else t).split()
    return _safe_filename(' '.join(snippet[:4]) or 'general')


_CATEGORY_ROUTING: dict[str, tuple[str, str]] = {
    # category → (folder, mode)  where mode is 'single_note' or 'aggregate'
    'person':     ('07 - People',          'single_note'),
    'place':      ('_pebble_imports',      'aggregate:places'),
    'goal':       ('09 - Goals',           'single_note'),
    'preference': ('50_preferences',       'single_note'),
    'fact':       ('_pebble_imports',      'aggregate:facts'),
    'other':      ('_pebble_imports/unsorted', 'single_note'),
}


def _warn_about_legacy_memory():
    """One-time warning if memory.json still exists (migration hasn't run)."""
    if _LEGACY_MEMORY_PATH.exists():
        print(
            f'[memory] Legacy memory.json still at {_LEGACY_MEMORY_PATH}. '
            f'Run `python migrate_memory_to_vault.py --apply` to migrate it to '
            f'the Obsidian vault. New writes go to the vault either way.',
            file=sys.stderr,
        )


# ── Public functional API (used by planners + chat) ──────────────────────────

def search(query: str, *, top_n: int = _DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Search vault notes for memory-style content. Returns a list of dicts
    in the same shape the legacy memory.search() used:

        [{'text': str, 'category': str, '_score': float, 'created': str}]
    """
    if not (query or '').strip():
        return []
    v = _vault()
    if v is None:
        return []
    try:
        hits = v.search(query, k=top_n)
        out = []
        for h in hits:
            # Boost #pebble-context-always notes
            score = h.score
            if 'pebble-context-always' in h.note.tags:
                score *= 1.5
            out.append({
                'text':     h.excerpt or h.note.title,
                'category': _infer_category(h.note),
                '_score':   round(score, 3),
                'created':  h.note.frontmatter.get('source_date', '')[:10]
                            or h.note.mtime.strftime('%Y-%m-%d'),
                'note_id':  h.note.id,
            })
        out.sort(key=lambda d: d['_score'], reverse=True)
        return out
    finally:
        v.stop()


def _infer_category(note) -> str:
    """Guess the memory category from a note's tags / folder."""
    tags = set(note.tags)
    if 'person' in tags or 'people' in tags:
        return 'person'
    if 'place' in tags:
        return 'place'
    if 'goal' in tags:
        return 'goal'
    if 'preference' in tags:
        return 'preference'
    if note.id.startswith('07 - People/'):
        return 'person'
    if note.id.startswith('09 - Goals/'):
        return 'goal'
    if note.id.startswith('50_preferences/'):
        return 'preference'
    return 'fact'


# ── PebbleModule wrapper ──────────────────────────────────────────────────────

class MemoryModule(PebbleModule):
    name         = 'memory'
    display_name = 'Pebble Memory'
    description  = 'Remember and recall facts, preferences, and context across conversations'
    icon         = '🧠'
    config_fields: list[dict] = []

    _default_tiers = {
        'remember': ActionTier.NOTIFY,
        'recall':   ActionTier.AUTO,
        'list':     ActionTier.AUTO,
        'forget':   ActionTier.ASK,   # tighter — never silently delete user content
    }

    def is_ready(self) -> bool:
        _warn_about_legacy_memory()
        return True

    def tool_name(self) -> str:
        return 'memory'

    def tool_description(self) -> str:
        return ('Store and retrieve persistent facts about the user, preferences, '
                'context, and anything Pebble should remember across sessions. '
                'Backed by your Obsidian vault. Use action="remember" to save, '
                '"recall" to search, "list" to show recent memory notes, '
                '"forget" to queue a removal proposal.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['remember', 'recall', 'list', 'forget'],
                },
                'text':     {'type': 'string',
                              'description': 'For remember: the fact. For recall/forget: the query.'},
                'category': {'type': 'string',
                              'description': 'person | place | goal | preference | fact | other'},
                'top_n':    {'type': 'integer', 'description': 'recall: max results (default 8)'},
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'list', text: str = '', category: str = 'fact',
                top_n: int = _DEFAULT_TOP_N, **_) -> str:
        try:
            if action == 'remember':
                return self._remember(text, category)
            if action == 'recall':
                return self._recall(text, top_n=top_n)
            if action == 'list':
                return self._list()
            if action == 'forget':
                return self._forget(text)
            return f'Unknown action: {action}'
        except Exception as e:
            return f'Memory error: {e}'

    # ── action implementations ────────────────────────────────────────────────

    def _remember(self, text: str, category: str) -> str:
        text = (text or '').strip()
        if not text:
            return 'Nothing to remember.'
        v = _vault()
        if v is None:
            return ("Memory write failed: Obsidian vault not configured. "
                    "Run `/connect obsidian <path>` first.")
        try:
            cat = (category or 'fact').lower()
            folder, mode = _CATEGORY_ROUTING.get(cat, _CATEGORY_ROUTING['other'])
            now_iso = datetime.datetime.now().astimezone().isoformat(timespec='seconds')

            if mode == 'single_note':
                # One note per remembered thing. For people, name = subject.
                if cat == 'person':
                    # Try to extract a name from the front of the text
                    subj_m = re.match(r'^([A-Z][\w-]+(?:\s+[A-Z][\w-]+)*)', text)
                    subject = subj_m.group(1) if subj_m else text.split('.', 1)[0][:60]
                else:
                    subject = _derive_topic(text)
                rel = f'{folder}/{_safe_filename(subject)}.md'
                tags = self._tags_for_category(cat)
                body = f'# {subject}\n\n- {text}  _(remembered {now_iso[:10]})_\n'
                # If note already exists, append to it
                from storage import NoteNotFound
                try:
                    existing = v.read(rel)
                    v.append_block(
                        existing.id, text,
                        trigger='user_remember_command', confidence=1.0,
                        label='memory', title=f'Added {now_iso[:10]}',
                    )
                    return f"Got it — appended to {existing.id}."
                except NoteNotFound:
                    note = v.create_note(
                        rel, body=body,
                        frontmatter={'tags': tags, 'category': cat},
                        trigger='user_remember_command', confidence=1.0,
                        note=f'remember: "{text[:100]}"',
                    )
                    return f"Got it — saved to {note.id}."

            # aggregate mode: one note holds many bullets
            agg_name = mode.split(':', 1)[1]
            rel = f'{folder}/{agg_name}.md'
            from storage import NoteNotFound
            try:
                existing = v.read(rel)
                # Append a [!memory]+ block carrying the new bullet
                v.append_block(
                    existing.id,
                    text,
                    trigger='user_remember_command', confidence=1.0,
                    label='memory', title=f'{cat} · {now_iso[:10]}',
                )
                return f"Got it — appended to {existing.id}."
            except NoteNotFound:
                body = (f'# {agg_name.capitalize()}\n\n'
                        f'_Collected memory entries from Pebble._\n\n'
                        f'- {text}  _(remembered {now_iso[:10]})_\n')
                tags = self._tags_for_category(cat)
                note = v.create_note(
                    rel, body=body,
                    frontmatter={'tags': tags},
                    trigger='user_remember_command', confidence=1.0,
                    note='aggregate memory file',
                )
                return f"Got it — created {note.id} and saved it there."
        finally:
            v.stop()

    def _recall(self, query: str, *, top_n: int) -> str:
        query = (query or '').strip()
        if not query:
            return self._list()
        hits = search(query, top_n=top_n)
        if not hits:
            return f'Nothing in memory matching "{query}".'
        lines = [f'Top {len(hits)} relevant memor{"y" if len(hits) == 1 else "ies"} for "{query}":']
        for h in hits:
            lines.append(
                f'  [{h["category"]}] {h["text"][:160]}'
                f' (score {h["_score"]:.2f}, {h["created"]}, in `{h["note_id"]}`)'
            )
        return '\n'.join(lines)

    def _list(self) -> str:
        v = _vault()
        if v is None:
            return ("No vault configured. Run `/connect obsidian <path>` "
                    "to set up vault-backed memory.")
        try:
            notes = []
            for n in v.list():
                if any(t in n.tags for t in ('preference', 'goal', 'fact', 'place', 'memory')):
                    notes.append(n)
                elif n.id.startswith('50_preferences/') or n.id.startswith('_pebble_imports/'):
                    notes.append(n)
            if not notes:
                return ("Memory is empty. Use `remember <text>` (or just tell me "
                        "to remember something in chat) to start.")
            notes.sort(key=lambda n: n.mtime, reverse=True)
            lines = [f'Memory ({len(notes)} notes, most-recent first):']
            for n in notes[:30]:
                cat = _infer_category(n)
                preview = (n.body.split('\n', 1)[0] or n.title)[:80]
                lines.append(f'  [{cat}] {n.title} — {preview}')
            if len(notes) > 30:
                lines.append(f'  … and {len(notes) - 30} more.')
            return '\n'.join(lines)
        finally:
            v.stop()

    def _forget(self, text: str) -> str:
        """Forget is destructive — never auto-delete user content. Queue a proposal."""
        text = (text or '').strip()
        if not text:
            return 'Specify what to forget.'
        v = _vault()
        if v is None:
            return 'No vault configured.'
        try:
            hits = v.search(text, k=5)
            if not hits:
                return f'Nothing in memory matching "{text}".'
            from storage import ProposalQueue
            q = ProposalQueue()
            queued = []
            for h in hits:
                pid = q.add({
                    'kind':    'forget',
                    'note_id': h.note.id,
                    'note':    f'Pebble proposed forgetting matches for "{text}"',
                    'query':   text,
                    'excerpt': h.excerpt,
                })
                queued.append((pid, h.note.id))
            lines = [
                f'Queued {len(queued)} forget proposal{"s" if len(queued) != 1 else ""} '
                f'for "{text}":'
            ]
            for pid, nid in queued:
                lines.append(f'  - {nid}  (proposal `{pid}`)')
            lines.append('')
            lines.append('Review with `/proposals` and accept the ones you want removed.')
            return '\n'.join(lines)
        finally:
            v.stop()

    # ── small helpers ─────────────────────────────────────────────────────────

    def _tags_for_category(self, cat: str) -> list[str]:
        return {
            'person':     ['people', 'memory'],
            'place':      ['place', 'memory'],
            'goal':       ['goal',  'memory'],
            'preference': ['preference', 'memory'],
            'fact':       ['fact', 'memory'],
            'other':      ['memory'],
        }.get(cat, ['memory'])
