"""Structured error reporter — captures unhandled exceptions.

Hooks sys.excepthook + threading.excepthook so any uncaught error in either
the main thread or a daemon thread lands in ~/.pebble/errors/<YYYY-MM-DD>.jsonl.
Optional remote reporting (Sentry-style) is intentionally NOT included; opt-in
later if it becomes useful.

Idempotent — install_hooks() is safe to call multiple times.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

_ERRORS_DIR = Path.home() / '.pebble' / 'errors'
_INSTALLED = False
_LOCK = threading.Lock()


# ── PII redaction ─────────────────────────────────────────────────────────────
# Errors get appended to a JSONL file users sometimes share when filing a bug
# (per setup.html's troubleshooting page). Strip the obvious credential/PII
# patterns before write so users don't accidentally leak.
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # OAuth bearer tokens & known provider key prefixes
    (re.compile(r'\b(Bearer\s+)([A-Za-z0-9._\-]+)\b'),            r'\1[REDACTED]'),
    (re.compile(r'\bsk-(?:proj-|ant-api03-)?[A-Za-z0-9_\-]{20,}'), '[REDACTED_OPENAI_OR_ANTHROPIC_KEY]'),
    (re.compile(r'\bGOCSPX-[A-Za-z0-9_\-]{10,}'),                  '[REDACTED_GOOGLE_SECRET]'),
    (re.compile(r'\bxox[abeprs]-[A-Za-z0-9._\-]+'),                '[REDACTED_SLACK_TOKEN]'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{20,}'),                '[REDACTED_GITHUB_PAT]'),
    (re.compile(r'\bghp_[A-Za-z0-9]{20,}'),                        '[REDACTED_GITHUB_PAT]'),
    (re.compile(r'\bAIza[A-Za-z0-9_\-]{20,}'),                     '[REDACTED_GOOGLE_API_KEY]'),
    # Email addresses
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '[REDACTED_EMAIL]'),
    # URL query strings (may carry tokens, refresh_tokens, etc.)
    (re.compile(r'(\?|&)(access_token|refresh_token|token|api_key|client_secret|password)=[^&\s"]+'),
                                                                   r'\1\2=[REDACTED]'),
]


def _redact(text: str) -> str:
    """Strip credential-ish and email patterns from a string before logging."""
    if not text:
        return text
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _redact_value(v: Any) -> Any:
    """Recursively redact strings inside a context dict/list."""
    if isinstance(v, str):
        return _redact(v)
    if isinstance(v, dict):
        return {k: _redact_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_redact_value(item) for item in v]
    return v


def _now_iso() -> str:
    # Microseconds — error rows can arrive in bursts; we need ordering within a second.
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _path_today() -> Path:
    return _ERRORS_DIR / f'{datetime.date.today().isoformat()}.jsonl'


def report(exc_type, exc_value, exc_tb, *, source: str = 'main',
           context: dict[str, Any] | None = None) -> Path | None:
    """Append one error record. Returns the file path or None on failure."""
    try:
        _ERRORS_DIR.mkdir(parents=True, exist_ok=True)
        path = _path_today()
        record = {
            'timestamp':  _now_iso(),
            'source':     source,
            'error_type': getattr(exc_type, '__name__', str(exc_type)),
            'message':    _redact(str(exc_value)[:1000]),
            'traceback':  _redact(''.join(traceback.format_exception(exc_type, exc_value, exc_tb))[:8000]),
            'context':    _redact_value(context or {}),
        }
        with _LOCK:
            with path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(record, default=str, ensure_ascii=False))
                f.write('\n')
        return path
    except Exception as inner:
        print(f'[error_reporter] failed to write error record: {inner}', file=sys.stderr)
        return None


def install_hooks() -> None:
    """Wire up sys.excepthook + threading.excepthook. Safe to call repeatedly."""
    global _INSTALLED
    if _INSTALLED:
        return

    prev_sys_hook = sys.excepthook
    prev_thread_hook = getattr(threading, 'excepthook', None)

    def _sys_hook(exc_type, exc_value, exc_tb):
        report(exc_type, exc_value, exc_tb, source='main_thread')
        if prev_sys_hook is not None:
            try:
                prev_sys_hook(exc_type, exc_value, exc_tb)
            except Exception:
                pass

    def _thread_hook(args):
        report(args.exc_type, args.exc_value, args.exc_traceback,
               source=f'thread:{getattr(args.thread, "name", "unknown")}')
        if prev_thread_hook is not None:
            try:
                prev_thread_hook(args)
            except Exception:
                pass

    sys.excepthook = _sys_hook
    if hasattr(threading, 'excepthook'):
        threading.excepthook = _thread_hook
    _INSTALLED = True


def errors_dir() -> Path:
    return _ERRORS_DIR


def tail(n: int = 20, *, days: int = 7) -> list[dict[str, Any]]:
    """Most recent N errors from the last `days` files. Useful for /errors command."""
    if not _ERRORS_DIR.exists():
        return []
    today = datetime.date.today()
    files = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        p = _ERRORS_DIR / f'{d.isoformat()}.jsonl'
        if p.exists():
            files.append(p)
    out: list[dict[str, Any]] = []
    for p in files:
        try:
            for line in p.read_text(encoding='utf-8').splitlines():
                if line.strip():
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    out.sort(key=lambda r: r.get('timestamp', ''), reverse=True)
    return out[:n]
