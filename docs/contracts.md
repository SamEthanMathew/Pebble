# Pebble Shared Contracts (v0)

Single source of truth for schemas, conventions, and protocols shared across the agent company. Owned by Coordinator. Specialist agents propose changes; Coordinator merges.

If your task seems to require deviating from anything here, stop and ask the Coordinator before proceeding.

---

## 1. ActionTier

```python
class ActionTier(Enum):
    AUTO   = "auto"    # act silently (reads, internal state, user-initiated timers)
    NOTIFY = "notify"  # act, then tell the user (drafts, task changes, journal appends)
    ASK    = "ask"     # propose, wait for approval (outbound sends, calendar writes, deletes)
```

Default for any unlisted action: `ASK`. Per-action declarations live in each `PebbleModule` subclass; user overrides live in `config.tiers.<module>.<action>`.

---

## 2. PebbleModule additions (in `modules/base.py`)

```python
def action_tier(self, action_name: str) -> ActionTier:
    """Return the tier for a given action of this module.
    
    Resolution: config override (config.tiers.<tool_name>.<action_name>) → 
    self._default_tiers.get(action_name, ActionTier.ASK).
    Subclasses define _default_tiers: dict[str, ActionTier].
    """

def outbound_target_id(self, action_name: str, args: dict) -> str | None:
    """For outbound actions, the canonical identifier of the target
    (e.g. recipient email, channel id). Used by the first-time-action 
    ledger to ask once per new target. Return None for non-outbound actions.
    """
```

---

## 3. Audit log line (`~/.pebble/audit.jsonl`)

One JSON object per line, append-only. Written by `audit.append(record)`.

```json
{
  "timestamp":      "2026-05-10T14:32:01Z",
  "module":         "gmail",
  "action":         "draft",
  "args":           {"to": "professor@cmu.edu", "subject": "..."},
  "result":         {"draft_id": "abc123", "status": "created"},
  "tier":           "notify",
  "user_approved":  true,
  "was_first_time": false,
  "first_time_key": "gmail.draft",
  "reversible":     true,
  "source":         "comms_planner",
  "was_dry_run":    false,
  "preview_path":   null
}
```

- `was_dry_run=true` ⇒ `preview_path` points to JSON in `~/.pebble/dry_run_previews/`; no external API call was made.
- `source` is the originator (`user`, `<planner_name>`, `<watcher_name>`).
- `result` may be `{"error": "..."}` on failure; still log the row.

---

## 4. Metrics line (`~/.pebble/metrics.jsonl`)

```json
{
  "timestamp": "2026-05-10T14:32:01Z",
  "event":     "notification.acted",
  "props":     {"notification_id": "abc", "kind": "meeting_prep", "latency_ms": 1240}
}
```

Canonical event names:
- `notification.fired`, `notification.dismissed`, `notification.acted`, `notification.suppressed`
- `proposal.received`, `proposal.approved`, `proposal.canceled`, `proposal.expired`
- `firsttime.asked`
- `draft.created`, `draft.kept`, `draft.edited`, `draft.deleted` (post-hoc by reading Gmail Drafts)
- `planner.started`, `planner.finished`, `planner.skipped` (with `gate_reason`)
- `tier.overridden`

---

## 5. Dry-run preview file (`~/.pebble/dry_run_previews/<ts>_<module>_<action>.json`)

```json
{
  "timestamp": "2026-05-10T14:32:01Z",
  "module":    "gmail",
  "action":    "draft",
  "args":      {"to": "...", "subject": "...", "body": "..."},
  "source":    "comms_planner",
  "tier":      "notify",
  "note":      "Would have created Gmail draft."
}
```

`/review-drafts` chat command lists these. Files are not auto-deleted; user can delete manually or wipe via `/review-drafts --clear`.

---

## 6. First-time-action ledger (`~/.pebble/first_time_seen.json`)

```json
{
  "gmail.draft": {"first_seen": "2026-05-10T14:00Z", "count": 12},
  "gmail.send:professor@cmu.edu": {"first_seen": "2026-05-11T09:15Z", "count": 1}
}
```

Key convention:
- Most actions: `<module>.<action>` (e.g. `gmail.draft`, `tasks.complete`).
- Outbound sends: `<module>.<action>:<target_id>` where `target_id` is from `PebbleModule.outbound_target_id()`.

Autonomy layer: ask on first-time key; auto-promote to module's declared tier after first approval.

---

## 7. State doc envelope (all files in `~/.pebble/state/`)

Every state doc carries:

```json
{
  "schema_version": 1,
  "generated_at":   "2026-05-10T14:32:01Z",
  "generated_by":   "schedule_planner",
  "ttl_seconds":    1800,
  "input_hash":     "sha256:abc...",
  "payload":        { /* doc-specific */ }
}
```

- `input_hash` is the re-run gate's hash. Planner re-run skips if hash unchanged.
- `ttl_seconds` is the freshness window. Consumers (chat agent, dispatcher) refresh via planner re-run if stale.
- All writes via `atomic_io.write_json()`.

### 7a. `schedule_today.json` payload
```json
{
  "date": "2026-05-10",
  "blocks": [{"start": "09:00", "end": "10:30", "kind": "class", "title": "15-122 Lecture", "entity_ref": "course:15-122"}],
  "free_windows": [{"start": "14:00", "end": "16:00", "suggested_use": "15-122 exam prep", "rationale": "..."}],
  "conflicts": [],
  "transitions": [{"note": "10min between meetings — not enough to context-switch"}]
}
```

### 7b. `comms_pending.json` payload
```json
{
  "action_required": [{
    "message_id": "...",
    "from":       {"email": "...", "name": "...", "entity_ref": "person:..."},
    "subject":    "...",
    "summary":    "...",
    "draft":      "...",          // optional, only if planner generated one
    "urgency":    "high"
  }],
  "fyi":      [{"message_id": "...", "summary": "..."}],
  "ignore_count": 17
}
```

### 7c. `school_status.json` payload
```json
{
  "courses": [{
    "code": "15-122",
    "next_deadline": {"title": "HW3", "due": "2026-05-13T23:59Z", "progress_pct": 30},
    "exam_plan": null
  }],
  "exam_plans": [{
    "course": "15-122",
    "exam_date": "2026-05-15",
    "plan": [{"date": "2026-05-12", "block": "14:00-16:00", "topic": "BSTs", "action": "...", "resources": ["..."]}]
  }]
}
```

### 7d. `daily_summary.json` payload
Written by daily wrap-up. Schema TBD in Phase 4.

---

## 8. Event-type catalog (`events.py`)

```python
CALENDAR_EVENT_APPROACHING    = "calendar.event_approaching"
EMAIL_RECEIVED_IMPORTANT      = "email.received_important"
EMAIL_RECEIVED_UNKNOWN        = "email.received_unknown"
TASK_DUE_SOON                 = "task.due_soon"
REMINDER_DUE                  = "reminder.due"
FOCUS_SESSION_STARTED         = "focus.started"
FOCUS_SESSION_ENDED           = "focus.ended"
USER_ACTIVE                   = "user.active"   # first input after idle threshold
USER_IDLE                     = "user.idle"     # no input for N min
ENTITY_UPDATED                = "entity.updated"
PLANNER_COMPLETED             = "planner.completed"
STATE_DOC_UPDATED             = "state_doc.updated"
```

**Payload conventions:**
- `CALENDAR_EVENT_APPROACHING`: `{event_id, title, start_iso, minutes_away, location, attendees}`
- `EMAIL_RECEIVED_*`: `{message_id, thread_id, from_email, from_name, subject, snippet}`
- `PLANNER_COMPLETED`: `{planner: str, state_doc: str, was_skipped: bool, gate_reason?: str}`

Subscribers must not raise; the bus wraps each handler in try/except + audits the failure.

---

## 9. Planner proposal interface (planner → autonomy)

```python
@dataclass
class Proposal:
    module:        str               # e.g. "gmail"
    action:        str               # e.g. "draft"
    args:          dict              # tool args
    source:        str               # planner name
    urgency:       str               # "critical" | "high" | "normal" | "low"
    reversible:    bool              # informs send-with-delay vs immediate
    target_id:     str | None        # for outbound; populated from outbound_target_id()
    rationale:     str               # one-line reason for audit + UI
```

Autonomy layer consumes proposals. Planners never call `module.execute()` directly.

---

## 10. Config additions (`~/.pebble/config.json`)

```json
{
  "dry_run": false,
  "model": {
    "primary": "...",
    "planner_model": "claude-sonnet-4-5",
    "fallback": "..."
  },
  "tiers": {
    "gmail":   {"search": "auto", "draft": "notify", "send": "ask"},
    "gcal":    {"list_events": "auto", "create_event": "ask", "update_event": "ask"},
    "slack":   {"read": "auto", "send": "ask"},
    "tasks":   {"list": "auto", "complete": "notify", "create": "notify"},
    "obsidian":{"search": "auto", "read": "auto", "append": "notify"},
    "alpaca_market": {"quote": "auto", "trade": "ask"},
    "kalshi":  {"positions": "auto", "trade": "ask"}
  },
  "schedule": {
    "wake_time": "08:30",
    "quiet_hours_start": "22:00",
    "quiet_hours_end":   "07:00",
    "briefing_window": ["08:30", "10:00"]
  },
  "notifications": {
    "max_per_10min": 1,
    "focus_suppress": true,
    "idle_suppress_after_min": 15
  },
  "context": {
    "max_conversation_turns": 20,
    "summary_batch_size": 5,
    "tier1_token_budget_pct": 0.30
  },
  "memory": {
    "max_entries": 1000,
    "consolidation_time": "03:00"
  },
  "scraping": {
    "cache_ttl_hours": {"syllabus": 168, "news": 24, "canvas": 2, "weather": 0.5}
  },
  "send_delay_seconds": 60,
  "audit_log": true,
  "entity_auto_suggest": true
}
```

`crab_config.get(key, default)` already supports nested access via dot notation if extended — Coordinator will add that helper if not present.

---

## 11. Atomic write convention

```python
# atomic_io.py
def write_json(path: Path, data: Any) -> None:
    """Write JSON atomically: serialize to <path>.tmp, fsync, os.replace(<path>.tmp, <path>)."""
```

All state docs, config, ledger files MUST use this. Audit log and metrics may use line-append (open with 'a' + flush + close per write) since they are append-only and tolerate the worst case (a partial line is parseable as junk).

---

## 12. Universal rules

- Read this file at the start of any specialist task; it changes the meaning of "owned" and "contract."
- No subagent creates new top-level shared schemas. Propose via PR-style edit and Coordinator integrates.
- Cloud-only for planners. If `config.model.planner_model` resolves to no API key, planner shuts down with a clear log line; it does not silently downgrade to a local model.
- Tests never call real cloud LLMs. Mock `model_backend.chat()`.
- Soft-import discipline for any new optional dep (see existing `modules/__init__.py`).
- Outbound sends only via `pebble-autonomy`. Planners propose; they do not call APIs that hit the network for write operations.

---

## Version

- **v0** — 2026-05-10 — initial. Authored by Coordinator, derived from approved plan at `C:\Users\samet\.claude\plans\create-a-full-plan-dazzling-tulip.md`.

Future versions bump on schema-breaking changes; agents read the current file each invocation.
