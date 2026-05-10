"""Shared slash-command logic for chat surfaces.

Both chat_window (Tk fallback) and chat_server (webview) delegate to handle()
so we don't have to maintain two implementations. Each command returns Markdown
text; surfaces wrap that in their rendering layer.

Long-running commands (like /briefing) return a sentinel so the surface knows
to spin its "thinking" indicator and run the work off-thread.
"""

from __future__ import annotations

import datetime
import shlex
from dataclasses import dataclass
from typing import Any, Callable

# Sentinel: caller should run `fn()` off the UI thread and display the result.
@dataclass
class AsyncCommand:
    label:   str
    fn:      Callable[[], str]


HELP_TEXT = """**Commands**

- `/briefing` — generate a morning briefing (runs planners)
- `/wrapup` — daily wrap-up (appends to journal)
- `/exam-prep <course-name> <YYYY-MM-DD>` — generate study plan for one exam
- `/how-am-i-doing [--days=N]` — observability summary
- `/audit [N]` — last N audit rows (default 20)
- `/errors [N]` — last N error rows (default 10)
- `/review-drafts` — list dry-run previews
- `/review-drafts --clear` — delete all dry-run previews
- `/add-course <code> [name]` — add a course entity
- `/add-person <email> [name]` — add a person entity
- `/entities [type]` — list known entities
- `/entity-suggestions` — show senders Pebble proposes adding
- `/entity-suggestions --accept <email>` — accept one
- `/entity-suggestions --dismiss <email>` — dismiss one
- `/forget <pattern>` — remove memory entries
- `/dry-run [on|off]` — toggle dry-run mode
- `/help` — this list
"""


def handle(text: str) -> str | AsyncCommand | None:
    """Run a slash command and return either a string (sync result) or
    AsyncCommand (caller should run off-thread). Returns None if not a command.
    """
    if not text.startswith('/'):
        return None

    try:
        parts = shlex.split(text)
    except ValueError:
        parts = text.split()
    cmd  = parts[0].lower()
    args = parts[1:]

    # ── instant / cheap commands ─────────────────────────────────────────────

    if cmd == '/help':
        return HELP_TEXT

    if cmd == '/review-drafts':
        import dry_run
        if '--clear' in args:
            n = dry_run.clear_previews()
            return f'Cleared {n} dry-run preview{"s" if n != 1 else ""}.'
        previews = dry_run.list_previews(limit=20)
        if not previews:
            return '_No dry-run previews. (Either dry-run is off or nothing has been proposed yet.)_'
        lines = [f'**{len(previews)} dry-run preview{"s" if len(previews) != 1 else ""}** '
                 f'(most recent first):', '']
        for p in previews:
            ts  = p.get('timestamp', '?')
            mod = p.get('module', '?')
            act = p.get('action', '?')
            src = p.get('source', '?')
            note = (p.get('note') or '')[:120]
            lines.append(f'- `{ts}` **{mod}.{act}** ← _{src}_')
            if note:
                lines.append(f'    {note}')
        lines.append('')
        lines.append('_Clear all: `/review-drafts --clear`_')
        return '\n'.join(lines)

    if cmd == '/dry-run':
        import dry_run
        if not args:
            return f'Dry-run is currently **{"ON" if dry_run.is_enabled() else "OFF"}**. ' \
                   f'Use `/dry-run on` or `/dry-run off` to toggle.'
        target = args[0].lower()
        if target in ('on', 'true', '1'):
            dry_run.set_enabled(True);  return '🧪 Dry-run **enabled**.'
        if target in ('off', 'false', '0'):
            dry_run.set_enabled(False); return '✅ Dry-run **disabled** — actions will fire live.'
        return f'Unknown argument: `{target}`. Try `on` or `off`.'

    if cmd == '/how-am-i-doing':
        import audit_reader
        days = 7
        for a in args:
            if a.startswith('--days='):
                try: days = int(a.split('=', 1)[1])
                except ValueError: pass
        return audit_reader.render_summary(audit_reader.how_am_i_doing(days=days))

    if cmd == '/audit':
        import audit_reader, json
        n = 20
        if args:
            try: n = int(args[0])
            except ValueError: pass
        rows = audit_reader.audit_tail(n)
        if not rows:
            return '_No audit entries yet._'
        lines = [f'**Last {len(rows)} audit row{"s" if len(rows) != 1 else ""}**', '']
        for r in rows[-n:]:
            ts = (r.get('timestamp') or '?')[11:19]
            tier = r.get('tier') or '-'
            dry = ' [dry]' if r.get('was_dry_run') else ''
            lines.append(f'- `{ts}` **{r.get("module")}.{r.get("action")}** '
                         f'({tier}{dry}) ← _{r.get("source", "?")}_')
        return '\n'.join(lines)

    if cmd == '/errors':
        import error_reporter
        n = 10
        if args:
            try: n = int(args[0])
            except ValueError: pass
        rows = error_reporter.tail(n=n)
        if not rows:
            return '_No errors logged. Good!_'
        lines = [f'**Last {len(rows)} error row{"s" if len(rows) != 1 else ""}**', '']
        for r in rows:
            ts = (r.get('timestamp') or '?')[:19]
            lines.append(f'- `{ts}` **{r.get("error_type")}**: {r.get("message", "")[:100]}')
        return '\n'.join(lines)

    if cmd == '/add-course':
        if not args:
            return 'Usage: `/add-course <code> [display name]`. Example: `/add-course 15-122 "Principles of Imperative Computation"`'
        import entity_store
        code = args[0]
        name = ' '.join(args[1:]) or code
        try:
            ent = entity_store.add(type='course', name=name, aliases=[code],
                                    payload={'code': code})
            return f'✓ Added course **{ent.name}** (`{code}`, id `{ent.id}`).'
        except Exception as e:
            return f'Failed to add course: {e}'

    if cmd == '/add-person':
        if not args:
            return 'Usage: `/add-person <email> [name]`. Example: `/add-person sarah@cmu.edu "Sarah Chen"`'
        import entity_store
        email = args[0]
        name = ' '.join(args[1:]) or email
        try:
            ent = entity_store.add(type='person', name=name, aliases=[email],
                                    payload={'email': email})
            return f'✓ Added person **{ent.name}** (`{email}`, id `{ent.id}`).'
        except Exception as e:
            return f'Failed to add person: {e}'

    if cmd == '/entities':
        import entity_store
        t = args[0] if args else None
        if t and t not in entity_store.ENTITY_TYPES:
            return f'Unknown type `{t}`. Valid: {", ".join(entity_store.ENTITY_TYPES)}.'
        ents = entity_store.list_entities(type=t)
        if not ents:
            return '_No entities stored yet._'
        lines = [f'**{len(ents)} entit{"y" if len(ents) == 1 else "ies"}**', '']
        for e in ents:
            aliases = f' ({", ".join(e.aliases)})' if e.aliases else ''
            lines.append(f'- `[{e.type}]` **{e.name}**{aliases}')
        return '\n'.join(lines)

    if cmd == '/entity-suggestions':
        import entity_suggest
        if '--accept' in args:
            try: idx = args.index('--accept')
            except ValueError: idx = -1
            if idx < 0 or idx + 1 >= len(args):
                return 'Usage: `/entity-suggestions --accept <email>`'
            email = args[idx + 1]
            ok = entity_suggest.accept(email)
            return f'✓ Accepted **{email}** — added as Person entity.' if ok else \
                   f'No suggestion for `{email}`.'
        if '--dismiss' in args:
            try: idx = args.index('--dismiss')
            except ValueError: idx = -1
            if idx < 0 or idx + 1 >= len(args):
                return 'Usage: `/entity-suggestions --dismiss <email>`'
            email = args[idx + 1]
            ok = entity_suggest.dismiss(email)
            return f'✓ Dismissed `{email}`.' if ok else f'No suggestion for `{email}`.'
        suggs = entity_suggest.find_suggestions(threshold=3)
        if not suggs:
            return '_No suggestions yet. Pebble will surface senders you interact with often._'
        lines = ['**Entity suggestions** (3+ unmatched interactions):', '']
        for s in suggs:
            label = f'{s.get("name") or s["email"]} (`{s["email"]}`)'
            lines.append(f'- {label} — seen {s["count"]}x · '
                         f'`accept`: `/entity-suggestions --accept {s["email"]}`')
        return '\n'.join(lines)

    if cmd == '/forget':
        if not args:
            return 'Usage: `/forget <pattern>`'
        from modules.memory import MemoryModule
        return MemoryModule({'enabled': True}).execute(action='forget', text=' '.join(args))

    # ── async / expensive commands ───────────────────────────────────────────

    if cmd == '/briefing':
        def _run():
            from planners.morning import generate_briefing
            text = generate_briefing(refresh_planners=True)
            return text or '_Briefing unavailable — set a `planner_model` in config.json._'
        return AsyncCommand(label='Generating briefing…', fn=_run)

    if cmd == '/wrapup':
        def _run():
            from planners.wrapup import generate_wrapup
            text = generate_wrapup(append_to_journal=True)
            return text or '_Wrap-up unavailable — set a `planner_model` in config.json._'
        return AsyncCommand(label='Writing wrap-up…', fn=_run)

    if cmd == '/exam-prep':
        if len(args) < 2:
            return 'Usage: `/exam-prep <course-name> <YYYY-MM-DD>`'
        course_query = args[0]
        exam_date    = args[1]
        def _run():
            import entity_store
            from planners import exam_prep
            ent = entity_store.lookup(course_query, type='course')
            if ent is None:
                return (f'No course matches `{course_query}`. '
                        f'Add it first with `/add-course {course_query}`.')
            plan = exam_prep.generate_for_course(ent, exam_date)
            if plan is None:
                return '_Exam-prep unavailable — set a `planner_model` in config.json._'
            blocks = plan.get('plan', [])
            lines = [f'**Study plan for {ent.name}** (exam {exam_date})', '']
            for b in blocks:
                lines.append(f'- `{b.get("date", "?")}` **{b.get("block", "")}** — '
                             f'{b.get("topic", "")}: {b.get("action", "")}')
            return '\n'.join(lines) if blocks else '_(plan empty — check planner output)_'
        return AsyncCommand(label='Generating exam plan…', fn=_run)

    return f'Unknown command: `{cmd}`. Try `/help`.'
