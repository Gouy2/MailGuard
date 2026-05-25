"""Small CLI for exercising MailGuard email tools locally."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence, TextIO

if __package__ in {None, ""}:  # pragma: no cover - runtime path bootstrap for script execution
    current_file = Path(__file__).resolve()
    sys.path.insert(0, str(current_file.parent))
    sys.path.insert(0, str(current_file.parent.parent))

try:
    from app.agent import AgentRuntime
    from app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
    from app.runtime_env import SERVER_ROOT
except ModuleNotFoundError as exc:  # pragma: no cover - used when imported from repo root
    if exc.name != "app":
        raise
    from server.app.agent import AgentRuntime
    from server.app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
    from server.app.runtime_env import SERVER_ROOT


DEFAULT_SESSION_ID = "email-cli"
DEFAULT_REAL_LABEL_PATH = SERVER_ROOT / "data" / "real_email_labels.json"
RuntimeFactory = Callable[[], Any]
InputFunc = Callable[[str], str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise MailGuard email tools without pasting Python snippets.",
    )
    parser.add_argument(
        "--session-id",
        default=DEFAULT_SESSION_ID,
        help=f"Runtime session id. Defaults to {DEFAULT_SESSION_ID}.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Print the raw tool result as JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Check active email provider status.")
    status.set_defaults(func=_cmd_status, display_command="status")

    mailboxes = subparsers.add_parser(
        "mailboxes",
        aliases=["folders"],
        help="List provider mailboxes/folders.",
    )
    mailboxes.set_defaults(func=_cmd_mailboxes, display_command="mailboxes")

    recent = subparsers.add_parser("recent", help="List recent emails without bodies.")
    recent.add_argument("--limit", type=int, default=5)
    recent.add_argument("--unread", action="store_true", help="Only list unread emails.")
    recent.set_defaults(func=_cmd_recent, display_command="recent")

    detail = subparsers.add_parser("detail", help="Show one email's metadata and classification.")
    detail.add_argument("email_id")
    detail.add_argument("--body", action="store_true", help="Print a truncated body preview.")
    detail.add_argument("--max-body-chars", type=int, default=600)
    detail.set_defaults(func=_cmd_detail, display_command="detail")

    search = subparsers.add_parser("search", help="Search emails without full bodies.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    search.set_defaults(func=_cmd_search, display_command="search")

    report = subparsers.add_parser("report", help="Classify recent emails and show important items.")
    report.add_argument("--limit", type=int, default=20)
    report.add_argument("--unread", action="store_true", help="Only classify unread emails.")
    report.set_defaults(func=_cmd_report, display_command="report")

    review = subparsers.add_parser(
        "review",
        help="Show classified real mailbox samples for manual labeling. Read-only.",
    )
    review.add_argument("--limit", type=int, default=10)
    review.add_argument("--unread", action="store_true", help="Only review unread emails.")
    review.add_argument("--label", action="store_true", help="Interactively label each reviewed email.")
    review.add_argument("--labels-path", default=str(DEFAULT_REAL_LABEL_PATH))
    review.set_defaults(func=_cmd_review, display_command="review")

    label = subparsers.add_parser(
        "label",
        help="Save a local label for one real email without storing the body.",
    )
    label.add_argument("email_id")
    label.add_argument("label", choices=["important", "ignore", "later"])
    label.add_argument("--note", default="")
    label.add_argument("--labels-path", default=str(DEFAULT_REAL_LABEL_PATH))
    label.set_defaults(func=_cmd_label, display_command="label")

    labels = subparsers.add_parser("labels", help="List saved real email labels.")
    labels.add_argument("--labels-path", default=str(DEFAULT_REAL_LABEL_PATH))
    labels.set_defaults(func=_cmd_labels, display_command="labels")

    eval_real = subparsers.add_parser(
        "eval-real",
        help="Evaluate saved real email labels against current predicted decisions.",
    )
    eval_real.add_argument("--labels-path", default=str(DEFAULT_REAL_LABEL_PATH))
    eval_real.set_defaults(func=_cmd_eval_real, display_command="eval-real")

    scan = subparsers.add_parser("scan", help="Run one scheduler scan and create local notifications.")
    scan.add_argument("--limit", type=int, default=20)
    scan.add_argument("--all", action="store_true", help="Scan all recent mail instead of unread only.")
    scan.add_argument(
        "--include-medium",
        action="store_true",
        help="Create notifications for all reportable mail instead of high-importance only.",
    )
    scan.set_defaults(func=_cmd_scan, display_command="scan")

    notifications = subparsers.add_parser("notifications", help="List local scheduler notifications.")
    notifications.add_argument("--limit", type=int, default=20)
    notifications.add_argument("--include-read", action="store_true")
    notifications.set_defaults(func=_cmd_notifications, display_command="notifications")

    mark_read = subparsers.add_parser(
        "mark-read",
        aliases=["mark_read"],
        help="Mark one email read. Requires --yes to mutate the mailbox.",
    )
    mark_read.add_argument("email_id")
    mark_read.add_argument("--unread", action="store_true", help="Mark unread instead of read.")
    mark_read.add_argument("--yes", action="store_true", help="Approve and execute the mailbox mutation.")
    mark_read.set_defaults(func=_cmd_mark_read, display_command="mark-read")

    archive = subparsers.add_parser(
        "archive",
        help="Archive one email. Requires --yes to mutate the mailbox.",
    )
    archive.add_argument("email_id")
    archive.add_argument("--yes", action="store_true", help="Approve and execute the mailbox mutation.")
    archive.set_defaults(func=_cmd_archive, display_command="archive")

    star = subparsers.add_parser(
        "star",
        help="Star one email. Requires --yes to mutate the mailbox.",
    )
    star.add_argument("email_id")
    star.add_argument("--unstar", action="store_true", help="Remove the star instead.")
    star.add_argument("--yes", action="store_true", help="Approve and execute the mailbox mutation.")
    star.set_defaults(func=_cmd_star, display_command="star")

    draft = subparsers.add_parser(
        "draft",
        aliases=["create-draft", "create_draft"],
        help="Create a reply draft without sending. Requires --yes to create the draft.",
    )
    draft.add_argument("email_id")
    draft.add_argument("--body", default="", help="Draft body text.")
    draft.add_argument("--body-file", default="", help="Read draft body from a UTF-8 text file.")
    draft.add_argument("--to", action="append", default=[], help="Optional recipient override. Repeatable.")
    draft.add_argument("--yes", action="store_true", help="Approve and create the draft.")
    draft.set_defaults(func=_cmd_draft, display_command="draft")

    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    runtime_factory: RuntimeFactory = AgentRuntime.create,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
    input_func: InputFunc = input,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    setattr(args, "_input_func", input_func)
    runtime = runtime_factory()
    try:
        result = args.func(args, runtime)
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)
        else:
            _print_human(args, result, stdout=stdout, stderr=stderr)
        return 0 if _is_success(result) else 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Error: {type(exc).__name__}: {exc}", file=stderr)
        return 1
    finally:
        close = getattr(runtime, "close", None)
        if close:
            close()


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv)


def _cmd_status(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(runtime, "email_provider_status", {}, session_id=args.session_id)


def _cmd_mailboxes(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(runtime, "email_list_mailboxes", {}, session_id=args.session_id)


def _cmd_recent(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_list_recent",
        {"limit": args.limit, "unread_only": args.unread},
        session_id=args.session_id,
    )


def _cmd_detail(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_get_detail",
        {"email_id": args.email_id, "max_body_chars": args.max_body_chars},
        session_id=args.session_id,
    )


def _cmd_search(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_search",
        {"query": args.query, "limit": args.limit},
        session_id=args.session_id,
    )


def _cmd_report(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_report_important",
        {"limit": args.limit, "unread_only": args.unread},
        session_id=args.session_id,
    )


def _cmd_review(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    recent = _execute_tool(
        runtime,
        "email_list_recent",
        {"limit": args.limit, "unread_only": args.unread},
        session_id=args.session_id,
    )
    if not recent.get("ok"):
        return recent
    reviewed = []
    for item in recent["result"].get("emails", []):
        classified = _execute_tool(
            runtime,
            "email_classify",
            {"email_id": item["id"]},
            session_id=args.session_id,
        )
        if classified.get("ok"):
            reviewed.append(
                {
                    "email": classified["result"].get("email", item),
                    "classification": classified["result"].get("classification", {}),
                    "error": "",
                }
            )
        else:
            reviewed.append(
                {
                    "email": item,
                    "classification": {},
                    "error": classified.get("error", "classification failed"),
                }
            )
    result = {
        "ok": True,
        "tool": "email_review",
        "result": {
            "provider": recent["result"].get("provider", ""),
            "fetched": recent["result"].get("count", 0),
            "emails": reviewed,
            "label_help": {
                "important": "must surface to the user",
                "later": "worth reviewing, but not urgent",
                "ignore": "safe to suppress",
            },
        },
    }
    if args.label:
        result["result"]["interactive_labeling"] = True
        result["result"]["labels_path"] = str(Path(args.labels_path))
    return result


def _cmd_label(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    classified = _execute_tool(
        runtime,
        "email_classify",
        {"email_id": args.email_id},
        session_id=args.session_id,
    )
    if not classified.get("ok"):
        return classified
    email = classified["result"].get("email", {})
    decision = classified["result"].get("classification", {})
    record = save_real_label(
        args.labels_path,
        email_id=args.email_id,
        label=args.label,
        note=args.note,
        subject=email.get("subject", ""),
        from_email=email.get("from_email", ""),
        predicted_category=decision.get("category", ""),
        predicted_importance=decision.get("importance", ""),
        predicted_action=decision.get("suggested_action", ""),
        predicted_reportable=bool(decision.get("is_reportable", False)),
        predicted_ignored=bool(decision.get("is_ignored", False)),
    )
    return {
        "ok": True,
        "tool": "email_label_real",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "record": record,
        },
    }


def _cmd_labels(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    data = load_real_labels(args.labels_path)
    records = sorted(data.get("labels", {}).values(), key=lambda item: item.get("updated_at", ""), reverse=True)
    return {
        "ok": True,
        "tool": "email_labels_real",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "count": len(records),
            "labels": records,
        },
    }


def _cmd_eval_real(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    data = load_real_labels(args.labels_path)
    evaluation = evaluate_real_labels(data)
    return {
        "ok": True,
        "tool": "email_eval_real",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "evaluation": evaluation,
        },
    }


def _cmd_scan(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_scheduler_run_once",
        {
            "limit": args.limit,
            "unread_only": not args.all,
            "important_only": not args.include_medium,
        },
        session_id=args.session_id,
    )


def _cmd_notifications(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_notifications",
        {"limit": args.limit, "include_read": args.include_read},
        session_id=args.session_id,
    )


def _cmd_mark_read(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_dangerous_tool(
        runtime,
        "email_mark_read",
        {"email_id": args.email_id, "is_read": not args.unread},
        session_id=args.session_id,
        approve=args.yes,
    )


def _cmd_archive(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_dangerous_tool(
        runtime,
        "email_archive",
        {"email_id": args.email_id},
        session_id=args.session_id,
        approve=args.yes,
    )


def _cmd_star(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_dangerous_tool(
        runtime,
        "email_star",
        {"email_id": args.email_id, "starred": not args.unstar},
        session_id=args.session_id,
        approve=args.yes,
    )


def _cmd_draft(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    body = _draft_body(args)
    tool_args: dict[str, Any] = {"email_id": args.email_id, "body": body}
    if args.to:
        tool_args["to"] = args.to
    return _execute_dangerous_tool(
        runtime,
        "email_create_draft",
        tool_args,
        session_id=args.session_id,
        approve=args.yes,
    )


def _execute_tool(runtime: Any, name: str, arguments: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    execute = getattr(runtime, "execute_tool", None) or getattr(runtime, "execute_tool_for_test")
    return execute(name, arguments, session_id=session_id)


def _execute_dangerous_tool(
    runtime: Any,
    name: str,
    arguments: dict[str, Any],
    *,
    session_id: str,
    approve: bool,
) -> dict[str, Any]:
    pending = _execute_tool(runtime, name, arguments, session_id=session_id)
    if not pending.get("requires_approval"):
        return pending

    pending_id = str(pending["pending_tool_call_id"])
    if not approve:
        reject = getattr(runtime, "reject_tool", None)
        rejected = reject(pending_id) if reject else None
        return {
            "ok": True,
            "tool": name,
            "requires_approval": True,
            "approved": False,
            "mutation_executed": False,
            "pending_tool_call_id": pending_id,
            "pending": pending,
            "rejected": rejected,
        }

    approved = runtime.approve_tool(pending_id)
    approved["approved"] = True
    approved["mutation_executed"] = bool(approved.get("ok"))
    approved["pending_tool_call_id"] = pending_id
    return approved


def _draft_body(args: argparse.Namespace) -> str:
    if args.body and args.body_file:
        raise ValueError("use either --body or --body-file, not both")
    if args.body_file:
        return Path(args.body_file).expanduser().read_text(encoding="utf-8")
    if not args.body.strip():
        raise ValueError("draft body is required; pass --body or --body-file")
    return args.body


def _print_human(args: argparse.Namespace, result: dict[str, Any], *, stdout: TextIO, stderr: TextIO) -> None:
    if result.get("requires_approval") and not result.get("approved"):
        _print_approval_preview(result, stdout)
        return
    if not result.get("ok"):
        _print_error(result, stderr)
        return

    command = args.display_command
    if command == "status":
        _print_status(result["result"], stdout)
    elif command == "mailboxes":
        _print_mailboxes(result["result"], stdout)
    elif command in {"recent", "search"}:
        _print_email_list(result["result"], stdout)
    elif command == "detail":
        _print_detail(result["result"], stdout, show_body=args.body)
    elif command == "report":
        _print_report(result["result"], stdout)
    elif command == "review":
        _print_review(result["result"], stdout)
        if result["result"].get("interactive_labeling"):
            _run_interactive_labeling(args, result["result"], stdout=stdout)
    elif command == "label":
        _print_label(result["result"], stdout)
    elif command == "labels":
        _print_labels(result["result"], stdout)
    elif command == "eval-real":
        _print_eval_real(result["result"], stdout)
    elif command == "scan":
        _print_scan(result["result"], stdout)
    elif command == "notifications":
        _print_notifications(result["result"], stdout)
    elif command in {"mark-read", "archive", "star", "draft"}:
        _print_mutation_result(command, result["result"], stdout)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)


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


def _run_interactive_labeling(args: argparse.Namespace, result: dict[str, Any], *, stdout: TextIO) -> None:
    input_func = getattr(args, "_input_func", input)
    labels_path = result.get("labels_path", str(DEFAULT_REAL_LABEL_PATH))
    saved_count = 0
    skipped_count = 0
    for item in result.get("emails", []):
        if item.get("error"):
            continue
        email = item.get("email", {})
        classification = item.get("classification", {})
        email_id = str(email.get("id", "")).strip()
        if not email_id:
            continue
        while True:
            raw = input_func(f"Label {email_id} [i/l/n/s/q]: ").strip().lower()
            label = _interactive_label(raw)
            if label == "quit":
                print(f"Stopped. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)
                return
            if label == "skip":
                skipped_count += 1
                print(f"Skipped {email_id}", file=stdout)
                break
            if label:
                save_real_label(
                    labels_path,
                    email_id=email_id,
                    label=label,
                    subject=email.get("subject", ""),
                    from_email=email.get("from_email", ""),
                    predicted_category=classification.get("category", ""),
                    predicted_importance=classification.get("importance", ""),
                    predicted_action=classification.get("suggested_action", ""),
                    predicted_reportable=bool(classification.get("is_reportable", False)),
                    predicted_ignored=bool(classification.get("is_ignored", False)),
                )
                saved_count += 1
                print(f"Saved {email_id} -> {label}", file=stdout)
                break
            print("Use important/i, later/l, ignore/n, skip/s, or quit/q.", file=stdout)
    print(f"Done. Saved: {saved_count}, skipped: {skipped_count}", file=stdout)


def _interactive_label(value: str) -> str:
    mapping = {
        "important": "important",
        "i": "important",
        "later": "later",
        "l": "later",
        "ignore": "ignore",
        "n": "ignore",
        "skip": "skip",
        "s": "skip",
        "quit": "quit",
        "q": "quit",
    }
    return mapping.get(value, "")


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


def _is_success(result: dict[str, Any]) -> bool:
    if result.get("ok"):
        return True
    return bool(result.get("requires_approval") and not result.get("approved") and not result.get("error"))


if __name__ == "__main__":
    raise SystemExit(main())
