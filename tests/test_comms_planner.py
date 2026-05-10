"""Comms planner: triage end-to-end with mocked LLM, sender heuristics."""

from __future__ import annotations

import json


def _setup_planner_model():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def test_likely_important_unknown_cmu_domain(pebble_home):
    from planners.comms import is_likely_important_unknown
    msg = {'from_name': 'Prof Smith', 'from_email': 'smith@cs.cmu.edu', 'subject': 'HW3'}
    assert is_likely_important_unknown(msg) is True


def test_likely_important_unknown_no_display_name(pebble_home):
    from planners.comms import is_likely_important_unknown
    msg = {'from_name': '', 'from_email': 'someone@cmu.edu', 'subject': 'X'}
    assert is_likely_important_unknown(msg) is False


def test_likely_important_unknown_newsletter_unsubscribe_header(pebble_home):
    from planners.comms import is_likely_important_unknown
    msg = {'from_name': 'Big Newsletter', 'from_email': 'noreply@cmu.edu',
           'headers': {'List-Unsubscribe': '<https://...>'}}
    assert is_likely_important_unknown(msg) is False


def test_likely_important_unknown_random_domain_no_prior_thread(pebble_home):
    from planners.comms import is_likely_important_unknown
    msg = {'from_name': 'Random Person', 'from_email': 'rando@example.com'}
    assert is_likely_important_unknown(msg) is False


def test_likely_important_unknown_prior_thread_count(pebble_home):
    from planners.comms import is_likely_important_unknown
    msg = {'from_name': 'Random', 'from_email': 'rando@example.com',
           'thread_message_count': 5}
    assert is_likely_important_unknown(msg) is True


def test_comms_planner_writes_state_doc(pebble_home, mock_backend, monkeypatch, sample_inbox):
    _setup_planner_model()
    from planners import comms as comms_module

    monkeypatch.setattr(comms_module.CommsPlanner, '_read_recent_unread',
                        lambda self, limit=20: sample_inbox)

    mock_backend.set_response(json.dumps({
        'action_required': [
            {'message_id': 'm1', 'from': {'email': 'professor@cmu.edu', 'name': 'Prof Smith',
                                           'entity_ref': None},
             'subject': 'HW3 office hours', 'summary': 'Wants to meet about HW3',
             'urgency': 'high'},
            {'message_id': 'm3', 'from': {'email': 'recruiter@biggcorp.com', 'name': 'Anna',
                                           'entity_ref': None},
             'subject': 'SWE Intern role', 'summary': 'Recruiter outreach',
             'urgency': 'normal'},
        ],
        'fyi': [],
        'ignore_count': 1,
    }))

    payload = comms_module.CommsPlanner().run()
    assert payload is not None
    assert len(payload['action_required']) == 2
    assert payload['ignore_count'] == 1

    from planners import read_state_doc
    env = read_state_doc('comms_pending.json')
    assert env and env['generated_by'] == 'comms'


def test_comms_planner_strips_drafts_in_phase2(pebble_home, mock_backend, monkeypatch, sample_inbox):
    """Phase 2 = triage only. If the LLM speculatively returns a draft, we strip it."""
    _setup_planner_model()
    from planners import comms as comms_module

    monkeypatch.setattr(comms_module.CommsPlanner, '_read_recent_unread',
                        lambda self, limit=20: sample_inbox)

    mock_backend.set_response(json.dumps({
        'action_required': [
            {'message_id': 'm1', 'from': {'email': 'a@b.com'}, 'subject': 'X',
             'summary': '...', 'draft': 'Hey, sounds good!', 'urgency': 'normal'},
        ],
        'fyi': [],
        'ignore_count': 0,
    }))

    payload = comms_module.CommsPlanner().run()
    assert payload is not None
    assert 'draft' not in payload['action_required'][0]


def test_comms_planner_handles_empty_inbox(pebble_home, mock_backend, monkeypatch):
    _setup_planner_model()
    from planners import comms as comms_module

    monkeypatch.setattr(comms_module.CommsPlanner, '_read_recent_unread',
                        lambda self, limit=20: [])
    mock_backend.set_response(json.dumps({
        'action_required': [], 'fyi': [], 'ignore_count': 0,
    }))

    payload = comms_module.CommsPlanner().run()
    assert payload == {'action_required': [], 'fyi': [], 'ignore_count': 0}
