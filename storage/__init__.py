"""Pebble storage layer — Obsidian vault as persistent knowledge backend.

Public surface:
    Note            — parsed .md file
    Vault           — read/write facade over the vault
    Hit             — search result
    NoteNotFound    — exception
    PebbleBlock     — one [!pebble]+ callout parsed from a note body

Plus the provenance helpers re-exported for callers that need to stamp,
parse, or check Pebble-authored content:
    stamp_frontmatter, stamp_block,
    mark_user_edited, promote_to_user,
    effective_source, is_user_authored,
    extract_pebble_blocks,
    ProvenanceViolation, assert_preserves_provenance
"""

from __future__ import annotations

from .note import (
    Note,
    extract_inline_tags,
    extract_wikilinks,
    parse_note,
)
from .provenance import (
    CALLOUT_LABEL,
    PebbleBlock,
    ProvenanceViolation,
    Source,
    assert_preserves_provenance,
    effective_source,
    extract_pebble_blocks,
    has_provenance_markers,
    is_user_authored,
    mark_user_edited,
    promote_to_user,
    stamp_block,
    stamp_frontmatter,
)
from .vault import Hit, NoteNotFound, Provenance, Vault

__all__ = [
    # Note layer
    'Note', 'parse_note', 'extract_wikilinks', 'extract_inline_tags',
    # Vault facade
    'Vault', 'Hit', 'NoteNotFound', 'Provenance',
    # Provenance
    'PebbleBlock', 'Source', 'CALLOUT_LABEL',
    'stamp_frontmatter', 'stamp_block',
    'mark_user_edited', 'promote_to_user',
    'effective_source', 'is_user_authored',
    'has_provenance_markers', 'extract_pebble_blocks',
    'ProvenanceViolation', 'assert_preserves_provenance',
]
