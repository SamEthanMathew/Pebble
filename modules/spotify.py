"""Spotify module — now playing, playback control, search, and queue from Pebble."""

from __future__ import annotations

import base64
import http.server
import json
import os
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

import requests

import crab_config
import paths
from .base import ActionTier, PebbleModule

_API_BASE   = 'https://api.spotify.com/v1'
_TOKEN_URL  = 'https://accounts.spotify.com/api/token'
_AUTH_URL   = 'https://accounts.spotify.com/authorize'

# Secrets file lives outside config.json so tokens never get committed via
# shared config dumps or backups. Only contains token-shaped fields.
_SECRETS_PATH = paths.secrets_dir() / 'spotify_tokens.json'
_TOKEN_KEYS   = ('access_token', 'refresh_token', 'expires_at')


def _read_token_secrets() -> dict:
    """Read tokens from the dedicated secrets file. Returns {} on any error."""
    if not _SECRETS_PATH.exists():
        return {}
    try:
        data = json.loads(_SECRETS_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return {k: data.get(k, '') for k in _TOKEN_KEYS if data.get(k)}
    except Exception:
        pass
    return {}


def _write_token_secrets(tokens: dict) -> None:
    """Write only token-shaped fields to the secrets file. Best-effort chmod 600."""
    payload = {k: tokens.get(k, '') for k in _TOKEN_KEYS if tokens.get(k)}
    try:
        _SECRETS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SECRETS_PATH.write_text(
            json.dumps(payload, indent=2),
            encoding='utf-8',
        )
        try:
            os.chmod(_SECRETS_PATH, 0o600)
        except Exception:
            # NTFS DACLs do the real protection on Windows.
            pass
    except Exception:
        pass


def _load_merged_cfg(name: str = 'spotify') -> dict:
    """Merge crab_config module cfg with the secrets file (secrets win on token keys).

    Also performs a one-time migration: if the legacy config.json still carries
    tokens, lift them into the secrets file and strip them from config on the
    next write path.
    """
    base = dict(crab_config.get_module_config(name) or {})
    secrets = _read_token_secrets()

    # Migrate any legacy tokens stranded in config.json
    legacy_tokens = {k: base.get(k, '') for k in _TOKEN_KEYS if base.get(k)}
    if legacy_tokens and not secrets:
        _write_token_secrets(legacy_tokens)
        secrets = _read_token_secrets()
        # Strip token-shaped fields from config.json so they don't linger.
        sanitized = {k: v for k, v in base.items() if k not in _TOKEN_KEYS}
        try:
            crab_config.set_module_config(name, sanitized)
            base = sanitized
        except Exception:
            pass

    # Secrets file wins on token keys.
    merged = {**base, **secrets}
    return merged


class SpotifyModule(PebbleModule):
    name         = 'spotify'
    display_name = 'Spotify'
    description  = "Control Spotify — see what's playing, skip tracks, search music"
    icon         = '🎵'
    config_fields = [
        {'key': 'client_id',     'label': 'Spotify Client ID',                                    'type': 'text'},
        {'key': 'client_secret', 'label': 'Spotify Client Secret',                                'type': 'password'},
        {'key': 'redirect_uri',  'label': 'Redirect URI (default: http://localhost:8888/callback)', 'type': 'text'},
        {'key': 'access_token',  'label': 'Access token (auto-managed after connect)',             'type': 'password'},
        {'key': 'refresh_token', 'label': 'Refresh token (auto-managed)',                         'type': 'password'},
    ]

    _default_tiers = {
        'now_playing': ActionTier.AUTO,
        'search':      ActionTier.AUTO,
        'queue':       ActionTier.AUTO,
        'play':        ActionTier.NOTIFY,
        'pause':       ActionTier.NOTIFY,
        'skip':        ActionTier.NOTIFY,
        'previous':    ActionTier.NOTIFY,
    }

    def __init__(self, cfg: dict):
        # Merge in tokens from the secrets file so the rest of the class can read
        # them off self.cfg the way it always has.
        super().__init__(cfg)
        try:
            secrets = _read_token_secrets()
            if secrets:
                self.cfg = {**self.cfg, **secrets}
            else:
                # Migrate legacy tokens stranded in config.json on first load.
                legacy = {k: cfg.get(k, '') for k in _TOKEN_KEYS if cfg.get(k)}
                if legacy:
                    _write_token_secrets(legacy)
                    sanitized = {k: v for k, v in cfg.items() if k not in _TOKEN_KEYS}
                    try:
                        crab_config.set_module_config('spotify', sanitized)
                    except Exception:
                        pass
        except Exception:
            pass

    # ── readiness ─────────────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        return (
            bool(self.cfg.get('client_id', '').strip()) and
            bool(self.cfg.get('client_secret', '').strip())
        )

    # ── tool identity ─────────────────────────────────────────────────────────

    def tool_name(self) -> str:
        return 'spotify'

    def tool_description(self) -> str:
        return self.description

    def tool_parameters(self) -> dict:
        return {
            'type': 'object',
            'properties': {
                'action': {
                    'type': 'string',
                    'enum': ['now_playing', 'play', 'pause', 'skip', 'previous', 'search', 'queue'],
                    'description': (
                        'now_playing: current track, '
                        'play: resume playback, '
                        'pause: pause playback, '
                        'skip: next track, '
                        'previous: previous track, '
                        'search: search for tracks/artists, '
                        'queue: add a track to the queue'
                    ),
                },
                'query': {
                    'type': 'string',
                    'description': 'Search query or track name (used by search and queue actions)',
                },
                'uri': {
                    'type': 'string',
                    'description': 'Spotify URI (e.g. spotify:track:XXXX) used by queue action',
                },
            },
            'required': ['action'],
        }

    # ── execute ───────────────────────────────────────────────────────────────

    def execute(self, action: str = '', query: str = '', uri: str = '', **_) -> str:
        headers, err = self._get_headers()
        if err:
            return err

        try:
            if action == 'now_playing':
                return self._now_playing(headers)
            elif action == 'play':
                return self._player_put(headers, 'play', '▶ Resumed playback')
            elif action == 'pause':
                return self._player_put(headers, 'pause', '⏸ Paused')
            elif action == 'skip':
                return self._player_post(headers, 'next', '⏭ Skipped to next track')
            elif action == 'previous':
                return self._player_post(headers, 'previous', '⏮ Went back')
            elif action == 'search':
                return self._search(headers, query)
            elif action == 'queue':
                return self._queue(headers, uri=uri, query=query)
            else:
                return (
                    f'Unknown action "{action}". '
                    'Valid: now_playing, play, pause, skip, previous, search, queue.'
                )
        except Exception as e:
            return f'Spotify error: {e}'

    # ── private helpers ───────────────────────────────────────────────────────

    def _get_headers(self) -> tuple[Optional[dict], Optional[str]]:
        token = self.cfg.get('access_token', '').strip()
        if not token:
            return None, "Spotify not connected — run 'spotify connect' or add access token in Settings"
        return {'Authorization': f'Bearer {token}'}, None

    def _refresh_if_needed(self, resp: requests.Response) -> Optional[dict]:
        """If 401, attempt token refresh. Returns new headers or None on failure."""
        if resp.status_code != 401:
            return None

        refresh_token  = self.cfg.get('refresh_token', '').strip()
        client_id      = self.cfg.get('client_id', '').strip()
        client_secret  = self.cfg.get('client_secret', '').strip()

        if not refresh_token or not client_id or not client_secret:
            return None

        creds  = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
        r = requests.post(
            _TOKEN_URL,
            headers={
                'Authorization': f'Basic {creds}',
                'Content-Type':  'application/x-www-form-urlencoded',
            },
            data={
                'grant_type':    'refresh_token',
                'refresh_token': refresh_token,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None

        data         = r.json()
        access_token = data.get('access_token', '')
        if not access_token:
            return None

        # Persist updated tokens to the secrets file ONLY. Non-sensitive
        # fields (client_id, redirect_uri, etc.) stay in config.json.
        new_tokens = {
            'access_token':  access_token,
            'refresh_token': data.get('refresh_token') or self.cfg.get('refresh_token', ''),
        }
        if 'expires_in' in data:
            new_tokens['expires_at'] = str(int(time.time()) + int(data['expires_in']))
        _write_token_secrets(new_tokens)

        # In-memory cfg keeps the merged view for the rest of the request.
        updated_cfg = dict(self.cfg)
        updated_cfg.update(new_tokens)
        self.cfg = updated_cfg

        return {'Authorization': f'Bearer {access_token}'}

    def _request(self, method: str, path: str, headers: dict, **kwargs) -> requests.Response:
        """Make an API request; retry once after token refresh if 401."""
        url  = path if path.startswith('http') else f'{_API_BASE}{path}'
        resp = requests.request(method, url, headers=headers, timeout=10, **kwargs)
        if resp.status_code == 401:
            new_headers = self._refresh_if_needed(resp)
            if new_headers:
                resp = requests.request(method, url, headers=new_headers, timeout=10, **kwargs)
        return resp

    # ── actions ───────────────────────────────────────────────────────────────

    def _now_playing(self, headers: dict) -> str:
        resp = self._request('GET', '/me/player/currently-playing', headers)
        if resp.status_code == 204 or not resp.content:
            return 'Nothing playing right now.'
        if resp.status_code != 200:
            return f'Spotify error: {resp.status_code}'

        data       = resp.json()
        item       = data.get('item')
        if not item:
            return 'Nothing playing right now.'

        track      = item.get('name', 'Unknown')
        artists    = ', '.join(a['name'] for a in item.get('artists', []))
        album      = item.get('album', {}).get('name', '')
        is_playing = data.get('is_playing', False)
        status     = '▶ Playing' if is_playing else '⏸ Paused'
        return f'🎵 {track} by {artists} [{album}] — {status}'

    def _player_put(self, headers: dict, endpoint: str, success_msg: str) -> str:
        resp = self._request('PUT', f'/me/player/{endpoint}', headers)
        if resp.status_code in (200, 204):
            return success_msg
        if resp.status_code == 403:
            return 'Spotify Premium required for playback control.'
        if resp.status_code == 404:
            return 'No active Spotify device found — open Spotify on a device first.'
        return f'Spotify error: {resp.status_code}'

    def _player_post(self, headers: dict, endpoint: str, success_msg: str) -> str:
        resp = self._request('POST', f'/me/player/{endpoint}', headers)
        if resp.status_code in (200, 204):
            return success_msg
        if resp.status_code == 403:
            return 'Spotify Premium required for playback control.'
        if resp.status_code == 404:
            return 'No active Spotify device found — open Spotify on a device first.'
        return f'Spotify error: {resp.status_code}'

    def _search(self, headers: dict, query: str) -> str:
        if not query.strip():
            return 'Provide a search query (e.g. "search Bohemian Rhapsody")'

        resp = self._request(
            'GET', '/search', headers,
            params={'q': query, 'type': 'track,artist', 'limit': 5},
        )
        if resp.status_code != 200:
            return f'Spotify search error: {resp.status_code}'

        data   = resp.json()
        tracks = data.get('tracks', {}).get('items', [])
        if not tracks:
            return f'No tracks found for "{query}"'

        lines: list[str] = []
        for i, t in enumerate(tracks, 1):
            name   = t.get('name', 'Unknown')
            artist = ', '.join(a['name'] for a in t.get('artists', []))
            album  = t.get('album', {}).get('name', '')
            lines.append(f'{i}. {name} — {artist} ({album})')
        return '\n'.join(lines)

    def _queue(self, headers: dict, uri: str = '', query: str = '') -> str:
        track_name = ''

        if not uri.strip():
            # Search for the track first
            if not query.strip():
                return 'Provide a Spotify URI (spotify:track:...) or a search query to queue'
            resp = self._request(
                'GET', '/search', headers,
                params={'q': query, 'type': 'track', 'limit': 1},
            )
            if resp.status_code != 200:
                return f'Spotify search error: {resp.status_code}'
            tracks = resp.json().get('tracks', {}).get('items', [])
            if not tracks:
                return f'No tracks found for "{query}"'
            top    = tracks[0]
            uri    = top.get('uri', '')
            artist = ', '.join(a['name'] for a in top.get('artists', []))
            track_name = f"{top.get('name', 'Unknown')} by {artist}"

        resp = self._request(
            'POST', '/me/player/queue', headers,
            params={'uri': uri},
        )
        if resp.status_code in (200, 204):
            label = track_name or uri
            return f'Added to queue: {label}'
        if resp.status_code == 403:
            return 'Spotify Premium required to modify the queue.'
        if resp.status_code == 404:
            return 'No active Spotify device found — open Spotify on a device first.'
        return f'Spotify error: {resp.status_code}'

    # ── OAuth connect ─────────────────────────────────────────────────────────

    @classmethod
    def connect_spotify(
        cls,
        client_id: str,
        client_secret: str,
        redirect_uri: str = 'http://localhost:8888/callback',
    ) -> tuple[bool, str]:
        """
        Opens browser for Spotify OAuth (Authorization Code flow).
        Starts a local HTTP server to catch the callback, exchanges the code
        for tokens, and saves them to config.

        Returns (success: bool, message: str).
        """
        scopes = (
            'user-read-currently-playing '
            'user-read-playback-state '
            'user-modify-playback-state'
        )
        state = base64.urlsafe_b64encode(
            str(time.time()).encode()
        ).decode()[:16]

        params = urllib.parse.urlencode({
            'response_type': 'code',
            'client_id':     client_id,
            'scope':         scopes,
            'redirect_uri':  redirect_uri,
            'state':         state,
        })
        auth_url = f'{_AUTH_URL}?{params}'

        # Parse port from redirect_uri
        parsed = urllib.parse.urlparse(redirect_uri)
        port   = parsed.port or 8888

        # Shared result container
        result: dict = {'code': None, 'error': None}
        server_ready = threading.Event()
        server_done  = threading.Event()

        class _CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                qs    = urllib.parse.urlparse(self.path).query
                args  = urllib.parse.parse_qs(qs)
                code  = args.get('code', [None])[0]
                error = args.get('error', [None])[0]
                if code:
                    result['code'] = code
                    body = b'<html><body><h2>Connected! You can close this tab.</h2></body></html>'
                else:
                    result['error'] = error or 'access_denied'
                    body = b'<html><body><h2>Spotify login failed. Close this tab.</h2></body></html>'
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(body)
                server_done.set()

            def log_message(self, *args):  # silence default logging
                pass

        httpd = http.server.HTTPServer(('localhost', port), _CallbackHandler)
        httpd.timeout = 1  # poll every second so we can check server_done

        def _serve():
            server_ready.set()
            while not server_done.is_set():
                httpd.handle_request()
            httpd.server_close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        server_ready.wait(timeout=3)

        webbrowser.open(auth_url)

        # Wait up to 120 seconds for the user to complete OAuth
        server_done.wait(timeout=120)

        if not result.get('code'):
            err = result.get('error', 'timeout')
            return False, f'Spotify OAuth failed: {err}'

        # Exchange code for tokens
        creds = base64.b64encode(f'{client_id}:{client_secret}'.encode()).decode()
        try:
            r = requests.post(
                _TOKEN_URL,
                headers={
                    'Authorization': f'Basic {creds}',
                    'Content-Type':  'application/x-www-form-urlencoded',
                },
                data={
                    'grant_type':   'authorization_code',
                    'code':          result['code'],
                    'redirect_uri':  redirect_uri,
                },
                timeout=15,
            )
        except Exception as e:
            return False, f'Token exchange request failed: {e}'

        if r.status_code != 200:
            return False, f'Token exchange failed: {r.status_code} {r.text}'

        data = r.json()
        # Non-sensitive fields land in config.json. client_secret remains in
        # config.json because the OAuth refresh flow needs it and we don't
        # yet have a separate "app secrets" channel; tokens themselves are
        # the higher-value secret and they go to the secrets file.
        cfg  = crab_config.get_module_config('spotify')
        cfg.update({
            'client_id':     client_id,
            'client_secret': client_secret,
            'redirect_uri':  redirect_uri,
        })
        # Strip any legacy tokens from config.json on connect.
        for k in _TOKEN_KEYS:
            cfg.pop(k, None)
        crab_config.set_module_config('spotify', cfg)

        # Tokens go to the dedicated secrets file with restrictive perms.
        new_tokens = {
            'access_token':  data.get('access_token', ''),
            'refresh_token': data.get('refresh_token', ''),
        }
        if 'expires_in' in data:
            new_tokens['expires_at'] = str(int(time.time()) + int(data['expires_in']))
        _write_token_secrets(new_tokens)

        return True, 'Spotify connected successfully!'
