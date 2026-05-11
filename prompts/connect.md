---
name: connect
version: 1
model_tier: planner
slots:
  - context
---

You are running a CONNECT pass — finding cross-domain connections in the
user's recent writing.

{context}

Look for **non-obvious threads** that span domains the user usually thinks
about separately. For example: an idea from research that applies to a
project, a pattern from finance that maps to academics, a person mentioned
in two different contexts whose work might intersect.

Surface 3-5 connections. For each:
- **Connection**: 1-2 sentences naming the link
- **Why it might matter**: the actionable angle (a question to explore,
  an experiment to run, a person to introduce to another, a piece of writing to draft)
- **Sources**: the 2-3 note ids that prompted you to see this

Skip the obvious. If two daily notes both mention the same project, that's
not a connection — that's just continuity. A connection crosses a domain or
introduces a frame the user hasn't applied to that area yet.

Output as Markdown. Under 700 words.
