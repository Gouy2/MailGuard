"""Human-readable rendering for the local email CLI."""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable, TextIO

from ..archive_shadow_workflow import proposal_item_id as _proposal_item_id
from .label import run_interactive_labeling, run_interactive_proposal_labeling


def print_human(args: argparse.Namespace, result: dict[str, Any], *, stdout: TextIO, stderr: TextIO) -> None:
    if result.get("requires_approval") and not result.get("approved"):
        _print_approval_preview(result, stdout)
        return
    if not result.get("ok"):
        _print_error(result, stderr)
        return

    renderer = COMMAND_RENDERERS.get(args.display_command)
    if renderer is not None:
        renderer(args, result, stdout)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)


def is_success(result: dict[str, Any]) -> bool:
    if result.get("ok"):
        return True
    return bool(result.get("requires_approval") and not result.get("approved") and not result.get("error"))


def _print_status(status: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {status.get('provider', '')}", file=out)
    if status.get("email"):
        print(f"Email: {status['email']}", file=out)
    if status.get("host"):
        print(f"IMAP: {status.get('host')}:{status.get('port')}", file=out)
    print(f"Configured mailbox: {_mailbox_label(status, 'mailbox')}", file=out)
    if "message_count" in status:
        print(f"Messages: {status.get('message_count')} total, {status.get('unread_count')} unread", file=out)
    if "selected_message_count" in status:
        print(f"Selected mailbox EXISTS: {status.get('selected_message_count')}", file=out)
    if "uid_search_all_count" in status:
        print(f"Selected mailbox UID SEARCH ALL: {status.get('uid_search_all_count')}", file=out)
    if status.get("visible_mailbox_count") is not None:
        print(f"Visible mailboxes: {status.get('visible_mailbox_count')}", file=out)
    if "archive_mailbox" in status:
        print(
            f"Archive mailbox: {_mailbox_label(status, 'archive_mailbox')} "
            f"({_exists_text(status.get('archive_mailbox_exists'))})",
            file=out,
        )
    if "drafts_mailbox" in status:
        print(
            f"Drafts mailbox: {_mailbox_label(status, 'drafts_mailbox')} "
            f"({_exists_text(status.get('drafts_mailbox_exists'))})",
            file=out,
        )
    if status.get("mailbox_counts"):
        print("Mailbox counts:", file=out)
        for item in status["mailbox_counts"]:
            marker = "*" if item.get("selected") else "-"
            if item.get("error"):
                print(f"  {marker} {item.get('name', '')}: error: {item['error']}", file=out)
                continue
            if not item.get("selectable", True):
                print(f"  {marker} {item.get('name', '')}: not selectable", file=out)
                continue
            if not item.get("status_available", True):
                print(f"  {marker} {item.get('name', '')}: status unavailable", file=out)
                continue
            print(
                f"  {marker} {item.get('name', '')}: {item.get('message_count')} total, "
                f"{item.get('unread_count')} unread",
                file=out,
            )


def _print_mailboxes(result: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    configured = result.get("configured") or {}
    if configured:
        print("Configured:", file=out)
        for key in ("mailbox", "archive_mailbox", "drafts_mailbox"):
            if key in configured:
                print(f"  {key}: {configured[key]}", file=out)
    print("Mailboxes:", file=out)
    for item in result.get("mailboxes", []):
        flags = " ".join(item.get("flags", []))
        suffix = f" [{flags}]" if flags else ""
        print(f"  - {item.get('name', '')}{suffix}", file=out)


def _print_email_list(result: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    if "query" in result:
        print(f"Query: {result.get('query')}", file=out)
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, item in enumerate(result.get("emails", []), start=1):
        read_state = "read" if item.get("is_read") else "unread"
        print(f"{index}. {item.get('id', '')} [{read_state}] {item.get('received_at', '')}", file=out)
        print(f"   From: {_format_sender(item)}", file=out)
        print(f"   Subject: {_clip(item.get('subject', ''), 140)}", file=out)
        snippet = _clip(item.get("snippet", ""), 180)
        if snippet:
            print(f"   Snippet: {snippet}", file=out)


def _print_detail(result: dict[str, Any], out: TextIO, *, show_body: bool) -> None:
    item = result.get("email", {})
    classification = result.get("classification", {})
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(f"ID: {item.get('id', '')}", file=out)
    print(f"From: {_format_sender(item)}", file=out)
    print(f"To: {', '.join(item.get('to', []))}", file=out)
    print(f"Subject: {item.get('subject', '')}", file=out)
    print(f"Received: {item.get('received_at', '')}", file=out)
    print(f"Read: {bool(item.get('is_read'))}", file=out)
    print(f"Labels: {', '.join(item.get('labels', []))}", file=out)
    print(
        "Classification: "
        f"{classification.get('category', '')} / {classification.get('importance', '')} / "
        f"{classification.get('suggested_action', '')}",
        file=out,
    )
    reasons = classification.get("reasons") or []
    if reasons:
        print(f"Reasons: {'; '.join(str(reason) for reason in reasons)}", file=out)
    if show_body:
        print("Body:", file=out)
        print(item.get("body", ""), file=out)
        if item.get("body_truncated"):
            print("[body truncated]", file=out)
    else:
        print("Body: omitted; pass --body to print a truncated preview", file=out)


def _print_report(result: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(
        f"Fetched: {result.get('fetched', 0)}, important: {result.get('important_count', 0)}, "
        f"ignored: {result.get('ignored_count', 0)}",
        file=out,
    )
    ignored_summary = result.get("ignored_summary") or {}
    if ignored_summary:
        print(f"Ignored summary: {ignored_summary}", file=out)
    for index, item in enumerate(result.get("important", []), start=1):
        print(
            f"{index}. {item.get('email_id', '')} [{item.get('importance', '')}/{item.get('category', '')}] "
            f"{_clip(item.get('subject', ''), 140)}",
            file=out,
        )
        print(f"   From: {_format_sender(item)}", file=out)
        print(f"   Action: {item.get('suggested_action', '')}", file=out)
        reasons = item.get("reasons") or []
        if reasons:
            print(f"   Reasons: {'; '.join(str(reason) for reason in reasons)}", file=out)


def _print_daily_report(result: dict[str, Any], out: TextIO) -> None:
    provider = result.get("provider", {})
    print(f"Run: {result.get('run_id', '')} [{result.get('status', '')}]", file=out)
    print(f"Planner: {result.get('planner', '')}", file=out)
    print(f"Provider: {provider.get('provider', '')}", file=out)
    mailbox = provider.get("mailbox_display") or provider.get("mailbox") or provider.get("selected_mailbox")
    if mailbox:
        print(f"Mailbox: {mailbox}", file=out)
    print(f"Artifact: {result.get('artifact_path', '')}", file=out)
    print(f"Steps: {len(result.get('steps', []))}", file=out)
    print(f"Key items: {len(result.get('items', []))}", file=out)
    for index, item in enumerate(result.get("items", []), start=1):
        print(
            f"{index}. {item.get('email_id', '')} [{item.get('priority', '')}] "
            f"{_clip(item.get('subject', ''), 140)}",
            file=out,
        )
        print(f"   From: {_format_sender(item)}", file=out)
        if item.get("reason"):
            print(f"   Reason: {_clip(item.get('reason', ''), 180)}", file=out)
    if result.get("report"):
        print("Report:", file=out)
        print(result.get("report", ""), file=out)


def _print_clean_preview(result: dict[str, Any], out: TextIO, *, show_protected: bool = False) -> None:
    provider = result.get("provider", {})
    print(f"Run: {result.get('run_id', '')} [{result.get('status', '')}]", file=out)
    print(f"Mode: {result.get('execution_mode', '')}", file=out)
    print(f"Provider: {provider.get('provider', '')}", file=out)
    mailbox = provider.get("mailbox_display") or provider.get("mailbox") or provider.get("selected_mailbox")
    if mailbox:
        print(f"Mailbox: {mailbox}", file=out)
    print(f"Artifact: {result.get('artifact_path', '')}", file=out)
    print(
        f"Fetched: {result.get('fetched', 0)}, auto-eligible: {result.get('auto_eligible_count', 0)}, "
        f"protected: {result.get('protected_count', 0)}, candidates: {result.get('candidate_count', 0)}, "
        f"no action: {result.get('no_action_count', 0)}",
        file=out,
    )
    print(
        f"Clean rules: {result.get('enabled_clean_rule_count', 0)} enabled "
        f"({result.get('archive_rule_count', 0)} archive, {result.get('protect_rule_count', 0)} protect)",
        file=out,
    )
    print(
        f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}, "
        f"proposal mutation: {bool(result.get('proposal_mutation', False))}, "
        f"LLM authorization: {bool(result.get('llm_authorization', False))}",
        file=out,
    )
    if result.get("auto_eligible"):
        print("Auto-eligible:", file=out)
        _print_proposal_items(result.get("auto_eligible", [])[:10], out)
    if result.get("candidates"):
        print("Candidates:", file=out)
        _print_proposal_items(result.get("candidates", [])[:10], out)
    if show_protected and result.get("protected"):
        print("Protected:", file=out)
        _print_protected_items(result.get("protected", [])[:10], out)


def _print_teach(result: dict[str, Any], out: TextIO) -> None:
    print(f"Parser: {result.get('parser', '')}", file=out)
    print(f"Rules: {result.get('rule_count', 0)} total, {result.get('created_count', 0)} created", file=out)
    print(
        f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}, "
        f"rule mutation: {bool(result.get('rule_mutation', False))}, "
        f"LLM authorization: {bool(result.get('llm_authorization', False))}",
        file=out,
    )
    if result.get("rules"):
        print("Proposed rules:", file=out)
        _print_clean_rule_items(result.get("rules", []), out)
    impact = result.get("impact") or {}
    rows = impact.get("rules") or []
    if rows:
        print(f"Impact preview: fetched {impact.get('fetched', 0)} recent emails", file=out)
        for item in rows:
            print(
                f"- {item.get('rule_id', '')}: matches {item.get('match_count', 0)}, "
                f"would archive {item.get('would_auto_archive_count', 0)}, "
                f"would protect {item.get('would_protect_count', 0)}, "
                f"blocked {item.get('blocked_by_guard_count', 0)}",
                file=out,
            )
            for example in item.get("examples", [])[:3]:
                print(
                    f"  * {example.get('email_id', '')} "
                    f"[{example.get('importance', '')}/{example.get('category', '')}] "
                    f"{_clip(example.get('subject', ''), 120)}",
                    file=out,
                )


def _print_clean_rules(result: dict[str, Any], out: TextIO) -> None:
    status = result.get("status") or "all"
    print(f"Status: {status}", file=out)
    print(f"Count: {result.get('count', 0)}", file=out)
    _print_clean_rule_items(result.get("rules", []), out)


def _print_clean_run(result: dict[str, Any], out: TextIO, *, show_protected: bool = False) -> None:
    _print_clean_preview(result, out, show_protected=show_protected)
    print(
        f"Selected: {result.get('selected_count', 0)}, executed: {result.get('executed_count', 0)}, "
        f"failed: {result.get('failed_count', 0)}, skipped: {result.get('skipped_count', 0)}",
        file=out,
    )
    print(
        f"Audit mutation: {bool(result.get('audit_mutation', False))}, "
        f"audit events: {result.get('audit_event_count', 0)}",
        file=out,
    )
    if result.get("approval_hint"):
        print(result.get("approval_hint", ""), file=out)
    if result.get("executed"):
        print("Executed:", file=out)
        _print_proposal_items(result.get("executed", [])[:10], out)
    if result.get("failed"):
        print("Failed:", file=out)
        _print_proposal_items(result.get("failed", [])[:10], out)
    if result.get("skipped"):
        print("Skipped:", file=out)
        _print_proposal_items(result.get("skipped", [])[:10], out)


def _print_clean_audit(result: dict[str, Any], out: TextIO) -> None:
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, item in enumerate(result.get("events", []), start=1):
        print(
            f"{index}. {item.get('event_id', '')} "
            f"{item.get('event_type', '')} run={item.get('run_id', '')} "
            f"email={item.get('email_id', '')} actor={item.get('actor', '')}",
            file=out,
        )
        payload = dict(item.get("payload") or {})
        if payload.get("clean_rule_match"):
            rule = payload["clean_rule_match"]
            print(
                f"   Rule: {rule.get('action', '')} {rule.get('scope', '')}:{rule.get('value', '')}",
                file=out,
            )
        if payload.get("memory_match"):
            print(f"   Memory: {payload.get('memory_match', '')}", file=out)
        if payload.get("error"):
            print(f"   Error: {_clip(payload.get('error', ''), 180)}", file=out)


def _print_clean_rule_decision(command: str, result: dict[str, Any], out: TextIO) -> None:
    rule = result.get("rule", {})
    print(f"{command}: {rule.get('rule_id', '')}", file=out)
    print(f"Status: {rule.get('status', '')}", file=out)
    print(f"Rule: {rule.get('action', '')} {rule.get('scope', '')}:{rule.get('value', '')}", file=out)
    print(f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}", file=out)


def _print_clean_rule_items(rules: list[dict[str, Any]], out: TextIO) -> None:
    for index, rule in enumerate(rules, start=1):
        status = str(rule.get("status", ""))
        if "created" in rule:
            suffix = "created" if rule.get("created") else "existing"
            status = f"{status}/{suffix}"
        print(
            f"{index}. {rule.get('rule_id', '')} "
            f"[{status}] "
            f"{rule.get('action', '')} {rule.get('scope', '')}:{rule.get('value', '')}",
            file=out,
        )
        if rule.get("reason"):
            print(f"   Reason: {_clip(rule.get('reason', ''), 180)}", file=out)


def _print_review(result: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(f"Fetched: {result.get('fetched', 0)}", file=out)
    for index, item in enumerate(result.get("emails", []), start=1):
        email = item.get("email", {})
        classification = item.get("classification", {})
        reportable = "report" if classification.get("is_reportable") else "suppress"
        print(
            f"{index}. {email.get('id', '')} "
            f"[{classification.get('importance', '')}/{classification.get('category', '')}/{reportable}]",
            file=out,
        )
        print(f"   From: {_format_sender(email)}", file=out)
        print(f"   Subject: {_clip(email.get('subject', ''), 140)}", file=out)
        snippet = _clip(email.get("snippet", ""), 180)
        if snippet:
            print(f"   Snippet: {snippet}", file=out)
        if item.get("error"):
            print(f"   Error: {item['error']}", file=out)
            continue
        print(f"   Action: {classification.get('suggested_action', '')}", file=out)
        reasons = classification.get("reasons") or []
        if reasons:
            print(f"   Reasons: {'; '.join(str(reason) for reason in reasons)}", file=out)
    print("Labels: important | later | ignore", file=out)
    if result.get("interactive_labeling"):
        print("Interactive input: important/i | later/l | ignore/n | skip/s | quit/q", file=out)
    else:
        print("Example: uv run python email_cli.py label imap-123 important", file=out)


def _print_label(result: dict[str, Any], out: TextIO) -> None:
    record = result.get("record", {})
    print(f"Saved label: {record.get('email_id', '')} -> {record.get('label', '')}", file=out)
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    if record.get("predicted_category"):
        print(
            "Prediction: "
            f"{record.get('predicted_category', '')} / {record.get('predicted_importance', '')} / "
            f"{record.get('predicted_action', '')}",
            file=out,
        )


def _print_labels(result: dict[str, Any], out: TextIO) -> None:
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, record in enumerate(result.get("labels", []), start=1):
        print(
            f"{index}. {record.get('email_id', '')} -> {record.get('label', '')} "
            f"[{record.get('predicted_importance', '')}/{record.get('predicted_category', '')}]",
            file=out,
        )
        print(f"   Subject: {_clip(record.get('subject', ''), 140)}", file=out)


def _print_eval_real(result: dict[str, Any], out: TextIO) -> None:
    evaluation = result.get("evaluation", {})
    metrics = evaluation.get("metrics", {})
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Sample count: {evaluation.get('sample_count', 0)}", file=out)
    print(f"Label counts: {evaluation.get('label_counts', {})}", file=out)
    for key in sorted(metrics):
        print(f"{key}: {metrics[key]}", file=out)
    mismatches = evaluation.get("mismatches", [])
    print(f"Mismatch count: {len(mismatches)}", file=out)
    for item in mismatches:
        print(
            f"- {item.get('email_id', '')}: label={item.get('label', '')}, "
            f"predicted={item.get('predicted_importance', '')}/{item.get('predicted_category', '')}",
            file=out,
        )


def _print_eval_proposals(result: dict[str, Any], out: TextIO) -> None:
    metrics = result.get("metrics", {})
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(f"Classifier: {result.get('classifier', '')}", file=out)
    print(f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}", file=out)
    print(
        f"Sample count: {result.get('sample_count', 0)}, proposals: {result.get('proposal_count', 0)}, "
        f"eligible safe archive: {result.get('eligible_safe_archive_count', 0)}",
        file=out,
    )
    for key in sorted(metrics):
        print(f"{key}: {metrics[key]}", file=out)
    false_positives = result.get("false_positive_proposals", [])
    missed = result.get("missed_safe_archive", [])
    print(f"False positive proposals: {len(false_positives)}", file=out)
    for item in false_positives:
        print(f"- {item.get('email_id', '')}: {_clip(item.get('subject', ''), 140)}", file=out)
    print(f"Missed safe archive: {len(missed)}", file=out)
    for item in missed[:10]:
        print(f"- {item.get('email_id', '')}: {_clip(item.get('subject', ''), 140)}", file=out)


def _print_scan(result: dict[str, Any], out: TextIO) -> None:
    scan = (result.get("scheduler") or {}).get("scan", {})
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(f"Scan ID: {scan.get('scan_id', '')}", file=out)
    print(
        f"Fetched: {scan.get('fetched', 0)}, classified: {scan.get('classified_count', 0)}, "
        f"reportable: {scan.get('reportable_count', 0)}, ignored: {scan.get('ignored_count', 0)}",
        file=out,
    )
    print(
        f"Created notifications: {scan.get('created_notification_count', 0)}, "
        f"skipped duplicates: {scan.get('skipped_duplicate_count', 0)}",
        file=out,
    )


def _print_notifications(result: dict[str, Any], out: TextIO) -> None:
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, item in enumerate(result.get("notifications", []), start=1):
        print(
            f"{index}. {item.get('notification_id', '')} "
            f"[{item.get('status', '')}/{item.get('importance', '')}] {item.get('email_id', '')}",
            file=out,
        )
        print(f"   Subject: {_clip(item.get('subject', ''), 140)}", file=out)
        print(f"   Action: {item.get('suggested_action', '')}", file=out)


def _print_propose(result: dict[str, Any], out: TextIO, *, show_protected: bool = False) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(
        f"Fetched: {result.get('fetched', 0)}, proposals: {result.get('proposal_count', 0)}, "
        f"created: {result.get('created_count', 0)}, duplicates: {result.get('duplicate_count', 0)}",
        file=out,
    )
    print(
        f"Protected: {result.get('protected_count', result.get('important_count', 0))}, "
        f"candidates: {result.get('candidate_count', result.get('review_count', 0))}, "
        f"no action: {result.get('no_action_count', 0)}",
        file=out,
    )
    if result.get("proposals"):
        print("Proposals:", file=out)
        _print_proposal_items(result.get("proposals", []), out)
    if result.get("candidates"):
        print("Candidates:", file=out)
        _print_proposal_items(result.get("candidates", []), out)
    if show_protected and result.get("protected"):
        print("Protected:", file=out)
        _print_protected_items(result.get("protected", []), out)


def _print_proposals(result: dict[str, Any], out: TextIO) -> None:
    status = result.get("status") or "all"
    print(f"Status: {status}", file=out)
    print(f"Count: {result.get('count', 0)}", file=out)
    _print_proposal_items(result.get("proposals", []), out)


def _print_proposal_label_help(result: dict[str, Any], out: TextIO) -> None:
    if not result.get("label_help"):
        return
    print("Labels: archive | keep | unsure", file=out)
    if result.get("interactive_labeling"):
        print("Interactive input: archive/a | keep/k | unsure/u | skip/s | quit/q", file=out)
    else:
        print("Example: uv run python email_cli.py label-proposal proposal-123 archive", file=out)


def _print_proposal_items(proposals: list[dict[str, Any]], out: TextIO) -> None:
    for index, item in enumerate(proposals, start=1):
        print(
            f"{index}. {_proposal_item_id(item)} "
            f"[{item.get('status', item.get('item_type', ''))}/{item.get('risk_level', '')}] "
            f"{item.get('action', '')} {item.get('email_id', '')}",
            file=out,
        )
        print(f"   From: {_format_sender(item)}", file=out)
        print(f"   Subject: {_clip(item.get('subject', ''), 140)}", file=out)
        if item.get("reason"):
            print(f"   Reason: {_clip(item.get('reason', ''), 220)}", file=out)


def _print_protected_items(items: list[dict[str, Any]], out: TextIO) -> None:
    for index, item in enumerate(items, start=1):
        print(
            f"{index}. {item.get('email_id', '')} "
            f"[{item.get('importance', '')}/{item.get('category', '')}/{item.get('suggested_action', '')}]",
            file=out,
        )
        print(f"   From: {_format_sender(item)}", file=out)
        print(f"   Subject: {_clip(item.get('subject', ''), 140)}", file=out)
        if item.get("policy_reason"):
            print(f"   Policy: {_clip(item.get('policy_reason', ''), 220)}", file=out)


def _print_proposal_label(result: dict[str, Any], out: TextIO) -> None:
    record = result.get("record", {})
    print(f"Saved proposal label: {_proposal_item_id(record)} -> {record.get('label', '')}", file=out)
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Email: {record.get('email_id', '')}", file=out)
    if record.get("subject"):
        print(f"Subject: {_clip(record.get('subject', ''), 140)}", file=out)


def _print_proposal_labels(result: dict[str, Any], out: TextIO) -> None:
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, record in enumerate(result.get("labels", []), start=1):
        print(
            f"{index}. {_proposal_item_id(record)} -> {record.get('label', '')} "
            f"{record.get('action', '')} {record.get('email_id', '')}",
            file=out,
        )
        print(f"   Subject: {_clip(record.get('subject', ''), 140)}", file=out)


def _print_eval_real_proposals(result: dict[str, Any], out: TextIO) -> None:
    evaluation = result.get("evaluation", {})
    metrics = evaluation.get("metrics", {})
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(
        f"Sample count: {evaluation.get('sample_count', 0)}, "
        f"decisive: {evaluation.get('decisive_count', 0)}",
        file=out,
    )
    print(f"Label counts: {evaluation.get('label_counts', {})}", file=out)
    for key in sorted(metrics):
        print(f"{key}: {metrics[key]}", file=out)
    for item_type, summary in sorted((evaluation.get("by_item_type") or {}).items()):
        item_metrics = summary.get("metrics", {})
        print(
            f"{item_type}: sample={summary.get('sample_count', 0)}, "
            f"decisive={summary.get('decisive_count', 0)}, "
            f"labels={summary.get('label_counts', {})}, "
            f"archive_acceptance_precision={item_metrics.get('archive_acceptance_precision', 0.0)}",
            file=out,
        )
    false_positives = evaluation.get("false_positive_proposals", [])
    print(f"False positive proposals: {len(false_positives)}", file=out)
    for item in false_positives:
        print(f"- {_proposal_item_id(item)}: {_clip(item.get('subject', ''), 140)}", file=out)


def _print_llm_archive_shadow(result: dict[str, Any], out: TextIO) -> None:
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Shadow file: {result.get('shadow_path', '')}", file=out)
    print(f"Memory file: {result.get('memory_path', '')}", file=out)
    print(f"Model: {result.get('model', '')}", file=out)
    print(
        f"Labels: {result.get('label_count', 0)}, selected: {result.get('selected_count', 0)}, "
        f"dry-run: {result.get('dry_run_count', 0)}, "
        f"scored: {result.get('scored_count', 0)}, skipped: {result.get('skipped_count', 0)}, "
        f"errors: {result.get('error_count', 0)}",
        file=out,
    )
    print(
        f"Total elapsed: {result.get('total_elapsed_ms', 0)}ms, "
        f"avg scored latency: {result.get('avg_latency_ms', 0)}ms",
        file=out,
    )
    print(
        f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}, "
        f"proposal mutation: {bool(result.get('proposal_mutation', False))}",
        file=out,
    )
    for index, record in enumerate(result.get("records", [])[:20], start=1):
        judgment = record.get("judgment", {})
        print(
            f"{index}. {record.get('item_id', '')} [{record.get('policy_bucket', '')}] "
            f"{judgment.get('archive_suitability', '')}/{judgment.get('confidence', '')} "
            f"{'dry-run ' if record.get('dry_run') else ''}"
            f"{'cached ' if record.get('cached') else ''}"
            f"{record.get('elapsed_ms', 0)}ms "
            f"{record.get('email_id', '')}",
            file=out,
        )
        print(f"   Subject: {_clip(record.get('subject', ''), 140)}", file=out)
        diagnostics = record.get("diagnostics") or {}
        if diagnostics:
            print(
                f"   Input: prompt={diagnostics.get('prompt_chars', 0)} chars, "
                f"request={diagnostics.get('request_chars', 0)} chars, "
                f"snippet={diagnostics.get('snippet_chars', 0)} chars, "
                f"memory_matches={diagnostics.get('memory_match_count', 0)}, "
                f"body_included={bool(diagnostics.get('body_included', False))}, "
                f"request_elapsed={diagnostics.get('request_elapsed_ms', 0)}ms",
                file=out,
            )
        if record.get("error"):
            print(f"   Error: {_clip(record.get('error', ''), 180)}", file=out)
        elif judgment.get("brief_reason"):
            print(f"   Reason: {_clip(judgment.get('brief_reason', ''), 180)}", file=out)
    slowest = list(result.get("slowest_items", []))
    if slowest:
        print("Slowest items:", file=out)
        for item in slowest:
            print(
                f"- {item.get('item_id', '')}: {item.get('elapsed_ms', 0)}ms "
                f"{item.get('archive_suitability', '')}/{item.get('confidence', '')} "
                f"{_clip(item.get('subject', ''), 120)}",
                file=out,
            )


def _print_eval_archive_shadow(result: dict[str, Any], out: TextIO) -> None:
    evaluation = result.get("evaluation", {})
    metrics = evaluation.get("metrics", {})
    readiness = evaluation.get("readiness", {})
    thresholds = readiness.get("thresholds", {})
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Shadow file: {result.get('shadow_path', '')}", file=out)
    print(
        f"Label sample count: {evaluation.get('label_sample_count', 0)}, "
        f"matched: {evaluation.get('matched_count', 0)}, decisive: {evaluation.get('decisive_count', 0)}",
        file=out,
    )
    print(f"Prediction counts: {evaluation.get('prediction_counts', {})}", file=out)
    for key in sorted(metrics):
        print(f"{key}: {metrics[key]}", file=out)
    if readiness:
        print("Readiness:", file=out)
        print(f"  ready_for_policy_experiment: {bool(readiness.get('ready_for_policy_experiment', False))}", file=out)
        print(f"  recommendation: {readiness.get('recommendation', '')}", file=out)
        print(
            "  gates: "
            f"decisive>={thresholds.get('min_decisive_labels', 0)}, "
            f"precision>={thresholds.get('target_archive_yes_precision', 0)}, "
            f"false_positive<={thresholds.get('max_false_positive_count', 0)}, "
            f"avg_latency<={thresholds.get('max_avg_latency_ms', 0)}ms",
            file=out,
        )
        print(
            "  status: "
            f"labels={bool(readiness.get('decisive_labels_ready', False))}, "
            f"prediction={bool(readiness.get('prediction_ready', False))}, "
            f"precision={bool(readiness.get('precision_ready', False))}, "
            f"false_positive={bool(readiness.get('false_positive_ready', False))}, "
            f"errors={bool(readiness.get('error_ready', False))}, "
            f"latency={bool(readiness.get('latency_ready', False))}",
            file=out,
        )
        for note in readiness.get("notes", [])[:3]:
            print(f"  note: {note}", file=out)
    for title, key in (
        ("False positive shadow", "false_positive_shadow"),
        ("Missed archive shadow", "missed_archive_shadow"),
        ("Unsure shadow", "unsure_shadow"),
    ):
        rows = list(evaluation.get(key, []))
        print(f"{title}: {len(rows)}", file=out)
        for item in rows[:10]:
            print(f"- {item.get('item_id', '')}: {_clip(item.get('subject', ''), 140)}", file=out)


def _print_observed_memory(result: dict[str, Any], out: TextIO) -> None:
    report = result.get("report", {})
    limit = max(1, int(result.get("limit", 20)))
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(
        f"Samples: {report.get('sample_count', 0)}, decisive: {report.get('decisive_count', 0)}, "
        f"labels: {report.get('label_counts', {})}",
        file=out,
    )
    print(f"Mailbox mutation: {bool(report.get('mailbox_mutation', False))}", file=out)

    insights = list(report.get("insights", []))
    print(f"Insights: {len(insights)}", file=out)
    for index, insight in enumerate(insights[:limit], start=1):
        print(
            f"{index}. {insight.get('kind', '')} "
            f"{insight.get('group_type', '')}={insight.get('key', '')} "
            f"archive={insight.get('archive_count', 0)}/{insight.get('sample_count', 0)} "
            f"keep={insight.get('keep_count', 0)} "
            f"rate={insight.get('archive_rate', 0.0)} "
            f"confidence={insight.get('confidence', '')}",
            file=out,
        )
        examples = insight.get("examples", [])
        for example in examples[:2]:
            print(
                f"   - {example.get('item_type', '')} {example.get('label', '')}: "
                f"{_clip(example.get('subject', ''), 120)}",
                file=out,
            )

    proposed = list(report.get("proposed_preferences", []))
    print(f"Proposed preferences: {len(proposed)} (observed only, not applied)", file=out)
    for item in proposed[:limit]:
        print(
            f"- {item.get('proposal', '')}={item.get('value', '')} "
            f"confidence={item.get('confidence', '')} "
            f"samples={item.get('sample_count', 0)} "
            f"archive_rate={item.get('archive_rate', 0.0)}",
            file=out,
        )


def _print_memory_proposals(result: dict[str, Any], out: TextIO) -> None:
    print(f"Labels file: {result.get('labels_path', '')}", file=out)
    print(f"Memory file: {result.get('memory_path', '')}", file=out)
    print(
        f"Created: {result.get('created_count', 0)}, updated: {result.get('updated_count', 0)}, "
        f"total: {result.get('total_count', 0)}, listed: {result.get('count', 0)}",
        file=out,
    )
    print(
        f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}, "
        f"policy mutation: {bool(result.get('policy_mutation', False))}",
        file=out,
    )
    _print_memory_items(result.get("proposals", []), out)
    print("Confirmed memory:", file=out)
    _print_confirmed_memory_block(result.get("confirmed_memory", {}), out)


def _print_memory_items(items: list[dict[str, Any]], out: TextIO) -> None:
    for index, item in enumerate(items, start=1):
        print(
            f"{index}. {item.get('proposal_id', '')} "
            f"[{item.get('status', '')}/{item.get('confidence', '')}] "
            f"{item.get('memory_type', '')}={item.get('value', '')} "
            f"archive={item.get('archive_count', 0)}/{item.get('sample_count', 0)} "
            f"keep={item.get('keep_count', 0)} "
            f"rate={item.get('archive_rate', 0.0)} "
            f"policy_applied={bool(item.get('applied_to_policy', False))}",
            file=out,
        )
        for example in list(item.get("examples", []))[:2]:
            print(
                f"   - {example.get('item_type', '')} {example.get('label', '')}: "
                f"{_clip(example.get('subject', ''), 120)}",
                file=out,
            )


def _print_memory_decision(command: str, result: dict[str, Any], out: TextIO) -> None:
    proposal = result.get("proposal", {})
    print(f"Action: {command}", file=out)
    print(f"Memory file: {result.get('memory_path', '')}", file=out)
    print(f"Proposal: {proposal.get('proposal_id', '')}", file=out)
    print(f"Status: {proposal.get('status', '')}", file=out)
    print(f"Memory: {proposal.get('memory_type', '')}={proposal.get('value', '')}", file=out)
    print(f"Applied to policy: {bool(proposal.get('applied_to_policy', False))}", file=out)
    print("Confirmed memory:", file=out)
    _print_confirmed_memory_block(result.get("confirmed_memory", {}), out)


def _print_confirmed_memory(result: dict[str, Any], out: TextIO) -> None:
    print(f"Memory file: {result.get('memory_path', '')}", file=out)
    print(
        f"Mailbox mutation: {bool(result.get('mailbox_mutation', False))}, "
        f"policy mutation: {bool(result.get('policy_mutation', False))}",
        file=out,
    )
    print(f"Approved proposal count: {result.get('count', 0)}", file=out)
    _print_confirmed_memory_block(result.get("confirmed_memory", {}), out)


def _print_confirmed_memory_block(confirmed: dict[str, Any], out: TextIO) -> None:
    for key in ("archive_senders", "archive_domains", "archive_categories"):
        values = list(confirmed.get(key, []))
        print(f"- {key}: {len(values)}", file=out)
        for value in values[:10]:
            print(f"  - {value}", file=out)


def _print_proposal_decision(command: str, result: dict[str, Any], out: TextIO) -> None:
    proposal = result.get("proposal", {})
    print(f"Action: {command}", file=out)
    print(f"Proposal: {proposal.get('proposal_id', '')}", file=out)
    print(f"Status: {proposal.get('status', '')}", file=out)
    print(f"Email: {proposal.get('email_id', '')}", file=out)


def _print_execute_approved(result: dict[str, Any], out: TextIO) -> None:
    print(f"Provider: {result.get('provider', '')}", file=out)
    print(
        f"Selected: {result.get('selected_count', 0)}, executed: {result.get('executed_count', 0)}, "
        f"failed: {result.get('failed_count', 0)}",
        file=out,
    )
    if result.get("executed"):
        print("Executed:", file=out)
        _print_proposal_items(result.get("executed", []), out)
    if result.get("failed"):
        print("Failed:", file=out)
        _print_proposal_items(result.get("failed", []), out)


def _print_audit(result: dict[str, Any], out: TextIO) -> None:
    print(f"Count: {result.get('count', 0)}", file=out)
    for index, item in enumerate(result.get("events", []), start=1):
        print(
            f"{index}. {item.get('event_id', '')} "
            f"{item.get('event_type', '')} proposal={item.get('proposal_id', '')} "
            f"actor={item.get('actor', '')}",
            file=out,
        )


def _print_mutation_result(command: str, result: dict[str, Any], out: TextIO) -> None:
    action = result.get("action", command)
    payload = result.get("result", {})
    print(f"Action: {action}", file=out)
    if command == "draft":
        print("Draft created. It was not sent.", file=out)
        for key in ("draft_id", "source_email_id", "to", "subject", "drafts_mailbox", "sent"):
            if key in payload:
                print(f"{key}: {payload[key]}", file=out)
        return
    for key, value in payload.items():
        print(f"{key}: {value}", file=out)


def _print_approval_preview(result: dict[str, Any], out: TextIO) -> None:
    print(f"Approval required: {result.get('tool', '')}", file=out)
    print(f"Pending id: {result.get('pending_tool_call_id', '')}", file=out)
    print("No mailbox mutation executed.", file=out)
    print("This CLI preview is automatically rejected and is not persisted between runs.", file=out)
    print("Re-run the same command with --yes when you are ready to execute it.", file=out)


def _print_error(result: dict[str, Any], err: TextIO) -> None:
    print(f"Error: {result.get('error', 'tool execution failed')}", file=err)
    if result.get("trace_id"):
        print(f"Trace ID: {result['trace_id']}", file=err)


def _format_sender(item: dict[str, Any]) -> str:
    name = str(item.get("from_name", "") or "").strip()
    address = str(item.get("from_email", "") or "").strip()
    if name and address:
        return f"{name} <{address}>"
    return address or name or "(unknown)"


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _exists_text(value: Any) -> str:
    return "exists" if value else "missing"


def _mailbox_label(status: dict[str, Any], key: str) -> str:
    value = str(status.get(key, "") or "")
    display = str(status.get(f"{key}_display", "") or "")
    if display and display != value:
        return f"{display} ({value})"
    return value


def _result_renderer(printer: Callable[[dict[str, Any], TextIO], None]) -> Callable[[argparse.Namespace, dict[str, Any], TextIO], None]:
    def render(_args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
        printer(result["result"], out)

    return render


def _render_detail(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_detail(result["result"], out, show_body=args.body)


def _render_clean_preview(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_clean_preview(result["result"], out, show_protected=args.show_protected)


def _render_clean_run(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_clean_run(result["result"], out, show_protected=args.show_protected)


def _render_clean_rule_decision(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_clean_rule_decision(args.display_command, result["result"], out)


def _render_review(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    payload = result["result"]
    _print_review(payload, out)
    if payload.get("interactive_labeling"):
        run_interactive_labeling(args, payload, stdout=out)


def _render_propose(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_propose(result["result"], out, show_protected=args.show_protected)


def _render_review_proposals(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    payload = result["result"]
    _print_propose(payload, out, show_protected=args.show_protected)
    _print_proposal_label_help(payload, out)
    if payload.get("interactive_labeling"):
        run_interactive_proposal_labeling(args, payload, stdout=out)


def _render_memory_decision(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_memory_decision(args.display_command, result["result"], out)


def _render_proposal_decision(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_proposal_decision(args.display_command, result["result"], out)


def _render_mutation_result(args: argparse.Namespace, result: dict[str, Any], out: TextIO) -> None:
    _print_mutation_result(args.display_command, result["result"], out)


COMMAND_RENDERERS: dict[str, Callable[[argparse.Namespace, dict[str, Any], TextIO], None]] = {
    "status": _result_renderer(_print_status),
    "mailboxes": _result_renderer(_print_mailboxes),
    "recent": _result_renderer(_print_email_list),
    "search": _result_renderer(_print_email_list),
    "detail": _render_detail,
    "report": _result_renderer(_print_report),
    "clean-preview": _render_clean_preview,
    "clean-run": _render_clean_run,
    "teach": _result_renderer(_print_teach),
    "rules": _result_renderer(_print_clean_rules),
    "rule-approve": _render_clean_rule_decision,
    "rule-disable": _render_clean_rule_decision,
    "clean-audit": _result_renderer(_print_clean_audit),
    "daily-report": _result_renderer(_print_daily_report),
    "review": _render_review,
    "label": _result_renderer(_print_label),
    "labels": _result_renderer(_print_labels),
    "eval-real": _result_renderer(_print_eval_real),
    "eval-proposals": _result_renderer(_print_eval_proposals),
    "scan": _result_renderer(_print_scan),
    "notifications": _result_renderer(_print_notifications),
    "propose": _render_propose,
    "review-proposals": _render_review_proposals,
    "proposals": _result_renderer(_print_proposals),
    "label-proposal": _result_renderer(_print_proposal_label),
    "proposal-labels": _result_renderer(_print_proposal_labels),
    "eval-real-proposals": _result_renderer(_print_eval_real_proposals),
    "llm-archive-shadow": _result_renderer(_print_llm_archive_shadow),
    "eval-archive-shadow": _result_renderer(_print_eval_archive_shadow),
    "observed-memory": _result_renderer(_print_observed_memory),
    "memory-proposals": _result_renderer(_print_memory_proposals),
    "approve-memory": _render_memory_decision,
    "reject-memory": _render_memory_decision,
    "confirmed-memory": _result_renderer(_print_confirmed_memory),
    "approve-proposal": _render_proposal_decision,
    "reject-proposal": _render_proposal_decision,
    "execute-approved": _result_renderer(_print_execute_approved),
    "audit": _result_renderer(_print_audit),
    "mark-read": _render_mutation_result,
    "archive": _render_mutation_result,
    "star": _render_mutation_result,
    "draft": _render_mutation_result,
}
