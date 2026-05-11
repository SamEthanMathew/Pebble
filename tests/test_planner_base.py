"""Planner infrastructure: state-doc envelope, re-run gate, lifecycle."""

from __future__ import annotations

import json


def test_input_hash_stable():
    from planners import input_hash
    a = input_hash({'a': 1, 'b': [2, 3]})
    b = input_hash({'b': [2, 3], 'a': 1})
    assert a == b
    assert a.startswith('sha256:')


def test_input_hash_changes_with_input():
    from planners import input_hash
    assert input_hash({'a': 1}) != input_hash({'a': 2})


def test_write_then_read_state_doc(pebble_home):
    from planners import write_state_doc, read_state_doc
    p = write_state_doc(
        name='test_state.json', generated_by='test_planner',
        ttl_seconds=300, input_hash_str='sha256:abc',
        payload={'hello': 'world'},
    )
    assert p.exists()
    env = read_state_doc('test_state.json')
    assert env is not None
    assert env['schema_version'] == 1
    assert env['generated_by'] == 'test_planner'
    assert env['ttl_seconds'] == 300
    assert env['input_hash'] == 'sha256:abc'
    assert env['payload'] == {'hello': 'world'}
    assert 'generated_at' in env


def test_is_fresh(pebble_home):
    from planners import is_fresh, write_state_doc, read_state_doc
    write_state_doc(name='t.json', generated_by='x', ttl_seconds=300,
                    input_hash_str='h', payload={})
    env = read_state_doc('t.json')
    assert is_fresh(env)

    write_state_doc(name='t.json', generated_by='x', ttl_seconds=0,
                    input_hash_str='h', payload={})
    env = read_state_doc('t.json')
    assert not is_fresh(env)


def test_planner_run_skipped_when_no_planner_model(pebble_home, capsys):
    """Cloud-only contract: missing planner_model = disabled, not degraded."""
    from planners.base import BasePlanner

    class _Toy(BasePlanner):
        name = 'toy'
        state_doc = 'toy_state.json'

        def collect_inputs(self):
            return {'k': 'v'}

        def render_prompt(self, inputs):
            return ('sys', 'user msg')

        def parse_output(self, text, inputs):
            return {'output': text}

    # No planner_model in config → must skip
    out = _Toy().run()
    assert out is None
    err = capsys.readouterr().err
    assert 'no planner_model' in err.lower() or 'disabled' in err.lower()


def test_planner_run_full_lifecycle(pebble_home, mock_backend, monkeypatch):
    """Configure a planner_model, mock the backend, assert state doc lands."""
    import crab_config
    from planners.base import BasePlanner

    # Configure a fake model entry the backend factory can find
    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    mock_backend.set_response('{"plan": "ok"}')

    class _Toy(BasePlanner):
        name = 'toy'
        state_doc = 'toy_state.json'
        ttl_seconds = 60

        def collect_inputs(self):
            return {'k': 'v'}

        def render_prompt(self, inputs):
            return ('be helpful', 'render plz')

        def parse_output(self, text, inputs):
            return json.loads(text)

    payload = _Toy().run()
    assert payload == {'plan': 'ok'}

    from planners import read_state_doc
    env = read_state_doc('toy_state.json')
    assert env is not None
    assert env['payload'] == {'plan': 'ok'}
    assert env['generated_by'] == 'toy'

    # mock_backend was called
    assert len(mock_backend.calls) == 1
    assert mock_backend.calls[0]['system'] == 'be helpful'


def test_planner_rerun_gate_skips_unchanged_inputs(pebble_home, mock_backend):
    """If collect_inputs returns the same data as last run, planner re-run is skipped."""
    import crab_config
    from planners.base import BasePlanner

    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    mock_backend.set_response('{"x": 1}')

    class _Toy(BasePlanner):
        name = 'toy'
        state_doc = 'toy.json'
        ttl_seconds = 60
        def collect_inputs(self): return {'k': 'v'}
        def render_prompt(self, inputs): return ('s', 'u')
        def parse_output(self, text, inputs): return json.loads(text)

    p = _Toy()
    p.run()  # writes
    assert len(mock_backend.calls) == 1

    p.run()  # gate skips
    assert len(mock_backend.calls) == 1  # no new call


def test_planner_force_bypasses_gate(pebble_home, mock_backend):
    import crab_config
    from planners.base import BasePlanner

    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    mock_backend.set_response('{"x": 1}')

    class _Toy(BasePlanner):
        name = 'toy'
        state_doc = 'toy.json'
        ttl_seconds = 60
        def collect_inputs(self): return {'k': 'v'}
        def render_prompt(self, inputs): return ('s', 'u')
        def parse_output(self, text, inputs): return json.loads(text)

    _Toy().run()
    _Toy().run(force=True)
    assert len(mock_backend.calls) == 2


def test_planner_publishes_planner_completed_event(pebble_home, mock_backend):
    import crab_config
    from events import bus, PLANNER_COMPLETED
    from planners.base import BasePlanner

    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    mock_backend.set_response('{}')

    received = []
    bus.subscribe(PLANNER_COMPLETED, lambda p: received.append(p))

    class _Toy(BasePlanner):
        name = 'toy_published'
        state_doc = 'toy_pub.json'
        def collect_inputs(self): return {}
        def render_prompt(self, inputs): return ('', '')
        def parse_output(self, text, inputs): return {}

    _Toy().run()
    bus.unsubscribe(PLANNER_COMPLETED, received.append)
    assert any(r.get('planner') == 'toy_published' for r in received)


def test_planner_parse_failure_skips_with_audit(pebble_home, mock_backend):
    """If parse_output raises, we don't write state, we skip with audit + metric."""
    import crab_config
    import audit
    from planners.base import BasePlanner

    crab_config.set_value('model', {'planner_model': 'fake-id'})
    crab_config.set_value('models', [{'id': 'fake-id', 'type': 'anthropic',
                                      'model_name': 'claude', 'api_key': 'fake',
                                      'enabled': True, 'display_name': 'Fake'}])
    mock_backend.set_response('not json')

    class _Toy(BasePlanner):
        name = 'toy_parse_fail'
        state_doc = 'toy_pf.json'
        def collect_inputs(self): return {}
        def render_prompt(self, inputs): return ('', '')
        def parse_output(self, text, inputs): return json.loads(text)  # raises

    out = _Toy().run()
    assert out is None
    rows = audit.tail(10)
    assert any(r.get('action') == 'parse_failed' for r in rows)
