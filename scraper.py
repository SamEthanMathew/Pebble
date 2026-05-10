"""Web scraper — readability extraction for public pages.

Tries trafilatura first; falls back to a minimal regex strip + requests if not
installed. All results pass through cache.py with per-source TTL.

NOT for auth-walled pages. Those use the user-assisted scrape flow (paste DOM
manually) — that's a Phase 4 chat command.
"""

from __future__ import annotations

import re
from typing import Any

import requests

import audit
import cache

try:
    import trafilatura  # type: ignore
    _HAS_TRAFILATURA = True
except ImportError:
    _HAS_TRAFILATURA = False


_USER_AGENT = 'Pebble/0.2 (+https://github.com/SamEthanMathew/Pebble)'
_DEFAULT_TIMEOUT = 20


def _strip_html_minimal(html: str) -> tuple[str, str]:
    """Cheap fallback when trafilatura isn't available.

    Returns (title, text). Not great — definitely use trafilatura in production.
    """
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = (title_m.group(1).strip() if title_m else '')[:200]

    # Strip script/style blocks
    cleaned = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html,
                     flags=re.IGNORECASE | re.DOTALL)
    # Strip all tags
    text = re.sub(r'<[^>]+>', ' ', cleaned)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return title, text[:60000]


def fetch(url: str, *, source: str = 'generic',
          force_refresh: bool = False, timeout: int = _DEFAULT_TIMEOUT) -> dict[str, Any] | None:
    """Fetch and extract readable content. Returns dict or None on failure.

    Result: { url, source, title, text, html, fetched_at }.
    Cached per-source with TTL.
    """
    if not force_refresh:
        cached = cache.get(source, url)
        if cached is not None:
            return cached

    try:
        resp = requests.get(url, timeout=timeout,
                            headers={'User-Agent': _USER_AGENT})
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        audit.append({
            'module':  'scraper', 'action': 'fetch_failed',
            'args':    {'url': url, 'source': source},
            'result':  {'error': str(e)},
            'tier':    'auto', 'source': 'scraper',
        })
        return None

    if _HAS_TRAFILATURA:
        try:
            extracted = trafilatura.extract(html, include_comments=False,
                                             include_tables=True) or ''
            metadata = trafilatura.extract_metadata(html)
            title = (getattr(metadata, 'title', '') or '')[:200] if metadata else ''
        except Exception:
            title, extracted = _strip_html_minimal(html)
    else:
        title, extracted = _strip_html_minimal(html)

    data = {
        'url':        url,
        'source':     source,
        'title':      title,
        'text':       extracted,
        'html_bytes': len(html),
        'fetched_at': _ts_iso(),
    }
    cache.set(source, url, data)

    audit.append({
        'module':  'scraper', 'action': 'fetched',
        'args':    {'url': url, 'source': source},
        'result':  {'title_preview': title[:80], 'text_chars': len(extracted)},
        'tier':    'auto', 'source': 'scraper',
    })
    return data


def _ts_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
