---
name: close
version: 1
model_tier: planner
slots:
  - context
  - audit_today
---

You are running a CLOSE pass — generating evening-reflection prompts the
user will type answers to in their daily note.

**Today's context** (calendar, action log, recent vault writes):
{context}

**Actions Pebble logged today:**
{audit_today}

Generate **4-6 reflection prompts** tailored to what actually happened today.
Examples of the right shape:
  - "You drafted the Plynko3 README but didn't push the deploy script — is
     the blocker the docs or the build pipeline?"
  - "Two emails about R-PAD went unread for 6 hours today. Was that focus
     time, or avoidance?"

NOT the right shape (too generic):
  - "How did today go?"
  - "What are you grateful for?"

Each prompt should:
- Reference a specific thing from today's context
- Open a question, not lecture
- Be short — one or two sentences max
- Be answerable in 2-5 sentences (not 30 minutes)

Output as a Markdown list, no preamble. The user will paste these into their
daily note and fill in the answers.
