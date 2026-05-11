# Pebble architecture

This is a contributor-oriented map of the codebase. For end-user features
see [README.md](README.md); for the credential and threat model see
[SECURITY.md](SECURITY.md).

## High-level shape

Pebble is a long-running Python process with a Tk taskbar icon (the pixel
crab) and an embedded chat window. Background threads watch Gmail / Calendar
/ Slack / the user's idle state and publish events to an in-process pub/sub
bus. Planners react to those events, propose actions, and the autonomy layer
routes proposals through tier-aware approval.

```
┌──────────────┐     events.bus    ┌──────────────┐
│  Watchers    │ ─────────────────▶│  Planners    │
│  (Gmail,     │                   │  (morning,   │
│   GCal,      │                   │   comms,     │
│   Slack,     │                   │   schedule,  │
│   idle)      │                   │   exam_prep) │
└──────────────┘                   └──────┬───────┘
                                          │ Proposal
                                          ▼
                                   ┌──────────────┐
                  user approval ◀──│  Autonomy    │──▶ audit.jsonl
                                   │  (tier gate, │
                                   │   first-time │
                                   │   ledger,    │
                                   │   dry-run)   │
                                   └──────┬───────┘
                                          ▼
                                   ┌──────────────┐
                                   │  Module      │
                                   │  .execute()  │
                                   │  (the only   │
                                   │   thing that │
                                   │   talks to   │
                                   │   APIs)      │
                                   └──────────────┘
```

The chat surface (Tk window or webview) is a separate path: the user types,
the LLM picks tools, `tool_orchestrator._run` dispatches. ASK-tier actions
from chat are refused outright; NOTIFY actions execute but are audited.

## Layout

```
main.py                  — CrabPet entry point + system tray icon
chat_window.py           — Tk-based chat surface (fallback)
chat_server.py           — pywebview chat surface (default)
chat_commands.py         — slash-command dispatcher shared by both surfaces
setup_wizard.py          — first-launch onboarding

tool_orchestrator.py     — native + prompted ReAct tool loop
model_backend.py         — ModelBackend.for_id() — multi-provider LLM client

autonomy.py              — Proposal routing, action tiers
first_time_ledger.py     — per-(module, action, target) approval memory
approval_queue.py        — durable ASK queue + delayed-send scheduling
proactive_engine.py      — watcher threads + event publishers
events.py                — in-process pub/sub bus

audit.py                 — append-only ~/.pebble/audit.jsonl
audit_reader.py          — /audit, /how-am-i-doing
metrics.py               — append-only ~/.pebble/metrics.jsonl
error_reporter.py        — structured ~/.pebble/errors/*.jsonl
dry_run.py               — global dry-run toggle + preview writer

atomic_io.py             — write-tmp + os.replace JSON helper
cache.py                 — TTL'd HTTP cache for scraper

scraper.py               — trafilatura-based web scraper used by chat
crab_config.py           — config.json read/write + module config helpers
idle_detect.py           — keyboard/mouse activity watcher

modules/                 — read- and write-side adapters for each integration
  base.py                — PebbleModule ABC + ActionTier enum
  gmail.py               — Gmail search/draft/send
  gcal.py                — Google Calendar list/create
  slack_module.py        — Slack search/post + channel watcher
  obsidian.py            — LLM-facing vault tool surface (delegates to storage/)
  memory.py              — vault-backed remember/recall/list/forget
  canvas.py              — Canvas LMS (assignments, announcements)
  github_module.py       — GitHub PR + issue surface
  spotify.py             — playback control
  notion.py              — pages + databases
  tasks.py, reminders.py, journal.py, news_feed.py, …
  google_auth.py         — shared OAuth helper (no embedded secrets)

planners/                — cloud-only reasoning over state docs + context
  base.py                — BasePlanner ABC (collect_inputs → render → parse → write)
  morning.py             — combines schedule + comms + exam_prep into a briefing
  schedule.py            — today's time allocation
  comms.py               — email triage + draft generation
  school.py              — upcoming deadlines + study plans
  exam_prep.py           — backward-from-exam-date day-by-day plan
  wrapup.py              — daily wrap-up
  dispatcher.py          — notification rate-limit / dedup / quiet hours

storage/                 — the vault layer (Pebble's persistent knowledge)
  vault.py               — read + write chokepoint, provenance enforcement
  provenance.py          — single source of truth for source-marker format
  note.py                — Note dataclass + parsers
  entity_resolver.py     — name → vault note resolution (exact, alias, fuzzy)
  context_loader.py      — build a ContextBundle for a planner trigger
  proposal_queue.py      — pending vault-edit proposals (chat-reviewable)
  thinking_pass.py       — /close, /connect, /emerge, …
  thinking_schedule.py   — cron-style scheduler for thinking passes

prompts/                 — Markdown templates with frontmatter slots
  __init__.py            — load + render templates
  morning.md, comms.md, schedule.md, exam_prep.md, wrapup.md
  close.md, connect.md, emerge.md, drift.md, ideas.md, challenge.md, ghost.md

entity_store.py          — SQLite fast-lookup index, repopulated from vault
entity_suggest.py        — heuristic "you should add this person" surface

settings_window.py       — Tk settings UI
feedback.py              — weekly dry-run-vs-reality report

release/build.ps1        — PyInstaller build script
pebble.spec              — PyInstaller spec
```

## State docs

Planners do not call modules directly. They write JSON envelopes to
`~/.pebble/state/` and emit events; other planners and the chat surface read
from those files. Envelope shape:

```json
{
  "schema_version": 1,
  "generated_at":   "2026-05-11T08:00:00Z",
  "generated_by":   "schedule",
  "ttl_seconds":    1800,
  "input_hash":     "sha256:abc123…",
  "payload":        { … planner-specific … }
}
```

The `input_hash` is the re-run gate — if the inputs haven't changed since the
last successful run, the planner skips rather than burning tokens.

## Adding a new module

A module is a class that inherits `modules.base.PebbleModule` and exposes a
single tool surface to the LLM. Minimum required:

```python
# modules/your_thing.py
from modules.base import PebbleModule, ActionTier

class YourModule(PebbleModule):
    _default_tiers = {
        'search': ActionTier.AUTO,    # read-only
        'create': ActionTier.NOTIFY,  # writes, but visible
        'delete': ActionTier.ASK,     # destructive → approval
    }

    def tool_name(self):        return 'your_thing'
    def tool_description(self): return 'Description shown to the LLM.'
    def tool_parameters(self):
        return {
            'type': 'object',
            'properties': {
                'action': {'type': 'string',
                            'enum': ['search', 'create', 'delete']},
                'query':  {'type': 'string'},
            },
            'required': ['action'],
        }

    def execute(self, action='', **kwargs):
        # Do the thing. NEVER call out to the network without checking
        # dry_run.is_enabled() — the autonomy layer routes around you in
        # dry-run mode but defensive checks are cheap.
        ...

    def outbound_target_id(self, action_name, args):
        # Optional. Return a unique ID for per-recipient first-time gating.
        # E.g. for an email send, return args['to'].
        return None
```

Register it by adding to `modules/__init__.py`'s soft-import list. The chat
orchestrator will pick it up automatically once `_default_tiers` is declared.

## Adding a new planner

Subclass `planners.base.BasePlanner`:

```python
class YourPlanner(BasePlanner):
    name        = 'your_planner'
    state_doc   = 'your_state.json'
    ttl_seconds = 3600

    def collect_inputs(self):
        return {...}   # the dict that gets hashed for the re-run gate

    def render_prompt(self, inputs):
        return system_text, user_text

    def parse_output(self, llm_text, inputs):
        return payload_dict
```

Then call `YourPlanner().run()` from wherever you want it to fire — a chat
command, a watcher, or `events.bus.subscribe`.

## Provenance invariant

Every vault write goes through `storage.vault.Vault._write_file`. That single
chokepoint calls `assert_preserves_provenance`, which refuses to:

- Strip an existing `source:` frontmatter field.
- Overwrite a Pebble-authored `<!-- pebble:... -->` HTML comment with one
  that loses fields.

This is the only thing keeping Pebble from silently rewriting your own notes,
so don't bypass it. If you need to write a markdown file, use Vault.

## Test infrastructure

`tests/conftest.py` provides two universal fixtures:

- `pebble_home` — redirects `Path.home()` to a tmp dir for the test, so no
  test ever touches your real `~/.pebble/`.
- `mock_backend` — patches `ModelBackend.chat` so no test ever makes a real
  LLM call. Use `.set_response("…")` to control what the planner sees.

Run `python -m pytest -x` for fail-fast. CI doesn't exist yet — run locally
before committing.
