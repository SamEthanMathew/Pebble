"""School planner: status doc generation with mocked LLM."""

from __future__ import annotations

import json


def _setup_planner_model():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def test_school_planner_writes_status_doc(pebble_home, mock_backend, monkeypatch):
    _setup_planner_model()
    import entity_store
    from planners import school as school_module

    entity_store.add('course', '15-122', aliases=['pic'],
                     payload={'professor': 'Saquib', 'exam_dates': []})
    entity_store.add('course', '21-122', aliases=['calc'])

    monkeypatch.setattr(school_module.SchoolPlanner, '_read_canvas', lambda self: [])
    monkeypatch.setattr(school_module.SchoolPlanner, '_read_obsidian_summary', lambda self: {})

    mock_backend.set_response(json.dumps({
        'courses': [
            {'code': '15-122',
             'next_deadline': {'title': 'HW3', 'due': '2026-05-13T23:59Z', 'progress_pct': 30},
             'exam_plan': None},
        ],
        'exam_plans': [],
    }))

    payload = school_module.SchoolPlanner().run()
    assert payload is not None
    assert 'courses' in payload
    assert 'exam_plans' in payload
    assert payload['courses'][0]['code'] == '15-122'

    from planners import read_state_doc
    env = read_state_doc('school_status.json')
    assert env and env['generated_by'] == 'school'


def test_school_planner_picks_up_upcoming_exams_from_entities(pebble_home, mock_backend, monkeypatch):
    """If a Course entity has exam_dates, the input collection surfaces them."""
    _setup_planner_model()
    import entity_store, datetime
    from planners import school as school_module

    in_5_days = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    entity_store.add('course', '15-122', aliases=[], payload={'exam_dates': [in_5_days]})

    monkeypatch.setattr(school_module.SchoolPlanner, '_read_canvas', lambda self: [])

    p = school_module.SchoolPlanner()
    inputs = p.collect_inputs()
    assert any(e['course'] == '15-122' for e in inputs['upcoming_exams'])


def test_school_planner_handles_no_courses(pebble_home, mock_backend, monkeypatch):
    _setup_planner_model()
    from planners import school as school_module

    monkeypatch.setattr(school_module.SchoolPlanner, '_read_canvas', lambda self: [])
    mock_backend.set_response(json.dumps({'courses': [], 'exam_plans': []}))

    payload = school_module.SchoolPlanner().run()
    assert payload == {'courses': [], 'exam_plans': []}
