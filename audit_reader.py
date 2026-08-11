"""Read API over ~/.pebble/audit.jsonl and ~/.pebble/metrics.jsonl.

Used by:
- /how-am-i-doing chat command
- pebble-user-feedback weekly reports
- daily wrap-up planner

Tolerant of malformed lines (skipped silently).
"""

from __future__ import annotations

import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import paths

_AUDIT_PATH   = paths.data_dir() / 'audit.jsonl'
_METRICS_PATH = paths.data_dir() / 'metrics.jsonl'


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    try:
        with path.open(encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def _parse_iso(s: str) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
    except Exception:
        return None


# ── audit log ─────────────────────────────────────────────────────────────────

def audit_tail(n: int = 50) -> list[dict[str, Any]]:
    rows = list(_iter_jsonl(_AUDIT_PATH))
    return rows[-n:]


def audit_since(when: datetime.datetime) -> list[dict[str, Any]]:
    out = []
    for r in _iter_jsonl(_AUDIT_PATH):
        ts = _parse_iso(r.get('timestamp', ''))
        if ts and ts >= when:
            out.append(r)
    return out


def audit_filter(*, module: str | None = None, action: str | None = None,
                 source: str | None = None,
                 was_dry_run: bool | None = None,
                 limit: int | None = None) -> list[dict[str, Any]]:
    out = []
    for r in _iter_jsonl(_AUDIT_PATH):
        if module      is not None and r.get('module')      != module:    continue
        if action      is not None and r.get('action')      != action:    continue
        if source      is not None and r.get('source')      != source:    continue
        if was_dry_run is not None and r.get('was_dry_run') != was_dry_run: continue
        out.append(r)
        if limit and len(out) >= limit:
            break
    return out


# ── metrics ───────────────────────────────────────────────────────────────────

def metrics_since(when: datetime.datetime) -> list[dict[str, Any]]:
    out = []
    for r in _iter_jsonl(_METRICS_PATH):
        ts = _parse_iso(r.get('timestamp', ''))
        if ts and ts >= when:
            out.append(r)
    return out


def metrics_count_by_event(rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    if rows is None:
        rows = list(_iter_jsonl(_METRICS_PATH))
    c = Counter(r.get('event', 'unknown') for r in rows)
    return dict(c)


# ── high-level summary ────────────────────────────────────────────────────────

def how_am_i_doing(*, days: int = 7) -> dict[str, Any]:
    """Aggregate the last N days into a /how-am-i-doing summary."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    ar = audit_since(cutoff)
    mr = metrics_since(cutoff)

    metric_counts = metrics_count_by_event(mr)
    by_module     = Counter(r.get('module', 'unknown') for r in ar)
    by_source     = Counter(r.get('source', 'unknown') for r in ar)
    dry_run_rows  = [r for r in ar if r.get('was_dry_run')]
    live_rows     = [r for r in ar if not r.get('was_dry_run')]
    first_time    = [r for r in ar if r.get('was_first_time')]
    failures      = [r for r in ar
                     if isinstance(r.get('result'), dict)
                     and (r['result'].get('error') or r['result'].get('ok') is False)]

    planner_skips = defaultdict(Counter)
    for r in mr:
        if r.get('event') == 'planner.skipped':
            props = r.get('props', {})
            planner_skips[props.get('planner', '?')][props.get('gate_reason', '?')] += 1

    return {
        'window_days':   days,
        'audit_rows':    len(ar),
        'metric_rows':   len(mr),
        'by_module':     dict(by_module.most_common(10)),
        'by_source':     dict(by_source.most_common(10)),
        'dry_run':       len(dry_run_rows),
        'live':          len(live_rows),
        'first_time':    len(first_time),
        'failures':      len(failures),
        'metric_counts': metric_counts,
        'planner_skips': {k: dict(v) for k, v in planner_skips.items()},
    }


def render_summary(summary: dict[str, Any]) -> str:
    """Format a how_am_i_doing summary as readable Markdown."""
    lines = [f'**How is Pebble doing?** _last {summary["window_days"]} days_', '']

    lines.append(f'- Audit rows: **{summary["audit_rows"]}** '
                 f'(live {summary["live"]}, dry-run {summary["dry_run"]}, '
                 f'first-time {summary["first_time"]}, failures {summary["failures"]})')
    lines.append(f'- Metric events: **{summary["metric_rows"]}**')

    if summary['by_module']:
        top = ', '.join(f'`{m}`={c}' for m, c in summary['by_module'].items())
        lines.append(f'- Top modules: {top}')
    if summary['by_source']:
        top = ', '.join(f'`{m}`={c}' for m, c in summary['by_source'].items())
        lines.append(f'- Top sources: {top}')

    notif_counts = {k: v for k, v in summary['metric_counts'].items()
                    if k.startswith('notification.')}
    if notif_counts:
        parts = ', '.join(f'`{k.split(".", 1)[1]}`={v}' for k, v in sorted(notif_counts.items()))
        lines.append(f'- Notifications: {parts}')

    proposal_counts = {k: v for k, v in summary['metric_counts'].items()
                       if k.startswith('proposal.')}
    if proposal_counts:
        parts = ', '.join(f'`{k.split(".", 1)[1]}`={v}' for k, v in sorted(proposal_counts.items()))
        lines.append(f'- Proposals: {parts}')

    if summary['planner_skips']:
        lines.append('')
        lines.append('**Planner skip reasons**')
        for planner, reasons in summary['planner_skips'].items():
            r = ', '.join(f'`{reason}`={count}' for reason, count in reasons.items())
            lines.append(f'- `{planner}`: {r}')

    return '\n'.join(lines)
