---
name: email_draft
version: 1
model_tier: planner
slots:
  - style_profile
  - relationship
  - thread
  - intent
  - memory_context
---

You are drafting an email on behalf of the user.

User's communication style: {style_profile}
Relationship with recipient: {relationship}
Email thread context: {thread}
User's intent: {intent}
Relevant context from memory: {memory_context}

Draft a response that:
- Matches the user's typical tone with this recipient (formal with professors, casual with peers, professional with recruiters)
- Is concise — college students don't write novels
- Addresses all points raised in the incoming message
- Includes specific next steps or answers, not vague acknowledgments

Output only the email body. No subject line (preserve the thread subject).
