"""Daily wrap-up — evening reflection + tomorrow preview.

Phase 4 deliverable. Reads audit since this morning, today's task state,
tomorrow's calendar, and current comms_pending state, then renders
daily_wrapup.md to produce a journal entry.

Cloud-only: returns None when no planner_model is configured.
Side effects: appends to ~/.pebble/journal/<YYYY-MM-DD>.md by default.
"""

from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

import audit
import audit_reader
import metrics
import paths
import prompts as prompt_lib
from planners.base import read_state_doc

_TASKS_PATH = paths.data_dir() / 'tasks.json'
_JOURNAL_DIR = paths.data_dir() / 'journal'


def _planner_backend():
    try:
        import crab_config
        from model_backend import ModelBackend
        cfg = crab_config.get('model', {}) or {}
        mid = cfg.get('planner_model') or cfg.get('primary')
        if not mid:
            return None
        return ModelBackend.for_id(mid)
    except Exception:
        return None


def _today_iso() -> str:
    return datetime.date.today().isoformat()


def _audit_today_summary() -> str:
    """Lines of "<module>.<action> ← <source>" for non-read actions today."""
    today = datetime.date.today()
    cutoff = datetime.datetime.combine(today, datetime.time.min, tzinfo=datetime.timezone.utc)
    rows = audit_reader.audit_since(cutoff)
    if not rows:
        return '(no logged actions today)'
    lines = []
    for r in rows[-30:]:  # cap so prompt stays bounded
        ts = (r.get('timestamp') or '?')[11:16]
        suffix = ' [dry-run]' if r.get('was_dry_run') else ''
        lines.append(f'{ts}  {r.get("module", "?")}.{r.get("action", "?")} '
                     f'← {r.get("source", "?")}{suffix}')
    return '\n'.join(lines)


def _read_task_state() -> tuple[str, str]:
    """Returns (tasks_done_today_str, tasks_remaining_str)."""
    if not _TASKS_PATH.exists():
        return '(no task file)', '(no task file)'
    try:
        tasks = json.loads(_TASKS_PATH.read_text(encoding='utf-8'))
    except Exception:
        return '(unreadable)', '(unreadable)'
    today = _today_iso()
    done_today = [t for t in tasks if t.get('done')
                  and (t.get('completed_at') or t.get('created') or '')[:10] == today]
    remaining  = [t for t in tasks if not t.get('done')]

    def fmt(items):
        if not items:
            return '(none)'
        return '\n'.join(f'- {t.get("text", "")}'
                         + (f' (due {t["due"]})' if t.get('due') else '')
                         for t in items[:20])
    return fmt(done_today), fmt(remaining)


def _tomorrow_schedule_text() -> str:
    """Best-effort — read GCal for tomorrow if connected, else placeholder."""
    try:
        from modules.google_auth import GoogleServices, is_google_connected
        if not is_google_connected():
            return '(GCal not connected)'
        svc = GoogleServices()
        tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        time_min = datetime.datetime.combine(tomorrow, datetime.time.min).astimezone().isoformat()
        time_max = datetime.datetime.combine(tomorrow, datetime.time.max).astimezone().isoformat()
        events = svc.calendar.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy='startTime', maxResults=10,
        ).execute().get('items', [])
        if not events:
            return '(no events)'
        return '\n'.join(
            f'- {(e.get("start") or {}).get("dateTime", "")[:16]} {e.get("summary", "(no title)")}'
            for e in events
        )
    except Exception:
        return '(unavailable)'


def _comms_state_text() -> str:
    env = read_state_doc('comms_pending.json')
    if not env:
        return '(no comms state)'
    payload = env.get('payload', {}) or {}
    n_action = len(payload.get('action_required', []) or [])
    n_fyi    = len(payload.get('fyi', []) or [])
    return f'{n_action} action_required, {n_fyi} fyi, {payload.get("ignore_count", 0)} ignored'


def generate_wrapup(*, append_to_journal: bool = True) -> str | None:
    """Run the daily wrap-up. Returns the rendered text, or None if disabled."""
    backend = _planner_backend()
    if backend is None:
        print('[daily_wrapup] disabled — no planner_model configured', file=sys.stderr)
        metrics.emit('planner.skipped',
                     {'planner': 'daily_wrapup', 'gate_reason': 'no_planner_model'})
        return None

    metrics.emit('planner.started', {'planner': 'daily_wrapup'})

    audit_today_text          = _audit_today_summary()
    tasks_done, tasks_remain  = _read_task_state()
    tomorrow_schedule         = _tomorrow_schedule_text()
    comms_pending_text        = _comms_state_text()

    system_prompt = prompt_lib.render('daily_wrapup', {
        'audit_today':       audit_today_text,
        'tasks_done':        tasks_done,
        'tasks_remaining':   tasks_remain,
        'tomorrow_schedule': tomorrow_schedule,
        'comms_pending':     comms_pending_text,
    })

    try:
        text = backend.chat(
            [{'role': 'user', 'content': 'Produce the wrap-up now.'}],
            system=system_prompt,
        )
    except Exception as e:
        audit.append({
            'module': 'daily_wrapup', 'action': 'llm_failed',
            'result': {'error': str(e)}, 'tier': 'auto', 'source': 'daily_wrapup',
        })
        metrics.emit('planner.skipped',
                     {'planner': 'daily_wrapup', 'gate_reason': 'llm_failed'})
        return None

    if append_to_journal:
        _append_journal(text)

    audit.append({
        'module': 'daily_wrapup', 'action': 'generated',
        'args':   {'preview_chars': len(text), 'appended_to_journal': append_to_journal},
        'result': {'ok': True}, 'tier': 'auto', 'source': 'daily_wrapup',
    })
    metrics.emit('planner.finished', {'planner': 'daily_wrapup'})
    return text


def _append_journal(text: str) -> Path:
    _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    path = _JOURNAL_DIR / f'{_today_iso()}.md'
    timestamp = datetime.datetime.now().strftime('%H:%M')
    block = f'\n\n---\n## Daily wrap-up — {timestamp}\n\n{text.strip()}\n'
    with path.open('a', encoding='utf-8') as f:
        f.write(block)
    return path
