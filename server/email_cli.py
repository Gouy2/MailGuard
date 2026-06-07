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
    from app.archive_shadow import (
        DEFAULT_ARCHIVE_SHADOW_MAX_AVG_LATENCY_MS,
        DEFAULT_ARCHIVE_SHADOW_MAX_FALSE_POSITIVES,
        DEFAULT_ARCHIVE_SHADOW_MIN_DECISIVE_LABELS,
        DEFAULT_ARCHIVE_SHADOW_TARGET_PRECISION,
        evaluate_archive_shadow_results,
        load_archive_shadow_results,
    )
    from app.archive_shadow_workflow import (
        proposal_item_id as _proposal_item_id,
        run_archive_shadow_workflow,
    )
    from app.cli.render import is_success, print_human
    from app.daily_report.runner import run_daily_report
    from app.daily_report.storage import DEFAULT_DAILY_REPORT_DIR
    from app.memory_proposals import (
        approve_memory_proposal,
        reject_memory_proposal,
    )
    from app.memory_workflow import (
        run_confirmed_memory_workflow,
        run_memory_proposals_workflow,
        run_observed_memory_workflow,
    )
    from app.real_proposal_eval import (
        evaluate_real_proposal_labels,
        load_real_proposal_labels,
        save_real_proposal_label,
    )
    from app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
    from app.runtime_env import SERVER_ROOT
except ModuleNotFoundError as exc:  # pragma: no cover - used when imported from repo root
    if exc.name != "app":
        raise
    from server.app.agent import AgentRuntime
    from server.app.archive_shadow import (
        DEFAULT_ARCHIVE_SHADOW_MAX_AVG_LATENCY_MS,
        DEFAULT_ARCHIVE_SHADOW_MAX_FALSE_POSITIVES,
        DEFAULT_ARCHIVE_SHADOW_MIN_DECISIVE_LABELS,
        DEFAULT_ARCHIVE_SHADOW_TARGET_PRECISION,
        evaluate_archive_shadow_results,
        load_archive_shadow_results,
    )
    from server.app.archive_shadow_workflow import (
        proposal_item_id as _proposal_item_id,
        run_archive_shadow_workflow,
    )
    from server.app.cli.render import is_success, print_human
    from server.app.daily_report.runner import run_daily_report
    from server.app.daily_report.storage import DEFAULT_DAILY_REPORT_DIR
    from server.app.memory_proposals import (
        approve_memory_proposal,
        reject_memory_proposal,
    )
    from server.app.memory_workflow import (
        run_confirmed_memory_workflow,
        run_memory_proposals_workflow,
        run_observed_memory_workflow,
    )
    from server.app.real_proposal_eval import (
        evaluate_real_proposal_labels,
        load_real_proposal_labels,
        save_real_proposal_label,
    )
    from server.app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
    from server.app.runtime_env import SERVER_ROOT


DEFAULT_SESSION_ID = "email-cli"
DEFAULT_REAL_LABEL_PATH = SERVER_ROOT / "data" / "real_email_labels.json"
DEFAULT_REAL_PROPOSAL_LABEL_PATH = SERVER_ROOT / "data" / "real_proposal_labels.json"
DEFAULT_MEMORY_PROPOSAL_PATH = SERVER_ROOT / "data" / "memory_proposals.json"
DEFAULT_ARCHIVE_SHADOW_PATH = SERVER_ROOT / "data" / "archive_shadow_results.json"
RuntimeFactory = Callable[[], Any]
InputFunc = Callable[[str], str]
CLI_PRESETS: dict[str, tuple[str, ...]] = {
    "daily": ("daily-report",),
    "archive-review": ("review-proposals", "--limit", "20", "--unread", "--label"),
    "protected": ("review-proposals", "--limit", "20", "--unread", "--show-protected"),
    "archive-labels": ("proposal-labels",),
    "archive-eval": ("eval-real-proposals",),
    "memory": ("memory-proposals", "--min-samples", "1"),
    "memory-list": ("confirmed-memory",),
    "shadow": ("llm-archive-shadow", "--limit", "20", "--continue-on-error"),
    "shadow-eval": ("eval-archive-shadow",),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise MailGuard email tools without pasting Python snippets.",
        epilog=_preset_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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

    daily_report = subparsers.add_parser(
        "daily-report",
        help="Run a manual read-only daily report agent loop and write a report artifact.",
    )
    daily_report.add_argument("--llm", choices=["mock", "openai"], default="mock")
    daily_report.add_argument("--limit", type=int, default=20)
    daily_report.add_argument("--hours", type=int, default=24)
    daily_report.add_argument("--max-steps", type=int, default=8)
    daily_report.add_argument("--timeout", type=float, default=120.0)
    daily_report.add_argument("--model", default="", help="Optional OpenAI model override.")
    daily_report.add_argument("--out-dir", default=str(DEFAULT_DAILY_REPORT_DIR))
    daily_report.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    daily_report.set_defaults(func=_cmd_daily_report, display_command="daily-report")

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

    eval_proposals = subparsers.add_parser(
        "eval-proposals",
        help="Evaluate archive proposal policy on labeled mock emails. Read-only.",
    )
    eval_proposals.add_argument("--limit", type=int, default=36)
    eval_proposals.add_argument("--unread", action="store_true", help="Only evaluate unread mock emails.")
    eval_proposals.add_argument("--include-rows", action="store_true", help="Include per-email rows in JSON output.")
    eval_proposals.set_defaults(func=_cmd_eval_proposals, display_command="eval-proposals")

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

    propose = subparsers.add_parser("propose", help="Scan mail and create low-risk archive action proposals.")
    propose.add_argument("--limit", type=int, default=20)
    propose.add_argument("--unread", action="store_true", help="Only scan unread emails.")
    propose.add_argument("--all", action="store_true", help="Scan all recent mail instead of unread only.")
    propose.add_argument("--show-protected", action="store_true", help="Print protected items for policy review.")
    propose.set_defaults(func=_cmd_propose, display_command="propose")

    review_proposals = subparsers.add_parser(
        "review-proposals",
        help="Scan action proposals/candidates and optionally label whether each archive item is acceptable. Read-only for mailbox.",
    )
    review_proposals.add_argument("--limit", type=int, default=20)
    review_proposals.add_argument("--unread", action="store_true", help="Only scan unread emails.")
    review_proposals.add_argument("--all", action="store_true", help="Scan all recent mail instead of unread only.")
    review_proposals.add_argument("--show-protected", action="store_true", help="Print protected items for policy review.")
    review_proposals.add_argument("--label", action="store_true", help="Interactively label returned proposals.")
    review_proposals.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    review_proposals.set_defaults(func=_cmd_review_proposals, display_command="review-proposals")

    proposals = subparsers.add_parser("proposals", help="List action proposals.")
    proposals.add_argument("--status", default="", help="Optional status filter.")
    proposals.add_argument("--limit", type=int, default=100)
    proposals.set_defaults(func=_cmd_proposals, display_command="proposals")

    label_proposal = subparsers.add_parser("label-proposal", help="Save a local label for one action proposal.")
    label_proposal.add_argument("proposal_id")
    label_proposal.add_argument("label", choices=["archive", "keep", "unsure"])
    label_proposal.add_argument("--note", default="")
    label_proposal.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    label_proposal.set_defaults(func=_cmd_label_proposal, display_command="label-proposal")

    proposal_labels = subparsers.add_parser("proposal-labels", help="List saved action proposal labels.")
    proposal_labels.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    proposal_labels.set_defaults(func=_cmd_proposal_labels, display_command="proposal-labels")

    eval_real_proposals = subparsers.add_parser(
        "eval-real-proposals",
        help="Evaluate saved real action proposal labels.",
    )
    eval_real_proposals.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    eval_real_proposals.set_defaults(func=_cmd_eval_real_proposals, display_command="eval-real-proposals")

    llm_archive_shadow = subparsers.add_parser(
        "llm-archive-shadow",
        help="Run LLM archive suitability shadow scoring for saved proposal/candidate labels.",
    )
    llm_archive_shadow.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    llm_archive_shadow.add_argument("--limit", type=int, default=20)
    llm_archive_shadow.add_argument("--shadow-path", default=str(DEFAULT_ARCHIVE_SHADOW_PATH))
    llm_archive_shadow.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    llm_archive_shadow.add_argument("--model", default="", help="Optional model override.")
    llm_archive_shadow.add_argument("--timeout", type=float, default=30.0)
    llm_archive_shadow.add_argument("--max-retries", type=int, default=1)
    llm_archive_shadow.add_argument("--force", action="store_true", help="Re-score items even when cached results exist.")
    llm_archive_shadow.add_argument(
        "--fetch-missing-snippet",
        action="store_true",
        help="Fetch email detail only for old labels that do not have a saved snippet.",
    )
    llm_archive_shadow.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and print input diagnostics without calling the LLM or writing shadow results.",
    )
    llm_archive_shadow.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Save per-item errors instead of aborting on the first LLM/detail failure.",
    )
    llm_archive_shadow.set_defaults(func=_cmd_llm_archive_shadow, display_command="llm-archive-shadow")

    eval_archive_shadow = subparsers.add_parser(
        "eval-archive-shadow",
        help="Evaluate saved LLM archive shadow results against local proposal/candidate labels.",
    )
    eval_archive_shadow.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    eval_archive_shadow.add_argument("--shadow-path", default=str(DEFAULT_ARCHIVE_SHADOW_PATH))
    eval_archive_shadow.add_argument(
        "--min-decisive-labels",
        type=int,
        default=DEFAULT_ARCHIVE_SHADOW_MIN_DECISIVE_LABELS,
        help="Minimum decisive archive/keep labels before shadow can be considered policy-ready.",
    )
    eval_archive_shadow.add_argument(
        "--target-precision",
        type=float,
        default=DEFAULT_ARCHIVE_SHADOW_TARGET_PRECISION,
        help="Target precision for archive=yes shadow predictions.",
    )
    eval_archive_shadow.add_argument(
        "--max-false-positives",
        type=int,
        default=DEFAULT_ARCHIVE_SHADOW_MAX_FALSE_POSITIVES,
        help="Maximum accepted keep->yes false positives.",
    )
    eval_archive_shadow.add_argument(
        "--max-avg-latency-ms",
        type=int,
        default=DEFAULT_ARCHIVE_SHADOW_MAX_AVG_LATENCY_MS,
        help="Maximum average shadow scoring latency for policy readiness.",
    )
    eval_archive_shadow.set_defaults(func=_cmd_eval_archive_shadow, display_command="eval-archive-shadow")

    observed_memory = subparsers.add_parser(
        "observed-memory",
        aliases=["memory-insights"],
        help="Summarize observed memory signals from local proposal/candidate labels. Read-only.",
    )
    observed_memory.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    observed_memory.add_argument("--min-samples", type=int, default=1)
    observed_memory.add_argument("--limit", type=int, default=20)
    observed_memory.set_defaults(func=_cmd_observed_memory, display_command="observed-memory")

    memory_proposals = subparsers.add_parser(
        "memory-proposals",
        help="Generate and list confirmable memory proposals from observed labels.",
    )
    memory_proposals.add_argument("--labels-path", default=str(DEFAULT_REAL_PROPOSAL_LABEL_PATH))
    memory_proposals.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    memory_proposals.add_argument("--min-samples", type=int, default=1)
    memory_proposals.add_argument("--limit", type=int, default=20)
    memory_proposals.add_argument("--status", choices=["", "proposed", "approved", "rejected"], default="")
    memory_proposals.set_defaults(func=_cmd_memory_proposals, display_command="memory-proposals")

    approve_memory = subparsers.add_parser(
        "approve-memory",
        help="Approve one local memory proposal. Archive sender/domain memory can affect future proposal scans.",
    )
    approve_memory.add_argument("proposal_id")
    approve_memory.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    approve_memory.set_defaults(func=_cmd_approve_memory, display_command="approve-memory")

    reject_memory = subparsers.add_parser(
        "reject-memory",
        help="Reject one local memory proposal.",
    )
    reject_memory.add_argument("proposal_id")
    reject_memory.add_argument("--reason", default="")
    reject_memory.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    reject_memory.set_defaults(func=_cmd_reject_memory, display_command="reject-memory")

    confirmed_memory = subparsers.add_parser(
        "confirmed-memory",
        help="List locally confirmed memory entries and policy applicability.",
    )
    confirmed_memory.add_argument("--memory-path", default=str(DEFAULT_MEMORY_PROPOSAL_PATH))
    confirmed_memory.set_defaults(func=_cmd_confirmed_memory, display_command="confirmed-memory")

    approve_proposal = subparsers.add_parser("approve-proposal", help="Approve one action proposal.")
    approve_proposal.add_argument("proposal_id")
    approve_proposal.set_defaults(func=_cmd_approve_proposal, display_command="approve-proposal")

    reject_proposal = subparsers.add_parser("reject-proposal", help="Reject one action proposal.")
    reject_proposal.add_argument("proposal_id")
    reject_proposal.add_argument("--reason", default="")
    reject_proposal.set_defaults(func=_cmd_reject_proposal, display_command="reject-proposal")

    execute_approved = subparsers.add_parser("execute-approved", help="Execute approved action proposals.")
    execute_approved.add_argument("--limit", type=int, default=20)
    execute_approved.set_defaults(func=_cmd_execute_approved, display_command="execute-approved")

    audit = subparsers.add_parser("audit", help="List action proposal audit events.")
    audit.add_argument("--proposal-id", default="")
    audit.add_argument("--email-id", default="")
    audit.add_argument("--limit", type=int, default=100)
    audit.set_defaults(func=_cmd_audit, display_command="audit")

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
    args = parser.parse_args(_expand_preset_args(sys.argv[1:] if argv is None else argv))
    setattr(args, "_input_func", input_func)
    setattr(args, "_progress_out", None if args.json_output else stderr)
    runtime = runtime_factory()
    try:
        result = args.func(args, runtime)
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)
        else:
            print_human(args, result, stdout=stdout, stderr=stderr)
        return 0 if is_success(result) else 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Error: {type(exc).__name__}: {exc}", file=stderr)
        return 1
    finally:
        close = getattr(runtime, "close", None)
        if close:
            close()


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv)


def _expand_preset_args(argv: Sequence[str]) -> list[str]:
    args = list(argv)
    command_index = _command_index(args)
    if command_index is None:
        return args
    preset = CLI_PRESETS.get(args[command_index])
    if preset is None:
        return args
    return [*args[:command_index], *preset, *args[command_index + 1 :]]


def _command_index(args: list[str]) -> int | None:
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--json":
            index += 1
            continue
        if item == "--session-id":
            index += 2
            continue
        if item.startswith("--session-id="):
            index += 1
            continue
        if item.startswith("-"):
            return index
        return index
    return None


def _preset_help() -> str:
    lines = [
        "Workflow presets:",
        "  daily            -> daily-report",
        "  archive-review   -> review-proposals --limit 20 --unread --label",
        "  protected        -> review-proposals --limit 20 --unread --show-protected",
        "  archive-labels   -> proposal-labels",
        "  archive-eval     -> eval-real-proposals",
        "  memory           -> memory-proposals --min-samples 1",
        "  memory-list      -> confirmed-memory",
        "  shadow           -> llm-archive-shadow --limit 20 --continue-on-error",
        "  shadow-eval      -> eval-archive-shadow",
        "",
        "Preset defaults can be overridden, for example: archive-review --limit 50 --all",
    ]
    return "\n".join(lines)


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


def _cmd_daily_report(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    report = run_daily_report(
        llm=args.llm,
        model=args.model,
        limit=args.limit,
        hours=args.hours,
        max_steps=args.max_steps,
        timeout_sec=args.timeout,
        memory_path=args.memory_path,
        output_dir=args.out_dir,
    )
    return {
        "ok": report.get("status") == "ok",
        "tool": "daily_report",
        "error": report.get("error", ""),
        "result": report,
    }


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


def _cmd_eval_proposals(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_eval_proposals",
        {"limit": args.limit, "unread_only": args.unread, "include_rows": args.include_rows},
        session_id=args.session_id,
    )


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


def _cmd_propose(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    unread_only = True
    if args.all:
        unread_only = False
    elif args.unread:
        unread_only = True
    return _execute_tool(
        runtime,
        "email_scan_proposals",
        {"limit": args.limit, "unread_only": unread_only},
        session_id=args.session_id,
    )


def _cmd_review_proposals(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    result = _cmd_propose(args, runtime)
    if result.get("ok"):
        result["tool"] = "email_review_proposals"
        result["result"]["label_help"] = {
            "archive": "proposal is acceptable to archive",
            "keep": "proposal should not be archived",
            "unsure": "cannot decide from metadata/snippet",
        }
        if args.label:
            result["result"]["interactive_labeling"] = True
            result["result"]["labels_path"] = str(Path(args.labels_path))
    return result


def _cmd_proposals(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_list_proposals",
        {"status": args.status, "limit": args.limit},
        session_id=args.session_id,
    )


def _cmd_label_proposal(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    listed = _execute_tool(
        runtime,
        "email_list_proposals",
        {"status": "", "limit": 500},
        session_id=args.session_id,
    )
    if not listed.get("ok"):
        return listed
    proposal = _find_proposal(listed["result"].get("proposals", []), args.proposal_id)
    if proposal is None:
        return {
            "ok": False,
            "tool": "email_label_proposal",
            "error": f"proposal not found: {args.proposal_id}",
        }
    record = save_real_proposal_label(
        args.labels_path,
        proposal=proposal,
        label=args.label,
        note=args.note,
    )
    return {
        "ok": True,
        "tool": "email_label_proposal",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "record": record,
        },
    }


def _cmd_proposal_labels(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    data = load_real_proposal_labels(args.labels_path)
    records = sorted(data.get("labels", {}).values(), key=lambda item: item.get("labeled_at", ""), reverse=True)
    return {
        "ok": True,
        "tool": "email_proposal_labels_real",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "count": len(records),
            "labels": records,
        },
    }


def _cmd_eval_real_proposals(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    data = load_real_proposal_labels(args.labels_path)
    evaluation = evaluate_real_proposal_labels(data)
    return {
        "ok": True,
        "tool": "email_eval_real_proposals",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "evaluation": evaluation,
        },
    }


def _cmd_llm_archive_shadow(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    def fetch_email(item: dict[str, Any]) -> dict[str, Any]:
        detail = _execute_tool(
            runtime,
            "email_get_detail",
            {"email_id": item["email_id"], "max_body_chars": 200},
            session_id=args.session_id,
        )
        if not detail.get("ok"):
            raise RuntimeError(str(detail.get("error", "email_get_detail failed")))
        return detail["result"].get("email", {})

    def progress(message: str) -> None:
        out = getattr(args, "_progress_out", None)
        if out is not None:
            print(message, file=out, flush=True)

    return {
        "ok": True,
        "tool": "email_llm_archive_shadow",
        "result": run_archive_shadow_workflow(
            labels_path=args.labels_path,
            shadow_path=args.shadow_path,
            memory_path=args.memory_path,
            limit=args.limit,
            model=args.model,
            timeout=args.timeout,
            max_retries=args.max_retries,
            force=args.force,
            dry_run=args.dry_run,
            continue_on_error=args.continue_on_error,
            fetch_missing_snippet=args.fetch_missing_snippet,
            fetch_email=fetch_email,
            progress=progress,
        ),
    }


def _cmd_eval_archive_shadow(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    label_data = load_real_proposal_labels(args.labels_path)
    shadow_data = load_archive_shadow_results(args.shadow_path)
    evaluation = evaluate_archive_shadow_results(
        label_data=label_data,
        shadow_data=shadow_data,
        min_decisive_labels=args.min_decisive_labels,
        target_precision=args.target_precision,
        max_false_positives=args.max_false_positives,
        max_avg_latency_ms=args.max_avg_latency_ms,
    )
    return {
        "ok": True,
        "tool": "email_eval_archive_shadow",
        "result": {
            "labels_path": str(Path(args.labels_path)),
            "shadow_path": str(Path(args.shadow_path)),
            "evaluation": evaluation,
        },
    }


def _cmd_observed_memory(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "email_observed_memory",
        "result": run_observed_memory_workflow(
            labels_path=args.labels_path,
            min_samples=args.min_samples,
            limit=args.limit,
        ),
    }


def _cmd_memory_proposals(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "email_memory_proposals",
        "result": run_memory_proposals_workflow(
            labels_path=args.labels_path,
            memory_path=args.memory_path,
            min_samples=args.min_samples,
            limit=args.limit,
            status=args.status,
        ),
    }


def _cmd_approve_memory(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    decision = approve_memory_proposal(args.memory_path, args.proposal_id)
    return {
        "ok": True,
        "tool": "email_approve_memory",
        "result": {
            "memory_path": str(Path(args.memory_path)),
            "proposal": decision["proposal"],
            "confirmed_memory": decision["confirmed_memory"],
            "mailbox_mutation": False,
            "policy_mutation": False,
        },
    }


def _cmd_reject_memory(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    decision = reject_memory_proposal(args.memory_path, args.proposal_id, reason=args.reason)
    return {
        "ok": True,
        "tool": "email_reject_memory",
        "result": {
            "memory_path": str(Path(args.memory_path)),
            "proposal": decision["proposal"],
            "confirmed_memory": decision["confirmed_memory"],
            "mailbox_mutation": False,
            "policy_mutation": False,
        },
    }


def _cmd_confirmed_memory(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "tool": "email_confirmed_memory",
        "result": run_confirmed_memory_workflow(memory_path=args.memory_path),
    }


def _cmd_approve_proposal(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_dangerous_tool(
        runtime,
        "email_approve_proposal",
        {"proposal_id": args.proposal_id},
        session_id=args.session_id,
        approve=True,
    )


def _cmd_reject_proposal(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_reject_proposal",
        {"proposal_id": args.proposal_id, "reason": args.reason},
        session_id=args.session_id,
    )


def _cmd_execute_approved(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_execute_approved_proposals",
        {"limit": args.limit},
        session_id=args.session_id,
    )


def _cmd_audit(args: argparse.Namespace, runtime: Any) -> dict[str, Any]:
    return _execute_tool(
        runtime,
        "email_audit_log",
        {"proposal_id": args.proposal_id, "email_id": args.email_id, "limit": args.limit},
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


def _find_proposal(proposals: list[dict[str, Any]], proposal_id: str) -> dict[str, Any] | None:
    proposal_id = proposal_id.strip()
    for proposal in proposals:
        if _proposal_item_id(proposal) == proposal_id:
            return proposal
    return None


if __name__ == "__main__":
    raise SystemExit(main())
