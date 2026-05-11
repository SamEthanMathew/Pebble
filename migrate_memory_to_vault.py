"""One-shot migration: ~/.pebble/memory.json → Obsidian vault notes.

Each entry becomes a .md file marked
    source: pebble
    source_trigger: legacy_memory_migration
    source_confidence: 0.5

Routing:
    person     → 07 - People/<safe>.md
    place      → _pebble_imports/places/<safe>.md
    goal       → 09 - Goals/<safe>.md
    preference → 50_preferences/<safe>.md
    fact|other → _pebble_imports/unsorted/<safe>.md

Run modes:
    python migrate_memory_to_vault.py --dry-run     (default — prints plan only)
    python migrate_memory_to_vault.py --apply       (writes files; renames source to memory.json.bak)

After --apply, the file is at ~/.pebble/memory.json.bak. To revert:
    move ~/.pebble/memory.json.bak ~/.pebble/memory.json

A migration report is written to ~/.pebble/workspace/migration_report.md.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path


_MEMORY_PATH        = Path.home() / '.pebble' / 'memory.json'
_BACKUP_PATH        = Path.home() / '.pebble' / 'memory.json.bak'
_REPORT_PATH        = Path.home() / '.pebble' / 'workspace' / 'migration_report.md'

# folder, single_note_per_entry
_CATEGORY_ROUTING: dict[str, str] = {
    'person':     '07 - People',
    'place':      '_pebble_imports/places',
    'goal':       '09 - Goals',
    'preference': '50_preferences',
    'fact':       '_pebble_imports/unsorted',
    'other':      '_pebble_imports/unsorted',
}


def _safe_filename(s: str, *, max_len: int = 60) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '-', s)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return (cleaned[:max_len] or 'unsorted').rstrip(' .-')


def _derive_subject(text: str, category: str) -> str:
    """Pick a short, file-name-friendly subject for the migrated entry."""
    t = text.strip()
    if category == 'person':
        # First proper-noun phrase in the text
        m = re.search(r'\b([A-Z][\w-]+(?:\s+[A-Z][\w-]+)*)\b', t)
        if m:
            return m.group(1)
    # Otherwise: first sentence, first 8 words
    first_sentence = re.split(r'[.;]\s', t, maxsplit=1)[0]
    words = first_sentence.split()[:8]
    return ' '.join(words) or 'entry'


def _vault_path() -> Path | None:
    try:
        import crab_config
        path = (crab_config.get_module_config('obsidian') or {}).get('vault_path', '')
        if path and Path(path).is_dir():
            return Path(path)
    except Exception:
        pass
    return None


def _plan(entries: list[dict], vault_root: Path) -> list[dict]:
    """Build the migration plan: each entry → its target path and frontmatter."""
    plan = []
    used_paths: set[Path] = set()
    for e in entries:
        text     = (e.get('text') or '').strip()
        category = (e.get('category') or 'fact').lower()
        folder   = _CATEGORY_ROUTING.get(category, _CATEGORY_ROUTING['other'])

        subject = _derive_subject(text, category)
        safe    = _safe_filename(subject)
        rel_path = Path(folder) / f'{safe}.md'

        # Disambiguate collisions inside this run
        target = vault_root / rel_path
        i = 2
        while target in used_paths or target.exists():
            rel_path = Path(folder) / f'{safe}_{i}.md'
            target = vault_root / rel_path
            i += 1
        used_paths.add(target)

        plan.append({
            'entry':         e,
            'text':          text,
            'category':      category,
            'target_rel':    rel_path.as_posix(),
            'target_abs':    target,
            'subject':       subject,
            'created':       e.get('created', ''),
            'frontmatter': {
                'tags':    [category, 'memory', 'migrated'],
                'source':  'pebble',
                'source_date':       datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                'source_trigger':    'legacy_memory_migration',
                'source_confidence': 0.5,
                'source_note':       f'migrated from memory.json (original created {e.get("created", "?")})',
                'category':          category,
                'legacy_id':         e.get('id'),
            },
        })
    return plan


def _render_plan(plan: list[dict]) -> str:
    lines = [f'Migration plan: {len(plan)} entries\n']
    for i, item in enumerate(plan, 1):
        lines.append(
            f'{i:3d}. [{item["category"]:10s}] -> {item["target_rel"]}\n'
            f'     "{item["text"][:100]}"'
        )
    return '\n'.join(lines)


def _apply(plan: list[dict]) -> tuple[int, list[str]]:
    """Execute the plan via Vault.create_note. Returns (count, error_list)."""
    from storage import Vault

    vault_root = _vault_path()
    if vault_root is None:
        raise RuntimeError('No Obsidian vault configured — run /connect obsidian first.')
    vault = Vault(vault_root, autostart_watcher=False)

    count  = 0
    errors: list[str] = []
    try:
        for item in plan:
            try:
                body = (f'# {item["subject"]}\n\n'
                        f'> [!memory]+ Migrated from memory.json\n'
                        f'> _Category: {item["category"]}_ · _Original date: {item["created"] or "unknown"}_\n'
                        f'>\n'
                        f'> {item["text"]}\n')
                vault.create_note(
                    item['target_rel'],
                    body=body,
                    frontmatter={
                        'tags':     item['frontmatter']['tags'],
                        'category': item['frontmatter']['category'],
                        'legacy_id': item['frontmatter']['legacy_id'],
                    },
                    trigger='legacy_memory_migration',
                    confidence=0.5,
                    note=item['frontmatter']['source_note'],
                )
                count += 1
            except Exception as e:
                errors.append(f'{item["target_rel"]}: {e}')
    finally:
        vault.stop()
    return count, errors


def _write_report(plan: list[dict], applied_count: int, errors: list[str]) -> None:
    _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().astimezone().isoformat(timespec='seconds')

    lines = [
        '# Memory.json → Vault Migration Report',
        '',
        f'_Run at {timestamp}_',
        '',
        f'- Entries in source: {len(plan)}',
        f'- Successfully migrated: {applied_count}',
        f'- Errors: {len(errors)}',
        '',
    ]
    if errors:
        lines.append('## Errors')
        for err in errors:
            lines.append(f'- {err}')
        lines.append('')

    lines.append('## Migration plan')
    lines.append('')
    lines.append('| # | Category | Target path | Preview |')
    lines.append('|---|---|---|---|')
    for i, item in enumerate(plan, 1):
        preview = item['text'][:80].replace('|', '\\|')
        lines.append(f'| {i} | {item["category"]} | `{item["target_rel"]}` | {preview} |')

    lines.append('')
    lines.append('## Next steps')
    lines.append('')
    lines.append('1. Review each migrated note in Obsidian.')
    lines.append('2. Use `/promote-note <path>` to promote any note from `source: pebble` to `source: user` once you confirm the content.')
    lines.append('3. Use `/proposals` to see related proposals (forget-requests etc.).')
    lines.append('4. If you want to revert: move `~/.pebble/memory.json.bak` back to `~/.pebble/memory.json` and the old data is back.')

    _REPORT_PATH.write_text('\n'.join(lines), encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Migrate ~/.pebble/memory.json into the Obsidian vault.')
    p.add_argument('--apply', action='store_true', help='Actually execute the migration (default: dry-run)')
    p.add_argument('--dry-run', action='store_true', help='Print the plan only (default if no --apply)')
    args = p.parse_args(argv)

    apply_mode = args.apply and not args.dry_run

    if not _MEMORY_PATH.exists():
        print(f'No memory.json at {_MEMORY_PATH} — nothing to migrate.')
        return 0

    try:
        entries = json.loads(_MEMORY_PATH.read_text(encoding='utf-8')) or []
    except json.JSONDecodeError as e:
        print(f'Failed to parse {_MEMORY_PATH}: {e}', file=sys.stderr)
        return 2

    if not entries:
        print('memory.json is empty — nothing to migrate.')
        return 0

    vault_root = _vault_path()
    if vault_root is None:
        print('No Obsidian vault configured (config.json → modules.obsidian.vault_path).',
              file=sys.stderr)
        print('Run `/connect obsidian <vault-path>` in Pebble first.', file=sys.stderr)
        return 2

    print(f'Vault: {vault_root}')
    print(f'Source: {_MEMORY_PATH} ({len(entries)} entries)')
    print()

    plan = _plan(entries, vault_root)
    print(_render_plan(plan))
    print()

    if not apply_mode:
        print('Dry-run only. Re-run with --apply to execute.')
        return 0

    print('Applying...')
    count, errors = _apply(plan)
    print()
    print(f'Migrated: {count}/{len(plan)}')
    if errors:
        print('Errors:')
        for err in errors:
            print(f'  - {err}')

    _write_report(plan, count, errors)
    print(f'Report: {_REPORT_PATH}')

    # Move the source file so it doesn't get re-migrated next time
    if count > 0 and not errors:
        _MEMORY_PATH.rename(_BACKUP_PATH)
        print(f'Source file moved to: {_BACKUP_PATH}')

    return 0 if not errors else 1


if __name__ == '__main__':
    sys.exit(main())
