---
name: ideas
version: 1
model_tier: planner
slots:
  - context
---

You are running an IDEAS pass — surfacing buildable things from the user's
recent writing.

{context}

Find 3-5 **concrete things the user could build** that follow naturally from
what they've been writing about. Each idea should be:

- **Small enough to start this weekend** (not "build the next big thing")
- **Directly traceable to something the user wrote** — cite the source notes
- **A real thing**, not advice ("write more" is not an idea; "spend 30 min
  drafting a one-pager on X" is)

For each:
- **Idea**: one sentence
- **First step**: the concrete action that would take less than 30 minutes
- **Why this one** (1 sentence): the link to what they've been thinking about

Skip "consider X" / "explore Y" — those aren't ideas, they're avoidance.
Make every entry something they could literally start in the next hour.

Output as Markdown. Under 500 words.
