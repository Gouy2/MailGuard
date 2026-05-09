# Windows Test Plan

This document describes how the Windows client should be tested as Wispera moves toward the Email Triage Agent MVP.

Mac is currently used for code editing and service-level tests. Windows remains the target runtime for the desktop client.

## Setup

Start the server:

```bash
cd server
uv sync
uv run uvicorn app.main:app --reload
```

Start the Windows client:

```bash
cd client
uv sync
uv run python main.py
```

## Current Regression Tests

Run service-level tests first:

```bash
python -m unittest tests.test_email_tools
```

These commands verify the existing tool-use runtime while email tools are being implemented.

```text
/server
/tools
/tool read_text_file {"path":"README.md","max_chars":200}
/tool run_shell_command {"command":"rm -rf .","timeout_seconds":3}
/tool run_shell_command {"command":"dir","timeout_seconds":3}
/pending
/approve <pending_id>
/trace <trace_id>
```

Expected:

- `/server` shows service status `ok`
- `/tools` lists registered tools
- `read_text_file` executes directly
- dangerous shell command is rejected by policy
- allowed shell command still enters pending approval
- approval executes the pending call
- trace shows tool result and approval decision

## Email MVP Tests

These commands verify the mock-provider email MVP.

```text
/email report
/email report --unread --limit=10
/email ignored
/email detail email-001
/email classify email-007
/trace <trace_id>
```

Expected:

- `/email report` shows important emails and ignored counts
- every important email includes reason and suggested action
- `/email ignored` shows low-priority emails and ignore reasons
- `/email detail <email_id>` shows summary and classification reason
- `/email classify <email_id>` shows category, importance, reason, and suggested action
- trace includes the email tool call and result

Reference examples:

- `email-001` should classify as `high/action_required`
- `email-004` should classify as `low/newsletter`
- `email-005` should classify as `low/promotion`
- `email-007` should classify as `high/security`

Planned approval-flow tests for Phase 2:

```text
/email archive <email_id>
/pending
/approve <pending_id>
```

Expected after Phase 2:

- `/email archive <email_id>` creates pending approval
- `/approve <pending_id>` executes archive
- trace includes email tool calls and user approval

## Safety Checks

The MVP must pass these checks:

- No email is archived without approval.
- No email is marked read without approval.
- No draft is created without approval.
- No email is sent.
- No email is deleted.
- Trace does not store full long email bodies.

## Mock Provider Checks

Before real provider integration:

- mock dataset loads on Windows
- reports are deterministic
- email IDs are stable
- ignored and important counts match expected labels
- evaluation script can run without network access
