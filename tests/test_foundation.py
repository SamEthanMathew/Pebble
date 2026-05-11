"""Smoke + correctness tests for the Phase 1 foundation: atomic_io, audit, dry_run, metrics."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest


def test_atomic_io_writes_and_reads(pebble_home):
    import atomic_io
    target = pebble_home / 'test.json'
    atomic_io.write_json(target, {'a': 1, 'b': [2, 3]})
    assert target.exists()
    assert atomic_io.read_json(target) == {'a': 1, 'b': [2, 3]}


def test_atomic_io_no_partial_files_on_failure(pebble_home):
    """If serialization fails the target must not exist with garbage."""
    import atomic_io
    target = pebble_home / 'test.json'

    # Circular reference — json can't serialize even with default=str
    a: dict = {}
    a['self'] = a

    with pytest.raises(ValueError):  # json raises ValueError on circular refs
        atomic_io.write_json(target, a)
    # Target should NOT have been created (write went to .tmp, never renamed)
    assert not target.exists()


def test_atomic_io_concurrent_safe(pebble_home):
    """20 concurrent writers must leave the file valid JSON, never half-written."""
    import atomic_io
    target = pebble_home / 'concurrent.json'

    def write(i: int):
        atomic_io.write_json(target, {'writer': i, 'value': list(range(50))})

    threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whatever winner is, the file must parse cleanly
    parsed = atomic_io.read_json(target)
    assert parsed is not None
    assert 'writer' in parsed
    assert parsed['value'] == list(range(50))


def test_audit_append_and_tail(pebble_home):
    import audit
    audit.append({'module': 'x', 'action': 'y', 'tier': 'auto', 'source': 'test'})
    audit.append({'module': 'x', 'action': 'z', 'tier': 'notify', 'source': 'test'})
    rows = audit.tail(10)
    assert len(rows) == 2
    assert rows[0]['action'] == 'y'
    assert rows[1]['action'] == 'z'
    assert rows[0]['timestamp']  # auto-filled
    assert rows[0]['was_dry_run'] is False  # default
    assert rows[0]['was_first_time'] is False  # default


def test_audit_survives_malformed_lines(pebble_home):
    import audit
    audit.append({'module': 'a', 'action': 'b'})
    # Sneak in garbage
    with audit.path().open('a', encoding='utf-8') as f:
        f.write('this is not json\n')
    audit.append({'module': 'c', 'action': 'd'})
    rows = audit.tail(10)
    # Garbage line is skipped silently
    assert len(rows) == 2
    assert {r['action'] for r in rows} == {'b', 'd'}


def test_audit_failure_does_not_raise(pebble_home, monkeypatch):
    """append must not crash callers even if the write fails."""
    import audit
    monkeypatch.setattr(audit, '_AUDIT_PATH', Path('/nonexistent_root/audit.jsonl'))
    # Should not raise
    audit.append({'module': 'x', 'action': 'y'})


def test_dry_run_writes_preview(pebble_home):
    import dry_run
    p = dry_run.write_preview({
        'module': 'gmail',
        'action': 'draft',
        'args':   {'to': 'a@b', 'subject': 's'},
        'source': 'comms_planner',
        'tier':   'notify',
    })
    assert p.exists()
    data = json.loads(p.read_text(encoding='utf-8'))
    assert data['module'] == 'gmail'
    assert data['action'] == 'draft'
    assert data['args']['to'] == 'a@b'


def test_dry_run_list_previews(pebble_home):
    import dry_run
    for i in range(3):
        dry_run.write_preview({'module': 'm', 'action': f'a{i}', 'args': {}, 'source': 'test', 'tier': 'notify'})
    out = dry_run.list_previews()
    assert len(out) == 3


def test_dry_run_clear_previews(pebble_home):
    import dry_run
    dry_run.write_preview({'module': 'm', 'action': 'a', 'args': {}, 'source': 'test', 'tier': 'notify'})
    n = dry_run.clear_previews()
    assert n == 1
    assert dry_run.list_previews() == []


def test_metrics_emit(pebble_home):
    import metrics, json
    metrics.emit('notification.fired', {'kind': 'meeting_prep', 'id': 'abc'})
    metrics.emit('notification.acted', {'id': 'abc', 'latency_ms': 1200})
    lines = metrics.path().read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 2
    e0 = json.loads(lines[0])
    e1 = json.loads(lines[1])
    assert e0['event'] == 'notification.fired'
    assert e1['props']['latency_ms'] == 1200


def test_metrics_failure_does_not_raise(pebble_home, monkeypatch):
    import metrics
    monkeypatch.setattr(metrics, '_METRICS_PATH', Path('/nonexistent_root/metrics.jsonl'))
    metrics.emit('x', {'y': 1})  # must not raise


def test_action_tier_enum():
    from modules.base import ActionTier
    assert ActionTier.AUTO.value == 'auto'
    assert ActionTier.NOTIFY.value == 'notify'
    assert ActionTier.ASK.value == 'ask'


def test_action_tier_resolution_default_is_ask(pebble_home):
    """A module with no _default_tiers and no config override resolves to ASK."""
    from modules.base import PebbleModule, ActionTier

    class _Probe(PebbleModule):
        name = 'probe'
        def tool_name(self): return 'probe'
        def tool_description(self): return 'probe'
        def tool_parameters(self): return {}
        def execute(self, **k): return ''

    m = _Probe({})
    assert m.action_tier('unknown_action') == ActionTier.ASK


def test_action_tier_default_then_config_override(pebble_home):
    """Default is honored when no override; config override wins when set."""
    from modules.base import PebbleModule, ActionTier
    import crab_config

    class _Probe(PebbleModule):
        name = 'probe'
        _default_tiers = {'read': ActionTier.AUTO, 'write': ActionTier.NOTIFY}
        def tool_name(self): return 'probe'
        def tool_description(self): return 'probe'
        def tool_parameters(self): return {}
        def execute(self, **k): return ''

    m = _Probe({})
    assert m.action_tier('read')  == ActionTier.AUTO
    assert m.action_tier('write') == ActionTier.NOTIFY

    # Config override
    crab_config.set_value('tiers', {'probe': {'read': 'ask'}})
    assert m.action_tier('read')  == ActionTier.ASK
    assert m.action_tier('write') == ActionTier.NOTIFY  # untouched


def test_outbound_target_id_default_is_none():
    from modules.base import PebbleModule

    class _Probe(PebbleModule):
        def tool_name(self): return 'probe'
        def tool_description(self): return ''
        def tool_parameters(self): return {}
        def execute(self, **k): return ''

    m = _Probe({})
    assert m.outbound_target_id('any_action', {'to': 'x@y'}) is None
