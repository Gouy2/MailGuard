"""Email tool registration and deterministic triage policy."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .email_provider import EmailMessage, EmailProvider, MockEmailProvider
from .tools import ToolContext, ToolPermission, ToolRegistry, ToolSpec


LOW_VALUE_CATEGORIES = {"newsletter", "promotion", "noise"}
REPORTABLE_IMPORTANCE = {"high", "medium"}

SECURITY_TERMS = (
    "security",
    "sign-in",
    "signin",
    "login",
    "password",
    "account access",
    "reset your password",
)
FINANCE_TERMS = (
    "invoice",
    "billing",
    "payment",
    "payout",
    "finance",
    "stripe",
)
ACTION_TERMS = (
    "action required",
    "please",
    "could you",
    "can you",
    "review",
    "confirm",
    "verify",
    "needed",
    "required",
    "are you available",
)
DEADLINE_TERMS = (
    "today",
    "tomorrow",
    "before",
    "by ",
    "deadline",
    "4 pm",
)
MEETING_TERMS = (
    "meeting",
    "calendar",
    "schedule",
    "moved",
    "changed",
    "canceled",
    "cancelled",
    "available",
)
RECRUITING_TERMS = (
    "interview",
    "next steps",
    "next round",
    "recruit",
)
NEWSLETTER_TERMS = (
    "newsletter",
    "weekly",
    "roundup",
    "subscribed",
    "unsubscribe",
    "product updates",
)
PROMOTION_TERMS = (
    "sale",
    "coupon",
    "off",
    "deal",
    "deals",
    "promotion",
    "flash sale",
    "limited time",
)
SOCIAL_NOISE_TERMS = (
    "friend suggestions",
    "what you missed",
    "social",
    "new notifications",
)
NOTIFICATION_TERMS = (
    "ci failed",
    "workflow run",
    "failed on main",
    "notification",
)
BULK_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "newsletter",
    "updates",
    "deals",
    "notification",
)


def register_email_tools(registry: ToolRegistry, provider: EmailProvider | None = None) -> None:
    email_provider = provider or MockEmailProvider()

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
            "classification": classify_email(email),
        }

    def email_classify(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        email_id = _required_string(args, "email_id")
        email = email_provider.get_detail(email_id)
        return {
            "provider": type(email_provider).__name__,
            "email": email.summary_dict(),
            "classification": classify_email(email),
        }

    def email_report_important(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=20, minimum=1, maximum=100)
        unread_only = bool(args.get("unread_only", False))
        emails = email_provider.list_recent(limit=limit, unread_only=unread_only)
        classified = [(email, classify_email(email)) for email in emails]
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
        ignored = [
            (email, decision)
            for email, decision in ((email, classify_email(email)) for email in emails)
            if decision["is_ignored"]
        ]
        return {
            "provider": type(email_provider).__name__,
            "fetched": len(emails),
            "ignored_count": len(ignored),
            "ignored": [_report_item(email, decision) for email, decision in ignored],
        }

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


def classify_email(email: EmailMessage) -> dict[str, Any]:
    text = _email_text(email)
    labels = {label.lower() for label in email.labels}
    from_local = email.from_email.split("@", 1)[0].lower()
    reasons: list[str] = []
    positive_signals: list[str] = []
    negative_signals: list[str] = []

    if _is_direct_sender(email):
        positive_signals.append("direct sender")
    if _has_any(text, ACTION_TERMS):
        positive_signals.append("asks for action")
    if _has_any(text, DEADLINE_TERMS):
        positive_signals.append("deadline or time-sensitive wording")
    if _has_any(text, SECURITY_TERMS) or "security" in labels:
        positive_signals.append("security/account access signal")
    if _has_any(text, FINANCE_TERMS) or "finance" in labels:
        positive_signals.append("finance or billing signal")
    if _has_any(text, RECRUITING_TERMS):
        positive_signals.append("interview/recruiting context")
    if _has_any(text, MEETING_TERMS) or "calendar" in labels:
        positive_signals.append("meeting or schedule change")
    if _has_any(text, NOTIFICATION_TERMS) or "notification" in labels:
        positive_signals.append("operational notification")

    if any(part in from_local for part in BULK_LOCAL_PARTS):
        negative_signals.append("bulk or automated sender")
    if _has_any(text, NEWSLETTER_TERMS) or "newsletter" in labels:
        negative_signals.append("newsletter/unsubscribe signal")
    if _has_any(text, PROMOTION_TERMS) or "promotion" in labels:
        negative_signals.append("promotion or sale language")
    if _has_any(text, SOCIAL_NOISE_TERMS) or "social" in labels:
        negative_signals.append("social digest/noise signal")

    category = _choose_category(text, labels, positive_signals, negative_signals)
    importance = _choose_importance(category, positive_signals, negative_signals)
    suggested_action = _suggest_action(category, text)

    if positive_signals:
        reasons.extend(positive_signals)
    if category in LOW_VALUE_CATEGORIES:
        reasons.extend(negative_signals)
    if not reasons:
        reasons.append("no strong importance signal")

    return {
        "email_id": email.id,
        "category": category,
        "importance": importance,
        "suggested_action": suggested_action,
        "reasons": _unique(reasons),
        "signals": {
            "positive": _unique(positive_signals),
            "negative": _unique(negative_signals),
        },
        "is_reportable": category not in LOW_VALUE_CATEGORIES and importance in REPORTABLE_IMPORTANCE,
        "is_ignored": category in LOW_VALUE_CATEGORIES or importance == "low",
    }


def _choose_category(
    text: str,
    labels: set[str],
    positive_signals: list[str],
    negative_signals: list[str],
) -> str:
    if _has_any(text, SECURITY_TERMS) or "security" in labels:
        return "security"
    if _has_any(text, FINANCE_TERMS) or "finance" in labels:
        return "finance"
    if _has_any(text, RECRUITING_TERMS):
        return "action_required"
    if _has_any(text, MEETING_TERMS) or "calendar" in labels:
        return "meeting"
    if _has_any(text, PROMOTION_TERMS) or "promotion" in labels:
        return "promotion"
    if _has_any(text, SOCIAL_NOISE_TERMS) or "social" in labels:
        return "noise"
    if _has_any(text, NEWSLETTER_TERMS) or "newsletter" in labels:
        return "newsletter"
    if _has_any(text, NOTIFICATION_TERMS) or "notification" in labels:
        return "notification"
    if "asks for action" in positive_signals:
        return "action_required"
    if negative_signals:
        return "noise"
    return "notification"


def _choose_importance(category: str, positive_signals: list[str], negative_signals: list[str]) -> str:
    if category in LOW_VALUE_CATEGORIES:
        return "low"
    if category in {"security", "finance", "action_required"}:
        return "high"
    if "deadline or time-sensitive wording" in positive_signals:
        return "high"
    if category in {"meeting", "notification"}:
        return "medium"
    if positive_signals and not negative_signals:
        return "medium"
    return "low"


def _suggest_action(category: str, text: str) -> str:
    if category == "security":
        return "pay_attention"
    if category == "meeting":
        return "schedule"
    if category == "finance":
        return "review"
    if category in LOW_VALUE_CATEGORIES:
        return "ignore"
    if "review" in text:
        return "review"
    if "schedule" in text or "available" in text:
        return "schedule"
    if "confirm" in text or "reply" in text:
        return "reply"
    return "review"


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


def _email_text(email: EmailMessage) -> str:
    return " ".join(
        [
            email.from_name,
            email.from_email,
            email.subject,
            email.snippet,
            email.body,
            " ".join(email.labels),
        ]
    ).lower()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _is_direct_sender(email: EmailMessage) -> bool:
    local_part = email.from_email.split("@", 1)[0].lower()
    labels = {label.lower() for label in email.labels}
    if any(part in local_part for part in BULK_LOCAL_PARTS):
        return False
    if labels & {"newsletter", "promotion", "social"}:
        return False
    return bool(email.from_name and "@" in email.from_email)


def _unique(items: list[str]) -> list[str]:
    seen = set()
    unique_items = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        unique_items.append(item)
    return unique_items


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


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema
