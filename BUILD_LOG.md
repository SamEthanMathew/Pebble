# Pebble V2 Build Log

## Core Architecture
- `main.py` — Crab taskbar overlay (tkinter, transparent, topmost, Win32)
- `chat_server.py` — Chat UI (pywebview + Edge/WebView2), spawned as subprocess
- `model_backend.py` — LLM abstraction (Anthropic, OpenAI, Gemini, Ollama)
- `tool_orchestrator.py` — Native + ReAct tool calling for all model types
- `crab_config.py` — Persistent config at `~/.pebble/config.json`
- `settings_window.py` — Active/Available module marketplace UI
- `setup_wizard.py` — First-run wizard with Ollama model download
- `notification_popup.py` — Dark floating popup near taskbar
- `proactive_engine.py` — Background polling: calendar (5min), tasks (1hr), morning briefing (8:30am)

## Modules

### Always Active
- `modules/system_context.py` — Date, time, OS info
- `modules/clipboard.py` — Win32 clipboard read

### Productivity (default on)
- `modules/tasks.py` — Local task storage at `~/.pebble/tasks.json`, actions: create/list/pending/complete/carry_forward/set_due
- `modules/journal.py` — Daily journal flow with 6 reflection questions, saves to `~/.pebble/journal/` and optionally Obsidian

### Knowledge & Notes
- `modules/obsidian.py` — Full vault CRUD: search, read, write, append_daily, list_folder
- `modules/notion.py` — Notion API: search, read_page, create_page, add_task

### Google Ecosystem (embedded OAuth — no credentials.json needed)
- `modules/google_auth.py` — Shared `GoogleServices` with embedded client config, token at `~/.pebble/google_token.json`
- `modules/gmail.py` — Search, read, draft reply + `GmailWatcherThread` for background polling
- `modules/gcal.py` — Get events, find free time, create events

### Web & Market Data
- `modules/brave_search.py` — Brave Search API web search
- `modules/weather.py` — OpenWeatherMap current + 24h forecast
- `modules/alpaca_market.py` — Portfolio, prices, news, positions (read-only)

## Proactive Behavior
- Calendar check every 5min → "Meeting in N minutes" popup
- Task check on startup + every hour → overdue/due-today notifications
- Morning briefing 8:30–10am → "Plan your day" prompt if no journal entry

## Google OAuth
- Single shared token for Gmail + Calendar
- Uses embedded OAuth client (no setup needed from user)
- Browser sign-in on first use, auto-refreshes after

## Settings UI
- "Active Integrations" — enabled modules with full config and Remove button
- "Available Integrations" — compact cards with "+ Add" button
- System Context + Clipboard are pinned (🔒)
- Google modules show "✓ Connected / Not connected" status

## Website
- V1 website at `V1/pebble-web/` — Next.js 16, Tailwind 4, Framer Motion, Three.js particles
- Run: `cd V1/pebble-web && npm start` → http://localhost:3000

## Notion Setup (needs user action)
1. notion.so/my-integrations → New integration → copy `secret_...` token
2. Open each database → Connections → add Pebble integration
3. Copy database ID from URL (32-char hex string)
4. Settings → Enable Notion → paste token + database ID

---

## Phase 2 — Additional Modules (built)

### New Modules
- `modules/canvas.py` — Canvas LMS: courses, assignments, upcoming, missing, announcements, grades (CMU: canvas.cmu.edu)
- `modules/kalshi.py` — Kalshi: balance, positions, market search, market details via REST API
- `modules/crypto.py` — CoinGecko: live prices, trending, top by market cap, search (no API key needed)
- `modules/discord_module.py` — Discord: read channels, send messages, list servers/channels, DMs
- `modules/memory.py` — Pebble Memory: persistent facts across sessions at `~/.pebble/memory.json`

### Chat Improvements
- **Markdown rendering** — marked.js renders bold, lists, code blocks, tables, blockquotes in bot messages
- **Command history** — Up/Down arrows navigate previously sent messages
- **Smart quick actions** — Morning briefing, Canvas, Kalshi, crypto, tasks, emails, weather shortcuts
- **Memory-aware system prompt** — Injects saved memories into every chat context

### Global Hotkey
- `Alt+Space` opens Pebble from anywhere (pynput keyboard listener)
- Stops cleanly on quit

### Proactive Engine — New Loop
- `_meeting_prep_loop` — checks every 2min, fires 8-12min before meetings with "Get briefed" button
- Total: 6 daemon polling threads

### Total Modules: 26
system_context, clipboard, obsidian, tasks, journal, reminders, focus, file_search,
notion, gmail, gcal, spotify, slack, github, todoist, news, brave_search, weather,
alpaca, canvas, kalshi, crypto, discord, memory, stt, screenshot

## In Progress / Planned

### Speech-to-Text
- Mic button in chat UI
- speech_recognition + pyaudio (Google Web Speech, free)
- STT module + chat_server integration

### Vision / Screenshot
- Screenshot module (PIL ImageGrab → base64)
- Vision-aware model_backend (Anthropic claude-sonnet-4-6 + OpenAI gpt-4o support)
- "Look at my screen" command triggers screenshot → AI analysis

### Spotify
- Spotify Web API + OAuth
- Now playing, play/pause/skip, search and queue

### Slack
- Slack SDK with bot token
- Read messages, send messages, search channels

### GitHub
- GitHub API with PAT
- Issues, PRs, notifications, repo search

### More Modules
- Todoist (task management)
- RSS News feeds
- Focus/Pomodoro timer
- Local file search
