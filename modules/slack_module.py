"""Slack module — read messages, send, search channels, and DM from Pebble."""

from __future__ import annotations

import datetime

import requests

from .base import PebbleModule

_API_BASE = 'https://slack.com/api'


class SlackModule(PebbleModule):
    name         = 'slack'
    display_name = 'Slack'
    description  = 'Read and send Slack messages, search channels'
    icon         = '💬'
    config_fields = [
        {'key': 'bot_token',          'label': 'Token (xoxb-/xoxp-/xoxe.xoxp-)',     'type': 'password'},
        {'key': 'default_channel',    'label': 'Default channel (e.g. general)',     'type': 'text'},
        {'key': 'important_channels', 'label': 'Watched channels (comma-separated, e.g. g-team,announcements)', 'type': 'text'},
    ]

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        token = self.cfg.get('bot_token', '').strip()
        # Accept any valid Slack token format: bot (xoxb-), user (xoxp-), or
        # the newer rotatable user-OAuth tokens (xoxe.xoxp-…).
        if not token:
            return False
        return any(token.startswith(p) for p in ('xoxb-', 'xoxp-', 'xoxe.xoxp-', 'xoxe-'))

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'slack'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['recent', 'search', 'send', 'channels', 'dm'],
                    'description': (
                        'recent: last 10 messages in a channel, '
                        'search: search messages by keyword, '
                        'send: post a message to a channel, '
                        'channels: list available channels, '
                        'dm: send a direct message to a user'
                    ),
                },
                'channel': {
                    'type': 'string',
                    'description': 'Channel name (without #). Falls back to default_channel.',
                },
                'query': {
                    'type': 'string',
                    'description': 'Search query (used by search action)',
                },
                'message': {
                    'type': 'string',
                    'description': 'Message text to send (used by send and dm actions)',
                },
                'user': {
                    'type': 'string',
                    'description': 'Slack username, display name, or email (used by dm action)',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(
        self,
        action:  str = '',
        channel: str = '',
        query:   str = '',
        message: str = '',
        user:    str = '',
        **_,
    ) -> str:
        token = self.cfg.get('bot_token', '').strip()
        if not token:
            return 'Slack not configured — add your Bot Token in Settings.'
        if not any(token.startswith(p) for p in ('xoxb-', 'xoxp-', 'xoxe.xoxp-', 'xoxe-')):
            return ('Slack token looks invalid — expected one of: '
                    'xoxb- (bot), xoxp- (user OAuth), xoxe.xoxp- (rotatable user OAuth).')

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }
        default_channel = channel.strip() or self.cfg.get('default_channel', 'general').strip()

        try:
            if action == 'recent':
                return self._recent(headers, default_channel)
            elif action == 'search':
                return self._search(headers, query)
            elif action == 'send':
                return self._send(headers, default_channel, message)
            elif action == 'channels':
                return self._channels(headers)
            elif action == 'dm':
                return self._dm(headers, user, message)
            else:
                return (
                    f'Unknown action "{action}". '
                    'Valid: recent, search, send, channels, dm.'
                )
        except Exception as e:
            return f'Slack error: {e}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _get(self, headers: dict, method: str, params: dict | None = None) -> dict:
        resp = requests.get(
            f'{_API_BASE}/{method}',
            headers=headers,
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok'):
            raise RuntimeError(data.get('error', 'unknown_error'))
        return data

    def _post(self, headers: dict, method: str, payload: dict) -> dict:
        resp = requests.post(
            f'{_API_BASE}/{method}',
            headers=headers,
            json=payload,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('ok'):
            raise RuntimeError(data.get('error', 'unknown_error'))
        return data

    def _resolve_channel_id(self, headers: dict, channel_name: str) -> str:
        """Convert a channel name (with or without #) to a Slack channel ID."""
        name = channel_name.lstrip('#').strip().lower()
        cursor = None
        while True:
            params: dict = {'limit': 200, 'exclude_archived': 'true'}
            if cursor:
                params['cursor'] = cursor
            data = self._get(headers, 'conversations.list', params=params)
            for ch in data.get('channels', []):
                if ch.get('name', '').lower() == name:
                    return ch['id']
            meta   = data.get('response_metadata', {})
            cursor = meta.get('next_cursor', '')
            if not cursor:
                break
        return ''

    def _fmt_ts(self, ts: str) -> str:
        """Format a Slack timestamp (Unix float string) to HH:MM."""
        try:
            dt = datetime.datetime.fromtimestamp(float(ts))
            return dt.strftime('%H:%M')
        except Exception:
            return ts

    def _resolve_user_display(self, headers: dict, user_id: str) -> str:
        """Return a display name for a user ID."""
        try:
            data = self._get(headers, 'users.info', params={'user': user_id})
            profile = data.get('user', {}).get('profile', {})
            return (
                profile.get('display_name')
                or profile.get('real_name')
                or user_id
            )
        except Exception:
            return user_id

    # ── actions ───────────────────────────────────────────────────────────────

    def _recent(self, headers: dict, channel_name: str) -> str:
        try:
            channel_id = self._resolve_channel_id(headers, channel_name)
        except RuntimeError as e:
            return f'Slack error: {e}'

        if not channel_id:
            return f'Channel not found: #{channel_name}'

        try:
            data = self._get(
                headers,
                'conversations.history',
                params={'channel': channel_id, 'limit': 10},
            )
        except RuntimeError as e:
            return f'Slack error: {e}'

        msgs = data.get('messages', [])
        if not msgs:
            return f'No messages in #{channel_name}.'

        lines: list[str] = []
        for msg in msgs:
            ts        = self._fmt_ts(msg.get('ts', ''))
            user_id   = msg.get('user', '')
            username  = self._resolve_user_display(headers, user_id) if user_id else 'Bot'
            text      = msg.get('text', '').replace('\n', ' ')
            if len(text) > 200:
                text = text[:197] + '...'
            lines.append(f'{username}: {text} ({ts})')

        return '\n'.join(lines)

    def _search(self, headers: dict, query: str) -> str:
        if not query.strip():
            return 'Provide a search query.'

        # search.messages requires a user token (xoxp-); with bot tokens it may fail
        try:
            data = self._get(
                headers,
                'search.messages',
                params={'query': query, 'count': 5},
            )
        except RuntimeError as e:
            if 'not_allowed_token_type' in str(e) or 'missing_scope' in str(e):
                return (
                    'Slack search requires a user token (xoxp-) with search:read scope. '
                    'Bot tokens cannot search messages.'
                )
            return f'Slack error: {e}'

        matches = data.get('messages', {}).get('matches', [])
        if not matches:
            return f'No messages found for "{query}"'

        lines: list[str] = []
        for m in matches:
            ch_name = m.get('channel', {}).get('name', '?')
            uname   = m.get('username', '?')
            text    = m.get('text', '').replace('\n', ' ')
            if len(text) > 150:
                text = text[:147] + '...'
            ts  = m.get('ts', '')
            date = self._fmt_ts(ts)
            lines.append(f'#{ch_name}: {uname}: {text} — {date}')

        return '\n'.join(lines)

    def _send(self, headers: dict, channel_name: str, message: str) -> str:
        if not message.strip():
            return 'Provide a message to send.'

        # Slack accepts channel names directly for postMessage
        ch = channel_name.lstrip('#').strip()
        if not ch:
            return 'Provide a channel name.'

        try:
            self._post(headers, 'chat.postMessage', {
                'channel': f'#{ch}',
                'text':    message,
            })
            return f'Message sent to #{ch}'
        except RuntimeError as e:
            return f'Slack error: {e}'

    def _channels(self, headers: dict) -> str:
        try:
            data = self._get(
                headers,
                'conversations.list',
                params={'limit': 20, 'exclude_archived': 'true'},
            )
        except RuntimeError as e:
            return f'Slack error: {e}'

        channels = data.get('channels', [])
        if not channels:
            return 'No channels found.'

        lines: list[str] = []
        for i, ch in enumerate(channels, 1):
            name    = ch.get('name', '?')
            members = ch.get('num_members', 0)
            purpose = ch.get('purpose', {}).get('value', '') or ''
            if len(purpose) > 80:
                purpose = purpose[:77] + '...'
            purpose_str = f' — {purpose}' if purpose else ''
            lines.append(f'{i}. #{name} ({members} members){purpose_str}')

        return '\n'.join(lines)

    def _dm(self, headers: dict, user: str, message: str) -> str:
        if not user.strip():
            return 'Provide a username, display name, or email to DM.'
        if not message.strip():
            return 'Provide a message to send.'

        # Try to find the user ID
        user_id = self._find_user_id(headers, user.strip())
        if not user_id:
            return f'Could not find Slack user: {user}'

        # Open (or reuse) a DM channel
        try:
            data = self._post(headers, 'conversations.open', {'users': user_id})
        except RuntimeError as e:
            return f'Slack error opening DM: {e}'

        dm_channel = data.get('channel', {}).get('id', '')
        if not dm_channel:
            return 'Could not open DM channel.'

        try:
            self._post(headers, 'chat.postMessage', {
                'channel': dm_channel,
                'text':    message,
            })
            return f'DM sent to {user}'
        except RuntimeError as e:
            return f'Slack error sending DM: {e}'

    def _find_user_id(self, headers: dict, query: str) -> str:
        """Find a user ID by email, display name, or real name."""
        # Try email first
        if '@' in query:
            try:
                data = self._get(
                    headers,
                    'users.lookupByEmail',
                    params={'email': query},
                )
                return data.get('user', {}).get('id', '')
            except RuntimeError:
                pass  # fall through to name search

        # Search by display name / real name via users.list
        query_lower = query.lower()
        cursor = None
        while True:
            params: dict = {'limit': 200}
            if cursor:
                params['cursor'] = cursor
            try:
                data = self._get(headers, 'users.list', params=params)
            except RuntimeError:
                break

            for member in data.get('members', []):
                if member.get('deleted') or member.get('is_bot'):
                    continue
                profile = member.get('profile', {})
                display = (profile.get('display_name') or '').lower()
                real    = (profile.get('real_name') or '').lower()
                name    = (member.get('name') or '').lower()
                if query_lower in (display, real, name):
                    return member['id']

            meta   = data.get('response_metadata', {})
            cursor = meta.get('next_cursor', '')
            if not cursor:
                break

        return ''


# ── Slack watcher thread (background poll) ────────────────────────────────────

import threading
from typing import Callable


class SlackWatcherThread:
    """Polls watched Slack channels for new messages.

    Mirrors the GmailWatcherThread pattern. First run silently records the
    current latest-message timestamp per channel (no notification flood on
    startup). Subsequent polls call on_notification() for messages newer
    than what we've already seen.

    on_notification(info) receives:
        {
            'channel_id':   str,
            'channel_name': str,
            'user_id':      str,
            'user_name':    str,
            'text':         str,
            'permalink':    str,
            'ts':           str,   # Slack timestamp
        }
    """

    POLL_INTERVAL_SECONDS = 60
    SLACK_API_BASE = 'https://slack.com/api'

    def __init__(self, *, token: str, channels: list[str],
                 on_notification: Callable[[dict], None],
                 workspace_label: str = ''):
        self._token             = (token or '').strip()
        self._channels_raw      = [c.strip().lstrip('#') for c in (channels or []) if c.strip()]
        self._on_notification   = on_notification
        self._workspace_label   = workspace_label or ''
        self._stop_event        = threading.Event()
        self._thread:           threading.Thread | None = None

        # Resolved at first poll: {channel_name_lower: channel_id}
        self._channel_ids:      dict[str, str] = {}
        # Last-seen timestamp per channel_id (Slack 'ts' string)
        self._last_seen_ts:     dict[str, str] = {}
        # Cache of user_id → display name
        self._user_name_cache:  dict[str, str] = {}

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._token or not self._channels_raw:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name='SlackWatcher')
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ── poll loop ─────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        first_run = True
        # First poll fires immediately; subsequent ones every POLL_INTERVAL_SECONDS
        while not self._stop_event.wait(timeout=0 if first_run else self.POLL_INTERVAL_SECONDS):
            try:
                self._tick(first_run=first_run)
            except Exception:
                # Swallow — never crash silently mid-session
                pass
            first_run = False

    def _tick(self, *, first_run: bool) -> None:
        # Resolve channel name → ID once (cache thereafter)
        if not self._channel_ids:
            self._resolve_channel_ids()
            if not self._channel_ids:
                return

        for channel_name, channel_id in self._channel_ids.items():
            try:
                new_msgs = self._fetch_new_messages(channel_id)
            except Exception:
                continue
            if not new_msgs:
                continue
            # Update last_seen to the most-recent message
            self._last_seen_ts[channel_id] = new_msgs[0].get('ts', self._last_seen_ts.get(channel_id, ''))
            if first_run:
                continue  # silent backfill on first run
            # Most-recent-first → notify oldest-first so the popup queue is natural
            for msg in reversed(new_msgs):
                if self._should_skip(msg):
                    continue
                info = self._build_info(channel_id, channel_name, msg)
                try:
                    self._on_notification(info)
                except Exception:
                    pass

    # ── helpers ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {'Authorization': f'Bearer {self._token}'}

    def _resolve_channel_ids(self) -> None:
        """Map each watched channel name to its ID via conversations.list."""
        try:
            r = requests.get(f'{self.SLACK_API_BASE}/conversations.list',
                              headers=self._headers(),
                              params={'limit': 1000,
                                      'types': 'public_channel,private_channel,mpim,im'},
                              timeout=15)
            data = r.json() if r.status_code == 200 else {}
        except Exception:
            return
        if not data.get('ok'):
            return
        wanted = {c.lower() for c in self._channels_raw}
        for ch in data.get('channels', []):
            name = (ch.get('name') or '').lower()
            if name in wanted and ch.get('id'):
                self._channel_ids[name] = ch['id']

    def _fetch_new_messages(self, channel_id: str) -> list[dict]:
        """Pull messages newer than the last-seen ts for this channel."""
        params = {'channel': channel_id, 'limit': 50}
        oldest = self._last_seen_ts.get(channel_id)
        if oldest:
            params['oldest'] = oldest
        r = requests.get(f'{self.SLACK_API_BASE}/conversations.history',
                          headers=self._headers(), params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get('ok'):
            return []
        msgs = data.get('messages', []) or []
        # Slack returns most-recent first; drop the boundary message if oldest was inclusive
        if oldest:
            msgs = [m for m in msgs if m.get('ts', '') > oldest]
        return msgs

    def _should_skip(self, msg: dict) -> bool:
        """Filter out bot messages, channel join/leave noise, and our own messages."""
        if msg.get('subtype') in ('bot_message', 'channel_join', 'channel_leave',
                                   'channel_topic', 'channel_purpose'):
            return True
        if msg.get('bot_id'):
            return True
        return False

    def _resolve_user_name(self, user_id: str) -> str:
        if not user_id:
            return 'someone'
        if user_id in self._user_name_cache:
            return self._user_name_cache[user_id]
        try:
            r = requests.get(f'{self.SLACK_API_BASE}/users.info',
                              headers=self._headers(),
                              params={'user': user_id}, timeout=10)
            data = r.json() if r.status_code == 200 else {}
        except Exception:
            return user_id
        if not data.get('ok'):
            return user_id
        profile = data.get('user', {}).get('profile', {}) or {}
        name = (profile.get('display_name') or profile.get('real_name')
                or data.get('user', {}).get('name') or user_id)
        self._user_name_cache[user_id] = name
        return name

    def _build_info(self, channel_id: str, channel_name: str, msg: dict) -> dict:
        user_id = msg.get('user', '') or msg.get('bot_id', '')
        return {
            'workspace':    self._workspace_label,
            'channel_id':   channel_id,
            'channel_name': channel_name,
            'user_id':      user_id,
            'user_name':    self._resolve_user_name(user_id),
            'text':         (msg.get('text') or '')[:500],
            'ts':           msg.get('ts', ''),
        }
