"""Shared pytest fixtures.

Two universal protections:
1. `pebble_home` redirects ~/.pebble/ into a tmp dir per test so tests never touch real user state.
2. `mock_backend` replaces model_backend.ModelBackend.chat with a deterministic stub so no real cloud calls fire.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable when running pytest from anywhere
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def pebble_home(tmp_path, monkeypatch):
    """Redirect Path.home() so anything that writes to ~/.pebble lands in tmp_path."""
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    # Many of our modules import their _PATH at module-load time; reload sensitive ones
    # Reload order matters: events first (bus singleton), then anything that
    # captured `bus` at import time. planners.* re-import bus from the fresh events.
    for mod_name in ('audit', 'dry_run', 'metrics', 'atomic_io', 'crab_config',
                     'entity_store', 'modules.entity_module',
                     'events',
                     'planners.base', 'planners',
                     'planners.schedule', 'planners.comms',
                     'planners.school', 'planners.dispatcher', 'planners.morning',
                     'first_time_ledger', 'autonomy', 'approval_queue',
                     'cache', 'scraper', 'planners.exam_prep',
                     'modules.memory', 'audit_reader', 'idle_detect',
                     'planners.wrapup', 'feedback', 'entity_suggest',
                     'error_reporter', 'setup_wizard', 'chat_commands'):
        if mod_name in sys.modules:
            import importlib
            importlib.reload(sys.modules[mod_name])
    yield tmp_path


@pytest.fixture
def mock_backend(monkeypatch):
    """Replace ModelBackend.chat so tests never call real LLMs.

    Usage:
        def test_x(mock_backend):
            mock_backend.set_response("hi")
            ...
    """
    class _Mock:
        def __init__(self):
            self._response = ""
            self.calls = []

        def set_response(self, text: str):
            self._response = text

        def chat(self, messages, system: str = "") -> str:
            self.calls.append({'messages': messages, 'system': system})
            return self._response

    mock = _Mock()

    try:
        import model_backend
        monkeypatch.setattr(model_backend.ModelBackend, 'chat', lambda self, messages, system='': mock.chat(messages, system))
    except ImportError:
        pass

    return mock


@pytest.fixture
def sample_inbox() -> list[dict]:
    """A small fixture inbox for comms-related tests."""
    return [
        {
            'message_id': 'm1',
            'thread_id':  't1',
            'from_email': 'professor@cmu.edu',
            'from_name':  'Prof Smith',
            'subject':    'HW3 office hours',
            'snippet':    'Hi — wanted to see if you could meet to discuss your HW3 submission.',
        },
        {
            'message_id': 'm2',
            'thread_id':  't2',
            'from_email': 'noreply@newsletter.example.com',
            'from_name':  '',
            'subject':    'Weekly digest',
            'snippet':    'Unsubscribe link...',
        },
        {
            'message_id': 'm3',
            'thread_id':  't3',
            'from_email': 'recruiter@biggcorp.com',
            'from_name':  'Anna Recruiter',
            'subject':    'SWE Intern role at BigGCorp',
            'snippet':    'Reaching out about a summer opportunity...',
        },
    ]
