---
name: exam_prep
version: 1
model_tier: planner
slots:
  - course_name
  - course_code
  - exam_date
  - days_remaining
  - topics
  - obsidian_notes_summary
  - available_blocks
---

You are building an exam study plan for the user.

Course: {course_name} ({course_code})
Exam date: {exam_date}
Days until exam: {days_remaining}
Exam topics (from syllabus/course site): {topics}
User's existing notes on these topics: {obsidian_notes_summary}
User's schedule between now and exam: {available_blocks}

Build a day-by-day study plan that:
- Allocates specific topics to specific time blocks in the user's actual schedule
- Front-loads weak areas (topics with fewer/no notes)
- Includes specific study actions: "Review lecture 14 slides on BSTs" not "study trees"
- Reserves the final day/session before the exam for review only, no new material
- Estimates time per topic realistically (1-2hr blocks, not 6hr marathons)

Output as structured JSON:
{{
  "course": "...",
  "exam_date": "...",
  "plan": [
    {{"date": "...", "block": "2-4pm", "topic": "...", "action": "...", "resources": ["..."]}}
  ]
}}
