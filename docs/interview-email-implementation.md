# Interview Notes: Email Implementation

This document records implementation choices that are important for interviews.

## Mock-first Provider Strategy

The first email provider is local and deterministic.

Why this matters:

- no OAuth setup during early development
- no dependency on external mailbox state
- stable demos
- repeatable tests
- easy evaluation with labeled samples

Interview line:

> I separated provider integration from agent behavior. The first provider is mock data so the email triage workflow is deterministic, testable, and demoable before OAuth complexity.

Current implementation:

- `server/app/email_provider.py`
- `server/data/mock_emails.json`
- stable sample IDs from `email-001` to `email-012`

## Provider Abstraction

The email agent should not depend on Gmail or Outlook directly.

Provider interface:

- list recent emails
- fetch email detail
- search emails
- archive email
- mark read/unread
- star/flag email
- create draft reply

The MVP implements read paths first:

- list recent
- get detail
- search

Current tools:

- `email_list_recent`
- `email_search`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`

Current approval-gated action tools:

- `email_archive`
- `email_mark_read`
- `email_star`
- `email_create_draft`

These tools are registered as `dangerous`, so calling them creates a pending tool call first. The provider state changes only after explicit approval.

Current preference tools:

- `email_get_preferences`
- `email_add_preference`
- `email_remove_preference`
- `email_set_preference`

Preference tools are regular write tools, not dangerous mailbox mutations. They update local structured triage state for the current session.

## Classification Policy

The first classifier is rule-based. This is intentional.

Reasons:

- deterministic
- explainable
- testable without API keys
- useful baseline before adding model-based classification

The classifier should always return:

- category
- importance
- suggested action
- reason

This lets the report explain itself and makes evaluation possible.

Current classifier behavior:

- security/account-access signals become high-importance security items
- finance/billing/payment signals become high-importance finance items
- direct requests, review/confirm/verify wording, deadlines, and recruiting context become action items
- newsletters, promotions, and social digests become ignored low-priority items
- operational notifications such as CI failures remain reportable at medium importance
- important sender/domain preferences can promote low-priority messages into reportable items
- ignored sender/domain/category preferences can suppress messages and cite the matched preference

Important design note:

The deterministic classifier is not meant to be the final intelligence layer. It is a baseline that makes behavior explainable, testable, and easy to compare against a later LLM-based classifier.

## Preference Memory

Email preferences are structured memory, not RAG.

Current preference state:

- `important_senders`
- `important_domains`
- `ignored_senders`
- `ignored_domains`
- `ignored_categories`
- `report_schedule`
- `timezone`

Why this matters:

- deterministic classification overrides
- inspectable user state
- clear edit/remove semantics
- easy test coverage
- reasons can cite exact matched preference

Interview line:

> I did not use vector memory for mailbox preferences. Sender, domain, and category preferences are structured product state because the classifier needs deterministic behavior and explainable overrides.

## Tool-use Mapping

Email capabilities are exposed as tools:

- `email_list_recent`
- `email_get_detail`
- `email_classify`
- `email_report_important`
- `email_list_ignored`

This preserves the core project story: Wispera is not a chat UI with email code hidden inside it. It is an agent runtime using typed tools.

The Windows client command `/email` is only a thin command adapter:

- `/email report` calls `email_report_important`
- `/email ignored` calls `email_list_ignored`
- `/email detail <id>` calls `email_get_detail`
- `/email classify <id>` calls `email_classify`

This keeps provider logic and classification policy on the server side, where tracing, permissions, and future scheduler execution already live.

Approval-gated actions are currently tested headlessly through the generic tool commands and APIs:

- `/tool email_archive {"email_id":"email-001"}`
- `/pending`
- `/approve <pending_id>`
- `/trace <trace_id>`

This is intentional. It lets the project mature the server-side action semantics before adding dedicated Windows UI.

## Approval Safety

The action tools demonstrate a key agent safety pattern:

- the model or user can propose a mailbox mutation
- the tool registry validates arguments and permission level
- dangerous tools become pending calls instead of executing
- user approval executes the stored call
- rejection drops the call without mutating provider state
- trace records the proposal, decision, and final result

Interview line:

> I treat mailbox mutations as dangerous tool calls. Even in a mock provider, archive, mark-read, star, and draft creation go through the same pending approval flow that a real Outlook/Gmail provider would use.

## Privacy Notes

Even mock email data should follow production-like rules:

- truncate long bodies in reports
- avoid dumping entire email body into trace
- keep write actions approval-gated
