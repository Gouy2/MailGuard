# Roadmap

The goal is to ship a focused interview-ready Email Triage Agent quickly, while preserving enough architecture depth to discuss tool use, safety, autonomy, memory, and evaluation.

## Phase 0 - Rescope

Goal: replace the broad desktop AI assistant plan with a focused email triage plan.

Deliverables:

- Rewrite docs around Email Triage Agent
- Define MVP/non-goals
- Define safety boundaries
- Define provider abstraction
- Define evaluation plan

Status: complete.

## Phase 1 - Email Tool Foundation

Goal: implement email tools against mock data.

Deliverables:

- `MockEmailProvider` - complete
- Mock email JSON dataset - complete
- `email_list_recent` - complete
- `email_search` - complete
- `email_get_detail` - complete
- `email_classify` - complete
- `email_report_important` - complete
- `email_list_ignored` - complete
- trace for every email tool call

Acceptance:

- No real email credentials required
- `/email report` works from Windows client
- Important/ignored decisions include reasons
- Tests can run deterministically

Implementation note:

- Start with a deterministic rule-based classifier before adding any LLM classification.

Status: implemented locally against mock data. Needs Windows client regression test after push.

## Phase 2 - Approval-gated Actions

Goal: safely support mailbox actions.

Deliverables:

- `archive_email`
- `mark_email_read`
- `star_email`
- `create_draft_reply`
- pending approval flow
- action trace

Acceptance:

- Write tools never execute without approval
- Client can approve/reject action
- Trace shows proposed action, user decision, and provider result

MVP excludes:

- send email
- delete email

## Phase 3 - Preference Memory

Goal: make triage adapt to the user without RAG.

Deliverables:

- Important sender/domain rules
- Ignored sender/category rules
- User feedback commands
- Preference storage
- Classification reasons that cite matching preferences

Acceptance:

- User can mark a sender as important or ignored
- Future reports reflect those preferences
- Preferences are inspectable and editable

## Phase 4 - Scheduler / Autonomy

Goal: add controlled autonomy.

Deliverables:

- periodic inbox scan
- dedupe reported emails
- important-only notification
- daily digest
- scheduler trace

Acceptance:

- The system can autonomously read and classify
- It never mutates mailbox state without approval
- Duplicate notifications are avoided

## Phase 5 - Evaluation

Goal: make quality measurable for interviews.

Deliverables:

- 30-50 labeled mock emails
- expected category/importance/action labels
- evaluation script
- confusion summary
- regression cases

Acceptance:

- Classification quality can be measured
- Failures can be explained
- Prompt/policy changes can be compared

## Phase 6 - Real Provider

Goal: connect one real email provider after the mock flow is solid.

Recommended order:

1. Microsoft Graph / Outlook if targeting Windows/enterprise story
2. Gmail if targeting general user story

Acceptance:

- OAuth setup documented
- Read-only flow works first
- Write actions still go through approval
