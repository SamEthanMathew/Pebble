"""Autonomy layer + first-time ledger tests."""

from __future__ import annotations

import pytest


def _make_module(name='probe', default_tiers=None, send_target='to'):
    """Build a tiny PebbleModule for autonomy tests."""
    from modules.base import PebbleModule, ActionTier

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
            return f'executed {action} with {kwargs}'

        def outbound_target_id(self, action_name, args):
            if action_name == 'send':
                return args.get(send_target)
            return None

    return _Probe()


def test_ledger_first_time_then_remember(pebble_home):
    import first_time_ledger as ledger
    assert ledger.is_first_time('gmail.draft')
    ledger.record('gmail.draft')
    assert not ledger.is_first_time('gmail.draft')
    info = ledger.info('gmail.draft')
    assert info['count'] == 1
    ledger.record('gmail.draft')
    assert ledger.info('gmail.draft')['count'] == 2


def test_ledger_per_target_for_outbound(pebble_home):
    import first_time_ledger as ledger
    ledger.record(ledger.make_key('gmail', 'send', 'a@b.com'))
    assert not ledger.is_first_time('gmail.send:a@b.com')
    # Different recipient = still first-time
    assert ledger.is_first_time('gmail.send:c@d.com')


def test_autonomy_auto_tier_executes(pebble_home):
    """An action declared AUTO with no first-time gate runs immediately."""
    import first_time_ledger as ledger
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('probe', {'lookup': ActionTier.AUTO})
    # Pre-record so first-time gate doesn't fire
    ledger.record(ledger.make_key('probe', 'lookup'))

    a = Autonomy(modules=[mod])
    out = a.route(Proposal(module='probe', action='lookup', args={'q': 'x'},
                            source='test', urgency='normal', reversible=True))
    assert out.status == 'executed_auto'
    assert mod.calls == [{'action': 'lookup', 'kwargs': {'q': 'x'}}]


def test_autonomy_first_time_forces_ask_even_for_auto(pebble_home):
    """First-time keys force ASK regardless of declared tier."""
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('probe', {'lookup': ActionTier.AUTO})

    asked_with: list = []

    def approve(proposal):
        asked_with.append(proposal)
        return True

    a = Autonomy(modules=[mod], approval_handler=approve)
    out = a.route(Proposal(module='probe', action='lookup', args={'q': 'x'},
                            source='test', urgency='normal', reversible=True))
    # Should have asked, then executed
    assert len(asked_with) == 1
    assert out.status == 'executed_ask'
    assert out.was_first_time is True
    assert out.user_approved is True

    # Second call: ledger now has the key, so AUTO tier applies
    out2 = a.route(Proposal(module='probe', action='lookup', args={'q': 'y'},
                             source='test', urgency='normal', reversible=True))
    assert len(asked_with) == 1  # didn't ask again
    assert out2.status == 'executed_auto'
    assert out2.was_first_time is False


def test_autonomy_outbound_per_target_first_time(pebble_home):
    """Even after the first send, a NEW recipient triggers ask again."""
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('email', {'send': ActionTier.ASK})

    ask_count = [0]
    def approve(proposal):
        ask_count[0] += 1
        return True

    a = Autonomy(modules=[mod], approval_handler=approve)

    # First send to a@b — asks
    a.route(Proposal(module='email', action='send', args={'to': 'a@b'},
                      target_id='a@b', source='t'))
    assert ask_count[0] == 1

    # Second send to a@b — declared ASK still asks
    a.route(Proposal(module='email', action='send', args={'to': 'a@b'},
                      target_id='a@b', source='t'))
    assert ask_count[0] == 2

    # New target c@d — still asks (per-target gate)
    a.route(Proposal(module='email', action='send', args={'to': 'c@d'},
                      target_id='c@d', source='t'))
    assert ask_count[0] == 3


def test_autonomy_ask_without_handler_queues(pebble_home):
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('probe', {'do': ActionTier.ASK})
    a = Autonomy(modules=[mod])  # no approval_handler

    out = a.route(Proposal(module='probe', action='do', args={'k': 'v'}, source='t'))
    assert out.status == 'awaits_approval'
    assert out.proposal_id in a.pending_proposals()

    # Approve via the queue API
    out2 = a.approve_pending(out.proposal_id)
    assert out2.status == 'executed_ask'
    assert mod.calls and mod.calls[0]['action'] == 'do'


def test_autonomy_deny_pending(pebble_home):
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('probe', {'do': ActionTier.ASK})
    a = Autonomy(modules=[mod])

    out = a.route(Proposal(module='probe', action='do', args={}, source='t'))
    out2 = a.deny_pending(out.proposal_id)
    assert out2.status == 'denied'
    assert mod.calls == []


def test_autonomy_dry_run_writes_preview_no_execute(pebble_home):
    """In dry-run, NEVER call execute(); always write preview + audit was_dry_run."""
    import dry_run, first_time_ledger as ledger
    from modules.base import ActionTier
    from autonomy import Autonomy
    from planners.base import Proposal

    mod = _make_module('probe', {'do': ActionTier.AUTO})
    ledger.record(ledger.make_key('probe', 'do'))  # bypass first-time

    dry_run.set_enabled(True)
    a = Autonomy(modules=[mod])
    out = a.route(Proposal(module='probe', action='do', args={'x': 1}, source='t'))
    dry_run.set_enabled(False)

    assert out.status == 'dry_run'
    assert out.preview_path is not None
    assert mod.calls == []  # execute NOT called

    # Audit row says was_dry_run=true
    import audit
    rows = audit.tail(10)
    assert any(r.get('was_dry_run') is True for r in rows)


def test_autonomy_unknown_module(pebble_home):
    from autonomy import Autonomy
    from planners.base import Proposal
    a = Autonomy(modules=[])
    out = a.route(Proposal(module='nonexistent', action='x', args={}, source='t'))
    assert out.status == 'error'
    assert 'Unknown module' in (out.error or '')


def test_approval_queue_fires_after_delay(pebble_home):
    """fire_now bypasses the threading.Timer for deterministic tests."""
    from approval_queue import ApprovalQueue
    q = ApprovalQueue(delay_seconds=60)

    fire_count = [0]
    def fn():
        fire_count[0] += 1
        return 'sent'

    item_id = q.enqueue(fn, label='Gmail send to a@b', metadata={'to': 'a@b'})
    assert q.status(item_id) == 'pending'
    assert q.fire_now(item_id) is True
    assert q.status(item_id) == 'fired'
    assert fire_count[0] == 1


def test_approval_queue_cancel_before_fire(pebble_home):
    from approval_queue import ApprovalQueue
    q = ApprovalQueue(delay_seconds=60)

    fire_count = [0]
    item_id = q.enqueue(lambda: fire_count.__setitem__(0, fire_count[0] + 1), label='X')
    assert q.cancel(item_id) is True
    assert q.status(item_id) == 'canceled'
    assert q.fire_now(item_id) is False  # already canceled
    assert fire_count[0] == 0


def test_approval_queue_pending_list(pebble_home):
    from approval_queue import ApprovalQueue
    q = ApprovalQueue(delay_seconds=60)
    q.enqueue(lambda: None, label='A')
    q.enqueue(lambda: None, label='B')
    assert len(q.pending()) == 2
