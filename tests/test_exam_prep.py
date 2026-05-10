"""Exam prep chain — generate plan, persist, idempotent updates."""

from __future__ import annotations

import datetime
import json


def _setup():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def _course():
    """A lightweight stand-in for entity_store.Entity."""
    class _E:
        name = '15-122'
        payload = {'code': '15-122', 'topics': 'arrays, BSTs, sorting',
                   'course_site_url': None}
    return _E()


def test_generate_for_course_writes_plan(pebble_home, mock_backend):
    _setup()
    from planners import exam_prep

    in_5_days = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    mock_backend.set_response(json.dumps({
        'course': '15-122', 'exam_date': in_5_days,
        'plan': [
            {'date': in_5_days, 'block': '14:00-16:00',
             'topic': 'BSTs', 'action': 'Review lecture 14',
             'resources': ['lec14.pdf']},
        ],
    }))

    out = exam_prep.generate_for_course(_course(), in_5_days)
    assert out is not None
    assert out['course'] == '15-122'
    assert len(out['plan']) == 1

    saved = exam_prep.list_plans()
    assert len(saved) == 1
    assert saved[0]['course'] == '15-122'


def test_generate_for_past_exam_returns_none(pebble_home, mock_backend):
    _setup()
    from planners import exam_prep
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    out = exam_prep.generate_for_course(_course(), yesterday)
    assert out is None


def test_generate_disabled_without_planner_model(pebble_home, capsys):
    """Cloud-only contract."""
    from planners import exam_prep
    in_3 = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    out = exam_prep.generate_for_course(_course(), in_3)
    assert out is None
    err = capsys.readouterr().err
    assert 'no planner_model' in err.lower() or 'disabled' in err.lower()


def test_idempotent_replace_by_course_and_date(pebble_home, mock_backend):
    """Re-running for the same (course, date) replaces, doesn't duplicate."""
    _setup()
    from planners import exam_prep
    in_5 = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()

    mock_backend.set_response(json.dumps({'course': '15-122', 'exam_date': in_5,
                                           'plan': [{'topic': 'A'}]}))
    exam_prep.generate_for_course(_course(), in_5)

    mock_backend.set_response(json.dumps({'course': '15-122', 'exam_date': in_5,
                                           'plan': [{'topic': 'B'}]}))
    exam_prep.generate_for_course(_course(), in_5)

    plans = exam_prep.list_plans()
    assert len(plans) == 1
    assert plans[0]['plan'][0]['topic'] == 'B'


def test_parse_failure_audits_and_returns_none(pebble_home, mock_backend):
    _setup()
    from planners import exam_prep
    mock_backend.set_response('not json at all')
    out = exam_prep.generate_for_course(_course(),
                                         (datetime.date.today() + datetime.timedelta(days=4)).isoformat())
    assert out is None
    import audit
    assert any(r.get('action') == 'parse_failed' for r in audit.tail(20))
