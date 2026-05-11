"""Alpaca Markets module — portfolio, prices, news, and positions from Pebble."""

from __future__ import annotations

import requests

from .base import PebbleModule

_PAPER_BASE = 'https://paper-api.alpaca.markets'
_LIVE_BASE  = 'https://api.alpaca.markets'
_DATA_BASE  = 'https://data.alpaca.markets'


class AlpacaMarketModule(PebbleModule):
    name         = 'alpaca'
    display_name = 'Alpaca Markets'
    description  = 'Check your portfolio, stock prices, and market news via Alpaca'
    icon         = '📈'
    config_fields = [
        {'key': 'api_key',      'label': 'Alpaca API key',              'type': 'text'},
        {'key': 'api_secret',   'label': 'Alpaca API secret',           'type': 'password'},
        {'key': 'paper_trading','label': 'Paper trading? (yes/no)',      'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return (
            bool(self.cfg.get('api_key', '').strip()) and
            bool(self.cfg.get('api_secret', '').strip())
        )

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'alpaca'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['portfolio', 'price', 'news', 'positions'],
                    'description': (
                        'portfolio: account summary, '
                        'price: latest quote for a symbol, '
                        'news: recent market news, '
                        'positions: open positions'
                    ),
                },
                'symbol': {
                    'type': 'string',
                    'description': 'Stock ticker symbol (e.g. AAPL, TSLA)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = '', symbol: str = '', **_) -> str:
        api_key    = self.cfg.get('api_key', '').strip()
        api_secret = self.cfg.get('api_secret', '').strip()

        if not api_key or not api_secret:
            return 'Alpaca not configured — add your API key and secret in Settings.'

        paper_raw  = self.cfg.get('paper_trading', '').strip().lower()
        is_paper   = paper_raw in ('yes', 'true', '')
        base_url   = _PAPER_BASE if is_paper else _LIVE_BASE
        headers    = {
            'APCA-API-KEY-ID':     api_key,
            'APCA-API-SECRET-KEY': api_secret,
        }

        try:
            if action == 'portfolio':
                return self._portfolio(base_url, headers)
            elif action == 'price':
                return self._price(headers, symbol)
            elif action == 'news':
                return self._news(headers, symbol)
            elif action == 'positions':
                return self._positions(base_url, headers)
            else:
                return (
                    f'Unknown action "{action}". '
                    'Valid actions: portfolio, price, news, positions.'
                )
        except Exception as e:
            return f'Alpaca error: {str(e)}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _portfolio(self, base_url: str, headers: dict) -> str:
        try:
            resp = requests.get(
                f'{base_url}/v2/account',
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            acct = resp.json()

            equity    = float(acct.get('equity', 0))
            cash      = float(acct.get('cash', 0))
            market_open = not acct.get('trading_blocked', False)
            status_note = '' if market_open else ' (market closed)'

            return (
                f'Portfolio: ${equity:,.2f} equity, '
                f'${cash:,.2f} cash'
                f'{status_note}'
            )
        except requests.HTTPError as e:
            return f'Portfolio error: {str(e)}'

    def _price(self, headers: dict, symbol: str) -> str:
        if not symbol.strip():
            return 'Provide a stock symbol (e.g. AAPL)'

        sym = symbol.upper().strip()
        try:
            resp = requests.get(
                f'{_DATA_BASE}/v2/stocks/{sym}/quotes/latest',
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data  = resp.json()
            quote = data.get('quote', {})
            # ask price preferred; fall back to bid
            price = quote.get('ap') or quote.get('bp')
            if price is not None:
                return f'{sym}: ${float(price):,.2f} (last quote)'
        except Exception:
            pass

        # Fallback: try bars endpoint
        try:
            resp = requests.get(
                f'{_DATA_BASE}/v2/stocks/{sym}/bars/latest',
                params={'feed': 'iex'},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data  = resp.json()
            bar   = data.get('bar', {})
            close = bar.get('c')
            if close is not None:
                return f'{sym}: ${float(close):,.2f} (last trade)'
        except Exception:
            pass

        return f'Price not available for {symbol}'

    def _news(self, headers: dict, symbol: str) -> str:
        try:
            params: dict = {'limit': 5}
            if symbol.strip():
                params['symbols'] = symbol.upper().strip()

            resp = requests.get(
                f'{_DATA_BASE}/v2/news',
                params=params,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data    = resp.json()
            articles = data.get('news', [])

            if not articles:
                return 'No recent news found.'

            lines: list[str] = []
            for i, article in enumerate(articles, 1):
                headline = article.get('headline', '(no headline)')
                source   = article.get('source', '')
                created  = article.get('created_at', '')[:10]  # YYYY-MM-DD
                lines.append(f'[{i}] {headline} ({source}, {created})')

            return '\n'.join(lines)
        except requests.HTTPError as e:
            return f'News error: {str(e)}'

    def _positions(self, base_url: str, headers: dict) -> str:
        try:
            resp = requests.get(
                f'{base_url}/v2/positions',
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            positions = resp.json()

            if not positions:
                return 'No open positions.'

            lines: list[str] = []
            for pos in positions:
                sym       = pos.get('symbol', '?')
                qty       = pos.get('qty', '0')
                avg_cost  = float(pos.get('avg_entry_price', 0))
                unrealized = float(pos.get('unrealized_pl', 0))
                sign      = '+' if unrealized >= 0 else ''
                lines.append(
                    f'{sym}: {qty} shares @ ${avg_cost:,.2f} '
                    f'(P&L: {sign}${unrealized:,.2f})'
                )

            return '\n'.join(lines)
        except requests.HTTPError as e:
            return f'Positions error: {str(e)}'
