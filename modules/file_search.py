"""File Search module — find files by name on the local filesystem."""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .base import PebbleModule

_MAX_DEPTH = 4


class FileSearchModule(PebbleModule):
    name         = 'file_search'
    display_name = 'File Search'
    description  = 'Search for files on your computer by name or content'
    icon         = '📁'
    config_fields = [
        {'key': 'search_paths',    'label': r'Search in folders (one per line, e.g. C:\Users\sam\Desktop)', 'type': 'text'},
        {'key': 'file_extensions', 'label': 'File types (e.g. .pdf,.docx,.txt — blank = all)',              'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'file_search'

    def tool_description(self) -> str:
        return (
            'Search for files on this computer. '
            '"find" searches by filename, "recent" lists recently modified files, '
            '"open" opens a file with its default application.'
        )

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['find', 'recent', 'open'],
                    'description': (
                        '"find" — search by filename containing query, '
                        '"recent" — files modified in last 7 days, '
                        '"open" — open a file by path or query'
                    ),
                },
                'query': {
                    'type': 'string',
                    'description': 'Filename search term (used with find / open)',
                },
                'path': {
                    'type': 'string',
                    'description': 'Full file path to open (used with action="open")',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = 'find', query: str = '', path: str = '', **_) -> str:
        action = action.strip().lower()
        try:
            if action == 'find':
                return self._find(query)
            if action == 'recent':
                return self._recent()
            if action == 'open':
                return self._open(path, query)
            return f'Unknown action "{action}". Valid actions: find, recent, open.'
        except Exception as e:
            return f'File search error: {str(e)}'

    # ── actions ───────────────────────────────────────────────────────────────

    def _find(self, query: str) -> str:
        if not query.strip():
            return 'Provide a filename to search for.'

        extensions = self._extensions()
        matches: list[dict] = []

        for root_path in self._search_paths():
            for info in self._walk(root_path):
                name_lower = info['name'].lower()
                if query.lower() in name_lower:
                    if not extensions or info['ext'] in extensions:
                        matches.append(info)
                if len(matches) >= 50:
                    break

        if not matches:
            return f'No files found matching "{query}".'

        matches = matches[:10]
        lines   = [f'Files matching "{query}":']
        for i, f in enumerate(matches, 1):
            lines.append(f'{i}. {f["name"]} — {f["path"]} ({f["size_str"]}, {f["modified_str"]})')
        return '\n'.join(lines)

    def _recent(self, days: int = 7) -> str:
        cutoff    = datetime.now(tz=timezone.utc) - timedelta(days=days)
        all_files: list[dict] = []

        for root_path in self._search_paths():
            for info in self._walk(root_path):
                if info['mtime'] and info['mtime'] >= cutoff:
                    all_files.append(info)

        if not all_files:
            return f'No files modified in the last {days} days.'

        all_files.sort(key=lambda x: x['mtime'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        top = all_files[:10]

        lines = [f'Recently modified files (last {days} days):']
        for i, f in enumerate(top, 1):
            lines.append(f'{i}. {f["name"]} — {f["path"]} ({f["size_str"]}, {f["modified_str"]})')
        return '\n'.join(lines)

    def _open(self, path: str, query: str) -> str:
        target = path.strip()

        if not target and query.strip():
            # Find the first matching file and open it
            extensions = self._extensions()
            for root_path in self._search_paths():
                for info in self._walk(root_path):
                    if query.lower() in info['name'].lower():
                        if not extensions or info['ext'] in extensions:
                            target = info['path']
                            break
                if target:
                    break

        if not target:
            return f'No file found for "{query}".'

        if not Path(target).exists():
            return f'File not found: {target}'

        try:
            os.startfile(target)  # Windows: opens with default application
            return f'Opened: {Path(target).name}'
        except AttributeError:
            # Non-Windows fallback
            import subprocess
            subprocess.Popen(['xdg-open', target])
            return f'Opened: {Path(target).name}'
        except Exception as e:
            return f'Could not open file: {str(e)}'

    # ── helpers ───────────────────────────────────────────────────────────────

    def _search_paths(self) -> list[Path]:
        raw = self.cfg.get('search_paths', '').strip()
        if raw:
            paths = [Path(p.strip()) for p in raw.splitlines() if p.strip()]
        else:
            home  = Path.home()
            paths = [home / 'Desktop', home / 'Documents', home / 'Downloads']
        return [p for p in paths if p.exists()]

    def _extensions(self) -> set[str]:
        raw = self.cfg.get('file_extensions', '').strip()
        if not raw:
            return set()
        exts = set()
        for ext in raw.split(','):
            ext = ext.strip().lower()
            if ext and not ext.startswith('.'):
                ext = '.' + ext
            if ext:
                exts.add(ext)
        return exts

    def _walk(self, root: Path):
        """Yield file info dicts, limited to _MAX_DEPTH levels deep."""
        root_str = str(root)
        root_depth = root_str.count(os.sep)

        try:
            for dirpath, dirnames, filenames in os.walk(root_str):
                current_depth = dirpath.count(os.sep) - root_depth
                if current_depth >= _MAX_DEPTH:
                    dirnames.clear()  # prune descension
                    continue
                # Skip hidden directories
                dirnames[:] = [d for d in dirnames if not d.startswith('.')]
                for filename in filenames:
                    if filename.startswith('.'):
                        continue
                    full_path = os.path.join(dirpath, filename)
                    yield self._file_info(full_path)
        except PermissionError:
            pass

    @staticmethod
    def _file_info(filepath: str) -> dict:
        p = Path(filepath)
        try:
            stat     = p.stat()
            size     = stat.st_size
            mtime_ts = stat.st_mtime
            mtime    = datetime.fromtimestamp(mtime_ts, tz=timezone.utc)
        except Exception:
            size   = 0
            mtime  = None

        # Format size
        if size < 1024:
            size_str = f'{size} B'
        elif size < 1024 ** 2:
            size_str = f'{size / 1024:.1f} KB'
        elif size < 1024 ** 3:
            size_str = f'{size / 1024 ** 2:.1f} MB'
        else:
            size_str = f'{size / 1024 ** 3:.1f} GB'

        # Format modified date
        if mtime:
            now   = datetime.now(tz=timezone.utc)
            delta = now - mtime
            if delta.days == 0:
                modified_str = 'today'
            elif delta.days == 1:
                modified_str = 'yesterday'
            elif delta.days < 30:
                modified_str = f'{delta.days} days ago'
            else:
                modified_str = mtime.strftime('%b %d, %Y')
        else:
            modified_str = 'unknown'

        return {
            'name':         p.name,
            'path':         str(p),
            'ext':          p.suffix.lower(),
            'size_str':     size_str,
            'modified_str': modified_str,
            'mtime':        mtime,
        }
