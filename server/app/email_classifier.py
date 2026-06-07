"""Deterministic email triage classifier."""

from __future__ import annotations

from typing import Any

from .email_provider import EmailMessage


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
    "receipt",
    "renewal",
    "subscription",
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
    "5 pm",
    "friday",
)
MEETING_TERMS = (
    "meeting",
    "calendar",
    "schedule",
    "moved",
    "changed",
    "canceled",
    "cancelled",
    "reminder:",
    "1:1",
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
    "incident triggered",
    "production alert",
    "latency alert",
    "alert recovered",
    "replica lag",
)
BULK_LOCAL_PARTS = (
    "noreply",
    "no-reply",
    "newsletter",
    "updates",
    "deals",
    "notification",
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
    if category == "notification" and "asks for action" in positive_signals and "deadline or time-sensitive wording" in positive_signals:
        category = "action_required"
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
    if _has_any(text, PROMOTION_TERMS) or "promotion" in labels:
        return "promotion"
    if _has_any(text, SOCIAL_NOISE_TERMS) or "social" in labels:
        return "noise"
    if _has_any(text, NEWSLETTER_TERMS) or "newsletter" in labels:
        return "newsletter"
    if _has_any(text, RECRUITING_TERMS):
        return "action_required"
    if _has_any(text, MEETING_TERMS) or "calendar" in labels:
        return "meeting"
    if (_has_any(text, NOTIFICATION_TERMS) or "notification" in labels) and "finance" not in labels:
        return "notification"
    if _has_any(text, FINANCE_TERMS) or "finance" in labels:
        return "finance"
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
    if category in {"meeting", "notification"}:
        return "medium"
    if "deadline or time-sensitive wording" in positive_signals:
        return "high"
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
    if category == "action_required" and "availability" in text and ("interview" in text or "next round" in text):
        return "schedule"
    if category == "action_required" and ("could you send" in text or "can you send" in text or "share " in text):
        return "reply"
    if "review" in text:
        return "review"
    if "schedule" in text or "available" in text:
        return "schedule"
    if "confirm" in text or "reply" in text:
        return "reply"
    return "review"


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
