"""Spotify module — now playing, playback control, search, and queue from Pebble."""

from __future__ import annotations

import base64
import http.server
import threading
import time
import urllib.parse
import webbrowser
from typing import Optional

import requests

import crab_config
from .base import PebbleModule

_API_BASE   = 'https://api.spotify.com/v1'
_TOKEN_URL  = 'https://accounts.spotify.com/api/token'
_AUTH_URL   = 'https://accounts.spotify.com/authorize'


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

        # Persist updated tokens
        updated_cfg = dict(self.cfg)
        updated_cfg['access_token'] = access_token
        if 'refresh_token' in data:
            updated_cfg['refresh_token'] = data['refresh_token']
        self.cfg = updated_cfg
        crab_config.set_module_config('spotify', updated_cfg)

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
        cfg  = crab_config.get_module_config('spotify')
        cfg.update({
            'client_id':     client_id,
            'client_secret': client_secret,
            'redirect_uri':  redirect_uri,
            'access_token':  data.get('access_token', ''),
            'refresh_token': data.get('refresh_token', ''),
        })
        crab_config.set_module_config('spotify', cfg)

        return True, 'Spotify connected successfully!'
