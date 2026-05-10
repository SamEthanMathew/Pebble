---
name: meeting_prep
version: 1
model_tier: planner
slots:
  - event_title
  - event_time
  - attendees_with_context
  - recent_threads
  - related_context
  - prev_notes
---

You are preparing the user for an upcoming meeting.

Meeting: {event_title}
Time: {event_time}
Attendees: {attendees_with_context}
Recent email threads with attendees: {recent_threads}
Related tasks/projects: {related_context}
Previous meeting notes (if any): {prev_notes}

Produce a prep brief:
1. Who's in the room (1 line per person — name, role, last interaction)
2. Likely agenda / what this is about (inferred from title + email context)
3. Things you might want to bring up (open items, pending questions)
4. Any prep actions needed before the meeting

Under 250 words. No filler.
