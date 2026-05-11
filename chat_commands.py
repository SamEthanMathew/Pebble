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

**Connections**
- `/connect google` — sign in to Google (Gmail + Calendar)
- `/connect obsidian <path>` — set Obsidian vault folder
- `/connect canvas <base-url> <access-token>` — Canvas LMS
- `/connect notion <api-key>` — Notion integration
- `/disconnect <google|obsidian|canvas|notion>` — undo
- `/status` — what's connected

**Views (sidebar tabs)**
- `/tasks` — open tasks
- `/reminders` — pending reminders
- `/gmail` — recent unread mail
- `/calendar` — upcoming events
- `/notes [query]` — Obsidian (list root or search)
- `/slack-watch [<workspace>] [channels|--add ch|--remove ch|--clear]` — pick channels for popups
- `/slack-workspaces` — list configured Slack workspaces

**Daily use**
- `/briefing` — generate a morning briefing (runs planners)
- `/wrapup` — daily wrap-up (appends to journal)
- `/exam-prep <course-name> <YYYY-MM-DD>` — generate study plan for one exam
- `/how-am-i-doing [--days=N]` — observability summary
- `/audit [N]` — last N audit rows (default 20)
- `/errors [N]` — last N error rows (default 10)
- `/review-drafts` — list dry-run previews
- `/review-drafts --clear` — delete all dry-run previews

**Knowledge graph**
- `/add-course <code> [name]` — add a course entity
- `/add-person <email> [name]` — add a person entity
- `/entities [type]` — list known entities
- `/entity-suggestions` — show senders Pebble proposes adding
- `/entity-suggestions --accept <email>` — accept one
- `/entity-suggestions --dismiss <email>` — dismiss one

**Other**
- `/forget <pattern>` — remove memory entries
- `/dry-run [on|off]` — toggle dry-run mode
- `/help` — this list
"""


# ── Connection helpers (used by /connect, /disconnect, /status) ────────────────

def _enable_module(name: str, fields: dict | None = None) -> None:
    """Set enabled=True and merge any provided fields for a module."""
    import crab_config
    cfg = crab_config.get_module_config(name) or {}
    cfg['enabled'] = True
    if fields:
        cfg.update(fields)
    crab_config.set_module_config(name, cfg)


def _disable_module(name: str) -> None:
    import crab_config
    cfg = crab_config.get_module_config(name) or {}
    cfg['enabled'] = False
    crab_config.set_module_config(name, cfg)


def _google_token_path():
    from pathlib import Path
    return Path.home() / '.pebble' / 'google_token.json'


def _run_module_action(module_name: str, action: str,
                       *, fallback_actions: list[str] | None = None,
                       missing_hint: str = '',
                       **kwargs) -> str:
    """Invoke an action on an active module; return its text output as Markdown.

    On failure, the module's own error string is returned (modules don't raise —
    they return strings starting with 'Unknown action' etc.).
    """
    try:
        from modules import get_active_modules
    except Exception as e:
        return f'Module registry unavailable: {e}'

    mod = next((m for m in get_active_modules() if m.name == module_name), None)
    if mod is None:
        suffix = f'\n\n_{missing_hint}_' if missing_hint else ''
        return f'**{module_name}** is not enabled (or not configured yet).{suffix}'

    actions = [action] + (fallback_actions or [])
    last = None
    for a in actions:
        try:
            out = mod.execute(action=a, **kwargs)
            text = str(out) if out is not None else ''
            if text.lower().startswith('unknown action'):
                last = text
                continue
            return text
        except Exception as e:
            last = f'`{module_name}.{a}` raised: {e}'
            continue
    return last or f'**{module_name}** has no supported action.'


def _connection_status() -> dict:
    import crab_config
    p = _google_token_path()
    cfgs = lambda n: crab_config.get_module_config(n) or {}

    obsidian_path = cfgs('obsidian').get('vault_path', '')
    canvas_url    = cfgs('canvas').get('base_url', '')
    canvas_token  = cfgs('canvas').get('access_token', '')
    notion_key    = cfgs('notion').get('notion_api_key', '')

    return {
        'google':   {'connected': p.exists(),
                     'gmail_enabled': bool(cfgs('gmail').get('enabled')),
                     'gcal_enabled':  bool(cfgs('gcal').get('enabled'))},
        'obsidian': {'connected': bool(obsidian_path), 'path': obsidian_path,
                     'enabled': bool(cfgs('obsidian').get('enabled'))},
        'canvas':   {'connected': bool(canvas_url and canvas_token),
                     'url': canvas_url,
                     'enabled': bool(cfgs('canvas').get('enabled'))},
        'notion':   {'connected': bool(notion_key),
                     'enabled': bool(cfgs('notion').get('enabled'))},
    }


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

    # ── module surface (sidebar tabs) ────────────────────────────────────────

    if cmd == '/tasks':
        return _run_module_action('tasks', 'pending', fallback_actions=['list'],
                                   missing_hint='Tasks module always on. If this fails, see /errors.')

    if cmd == '/reminders':
        return _run_module_action('reminders', 'list')

    if cmd == '/slack-workspaces':
        # List configured Slack workspaces and what's watched in each.
        import crab_config
        cfg = crab_config.get_module_config('slack') or {}
        ws  = cfg.get('workspaces', {}) or {}
        if not ws:
            return ('No Slack workspaces configured. '
                    'Run `/connect slack <token>` to add one.')
        lines = [f'**Slack workspaces** (active: `{cfg.get("active_workspace", "?")}`)', '']
        for name, w in ws.items():
            ch_raw  = w.get('important_channels', '')
            chans   = [c.strip().lstrip('#') for c in ch_raw.split(',') if c.strip()]
            token_ok = bool((w.get('access_token') or '').strip())
            line = f'- **{name}** · token {"✓" if token_ok else "✗"} · watching '
            line += (', '.join(f'`#{c}`' for c in chans) if chans else '_(none)_')
            lines.append(line)
        return '\n'.join(lines)

    if cmd == '/slack-watch':
        # New surface (multi-workspace):
        #   /slack-watch                                  → list everything
        #   /slack-watch <ws>                             → show that workspace's channels
        #   /slack-watch <ws> ch1,ch2,…                   → REPLACE channels for workspace
        #   /slack-watch <ws> --add <ch>                  → add one
        #   /slack-watch <ws> --remove <ch>               → remove one
        #   /slack-watch <ws> --clear                     → clear
        # Single-workspace shortcut (when only one configured): the workspace arg is optional.
        import crab_config
        cfg = crab_config.get_module_config('slack') or {}
        workspaces = cfg.get('workspaces', {}) or {}

        # No args → list across all workspaces
        if not args:
            if not workspaces:
                return ('No Slack workspaces configured. '
                        'Run `/connect slack <token>` first.')
            lines = ['**Watched Slack channels per workspace**', '']
            for ws_name, w in workspaces.items():
                ch_raw = w.get('important_channels', '')
                chans  = [c.strip().lstrip('#') for c in ch_raw.split(',') if c.strip()]
                lines.append(
                    f'- **{ws_name}**: ' +
                    (', '.join(f'`#{c}`' for c in chans) if chans else '_(none)_')
                )
            lines.append('')
            lines.append('Examples:')
            lines.append('- `/slack-watch r-pad g-team,announcements`')
            lines.append('- `/slack-watch scotty-labs --add proj-pebble`')
            lines.append('- `/slack-watch scotty-labs --clear`')
            return '\n'.join(lines)

        # Identify the workspace arg
        ws_name = args[0].lower()
        rest    = args[1:]

        # Backward-compat: single workspace and first arg isn't a known workspace → assume rest
        if workspaces and ws_name not in workspaces:
            if len(workspaces) == 1:
                ws_name = next(iter(workspaces.keys()))
                rest    = args
            else:
                return (f'Unknown workspace `{ws_name}`. '
                        f'Configured: ' + ', '.join(f'`{k}`' for k in workspaces) + '. '
                        f'Use `/slack-workspaces` to inspect.')

        if not workspaces:
            return ('No Slack workspaces configured. '
                    'Run `/connect slack <token>` to add one.')

        # Pull current channels for this workspace
        ws_cfg     = dict(workspaces.get(ws_name, {}))
        current_raw = ws_cfg.get('important_channels', '') or ''
        current = [c.strip().lstrip('#') for c in current_raw.split(',') if c.strip()]

        def _save(new_channels: list[str]) -> None:
            ws_cfg['important_channels'] = ','.join(new_channels)
            workspaces[ws_name] = ws_cfg
            cfg['workspaces']   = workspaces
            crab_config.set_module_config('slack', cfg)

        if not rest:
            if not current:
                return (f'No channels watched in `{ws_name}` yet.\n\n'
                        f'Use `/slack-watch {ws_name} <ch1>,<ch2>` to start.')
            return (f'`{ws_name}` is watching: ' +
                    ', '.join(f'`#{c}`' for c in current) +
                    '\n\n_Restart Pebble to apply changes (watcher reads config at startup)._')

        if rest[0] == '--clear':
            _save([])
            return f'✓ Cleared watched channels in `{ws_name}`. Restart Pebble.'

        if rest[0] == '--add' and len(rest) >= 2:
            ch = rest[1].lstrip('#')
            if ch not in current:
                current.append(ch)
            _save(current)
            return (f'✓ Added `#{ch}` to `{ws_name}`. Now watching: ' +
                    ', '.join(f'`#{c}`' for c in current) +
                    '\n\n_Restart Pebble._')

        if rest[0] == '--remove' and len(rest) >= 2:
            ch = rest[1].lstrip('#')
            current = [c for c in current if c != ch]
            _save(current)
            remaining = ', '.join(f'`#{c}`' for c in current) if current else 'none'
            return (f'✓ Removed `#{ch}` from `{ws_name}`. Now watching: {remaining}\n\n'
                    '_Restart Pebble._')

        # Replace mode
        new_set = [c.strip().lstrip('#') for c in ' '.join(rest).replace(',', ' ').split() if c.strip()]
        _save(new_set)
        if not new_set:
            return f'_({ws_name}: empty list — no channels watched)_'
        return (f'✓ `{ws_name}` watching: ' + ', '.join(f'`#{c}`' for c in new_set) +
                '\n\n_Restart Pebble._')

    if cmd == '/gmail':
        return _run_module_action('gmail', 'unread',
                                   missing_hint='Run `/connect google` to enable Gmail.')

    if cmd == '/calendar':
        # gcal.get_events with no args returns upcoming events (per module convention)
        return _run_module_action('gcal', 'get_events',
                                   missing_hint='Run `/connect google` to enable Calendar.')

    if cmd == '/notes':
        if args:
            return _run_module_action('obsidian', 'search', query=' '.join(args),
                                       missing_hint='Set your Obsidian vault in Settings.')
        return _run_module_action('obsidian', 'list_folder',
                                   missing_hint='Set your Obsidian vault in Settings.')

    if cmd == '/status':
        s = _connection_status()
        g = s['google']
        google_line = (f'✅  **Google** — connected (Gmail enabled={g["gmail_enabled"]}, '
                       f'Calendar enabled={g["gcal_enabled"]})'
                       if g['connected']
                       else '❌  **Google** — not connected. Run `/connect google`.')
        ob = s['obsidian']
        ob_line = (f'✅  **Obsidian** — `{ob["path"]}` (enabled={ob["enabled"]})'
                   if ob['connected']
                   else '❌  **Obsidian** — no vault. Run `/connect obsidian <path>`.')
        ca = s['canvas']
        ca_line = (f'✅  **Canvas** — `{ca["url"]}` (enabled={ca["enabled"]})'
                   if ca['connected']
                   else '❌  **Canvas** — not connected. Run `/connect canvas <url> <token>`.')
        no = s['notion']
        no_line = (f'✅  **Notion** — token present (enabled={no["enabled"]})'
                   if no['connected']
                   else '❌  **Notion** — no API key. Run `/connect notion <api-key>`.')
        return '\n'.join(['**Connection status**', '',
                          google_line, ob_line, ca_line, no_line])

    if cmd == '/connect':
        if not args:
            return ('Usage: `/connect <service>`. Services: '
                    '`google`, `obsidian <path>`, `canvas <url> <token>`, `notion <api-key>`.')
        service = args[0].lower()

        if service == 'google':
            def _run():
                try:
                    from modules.google_auth import GoogleServices
                    GoogleServices()  # opens browser, writes token on success
                except Exception as e:
                    return f'Google sign-in failed: {e}'
                # Auto-enable both Gmail and GCal modules so they start working
                _enable_module('gmail')
                _enable_module('gcal')
                return ('✅  Google connected. Gmail and Calendar modules enabled. '
                        'A restart is needed before the Gmail watcher starts; '
                        '`/briefing` works right now.')
            return AsyncCommand(label='Opening Google sign-in…', fn=_run)

        if service == 'obsidian':
            if len(args) < 2:
                return ('Usage: `/connect obsidian <vault-path>`. '
                        'Example: `/connect obsidian "C:\\Users\\you\\Documents\\MyVault"`')
            from pathlib import Path
            path = ' '.join(args[1:])  # in case path has spaces
            p = Path(path).expanduser()
            if not p.exists():
                return f'Vault path does not exist: `{p}`'
            if not p.is_dir():
                return f'Vault path is not a directory: `{p}`'
            _enable_module('obsidian', {'vault_path': str(p)})
            return f'✅  Obsidian connected — vault at `{p}`. Module enabled.'

        if service == 'canvas':
            if len(args) < 3:
                return ('Usage: `/connect canvas <base-url> <access-token>`. '
                        'Example: `/connect canvas https://canvas.cmu.edu 14234~abc...`')
            url, token = args[1], args[2]
            if not url.startswith('http'):
                return f'Canvas URL must start with http(s)://. Got: `{url}`'
            _enable_module('canvas', {'base_url': url.rstrip('/'), 'access_token': token})
            return f'✅  Canvas connected at `{url}`. Module enabled.'

        if service == 'notion':
            if len(args) < 2:
                return ('Usage: `/connect notion <api-key>`. '
                        'Get one at https://www.notion.so/my-integrations.')
            api_key = args[1]
            _enable_module('notion', {'notion_api_key': api_key})
            return '✅  Notion connected. Module enabled.'

        if service == 'slack':
            if len(args) < 2:
                return ('Usage: `/connect slack <token> [default-channel]`. '
                        'Token: User OAuth (xoxp-…) or Bot (xoxb-…). '
                        'Get one at api.slack.com/apps → your app → OAuth & Permissions.')
            token = args[1]
            default_channel = args[2] if len(args) >= 3 else ''
            fields = {'bot_token': token}
            if default_channel:
                fields['default_channel'] = default_channel.lstrip('#')
            _enable_module('slack', fields)
            return '✅  Slack connected. Module enabled.'

        if service == 'github':
            if len(args) < 2:
                return ('Usage: `/connect github <personal-access-token> [owner/repo]`. '
                        'Get one at github.com/settings/tokens (scopes: repo, read:user, gist).')
            pat = args[1]
            default_repo = args[2] if len(args) >= 3 else ''
            fields = {'personal_access_token': pat}
            if default_repo:
                fields['default_repo'] = default_repo
            _enable_module('github', fields)
            return '✅  GitHub connected. Module enabled.'

        if service == 'spotify':
            if len(args) < 3:
                return ('Usage: `/connect spotify <client-id> <client-secret>`. '
                        'Get them at developer.spotify.com/dashboard → your app → '
                        'Settings (the Client Secret needs "Show client secret" → copy). '
                        'OAuth will fire on the first playback request.')
            cid, secret = args[1], args[2]
            _enable_module('spotify', {
                'client_id':     cid,
                'client_secret': secret,
                'redirect_uri':  'http://localhost:8888/callback',
            })
            return ('✅  Spotify credentials saved. A browser will open for OAuth '
                    'the first time you ask Pebble to play something.')

        return (f'Unknown service: `{service}`. Try `google`, `obsidian`, `canvas`, '
                f'`notion`, `slack`, `github`, or `spotify`.')

    if cmd == '/disconnect':
        if not args:
            return 'Usage: `/disconnect <google|obsidian|canvas|notion>`'
        service = args[0].lower()
        if service == 'google':
            try:
                p = _google_token_path()
                if p.exists():
                    p.unlink()
                _disable_module('gmail')
                _disable_module('gcal')
                return '✅  Google disconnected. Gmail and Calendar disabled.'
            except Exception as e:
                return f'Disconnect failed: {e}'
        if service in ('obsidian', 'canvas', 'notion'):
            _disable_module(service)
            return f'✅  {service.title()} disabled. Stored credentials kept (use Settings to clear).'
        return f'Unknown service: `{service}`.'


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
