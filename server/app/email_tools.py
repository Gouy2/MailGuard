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


def classify_email(email: EmailMessage, preferences: dict[str, Any] | None = None) -> dict[str, Any]:
    preferences = preferences or {}
    text = _email_text(email)
    labels = {label.lower() for label in email.labels}
    from_local = email.from_email.split("@", 1)[0].lower()
    reasons: list[str] = []
    positive_signals: list[str] = []
    negative_signals: list[str] = []
    preference_signals = _preference_signals(email, preferences)

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
    if category in _normalized_preferences(preferences, "ignored_categories"):
        preference_signals["ignored_category"].append(category)
        preference_signals["ignored"].append(f"ignored category preference: {category}")
    importance = _choose_importance(category, positive_signals, negative_signals)
    suggested_action = _suggest_action(category, text)

    explicit_ignored = bool(
        preference_signals["ignored_sender"] or preference_signals["ignored_domain"]
    )
    important_preference = bool(
        preference_signals["important_sender"] or preference_signals["important_domain"]
    )
    ignored_preference = explicit_ignored or bool(preference_signals["ignored_category"])

    if explicit_ignored:
        category = "noise"
        importance = "low"
        suggested_action = "ignore"
    elif important_preference:
        if category in LOW_VALUE_CATEGORIES or category == "notification":
            category = "important"
        importance = "high"
        if suggested_action == "ignore":
            suggested_action = "review"
    elif ignored_preference:
        importance = "low"
        suggested_action = "ignore"

    if positive_signals:
        reasons.extend(positive_signals)
    if category in LOW_VALUE_CATEGORIES:
        reasons.extend(negative_signals)
    reasons.extend(preference_signals["important"])
    reasons.extend(preference_signals["ignored"])
    if not reasons:
        reasons.append("no strong importance signal")

    is_ignored = category in LOW_VALUE_CATEGORIES or importance == "low"
    is_reportable = category not in LOW_VALUE_CATEGORIES and importance in REPORTABLE_IMPORTANCE
    if explicit_ignored or ignored_preference and not important_preference:
        is_ignored = True
        is_reportable = False
    if important_preference and not explicit_ignored:
        is_reportable = True
        is_ignored = False

    return {
        "email_id": email.id,
        "category": category,
        "importance": importance,
        "suggested_action": suggested_action,
        "reasons": _unique(reasons),
        "signals": {
            "positive": _unique(positive_signals),
            "negative": _unique(negative_signals),
            "preferences": {
                "important": _unique(preference_signals["important"]),
                "ignored": _unique(preference_signals["ignored"]),
            },
        },
        "is_reportable": is_reportable,
        "is_ignored": is_ignored,
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


def _preference_signals(email: EmailMessage, preferences: dict[str, Any]) -> dict[str, list[str]]:
    from_email = email.from_email.strip().lower()
    domain = _email_domain(from_email)
    signals: dict[str, list[str]] = {
        "important": [],
        "ignored": [],
        "important_sender": [],
        "important_domain": [],
        "ignored_sender": [],
        "ignored_domain": [],
        "ignored_category": [],
    }

    if from_email in _normalized_preferences(preferences, "important_senders"):
        signals["important_sender"].append(from_email)
        signals["important"].append(f"important sender preference: {from_email}")
    if from_email in _normalized_preferences(preferences, "ignored_senders"):
        signals["ignored_sender"].append(from_email)
        signals["ignored"].append(f"ignored sender preference: {from_email}")

    important_domain = _matching_domain(domain, _normalized_preferences(preferences, "important_domains"))
    if important_domain:
        signals["important_domain"].append(important_domain)
        signals["important"].append(f"important domain preference: {important_domain}")

    ignored_domain = _matching_domain(domain, _normalized_preferences(preferences, "ignored_domains"))
    if ignored_domain:
        signals["ignored_domain"].append(ignored_domain)
        signals["ignored"].append(f"ignored domain preference: {ignored_domain}")

    return signals


def _normalized_preferences(preferences: dict[str, Any], key: str) -> set[str]:
    value = preferences.get(key, [])
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return {str(item).strip().lower() for item in values if str(item).strip()}


def _email_domain(from_email: str) -> str:
    if "@" not in from_email:
        return ""
    return from_email.rsplit("@", 1)[1].strip().lower()


def _matching_domain(domain: str, preferred_domains: set[str]) -> str:
    if not domain:
        return ""
    for preferred in sorted(preferred_domains, key=len, reverse=True):
        if domain == preferred or domain.endswith(f".{preferred}"):
            return preferred
    return ""


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
