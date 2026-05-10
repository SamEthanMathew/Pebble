# Pebble Prompt Library

Source-controlled, versioned system prompts used by planners and chat features.

## File format

```markdown
---
name: <prompt_name>
version: 1
model_tier: planner | chat
slots:
  - <slot_name_1>
  - <slot_name_2>
---

<system prompt text with {slot_name_1}, {slot_name_2} placeholders>
```

## Slots

Slots are filled from state-doc payload keys (see `docs/contracts.md` §7) or
passed explicitly by the caller. Slot names must match contracts.md — no
inventing new slot names without updating contracts first.

## Versioning

Bump `version` on any prose change. Old versions stay in the repo renamed as
`<name>.v<N>.md` so replay tests can pin a version.

## Loader contract (Phase 2)

```python
from prompts import render
text = render('morning_briefing', {'schedule_state': '...', 'comms_state': '...'})
# returns the body with slots substituted; raises KeyError on missing slot
```

The loader is built by `pebble-prompt-eng` in Phase 2.

## Current prompts

- `morning_briefing.md` — daily morning brief reading all state docs
- `email_draft.md` — drafting individual emails in user's tone
- `exam_prep.md` — day-by-day study plan generation
- `meeting_prep.md` — prep brief for upcoming meetings
- `daily_wrapup.md` — evening wrap-up + tomorrow preview
- `comms_triage.md` — inbox classification + routing
- `schedule_planner.md` — schedule planner state-doc generator
- `school_status.md` — school planner state-doc generator
