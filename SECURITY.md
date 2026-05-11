# Security policy

## Credential handling

Pebble talks to Google (Gmail + Calendar), Canvas, GitHub, Slack, Spotify, and
several read-only APIs. **No client secrets, API keys, or OAuth tokens are
checked into this repository.** Every credential is loaded at runtime from one
of:

- An environment variable (e.g. `PEBBLE_GOOGLE_CLIENT_SECRET`)
- A user-local secrets file (e.g. `~/.pebble/secrets/google_oauth.json`)
- The setup wizard (`python main.py` first-run flow), which writes to those
  files for you.

`~/.pebble/` is gitignored by default — see `.gitignore`. Don't symlink it into
the repo.

### Setting up Google OAuth (Gmail + Calendar)

You need an "OAuth 2.0 Client ID" of type **Desktop app** from a Google Cloud
project you control. From [Google Cloud Console](https://console.cloud.google.com/):

1. Create a project (or reuse one).
2. Enable the **Gmail API** and **Google Calendar API**.
3. Configure the OAuth consent screen (External, Testing mode is fine).
4. Create an OAuth client ID of type **Desktop**. Download the JSON.
5. Save the downloaded JSON to `~/.pebble/secrets/google_oauth.json`
   (the file should have the `installed` key at the top level — Google's
   default format).

Alternatively, set environment variables before launch:

```powershell
$env:PEBBLE_GOOGLE_CLIENT_ID     = "<client_id>"
$env:PEBBLE_GOOGLE_CLIENT_SECRET = "<client_secret>"
```

On first connect (`/connect google` in chat), a browser window will open for
the OAuth consent flow. The resulting refresh token is stored at
`~/.pebble/google_token.json` and is also gitignored.

### Other credentials

| Service | How to provide |
|---|---|
| Canvas LMS | `/connect canvas <token>` — stored in `~/.pebble/config.json` |
| GitHub | `/connect github <pat>` — stored in `~/.pebble/config.json` |
| Slack workspace(s) | `/connect slack <token>` — stored per-workspace |
| Spotify | OAuth flow via `/connect spotify` |
| Anthropic / OpenAI | Setup wizard, stored in `~/.pebble/config.json` |

`~/.pebble/config.json` is gitignored. If you publish your home directory or
take a backup, exclude `~/.pebble/`.

## Rotating a leaked credential

If you suspect a credential has been exposed:

1. **Revoke immediately** in the originating console (Google Cloud, GitHub
   developer settings, Slack app config, etc.).
2. Generate a new credential.
3. Update the relevant `~/.pebble/secrets/*.json` file (or env var) with the
   new value.
4. Restart Pebble — token caches in `~/.pebble/google_token.json` etc. will
   re-auth on next API call.

For Google OAuth specifically: the `installed app` flow client secret is not
treated as confidential by Google's policy, but a public exposure can still
trigger an automatic suspension of your OAuth project. Rotate promptly if
the secret has ever been in a public commit.

## Reporting a vulnerability

Email <semathew@andrew.cmu.edu> with `[pebble security]` in the subject. Please
do not open a public GitHub issue for security-relevant findings until a fix
has been released.

## Threat model — what Pebble defends against

- **Prompt injection via the web scraper**: pages fetched by Pebble cannot
  trigger ASK-tier outbound actions (gmail.send, gcal.create_event, etc.).
  The chat tool path refuses ASK-tier calls outright; planner-driven writes
  always pass through the autonomy layer's first-time gate. All NOTIFY/ASK
  tool calls from chat are logged in `~/.pebble/audit.jsonl`.
- **First-time gating**: the very first invocation of any tool action — and
  the first outbound message to any new recipient — forces an approval prompt
  even if the action's declared tier is AUTO.
- **Dry-run by default during setup**: the wizard offers a dry-run period
  where every outbound API call is logged but never fires. See
  `~/.pebble/audit.jsonl` for `was_dry_run: true` rows.
- **Provenance invariant**: vault writes pass through a single chokepoint
  (`storage/vault._write_file`) that refuses to overwrite or strip the
  `source:` frontmatter / `<!-- pebble:... -->` markers on user-authored
  content.

## Threat model — what Pebble does NOT defend against

- A user running Pebble as administrator with a hostile model backend
  (e.g. a malicious Ollama endpoint) — the LLM can construct any tool call.
- A user manually placing untrusted JSON into `~/.pebble/state/*.json` —
  these are trusted intermediate files between planners and the runtime.
- An attacker with filesystem write access to `~/.pebble/` — they can do
  whatever Pebble can do.
