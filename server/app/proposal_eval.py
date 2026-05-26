"""Evaluation helpers for low-risk email action proposal policy."""

from __future__ import annotations

from typing import Any, Callable

from .email_proposals import ARCHIVE_CATEGORIES, ArchiveProposalPolicy
from .email_provider import EmailMessage, EmailProvider


Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]


def evaluate_archive_proposal_policy(
    *,
    provider: EmailProvider,
    classifier: Classifier,
    preferences: dict[str, Any] | None = None,
    limit: int = 100,
    unread_only: bool = False,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    preferences = preferences or {}
    policy = ArchiveProposalPolicy()
    emails = provider.list_recent(limit=limit, unread_only=unread_only)
    rows = []

    for email in emails:
        classifier_error = ""
        try:
            decision = classifier(email, preferences)
        except Exception as exc:
            if not continue_on_error:
                raise
            classifier_error = f"{type(exc).__name__}: {exc}"
            decision = {
                "category": "",
                "importance": "",
                "suggested_action": "",
                "is_reportable": False,
                "is_ignored": False,
                "reasons": [classifier_error],
                "signals": {},
            }

        policy_decision = policy.evaluate(email, decision, preferences)
        proposed_archive = policy_decision["decision"] == "propose_archive"
        expected_safe_archive = _expected_safe_archive(email, preferences)
        rows.append(
            {
                "email_id": email.id,
                "subject": email.subject,
                "from_name": email.from_name,
                "from_email": email.from_email,
                "expected_category": email.expected_category or "",
                "expected_importance": email.expected_importance or "",
                "expected_action": email.expected_action or "",
                "expected_safe_archive": expected_safe_archive,
                "preference_protected": _sender_has_important_preference(email, preferences),
                "predicted_category": decision.get("category", ""),
                "predicted_importance": decision.get("importance", ""),
                "predicted_action": decision.get("suggested_action", ""),
                "proposed_archive": proposed_archive,
                "policy_decision": policy_decision["decision"],
                "policy_reason": policy_decision["reason"],
                "classifier_error": classifier_error,
            }
        )

    proposed = [row for row in rows if row["proposed_archive"]]
    eligible = [row for row in rows if row["expected_safe_archive"]]
    true_positive = [row for row in proposed if row["expected_safe_archive"]]
    false_positive = [row for row in proposed if not row["expected_safe_archive"]]
    missed_safe_archive = [row for row in eligible if not row["proposed_archive"]]
    important_false_positive = [
        row
        for row in false_positive
        if row["expected_category"] not in ARCHIVE_CATEGORIES or row["expected_importance"] != "low"
    ]

    return {
        "sample_count": len(emails),
        "labeled_count": len([row for row in rows if row["expected_category"] and row["expected_importance"]]),
        "proposal_count": len(proposed),
        "eligible_safe_archive_count": len(eligible),
        "metrics": {
            "archive_proposal_precision": _ratio(len(true_positive), len(proposed)),
            "archive_proposal_recall": _ratio(len(true_positive), len(eligible)),
            "false_positive_count": len(false_positive),
            "missed_safe_archive_count": len(missed_safe_archive),
            "important_false_positive_count": len(important_false_positive),
        },
        "proposals": _summarize_rows(proposed),
        "false_positive_proposals": _summarize_rows(false_positive),
        "missed_safe_archive": _summarize_rows(missed_safe_archive),
        "errors": [row for row in rows if row["classifier_error"]],
        "rows": rows,
    }


def _expected_safe_archive(email: EmailMessage, preferences: dict[str, Any]) -> bool:
    if _sender_has_important_preference(email, preferences):
        return False
    return (
        (email.expected_category or "") in ARCHIVE_CATEGORIES
        and (email.expected_importance or "") == "low"
        and (email.expected_action or "") == "ignore"
        and not bool(email.expected_reportable)
    )


def _sender_has_important_preference(email: EmailMessage, preferences: dict[str, Any]) -> bool:
    from_email = email.from_email.strip().lower()
    domain = _email_domain(from_email)
    important_senders = _normalized_preferences(preferences, "important_senders")
    important_domains = _normalized_preferences(preferences, "important_domains")
    return from_email in important_senders or any(
        domain == preferred or domain.endswith(f".{preferred}")
        for preferred in important_domains
    )


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


def _summarize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "email_id": row["email_id"],
            "subject": row["subject"],
            "from_email": row["from_email"],
            "expected_category": row["expected_category"],
            "expected_importance": row["expected_importance"],
            "expected_action": row["expected_action"],
            "policy_decision": row["policy_decision"],
            "policy_reason": row["policy_reason"],
        }
        for row in rows
    ]


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
