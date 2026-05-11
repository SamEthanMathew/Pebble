"""Kalshi prediction markets module — prices, positions, balance via Kalshi REST API."""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from .base import PebbleModule


class KalshiModule(PebbleModule):
    name         = 'kalshi'
    display_name = 'Kalshi'
    description  = 'Check Kalshi prediction market prices, your positions, and portfolio balance'
    icon         = '📊'
    config_fields = [
        {'key': 'api_key',    'label': 'API Key',    'type': 'password'},
        {'key': 'api_secret', 'label': 'API Secret', 'type': 'password'},
    ]

    _BASE = 'https://trading-api.kalshi.com/trade-api/v2'

    def is_ready(self) -> bool:
        return bool(self.cfg.get('api_key'))

    def tool_name(self) -> str:
        return 'kalshi'

    def tool_description(self) -> str:
        return ('Access Kalshi prediction markets: search markets, get prices, '
                'view your positions and portfolio balance.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['balance', 'positions', 'markets', 'search', 'market'],
                    'description': 'What to do: balance=portfolio value, positions=open trades, markets=trending markets, search=find a market, market=get single market details',
                },
                'query': {
                    'type': 'string',
                    'description': 'Search query for "search" action (e.g. "Fed rate cut 2025")',
                },
                'ticker': {
                    'type': 'string',
                    'description': 'Market ticker for "market" action (e.g. "FED-25DEC-T5.25")',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'balance', query: str = '', ticker: str = '', **_) -> str:
        try:
            if action == 'balance':
                return self._balance()
            elif action == 'positions':
                return self._positions()
            elif action == 'markets':
                return self._markets()
            elif action == 'search':
                return self._search(query)
            elif action == 'market':
                return self._market(ticker)
            else:
                return f'Unknown action: {action}'
        except Exception as e:
            return f'Kalshi error: {e}'

    # ── internal ───────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f'{self._BASE}/{path}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        key = self.cfg.get('api_key', '')
        req = urllib.request.Request(url, headers={
            'Authorization': f'Token {key}',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _balance(self) -> str:
        data = self._get('portfolio/balance')
        bal = data.get('balance', 0)
        dollars = bal / 100
        return f'Kalshi balance: ${dollars:,.2f}'

    def _positions(self) -> str:
        data = self._get('portfolio/positions', {'limit': 20})
        positions = data.get('event_positions', []) or data.get('positions', [])
        if not positions:
            return 'No open Kalshi positions.'
        lines = ['Open Kalshi positions:']
        for p in positions:
            ticker = p.get('ticker', p.get('event_ticker', '?'))
            yes_count = p.get('yes_contracts', 0)
            no_count  = p.get('no_contracts', 0)
            unrealized = p.get('unrealized_pnl', 0) / 100 if p.get('unrealized_pnl') else 0
            side = f'YES x{yes_count}' if yes_count else f'NO x{no_count}'
            pnl = f'P&L: ${unrealized:+.2f}' if unrealized != 0 else ''
            lines.append(f'  {ticker}: {side} {pnl}'.rstrip())
        return '\n'.join(lines)

    def _markets(self) -> str:
        data = self._get('markets', {'limit': 10, 'status': 'open'})
        markets = data.get('markets', [])
        if not markets:
            return 'No markets found.'
        lines = ['Trending Kalshi markets:']
        for m in markets[:8]:
            title = m.get('title', m.get('subtitle', '?'))[:60]
            yes_bid = m.get('yes_bid', 0)
            yes_ask = m.get('yes_ask', 0)
            yes_pct = round((yes_bid + yes_ask) / 2) if yes_bid and yes_ask else yes_bid or yes_ask
            ticker = m.get('ticker', '')
            lines.append(f'  [{yes_pct}¢ YES] {title} ({ticker})')
        return '\n'.join(lines)

    def _search(self, query: str) -> str:
        if not query:
            return 'Provide a search query.'
        data = self._get('markets', {'limit': 10, 'status': 'open', 'search': query})
        markets = data.get('markets', [])
        if not markets:
            return f'No Kalshi markets found for "{query}".'
        lines = [f'Kalshi markets for "{query}":']
        for m in markets[:8]:
            title = m.get('title', m.get('subtitle', '?'))[:65]
            yes_bid = m.get('yes_bid', 0)
            yes_ask = m.get('yes_ask', 0)
            yes_pct = round((yes_bid + yes_ask) / 2) if yes_bid and yes_ask else yes_bid or yes_ask
            ticker = m.get('ticker', '')
            lines.append(f'  [{yes_pct}¢] {title} ({ticker})')
        return '\n'.join(lines)

    def _market(self, ticker: str) -> str:
        if not ticker:
            return 'Provide a market ticker.'
        data = self._get(f'markets/{ticker}')
        m = data.get('market', data)
        title = m.get('title', m.get('subtitle', '?'))
        yes_bid  = m.get('yes_bid', 0)
        yes_ask  = m.get('yes_ask', 0)
        no_bid   = m.get('no_bid', 0)
        no_ask   = m.get('no_ask', 0)
        volume   = m.get('volume', 0)
        close_time = m.get('close_time', '')[:10]
        result = m.get('result', '')
        lines = [
            f'Market: {title}',
            f'Ticker: {ticker}',
            f'YES: bid {yes_bid}¢  ask {yes_ask}¢',
            f'NO:  bid {no_bid}¢  ask {no_ask}¢',
            f'Volume: {volume:,}',
        ]
        if close_time:
            lines.append(f'Closes: {close_time}')
        if result:
            lines.append(f'Result: {result}')
        return '\n'.join(lines)
