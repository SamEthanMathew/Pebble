"""Smoke tests for model_backend — never call real cloud APIs."""

from __future__ import annotations


def test_mock_backend_responds(mock_backend):
    """The mock_backend fixture works as documented."""
    import model_backend
    mock_backend.set_response('hello')

    # We can't instantiate a real ModelBackend without a config entry,
    # but we can verify the monkey-patch took effect on the class method.
    entry = {'type': 'anthropic', 'model_name': 'claude', 'api_key': 'fake'}
    backend = model_backend.ModelBackend(entry)
    assert backend.chat([{'role': 'user', 'content': 'hi'}]) == 'hello'
    assert len(mock_backend.calls) == 1
    assert mock_backend.calls[0]['messages'][0]['content'] == 'hi'
