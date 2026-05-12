# Pebble v0.3.0 — production hardening

First publicly-distributed build of Pebble. Brings the project to a state
where a stranger can download it, run the setup wizard, and use it without
cloning the source.

## Download

`Pebble-v0.3.0-windows.zip` — Windows 10 / 11. Extract anywhere and run
`Pebble.exe`. **Pebble is not yet code-signed**, so Windows will show a
SmartScreen warning the first time you launch it. Click **More info** →
**Run anyway**. v0.4 will ship signed.

The setup wizard runs automatically on first launch — it picks a model,
connects Gmail / Calendar / Obsidian, and finishes with the crab pet on
your taskbar.

See [the website](https://samethanmathew.github.io/Pebble/) for the full
setup guide and feature list.

## What changed since v0.2.0

### Credentials and safety

- **Google OAuth client secrets removed from source.** v0.2 embedded a
  client_secret in `modules/google_auth.py`; v0.3 loads from
  `PEBBLE_GOOGLE_CLIENT_*` env vars or `~/.pebble/secrets/google_oauth.json`.
  Git history rewritten to scrub the old value.
- **Setup wizard now guides Google OAuth (BYOC).** New "Connect Gmail &
  Calendar" step links to Google Cloud Console, walks through five steps,
  accepts a pasted JSON or `client_id|client_secret` pair, and writes
  the secrets file.
- **Chat-path tool calls now tier-gated.** ASK-tier actions invoked from
  chat are refused outright; NOTIFY/ASK are logged to audit. Closes the
  prompt-injection-via-scraper hole.
- **autonomy.route** wraps the approval handler in try/except so a UI
  crash surfaces as `status='error'` with an audit row instead of silent
  denial.

### Concurrency and reliability

- `storage/thinking_schedule`: module-level lock; fire recorded before
  `run_pass` so a vault write failure doesn't unbounded-retry at LLM
  cost; swallowed exceptions now surface to `error_reporter`.
- `storage/proposal_queue`: lock widened across the read-modify-write so
  concurrent `accept(same_id)` calls produce exactly one winner.
- `storage/vault`: atomic-write tmp files staged outside the vault
  directory so failed writes don't pollute Obsidian.
- `model_backend._openai_vision`: now passes `max_tokens=1024`.

### Docs

- `README.md`, `ARCHITECTURE.md`, `SECURITY.md` added.
- `SECURITY.md` covers credential setup, rotation, and the threat model.

### Tests + lint

- **336 pytest tests passing** (up from 299 at the start of v0.3 work).
- 26 new tests across `tests/test_orchestrator_safety.py`,
  `tests/test_proposal_queue.py`, `tests/test_autonomy.py`,
  `tests/test_chat_commands.py`, `tests/test_wizard_google_step.py`.
- `ruff --select=F` clean across the whole repo.

## Known issues

- **Not code-signed** — see SmartScreen note above.
- **No auto-updater** — check this page for new releases.
- **Bundle size ~500 MB** — includes the Python runtime, Anthropic /
  OpenAI / Gemini SDKs, and 20+ integration modules.
- **First-run Google connect requires a free Google Cloud project.** The
  wizard walks you through it; the project takes about two minutes to
  set up.

## Acknowledgements

Pebble is a solo project by Sam Mathew (CMU). Feedback, issues, and PRs
welcome at <https://github.com/SamEthanMathew/Pebble>.
