"""Email tool registration for MailGuard email workflows."""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

from .email_classifier import classify_email
from .email_eval import evaluate_email_classifier
from .email_eval_report import write_eval_report
from .email_provider import EmailMessage, EmailProvider, MockEmailProvider
from .email_proposals import (
    action_audit_log,
    approve_action_proposal,
    execute_approved_action_proposals,
    list_action_proposals,
    reject_action_proposal,
    scan_action_proposals,
)
from .email_scheduler import (
    email_daily_digest,
    email_scheduler_state,
    list_email_notifications,
    mark_email_notification_read,
    run_email_scan,
)
from .llm_email_classifier import LLMEmailClassifier
from .memory_proposals import confirmed_memory_from_store, load_memory_proposals
from .proposal_eval import evaluate_archive_proposal_policy
from .runtime_env import SERVER_ROOT, load_server_env
from .tools import ToolContext, ToolPermission, ToolRegistry, ToolSpec, _normalize_relative_path


REPORT_OUTPUT_ROOT = "docs/test-logs"
DEFAULT_MEMORY_PROPOSAL_PATH = SERVER_ROOT / "data" / "memory_proposals.json"


def _confirmed_memory_for_scan() -> dict[str, Any]:
    load_server_env()
    raw_path = os.environ.get("MAILGUARD_MEMORY_PROPOSALS", "").strip()
    if raw_path.lower() in {"0", "false", "off", "disabled", "none"}:
        return {}
    path = Path(raw_path).expanduser() if raw_path else DEFAULT_MEMORY_PROPOSAL_PATH
    return confirmed_memory_from_store(load_memory_proposals(path))


def register_email_tools(registry: ToolRegistry, provider: EmailProvider | None = None) -> None:
    email_provider = provider or MockEmailProvider()

    def email_provider_status(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        status = getattr(email_provider, "status", None)
        if status:
            return status()
        return {
            "provider": type(email_provider).__name__,
            "status": "available",
            "mailbox_mutation": True,
        }

    def email_list_mailboxes(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        list_mailboxes = getattr(email_provider, "list_mailboxes", None)
        if not list_mailboxes:
            return {
                "provider": type(email_provider).__name__,
                "supported": False,
                "mailboxes": [],
            }
        result = list_mailboxes()
        result["supported"] = True
        return result

    def email_list_recent(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", False))
        emails = email_provider.list_recent(limit=limit, unread_only=unread_only)
        return {
            "provider": type(email_provider).__name__,
            "count": len(emails),
            "emails": [email.summary_dict() for email in emails],
        }

    def email_search(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        emails = email_provider.search(query=query, limit=limit)
        return {
            "provider": type(email_provider).__name__,
            "query": query,
            "count": len(emails),
            "emails": [email.summary_dict() for email in emails],
        }

    def email_get_detail(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        max_body_chars = _bounded_int(args.get("max_body_chars"), default=2000, minimum=200, maximum=8000)
        email = email_provider.get_detail(email_id)
        return {
            "provider": type(email_provider).__name__,
            "email": email.detail_dict(max_body_chars=max_body_chars),
            "summary": email.snippet,
            "classification": classify_email(email, context.memory_store.email_preferences(context.session_id)),
        }

    def email_classify(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        email = email_provider.get_detail(email_id)
        return {
            "provider": type(email_provider).__name__,
            "email": email.summary_dict(),
            "classification": classify_email(email, context.memory_store.email_preferences(context.session_id)),
        }

    def email_report_important(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", False))
        emails = email_provider.list_recent(limit=limit, unread_only=unread_only)
        preferences = context.memory_store.email_preferences(context.session_id)
        classified = [(email, classify_email(email, preferences)) for email in emails]
        important = [(email, decision) for email, decision in classified if decision["is_reportable"]]
        ignored = [(email, decision) for email, decision in classified if decision["is_ignored"]]
        ignored_counts = Counter(decision["category"] for _, decision in ignored)
        return {
            "provider": type(email_provider).__name__,
            "fetched": len(emails),
            "important_count": len(important),
            "ignored_count": len(ignored),
            "important": [_report_item(email, decision) for email, decision in important],
            "ignored_summary": dict(sorted(ignored_counts.items())),
            "classified": [
                {
                    "email_id": email.id,
                    "category": decision["category"],
                    "importance": decision["importance"],
                    "suggested_action": decision["suggested_action"],
                    "reasons": decision["reasons"],
                    "is_reportable": decision["is_reportable"],
                    "is_ignored": decision["is_ignored"],
                }
                for email, decision in classified
            ],
        }

    def email_list_ignored(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", False))
        emails = email_provider.list_recent(limit=limit, unread_only=unread_only)
        preferences = context.memory_store.email_preferences(context.session_id)
        ignored = [
            (email, decision)
            for email, decision in ((email, classify_email(email, preferences)) for email in emails)
            if decision["is_ignored"]
        ]
        return {
            "provider": type(email_provider).__name__,
            "fetched": len(emails),
            "ignored_count": len(ignored),
            "ignored": [_report_item(email, decision) for email, decision in ignored],
        }

    def email_archive(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        return {
            "provider": type(email_provider).__name__,
            "action": "archive",
            "result": email_provider.archive(email_id),
        }

    def email_mark_read(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        is_read = bool(args.get("is_read", True))
        return {
            "provider": type(email_provider).__name__,
            "action": "mark_read",
            "result": email_provider.mark_read(email_id, is_read=is_read),
        }

    def email_star(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        starred = bool(args.get("starred", True))
        return {
            "provider": type(email_provider).__name__,
            "action": "star",
            "result": email_provider.star(email_id, starred=starred),
        }

    def email_create_draft(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        body = _required_string(args, "body")
        to = args.get("to")
        if to is not None and not all(isinstance(item, str) and item.strip() for item in to):
            raise ValueError("to must contain non-empty email addresses")
        return {
            "provider": type(email_provider).__name__,
            "action": "create_draft",
            "result": email_provider.create_draft(email_id, body=body, to=to),
        }

    def email_get_preferences(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return {
            "preferences": context.memory_store.email_preferences(context.session_id),
        }

    def email_add_preference(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        key = _required_string(args, "key")
        value = _required_string(args, "value")
        preferences = context.memory_store.add_email_preference(context.session_id, key, value)
        return {
            "updated": True,
            "operation": "add",
            "key": key,
            "value": value.strip().lower(),
            "preferences": preferences,
        }

    def email_remove_preference(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        key = _required_string(args, "key")
        value = _required_string(args, "value")
        preferences = context.memory_store.remove_email_preference(context.session_id, key, value)
        return {
            "updated": True,
            "operation": "remove",
            "key": key,
            "value": value.strip().lower(),
            "preferences": preferences,
        }

    def email_set_preference(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        key = _required_string(args, "key")
        if "value" not in args:
            raise ValueError("value is required")
        preferences = context.memory_store.set_email_preference(context.session_id, key, args["value"])
        return {
            "updated": True,
            "operation": "set",
            "key": key,
            "preferences": preferences,
        }

    def email_scheduler_run_once(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", True))
        important_only = bool(args.get("important_only", True))
        return {
            "provider": type(email_provider).__name__,
            "scheduler": run_email_scan(
                provider=email_provider,
                memory_store=context.memory_store,
                session_id=context.session_id,
                classifier=classify_email,
                limit=limit,
                unread_only=unread_only,
                important_only=important_only,
            ),
        }

    def email_notifications(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        include_read = bool(args.get("include_read", False))
        return list_email_notifications(
            memory_store=context.memory_store,
            session_id=context.session_id,
            include_read=include_read,
            limit=limit,
        )

    def email_notification_mark_read(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        notification_id = _required_string(args, "notification_id")
        return mark_email_notification_read(
            memory_store=context.memory_store,
            session_id=context.session_id,
            notification_id=notification_id,
        )

    def email_daily_digest_tool(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=50, minimum=1, maximum=200)
        return email_daily_digest(memory_store=context.memory_store, session_id=context.session_id, limit=limit)

    def email_scheduler_state_tool(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        return email_scheduler_state(memory_store=context.memory_store, session_id=context.session_id)

    def email_scan_proposals(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", True))
        return scan_action_proposals(
            provider=email_provider,
            memory_store=context.memory_store,
            session_id=context.session_id,
            classifier=classify_email,
            limit=limit,
            unread_only=unread_only,
            confirmed_memory=_confirmed_memory_for_scan(),
        )

    def email_list_proposals(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        status = str(args.get("status", "")).strip().lower()
        limit = _bounded_int(args.get("limit"), default=100, minimum=1, maximum=500)
        return list_action_proposals(
            memory_store=context.memory_store,
            session_id=context.session_id,
            status=status,
            limit=limit,
        )

    def email_approve_proposal(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        proposal_id = _required_string(args, "proposal_id")
        return approve_action_proposal(
            memory_store=context.memory_store,
            session_id=context.session_id,
            proposal_id=proposal_id,
        )

    def email_reject_proposal(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        proposal_id = _required_string(args, "proposal_id")
        reason = str(args.get("reason", "")).strip()
        return reject_action_proposal(
            memory_store=context.memory_store,
            session_id=context.session_id,
            proposal_id=proposal_id,
            reason=reason,
        )

    def email_execute_approved_proposals(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        return execute_approved_action_proposals(
            provider=email_provider,
            memory_store=context.memory_store,
            session_id=context.session_id,
            limit=limit,
        )

    def email_audit_log(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        proposal_id = str(args.get("proposal_id", "")).strip()
        email_id = str(args.get("email_id", "")).strip()
        limit = _bounded_int(args.get("limit"), default=100, minimum=1, maximum=500)
        return action_audit_log(
            memory_store=context.memory_store,
            session_id=context.session_id,
            proposal_id=proposal_id,
            email_id=email_id,
            limit=limit,
        )

    def email_eval_mock(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=100, minimum=1, maximum=500)
        include_rows = bool(args.get("include_rows", False))
        evaluation = evaluate_email_classifier(
            provider=MockEmailProvider(),
            classifier=classify_email,
            preferences=context.memory_store.email_preferences(context.session_id),
            limit=limit,
        )
        return _compact_evaluation(evaluation, include_rows=include_rows)

    def email_eval_proposals(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=100, minimum=1, maximum=500)
        unread_only = bool(args.get("unread_only", False))
        include_rows = bool(args.get("include_rows", False))
        evaluation = evaluate_archive_proposal_policy(
            provider=MockEmailProvider(),
            classifier=classify_email,
            preferences=context.memory_store.email_preferences(context.session_id),
            limit=limit,
            unread_only=unread_only,
        )
        evaluation["classifier"] = "rule"
        evaluation["provider"] = "MockEmailProvider"
        evaluation["mailbox_mutation"] = False
        return _compact_evaluation(evaluation, include_rows=include_rows)

    def email_eval_llm_shadow(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=12, minimum=1, maximum=50)
        model = str(args.get("model", "")).strip() or None
        continue_on_error = bool(args.get("continue_on_error", False))
        include_rows = bool(args.get("include_rows", False))
        timeout = _bounded_float(args.get("timeout"), default=30.0, minimum=5.0, maximum=120.0)
        max_retries = _bounded_int(args.get("max_retries"), default=1, minimum=0, maximum=3)
        classifier = LLMEmailClassifier(model=model, timeout=timeout, max_retries=max_retries)
        evaluation = evaluate_email_classifier(
            provider=MockEmailProvider(),
            classifier=classifier.classify,
            preferences=context.memory_store.email_preferences(context.session_id),
            limit=limit,
            continue_on_error=continue_on_error,
        )
        return {
            "classifier": "llm_shadow",
            "model": classifier.model,
            "timeout": timeout,
            "max_retries": max_retries,
            "provider": "MockEmailProvider",
            "mailbox_mutation": False,
            "evaluation": _compact_evaluation(evaluation, include_rows=include_rows),
        }

    def email_eval_report(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        classifier_name = str(args.get("classifier", "rule")).strip().lower()
        if classifier_name not in {"rule", "llm"}:
            raise ValueError("classifier must be rule or llm")
        limit = _bounded_int(args.get("limit"), default=36, minimum=1, maximum=50)
        report_format = str(args.get("format", "markdown")).strip().lower()
        output_path = str(args.get("output_path", "docs/test-logs/latest-email-eval-report.md")).strip()
        if not output_path:
            raise ValueError("output_path is required")
        include_rows = bool(args.get("include_rows", False))

        if classifier_name == "llm":
            model = str(args.get("model", "")).strip() or None
            continue_on_error = bool(args.get("continue_on_error", True))
            timeout = _bounded_float(args.get("timeout"), default=60.0, minimum=5.0, maximum=120.0)
            max_retries = _bounded_int(args.get("max_retries"), default=2, minimum=0, maximum=3)
            llm_classifier = LLMEmailClassifier(model=model, timeout=timeout, max_retries=max_retries)
            evaluation = evaluate_email_classifier(
                provider=MockEmailProvider(),
                classifier=llm_classifier.classify,
                preferences=context.memory_store.email_preferences(context.session_id),
                limit=limit,
                continue_on_error=continue_on_error,
            )
            evaluation["classifier"] = "llm_shadow"
            evaluation["model"] = llm_classifier.model
            evaluation["timeout"] = timeout
            evaluation["max_retries"] = max_retries
            evaluation["provider"] = "MockEmailProvider"
            evaluation["mailbox_mutation"] = False
        else:
            evaluation = evaluate_email_classifier(
                provider=MockEmailProvider(),
                classifier=classify_email,
                preferences=context.memory_store.email_preferences(context.session_id),
                limit=limit,
            )
            evaluation["classifier"] = "rule"
            evaluation["provider"] = "MockEmailProvider"
            evaluation["mailbox_mutation"] = False

        report_output_path = _validated_report_output_path(output_path, context.workspace_root)
        report_path = write_eval_report(
            evaluation,
            output_path=report_output_path,
            report_format=report_format,
        )
        return {
            "report": {
                "path": str(report_path.relative_to(context.workspace_root)),
                "format": report_format,
            },
            "classifier": evaluation["classifier"],
            "model": evaluation.get("model", ""),
            "provider": evaluation["provider"],
            "mailbox_mutation": False,
            "evaluation": _compact_evaluation(evaluation, include_rows=include_rows),
        }

    # Provider and read-only mailbox tools.
    registry.register(
        ToolSpec(
            name="email_provider_status",
            description="Check the active email provider connection and configured mailbox state without returning message bodies.",
            input_schema=_schema({}),
            handler=email_provider_status,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_list_mailboxes",
            description="List mailboxes/folders from the active provider when supported.",
            input_schema=_schema({}),
            handler=email_list_mailboxes,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_list_recent",
            description="List recent emails from the active provider without returning full bodies.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to fetch.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only include unread emails.",
                        "default": False,
                    },
                }
            ),
            handler=email_list_recent,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_search",
            description="Search emails in the active provider without returning full bodies.",
            input_schema=_schema(
                {
                    "query": {"type": "string", "description": "Search query."},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to fetch.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                required=["query"],
            ),
            handler=email_search,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_get_detail",
            description="Fetch one email body with truncation and return its triage classification.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                    "max_body_chars": {
                        "type": "integer",
                        "description": "Maximum body characters to return.",
                        "default": 2000,
                        "minimum": 200,
                        "maximum": 8000,
                    },
                },
                required=["email_id"],
            ),
            handler=email_get_detail,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_classify",
            description="Classify one email and explain the decision.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                },
                required=["email_id"],
            ),
            handler=email_classify,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_report_important",
            description="Fetch recent emails, classify them, and return important items plus ignored counts.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to classify.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only include unread emails.",
                        "default": False,
                    },
                }
            ),
            handler=email_report_important,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_list_ignored",
            description="Fetch recent emails, classify them, and return low-priority ignored items.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of emails to classify.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only include unread emails.",
                        "default": False,
                    },
                }
            ),
            handler=email_list_ignored,
            permission=ToolPermission.READ,
        )
    )
    # Preference tools mutate only local structured preferences.
    registry.register(
        ToolSpec(
            name="email_get_preferences",
            description="Inspect structured email triage preferences for this session.",
            input_schema=_schema({}),
            handler=email_get_preferences,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_add_preference",
            description="Add one structured email triage preference, such as an important sender or ignored category.",
            input_schema=_schema(
                {
                    "key": {
                        "type": "string",
                        "description": "Preference key, for example important_senders, ignored_domains, ignored_categories.",
                    },
                    "value": {"type": "string", "description": "Preference value to add."},
                },
                required=["key", "value"],
            ),
            handler=email_add_preference,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_remove_preference",
            description="Remove one structured email triage preference.",
            input_schema=_schema(
                {
                    "key": {"type": "string", "description": "Preference key to update."},
                    "value": {"type": "string", "description": "Preference value to remove."},
                },
                required=["key", "value"],
            ),
            handler=email_remove_preference,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_set_preference",
            description="Set a structured email triage preference, such as report_schedule or timezone.",
            input_schema=_schema(
                {
                    "key": {"type": "string", "description": "Preference key to set."},
                    "value": {
                        "description": "Preference value. List keys accept a string or array; scalar keys accept a string.",
                    },
                },
                required=["key", "value"],
            ),
            handler=email_set_preference,
            permission=ToolPermission.WRITE,
        )
    )
    # Scheduler and notification tools operate on local notification state.
    registry.register(
        ToolSpec(
            name="email_scheduler_run_once",
            description="Run one headless email scheduler scan and create deduplicated notifications for important unread mail.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of recent emails to scan.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only scan unread emails.",
                        "default": True,
                    },
                    "important_only": {
                        "type": "boolean",
                        "description": "Only notify high-importance reportable emails.",
                        "default": True,
                    },
                }
            ),
            handler=email_scheduler_run_once,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_notifications",
            description="List pending email notifications created by scheduler scans.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum notifications to return.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "include_read": {
                        "type": "boolean",
                        "description": "Include notifications already marked read.",
                        "default": False,
                    },
                }
            ),
            handler=email_notifications,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_notification_mark_read",
            description="Mark a local scheduler notification as read. This does not mutate the mailbox.",
            input_schema=_schema(
                {
                    "notification_id": {"type": "string", "description": "Notification id from email_notifications."},
                },
                required=["notification_id"],
            ),
            handler=email_notification_mark_read,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_daily_digest",
            description="Build a digest from local scheduler notification history.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum historical notifications to include.",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 200,
                    },
                }
            ),
            handler=email_daily_digest_tool,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_scheduler_state",
            description="Inspect local scheduler dedupe and scan state.",
            input_schema=_schema({}),
            handler=email_scheduler_state_tool,
            permission=ToolPermission.READ,
        )
    )
    # Archive proposal tools bridge policy planning, approval state, and audit.
    registry.register(
        ToolSpec(
            name="email_scan_proposals",
            description="Scan recent mail into protected/candidate/proposal buckets and create low-risk archive action proposals with audit events.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of recent emails to scan.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only scan unread emails.",
                        "default": True,
                    },
                }
            ),
            handler=email_scan_proposals,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_list_proposals",
            description="List persisted action proposals for this session.",
            input_schema=_schema(
                {
                    "status": {
                        "type": "string",
                        "description": "Optional proposal status filter, such as proposed, approved, rejected, executed, or failed.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum proposals to return.",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                }
            ),
            handler=email_list_proposals,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_approve_proposal",
            description="Approve one action proposal for later execution.",
            input_schema=_schema(
                {
                    "proposal_id": {"type": "string", "description": "Action proposal id."},
                },
                required=["proposal_id"],
            ),
            handler=email_approve_proposal,
            permission=ToolPermission.DANGEROUS,
        )
    )
    registry.register(
        ToolSpec(
            name="email_reject_proposal",
            description="Reject one action proposal so it will not execute.",
            input_schema=_schema(
                {
                    "proposal_id": {"type": "string", "description": "Action proposal id."},
                    "reason": {"type": "string", "description": "Optional rejection reason."},
                },
                required=["proposal_id"],
            ),
            handler=email_reject_proposal,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_execute_approved_proposals",
            description="Execute approved action proposals. M1 only supports approved archive proposals.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum approved proposals to execute.",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 100,
                    },
                }
            ),
            handler=email_execute_approved_proposals,
            permission=ToolPermission.WRITE,
        )
    )
    registry.register(
        ToolSpec(
            name="email_audit_log",
            description="Read product audit events for action proposals.",
            input_schema=_schema(
                {
                    "proposal_id": {"type": "string", "description": "Optional action proposal id filter."},
                    "email_id": {"type": "string", "description": "Optional email id filter."},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum audit events to return.",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                }
            ),
            handler=email_audit_log,
            permission=ToolPermission.READ,
        )
    )
    # Evaluation tools are development/readiness helpers and never read real mail.
    registry.register(
        ToolSpec(
            name="email_eval_mock",
            description="Evaluate the deterministic email classifier against labeled mock emails.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum labeled mock emails to evaluate.",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                    "include_rows": {
                        "type": "boolean",
                        "description": "Include per-email rows. Defaults to false to keep tool results compact.",
                        "default": False,
                    },
                }
            ),
            handler=email_eval_mock,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_eval_proposals",
            description="Evaluate low-risk archive proposal policy on labeled mock emails without mailbox mutation.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum labeled mock emails to evaluate.",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only evaluate unread mock emails.",
                        "default": False,
                    },
                    "include_rows": {
                        "type": "boolean",
                        "description": "Include per-email rows. Defaults to false to keep tool results compact.",
                        "default": False,
                    },
                }
            ),
            handler=email_eval_proposals,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_eval_llm_shadow",
            description="Run LLM shadow evaluation on labeled mock emails without touching a real mailbox.",
            input_schema=_schema(
                {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum labeled mock emails to evaluate. Keep small for API smoke tests.",
                        "default": 12,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional model override. Defaults to OPENAI_MODEL.",
                    },
                    "continue_on_error": {
                        "type": "boolean",
                        "description": "Record per-email classifier errors instead of aborting immediately.",
                        "default": False,
                    },
                    "include_rows": {
                        "type": "boolean",
                        "description": "Include per-email rows. Defaults to false to keep tool results compact.",
                        "default": False,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-request LLM timeout in seconds.",
                        "default": 30.0,
                        "minimum": 5.0,
                        "maximum": 120.0,
                    },
                    "max_retries": {
                        "type": "integer",
                        "description": "Retry count for retryable LLM request failures.",
                        "default": 1,
                        "minimum": 0,
                        "maximum": 3,
                    },
                }
            ),
            handler=email_eval_llm_shadow,
            permission=ToolPermission.READ,
        )
    )
    registry.register(
        ToolSpec(
            name="email_eval_report",
            description="Write a compact evaluation report for mock email triage results.",
            input_schema=_schema(
                {
                    "classifier": {
                        "type": "string",
                        "description": "Classifier to evaluate: rule or llm.",
                        "default": "rule",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum labeled mock emails to evaluate.",
                        "default": 36,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "format": {
                        "type": "string",
                        "description": "Report format: markdown or json.",
                        "default": "markdown",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Workspace-relative path for the report file.",
                        "default": "docs/test-logs/latest-email-eval-report.md",
                    },
                    "model": {
                        "type": "string",
                        "description": "Optional LLM model override when classifier is llm.",
                    },
                    "continue_on_error": {
                        "type": "boolean",
                        "description": "Record per-email LLM classifier errors instead of aborting.",
                        "default": True,
                    },
                    "include_rows": {
                        "type": "boolean",
                        "description": "Include per-email rows in tool result. The written report always stays compact.",
                        "default": False,
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Per-request LLM timeout in seconds when classifier is llm.",
                        "default": 60.0,
                        "minimum": 5.0,
                        "maximum": 120.0,
                    },
                    "max_retries": {
                        "type": "integer",
                        "description": "Retry count for retryable LLM failures when classifier is llm.",
                        "default": 2,
                        "minimum": 0,
                        "maximum": 3,
                    },
                }
            ),
            handler=email_eval_report,
            permission=ToolPermission.WRITE,
        )
    )
    # Direct mailbox mutations are dangerous and must pass pending approval.
    registry.register(
        ToolSpec(
            name="email_archive",
            description="Archive one email. This mutates mailbox state and requires approval.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                },
                required=["email_id"],
            ),
            handler=email_archive,
            permission=ToolPermission.DANGEROUS,
        )
    )
    registry.register(
        ToolSpec(
            name="email_mark_read",
            description="Mark one email as read or unread. This mutates mailbox state and requires approval.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                    "is_read": {
                        "type": "boolean",
                        "description": "True to mark read, false to mark unread.",
                        "default": True,
                    },
                },
                required=["email_id"],
            ),
            handler=email_mark_read,
            permission=ToolPermission.DANGEROUS,
        )
    )
    registry.register(
        ToolSpec(
            name="email_star",
            description="Star or unstar one email. This mutates mailbox state and requires approval.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                    "starred": {
                        "type": "boolean",
                        "description": "True to star, false to unstar.",
                        "default": True,
                    },
                },
                required=["email_id"],
            ),
            handler=email_star,
            permission=ToolPermission.DANGEROUS,
        )
    )
    registry.register(
        ToolSpec(
            name="email_create_draft",
            description="Create a draft reply for one email without sending it. This requires approval.",
            input_schema=_schema(
                {
                    "email_id": {"type": "string", "description": "Stable email id."},
                    "body": {"type": "string", "description": "Draft body. The draft is not sent."},
                    "to": {
                        "type": "array",
                        "description": "Optional recipient override. Defaults to the original sender.",
                    },
                },
                required=["email_id", "body"],
            ),
            handler=email_create_draft,
            permission=ToolPermission.DANGEROUS,
        )
    )


def _report_item(email: EmailMessage, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "email_id": email.id,
        "from_name": email.from_name,
        "from_email": email.from_email,
        "subject": email.subject,
        "snippet": email.snippet,
        "received_at": email.received_at,
        "category": decision["category"],
        "importance": decision["importance"],
        "suggested_action": decision["suggested_action"],
        "reasons": decision["reasons"],
    }


def _compact_evaluation(evaluation: dict[str, Any], *, include_rows: bool) -> dict[str, Any]:
    if include_rows:
        return evaluation
    compact = dict(evaluation)
    rows = compact.pop("rows", [])
    compact["rows_omitted"] = len(rows)
    return compact


def _required_string(args: dict[str, Any], name: str) -> str:
    value = str(args.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        number = default
    else:
        number = int(value)
    return max(minimum, min(maximum, number))


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    if value is None:
        number = default
    else:
        number = float(value)
    return max(minimum, min(maximum, number))


def _validated_report_output_path(output_path: str, workspace_root) -> Any:
    target = _normalize_relative_path(output_path, workspace_root)
    report_root = _normalize_relative_path(REPORT_OUTPUT_ROOT, workspace_root)
    if report_root not in target.parents and target != report_root:
        raise ValueError(f"output_path must be under {REPORT_OUTPUT_ROOT}")
    if target.exists() and target.suffix.lower() not in {".md", ".json"}:
        raise ValueError("report output can only overwrite markdown or json files")
    if target.suffix.lower() not in {".md", ".json"}:
        raise ValueError("report output must end with .md or .json")
    return target


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema
