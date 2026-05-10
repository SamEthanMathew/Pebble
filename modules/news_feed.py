"""News Feed module — latest headlines from RSS feeds."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

import crab_config
from .base import PebbleModule

DEFAULT_FEEDS = [
    'https://feeds.feedburner.com/TechCrunch',
    'https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml',
]

# XML namespaces commonly used in Atom feeds
_ATOM_NS   = 'http://www.w3.org/2005/Atom'
_ATOM_LINK = f'{{{_ATOM_NS}}}link'
_ATOM_TITL = f'{{{_ATOM_NS}}}title'
_ATOM_PUBD = f'{{{_ATOM_NS}}}published'
_ATOM_UPDD = f'{{{_ATOM_NS}}}updated'
_ATOM_SUMM = f'{{{_ATOM_NS}}}summary'
_ATOM_ENTR = f'{{{_ATOM_NS}}}entry'


class NewsFeedModule(PebbleModule):
    name         = 'news'
    display_name = 'News Feed'
    description  = 'Get latest news from RSS feeds — tech, finance, or custom feeds'
    icon         = '📰'
    config_fields = [
        {'key': 'feeds',     'label': 'RSS feed URLs (one per line)',       'type': 'text'},
        {'key': 'max_items', 'label': 'Max items per feed (default: 5)',    'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return True  # uses defaults when no feeds configured

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'news'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['latest', 'search'],
                    'description': '"latest" — top recent headlines; "search" — filter by query',
                },
                'query': {
                    'type': 'string',
                    'description': 'Search terms (used with action="search")',
                },
                'topic': {
                    'type': 'string',
                    'description': 'Topic hint for context (optional)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = 'latest', query: str = '', topic: str = '', **_) -> str:
        feeds = self._configured_feeds()
        max_per_feed = self._max_items()

        items: list[dict] = []
        for url in feeds:
            try:
                fetched = self._fetch_feed(url)
                items.extend(fetched[:max_per_feed])
            except Exception:
                pass  # skip broken feeds silently

        # Sort newest-first; items without dates go to the back
        items.sort(key=lambda x: x.get('dt') or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        if action == 'search':
            terms = query.lower().split()
            if not terms:
                return 'Provide a search query.'
            items = [
                it for it in items
                if any(
                    term in it.get('title', '').lower() or term in it.get('description', '').lower()
                    for term in terms
                )
            ]
            if not items:
                return f'No news items matched "{query}".'

        top = items[:10]
        if not top:
            return 'No news items retrieved. Check your feed URLs in Settings.'

        today = datetime.now(tz=timezone.utc)
        header_date = today.strftime('%A, %b ') + str(today.day)

        lines: list[str] = [f'📰 Latest News — {header_date}', '']
        for i, item in enumerate(top, 1):
            title       = item.get('title', '(no title)')
            source      = item.get('source', '')
            description = item.get('description', '')
            dt          = item.get('dt')
            ago         = self._time_ago(dt) if dt else ''

            source_part = f' ({source})' if source else ''
            time_part   = f' — {ago}' if ago else ''
            desc_part   = f'\n   {description}' if description else ''

            lines.append(f'{i}. {title}{source_part}')
            if description or ago:
                lines.append(f'   {description}{time_part}')
            lines.append('')

        return '\n'.join(lines).rstrip()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _configured_feeds(self) -> list[str]:
        raw = self.cfg.get('feeds', '').strip()
        if not raw:
            return DEFAULT_FEEDS
        return [u.strip() for u in raw.splitlines() if u.strip()]

    def _max_items(self) -> int:
        try:
            return max(1, int(self.cfg.get('max_items', '5').strip()))
        except (ValueError, AttributeError):
            return 5

    def _fetch_feed(self, url: str) -> list[dict]:
        """Fetch and parse an RSS 2.0 or Atom feed. Returns list of item dicts."""
        resp = requests.get(url, timeout=10, headers={'User-Agent': 'PebbleFeed/1.0'})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        # Detect feed format
        tag = root.tag.lower()
        if 'feed' in tag or root.tag == f'{{{_ATOM_NS}}}feed':
            return self._parse_atom(root, url)
        return self._parse_rss(root, url)

    def _parse_rss(self, root: ET.Element, url: str) -> list[dict]:
        """Parse RSS 2.0 <item> elements."""
        source = ''
        channel = root.find('channel')
        if channel is not None:
            title_el = channel.find('title')
            if title_el is not None and title_el.text:
                source = title_el.text.strip()

        items: list[dict] = []
        container = channel if channel is not None else root
        for item in container.findall('item'):
            title_el   = item.find('title')
            link_el    = item.find('link')
            date_el    = item.find('pubDate')
            desc_el    = item.find('description')

            title       = (title_el.text or '').strip() if title_el is not None else ''
            link        = (link_el.text  or '').strip() if link_el  is not None else ''
            date_str    = (date_el.text  or '').strip() if date_el  is not None else ''
            description = (desc_el.text  or '').strip() if desc_el  is not None else ''

            # Strip HTML tags from description (very basic)
            description = self._strip_html(description)
            if len(description) > 150:
                description = description[:147] + '...'

            items.append({
                'title':       title,
                'link':        link,
                'description': description,
                'dt':          self._parse_date(date_str),
                'source':      source,
            })
        return items

    def _parse_atom(self, root: ET.Element, url: str) -> list[dict]:
        """Parse Atom <entry> elements."""
        # Determine source title
        source = ''
        for tag in (f'{{{_ATOM_NS}}}title', 'title'):
            el = root.find(tag)
            if el is not None and el.text:
                source = el.text.strip()
                break

        items: list[dict] = []
        # Try namespaced entry, then bare
        entries = root.findall(f'{{{_ATOM_NS}}}entry') or root.findall('entry')
        for entry in entries:
            title = ''
            for tag in (f'{{{_ATOM_NS}}}title', 'title'):
                el = entry.find(tag)
                if el is not None and el.text:
                    title = el.text.strip()
                    break

            # Link: prefer alternate rel, else first link
            link = ''
            for link_el in entry.findall(f'{{{_ATOM_NS}}}link') + entry.findall('link'):
                rel  = link_el.get('rel', 'alternate')
                href = link_el.get('href', '')
                if href and rel == 'alternate':
                    link = href
                    break
                if href and not link:
                    link = href

            date_str = ''
            for tag in (f'{{{_ATOM_NS}}}published', 'published', f'{{{_ATOM_NS}}}updated', 'updated'):
                el = entry.find(tag)
                if el is not None and el.text:
                    date_str = el.text.strip()
                    break

            description = ''
            for tag in (f'{{{_ATOM_NS}}}summary', 'summary', f'{{{_ATOM_NS}}}content', 'content'):
                el = entry.find(tag)
                if el is not None and el.text:
                    description = self._strip_html(el.text.strip())
                    break
            if len(description) > 150:
                description = description[:147] + '...'

            items.append({
                'title':       title,
                'link':        link,
                'description': description,
                'dt':          self._parse_date(date_str),
                'source':      source,
            })
        return items

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags with a simple state machine (no regex)."""
        out: list[str] = []
        in_tag = False
        for ch in text:
            if ch == '<':
                in_tag = True
            elif ch == '>':
                in_tag = False
                out.append(' ')
            elif not in_tag:
                out.append(ch)
        return ' '.join(''.join(out).split())  # also collapse whitespace

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        """Try RFC 2822 (RSS) then ISO 8601 (Atom)."""
        if not date_str:
            return None
        # RFC 2822 (used in RSS)
        try:
            dt = parsedate_to_datetime(date_str)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
        # ISO 8601 variants (Atom)
        for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(date_str[:25], fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                pass
        return None

    @staticmethod
    def _time_ago(dt: datetime) -> str:
        now   = datetime.now(tz=timezone.utc)
        delta = now - dt
        secs  = int(delta.total_seconds())
        if secs < 0:
            return 'just now'
        if secs < 3600:
            m = max(1, secs // 60)
            return f'{m}m ago'
        if secs < 86400:
            h = secs // 3600
            return f'{h}h ago'
        d = secs // 86400
        return f'{d} days ago'
