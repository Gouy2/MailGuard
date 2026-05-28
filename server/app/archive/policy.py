"""Precision-first archive proposal policy."""

from __future__ import annotations

from typing import Any

from ..email_provider import EmailMessage
from .models import (
    ARCHIVE_CATEGORIES,
    PROTECTED_CATEGORIES,
    ArchivePolicyDecision,
    EmailClassification,
)


class ArchiveProposalPolicy:
    """Gate low-risk archive proposals while preserving protected mail."""

    def evaluate(
        self,
        email: EmailMessage,
        decision: dict[str, Any],
        preferences: dict[str, Any],
        *,
        confirmed_memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.evaluate_typed(
            email,
            EmailClassification.from_decision(decision),
            preferences,
            confirmed_memory=confirmed_memory,
        ).to_dict()

    def evaluate_typed(
        self,
        email: EmailMessage,
        classification: EmailClassification,
        preferences: dict[str, Any],
        *,
        confirmed_memory: dict[str, Any] | None = None,
    ) -> ArchivePolicyDecision:
        archive_memory_match = _sender_archive_memory_match(email, confirmed_memory or {})

        if classification.normalized_category in PROTECTED_CATEGORIES or classification.is_reportable:
            return _policy_result(
                "protected",
                "protected category or reportable mail",
                classification=classification,
            )

        if _sender_has_important_preference(email, preferences) or classification.important_preferences:
            return _policy_result(
                "protected",
                "sender or domain is protected by important preference",
                classification=classification,
            )

        if classification.positive_signals:
            if _is_archive_candidate(classification):
                if archive_memory_match:
                    return _policy_result(
                        "propose_archive",
                        "confirmed memory promotes low-value candidate to archive proposal",
                        classification=classification,
                        memory_match=archive_memory_match,
                    )
                return _policy_result(
                    "candidate",
                    "low-value mail has positive signals, needs user feedback before proposal",
                    classification=classification,
                )
            return _policy_result(
                "protected",
                "positive importance signal blocks automatic archive proposal",
                classification=classification,
            )

        if (
            classification.normalized_category in ARCHIVE_CATEGORIES
            and classification.normalized_importance == "low"
            and classification.normalized_suggested_action == "ignore"
        ):
            return _policy_result(
                "propose_archive",
                "low-value mail classified as safe to ignore",
                classification=classification,
            )

        if classification.is_ignored:
            if archive_memory_match:
                return _policy_result(
                    "propose_archive",
                    "confirmed memory promotes ignored mail to archive proposal",
                    classification=classification,
                    memory_match=archive_memory_match,
                )
            return _policy_result(
                "candidate",
                "ignored mail did not satisfy strict archive proposal policy",
                classification=classification,
            )

        return _policy_result(
            "no_action",
            "no low-risk archive action",
            classification=classification,
        )


def _is_archive_candidate(classification: EmailClassification) -> bool:
    return (
        classification.is_ignored
        or classification.normalized_category in ARCHIVE_CATEGORIES
        or classification.normalized_importance == "low"
        or classification.normalized_suggested_action == "ignore"
    )


def _policy_result(
    decision: str,
    reason: str,
    *,
    classification: EmailClassification,
    memory_match: str = "",
) -> ArchivePolicyDecision:
    return ArchivePolicyDecision(
        decision=decision,
        reason=reason,
        category=classification.normalized_category,
        importance=classification.normalized_importance,
        memory_match=memory_match,
    )


def _sender_has_important_preference(email: EmailMessage, preferences: dict[str, Any]) -> bool:
    from_email = email.from_email.strip().lower()
    domain = _email_domain(from_email)
    return (
        from_email in _normalized_preferences(preferences, "important_senders")
        or _matching_domain(domain, _normalized_preferences(preferences, "important_domains"))
    )


def _sender_archive_memory_match(email: EmailMessage, confirmed_memory: dict[str, Any]) -> str:
    from_email = email.from_email.strip().lower()
    domain = _email_domain(from_email)
    if from_email in _normalized_preferences(confirmed_memory, "archive_senders"):
        return f"archive_sender:{from_email}"
    domains = _normalized_preferences(confirmed_memory, "archive_domains")
    matched_domain = next((preferred for preferred in domains if _matching_domain(domain, {preferred})), "")
    if matched_domain:
        return f"archive_domain:{matched_domain}"
    return ""


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


def _matching_domain(domain: str, preferred_domains: set[str]) -> bool:
    if not domain:
        return False
    return any(domain == preferred or domain.endswith(f".{preferred}") for preferred in preferred_domains)
