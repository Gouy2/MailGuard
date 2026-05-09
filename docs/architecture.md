# Architecture

Wispera uses a Windows client plus a local Agent service. The client should stay thin; the server owns email logic, tool execution, permissions, trace, scheduling, and evaluation hooks.

## Target Layers

### Windows Client

Responsibilities:

- Desktop pet / tray / chat entry
- User commands and notifications
- Display important email reports
- Show pending approvals
- Send approval/rejection decisions to the server

The client should not contain classification logic, provider SDK code, or mailbox mutation logic.

### Agent Service

Responsibilities:

- Orchestrate email triage flows
- Decide which tools to call
- Generate important email reports
- Explain why messages are important or ignored
- Route write actions through approval gates
- Store trace and evaluation records

### Tool Runtime

Responsibilities:

- Typed tool registry
- Schema validation
- Permission levels
- Pending approval queue
- Execution trace
- Error normalization

Tool permissions:

- `read`: inspect email or local state
- `write`: update local preference memory or create non-destructive output
- `dangerous`: mutate mailbox state or execute system commands

### Email Provider Layer

Initial provider:

- `MockEmailProvider`
- Loads local JSON email samples
- Supports deterministic demos and evaluation

Later providers:

- Gmail API
- Microsoft Graph / Outlook

Provider interface should support:

- list recent emails
- fetch email detail
- search emails
- archive email
- mark read/unread
- star/flag email
- create draft reply

### Preference Memory

This is structured memory, not RAG.

Examples:

- important senders
- important domains
- ignored senders
- ignored categories
- report schedule
- user feedback on past classifications

Storage can start with SQLite or JSON and later move to SQLite once write paths stabilize.

### Scheduler

Responsibilities:

- Periodically run inbox scan
- Avoid duplicate reports
- Trigger notifications only for important items
- Generate daily digest

The scheduler may classify and report autonomously. It must not mutate mailbox state without approval.

### Evaluation Layer

Responsibilities:

- Load labeled mock email samples
- Run classifier/triage pipeline
- Compare predicted labels with expected labels
- Track false positives and false negatives
- Preserve hard cases for regression tests

## Data Flow

Manual report:

```text
User command
-> Windows client
-> server / email triage endpoint
-> email tools
-> classifier / policy
-> report
-> trace
-> client display
```

Scheduled report:

```text
Scheduler tick
-> provider list_recent_emails
-> classify
-> dedupe
-> important report
-> client notification
-> trace
```

Approved write action:

```text
Agent proposes action
-> pending tool call
-> client shows approval request
-> user approves
-> provider write tool executes
-> trace records decision and result
```

## Safety Principles

- Read actions may run automatically.
- Write actions require approval.
- Delete and send are excluded from MVP.
- Email contents in trace must be truncated or redacted.
- Every classification should have a reason.
- Every ignored email should have a category/reason, at least in debug/report mode.
