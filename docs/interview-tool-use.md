# Interview Notes

This document records how to present Wispera in interviews.

## Core Positioning

Wispera is a Windows desktop Email Triage Agent focused on tool use.

Strong pitch:

> I narrowed the project to a real workflow: email triage. The system reads email through typed tools, classifies messages, filters noise, reports important items, and routes risky actions through approval gates with traceability.

Avoid pitching it as:

- a generic chatbot
- a RAG project
- a multimodal assistant
- a desktop pet toy

## Why This Scope Is Strong

The email domain naturally demonstrates AI application engineering:

- private data
- external provider APIs
- structured tool calls
- user preferences
- background jobs
- safety boundaries
- explainability
- evaluation

The project can be small but deep.

## What To Emphasize

### Tool Use

Every email capability should be a tool:

- list emails
- fetch detail
- classify
- summarize report
- archive
- mark read
- create draft

The model/agent does not directly mutate state. It proposes tool calls; the server validates and executes.

### Permission Model

Read tools can run automatically.

Write tools require approval:

- archive
- mark read
- star/flag
- create draft

Excluded:

- send email
- delete email

This is a deliberate product and safety choice.

### Explainability

Each classification should answer:

- why important?
- why ignored?
- what action is suggested?
- what signal triggered the decision?

This makes the system auditable and easier to debug.

### Preference Memory

Use structured memory instead of RAG:

- important senders
- ignored senders
- important domains
- ignored categories
- report schedule
- user feedback

This is easier to reason about and more useful for email triage.

### Evaluation

Prepare labeled mock emails.

Measure:

- important email recall
- noise filtering precision
- false negatives
- false positives
- action suggestion quality

Interview line:

> I treated the email classifier as a product behavior that needs evaluation, not just a prompt that seems to work in demos.

## Demo Flow

Recommended demo:

1. Start server and Windows client.
2. Run `/email report`.
3. Show important emails and ignored counts.
4. Run `/email ignored`.
5. Inspect one ignored message and its reason.
6. Run `/email detail email-001`.
7. Run `/email classify email-007`.
8. Open trace and show the tool call/result chain.

After approval-gated actions are implemented, extend the demo:

1. Run `/email archive <id>`.
2. Show pending approval.
3. Approve the action.
4. Open trace and show the full chain.

## Likely Interview Questions

### Why not RAG?

Because the core problem is not retrieving knowledge from documents. It is acting safely over a mailbox using structured tools, user preferences, and provider state.

### Why mock provider first?

It makes the agent deterministic, testable, and demoable before OAuth complexity. The provider abstraction preserves the path to Gmail or Outlook.

### How autonomous is it?

Autonomous for read/classify/report. Approval-gated for any write action. No auto-send or auto-delete in MVP.

### How do you handle privacy?

Email body is truncated/redacted in trace, write actions require approval, and provider integration can start read-only.

### How do you improve quality?

Use labeled mock emails, classification metrics, user feedback, and regression tests.
