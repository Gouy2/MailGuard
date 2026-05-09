# Wispera Project Plan

Wispera is now scoped as a Windows desktop Email Triage Agent.

The project should be small enough to finish, but deep enough to defend in an AI application development interview. The core bet is that a focused tool-use email workflow is stronger than a broad demo with RAG, multimodal input, post-training, and desktop animation all partially implemented.

## One-sentence Pitch

Wispera reads email through tools, filters noise, reports important messages, explains why each message matters, and asks for approval before taking any write action.

## Current Scope

In scope:

- Tool-use-first agent runtime
- Email provider abstraction
- Mock email provider for fast development
- Important email report
- Ignored/noise email report
- Email detail inspection
- Structured preference memory
- Pending approval for write actions
- Trace/audit log
- Evaluation set based on labeled mock emails
- Windows desktop client as the interaction shell

Out of scope for now:

- RAG knowledge base
- Multimodal input
- Post-training
- Autonomous email deletion
- Autonomous email sending
- Cross-platform client rewrite

## Product Behavior

The agent may automatically:

- Fetch recent emails
- Classify emails
- Generate important email summaries
- Report urgent/action-required messages
- Update internal preference memory from explicit user feedback

The agent must ask approval before:

- Archiving email
- Marking email as read
- Starring or flagging email
- Creating a draft reply
- Applying a preference rule that affects future classification

The MVP must not:

- Send emails automatically
- Delete emails automatically
- Modify mailbox state without approval

## Interview Story

The strongest interview framing:

> I intentionally narrowed the project to one high-value workflow: email triage. That let me show real tool use, safety boundaries, auditability, background automation, preference memory, and evaluation, instead of spreading effort across many AI features.

## Document Map

- [Architecture](architecture.md)
- [Roadmap](roadmap.md)
- [Email Agent Design](email-agent-design.md)
- [Email Implementation Notes](interview-email-implementation.md)
- [Interview Tool-use Notes](interview-tool-use.md)
- [Windows Test Plan](windows-test-plan.md)
