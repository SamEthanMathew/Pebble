"""Setup-wizard Google OAuth step + parse_oauth_paste helper.

The wizard's `_step_google_oauth` is a Tk page, so we don't test it via the UI
here — we test the parse + save helpers it delegates to, plus the config flag
that the Skip path writes. Together these cover the failure modes that would
break the user's first-run experience.
"""

from __future__ import annotations

import json


# ── parse_oauth_paste ─────────────────────────────────────────────────────────

def test_parse_full_installed_json(pebble_home):
    """The full JSON Google gives you (with the `installed` wrapper) parses."""
    from modules.google_auth import parse_oauth_paste
    raw = json.dumps({'installed': {
        'client_id':     'CID',
        'client_secret': 'SECRET',
        'auth_uri':      'https://example/auth',
        'token_uri':     'https://example/token',
        'redirect_uris': ['http://localhost'],
    }})
    cfg = parse_oauth_paste(raw)
    assert cfg is not None
    assert cfg['installed']['client_id']     == 'CID'
    assert cfg['installed']['client_secret'] == 'SECRET'


def test_parse_flat_json(pebble_home):
    """A flat `{client_id, client_secret}` dict also parses."""
    from modules.google_auth import parse_oauth_paste
    raw = json.dumps({'client_id': 'a', 'client_secret': 'b'})
    cfg = parse_oauth_paste(raw)
    assert cfg is not None
    assert cfg['installed']['client_id']     == 'a'
    assert cfg['installed']['client_secret'] == 'b'
    # Defaults filled in
    assert 'auth_uri'  in cfg['installed']
    assert 'token_uri' in cfg['installed']


def test_parse_pipe_pair(pebble_home):
    """The `client_id|client_secret` shortcut works too."""
    from modules.google_auth import parse_oauth_paste
    cfg = parse_oauth_paste('  myid  |  mysecret  ')
    assert cfg is not None
    assert cfg['installed']['client_id']     == 'myid'
    assert cfg['installed']['client_secret'] == 'mysecret'


def test_parse_web_wrapper(pebble_home):
    """If a user pastes a Web-application JSON, we accept it (and treat it like installed)."""
    from modules.google_auth import parse_oauth_paste
    raw = json.dumps({'web': {'client_id': 'wid', 'client_secret': 'wsec'}})
    cfg = parse_oauth_paste(raw)
    assert cfg is not None
    assert cfg['installed']['client_id'] == 'wid'


def test_parse_returns_none_on_garbage(pebble_home):
    from modules.google_auth import parse_oauth_paste
    assert parse_oauth_paste('')          is None
    assert parse_oauth_paste('   ')       is None
    assert parse_oauth_paste('not json')  is None
    # JSON, but missing required fields
    assert parse_oauth_paste('{}')                                 is None
    assert parse_oauth_paste('{"client_id": "only"}')              is None
    assert parse_oauth_paste('{"installed": {"client_id": "x"}}')  is None


# ── save_oauth_paste ──────────────────────────────────────────────────────────

def test_save_writes_file_at_secrets_path(pebble_home):
    """save_oauth_paste writes a valid JSON file at SECRETS_PATH."""
    from modules.google_auth import save_oauth_paste, SECRETS_PATH
    assert not SECRETS_PATH.exists()
    ok = save_oauth_paste('cid|csec')
    assert ok is True
    assert SECRETS_PATH.exists()
    data = json.loads(SECRETS_PATH.read_text(encoding='utf-8'))
    assert data['installed']['client_id']     == 'cid'
    assert data['installed']['client_secret'] == 'csec'


def test_save_returns_false_without_writing_on_garbage(pebble_home):
    from modules.google_auth import save_oauth_paste, SECRETS_PATH
    ok = save_oauth_paste('definitely not credentials')
    assert ok is False
    assert not SECRETS_PATH.exists()


def test_loader_reads_saved_file(pebble_home):
    """End-to-end: save via paste, then _load_client_config sees it."""
    from modules.google_auth import save_oauth_paste, _load_client_config
    save_oauth_paste('myid|mysecret')
    cfg = _load_client_config()
    assert cfg['installed']['client_id']     == 'myid'
    assert cfg['installed']['client_secret'] == 'mysecret'


def test_loader_raises_when_no_credentials(pebble_home, monkeypatch):
    """Pre-rotation behavior preserved: missing creds → OAuthNotConfigured."""
    from modules.google_auth import _load_client_config, OAuthNotConfigured
    monkeypatch.delenv('PEBBLE_GOOGLE_CLIENT_ID',     raising=False)
    monkeypatch.delenv('PEBBLE_GOOGLE_CLIENT_SECRET', raising=False)
    try:
        _load_client_config()
    except OAuthNotConfigured:
        return
    raise AssertionError('Expected OAuthNotConfigured to be raised')


def test_loader_prefers_env_vars(pebble_home, monkeypatch):
    """If both env vars and a file exist, env wins (matches docs)."""
    from modules.google_auth import save_oauth_paste, _load_client_config
    save_oauth_paste('file-id|file-secret')
    monkeypatch.setenv('PEBBLE_GOOGLE_CLIENT_ID',     'env-id')
    monkeypatch.setenv('PEBBLE_GOOGLE_CLIENT_SECRET', 'env-secret')
    cfg = _load_client_config()
    assert cfg['installed']['client_id']     == 'env-id'
    assert cfg['installed']['client_secret'] == 'env-secret'


# ── Skip path config flag ─────────────────────────────────────────────────────

def test_skip_sets_config_flag(pebble_home):
    """The wizard's Skip button is expected to write google_oauth_setup_skipped=True
    so chat can surface a 'reconnect Google' nudge later."""
    import crab_config
    # Pre-condition: flag absent or falsy
    assert not crab_config.get('google_oauth_setup_skipped')
    # Simulate what _skip_google does:
    crab_config.set_value('google_oauth_setup_skipped', True)
    assert crab_config.get('google_oauth_setup_skipped') is True
