"""Daily wrap-up: text generation, journal append, audit shape."""

from __future__ import annotations

import datetime


def _setup():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def test_wrapup_generates_text(pebble_home, mock_backend, monkeypatch):
    _setup()
    from planners import wrapup as wm
    monkeypatch.setattr(wm, '_tomorrow_schedule_text', lambda: '(stub)')
    mock_backend.set_response('Today: 3 things shipped. Tomorrow: focus on HW3.')

    text = wm.generate_wrapup(append_to_journal=False)
    assert text and 'Today' in text


def test_wrapup_appends_to_journal(pebble_home, mock_backend, monkeypatch):
    _setup()
    from planners import wrapup as wm
    monkeypatch.setattr(wm, '_tomorrow_schedule_text', lambda: '(stub)')
    mock_backend.set_response('wrap text')

    wm.generate_wrapup(append_to_journal=True)
    today = datetime.date.today().isoformat()
    journal_file = wm._JOURNAL_DIR / f'{today}.md'
    assert journal_file.exists()
    content = journal_file.read_text(encoding='utf-8')
    assert 'Daily wrap-up' in content
    assert 'wrap text' in content


def test_wrapup_disabled_without_model(pebble_home, capsys):
    from planners import wrapup as wm
    text = wm.generate_wrapup(append_to_journal=False)
    assert text is None
    err = capsys.readouterr().err
    assert 'no planner_model' in err.lower() or 'disabled' in err.lower()


def test_wrapup_audit_recorded(pebble_home, mock_backend, monkeypatch):
    _setup()
    from planners import wrapup as wm
    monkeypatch.setattr(wm, '_tomorrow_schedule_text', lambda: '(stub)')
    mock_backend.set_response('text')

    wm.generate_wrapup(append_to_journal=False)
    import audit
    rows = audit.tail(20)
    assert any(r.get('module') == 'daily_wrapup' and r.get('action') == 'generated'
               for r in rows)


def test_wrapup_reads_audit_today(pebble_home, mock_backend, monkeypatch):
    """The audit_today slot should reflect actions taken today."""
    _setup()
    import audit
    audit.append({'module': 'tasks', 'action': 'complete', 'source': 'user'})

    from planners import wrapup as wm
    monkeypatch.setattr(wm, '_tomorrow_schedule_text', lambda: '(stub)')
    mock_backend.set_response('summary')

    wm.generate_wrapup(append_to_journal=False)
    # The system prompt was rendered with audit data — assert through mock_backend.calls
    call = mock_backend.calls[-1]
    assert 'tasks.complete' in call['system']
