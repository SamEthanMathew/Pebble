"""Weekly feedback report generator tests."""

from __future__ import annotations

import json


def test_report_writes_when_no_activity(pebble_home):
    import feedback
    p = feedback.generate_weekly_report(days=7)
    assert p.exists()
    text = p.read_text(encoding='utf-8')
    assert 'Pebble weekly feedback' in text
    assert 'Audit rows' in text


def test_report_summarizes_activity(pebble_home):
    import audit, metrics, feedback
    audit.append({'module': 'gmail', 'action': 'draft', 'source': 'comms_planner',
                  'was_dry_run': False})
    audit.append({'module': 'gmail', 'action': 'draft', 'source': 'comms_planner',
                  'was_dry_run': True})
    metrics.emit('notification.fired',     {})
    metrics.emit('notification.acted',     {})
    metrics.emit('proposal.received',      {})
    metrics.emit('proposal.approved',      {})

    p = feedback.generate_weekly_report(days=7)
    text = p.read_text(encoding='utf-8')
    assert 'gmail' in text
    assert 'Notifications' in text
    assert 'Proposals' in text
    assert 'Drafts created: **2**' in text


def test_suggestions_flag_low_act_ratio(pebble_home):
    import metrics, feedback
    for _ in range(15):
        metrics.emit('notification.fired', {})
    metrics.emit('notification.acted', {})  # 1/15 ≈ 7% — very low
    feedback.generate_weekly_report(days=7)

    sugg_path = feedback._SUGGESTIONS_PATH
    assert sugg_path.exists()
    lines = sugg_path.read_text(encoding='utf-8').strip().splitlines()
    rows = [json.loads(l) for l in lines]
    assert any(r['kind'] == 'notification_act_ratio_low' for r in rows)


def test_suggestions_flag_parse_failures(pebble_home):
    import metrics, feedback
    for _ in range(3):
        metrics.emit('planner.skipped',
                     {'planner': 'comms', 'gate_reason': 'parse_failed'})
    feedback.generate_weekly_report(days=7)
    rows = [json.loads(l) for l in feedback._SUGGESTIONS_PATH.read_text(encoding='utf-8').strip().splitlines()]
    assert any(r['kind'] == 'planner_parse_failures' and r.get('planner') == 'comms' for r in rows)


def test_no_suggestions_for_quiet_week(pebble_home):
    """A quiet week with healthy ratios produces a report but no suggestion rows."""
    import audit, metrics, feedback
    audit.append({'module': 'tasks', 'action': 'list', 'source': 'user'})
    metrics.emit('notification.fired', {})
    metrics.emit('notification.acted', {})  # 100% act ratio
    feedback.generate_weekly_report(days=7)
    # Either suggestions file doesn't exist OR it's empty
    p = feedback._SUGGESTIONS_PATH
    if p.exists():
        assert not p.read_text(encoding='utf-8').strip()
