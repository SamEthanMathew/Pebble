"""Slack watcher + /slack-watch chat command tests (multi-workspace)."""

from __future__ import annotations


def _seed_workspaces(*names):
    """Set up a multi-workspace slack config with the given workspace names."""
    import crab_config
    cfg = crab_config.get_module_config('slack') or {}
    cfg['enabled'] = True
    cfg['workspaces'] = {
        n: {'access_token': 'xoxp-fake', 'important_channels': ''}
        for n in names
    }
    cfg['active_workspace'] = names[0]
    crab_config.set_module_config('slack', cfg)


def test_slack_watch_no_workspaces(pebble_home):
    from chat_commands import handle
    out = handle('/slack-watch')
    assert 'No Slack workspaces' in out


def test_slack_watch_single_workspace_implicit(pebble_home):
    """When only one workspace, the workspace arg is optional."""
    import crab_config
    from chat_commands import handle
    _seed_workspaces('only-one')
    handle('/slack-watch --add g-team')
    cfg = crab_config.get_module_config('slack')
    assert cfg['workspaces']['only-one']['important_channels'] == 'g-team'


def test_slack_watch_add_strips_hash(pebble_home):
    import crab_config
    from chat_commands import handle
    _seed_workspaces('only-one')
    handle('/slack-watch --add #announcements')
    cfg = crab_config.get_module_config('slack')
    assert cfg['workspaces']['only-one']['important_channels'] == 'announcements'


def test_slack_watch_replace_set_with_workspace(pebble_home):
    import crab_config
    from chat_commands import handle
    _seed_workspaces('r-pad', 'scotty-labs')
    handle('/slack-watch r-pad bar,baz,qux')
    cfg = crab_config.get_module_config('slack')
    assert set(cfg['workspaces']['r-pad']['important_channels'].split(',')) == {'bar', 'baz', 'qux'}
    # other workspace untouched
    assert cfg['workspaces']['scotty-labs']['important_channels'] == ''


def test_slack_watch_unknown_workspace(pebble_home):
    from chat_commands import handle
    _seed_workspaces('r-pad', 'scotty-labs')
    out = handle('/slack-watch nonexistent foo')
    assert 'Unknown workspace' in out
    assert '`r-pad`' in out


def test_slack_watch_remove_one(pebble_home):
    import crab_config
    from chat_commands import handle
    _seed_workspaces('r-pad')
    handle('/slack-watch r-pad foo,bar,baz')
    handle('/slack-watch r-pad --remove bar')
    cfg = crab_config.get_module_config('slack')
    assert set(cfg['workspaces']['r-pad']['important_channels'].split(',')) == {'foo', 'baz'}


def test_slack_watch_clear(pebble_home):
    import crab_config
    from chat_commands import handle
    _seed_workspaces('r-pad')
    handle('/slack-watch r-pad --add foo')
    handle('/slack-watch r-pad --clear')
    cfg = crab_config.get_module_config('slack')
    assert cfg['workspaces']['r-pad']['important_channels'] == ''


def test_slack_workspaces_command(pebble_home):
    from chat_commands import handle
    _seed_workspaces('r-pad', 'scotty-labs')
    out = handle('/slack-workspaces')
    assert 'r-pad' in out
    assert 'scotty-labs' in out


def test_slack_watcher_no_start_without_channels(pebble_home):
    """Calling start() with no channels is a no-op (no thread spawned)."""
    from modules.slack_module import SlackWatcherThread
    w = SlackWatcherThread(token='xoxp-fake', channels=[], on_notification=lambda x: None)
    w.start()
    assert w._thread is None


def test_slack_watcher_no_start_without_token(pebble_home):
    from modules.slack_module import SlackWatcherThread
    w = SlackWatcherThread(token='', channels=['general'], on_notification=lambda x: None)
    w.start()
    assert w._thread is None


def test_slack_watcher_filters_bot_messages(pebble_home):
    from modules.slack_module import SlackWatcherThread
    w = SlackWatcherThread(token='xoxp-fake', channels=['g'], on_notification=lambda x: None)
    assert w._should_skip({'subtype': 'bot_message', 'text': 'x'}) is True
    assert w._should_skip({'bot_id': 'B123', 'text': 'x'}) is True
    assert w._should_skip({'subtype': 'channel_join'}) is True
    assert w._should_skip({'text': 'hello'}) is False


def test_slack_watcher_build_info_includes_workspace_label(pebble_home):
    """Multi-workspace: each notification includes which workspace it came from."""
    from modules.slack_module import SlackWatcherThread
    w = SlackWatcherThread(
        token='xoxp-fake', channels=['g'],
        on_notification=lambda x: None,
        workspace_label='r-pad',
    )
    w._user_name_cache['U123'] = 'Sarah'
    msg = {'user': 'U123', 'text': 'hi there', 'ts': '12345.67'}
    info = w._build_info('C999', 'g-team', msg)
    assert info['workspace'] == 'r-pad'
    assert info['channel_name'] == 'g-team'
    assert info['user_name'] == 'Sarah'
    assert info['text'] == 'hi there'
