"""Context loader — pulls structured context from the vault for a trigger.

Replaces the ad-hoc JSON reads scattered across the planners. One function:

    load_context(trigger, depth=1, max_tokens=8000, provenance="all")
        → ContextBundle

The bundle is structured (not a flat string) so the consuming planner can
either render the whole thing into a prompt or just pick out the pieces it
needs (preferences, goals, recent decisions, daily-note excerpts).

Token budgeting is approximate — we use character count / 4 as a rough proxy
(close enough to GPT-4 / Claude tokenization for this purpose). Compaction is
"recent verbatim, older bullet-summarized": notes above the budget get their
bodies replaced with the top-N most-recent paragraphs.

Summaries NEVER persist back to the vault. If a summary is worth keeping,
it goes through Vault.create_note explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .note import Note
from .entity_resolver import EntityResolver
from .vault import Provenance, Vault


# ── Bundle shapes ─────────────────────────────────────────────────────────────

@dataclass
class EntityRef:
    """One resolved entity in the bundle. Cheaper than carrying full Notes
    so callers can decide what to expand."""
    note_id:    str
    title:      str
    source:     str                # 'user' | 'pebble' | 'mixed'
    confidence: float
    excerpt:    str                # frontmatter summary or first paragraph
    tags:       list[str] = field(default_factory=list)


@dataclass
class ContextBundle:
    """Structured context for a trigger. Total chars ~= total_tokens * 4."""
    trigger:                 dict[str, Any]
    provenance_filter_used:  Provenance
    entities:                list[EntityRef]              = field(default_factory=list)
    primary_notes:           list[Note]                   = field(default_factory=list)
    neighbor_notes:          list[Note]                   = field(default_factory=list)
    daily_note_excerpts:     list[tuple[str, str]]        = field(default_factory=list)  # (note_id, excerpt)
    preferences:             list[Note]                   = field(default_factory=list)
    goals:                   list[Note]                   = field(default_factory=list)
    recent_decisions:        list[Note]                   = field(default_factory=list)
    open_questions:          list[str]                    = field(default_factory=list)
    total_tokens:            int                          = 0
    warnings:                list[str]                    = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            'trigger':                self.trigger,
            'provenance_filter_used': self.provenance_filter_used,
            'entities': [
                {'note_id': e.note_id, 'title': e.title, 'source': e.source,
                 'confidence': e.confidence, 'tags': e.tags,
                 'excerpt': e.excerpt}
                for e in self.entities
            ],
            'primary_notes':       [n.id for n in self.primary_notes],
            'neighbor_notes':      [n.id for n in self.neighbor_notes],
            'daily_note_excerpts': self.daily_note_excerpts,
            'preferences':         [n.id for n in self.preferences],
            'goals':               [n.id for n in self.goals],
            'recent_decisions':    [n.id for n in self.recent_decisions],
            'open_questions':      self.open_questions,
            'total_tokens':        self.total_tokens,
            'warnings':            self.warnings,
        }


# ── Token estimation ──────────────────────────────────────────────────────────

def _est_tokens(text: str) -> int:
    """Rough token count: ~4 chars/token for English+code mix."""
    if not text:
        return 0
    return max(1, len(text) // 4)


# ── Compaction ────────────────────────────────────────────────────────────────

def _compact_body(body: str, max_chars: int) -> str:
    """Trim body to max_chars by keeping the first paragraph + the most-recent
    callout blocks at the end. Crude but adequate for Phase C."""
    if len(body) <= max_chars:
        return body
    # Keep head and tail; drop middle. Most notes have intro at top and
    # most-recent content at bottom (daily notes especially).
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars - 32  # leave room for the separator
    head = body[:head_chars]
    tail = body[-tail_chars:] if tail_chars > 0 else ''
    return f'{head}\n\n…[{len(body) - max_chars} chars compacted]…\n\n{tail}'


def _first_paragraph(body: str, *, max_chars: int = 280) -> str:
    """Extract the first non-empty paragraph of a note body, truncated."""
    for chunk in body.split('\n\n'):
        chunk = chunk.strip()
        if chunk and not chunk.startswith('---'):
            if len(chunk) > max_chars:
                return chunk[:max_chars].rsplit(' ', 1)[0] + '…'
            return chunk
    return ''


# ── Main entry point ─────────────────────────────────────────────────────────

def load_context(
    trigger: dict[str, Any],
    *,
    vault: Vault,
    resolver: EntityResolver | None = None,
    depth: int = 1,
    max_tokens: int = 8000,
    include_daily_notes: int = 7,
    provenance: Provenance = 'all',
) -> ContextBundle:
    """Build a ContextBundle for a planner / reasoning pass.

    `trigger` shape (caller-defined):
        {
            'type':         str,                # e.g. 'calendar_event', 'email', 'manual'
            'title':        str,                # short label
            'entity_hints': list[str],          # strings to resolve against the vault
            ...                                 # arbitrary additional context
        }
    """
    resolver = resolver or EntityResolver(vault)

    bundle = ContextBundle(
        trigger                = dict(trigger),
        provenance_filter_used = provenance,
    )

    # ── 1. Resolve entity hints (best-effort; misses don't fail) ─────────────
    hints = list(trigger.get('entity_hints') or [])
    title = trigger.get('title', '')
    if title and title not in hints:
        hints.insert(0, title)

    seen_ids: set[str] = set()
    primary_notes: list[Note] = []
    for h in hints[:8]:  # cap to avoid going wild
        results = resolver.resolve(h)
        if not results:
            continue
        r = results[0]
        if not r.note:
            continue
        if r.note.id in seen_ids:
            continue
        seen_ids.add(r.note.id)
        primary_notes.append(r.note)
        bundle.entities.append(EntityRef(
            note_id    = r.note.id,
            title      = r.note.title,
            source     = r.note.source,
            confidence = r.confidence,
            tags       = list(r.note.tags),
            excerpt    = _first_paragraph(r.note.body),
        ))

    bundle.primary_notes = primary_notes

    # ── 2. Wikilink neighbors (depth N) ──────────────────────────────────────
    neighbors: list[Note] = []
    frontier = list(primary_notes)
    visited = set(seen_ids)
    for _ in range(max(0, depth)):
        next_frontier: list[Note] = []
        for n in frontier:
            for target in n.wikilinks:
                # Resolve the wikilink target to a Note (best-effort)
                from .vault import NoteNotFound
                try:
                    neighbor = vault.read(target)
                except NoteNotFound:
                    continue
                if neighbor.id in visited:
                    continue
                if not vault._matches_provenance(neighbor, provenance):
                    continue
                visited.add(neighbor.id)
                neighbors.append(neighbor)
                next_frontier.append(neighbor)
        frontier = next_frontier
        if not frontier:
            break

    bundle.neighbor_notes = neighbors

    # ── 3. Recent daily notes ────────────────────────────────────────────────
    daily = vault.recent_daily_notes(days=include_daily_notes)
    bundle.daily_note_excerpts = [
        (n.id, _compact_body(n.body, max_chars=1500))
        for n in daily
        if vault._matches_provenance(n, provenance)
    ]

    # ── 4. Preferences / goals (notes tagged accordingly, OR in known folders) ──
    bundle.preferences = vault.find_by_frontmatter(tag='preference', provenance=provenance)
    if not bundle.preferences:
        # Fallback: anything under 50_preferences/
        bundle.preferences = [
            n for n in vault.list(provenance=provenance)
            if n.id.startswith('50_preferences/')
        ]

    bundle.goals = vault.find_by_frontmatter(tag='goal', provenance=provenance)
    if not bundle.goals:
        bundle.goals = [
            n for n in vault.list(provenance=provenance)
            if n.id.startswith('09 - Goals/')
        ]

    # ── 5. Recent decisions ───────────────────────────────────────────────────
    bundle.recent_decisions = [
        n for n in vault.find_by_frontmatter(tag='decision', provenance=provenance)
    ][:5]

    # ── 6. Open questions: notes tagged #open (in body) ──────────────────────
    for n in vault.list(provenance=provenance):
        if 'open' in n.tags:
            q = _first_paragraph(n.body, max_chars=200)
            if q:
                bundle.open_questions.append(f'[{n.id}] {q}')
    bundle.open_questions = bundle.open_questions[:10]

    # ── 7. Compact to fit token budget ───────────────────────────────────────
    max_chars = max_tokens * 4
    total_chars = sum(_est_section_chars(bundle))
    if total_chars > max_chars:
        bundle.warnings.append(
            f'context bundle exceeded budget: {total_chars} chars > {max_chars} '
            f'({total_chars - max_chars} chars over)'
        )
        # Coarse trim: drop neighbor notes first, then truncate daily excerpts
        while total_chars > max_chars and bundle.neighbor_notes:
            bundle.neighbor_notes.pop()
            total_chars = sum(_est_section_chars(bundle))
        # Then halve daily excerpts
        if total_chars > max_chars:
            bundle.daily_note_excerpts = [
                (nid, _compact_body(body, max_chars=600))
                for nid, body in bundle.daily_note_excerpts
            ]
            total_chars = sum(_est_section_chars(bundle))

    bundle.total_tokens = max(1, total_chars // 4)
    return bundle


def _est_section_chars(b: ContextBundle) -> list[int]:
    """Sum of chars across each section. Used for budget enforcement."""
    return [
        sum(len(n.body) for n in b.primary_notes),
        sum(len(n.body) for n in b.neighbor_notes),
        sum(len(excerpt) for _, excerpt in b.daily_note_excerpts),
        sum(len(n.body) for n in b.preferences),
        sum(len(n.body) for n in b.goals),
        sum(len(n.body) for n in b.recent_decisions),
        sum(len(q) for q in b.open_questions),
    ]


# ── Convenience: render bundle as Markdown for prompt injection ──────────────

def render_bundle_markdown(b: ContextBundle, *, max_chars: int = 8000) -> str:
    """Compact Markdown rendering for sticking into an LLM system prompt.

    Format:
        ## Context from your vault

        ### People / Projects / Courses
        - **{title}** ({source}, {confidence}): {excerpt}

        ### Daily notes (recent)
        ...

        ### Preferences
        ...
    """
    lines: list[str] = ['## Context from your vault', '']

    if b.entities:
        lines.append('### Resolved entities')
        for e in b.entities:
            lines.append(f'- **{e.title}** _({e.source}, conf {e.confidence:.2f})_: {e.excerpt}')
        lines.append('')

    if b.daily_note_excerpts:
        lines.append('### Recent daily notes')
        for nid, excerpt in b.daily_note_excerpts[:5]:
            lines.append(f'**{nid}**')
            lines.append(excerpt)
            lines.append('')

    if b.preferences:
        lines.append('### Preferences')
        for n in b.preferences[:10]:
            lines.append(f'- **{n.title}**: {_first_paragraph(n.body, max_chars=180)}')
        lines.append('')

    if b.goals:
        lines.append('### Goals')
        for n in b.goals[:10]:
            lines.append(f'- **{n.title}**: {_first_paragraph(n.body, max_chars=180)}')
        lines.append('')

    if b.recent_decisions:
        lines.append('### Recent decisions')
        for n in b.recent_decisions:
            lines.append(f'- **{n.title}** ({n.mtime.date()}): {_first_paragraph(n.body, max_chars=180)}')
        lines.append('')

    if b.open_questions:
        lines.append('### Open questions')
        for q in b.open_questions[:10]:
            lines.append(f'- {q}')
        lines.append('')

    text = '\n'.join(lines).rstrip()
    if len(text) > max_chars:
        text = text[:max_chars - 80] + '\n\n…[context truncated to fit budget]…'
    return text
