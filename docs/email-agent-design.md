# Email Agent Design

This document defines the MVP behavior for Wispera as an Email Triage Agent.

## MVP User Stories

### Important Email Report

User asks:

```text
/email report
```

Wispera should:

- fetch recent emails
- classify each email
- ignore low-value messages
- summarize important messages
- explain why each message matters
- suggest next actions

Report format:

```text
Important emails: 3

1. [Action required] Jane from Acme
   Subject: Contract review
   Why: direct sender, deadline tomorrow, asks for review
   Suggested action: reply or create draft

Ignored: 12
- newsletters: 5
- promotions: 4
- automated notifications: 3
```

### Ignored Email Review

User asks:

```text
/email ignored
```

Wispera should show low-priority emails and reasons:

- newsletter
- promotion
- automated notification
- social update
- duplicate thread update

### Email Detail

User asks:

```text
/email detail <email_id>
```

Wispera should fetch the email detail, summarize it, and show classification reason.

### Approval-gated Action

User asks:

```text
/email archive <email_id>
```

Wispera should create a pending tool call. The action executes only after approval.

## Email Classification

Suggested labels:

- `important`
- `action_required`
- `security`
- `finance`
- `meeting`
- `personal`
- `newsletter`
- `promotion`
- `notification`
- `noise`

Suggested importance levels:

- `high`
- `medium`
- `low`

Suggested action labels:

- `reply`
- `review`
- `schedule`
- `pay_attention`
- `archive`
- `ignore`
- `draft_reply`

## Importance Signals

Positive signals:

- direct sender
- known important sender/domain
- asks user to take action
- contains deadline
- security or account access
- financial/billing issue
- interview/recruiting/client context
- meeting change or cancellation
- thread from real person rather than bulk sender

Negative signals:

- unsubscribe link
- marketing language
- bulk sender
- tracking links
- social digest
- no direct action
- repeated automated notification
- promotion/coupon/sale

## Tool Set

Read tools:

- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`

Write tools:

- `email_save_preference`

Dangerous tools:

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

Excluded from MVP:

- `email_send`
- `email_delete`

## Phase 1 Implementation Boundary

The first implementation round ships only local, deterministic read behavior:

- load messages from `server/data/mock_emails.json`
- expose read-only email tools through the existing tool registry
- classify messages with deterministic rules
- return category, importance, suggested action, and reasons for each decision
- support `/email report`, `/email ignored`, `/email detail <id>`, and `/email classify <id>` from the Windows client

This phase does not implement OAuth, real mailbox access, scheduler autonomy, archive/mark-read/star/draft actions, or preference memory. Those features remain planned, but keeping Phase 1 read-only makes the project demoable and testable quickly.

## Phase 2 Implementation Boundary

The second implementation round ships headless approval-gated mailbox actions:

- expose `email_archive`, `email_mark_read`, `email_star`, and `email_create_draft` as dangerous tools
- keep all mutation logic on the server side
- require the existing pending approval flow before any action executes
- store mock provider state in memory only, so tests remain deterministic and do not rewrite the JSON fixture
- verify the flow through service tests, `/tool`, `/pending`, `/approve`, `/reject`, and `/trace`

This phase does not add a dedicated Windows UI for mailbox actions. The client remains a thin command shell until the server-side action semantics, trace, and tests are stable.

## Phase 3 Implementation Boundary

The third implementation round ships structured preference memory:

- store email-specific preferences separately from free-form chat notes
- support important senders/domains, ignored senders/domains, ignored categories, report schedule, and timezone
- expose preference tools through the existing tool registry
- make classification cite matched preferences in its reasons
- keep the implementation headless-first and testable through service tests and `/tool`

This phase does not use vector search or RAG. Email triage preferences are structured product state because the classifier needs deterministic rules, inspectability, and predictable overrides.

## Mock Email Provider

The first provider should be deterministic and local.

Suggested file:

```text
server/data/mock_emails.json
```

Suggested fields:

- `id`
- `thread_id`
- `from_name`
- `from_email`
- `to`
- `subject`
- `snippet`
- `body`
- `received_at`
- `labels`
- `is_read`
- `has_attachments`
- `expected_category`
- `expected_importance`

This lets the project demonstrate behavior before real OAuth integration.

## Preference Memory

Preference examples:

```json
{
  "important_senders": ["hr@example.com"],
  "important_domains": ["company.com"],
  "ignored_senders": ["newsletter@example.com"],
  "ignored_categories": ["promotion"],
  "report_schedule": "09:00",
  "timezone": "Asia/Shanghai"
}
```

Memory should be inspectable and editable.

## Scheduler Behavior

The scheduler should:

- run every N minutes or once per day
- fetch recent unread emails
- skip emails already reported
- notify only when high-importance emails exist
- generate daily digest

The scheduler must not:

- archive automatically
- mark as read automatically
- create drafts automatically
- send or delete email

## Trace Requirements

Each report should record:

- provider used
- number of emails fetched
- classification result per email
- ignored reason
- important reason
- suggested action
- pending tool calls

Email body in trace should be truncated or redacted.
