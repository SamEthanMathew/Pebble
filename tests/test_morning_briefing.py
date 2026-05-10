"""Morning briefing v2 — reads three state docs, calls LLM, returns text."""

from __future__ import annotations


def _setup_planner_model():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def _seed_state_docs():
    """Seed all three planner state docs so morning briefing has something to read."""
    from planners.base import write_state_doc
    write_state_doc(name='schedule_today.json', generated_by='schedule',
                    ttl_seconds=3600, input_hash_str='h',
                    payload={'date': '2026-05-10',
                             'blocks': [{'start': '09:00', 'end': '10:30',
                                         'title': '15-122 Lecture'}],
                             'free_windows': [], 'conflicts': [], 'transitions': []})
    write_state_doc(name='comms_pending.json', generated_by='comms',
                    ttl_seconds=3600, input_hash_str='h',
                    payload={'action_required': [], 'fyi': [], 'ignore_count': 3})
    write_state_doc(name='school_status.json', generated_by='school',
                    ttl_seconds=3600, input_hash_str='h',
                    payload={'courses': [], 'exam_plans': []})


def test_morning_briefing_returns_text(pebble_home, mock_backend):
    _setup_planner_model()
    _seed_state_docs()

    from planners.morning import generate_briefing
    mock_backend.set_response('Good morning! Today: 15-122 lecture at 9. Free 14-16. Triage clean.')

    text = generate_briefing(refresh_planners=False, weather_summary='clear · 52°F',
                              overdue_tasks='none')
    assert text and 'Good morning' in text


def test_morning_briefing_disabled_without_planner_model(pebble_home, mock_backend, capsys):
    _seed_state_docs()  # no planner_model configured
    from planners.morning import generate_briefing
    text = generate_briefing(refresh_planners=False)
    assert text is None
    err = capsys.readouterr().err
    assert 'no planner_model' in err.lower() or 'disabled' in err.lower()


def test_morning_briefing_handles_missing_state_docs(pebble_home, mock_backend):
    """If state docs don't exist yet, the briefing still runs — just with empty payloads."""
    _setup_planner_model()
    # No _seed_state_docs() — state docs don't exist
    from planners.morning import generate_briefing
    mock_backend.set_response('Sparse briefing — no upstream data yet.')
    text = generate_briefing(refresh_planners=False)
    assert text is not None
    assert 'Sparse' in text


def test_morning_briefing_audits(pebble_home, mock_backend):
    _setup_planner_model()
    _seed_state_docs()
    from planners.morning import generate_briefing
    mock_backend.set_response('briefing text')

    generate_briefing(refresh_planners=False)

    import audit
    rows = audit.tail(20)
    assert any(r.get('module') == 'morning_briefing' and r.get('action') == 'generated'
               for r in rows)
