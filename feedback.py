"""Weekly feedback report generator.

Reads ~/.pebble/audit.jsonl + ~/.pebble/metrics.jsonl over a window and produces
~/.pebble/feedback/<YYYY-WW>.md with quality signals for prompt tuning.

Also queues prompt-tuning suggestions to ~/.pebble/feedback/prompt_suggestions.jsonl
when patterns suggest the underlying prompts could improve.
"""

from __future__ import annotations

import datetime
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import audit_reader

_FEEDBACK_DIR = Path.home() / '.pebble' / 'feedback'
_SUGGESTIONS_PATH = _FEEDBACK_DIR / 'prompt_suggestions.jsonl'


def _iso_week(d: datetime.date) -> str:
    iso = d.isocalendar()
    return f'{iso[0]}-W{iso[1]:02d}'


def _summarize(rows: list[dict[str, Any]],
               metrics_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_module = Counter(r.get('module', '?') for r in rows)
    by_source = Counter(r.get('source', '?') for r in rows)
    dry_count  = sum(1 for r in rows if r.get('was_dry_run'))
    live_count = sum(1 for r in rows if not r.get('was_dry_run'))
    failures   = sum(1 for r in rows
                     if isinstance(r.get('result'), dict)
                     and (r['result'].get('error') or r['result'].get('ok') is False))

    metric_counts = Counter(r.get('event', '?') for r in metrics_rows)

    notifs_fired      = metric_counts.get('notification.fired', 0)
    notifs_acted      = metric_counts.get('notification.acted', 0)
    notifs_dismissed  = metric_counts.get('notification.dismissed', 0)
    notifs_suppressed = metric_counts.get('notification.suppressed', 0)
    proposals_received = metric_counts.get('proposal.received', 0)
    proposals_approved = metric_counts.get('proposal.approved', 0)
    proposals_canceled = metric_counts.get('proposal.canceled', 0)

    skip_breakdown: dict[str, Counter] = defaultdict(Counter)
    for r in metrics_rows:
        if r.get('event') == 'planner.skipped':
            props = r.get('props', {})
            skip_breakdown[props.get('planner', '?')][props.get('gate_reason', '?')] += 1

    drafts_created = sum(1 for r in rows
                         if r.get('module') == 'gmail' and r.get('action') == 'draft')

    return {
        'audit_rows':         len(rows),
        'metric_rows':        len(metrics_rows),
        'live_count':         live_count,
        'dry_count':          dry_count,
        'failures':           failures,
        'top_modules':        dict(by_module.most_common(8)),
        'top_sources':        dict(by_source.most_common(8)),
        'notifications': {
            'fired':           notifs_fired,
            'acted':           notifs_acted,
            'dismissed':       notifs_dismissed,
            'suppressed':      notifs_suppressed,
            'act_ratio':       (notifs_acted / notifs_fired) if notifs_fired else 0.0,
        },
        'proposals': {
            'received':        proposals_received,
            'approved':        proposals_approved,
            'canceled':        proposals_canceled,
            'approval_ratio':  (proposals_approved / max(1, proposals_approved + proposals_canceled)),
        },
        'planner_skips':      {k: dict(v) for k, v in skip_breakdown.items()},
        'drafts_created':     drafts_created,
    }


def _suggestions_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface obvious prompt-tuning signals."""
    out = []
    notifs = summary.get('notifications', {})
    if notifs.get('fired', 0) >= 10 and notifs.get('act_ratio', 0) < 0.2:
        out.append({
            'kind':   'notification_act_ratio_low',
            'detail': f'{notifs["acted"]}/{notifs["fired"]} acted '
                      f'({notifs["act_ratio"]:.0%}). Many notifications dismissed; '
                      f'consider tightening dispatcher rules or notification copy.',
        })
    proposals = summary.get('proposals', {})
    decided = proposals.get('approved', 0) + proposals.get('canceled', 0)
    if decided >= 5 and proposals.get('approval_ratio', 0) < 0.3:
        out.append({
            'kind':   'proposal_approval_low',
            'detail': f'{proposals["approved"]}/{decided} approved. '
                      f'Planners are proposing actions the user rejects — '
                      f'consider tighter eligibility heuristics in the source planner.',
        })
    if summary.get('failures', 0) >= 3:
        out.append({
            'kind':   'failures_present',
            'detail': f'{summary["failures"]} failed actions in the window. '
                      f'Inspect audit log for module-level errors.',
        })
    skips = summary.get('planner_skips', {})
    for planner, reasons in skips.items():
        if reasons.get('parse_failed', 0) >= 3:
            out.append({
                'kind':    'planner_parse_failures',
                'planner': planner,
                'detail':  f'{planner} parse_failed {reasons["parse_failed"]}x. '
                           f'LLM output likely deviating from declared schema — '
                           f'tighten the prompt.',
            })
    return out


def _render_report(week: str, since: datetime.datetime, summary: dict[str, Any],
                   suggestions: list[dict[str, Any]]) -> str:
    lines = [f'# Pebble weekly feedback — {week}', '',
             f'_Window: since {since.date().isoformat()}_', '',
             '## Activity', '',
             f'- Audit rows: **{summary["audit_rows"]}** '
             f'(live {summary["live_count"]}, dry-run {summary["dry_count"]}, '
             f'failures {summary["failures"]})',
             f'- Metric events: **{summary["metric_rows"]}**',
             f'- Drafts created: **{summary["drafts_created"]}**', '']

    if summary['top_modules']:
        lines.append('### Top modules')
        for m, c in summary['top_modules'].items():
            lines.append(f'- `{m}` × {c}')
        lines.append('')

    notifs = summary['notifications']
    if notifs['fired']:
        lines.append('### Notifications')
        lines.append(f'- fired {notifs["fired"]} · '
                     f'acted {notifs["acted"]} · '
                     f'dismissed {notifs["dismissed"]} · '
                     f'suppressed {notifs["suppressed"]} · '
                     f'act ratio **{notifs["act_ratio"]:.0%}**')
        lines.append('')

    proposals = summary['proposals']
    if proposals['received']:
        lines.append('### Proposals')
        lines.append(f'- received {proposals["received"]} · '
                     f'approved {proposals["approved"]} · '
                     f'canceled {proposals["canceled"]} · '
                     f'approval ratio **{proposals["approval_ratio"]:.0%}**')
        lines.append('')

    if summary['planner_skips']:
        lines.append('### Planner skips')
        for planner, reasons in summary['planner_skips'].items():
            r = ', '.join(f'`{k}`={v}' for k, v in reasons.items())
            lines.append(f'- `{planner}`: {r}')
        lines.append('')

    if suggestions:
        lines.append('## Prompt-tuning suggestions')
        for s in suggestions:
            extra = f' ({s["planner"]})' if s.get('planner') else ''
            lines.append(f'- **{s["kind"]}**{extra}: {s["detail"]}')
        lines.append('')
    else:
        lines.append('## Prompt-tuning suggestions')
        lines.append('_No issues flagged this week._')
        lines.append('')

    return '\n'.join(lines)


def generate_weekly_report(*, days: int = 7) -> Path:
    """Compute the report and write to ~/.pebble/feedback/<YYYY-WW>.md.

    Also appends suggestions to ~/.pebble/feedback/prompt_suggestions.jsonl.
    Returns the path of the report file.
    """
    _FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    audit_rows   = audit_reader.audit_since(since)
    metric_rows  = audit_reader.metrics_since(since)

    summary     = _summarize(audit_rows, metric_rows)
    suggestions = _suggestions_from_summary(summary)
    week        = _iso_week(datetime.date.today())
    text        = _render_report(week, since, summary, suggestions)

    report_path = _FEEDBACK_DIR / f'{week}.md'
    report_path.write_text(text, encoding='utf-8')

    if suggestions:
        with _SUGGESTIONS_PATH.open('a', encoding='utf-8') as f:
            for s in suggestions:
                row = {'week': week, 'generated_at': datetime.datetime.now(datetime.timezone.utc)
                       .isoformat(timespec='seconds').replace('+00:00', 'Z'), **s}
                f.write(json.dumps(row, default=str, ensure_ascii=False))
                f.write('\n')

    return report_path


def report_path_for(week: str | None = None) -> Path:
    if week is None:
        week = _iso_week(datetime.date.today())
    return _FEEDBACK_DIR / f'{week}.md'
