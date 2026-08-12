"""Tool-orchestrator chat-path safety: tier-aware audit + ASK refusal.

The orchestrator runs synchronous tool calls during chat. A prompt-injected
webpage fed through the scraper must not be able to trigger silent ASK-tier
outbound writes (e.g. gmail.draft sending unreviewed email).
"""

from __future__ import annotations


def _make_module(name='probe', default_tiers=None):
    from modules.base import PebbleModule

    class _Probe(PebbleModule):
        _default_tiers = default_tiers or {}

        def __init__(self):
            super().__init__({'enabled': True})
            self.calls = []

        def tool_name(self): return name
        def tool_description(self): return 'probe'
        def tool_parameters(self): return {}

        def execute(self, action='', **kwargs):
            self.calls.append({'action': action, 'kwargs': kwargs})
            return f'ran {action}'

    return _Probe()


def _orchestrator_with(mod):
    from tool_orchestrator import ToolOrchestrator

    # The orchestrator only needs `backend.entry` for tool_mode_for(); we never
    # call backend.chat in these tests because _run is invoked directly.
    class _FakeBackend:
        entry = {'type': 'anthropic'}

    o = ToolOrchestrator(_FakeBackend(), [mod])
    return o


def test_ask_tier_chat_call_is_refused(pebble_home):
    """ASK-tier actions invoked via chat are refused with a clear message."""
    from modules.base import ActionTier
    mod = _make_module('email', {'send': ActionTier.ASK})
    o = _orchestrator_with(mod)
    result = o._run('email', {'action': 'send', 'to': 'a@b'})
    assert 'Refusing' in result or 'requires explicit approval' in result
    assert mod.calls == []   # the live API was NEVER reached


def test_notify_tier_chat_call_is_audited(pebble_home):
    """NOTIFY-tier actions execute but are audit-logged with source=chat."""
    import audit
    import first_time_ledger as ledger
    from modules.base import ActionTier
    mod = _make_module('todo', {'add': ActionTier.NOTIFY})
    # Pre-record the first-time key so this exercises the steady-state NOTIFY path;
    # first-time gating (-> ASK) is covered by the autonomy tests.
    ledger.record(ledger.make_key('todo', 'add'))
    o = _orchestrator_with(mod)
    o._run('todo', {'action': 'add', 'text': 'buy milk'})
    rows = audit.tail(20)
    chat_rows = [r for r in rows
                 if r.get('source') == 'chat' and r.get('module') == 'todo']
    assert chat_rows, 'NOTIFY-tier chat call should write an audit row'
    assert chat_rows[-1]['tier'] == 'notify'
    assert mod.calls == [{'action': 'add', 'kwargs': {'text': 'buy milk'}}]


def test_dry_run_blocks_chat_notify_write(pebble_home):
    """In dry-run mode a chat NOTIFY write must NOT reach the live module.

    Regression guard for the chat->autonomy bypass (P0-4): _run must route
    write-tier calls through Autonomy.route(), which honours dry_run.is_enabled().
    Before the fix, _run called mod.execute() directly, so a chat-driven (or
    prompt-injected) NOTIFY write fired for real even with dry-run ON.
    """
    import dry_run
    from modules.base import ActionTier
    dry_run.set_enabled(True)
    try:
        mod = _make_module('todo', {'add': ActionTier.NOTIFY})
        o = _orchestrator_with(mod)
        result = o._run('todo', {'action': 'add', 'text': 'buy milk'})
        assert mod.calls == []                 # live module NEVER executed under dry-run
        assert 'dry-run' in result.lower()
    finally:
        dry_run.set_enabled(False)


def test_first_time_notify_is_gated_then_runs_from_chat(pebble_home):
    """A first-ever NOTIFY chat write is first-time-gated (audited as ask) but
    still executes (chat auto-approves NOTIFY) — and the ledger records it."""
    import audit
    import first_time_ledger as ledger
    from modules.base import ActionTier
    mod = _make_module('journal', {'append': ActionTier.NOTIFY})
    assert ledger.is_first_time(ledger.make_key('journal', 'append'))
    o = _orchestrator_with(mod)
    o._run('journal', {'action': 'append', 'text': 'note'})
    assert mod.calls == [{'action': 'append', 'kwargs': {'text': 'note'}}]
    assert not ledger.is_first_time(ledger.make_key('journal', 'append'))
    row = [r for r in audit.tail(20) if r.get('source') == 'chat'][-1]
    assert row['tier'] == 'ask' and row['was_first_time'] is True


def test_auto_tier_chat_call_is_not_audited(pebble_home):
    """AUTO-tier read-only actions don't pollute the audit log."""
    import audit
    from modules.base import ActionTier
    mod = _make_module('search', {'query': ActionTier.AUTO})
    o = _orchestrator_with(mod)
    before = len(audit.tail(50))
    o._run('search', {'action': 'query', 'q': 'cats'})
    after = audit.tail(50)
    chat_rows = [r for r in after[before:] if r.get('source') == 'chat']
    assert chat_rows == []   # AUTO is silent
    assert mod.calls == [{'action': 'query', 'kwargs': {'q': 'cats'}}]


def _make_actionless_module(name='take_photo', tier=None):
    """A write-tier module with NO 'action' parameter (like take_screenshot)."""
    from modules.base import ActionTier, PebbleModule

    class _Cam(PebbleModule):
        _default_tiers = {'capture': tier or ActionTier.NOTIFY}

        def __init__(self):
            super().__init__({'enabled': True})
            self.calls = []

        def tool_name(self): return name
        def tool_description(self): return 'capture'
        def tool_parameters(self):
            return {'type': 'object',
                    'properties': {'region': {'type': 'string'}}, 'required': []}

        def execute(self, region='full', **_):
            self.calls.append(region)
            return 'captured'

    return _Cam()


def test_actionless_notify_write_is_dry_run_safe(pebble_home):
    """A NOTIFY tool with no 'action' param (take_screenshot-style) must NOT
    execute in dry-run — it must route through autonomy, not the AUTO fast path."""
    import dry_run
    dry_run.set_enabled(True)
    try:
        mod = _make_actionless_module()
        o = _orchestrator_with(mod)
        result = o._run('take_photo', {'region': 'full'})   # NO 'action' key
        assert mod.calls == []                 # live capture must NOT fire under dry-run
        assert 'dry-run' in result.lower()
    finally:
        dry_run.set_enabled(False)


def test_actionless_notify_write_is_routed_and_audited(pebble_home):
    """Live (non-dry-run): the actionless NOTIFY write executes but via autonomy,
    so it is audited with source=chat (no longer a raw AUTO execute)."""
    import audit
    import first_time_ledger as ledger
    mod = _make_actionless_module()
    ledger.record(ledger.make_key('take_photo', 'capture'))  # steady-state NOTIFY
    o = _orchestrator_with(mod)
    o._run('take_photo', {'region': 'full'})
    assert mod.calls == ['full']
    chat_rows = [r for r in audit.tail(20)
                 if r.get('source') == 'chat' and r.get('module') == 'take_photo']
    assert chat_rows and chat_rows[-1]['tier'] == 'notify'


def test_actionless_ask_write_is_refused_from_chat(pebble_home):
    """An actionless ASK-tier tool is still refused from chat (no bypass)."""
    from modules.base import ActionTier
    mod = _make_actionless_module('danger', tier=ActionTier.ASK)
    o = _orchestrator_with(mod)
    result = o._run('danger', {'region': 'full'})
    assert 'Refusing' in result or 'requires explicit approval' in result
    assert mod.calls == []


def test_unknown_tool_returns_error_without_audit(pebble_home):
    o = _orchestrator_with(_make_module('probe'))
    result = o._run('nonexistent', {})
    assert 'Unknown tool' in result
