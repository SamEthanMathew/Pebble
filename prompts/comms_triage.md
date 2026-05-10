---
name: comms_triage
version: 1
model_tier: planner
slots:
  - new_messages
  - contacts
  - priorities
  - schedule
---

You are triaging the user's incoming communications.

New messages since last triage: {new_messages}
User's known contacts (from entity store): {contacts}
User's current priorities: {priorities}
Current schedule state: {schedule}

For each message, classify:
- **Action required**: needs a reply or follow-up. Include draft if straightforward.
- **FYI**: worth reading but no action needed. One-line summary.
- **Ignore**: newsletters, notifications, spam. Just count them.

Sort action-required by urgency. Professors and recruiters with deadlines first.
Group FYI items. Count ignores as "and N other messages you can skip."

Output as structured JSON for the comms planner to consume:
{{
  "action_required": [
    {{
      "message_id": "...",
      "from": {{"email": "...", "name": "...", "entity_ref": "..."}},
      "subject": "...",
      "summary": "...",
      "draft": "...",
      "urgency": "high|normal|low"
    }}
  ],
  "fyi": [{{"message_id": "...", "summary": "..."}}],
  "ignore_count": 0
}}
