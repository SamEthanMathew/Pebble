"""Comms draft activation (Phase 3): drafts kept in payload, proposals generated."""

from __future__ import annotations

import json
import pytest


def _setup():
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])


def test_drafts_stripped_when_disabled(pebble_home, mock_backend, monkeypatch):
    """Default (no comms.draft_enabled) → drafts stripped from payload."""
    _setup()
    from planners import comms as comms_module

    monkeypatch.setattr(comms_module.CommsPlanner, '_read_recent_unread',
                        lambda self, limit=20: [])

    mock_backend.set_response(json.dumps({
        'action_required': [{'message_id': 'm1', 'from': {'email': 'a@b'},
                              'subject': 'X', 'summary': '...',
                              'draft': 'Sounds great!', 'urgency': 'normal'}],
        'fyi': [], 'ignore_count': 0,
    }))

    payload = comms_module.CommsPlanner().run()
    assert 'draft' not in payload['action_required'][0]


def test_drafts_kept_when_enabled(pebble_home, mock_backend, monkeypatch):
    _setup()
    import crab_config
    crab_config.set_value('comms', {'draft_enabled': True})

    from planners import comms as comms_module
    # Reload so _draft_enabled() picks up the fresh config
    import importlib
    importlib.reload(comms_module)

    monkeypatch.setattr(comms_module.CommsPlanner, '_read_recent_unread',
                        lambda self, limit=20: [])

    mock_backend.set_response(json.dumps({
        'action_required': [{'message_id': 'm1', 'from': {'email': 'a@b'},
                              'subject': 'X', 'summary': '...',
                              'draft': 'Sounds great!', 'urgency': 'normal'}],
        'fyi': [], 'ignore_count': 0,
    }))

    payload = comms_module.CommsPlanner().run()
    assert payload['action_required'][0]['draft'] == 'Sounds great!'


def test_draft_proposals_generated(pebble_home, monkeypatch):
    """When enabled, draft_proposals() yields Proposal objects for each draft."""
    import crab_config
    crab_config.set_value('comms', {'draft_enabled': True})

    from planners import comms as comms_module
    import importlib; importlib.reload(comms_module)

    payload = {
        'action_required': [
            {'message_id': 'm1', 'thread_id': 't1',
             'from': {'email': 'professor@cmu.edu', 'name': 'Prof Smith'},
             'subject': 'Office hours', 'summary': 'Meeting request',
             'draft': 'Tomorrow at 3 works.', 'urgency': 'high'},
            {'message_id': 'm2', 'from': {'email': 'random@x.com'},
             'subject': 'Hi',     'summary': '...',
             'urgency': 'low'},   # no draft → no proposal
        ],
        'fyi': [], 'ignore_count': 0,
    }

    proposals = comms_module.CommsPlanner().draft_proposals(payload)
    assert len(proposals) == 1
    p = proposals[0]
    assert p.module == 'gmail'
    assert p.action == 'draft'
    assert p.target_id == 'professor@cmu.edu'
    assert p.args['to'] == 'professor@cmu.edu'
    assert p.args['subject'].startswith('Re: ')
    assert 'Tomorrow at 3' in p.args['body']
    assert p.urgency == 'high'


def test_no_proposals_when_disabled(pebble_home):
    """draft_enabled=False → draft_proposals() returns []."""
    from planners import comms as comms_module
    payload = {
        'action_required': [
            {'message_id': 'm', 'from': {'email': 'a@b'}, 'subject': 'X',
             'summary': '...', 'draft': 'hi', 'urgency': 'low'},
        ],
        'fyi': [], 'ignore_count': 0,
    }
    assert comms_module.CommsPlanner().draft_proposals(payload) == []
