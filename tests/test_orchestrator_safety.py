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
    from modules.base import ActionTier
    mod = _make_module('todo', {'add': ActionTier.NOTIFY})
    o = _orchestrator_with(mod)
    o._run('todo', {'action': 'add', 'text': 'buy milk'})
    rows = audit.tail(20)
    chat_rows = [r for r in rows
                 if r.get('source') == 'chat' and r.get('module') == 'todo']
    assert chat_rows, 'NOTIFY-tier chat call should write an audit row'
    assert chat_rows[-1]['tier'] == 'notify'
    assert mod.calls == [{'action': 'add', 'kwargs': {'text': 'buy milk'}}]


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


def test_unknown_tool_returns_error_without_audit(pebble_home):
    o = _orchestrator_with(_make_module('probe'))
    result = o._run('nonexistent', {})
    assert 'Unknown tool' in result
