"""End-to-end integration: simulate a slice of a day.

Pushes synthetic events through the bus, runs planners, routes proposals
through autonomy in dry-run mode, and asserts the audit log + state docs
reflect what should have happened.

If this test breaks after a change, real-world behavior likely also breaks.
"""

from __future__ import annotations

import json


def _seed_config():
    """Apply the same defaults the setup wizard would have."""
    import crab_config
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    crab_config.set_value('dry_run', True)
    crab_config.set_value('tiers', {
        'gmail':   {'search': 'auto', 'draft': 'notify', 'send': 'ask'},
        'gcal':    {'list_events': 'auto', 'create_event': 'ask'},
        'tasks':   {'list': 'auto', 'complete': 'notify'},
        'memory':  {'recall': 'auto', 'remember': 'notify'},
        'entities':{'lookup': 'auto', 'add': 'notify', 'delete': 'ask'},
    })
    crab_config.set_value('comms', {'draft_enabled': True})


def test_one_day_slice(pebble_home, mock_backend, monkeypatch, sample_inbox):
    """A morning's events flow through planners → autonomy → audit, all dry-run."""
    _seed_config()

    # ── 1. Schedule planner runs at wake-up ──────────────────────────────────
    from planners import schedule as schedule_module
    monkeypatch.setattr(schedule_module.SchedulePlanner,
                        '_read_gcal_events_today', lambda self: [
        {'id': 'e1', 'title': '15-122 Lecture', 'start': '09:00',
         'end': '10:30', 'location': 'GHC 4307', 'attendees_count': 0},
    ])
    monkeypatch.setattr(schedule_module.SchedulePlanner,
                        '_read_tasks_with_deadlines', lambda self: [
        {'id': 1, 'text': 'HW3', 'due': '2026-05-13'},
    ])
    monkeypatch.setattr(schedule_module.SchedulePlanner,
                        '_read_entity_context', lambda self: [])

    mock_backend.set_response(json.dumps({
        'date': '2026-05-10',
        'blocks': [{'start': '09:00', 'end': '10:30', 'kind': 'class',
                    'title': '15-122 Lecture', 'entity_ref': 'course:15-122'}],
        'free_windows': [{'start': '14:00', 'end': '16:00',
                          'suggested_use': 'HW3 work',
                          'rationale': 'Due in 3 days, no work blocked yet'}],
        'conflicts': [],
        'transitions': [],
    }))
    out = schedule_module.SchedulePlanner().run()
    assert out is not None and out['blocks'][0]['title'] == '15-122 Lecture'

    # ── 2. Email arrives → comms planner triages with a draft ────────────────
    from planners import comms as comms_module
    import importlib; importlib.reload(comms_module)  # picks up draft_enabled
    monkeypatch.setattr(comms_module.CommsPlanner,
                        '_read_recent_unread', lambda self, limit=20: sample_inbox)

    mock_backend.set_response(json.dumps({
        'action_required': [{
            'message_id': 'm1',
            'thread_id':  't1',
            'from':       {'email': 'professor@cmu.edu', 'name': 'Prof Smith'},
            'subject':    'HW3 office hours',
            'summary':    'Wants to meet about HW3',
            'draft':      'Tomorrow at 3pm works.',
            'urgency':    'high',
        }],
        'fyi': [],
        'ignore_count': 1,
    }))
    comms_payload = comms_module.CommsPlanner().run()
    assert comms_payload['action_required'][0]['draft'] == 'Tomorrow at 3pm works.'

    # ── 3. Comms planner emits draft proposals; autonomy routes them ────────
    from autonomy import Autonomy
    from modules.base import PebbleModule, ActionTier

    # Tiny gmail stub so autonomy has something to call
    class _GmailStub(PebbleModule):
        _default_tiers = {'draft': ActionTier.NOTIFY, 'send': ActionTier.ASK}
        def __init__(self):
            super().__init__({'enabled': True})
            self.calls = []
        def tool_name(self): return 'gmail'
        def tool_description(self): return 'gmail stub'
        def tool_parameters(self): return {}
        def execute(self, action='', **kw):
            self.calls.append({'action': action, 'kw': kw})
            return 'ok'
        def outbound_target_id(self, action_name, args):
            return args.get('to') if action_name == 'send' else None

    gmail = _GmailStub()

    # In a real user session, drafting to a recipient happens after
    # the user has approved the first-time `gmail.draft` global ask AND
    # the first-time `gmail.draft:professor@cmu.edu` per-recipient ask.
    # The autonomy first-time gate forces ASK on these. Auto-approve in the
    # simulation so we exercise the full chain.
    approvals = []
    def approve(p):
        approvals.append(p)
        return True

    a = Autonomy(modules=[gmail], approval_handler=approve)

    proposals = comms_module.CommsPlanner().draft_proposals(comms_payload)
    assert len(proposals) == 1
    result = a.route(proposals[0])

    # Dry-run → preview written, real execute NEVER called
    assert result.status == 'dry_run'
    assert result.preview_path is not None
    assert gmail.calls == []  # NEVER called the live API in dry-run
    assert approvals  # first-time ask fired

    # ── 4. Audit chain looks coherent ────────────────────────────────────────
    import audit
    rows = audit.tail(50)

    # We should see: schedule state_doc_written, comms state_doc_written,
    # entity_store interactions (if any), and the gmail.draft dry-run row.
    actions = {(r['module'], r['action']) for r in rows}
    assert ('planner.schedule', 'state_doc_written') in actions
    assert ('planner.comms',    'state_doc_written') in actions
    # The autonomy dry-run row uses module='gmail', action='draft'
    assert ('gmail', 'draft') in actions

    gmail_draft_row = next(r for r in rows if r['module'] == 'gmail' and r['action'] == 'draft')
    assert gmail_draft_row['was_dry_run'] is True
    assert gmail_draft_row['tier'] in ('notify', 'ask')   # notify after first-time, ask before
    assert gmail_draft_row['preview_path']

    # ── 5. /how-am-i-doing summarizes ────────────────────────────────────────
    import audit_reader
    summary = audit_reader.how_am_i_doing(days=30)
    assert summary['dry_run'] >= 1
    assert 'planner.schedule' in summary['by_module'] or \
           'planner.comms'    in summary['by_module']  # at least one shows up

    # And the renderer produces text
    text = audit_reader.render_summary(summary)
    assert '**How is Pebble doing?**' in text


def test_first_time_ask_then_auto_after_approval(pebble_home, mock_backend):
    """First action of any type asks; once approved, the declared tier applies."""
    _seed_config()
    from autonomy import Autonomy
    from modules.base import PebbleModule, ActionTier
    from planners.base import Proposal
    import dry_run
    dry_run.set_enabled(False)  # exercise the live path

    class _Stub(PebbleModule):
        _default_tiers = {'write': ActionTier.NOTIFY}
        def __init__(self):
            super().__init__({'enabled': True}); self.calls = []
        def tool_name(self): return 'stub'
        def tool_description(self): return ''
        def tool_parameters(self): return {}
        def execute(self, action='', **k): self.calls.append(action); return 'ok'

    stub = _Stub()

    asked = []
    def approve(p): asked.append(p); return True

    a = Autonomy(modules=[stub], approval_handler=approve)

    # First time — must ask
    r1 = a.route(Proposal(module='stub', action='write', args={'x': 1}, source='t'))
    assert r1.was_first_time is True
    assert len(asked) == 1

    # Second time — declared NOTIFY tier applies, no ask
    r2 = a.route(Proposal(module='stub', action='write', args={'x': 2}, source='t'))
    assert r2.was_first_time is False
    assert r2.status == 'executed_notify'
    assert len(asked) == 1   # still 1


def test_dispatcher_under_realistic_event_burst(pebble_home, mock_backend):
    """Simulate 6 events back-to-back; dispatcher must rate-limit and dedup."""
    _seed_config()
    from events import bus, CALENDAR_EVENT_APPROACHING, REMINDER_DUE
    from planners.dispatcher import NotificationDispatcher

    popups = []
    def popup(*, title, body, buttons, metadata):
        popups.append({'title': title, 'body': body})

    d = NotificationDispatcher(popup_fn=popup, max_per_10min=1,
                                quiet_hours_start='99:99', quiet_hours_end='99:99')
    d.start()

    # 4 calendar events + 2 reminders all "at once"
    for i in range(4):
        bus.publish(CALENDAR_EVENT_APPROACHING, {
            'event_id': f'e{i}', 'title': f'Meeting {i}',
            'minutes_away': 10, 'location': '',
        })
    bus.publish(REMINDER_DUE, {'reminder': {'id': 'r1', 'text': 'Submit form'}})
    bus.publish(REMINDER_DUE, {'reminder': {'id': 'r1', 'text': 'Submit form'}})  # dup

    # Rate limit: ≤2 popups for non-critical (first non-critical + queued)
    # Critical bypasses (urgency=critical only fires for events ≤2 min away)
    assert d.fired_count() <= 2
    assert d.queue_size() >= 2

    bus.clear()
