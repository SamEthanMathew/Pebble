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


def test_redact_strips_credentials_and_emails():
    """Errors users share in bug reports should not leak tokens or emails."""
    from error_reporter import _redact
    assert '[REDACTED_GOOGLE_SECRET]'  in _redact('GOCSPX-abc123xyz456 leaked')
    assert '[REDACTED_OPENAI_OR_ANTHROPIC_KEY]' in _redact('key=sk-ant-api03-abcdefghijklmnopqrstuv')
    assert '[REDACTED_SLACK_TOKEN]'     in _redact('token xoxb-1234-5678-abcdefg')
    assert '[REDACTED_GITHUB_PAT]'      in _redact('use github_pat_abcdef1234567890abcdef')
    assert '[REDACTED_GITHUB_PAT]'      in _redact('legacy ghp_abcdefghijklmnopqrst')
    assert '[REDACTED_GOOGLE_API_KEY]'  in _redact('key=AIzaSyAbcdefghijklmnop1234')
    assert '[REDACTED_EMAIL]'           in _redact('contact me at user@example.com please')
    assert '[REDACTED]'                 in _redact('Bearer eyJabc.def.ghi')
    # query-string token stripping
    assert 'access_token=[REDACTED]'    in _redact('https://api.example.com/x?access_token=secret&user=1')
    assert 'user=1' in _redact('https://api.example.com/x?access_token=secret&user=1')  # other params kept
    # non-PII text unchanged
    assert _redact('hello world') == 'hello world'


def test_report_redacts_message_and_traceback(pebble_home):
    """An exception whose message contains a token has it stripped before write."""
    import error_reporter, json, sys
    try:
        raise RuntimeError("Auth failed for sk-ant-api03-supersecret-token1234 and user@example.com")
    except RuntimeError:
        path = error_reporter.report(*sys.exc_info(), source='test',
                                     context={'note': 'sk-ant-api03-othersecret-token9999'})
    rec = json.loads(path.read_text(encoding='utf-8').strip())
    # Raw secrets must not appear in the written record
    assert 'sk-ant-api03-supersecret-token1234' not in rec['message']
    assert 'user@example.com' not in rec['message']
    assert 'sk-ant-api03-othersecret-token9999' not in json.dumps(rec['context'])
    # The redaction markers should appear
    assert '[REDACTED' in rec['message']


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
