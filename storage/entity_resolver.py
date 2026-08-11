"""EntityResolver — turn a string into a vault Note, or propose creating one.

Resolution precedence (per spec §4.2):
  1. Exact basename match (case-insensitive)
  2. Alias table (~/.pebble/workspace/aliases.yml)
  3. Frontmatter `aliases:` field on existing notes
  4. Fuzzy match on titles + aliases (rapidfuzz, threshold 85)
  5. LLM disambiguation (deferred — only when multiple candidates within 5 pts)

`resolve_or_create` creates a stub note when confidence is above the
configured threshold AND the input looks like a real entity. Stubs go to
`_pebble_imports/unsorted/` first by default (per plan risk #3), or to the
caller-specified folder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from rapidfuzz import fuzz, process

import paths

from .note import Note
from .vault import Vault


ResolutionSource = Literal['exact', 'alias', 'frontmatter', 'fuzzy', 'llm_disambig', 'none']


@dataclass
class Resolution:
    """One ranked match (or proposal-creation hint) from EntityResolver."""
    note:                Note | None
    confidence:          float
    source:              ResolutionSource
    suggested_creation:  dict[str, Any] | None = None
    candidates:          list[Note] = field(default_factory=list)  # for disambig

    def __bool__(self) -> bool:
        return self.note is not None


# ── heuristics for stub creation ──────────────────────────────────────────────

_COURSE_CODE_RE = re.compile(r'^\d{2}-\d{3}$')        # e.g. "15-122"
_EMAIL_RE       = re.compile(r'^[\w.+\-]+@[\w.\-]+\.\w+$')


def _looks_like_entity(text: str) -> bool:
    """Coarse "is this a real entity name?" check used before creating stubs."""
    s = text.strip()
    if not s or len(s) < 2:
        return False
    if _COURSE_CODE_RE.match(s):
        return True
    if _EMAIL_RE.match(s):
        return True
    # Multi-word, mostly capitalized — looks like a name or proper noun
    words = s.split()
    if len(words) >= 2 and sum(1 for w in words if w and w[0].isupper()) >= len(words) - 1:
        return True
    return False


def _suggest_type(text: str, hints: dict[str, Any] | None) -> tuple[str, str]:
    """Guess (entity_type, folder) from a string + optional hints.

    Returns the most likely type + the folder to put a stub in. Defaults to
    `_pebble_imports/unsorted` if no guess.
    """
    s = (text or '').strip()
    hint_type = (hints or {}).get('type')
    if hint_type == 'course' or _COURSE_CODE_RE.match(s):
        return ('course', '02 - Academia')
    if hint_type == 'person' or _EMAIL_RE.match(s):
        return ('person', '07 - People')
    if hint_type == 'project':
        return ('project', '05 - Projects')
    if hint_type == 'company':
        return ('company', '40_companies')
    return ('unsorted', '_pebble_imports/unsorted')


# ── EntityResolver ────────────────────────────────────────────────────────────

class EntityResolver:
    """Resolves user-mentioned strings against the vault.

    Aliases are stored as YAML at `~/.pebble/workspace/aliases.yml`:
        aliases:
          PIC: "02 - Academia/15-122/_Index"
          Dr Li: "07 - People/Amber Li"
    """

    DEFAULT_FUZZY_THRESHOLD = 85
    DEFAULT_STUB_CONFIDENCE_THRESHOLD = 0.4

    def __init__(
        self,
        vault: Vault,
        *,
        aliases_path: Path | None = None,
        fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
        stub_confidence_threshold: float = DEFAULT_STUB_CONFIDENCE_THRESHOLD,
    ):
        self._vault                     = vault
        self._fuzzy_threshold           = fuzzy_threshold
        self._stub_confidence_threshold = stub_confidence_threshold
        self._aliases_path              = aliases_path or (
            paths.workspace_dir() / 'aliases.yml'
        )

    # ── alias table ─────────────────────────────────────────────────────────

    def _load_aliases(self) -> dict[str, str]:
        """Return {alias_lower → note_id}. Empty dict on any failure."""
        if not _HAS_YAML or not self._aliases_path.exists():
            return {}
        try:
            data = yaml.safe_load(self._aliases_path.read_text(encoding='utf-8')) or {}
        except Exception:
            return {}
        raw = data.get('aliases', {}) if isinstance(data, dict) else {}
        out: dict[str, str] = {}
        for k, v in (raw or {}).items():
            if isinstance(k, str) and isinstance(v, str):
                out[k.strip().lower()] = v.strip()
        return out

    # ── resolution ──────────────────────────────────────────────────────────

    def resolve(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
    ) -> list[Resolution]:
        """Return ranked matches. Empty list when nothing plausible matches.

        First entry is the best match; subsequent entries are tied candidates
        (within 5 points of the best fuzzy score, for LLM-disambig wiring).
        """
        s = (text or '').strip()
        if not s:
            return []

        notes = self._vault.list()
        if not notes:
            return []

        # ── 1. exact basename ─────────────────────────────────────────────
        s_lower = s.lower()
        for n in notes:
            basename = n.id.rsplit('/', 1)[-1]
            if basename.lower() == s_lower:
                return [Resolution(note=n, confidence=1.0, source='exact')]

        # ── 2. alias table ────────────────────────────────────────────────
        aliases = self._load_aliases()
        target_id = aliases.get(s_lower)
        if target_id:
            from .vault import NoteNotFound
            try:
                n = self._vault.read(target_id)
                return [Resolution(note=n, confidence=0.98, source='alias')]
            except NoteNotFound:
                pass  # stale alias entry — ignore

        # ── 3. frontmatter `aliases:` field ───────────────────────────────
        for n in notes:
            fm_aliases = n.frontmatter.get('aliases', []) or []
            if isinstance(fm_aliases, str):
                fm_aliases = [a.strip() for a in fm_aliases.split(',') if a.strip()]
            if any((a or '').lower() == s_lower for a in fm_aliases):
                return [Resolution(note=n, confidence=0.95, source='frontmatter')]

        # ── 4. fuzzy match across titles + aliases ────────────────────────
        # Short inputs are too ambiguous for fuzzy matching — bail out
        if len(s) < 3:
            return []
        # Build a list of (key, note) where key is the searchable string
        choices: list[tuple[str, Note]] = []
        for n in notes:
            basename = n.id.rsplit('/', 1)[-1]
            choices.append((basename, n))
            fm_aliases = n.frontmatter.get('aliases', []) or []
            if isinstance(fm_aliases, str):
                fm_aliases = [a.strip() for a in fm_aliases.split(',') if a.strip()]
            for a in fm_aliases:
                if isinstance(a, str) and a.strip():
                    choices.append((a, n))
            # Also include the title if it's distinct from basename
            t = n.title
            if t and t != basename:
                choices.append((t, n))

        # rapidfuzz process.extract with scorer
        results = process.extract(
            s,
            [c[0] for c in choices],
            scorer=fuzz.WRatio,
            limit=10,
        )
        # results: list of (matched_str, score, index)
        results = [(score, choices[idx][1]) for matched, score, idx in results
                   if score >= self._fuzzy_threshold]
        # Dedupe by note id, keep best score
        best_by_note: dict[str, tuple[float, Note]] = {}
        for score, note in results:
            cur = best_by_note.get(note.id)
            if cur is None or score > cur[0]:
                best_by_note[note.id] = (score, note)
        ranked = sorted(best_by_note.values(), key=lambda x: x[0], reverse=True)

        if not ranked:
            return []

        top_score, top_note = ranked[0]
        confidence = round(top_score / 100.0, 3)
        # Gather tied candidates (within 5 points of best) for downstream LLM disambig
        tied = [n for sc, n in ranked[1:] if (top_score - sc) <= 5]
        return [Resolution(
            note=top_note, confidence=confidence, source='fuzzy',
            candidates=([top_note] + tied),
        )]

    def resolve_or_create(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
        *,
        trigger: str = 'entity_resolution',
        force_create: bool = False,
    ) -> Resolution:
        """Resolve, or create a stub note if nothing matches AND the text
        looks like a real entity.

        Returns the resolution. If a stub was created, `.note` is the new note
        and `.source == 'fuzzy'` (with confidence = stub_confidence_threshold).
        """
        results = self.resolve(text, hints=hints)
        if results:
            return results[0]

        if not force_create and not _looks_like_entity(text):
            return Resolution(note=None, confidence=0.0, source='none')

        entity_type, folder = _suggest_type(text, hints)
        # Sanitize filename (no special chars)
        safe = re.sub(r'[<>:"/\\|?*]', '-', text).strip() or 'unnamed'
        rel  = f'{folder}/{safe}.md'

        # Build a reasonable frontmatter for this type
        fm: dict[str, Any] = {'tags': []}
        body = f'# {text}\n\n_Stub created from {trigger}._\n'
        if entity_type == 'course':
            fm['tags'] = ['academia', 'course']
            fm['course_id'] = text
        elif entity_type == 'person':
            fm['tags'] = ['people']
            if _EMAIL_RE.match(text):
                fm['email'] = text
        elif entity_type == 'project':
            fm['tags'] = ['project']
            fm['status'] = 'active'
        elif entity_type == 'company':
            fm['tags'] = ['company']
            fm['status'] = 'tracking'

        note = self._vault.create_note(
            rel, body=body, frontmatter=fm,
            trigger=trigger,
            confidence=self._stub_confidence_threshold,
            note=f'Auto-created stub for "{text}" (no vault match)',
        )
        return Resolution(
            note=note,
            confidence=self._stub_confidence_threshold,
            source='fuzzy',
            suggested_creation={'created_path': str(note.path),
                                 'entity_type': entity_type},
        )
