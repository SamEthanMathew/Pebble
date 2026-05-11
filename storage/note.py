"""Note dataclass + parsing helpers.

A `Note` is the lazy, parsed representation of a single .md file in the vault.
Parsing uses python-frontmatter for the YAML block + raw text for the body.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import frontmatter  # python-frontmatter

from .provenance import (
    PebbleBlock,
    Source,
    effective_source,
    extract_pebble_blocks,
)


# ── Wikilink extraction ───────────────────────────────────────────────────────
# Pattern matches [[target]] or [[target|display]] anywhere in text.
# Captures the target only (drops the display text).
_WIKILINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')

# Inline tag: #thing-with-dashes, #nested/tag
_INLINE_TAG_RE = re.compile(r'(?<!\w)#([A-Za-z][\w/_-]*)')


def extract_wikilinks(body: str) -> list[str]:
    """Return wikilink targets in the order they appear, deduplicated."""
    seen: set[str] = set()
    out:  list[str] = []
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        # Strip a trailing display fragment that some parsers leave on
        target = target.split('#', 1)[0].strip()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def extract_inline_tags(body: str) -> list[str]:
    """Return inline #tags from the body (NOT frontmatter tags)."""
    seen: set[str] = set()
    out:  list[str] = []
    for m in _INLINE_TAG_RE.finditer(body):
        t = m.group(1)
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ── Note dataclass ────────────────────────────────────────────────────────────

@dataclass
class Note:
    """A parsed .md file from the vault.

    `id` is the relative path from the vault root, without the .md extension,
    using forward slashes (e.g. '07 - People/Amber Li'). This is what users
    type in wikilinks.
    """
    id:                     str
    path:                   Path
    frontmatter:            dict[str, Any]
    body:                   str
    wikilinks:              list[str]            = field(default_factory=list)
    tags:                   list[str]            = field(default_factory=list)
    mtime:                  datetime.datetime    = field(default_factory=lambda: datetime.datetime.now())
    source:                 Source               = 'user'
    source_edited_by_user:  datetime.datetime | None = None
    pebble_blocks:          list[PebbleBlock]    = field(default_factory=list)

    @property
    def title(self) -> str:
        """Display title — frontmatter `title:` if set, else basename of id."""
        t = self.frontmatter.get('title')
        if isinstance(t, str) and t.strip():
            return t.strip()
        return self.id.rsplit('/', 1)[-1]


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_note(path: Path, *, vault_root: Path) -> Note:
    """Parse a single .md file into a Note.

    Tolerates missing/invalid frontmatter (treats as empty + full body).
    """
    raw   = path.read_text(encoding='utf-8', errors='replace')
    try:
        post = frontmatter.loads(raw)
        fm   = dict(post.metadata or {})
        body = post.content or ''
    except Exception:
        fm, body = {}, raw

    # Tags: union of frontmatter `tags` (string or list) and inline #tags
    fm_tags = fm.get('tags', []) or []
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(',') if t.strip()]
    inline = extract_inline_tags(body)
    tags = list(dict.fromkeys([*map(str, fm_tags), *inline]))  # preserve order, dedupe

    rel = path.relative_to(vault_root).with_suffix('').as_posix()

    edited_raw = fm.get('source_edited_by_user')
    edited_dt: datetime.datetime | None = None
    if isinstance(edited_raw, str):
        try:
            edited_dt = datetime.datetime.fromisoformat(edited_raw)
        except ValueError:
            edited_dt = None

    return Note(
        id                    = rel,
        path                  = path,
        frontmatter           = fm,
        body                  = body,
        wikilinks             = extract_wikilinks(body),
        tags                  = tags,
        mtime                 = datetime.datetime.fromtimestamp(path.stat().st_mtime),
        source                = effective_source(fm),
        source_edited_by_user = edited_dt,
        pebble_blocks         = extract_pebble_blocks(body),
    )


def iter_md_files(root: Path, *, ignore: Iterable[str] = ('.obsidian', '.trash')) -> Iterable[Path]:
    """Yield .md file paths under root, skipping ignored top-level dirs."""
    ignore_set = {i.lower() for i in ignore}
    for p in root.rglob('*.md'):
        # Skip files inside ignored directories at any depth
        if any(part.lower() in ignore_set for part in p.relative_to(root).parts):
            continue
        yield p
