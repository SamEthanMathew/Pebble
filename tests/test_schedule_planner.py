"""Schedule planner: snapshot-style test against a fixture using mocked LLM."""

from __future__ import annotations

import json


def _setup_planner_model():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def test_schedule_planner_writes_state_doc(pebble_home, mock_backend, monkeypatch):
    """Mock the LLM, run the planner, assert state doc payload matches schema."""
    _setup_planner_model()

    # Mock the GCal read to avoid touching real Google APIs
    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_gcal_events_today',
                        lambda self: [
                            {'id': 'e1', 'title': 'Lecture', 'start': '2026-05-10T09:00',
                             'end': '2026-05-10T10:30', 'location': 'GHC 4307',
                             'attendees_count': 1},
                        ])
    # No real tasks file
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_tasks_with_deadlines',
                        lambda self: [{'id': 1, 'text': 'HW3', 'due': '2026-05-12'}])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_entity_context',
                        lambda self: [{'type': 'course', 'name': '15-122',
                                       'aliases': ['pic'], 'payload': {}}])

    # Mock LLM to return a valid JSON payload
    mock_backend.set_response(json.dumps({
        'date': '2026-05-10',
        'blocks': [
            {'start': '09:00', 'end': '10:30', 'kind': 'class',
             'title': 'Lecture', 'entity_ref': 'course:15-122'},
        ],
        'free_windows': [
            {'start': '14:00', 'end': '16:00',
             'suggested_use': 'HW3 work', 'rationale': 'Due in 2 days, no work blocks yet'},
        ],
        'conflicts': [],
        'transitions': [],
    }))

    payload = schedule_module.SchedulePlanner().run()

    assert payload is not None
    assert 'blocks' in payload
    assert 'free_windows' in payload
    assert payload['blocks'][0]['title'] == 'Lecture'

    from planners import read_state_doc
    env = read_state_doc('schedule_today.json')
    assert env is not None
    assert env['generated_by'] == 'schedule'
    assert env['ttl_seconds'] == 3600


def test_schedule_planner_handles_code_fenced_json(pebble_home, mock_backend, monkeypatch):
    """LLMs often wrap JSON in ```json … ``` — planner must strip the fence."""
    _setup_planner_model()

    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_gcal_events_today', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_tasks_with_deadlines', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_entity_context', lambda self: [])

    fenced = '```json\n{"date": "2026-05-10", "blocks": [], "free_windows": [], "conflicts": [], "transitions": []}\n```'
    mock_backend.set_response(fenced)

    payload = schedule_module.SchedulePlanner().run()
    assert payload is not None
    assert payload['date'] == '2026-05-10'


def test_schedule_planner_parse_failure_logs_audit(pebble_home, mock_backend, monkeypatch):
    """Non-JSON LLM output → planner skips with audit and parse_failed metric."""
    _setup_planner_model()

    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_gcal_events_today', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_tasks_with_deadlines', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_entity_context', lambda self: [])

    mock_backend.set_response('I cannot complete this request.')

    out = schedule_module.SchedulePlanner().run()
    assert out is None

    import audit
    rows = audit.tail(20)
    assert any(r.get('action') == 'parse_failed' for r in rows)


def test_schedule_planner_disabled_without_planner_model(pebble_home, mock_backend, monkeypatch):
    """Cloud-only contract: skip silently when planner_model is missing."""
    # Note: NO _setup_planner_model() call — config is empty
    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_gcal_events_today', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_tasks_with_deadlines', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_entity_context', lambda self: [])

    out = schedule_module.SchedulePlanner().run()
    assert out is None
    # Mock backend should NOT have been called
    assert len(mock_backend.calls) == 0


def test_schedule_planner_rerun_gate_skips_unchanged(pebble_home, mock_backend, monkeypatch):
    _setup_planner_model()

    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_gcal_events_today',
                        lambda self: [{'id': 'e', 'title': 'X', 'start': 's', 'end': 'e',
                                        'location': '', 'attendees_count': 0}])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_tasks_with_deadlines', lambda self: [])
    monkeypatch.setattr(schedule_module.SchedulePlanner, '_read_entity_context', lambda self: [])

    mock_backend.set_response(json.dumps({
        'date': '2026-05-10', 'blocks': [], 'free_windows': [],
        'conflicts': [], 'transitions': [],
    }))

    p = schedule_module.SchedulePlanner()
    p.run()
    assert len(mock_backend.calls) == 1
    p.run()  # gate skips
    assert len(mock_backend.calls) == 1
