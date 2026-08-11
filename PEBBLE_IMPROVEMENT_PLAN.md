# Pebble — Analysis & Improvement Plan
*Synthesis of: a graphify knowledge-graph of the codebase + five deep-research/audit passes (user needs, market, Hermes/Composio, UI/UX, codebase restructure). Prepared 2026-08-11. No code was changed — this is a plan.*

---

## Part 0 — TL;DR (read this if nothing else)

**What Pebble is:** a privacy-first, local-run, *proactive* Windows desktop assistant with a tiered human-in-the-loop autonomy layer. That exact combination — **local/private + proactive + safe-action + student-native** — is a genuine market whitespace no competitor currently occupies.

**The core finding (from the code audit + graphify):** Pebble's *domain core is better than its assembly*. The autonomy tiers, event bus, provenance-guarded vault, `docs/contracts.md`, and 324 logic tests are genuinely strong and portable. But three things undercut it:
1. **The clean architecture in `ARCHITECTURE.md` is largely not the code that runs.** The decoupled dispatcher, the autonomy chokepoint, and the send-delay queue are written and tested but **wired only in tests** — at runtime the app still uses the older coupled path.
2. **The engine is fused to Windows/Tk at the entry point** — there is no headless core a phone/web/mac client could talk to.
3. **The UI is two half-products stapled together** (a modern blue webview chat + everything else in purple Tkinter), and ~34 of ~40 features are invisible behind typed slash commands. The single best feature — the approvals/autonomy flow — has *no real UI at all*.

**The biggest strategic risk (from user + market research):** Pebble is **Windows-desktop-only**, but the continuity people actually want is **phone ↔ laptop**, and the phone is home base. Retention in AI apps is brutal (9–11% at 12 months; AI apps churn 36% faster than non-AI). The moat that beats churn is **accumulated personal context** + **habit on unavoidable daily loops** (briefing, deadlines, inbox) — not more features.

**On the stakeholder's questions:**
- **Hermes** (most plausibly *Nous Hermes* local function-calling models via Ollama): **adopt** — low-risk, on-brand, deepens the local/private story. Do *not* adopt "Hermes Agent" (redundant with Pebble) or the Rhasspy MQTT "Hermes" (out of scope).
- **Composio** (hosted 1,000-app tool-broker): **adopt selectively as an opt-in "expansion pack" for the long tail, behind a consent wall and BYO-OAuth — never as a replacement for the core local modules.** It's a hosted credential broker, which pulls against Pebble's local ethos; the reconciliation is architectural (governance stays in Pebble; Composio only provides actions).
- **"Better than AI slop"** is *not* solved by more integrations — it's solved by grounding in the user's real data, correct reversible actions, provenance, memory, and restraint. Every new integration *increases* slop risk unless the guardrails scale with it.

**The plan:** 6 phases over ~5 "milestones," each independently shippable, front-loaded on (a) turning on the safety architecture that already exists, (b) extracting a headless engine, and (c) fixing the UI into one coherent, discoverable system with a real approvals inbox — then validating with hard activation/retention/agreement metrics before scaling breadth.

---

## Part 1 — Where Pebble stands in the market

### 1.1 The market splits into four camps — none owns Pebble's intersection
- **Platform chat assistants** (ChatGPT, Claude, Gemini, Copilot): added memory, scheduled "Tasks," and agentic action modes — but cloud-first, general-purpose, and only act when asked.
- **Life-organizers** (Reclaim, Motion, Notion AI): great at calendar/task orchestration, but narrow, subscription-heavy, cloud.
- **Wearable "second brain"** (Limitless/Rewind, Personal.ai): **cautionary tale** — Limitless was acquired by Meta (Dec 2025) and is shutting down the Pendant + Rewind app; Humane's Pins bricked Feb 2025. Ambient recording + cloud + an ad-business acquirer = trust collapse.
- **Agent/action platforms** (ChatGPT Agent, Lindy, Catch, Martin): now *do things* on your accounts — and are exactly where prompt-injection risk lives (**OWASP's #1 LLM risk for 2025–26**). Rabbit R1's "LAM" over-promised autonomous web actions and torched trust on reliability.

**Whitespace Pebble can own:** *"The private, proactive desktop companion that safely does things for you — built for student life on Windows."* Mapped on four axes (proactive / takes-actions / local-private / student-native), **no competitor ticks all four.** Pebble's autonomy-tier + first-time-ledger + send-delay + audit stack is *literally the OWASP/Meta-recommended defense pattern* for action-taking agents — a feature to market, not plumbing.

### 1.2 What people actually need (and where current tools fail)
- **People chat with AI; they rarely let it *do*.** Only ~16% automate chores like bills/day-organization despite 77–82% doing those tasks manually (Menlo Ventures 2025). The white space is *completing loops*, with approval — not more suggestions.
- **Memory/continuity is now baseline, not a differentiator.** The universal complaint: "every new session is a blank slate." Google/MS/Grok all shipped default-on memory in 2025–26.
- **Context-switching is a quantified daily pain** (~47 tool-switches/day; ~4 hrs/week lost re-orienting). A single proactive "one surface" is the pitch.
- **Trust is delegated only with guardrails.** 64% comfortable letting AI manage to-dos/calendars, but only 24–39% trust autonomous action/purchases; **84% want a human option**; one significant autonomous error sends ~58% back to manual. Top trust drivers: privacy (57%), transparency (48%), human oversight (46%). *Pebble's design maps almost 1:1 onto this — its strongest external validation.*
- **Privacy/local processing is a real, marketable preference** (59% of US adults feel they've lost control of AI) — **but only if it's real end-to-end** (no silent cloud fallback for sensitive email/calendar data).
- **Retention is the existential risk.** AI apps: 41% more revenue/user but 36% worse monthly churn; ~9–11% payer retention at 12 months; ~0 switching cost. **The moat is accumulated personal context** that makes leaving costly.
- **Students** specifically want **syllabus/LMS → calendar** automation (manual deadline entry is the sharp, repeated pain).

### 1.3 Table stakes Pebble is currently missing
1. A **visible, editable, exportable memory system** (it has the SQLite entity store — make it a demoable trust feature).
2. A **Canvas/LMS connector** — student competitors (IntelliPlan, StudyWise, CampusConnect) auto-import assignments; a manual-only school planner looks dated. *(Pebble has a `canvas.py` module — verify it's wired and first-class.)*
3. **Some cross-device presence** — the #1 structural weakness vs. Gemini/Copilot/Apple.
4. **Connector breadth** — at least Outlook/Microsoft (Windows users!), Slack/Discord (already patterned).
5. A **clear hybrid model story** — what runs local vs. cloud, and *what leaves the device*.
6. **Reliability guardrails on actions** (under-promise, over-deliver — the Rabbit lesson).
7. A **"works even if we disappear" data-portability promise** — a marketing asset the Limitless shutdown made concrete, and one cloud competitors literally cannot copy.
8. **Transparent, student-friendly pricing** vs. Gemini's *free year for students*.

---

## Part 2 — The stakeholder's strategic questions, answered

### 2.1 Does adding Hermes + Composio give Pebble a "wider grasp"? — Yes, but they play different roles
- **Composio = breadth.** It solves the exact N×M OAuth + tooling grind Pebble does by hand: ~1,000 pre-authenticated app integrations, managed token refresh, and event *triggers*. **But it is a hosted broker** — on self-serve plans users' OAuth tokens live in Composio's cloud and calls execute on Composio's servers (even the documented BYO-OAuth path registers Composio's callback). The credential-storing backend is closed-source/license-gated, so "self-host" doesn't fully restore local-only. → **Adopt as a bounded, opt-in, clearly-labeled expansion pack for the long tail; keep hand-written local modules for the privacy-critical core (Gmail/GCal/Slack); route every Composio action back through Pebble's tiers + dry-run + audit so governance and provenance aren't lost.**
- **Hermes = depth of the local/private story.** Most plausibly *Nous Hermes 3/4* — open-weight, function-calling-tuned models that run locally via Ollama. Plugs straight into Pebble's existing multi-provider backend as the *private/cheap/offline* tier. → **Adopt as a first-class local provider option; keep cloud planners for hard reasoning; benchmark local tool-call reliability on Pebble's own module schemas before trusting it in AUTO tier; skip the 405B (impractical on a laptop).** *(Confirm the interpretation with the stakeholder — "Hermes Agent" the framework and Rhasspy "Hermes" are the wrong reads.)*

### 2.2 How is this better than "AI slop"?
More integrations *multiply* the slop surface. What actually creates signal — and what Pebble should double down on as it adds any surface:
1. **Grounding + input provenance** — "I'm proposing this *because of* message X from person Y at time Z." Extend provenance from outputs to *inputs*.
2. **Correct, reversible actions over more actions** — new integrations inherit the **strictest tier by default**, earning AUTO only via measured accuracy.
3. **Verify before commit** — dry-run, show the concrete draft/diff, check preconditions (right recipient? idempotent?).
4. **Memory & continuity** — remember decisions/corrections so it doesn't re-ask or re-err.
5. **Restraint / taste** — optimize for *interruptions avoided*, not actions taken; a signal budget caps proactive noise.
6. **Calibrated uncertainty** — escalate to ASK when unsure; never fabricate an entity/ID (the entity store is the antidote).

### 2.3 How do we ensure people really need this?
Validate with *pull*, not features-shipped:
- **JTBD interviews (5–8 target users)** before building — look for a hair-on-fire, recurring, currently-hacked-around job.
- **Wizard-of-Oz / concierge** a new integration before wiring it.
- **Dogfood relentlessly** — the existing "dry-run-on-real-life" weekly report (what Pebble *would* have done vs. what the user *actually* did) is the single best slop detector. **Agreement rate = north-star quality metric.**
- **Sharp activation metric** — e.g., "first *accepted* NOTIFY/autonomous action within 24h of install."
- **Retention (D7/D30 + task retention)**, segmented by which integrations retained users actually connected.
- **Fake-door** the "Connect 1,000+ apps" option to measure demand before taking the Composio dependency.
- **Kill criterion up front:** an integration that doesn't clear its activation/agreement/retention bar in N weeks gets pulled, not polished.

---

## Part 3 — Codebase: what to fix and restructure

*From the restructure audit + graphify. Graphify god nodes confirm the shape: `Vault` (104 edges), `PebbleModule` (88, betweenness 0.170 — the central contract), `ActionTier` (72) are the healthy core; `handle()` (57, the 890-LOC `chat_commands.py` dispatcher) and `SetupWizard` (979 LOC) are god-object coupling smells. Community clusters map 1:1 onto subsystems — the seams for a headless extraction are already visible.*

### P0 — Blocking (prerequisites for "significantly better" and cross-device)
- **P0-1 · No headless core.** `main.py` is bootstrap + Win32 window manager + watcher supervisor + hotkey + scheduler all at once, and launches chat as a subprocess. **→ Extract `PebbleEngine`** (zero `tkinter`/`ctypes` imports) owning config, module registry, watchers, event bus, autonomy, planner scheduling. `main.py` becomes *one client* (the Windows tray shell). Everything cross-device depends on this.
- **P0-2 · The decoupled notification path is dead code.** `proactive_engine.py` imports Tk and builds `NotificationPopup` directly; the clean, tested `planners/dispatcher.py` (rate-limit/quiet-hours/dedup, pluggable `popup_fn`) is called **only from tests**. **→ Cut over:** watchers publish events only; `NotificationDispatcher` is the sole subscriber; the shell injects the `popup_fn` (Tk today, push-notification later). Mostly deletion.
- **P0-3 · The planner→autonomy→module pipeline is barely connected.** `autonomy.route()` has **one** production caller (`comms.py`); other planners only write state-doc JSON; `PLANNER_COMPLETED` → proposal generation is a no-op; `ApprovalQueue` is instantiated **only in tests**. The entire safety story is real, tested, and unused. **→ Make autonomy the enforced funnel:** every planner emits `Proposal`s; a single `AutonomyService` owns the `ApprovalQueue`; wire `PLANNER_COMPLETED` → proposals → dispatcher.
- **P0-4 · Chat tool path bypasses autonomy for NOTIFY writes.** `tool_orchestrator._run` refuses only ASK; NOTIFY writes (`gmail.draft`, `journal.append`, `tasks.create`, memory) execute directly, skipping the first-time ledger and send-delay. Combined with the scraper feeding untrusted web text to the model, this is a **prompt-injection → real side-effect** path. **→ Route *all* write-tier tool calls through `Autonomy.route()`; reserve direct `execute` for AUTO/read-only.**
- **P0-5 · `crab_config` is non-atomic and written by two processes.** Plain `write_text` (violates contracts.md §11), written concurrently by the tray *and* the chat subprocess → torn-read/lost-update on a file holding **API keys and tier overrides** (a dropped override silently downgrades ASK→default). **→ Route config through `atomic_io.write_json` now; eliminate the second writer once chat is in-process (P1-6).**

### P1 — Important (scale, reliability, sync foundation)
- **P1-1 · Windows lock-in is concentrated in ~6 GUI files** (ctypes/Win32, Tk, pywebview/WebView2, `CREATE_NO_WINDOW`, pynput). The domain layer (`modules/`, `planners/`, `storage/`) is already GUI/OS-free — **the port is a shell-replacement, not a rewrite.** → Define thin platform ports: `Notifier`, `IdleSource`, `HotkeyRegistrar`, `TrayShell`.
- **P1-2 · `~/.pebble` hardcoded in ~39 files.** → Single `paths.py` resolver (env/OS convention: `%APPDATA%`, `~/Library/Application Support`, `$XDG_DATA_HOME`), injected everywhere. Prerequisite for port *and* sync.
- **P1-3 · State is fragmented across 6+ stores** with three persistence philosophies. → Don't unify engines; add a **sync-aware repository layer** classifying each: `append_log` (audit/metrics — concat-merge), `document` (config/state — per-key merge/LWW), `index` (entities.db — **derived, never synced, rebuilt from vault per device**), `content` (vault — file-level sync), `secrets` (keyring/DPAPI, never synced raw).
- **P1-4 · Hand-written integration pattern won't scale to 50+** (25+ near-identical soft-import blocks; `default_on` easy to miss → silent). → **Directory/entry-point discovery** + class-metadata manifest (`default_on`, `config_fields`, `provides_watcher`); this is also where the **Composio adapter** slots in (broker the long tail, keep ~10 bespoke locals; the tier/autonomy layer is the piece to preserve).
- **P1-5 · ~79 `except Exception: pass` swallows**, concentrated in the watcher/polling hot loops — a silently-dead watcher is worse than a crash for a *proactive* app. → Log to `audit`/`error_reporter` + a health signal; add `/health` + a watcher heartbeat in the UI; narrow the orchestrator's native→prompted fallback to the specific "tools unsupported" error.
- **P1-6 · Chat is a subprocess sharing files, not an API client.** → `PebbleEngine` exposes a small local API (in-proc iface + localhost RPC/WS); the webview becomes a thin client; **this is the exact attach point a future phone/web client reuses**, and it removes the P0-5 config hazard.

### P2 — Nice (quality/cost/headroom)
- `entity_store` alias lookup is an in-Python full scan + `CREATE TABLE IF NOT EXISTS` on every call → normalized aliases table + FTS, open connection once.
- `model_backend` has no streaming/retries/backoff, new SDK client per call, hardcoded model-capability string lists (will rot). Vision hardcodes `max_tokens=1024`.
- Vault search is substring + term-frequency, re-scans on every `list()`/`search()` → the `pebble-memory-rag` (sqlite-vec) work is the ceiling-raiser for "assistant that knows my stuff."
- Big files mix UI + logic: `chat_server.py` (1,142), `setup_wizard.py` (979), `chat_commands.py` (890 — this is the de-facto application-service layer and should be *extracted into the engine*, not left behind the chat UI).
- Model API keys sit plaintext in `config.json` (Google OAuth is handled well) → bring to keyring/DPAPI parity.
- **Preserve the provenance invariant** (`Vault._write_file` → `assert_preserves_provenance`) through any refactor — it's the model for how the *action* side should also be a mandatory chokepoint.

### Target architecture (the north star)
```
CLIENT SHELLS (thin, per-platform):  Windows tray (Tk crab + webview) · macOS/Linux (Tauri) · Web · Phone
        │  local API (in-proc iface + localhost RPC/WS)
        ▼
PEBBLE ENGINE (headless, zero GUI imports)
  PlatformPorts: Notifier · IdleSource · HotkeyRegistrar · TrayShell
  Watchers ─publish▶ EventBus ─▶ Planners ─emit Proposals▶ Autonomy (tiers, first-time ledger, send-delay)
      ▲                              │                          │
   ModuleRegistry(discovery)     Dispatcher ─▶ Notifier port    ▼
      │                          (rate/quiet/dedup)      Module.execute()  ← ONLY writer
      ▼
  Integration Adapter Layer: native PebbleModules (Gmail triage, Vault, ~10 bespoke)
                             + Composio adapter (long-tail SaaS) — all under the SAME tier governance
        ▼
STORAGE / SYNC-AWARE REPOSITORY:  paths.py · content(vault→file sync) · document(config/state→merge)
                                  append_log(audit/metrics→concat) · index(entities.db→rebuild) · secrets(keyring)
```

---

## Part 4 — UI/UX: what's broken and the redesign

**Diagnosis:** two disjoint design languages (modern **blue** webview chat vs. **purple** Tkinter for the crab menu, fallback chat, toasts, settings, onboarding). `crab_config` has **no theme field** — the divergence is structural. ~34/40 features are invisible behind typed slash commands. The autonomy layer's approvals/drafts — the product's differentiator — has **no real UI** (a 60s auto-dismissing toast + `/proposals --accept <id>`).

**Concrete bugs found:** single-line `<input>` that advertises "Shift+Enter for newline" (impossible in a text input); no token streaming (whole reply built, then shown — worst status model for a tool-using agent); the `.tool-card` component exists in CSS but is **never rendered**; voice transcript **auto-submits after 1.5s** (can fire misheard commands at an agent that sends email); toasts all compute the same bottom-right coords → **overlap** (7+ popup types will collide); conversation is wiped on every window open; CDN fonts/icons/markdown at runtime (contradicts "local-first" + breaks offline); no light theme; frameless `overrideredirect` windows → no screen-reader/keyboard semantics.

**Redesign direction:**
- **One design system, one theme source.** Add a `theme` block to `crab_config`; generate CSS vars (webview) and Tk palettes from it. **Kill blue-vs-purple.** Brand concept **"warm shell, cool depth"**: the crab's **coral** (`#FF6B4A`) as the signature accent on a deep tidal-blue ground — distinctive, not another indigo chatbot. Ship **light + dark**. Amber = "Pebble wants approval" as a consistent signal.
- **Command palette (Ctrl/⌘K)** over all ~40 commands (labels/descriptions from `HELP_TEXT`) — the highest-leverage fix; turns mystery-meat into search for a few hundred lines.
- **Approvals Inbox (the missing keystone)** wired to `autonomy.approve/deny_pending` + `ApprovalQueue.pending/cancel/fire_now`: a card per pending action with a rendered **preview/diff**, Approve/Deny/Postpone, and a **live countdown + Cancel/Send-now** for queued sends. Sidebar **badge** for pending count.
- **Streaming + tool-activity cards** from live orchestrator events ("Reading Gmail → 3 unread"), plus a **Stop** button; fix the composer (`<textarea>`), remove the 1.5s voice auto-submit.
- **Real, persistent sidebar** (Chat · Approvals · Today · Inbox · Notes/Entities · Reflections · Activity · Settings) with panels wired to real data (retire the dead placeholder right panel and hardcoded Quick Actions).
- **Notifications:** stack (fix overlap), theme, make actionable ones sticky, prefer native OS notifications.
- **Settings → tabs** (Models · Integrations · Automation · Appearance · Privacy/Audit) surfacing the tier/send-delay/thinking-pass controls that are config-only today.
- **Tech:** near-term — keep pywebview/WebView2 but add a **real frontend build** (Vite + component lib, **assets bundled, no CDN**) served by the core; strategic — **Tauri** shell + the **local core API**, so a phone/web client reuses the *same* frontend against the *same* API. Keep native only what must be: the clever chroma-keyed crab sprite, the global hotkey, OS toasts.

---

## Part 5 — THE PLAN (phased, each milestone independently shippable)

Guiding principle: **turn on the safety architecture that already exists → extract the headless engine → make the UI coherent and discoverable → validate with hard metrics → only then scale breadth (Composio) and platforms (mobile/mac).** Every phase names a goal and a validation gate.

### Milestone A — "Make the real architecture the running architecture" (foundational, low external risk)
**Goal:** the safety/decoupling the docs promise is actually load-bearing; zero user-visible regressions.
1. `paths.py` single data-dir resolver + route `crab_config` through `atomic_io` (P0-5, P1-2).
2. Cut over `ProactiveEngine` → events-only; `NotificationDispatcher` becomes sole subscriber with injected `popup_fn` (P0-2).
3. Make `Autonomy.route()` the enforced write funnel; every planner emits `Proposal`s; wire `PLANNER_COMPLETED` → proposals; instantiate `ApprovalQueue` in production (P0-3).
4. Route **all** write-tier tool calls (chat included) through autonomy; AUTO/read-only only may `execute` directly (P0-4).
5. Replace `except Exception: pass` in hot loops with logged + health-signalled handlers; add `/health` (P1-5).

**Validation gate:** existing 324 tests still green; new tests proving (a) a chat-driven `gmail.draft` now hits the first-time ledger, (b) a prompt-injection string in scraped text cannot trigger a NOTIFY write without gating, (c) two concurrent config writes don't lose a tier override, (d) a killed Gmail watcher surfaces in `/health` instead of dying silently.

### Milestone B — "Headless engine + one coherent, discoverable UI" (the biggest quality jump)
**Goal:** a `PebbleEngine` runnable with no GUI imports; the UI becomes one design system with the approvals inbox and streaming.
1. Extract `PebbleEngine` (P0-1); move `chat_commands` application logic into it; `main.py` → Windows shell injecting Win32 ports (P1-1).
2. Local core API (in-proc + localhost RPC/WS); chat webview becomes an API client (P1-6) — removes the subprocess config hazard.
3. **Design system unification**: `theme` in config → CSS vars + Tk palettes; coral-on-tidal; light + dark; bundle assets, kill CDN.
4. **Approvals Inbox** + **command palette (Ctrl+K)** + **streaming/tool-cards** + composer/voice fixes + stacked themed notifications.
5. Settings → tabbed; sidebar wired to real data.

**Validation gate:** `python -m pebble.engine` starts and runs a full watcher→planner→proposal→dispatch cycle headless (no `tkinter`/`ctypes` imported — assert via import check). Usability: a new user can find and run 5 previously-slash-only features via the palette without reading `/help`; an ASK-tier email send can be approved/denied/cancelled entirely from the inbox. Visual regression: one theme source, screenshot diff shows no blue/purple split.

### Milestone C — "Trust & retention moat" (memory + provenance + reliability)
**Goal:** the things research says drive retention and separate signal from slop.
1. **Visible, editable, exportable memory** — surface the entity store + memories as a first-class panel; "export my data" button (the "works even if we disappear" promise).
2. **Input provenance** — proposals/answers cite *why* ("because of message X from Y at Z"); never fabricate an entity/ID.
3. **sqlite-vec semantic memory** (`pebble-memory-rag`) to raise retrieval quality (P2).
4. **Reliability**: model_backend retries/backoff/timeouts + streaming; narrow capability detection; keyring/DPAPI for API keys.
5. **Hermes local tier**: add Nous Hermes (GGUF via Ollama) as a first-class private/offline provider; benchmark tool-call reliability on Pebble's own schemas before AUTO trust.

**Validation gate:** dry-run-vs-reality **agreement rate** tracked and trending up; memory panel round-trips (add → recall → export → reimport); Hermes local path passes the module-schema tool-call benchmark at an agreed threshold before being allowed above ASK.

### Milestone D — "Knowledge-worker breadth via Composio" (DECIDED: adopt Composio aggressively)
**Goal:** *(Decisions: primary ICP = knowledge workers; adopt Composio as a core breadth pillar.)* Make broad app coverage a headline capability while preserving Pebble's governance as the differentiator.
1. **Module discovery + manifest** refactor (P1-4) first — the registry the Composio adapter plugs into.
2. **Composio adapter as a core pillar:** normalize Composio's tools into the `PebbleModule`/`ActionTier` contract so *every* brokered action still flows through Pebble's tiers + first-time ledger + dry-run + audit. Shrink hand-written modules to the **~10 bespoke essentials** that need custom UX (Gmail triage, Vault, Calendar); broker the rest.
   - **Non-negotiable guardrails even when aggressive:** BYO-OAuth wherever supported; minimal scopes; an unmistakable consent boundary ("this connects through Composio's cloud"); every brokered action mirrored into Pebble's local audit so provenance stays single-source; new tools start in ASK/dry-run and earn AUTO only via measured agreement rate. *Governance is the moat — do not let breadth erode it.*
3. **Knowledge-worker connectors** prioritized: **Outlook/Microsoft 365**, Slack, Notion, GitHub, Linear/Jira (Windows + work stack), with the same tier governance.
4. Per-integration quality metrics (proposed/accepted/corrected) so noisy integrations are *retired*, not accumulated.

**Validation gate:** every Composio action provably passes through `Autonomy.route()` + audit (test); a red-team confirms a brokered write cannot fire without gating; per-integration agreement rate gates promotion above ASK; the closed-backend/self-host license terms are verified before any "local" claim references Composio.

### Milestone E — "Desktop deep-work depth" (DECIDED: reposition, do not chase cross-device)
**Goal:** *(Decision: reposition as the best private, proactive Windows deep-work companion; compete on depth, not phone↔laptop continuity.)* Instead of a mobile client, invest E in **depth**: focus-session intelligence, richer schedule/deep-work planning, idle-aware proactivity, and a polished single-surface experience that makes the desktop the point.
- The headless engine + local API (A/B) still pay off — they power the webview cleanly and keep the door open — but **no mobile app is planned**; the "cross-device" table-stakes gap is answered by *positioning* ("your deep-work desk"), not by building phone parity.
- Marketing leans hard on local-first / "works even if we disappear" and the deep-work angle vs. cloud generalists.

**Validation gate:** deep-work session metrics (focus time protected, briefing→action habit) trend up; positioning tested with target knowledge-worker users.

---

## Part 6 — How we test that this is *actually better*

**Engineering (does it work / did we break it):**
- Keep the 324-test suite green as the regression floor; add tests per validation gate above (autonomy-funnel enforcement, prompt-injection cannot write, config atomicity, headless-import assertion, dispatcher-is-sole-subscriber).
- **Headless assertion test:** importing `pebble.engine` must not import `tkinter`/`ctypes`/`pywebview` (guards P0-1 permanently).
- **Injection red-team test:** scraped/email content containing tool-invoking instructions cannot cause a NOTIFY/ASK side-effect without gating.
- CI (doesn't exist yet) running pytest + `ruff` on every push.

**Product (is it better for a real person):**
- **North-star: dry-run-vs-reality agreement rate** (what Pebble would have done vs. what the user did) — must trend up milestone-over-milestone.
- **Activation:** % of installs reaching a first *accepted* NOTIFY/autonomous action within 24h.
- **Habit/core-loop:** % of days the morning briefing is opened **and** an action is approved (if this isn't climbing, it's novelty, not need).
- **Retention:** D7/D30 + task-retention, segmented by connected integrations.
- **Trust leading indicators:** edit-before-send rate, AUTO→undo rate, "approve without changes" rate.
- **Reliability:** watcher uptime / silent-failure count (should approach zero after P1-5).
- **UI usability test:** can a first-run user discover & run 5 previously-hidden features via the palette without `/help`; time-to-first-approved-action.
- **Kill criterion:** any integration or proactive behavior that doesn't clear its activation/agreement/retention bar in N weeks is pulled.

**Validation cadence:** dogfood the team's own Gmail/GCal/Slack daily from Milestone A; JTBD interviews before Milestone D and E; fake-door for Composio; primary research before the cross-device bet.

---

## Part 7 — Decisions (MADE 2026-08-11)

1. **Cross-device → REPOSITION as a desktop deep-work tool.** Not building a mobile/web companion. Compete on depth and local privacy. Milestone E re-scoped to depth (focus intelligence, deep-work planning), not phone parity. The headless core/API (A/B) still ship — for testability and a clean webview — but mobile is out of scope.
2. **Primary ICP → KNOWLEDGE WORKERS.** Inbox triage, calendar, meeting prep, deep-work. Milestone D reprioritized to the work stack (Outlook/M365, Slack, Notion, GitHub, Linear/Jira). Student/LMS features become secondary, not the wedge.
3. **Composio → ADOPT AGGRESSIVELY** as a core breadth pillar; shrink hand-written modules to ~10 bespoke essentials. **Governance guardrails remain non-negotiable** (BYO-OAuth, consent boundary, all actions through tiers+audit) — breadth must not erode the moat. Milestone D updated accordingly.
4. **Hermes → Nous Hermes local models** (confirmed reading); adopt as the local/private provider tier in Milestone C.
5. **Next action → START MILESTONE A.** Execute the internal, low-risk safety/decoupling work now, with tests. In progress.

*Analysis artifacts live in `graphify-out/` (`graph.html` is browsable).*
