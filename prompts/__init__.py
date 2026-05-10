"""Prompt loader — parses frontmatter, validates slots, renders templates.

Usage:
    from prompts import render
    text = render('schedule_planner', {'date': '...', 'events': '...', ...})

Convention:
- Each prompt is `prompts/<name>.md` with YAML frontmatter.
- Required keys: name, version, model_tier.
- Optional: slots (list of required slot names).
- Body uses Python str.format placeholders: `{slot_name}`.
- Use `{{` and `}}` to escape literal braces (e.g. JSON examples).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent


@dataclass
class PromptTemplate:
    name:       str
    version:    int
    model_tier: str
    slots:      list[str]
    body:       str


# ── frontmatter parsing (hand-rolled for the constrained schema we use) ────────

def _parse_frontmatter(text: str) -> dict[str, Any]:
    """Parse our restricted YAML subset:
        scalar: value     (str | int)
        listkey:
          - item
          - item
    """
    out: dict[str, Any] = {}
    cur_list_key: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if line.startswith('  - ') or line.startswith('- '):
            if cur_list_key is None:
                continue
            item = stripped[2:].strip() if stripped.startswith('- ') else stripped
            out.setdefault(cur_list_key, []).append(item)
            continue
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key = key.strip()
        val = val.strip()
        if val:
            if val.lstrip('-').isdigit():
                out[key] = int(val)
            else:
                out[key] = val
            cur_list_key = None
        else:
            out[key] = []
            cur_list_key = key
    return out


# ── public API ─────────────────────────────────────────────────────────────────

def load(name: str) -> PromptTemplate:
    """Load and parse a prompt by name."""
    path = _PROMPTS_DIR / f'{name}.md'
    if not path.exists():
        raise FileNotFoundError(f'Prompt not found: {path}')

    raw = path.read_text(encoding='utf-8').lstrip('﻿')  # strip BOM
    parts = raw.split('---\n', 2)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError(f'{name}.md missing YAML frontmatter (must start with ---)')
    fm_text, body = parts[1], parts[2].lstrip('\n')
    meta = _parse_frontmatter(fm_text)

    if 'name' not in meta or 'version' not in meta:
        raise ValueError(f'{name}.md frontmatter missing required keys (name, version). Got: {sorted(meta)}')

    return PromptTemplate(
        name       = str(meta['name']),
        version    = int(meta['version']),
        model_tier = str(meta.get('model_tier', 'planner')),
        slots      = list(meta.get('slots', [])),
        body       = body.rstrip() + '\n',
    )


def render(name: str, slots: dict[str, Any]) -> str:
    """Load + render. Validates that all declared slots are present."""
    tpl = load(name)
    declared = set(tpl.slots)
    provided = set(slots.keys())

    missing = declared - provided
    if missing:
        raise KeyError(
            f'Missing slots for prompt {name!r}: {sorted(missing)}. '
            f'Declared: {sorted(declared)}, Provided: {sorted(provided)}'
        )

    try:
        return tpl.body.format(**slots)
    except KeyError as e:
        # Body references a slot the frontmatter didn't declare
        raise KeyError(
            f'Prompt {name!r} body references {e} but it is not declared in frontmatter slots. '
            f'Add it to slots or escape with double braces.'
        )


def list_prompts() -> list[str]:
    """Return all prompt names available in the prompts directory."""
    return sorted(p.stem for p in _PROMPTS_DIR.glob('*.md') if p.stem != 'README')
