"""Error reporter: explicit reports, hook installation, tail."""

from __future__ import annotations

import sys


def test_report_writes_jsonl(pebble_home):
    import error_reporter
    try:
        raise ValueError('boom')
    except ValueError:
        path = error_reporter.report(*sys.exc_info(), source='test', context={'k': 'v'})
    assert path is not None
    text = path.read_text(encoding='utf-8').strip().splitlines()
    assert len(text) == 1
    import json
    rec = json.loads(text[0])
    assert rec['error_type'] == 'ValueError'
    assert rec['message'] == 'boom'
    assert rec['context'] == {'k': 'v'}
    assert 'ValueError: boom' in rec['traceback']


def test_install_hooks_idempotent(pebble_home):
    import error_reporter
    error_reporter._INSTALLED = False
    error_reporter.install_hooks()
    first = sys.excepthook
    error_reporter.install_hooks()
    # Second install should NOT re-wrap
    assert sys.excepthook is first


def test_tail_returns_recent_first(pebble_home):
    import error_reporter, sys, time

    for label in ('first', 'second'):
        try:
            raise RuntimeError(label)
        except RuntimeError:
            error_reporter.report(*sys.exc_info(), source='test')
        time.sleep(0.01)

    out = error_reporter.tail(n=5)
    assert len(out) == 2
    assert out[0]['message'] == 'second'  # most recent first
