"""Vault — facade over an Obsidian markdown vault.

Phase A: read surface (`read`, `list`, `search`, `find_by_frontmatter`,
`daily_note`, `recent_daily_notes`).

Phase B: write surface (`create_note`, `append_block`, `promote_note`,
`propose_edit`) through a single chokepoint that:
  1. validates path is under vault_root
  2. parses old content + frontmatter
  3. enforces `assert_preserves_provenance` (refuses to strip pebble markers)
  4. writes atomically (write-tmp + os.replace, with Windows retry)
  5. appends a row to `~/.pebble/workspace/write_log.jsonl`
  6. updates the in-memory cache
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import frontmatter as fm_lib  # python-frontmatter

from .note import Note, parse_note, iter_md_files
from .provenance import (
    PebbleBlock,
    ProvenanceViolation,
    assert_preserves_provenance,
    effective_source,
    extract_pebble_blocks,
    is_user_authored,
    mark_user_edited,
    promote_to_user,
    stamp_block,
    stamp_frontmatter,
)


# Provenance filters that callers can request.
Provenance = Literal['all', 'user_only', 'pebble_only', 'mixed_ok']


class NoteNotFound(LookupError):
    """A requested note id / path does not resolve to a vault file."""


@dataclass
class Hit:
    """One search result. `excerpt` is the snippet of body around the match;
    `score` is the term-frequency score used for ranking.
    """
    note:    Note
    score:   float
    excerpt: str


class Vault:
    """The vault facade. Construct once per Pebble process."""

    DEFAULT_IGNORE_DIRS = ('.obsidian', '.trash')

    def __init__(
        self,
        vault_path: Path | str,
        *,
        ignore_dirs: Iterable[str] = DEFAULT_IGNORE_DIRS,
        autostart_watcher: bool = True,
    ):
        self.root         = Path(vault_path).resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise FileNotFoundError(f'Vault path is not a directory: {self.root}')
        self._ignore      = tuple(ignore_dirs)
        self._by_id:      dict[str, Note] = {}
        self._by_path:    dict[Path, Note] = {}
        self._mtimes:     dict[Path, float] = {}
        self._lock        = threading.RLock()
        self._observer    = None  # watchdog Observer (set in _start_watcher)
        self._loaded      = False
        # path → most-recent self-write timestamp (epoch). Used to suppress
        # mark_edited_by_user on the watchdog event that fires from our own writes.
        self._recent_writes: dict[Path, float] = {}
        self._self_write_suppress_seconds = 5.0
        if autostart_watcher:
            self._ensure_loaded()
            self._start_watcher()

    # ── Cache priming ────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            for p in iter_md_files(self.root, ignore=self._ignore):
                try:
                    self._index_path(p)
                except Exception:
                    # Tolerate one bad file rather than abandoning the whole cache
                    continue
            self._loaded = True

    def _index_path(self, p: Path) -> Note:
        """Parse and index a single path. Returns the new Note. Caller holds lock."""
        note = parse_note(p, vault_root=self.root)
        self._by_id[note.id]     = note
        self._by_path[note.path] = note
        self._mtimes[note.path]  = p.stat().st_mtime
        return note

    def _refresh_if_stale(self, p: Path) -> Note | None:
        """Re-parse `p` if its on-disk mtime is newer than what we have cached.
        Returns the (possibly refreshed) Note, or None if the file no longer exists.
        """
        if not p.exists():
            with self._lock:
                old = self._by_path.pop(p, None)
                if old:
                    self._by_id.pop(old.id, None)
                self._mtimes.pop(p, None)
            return None
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return None
        with self._lock:
            cached_mtime = self._mtimes.get(p, 0.0)
            if mtime > cached_mtime + 1e-6:
                return self._index_path(p)
            return self._by_path.get(p)

    # ── Watchdog ─────────────────────────────────────────────────────────────

    def _start_watcher(self) -> None:
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            return  # watchdog optional

        outer = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                try:
                    p = Path(event.src_path).resolve()
                except Exception:
                    return
                if p.suffix.lower() != '.md':
                    return
                # Skip ignored dirs
                try:
                    rel_parts = p.relative_to(outer.root).parts
                except ValueError:
                    return
                if any(part.lower() in {i.lower() for i in outer._ignore} for part in rel_parts):
                    return

                # If this event corresponds to a recent self-write, suppress
                # the "user edited a Pebble note" detection — otherwise we'd
                # mark our own writes as user edits and loop.
                now = time.time()
                last_self = outer._recent_writes.get(p)
                is_self_write = (
                    last_self is not None
                    and (now - last_self) < outer._self_write_suppress_seconds
                )

                refreshed = outer._refresh_if_stale(p)
                if refreshed is None:
                    return

                # If the refreshed note is source: pebble and the event wasn't
                # from us, flag it as user-edited.
                if not is_self_write and refreshed.frontmatter.get('source') == 'pebble':
                    if 'source_edited_by_user' not in refreshed.frontmatter:
                        try:
                            outer.mark_edited_by_user(p)
                        except Exception:
                            pass

        observer = Observer()
        observer.schedule(_Handler(), str(self.root), recursive=True)
        observer.daemon = True
        try:
            observer.start()
        except Exception:
            return  # watcher is optional; cache still works via _refresh_if_stale on reads
        self._observer = observer

    def stop(self) -> None:
        """Stop the watchdog observer. Safe to call multiple times."""
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=2)
            except Exception:
                pass
            self._observer = None

    # ── Provenance filter ────────────────────────────────────────────────────

    @staticmethod
    def _matches_provenance(note: Note, provenance: Provenance) -> bool:
        if provenance == 'all':
            return True
        if provenance == 'user_only':
            return is_user_authored(note.frontmatter)
        if provenance == 'mixed_ok':
            return is_user_authored(note.frontmatter) or note.source == 'mixed'
        if provenance == 'pebble_only':
            return note.source in ('pebble', 'mixed')
        return True  # unknown filter — be permissive rather than throw

    # ── Read API ─────────────────────────────────────────────────────────────

    def read(self, note_id_or_path: str) -> Note:
        """Resolve a note by id ('07 - People/Amber Li'), relative path
        ('07 - People/Amber Li.md'), or absolute path.

        Raises NoteNotFound if no match.
        """
        self._ensure_loaded()
        s = note_id_or_path.strip()

        # Try absolute path
        candidate = Path(s)
        if candidate.is_absolute() and candidate.suffix.lower() == '.md':
            try:
                resolved = candidate.resolve()
                refreshed = self._refresh_if_stale(resolved)
                if refreshed:
                    return refreshed
            except Exception:
                pass

        # Try id (no extension)
        clean = s.removesuffix('.md').strip().replace('\\', '/')
        with self._lock:
            note = self._by_id.get(clean)
        if note:
            return self._refresh_if_stale(note.path) or note

        # Try relative path under vault
        rel = self.root / s
        if not rel.suffix:
            rel = rel.with_suffix('.md')
        if rel.exists():
            refreshed = self._refresh_if_stale(rel)
            if refreshed:
                return refreshed

        raise NoteNotFound(f'No vault note matches {note_id_or_path!r}')

    def list(
        self,
        glob: str = '**/*.md',
        *,
        provenance: Provenance = 'all',
    ) -> list[Note]:
        """List notes whose path matches `glob` (relative to vault root)."""
        self._ensure_loaded()
        out: list[Note] = []
        with self._lock:
            notes = list(self._by_id.values())
        for n in notes:
            try:
                rel = n.path.relative_to(self.root)
            except ValueError:
                continue
            if glob == '**/*.md' or rel.match(glob):
                fresh = self._refresh_if_stale(n.path)
                if fresh and self._matches_provenance(fresh, provenance):
                    out.append(fresh)
        return out

    def find_by_frontmatter(
        self,
        *,
        provenance: Provenance = 'all',
        **filters: Any,
    ) -> list[Note]:
        """Return notes whose frontmatter matches ALL given key/value filters.

        Tag matching is special-cased: passing tag='people' matches if 'people'
        is in the note's tags list. Other keys do exact equality.
        """
        self._ensure_loaded()
        out: list[Note] = []
        with self._lock:
            notes = list(self._by_id.values())
        tag_filter = filters.pop('tag', None)
        for n in notes:
            ok = True
            for key, want in filters.items():
                if n.frontmatter.get(key) != want:
                    ok = False
                    break
            if ok and tag_filter is not None and tag_filter not in n.tags:
                ok = False
            if ok and self._matches_provenance(n, provenance):
                out.append(n)
        return out

    def search(
        self,
        query: str,
        *,
        k: int = 10,
        provenance: Provenance = 'all',
    ) -> list[Hit]:
        """Substring + term-frequency search across body text.

        This is the same scoring as the legacy ObsidianModule._search but
        returns structured Hit objects so callers can render however they want.
        """
        if not query.strip():
            return []
        self._ensure_loaded()
        terms = [t for t in query.lower().split() if t]
        hits: list[Hit] = []
        with self._lock:
            notes = list(self._by_id.values())
        for n in notes:
            if not self._matches_provenance(n, provenance):
                continue
            lower = n.body.lower()
            score = sum(lower.count(t) for t in terms)
            if score == 0:
                continue
            hits.append(Hit(note=n, score=float(score), excerpt=_excerpt(n.body, terms)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    # ── Daily notes (canonical location: Daily/) ─────────────────────────────

    DAILY_FOLDER = 'Daily'

    def daily_note(
        self,
        date: datetime.date | str = 'today',
        *,
        create_if_missing: bool = False,
    ) -> Note:
        """Return the Daily/YYYY-MM-DD.md note for the given date.

        Phase A is read-only: passing create_if_missing=True still raises
        NoteNotFound (Phase B wires the write path).
        """
        if isinstance(date, str):
            if date.lower() == 'today':
                d = datetime.date.today()
            else:
                d = datetime.date.fromisoformat(date)
        else:
            d = date
        rel = f'{self.DAILY_FOLDER}/{d.isoformat()}.md'
        try:
            return self.read(rel)
        except NoteNotFound:
            if not create_if_missing:
                raise
            # Phase B: create a minimal scaffolded daily note (source: pebble)
            return self.create_note(
                rel,
                body=f'# {d.isoformat()}\n\n',
                frontmatter={'date': d.isoformat(), 'tags': ['daily']},
                trigger='daily_note_scaffold',
                confidence=1.0,
            )

    def recent_daily_notes(self, days: int = 7) -> list[Note]:
        """Most recent daily notes (newest first), up to `days` entries.

        Skips dates without a note; returns whatever Daily/ files actually exist.
        """
        self._ensure_loaded()
        out: list[Note] = []
        with self._lock:
            for nid, n in self._by_id.items():
                if not nid.startswith(self.DAILY_FOLDER + '/'):
                    continue
                stem = nid.rsplit('/', 1)[-1]
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', stem):
                    out.append(n)
        out.sort(key=lambda n: n.id, reverse=True)
        return out[:days]

    # ── Write surface (Phase B) ──────────────────────────────────────────────

    @property
    def _workspace_dir(self) -> Path:
        """~/.pebble/workspace/ — operational state for the storage layer."""
        d = Path.home() / '.pebble' / 'workspace'
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def _write_log_path(self) -> Path:
        return self._workspace_dir / 'write_log.jsonl'

    def _resolve_target(self, note_id_or_path: str) -> Path:
        """Convert an id or relative path into an absolute Path under the vault.

        Raises ValueError if the resolved path escapes vault_root.
        """
        s = note_id_or_path.strip().replace('\\', '/')
        if not s:
            raise ValueError('Empty note id / path')
        if s.startswith('/') or (len(s) > 2 and s[1] == ':'):  # absolute
            target = Path(s).resolve()
        else:
            if not s.endswith('.md'):
                s = s + '.md'
            target = (self.root / s).resolve()
        # Enforce containment
        try:
            target.relative_to(self.root)
        except ValueError as e:
            raise ValueError(
                f'Target path escapes vault root: {target} (vault: {self.root})'
            ) from e
        return target

    def _serialize_note(self, frontmatter: dict[str, Any], body: str) -> str:
        """Serialize frontmatter + body to a .md file content string.
        Uses python-frontmatter so YAML rendering is consistent.
        """
        post = fm_lib.Post(body or '', **frontmatter)
        return fm_lib.dumps(post)

    def _atomic_write(self, target: Path, content: str) -> None:
        """Windows-safe atomic write: write to tmp + os.replace with retry."""
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=target.name + '.', suffix='.tmp', dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
                f.write(content)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            delay = 0.02
            for attempt in range(5):
                try:
                    os.replace(tmp_path, target)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(delay)
                    delay *= 2
            # Record self-write so the watchdog suppresses mark_edited_by_user
            # for events fired by our own writes.
            self._recent_writes[target] = time.time()
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _log_write(self, action: str, target: Path, *,
                   trigger: str = '', confidence: float | None = None,
                   extra: dict[str, Any] | None = None) -> None:
        """Append a row to write_log.jsonl. Failures are non-fatal."""
        try:
            row = {
                'timestamp':  datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                'action':     action,
                'path':       str(target),
                'rel':        target.relative_to(self.root).as_posix() if target.is_absolute() else str(target),
                'trigger':    trigger,
                'confidence': confidence,
            }
            if extra:
                row.update(extra)
            with self._write_log_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(row, default=str, ensure_ascii=False))
                f.write('\n')
        except Exception as e:
            print(f'[vault] write_log append failed: {e}', file=sys.stderr)

    def _write_file(
        self,
        target: Path,
        *,
        new_frontmatter: dict[str, Any],
        new_body: str,
        action_label: str,
        trigger: str,
        confidence: float,
    ) -> Note:
        """The single write chokepoint. ALL writes go through here.

        Reads any existing file at `target`, runs the provenance invariant
        check (refuses to strip pebble markers), writes atomically, logs,
        and updates the cache.
        """
        # Defense in depth — target must still be under vault root
        try:
            target.resolve().relative_to(self.root)
        except ValueError as e:
            raise ValueError(
                f'Refusing to write outside vault: {target}'
            ) from e

        # Read existing content if any
        old_frontmatter: dict[str, Any] | None = None
        old_body:        str | None = None
        if target.exists():
            try:
                raw  = target.read_text(encoding='utf-8', errors='replace')
                post = fm_lib.loads(raw)
                old_frontmatter = dict(post.metadata or {})
                old_body        = post.content or ''
            except Exception:
                old_frontmatter = {}
                old_body = target.read_text(encoding='utf-8', errors='replace')

        # Invariant: never strip provenance markers from existing content
        assert_preserves_provenance(
            old_frontmatter=old_frontmatter,
            new_frontmatter=new_frontmatter,
            old_body=old_body,
            new_body=new_body,
        )

        content = self._serialize_note(new_frontmatter, new_body)
        self._atomic_write(target, content)

        self._log_write(
            action_label, target,
            trigger=trigger, confidence=confidence,
            extra={'effective_source': effective_source(new_frontmatter)},
        )

        # Update cache from the now-persisted file
        with self._lock:
            note = self._index_path(target)
        return note

    def create_note(
        self,
        path: str,
        body: str,
        frontmatter: dict[str, Any] | None = None,
        *,
        trigger: str = 'unspecified',
        confidence: float = 0.5,
        source: Literal['pebble', 'user'] = 'pebble',
        note: str = '',
    ) -> Note:
        """Create (or update) a note. Default is `source: pebble` (Pebble-authored).

        Pass `source='user'` for the rare case where Pebble is writing on the
        user's behalf via an LLM-tool call (preserves the legacy behavior of
        ObsidianModule._action_write).

        If the target already has `source: pebble` frontmatter, the existing
        provenance is preserved (stamp_frontmatter is a no-op on the source
        field when it's already there). If the target has `source: user`,
        the write is refused via the invariant check.
        """
        target = self._resolve_target(path)
        body   = body or ''
        fm     = dict(frontmatter or {})

        if source == 'pebble':
            fm = stamp_frontmatter(fm, trigger=trigger,
                                    confidence=confidence, note=note)
        else:
            # source: user — caller explicitly wants user-authored
            fm.setdefault('source', 'user')

        return self._write_file(
            target,
            new_frontmatter=fm,
            new_body=body,
            action_label='create_note',
            trigger=trigger,
            confidence=confidence,
        )

    def append_block(
        self,
        note_id_or_path: str,
        content: str,
        *,
        trigger: str,
        confidence: float,
        label: str = 'pebble',
        title: str = '',
    ) -> Note:
        """Append a [!label]+ callout block (with provenance comment) to a note.

        If the note doesn't exist, raises NoteNotFound (callers should pair
        with create_note or daily_note(create_if_missing=True) first).
        """
        target = self._resolve_target(note_id_or_path)
        if not target.exists():
            raise NoteNotFound(f'Cannot append to missing note: {target}')

        # Read existing
        raw  = target.read_text(encoding='utf-8', errors='replace')
        post = fm_lib.loads(raw)
        old_fm  = dict(post.metadata or {})
        old_body = post.content or ''

        # If user has edited a Pebble-authored note, flag it as mixed FIRST
        # (separate write — keeps invariant checks straightforward)
        new_fm = dict(old_fm)
        if effective_source(old_fm) == 'pebble':
            # No user edit detected here; but the source: pebble marker is
            # preserved by mark_user_edited being a no-op when not needed.
            pass

        block = stamp_block(content, trigger=trigger, confidence=confidence,
                            label=label, title=title)
        new_body = old_body.rstrip() + ('\n\n' if old_body.strip() else '') + block + '\n'

        return self._write_file(
            target,
            new_frontmatter=new_fm,
            new_body=new_body,
            action_label='append_block',
            trigger=trigger,
            confidence=confidence,
        )

    def promote_note(self, note_id_or_path: str) -> Note:
        """Flip a `source: pebble` note to `source: user` via promote_to_user.
        Idempotent: a note already at `source: user` is returned unchanged.
        """
        target = self._resolve_target(note_id_or_path)
        if not target.exists():
            raise NoteNotFound(f'Cannot promote missing note: {target}')

        raw  = target.read_text(encoding='utf-8', errors='replace')
        post = fm_lib.loads(raw)
        old_fm   = dict(post.metadata or {})
        old_body = post.content or ''

        if effective_source(old_fm) == 'user':
            with self._lock:
                return self._refresh_if_stale(target) or self._index_path(target)

        new_fm = promote_to_user(old_fm)
        return self._write_file(
            target,
            new_frontmatter=new_fm, new_body=old_body,
            action_label='promote_note',
            trigger='promote', confidence=1.0,
        )

    def mark_edited_by_user(self, note_id_or_path: str) -> Note | None:
        """Mark a `source: pebble` note as user-edited (mixed provenance).

        Called by the watchdog event handler when a Pebble note changes on
        disk for a reason other than a recent Vault.write. No-op for non-pebble
        notes. Returns the updated Note, or None if the file is gone.
        """
        try:
            target = self._resolve_target(note_id_or_path)
        except ValueError:
            return None
        if not target.exists():
            return None
        raw  = target.read_text(encoding='utf-8', errors='replace')
        try:
            post = fm_lib.loads(raw)
            old_fm   = dict(post.metadata or {})
            old_body = post.content or ''
        except Exception:
            return None

        if old_fm.get('source') != 'pebble':
            return None
        if 'source_edited_by_user' in old_fm:
            return self._refresh_if_stale(target)

        new_fm = mark_user_edited(old_fm)
        return self._write_file(
            target,
            new_frontmatter=new_fm, new_body=old_body,
            action_label='mark_edited_by_user',
            trigger='user_edit_detected', confidence=1.0,
        )

    def propose_edit(self, note_id_or_path: str, edit: dict[str, Any]) -> str:
        """Queue a proposed edit to a user-authored note.

        Returns a proposal ID. The edit is NOT applied — `proposal_queue.accept`
        is the only path to apply it. This is the spec §2.5 contract: Pebble
        never silently rewrites user-authored content.
        """
        from .proposal_queue import ProposalQueue
        queue = ProposalQueue(self._workspace_dir / 'proposals.jsonl')
        return queue.add({
            'kind':      'edit',
            'note_id':   note_id_or_path,
            'edit':      edit,
            'created_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        })

    # ── Stats / introspection ────────────────────────────────────────────────

    def note_count(self) -> int:
        self._ensure_loaded()
        with self._lock:
            return len(self._by_id)


# ── Internal: excerpt generation (preserves legacy obsidian module behavior) ──

def _excerpt(text: str, terms: list[str], window: int = 420) -> str:
    """Find the window-sized chunk with the most term hits; clean up images,
    wikilink display text, and code blocks.
    """
    lower = text.lower()
    best_pos, best_score = 0, 0
    for i in range(0, len(text), 60):
        chunk = lower[i:i + window]
        score = sum(chunk.count(t) for t in terms)
        if score > best_score:
            best_score, best_pos = score, i
    snippet = text[best_pos:best_pos + window].strip()
    # Drop image embeds
    snippet = re.sub(r'!\[.*?\]\(.*?\)', '', snippet)
    # Flatten wikilinks to their target (drop display text)
    snippet = re.sub(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', r'\1', snippet)
    # Replace code blocks with marker so they don't dominate the snippet
    snippet = re.sub(r'```.*?```', '[code block]', snippet, flags=re.S)
    return snippet.strip()
