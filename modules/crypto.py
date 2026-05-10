"""Crypto prices module — CoinGecko API (free, no key required)."""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from .base import PebbleModule


class CryptoModule(PebbleModule):
    name         = 'crypto'
    display_name = 'Crypto Prices'
    description  = 'Get live cryptocurrency prices, market cap, and 24h change via CoinGecko'
    icon         = '₿'
    config_fields: list[dict] = []

    _BASE = 'https://api.coingecko.com/api/v3'

    # Friendly name → CoinGecko ID
    _ALIASES: dict[str, str] = {
        'bitcoin': 'bitcoin', 'btc': 'bitcoin',
        'ethereum': 'ethereum', 'eth': 'ethereum',
        'solana': 'solana', 'sol': 'solana',
        'cardano': 'cardano', 'ada': 'cardano',
        'xrp': 'ripple', 'ripple': 'ripple',
        'dogecoin': 'dogecoin', 'doge': 'dogecoin',
        'polkadot': 'polkadot', 'dot': 'polkadot',
        'chainlink': 'chainlink', 'link': 'chainlink',
        'avalanche': 'avalanche-2', 'avax': 'avalanche-2',
        'matic': 'matic-network', 'polygon': 'matic-network',
        'shib': 'shiba-inu', 'pepe': 'pepe', 'sui': 'sui',
        'aptos': 'aptos', 'apt': 'aptos',
        'near': 'near', 'atom': 'cosmos', 'cosmos': 'cosmos',
    }

    def is_ready(self) -> bool:
        return True

    def tool_name(self) -> str:
        return 'crypto'

    def tool_description(self) -> str:
        return ('Get live crypto prices, market data, and trending coins. '
                'Supports BTC, ETH, SOL, and hundreds of others.')

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['price', 'trending', 'top', 'search'],
                    'description': 'price=get price for coin(s), trending=top trending, top=top by market cap, search=find a coin',
                },
                'coins': {
                    'type': 'string',
                    'description': 'Comma-separated coin names or symbols for "price" action (e.g. "btc,eth,sol")',
                },
                'query': {
                    'type': 'string',
                    'description': 'Search term for "search" action',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Number of results for top/trending (default 10)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'price', coins: str = 'bitcoin,ethereum',
                query: str = '', limit: int = 10, **_) -> str:
        try:
            if action == 'price':
                return self._price(coins)
            elif action == 'trending':
                return self._trending()
            elif action == 'top':
                return self._top(min(limit, 25))
            elif action == 'search':
                return self._search(query)
            else:
                return f'Unknown action: {action}'
        except Exception as e:
            return f'Crypto error: {e}'

    # ── helpers ────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f'{self._BASE}/{path}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Pebble/2.0'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _resolve_ids(self, coins_str: str) -> list[str]:
        ids = []
        for c in coins_str.split(','):
            c = c.strip().lower()
            ids.append(self._ALIASES.get(c, c))
        return ids

    def _price(self, coins_str: str) -> str:
        ids = self._resolve_ids(coins_str)
        data = self._get('simple/price', {
            'ids': ','.join(ids),
            'vs_currencies': 'usd',
            'include_24hr_change': 'true',
            'include_market_cap': 'true',
        })
        if not data:
            return 'No price data found.'
        lines = ['Crypto prices:']
        for cid in ids:
            info = data.get(cid, {})
            if not info:
                lines.append(f'  {cid.upper()}: not found')
                continue
            price  = info.get('usd', 0)
            change = info.get('usd_24h_change', 0)
            mcap   = info.get('usd_market_cap', 0)
            arrow  = '+' if change >= 0 else '-'
            price_str = f'${price:,.2f}' if price >= 1 else f'${price:.6f}'
            lines.append(f'  {cid.upper()}: {price_str}  ({arrow}{abs(change):.2f}% 24h)  MCap: ${mcap/1e9:.1f}B')
        return '\n'.join(lines)

    def _trending(self) -> str:
        data = self._get('search/trending')
        coins = data.get('coins', [])
        if not coins:
            return 'No trending coins found.'
        lines = ['Trending on CoinGecko:']
        for i, c in enumerate(coins[:7], 1):
            item = c.get('item', {})
            name   = item.get('name', '?')
            symbol = item.get('symbol', '').upper()
            score  = item.get('score', 0)
            lines.append(f'  {i}. {name} ({symbol})')
        return '\n'.join(lines)

    def _top(self, n: int) -> str:
        data = self._get('coins/markets', {
            'vs_currency': 'usd',
            'order': 'market_cap_desc',
            'per_page': n,
            'page': 1,
            'sparkline': 'false',
        })
        if not data:
            return 'No data.'
        lines = [f'Top {n} crypto by market cap:']
        for c in data:
            name   = c.get('name', '?')
            symbol = c.get('symbol', '').upper()
            price  = c.get('current_price', 0)
            change = c.get('price_change_percentage_24h', 0)
            rank   = c.get('market_cap_rank', '?')
            price_str = f'${price:,.2f}' if price >= 1 else f'${price:.6f}'
            arrow = '+' if change >= 0 else '-'
            lines.append(f'  #{rank} {name} ({symbol}): {price_str}  ({arrow}{abs(change):.1f}% 24h)')
        return '\n'.join(lines)

    def _search(self, query: str) -> str:
        if not query:
            return 'Provide a search term.'
        data = self._get('search', {'query': query})
        coins = data.get('coins', [])[:6]
        if not coins:
            return f'No coins found for "{query}".'
        lines = [f'Coins matching "{query}":']
        for c in coins:
            name   = c.get('name', '?')
            symbol = c.get('symbol', '').upper()
            rank   = c.get('market_cap_rank', 'unranked')
            lines.append(f'  {name} ({symbol}) — rank #{rank}')
        return '\n'.join(lines)
