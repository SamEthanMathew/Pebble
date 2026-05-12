# Pebble

A personal AI assistant that lives on your Windows taskbar as a pixel-art crab.

🌐 **Website + downloads:** <https://samethanmathew.github.io/Pebble/>

Pebble runs locally on your machine, reads your calendar / email / class
schedule, and proposes actions (briefings, reminders, study plans, draft
replies). Every outbound action is gated through an autonomy layer with
first-time approval, so Pebble never silently sends an email or creates a
calendar event you didn't expect.

**Status:** v0.3.0 — feature-complete for single-user daily use. Distributed
as an unsigned Windows folder (SmartScreen workaround required on first
launch); v0.4 will ship signed.

## What Pebble can do

- **Morning briefing** that combines your day's calendar, unread email
  triage, and upcoming deadlines into one summary.
- **Comms triage**: classifies inbox into action-required / FYI / ignore;
  drafts replies to messages Pebble thinks need one.
- **Exam-prep chain**: given a course and exam date, reasons backward to
  produce a day-by-day study plan.
- **Daily wrap-up** appended to your Obsidian daily note.
- **Thinking passes** (`/close`, `/connect`, `/drift`, `/emerge`, `/ideas`,
  `/challenge`, `/ghost`) — scheduled reasoning over your vault, never over
  Pebble's own writeback.
- **Vault-backed memory**: Pebble's persistent knowledge lives in your
  Obsidian vault, with provenance markers so you can always see what Pebble
  wrote vs. what you wrote.

## Quickstart (developers running from source)

Requires Python 3.11 or newer on Windows 10/11.

```powershell
git clone <this-repo>
cd Pebble
pip install -e .
python main.py
```

On first launch, a setup wizard runs:

1. Pick a model backend. Cloud (Anthropic Claude, OpenAI, Gemini) gives the
   best planner quality. Local (Ollama) works for the chat surface.
2. Connect Google for Gmail + Calendar (see [SECURITY.md](SECURITY.md) for
   OAuth setup).
3. Point Pebble at your Obsidian vault.
4. Optionally connect Canvas, GitHub, Spotify, Slack, etc.

A pixel-art crab appears on your taskbar. Click it to open the chat window;
type `/help` to see all commands.

## Quickstart (end users — pre-built `.exe`)

Not yet shipping. The PyInstaller spec at `pebble.spec` builds a standalone
folder under `dist/Pebble/` (`pwsh release/build.ps1 -Clean`), but the result
is not yet code-signed.

## Configuration

| File | Purpose |
|---|---|
| `~/.pebble/config.json` | Module enablement, model choice, tier overrides |
| `~/.pebble/secrets/` | OAuth client configs and API tokens (gitignored) |
| `~/.pebble/audit.jsonl` | Append-only log of every Pebble action |
| `~/.pebble/state/*.json` | Planner outputs (today's schedule, comms triage, …) |
| `~/.pebble/errors/*.jsonl` | Daily error reports (see `/errors`) |
| Your Obsidian vault | Pebble's persistent knowledge — never written without provenance markers |

See [SECURITY.md](SECURITY.md) for credential setup and rotation, and
[ARCHITECTURE.md](ARCHITECTURE.md) for the codebase map.

## Slash commands

Type `/help` in the chat window for the full list. Categories:

- **Connections** — `/connect google`, `/connect obsidian <path>`,
  `/connect canvas`, `/connect github`, `/connect notion`, `/disconnect`,
  `/status`.
- **Views** — `/tasks`, `/reminders`, `/gmail`, `/calendar`, `/notes`,
  `/slack-watch`, `/slack-workspaces`.
- **Daily use** — `/briefing`, `/wrapup`, `/exam-prep`, `/how-am-i-doing`,
  `/audit`, `/errors`, `/review-drafts`.
- **Knowledge graph** — `/add-course`, `/add-person`, `/entities`,
  `/entity-suggestions`.
- **Vault** — `/my-world`, `/proposals`, `/promote-note`, `/trace`,
  `/migrate-memory`.
- **Thinking passes** — `/close`, `/connect`, `/emerge`, `/drift`,
  `/ideas`, `/challenge <note>`, `/ghost <question>`.
- **Other** — `/forget`, `/dry-run`, `/help`.

## Safety model

Every outbound action passes through `autonomy.py`, which decides one of:

- **AUTO** — execute immediately, audit.
- **NOTIFY** — execute, audit, show a toast.
- **ASK** — queue for user approval before executing.

The first invocation of any action — and the first message to any new
recipient — forces ASK regardless of declared tier. Dry-run mode (toggle with
`/dry-run on`) routes every outbound call to a preview file instead of the
real API.

Chat-driven tool calls (when the LLM picks a tool during a chat reply) cannot
bypass this — see `tool_orchestrator._run` for the tier check. Prompt
injection in scraped web pages cannot trigger silent outbound writes.

## Development

```powershell
pip install -e .[dev]            # installs pytest + ruff
python -m pytest                  # 320+ tests, all should pass
python -m ruff check . --select=F # lint for unused imports/vars
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the module-development contract
and where to add new integrations.

## License

Proprietary — see header in each file. Contact <semathew@andrew.cmu.edu>.

## Issues

Report at <semathew@andrew.cmu.edu> with `[pebble]` in the subject. Bug
reports should include the relevant lines from `~/.pebble/errors/<date>.jsonl`.
