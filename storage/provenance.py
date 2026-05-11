"""Provenance marker helpers — the only place we construct or parse the
`source: pebble` frontmatter format and the `[!pebble]+ ... <!-- pebble:... -->`
block format.

Per the storage plan §2.4 (the one hard rule):
    Pebble must never strip, overwrite, or modify provenance markers it didn't
    just create.

These helpers are pure: no file I/O. The Vault enforces the invariant; this
module just defines the shapes.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any, Literal


# The four canonical source values we use:
#   - "user"        ─ authored by the user (default when no source: field present)
#   - "pebble"      ─ authored by Pebble; user has not edited it
#   - "mixed"       ─ Pebble-authored but user has since edited
#                     (frontmatter still says source: pebble + source_edited_by_user)
Source = Literal['user', 'pebble', 'mixed']

CALLOUT_LABEL = 'pebble'  # > [!pebble]+ ...
COMMENT_PREFIX = '<!-- pebble:'
COMMENT_SUFFIX = '-->'


# ── Datetime helpers ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Local-aware ISO string with offset, e.g. 2026-05-11T14:32:00-04:00."""
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


# ── Frontmatter (whole-note level) ────────────────────────────────────────────

def stamp_frontmatter(
    fm: dict[str, Any],
    *,
    trigger: str,
    confidence: float,
    note: str = '',
) -> dict[str, Any]:
    """Stamp a frontmatter dict for a Pebble-created note.

    If the dict already has a `source:` field, that field is preserved
    (never overwritten). Returns a NEW dict (does not mutate the input).
    """
    out = dict(fm)
    # Preserve any existing source — we never overwrite
    if 'source' in out:
        return out
    out['source']            = 'pebble'
    out['source_date']       = _now_iso()
    out['source_trigger']    = trigger
    out['source_confidence'] = round(float(confidence), 3)
    if note:
        out['source_note'] = note
    return out


def mark_user_edited(fm: dict[str, Any]) -> dict[str, Any]:
    """Add `source_edited_by_user: <now>` to a Pebble-authored note's frontmatter.
    No-op if there's no `source: pebble` to mark, or if the marker is already there.
    Returns a NEW dict.
    """
    out = dict(fm)
    if out.get('source') != 'pebble':
        return out
    if 'source_edited_by_user' in out:
        return out
    out['source_edited_by_user'] = _now_iso()
    return out


def promote_to_user(fm: dict[str, Any]) -> dict[str, Any]:
    """User explicitly promotes a Pebble note to user-authored.
    Sets source: user, records promoted_from_pebble + promoted_at.
    """
    out = dict(fm)
    out['source']               = 'user'
    out['promoted_from_pebble'] = True
    out['promoted_at']          = _now_iso()
    return out


def effective_source(fm: dict[str, Any]) -> Source:
    """Resolve the effective provenance for a parsed frontmatter dict.

    - source: user                           → 'user'
    - no source field                        → 'user' (default)
    - source: pebble + source_edited_by_user → 'mixed'
    - source: pebble                         → 'pebble'
    - anything else (unexpected value)       → 'user' (safe default)
    """
    raw = fm.get('source')
    if raw == 'pebble':
        return 'mixed' if 'source_edited_by_user' in fm else 'pebble'
    return 'user'


def is_user_authored(fm: dict[str, Any]) -> bool:
    """True if the note can be treated as user content for thinking-pass filters.

    user_only filter:  is_user_authored == True
    mixed_ok filter:   is_user_authored == True OR effective_source == 'mixed'
    """
    return effective_source(fm) == 'user'


def has_provenance_markers(fm: dict[str, Any]) -> bool:
    """True if the frontmatter contains any source_* markers Pebble owns."""
    return any(
        k in fm
        for k in ('source', 'source_date', 'source_trigger', 'source_confidence',
                  'source_note', 'source_edited_by_user',
                  'promoted_from_pebble', 'promoted_at')
    )


# ── Callout blocks (appended into user notes) ────────────────────────────────

@dataclass
class PebbleBlock:
    """One [!pebble]+ callout parsed from a note body."""
    label:      str                  # the post-! tag, e.g. 'pebble' or 'grocery'
    title:      str                  # the text after the type tag on the callout's first line
    body:       str                  # the rendered body content (without the > prefixes)
    trigger:    str                  # parsed from the <!-- pebble:... --> comment
    date_iso:   str
    confidence: float
    edited_by_user: str | None       # ISO timestamp if user has edited this block
    start_line: int                  # 0-based line index in the file
    end_line:   int                  # 0-based, inclusive


def stamp_block(
    body: str,
    *,
    trigger: str,
    confidence: float,
    label: str = CALLOUT_LABEL,
    title: str = '',
) -> str:
    """Wrap `body` in a `> [!label]+ title` callout with an HTML provenance comment.

    Example output:
        > [!pebble]+ Meeting summary — 2026-05-08
        > <!-- pebble:source=pebble date=2026-05-08T10:14:00-04:00 trigger=meeting_end confidence=0.91 -->
        > body line 1
        > body line 2
    """
    label = (label or CALLOUT_LABEL).strip() or CALLOUT_LABEL
    title = title.strip()
    header = f'> [!{label}]+ {title}'.rstrip()
    comment = (
        f'> <!-- pebble:source=pebble date={_now_iso()} '
        f'trigger={trigger} confidence={round(float(confidence), 3)} -->'
    )
    quoted_body = '\n'.join('> ' + ln if ln else '>' for ln in body.rstrip().splitlines())
    return '\n'.join([header, comment, quoted_body])


# Regex for one [!label]+ callout from the start of a line, capturing the title.
# Format: > [!label]+ optional title
_CALLOUT_HEADER_RE = re.compile(
    r'^>\s*\[!(?P<label>[A-Za-z0-9_-]+)\]\+?\s*(?P<title>.*)$',
)
# The HTML provenance comment line (already inside the callout, prefixed with `> `):
_COMMENT_RE = re.compile(
    r'^>\s*<!--\s*pebble:'
    r'(?:source=(?P<source>\S+)\s+)?'
    r'date=(?P<date>\S+)\s+'
    r'trigger=(?P<trigger>\S+)\s+'
    r'confidence=(?P<confidence>[0-9.]+)'
    r'(?:\s+edited_by_user=(?P<edited>\S+))?'
    r'\s*-->\s*$'
)


def extract_pebble_blocks(body: str, *, callout_label: str | None = None) -> list[PebbleBlock]:
    """Find every [!pebble]+ (or other-labeled) callout that has a pebble:... comment.

    Returns PebbleBlock objects in document order. Callouts without a pebble:
    comment are not Pebble-authored and are ignored.
    """
    lines  = body.splitlines()
    blocks: list[PebbleBlock] = []
    i      = 0
    n      = len(lines)

    while i < n:
        m = _CALLOUT_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue

        label = m.group('label')
        if callout_label and label != callout_label:
            i += 1
            continue

        # Next non-empty quoted line must be the pebble comment to count as ours
        comment_line_idx = i + 1
        if comment_line_idx >= n:
            i += 1
            continue
        cm = _COMMENT_RE.match(lines[comment_line_idx])
        if not cm:
            # Not Pebble-authored — just a normal user callout.
            i += 1
            continue

        # Consume the rest of the callout: lines starting with '>'.
        body_start = comment_line_idx + 1
        j = body_start
        while j < n and (lines[j].startswith('>') or lines[j].strip() == ''):
            # An empty line breaks the callout in Obsidian's renderer, but we
            # only consume lines that are quoted ('>...'). Treat blank as a break.
            if not lines[j].startswith('>'):
                break
            j += 1

        body_lines = [
            (ln[2:] if ln.startswith('> ') else ln.lstrip('>'))
            for ln in lines[body_start:j]
        ]

        try:
            confidence = float(cm.group('confidence'))
        except (TypeError, ValueError):
            confidence = 0.0

        blocks.append(PebbleBlock(
            label          = label,
            title          = m.group('title').strip(),
            body           = '\n'.join(body_lines).rstrip(),
            trigger        = cm.group('trigger') or '',
            date_iso       = cm.group('date') or '',
            confidence     = confidence,
            edited_by_user = cm.group('edited'),
            start_line     = i,
            end_line       = j - 1,
        ))
        i = j

    return blocks


def callout_has_pebble_marker(callout_lines: list[str]) -> bool:
    """Cheap check: does this set of quoted lines start with a pebble HTML comment?
    Used by the write chokepoint to detect user attempts to remove a marker.
    """
    for ln in callout_lines[:3]:
        if _COMMENT_RE.match(ln):
            return True
    return False


# ── Invariant enforcement (used by Vault.write chokepoint) ───────────────────

class ProvenanceViolation(RuntimeError):
    """Raised when a write would remove or overwrite an existing provenance marker."""


def assert_preserves_provenance(
    *,
    old_frontmatter: dict[str, Any] | None,
    new_frontmatter: dict[str, Any] | None,
    old_body: str | None,
    new_body: str | None,
) -> None:
    """Verify a proposed write doesn't strip provenance from what exists.

    Rules:
      1. If old fm has `source:`, new fm must have it too (and equal — or set to user
         only via promote_to_user, which adds promoted_from_pebble).
      2. The set of pebble blocks in old_body must all still appear (by trigger+date)
         in new_body. Replacing a block's body is fine; removing the marker is not.
    """
    if old_frontmatter is not None and new_frontmatter is not None:
        if 'source' in old_frontmatter and 'source' not in (new_frontmatter or {}):
            raise ProvenanceViolation(
                'write would strip `source:` field from frontmatter'
            )
        # Promotion path is the only legal source change: pebble → user with promoted_from_pebble.
        old_src = old_frontmatter.get('source')
        new_src = new_frontmatter.get('source')
        if old_src and new_src and old_src != new_src:
            if not (old_src == 'pebble' and new_src == 'user'
                    and new_frontmatter.get('promoted_from_pebble')):
                raise ProvenanceViolation(
                    f'write would change source: {old_src} → {new_src} '
                    f'without using promote_to_user()'
                )

    if old_body is not None and new_body is not None:
        old_keys = {(b.trigger, b.date_iso) for b in extract_pebble_blocks(old_body)}
        new_keys = {(b.trigger, b.date_iso) for b in extract_pebble_blocks(new_body)}
        missing = old_keys - new_keys
        if missing:
            raise ProvenanceViolation(
                f'write would remove existing pebble block(s): {sorted(missing)}'
            )
