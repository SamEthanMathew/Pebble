"""Audit reader + /how-am-i-doing summary."""

from __future__ import annotations

import datetime


def test_audit_tail(pebble_home):
    import audit, audit_reader
    audit.append({'module': 'a', 'action': '1'})
    audit.append({'module': 'b', 'action': '2'})
    rows = audit_reader.audit_tail(10)
    assert [r['action'] for r in rows] == ['1', '2']


def test_audit_filter_by_module(pebble_home):
    import audit, audit_reader
    audit.append({'module': 'gmail', 'action': 'draft'})
    audit.append({'module': 'gcal', 'action': 'create'})
    audit.append({'module': 'gmail', 'action': 'send'})
    rows = audit_reader.audit_filter(module='gmail')
    assert len(rows) == 2
    assert all(r['module'] == 'gmail' for r in rows)


def test_audit_filter_dry_run(pebble_home):
    import audit, audit_reader
    audit.append({'module': 'm', 'action': 'a', 'was_dry_run': True})
    audit.append({'module': 'm', 'action': 'b', 'was_dry_run': False})
    dry = audit_reader.audit_filter(was_dry_run=True)
    live = audit_reader.audit_filter(was_dry_run=False)
    assert len(dry) == 1 and dry[0]['action'] == 'a'
    assert len(live) == 1 and live[0]['action'] == 'b'


def test_audit_since_window(pebble_home):
    import audit, audit_reader, time
    audit.append({'module': 'a', 'action': '1'})
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1)
    time.sleep(1)
    audit.append({'module': 'a', 'action': '2'})
    rows = audit_reader.audit_since(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(milliseconds=500))
    actions = [r['action'] for r in rows]
    assert '2' in actions


def test_metrics_count_by_event(pebble_home):
    import metrics, audit_reader
    metrics.emit('notification.fired', {'k': 'm'})
    metrics.emit('notification.fired', {'k': 'r'})
    metrics.emit('notification.dismissed', {'k': 'm'})
    counts = audit_reader.metrics_count_by_event()
    assert counts.get('notification.fired') == 2
    assert counts.get('notification.dismissed') == 1


def test_how_am_i_doing_summary_shape(pebble_home):
    import audit, metrics, audit_reader
    audit.append({'module': 'gmail',  'action': 'draft', 'source': 'comms_planner',
                  'was_dry_run': False, 'was_first_time': False})
    audit.append({'module': 'gmail',  'action': 'send',  'source': 'autonomy',
                  'was_dry_run': True,  'was_first_time': True})
    audit.append({'module': 'tasks',  'action': 'complete', 'source': 'user',
                  'was_dry_run': False, 'was_first_time': False,
                  'result': {'error': 'nope'}})
    metrics.emit('notification.fired',     {'k': 'meeting'})
    metrics.emit('notification.dismissed', {'k': 'meeting'})
    metrics.emit('planner.skipped', {'planner': 'schedule', 'gate_reason': 'input_unchanged'})

    out = audit_reader.how_am_i_doing(days=30)
    assert out['audit_rows'] == 3
    assert out['live'] == 2
    assert out['dry_run'] == 1
    assert out['first_time'] == 1
    assert out['failures'] == 1
    assert 'gmail' in out['by_module']
    assert out['metric_counts'].get('notification.fired') == 1
    assert out['planner_skips'].get('schedule', {}).get('input_unchanged') == 1


def test_render_summary_produces_markdown(pebble_home):
    import audit, audit_reader
    audit.append({'module': 'a', 'action': 'x', 'source': 'test'})
    summary = audit_reader.how_am_i_doing(days=7)
    text = audit_reader.render_summary(summary)
    assert text.startswith('**How is Pebble doing?**')
    assert 'Audit rows' in text


def test_malformed_lines_skipped(pebble_home):
    import audit, audit_reader
    audit.append({'module': 'a', 'action': '1'})
    with audit_reader._AUDIT_PATH.open('a', encoding='utf-8') as f:
        f.write('this is not json\n')
    audit.append({'module': 'a', 'action': '2'})
    rows = audit_reader.audit_tail(10)
    assert len(rows) == 2  # garbage line silently dropped
