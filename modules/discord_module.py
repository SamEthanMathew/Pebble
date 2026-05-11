"""Discord module — read messages, send messages, check DMs via Discord REST API."""

from __future__ import annotations

import json
import urllib.request
import urllib.parse
from .base import PebbleModule


class DiscordModule(PebbleModule):
    name         = 'discord'
    display_name = 'Discord'
    description  = 'Read Discord messages, send messages, and check DMs'
    icon         = '💬'
    config_fields = [
        {'key': 'bot_token',    'label': 'Bot Token',               'type': 'password'},
        {'key': 'user_token',   'label': 'User Token (optional)',    'type': 'password'},
        {'key': 'guild_id',     'label': 'Server ID (optional)',     'type': 'text'},
        {'key': 'channel_id',   'label': 'Default Channel ID',      'type': 'text'},
    ]

    _BASE = 'https://discord.com/api/v10'

    def is_ready(self) -> bool:
        return bool(self.cfg.get('bot_token') or self.cfg.get('user_token'))

    def tool_name(self) -> str:
        return 'discord'

    def tool_description(self) -> str:
        return 'Read Discord channels, send messages, list servers and channels.'

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['recent', 'send', 'channels', 'guilds', 'dms'],
                    'description': 'recent=latest messages, send=post a message, channels=list channels, guilds=list servers, dms=recent DMs',
                },
                'message': {
                    'type': 'string',
                    'description': 'Message content for "send" action',
                },
                'channel_id': {
                    'type': 'string',
                    'description': 'Channel ID to read or send to (uses default if omitted)',
                },
                'guild_id': {
                    'type': 'string',
                    'description': 'Server ID for "channels" action (uses default if omitted)',
                },
                'limit': {
                    'type': 'integer',
                    'description': 'Number of messages (default 10)',
                },
            },
            'required': ['action'],
        }

    def execute(self, action: str = 'recent', message: str = '',
                channel_id: str = '', guild_id: str = '',
                limit: int = 10, **_) -> str:
        ch = channel_id or self.cfg.get('channel_id', '')
        g  = guild_id   or self.cfg.get('guild_id', '')

        try:
            if action == 'recent':
                return self._recent(ch, min(limit, 25))
            elif action == 'send':
                return self._send(ch, message)
            elif action == 'channels':
                return self._channels(g)
            elif action == 'guilds':
                return self._guilds()
            elif action == 'dms':
                return self._dms(min(limit, 10))
            else:
                return f'Unknown action: {action}'
        except Exception as e:
            return f'Discord error: {e}'

    # ── helpers ────────────────────────────────────────────────────────────────

    def _token(self) -> str:
        tok = self.cfg.get('bot_token') or self.cfg.get('user_token', '')
        prefix = 'Bot ' if self.cfg.get('bot_token') else ''
        return prefix + tok

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        url = f'{self._BASE}/{path}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            'Authorization': self._token(),
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _post(self, path: str, data: dict) -> dict:
        url = f'{self._BASE}/{path}'
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method='POST', headers={
            'Authorization': self._token(),
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _recent(self, channel_id: str, limit: int) -> str:
        if not channel_id:
            return 'Set a default channel ID in Discord settings.'
        msgs = self._get(f'channels/{channel_id}/messages', {'limit': limit})
        if not msgs:
            return 'No messages found.'
        lines = [f'Last {len(msgs)} messages:']
        for m in reversed(msgs):
            author = m.get('author', {}).get('username', '?')
            content = m.get('content', '[embed/attachment]')[:120]
            ts = m.get('timestamp', '')[:10]
            lines.append(f'  [{ts}] {author}: {content}')
        return '\n'.join(lines)

    def _send(self, channel_id: str, message: str) -> str:
        if not channel_id:
            return 'Set a default channel ID in Discord settings.'
        if not message:
            return 'No message provided.'
        result = self._post(f'channels/{channel_id}/messages', {'content': message})
        if result.get('id'):
            return f'Message sent to channel {channel_id}.'
        return f'Failed to send: {result}'

    def _channels(self, guild_id: str) -> str:
        if not guild_id:
            return 'Set a Guild/Server ID in Discord settings.'
        channels = self._get(f'guilds/{guild_id}/channels')
        if not channels:
            return 'No channels found.'
        text_channels = [c for c in channels if c.get('type') == 0]
        lines = [f'Text channels in server {guild_id}:']
        for c in sorted(text_channels, key=lambda x: x.get('position', 0)):
            lines.append(f'  #{c["name"]} (ID: {c["id"]})')
        return '\n'.join(lines[:20])

    def _guilds(self) -> str:
        guilds = self._get('users/@me/guilds')
        if not guilds:
            return 'No servers found.'
        lines = ['Discord servers:']
        for g in guilds[:15]:
            lines.append(f'  {g.get("name", "?")} (ID: {g.get("id", "?")})')
        return '\n'.join(lines)

    def _dms(self, limit: int) -> str:
        channels = self._get('users/@me/channels')
        dms = [c for c in channels if c.get('type') == 1]
        if not dms:
            return 'No recent DMs.'
        lines = ['Recent DM channels:']
        for dm in dms[:limit]:
            recipients = dm.get('recipients', [{}])
            name = recipients[0].get('username', '?') if recipients else '?'
            lines.append(f'  {name} (channel: {dm.get("id", "?")})')
        return '\n'.join(lines)
