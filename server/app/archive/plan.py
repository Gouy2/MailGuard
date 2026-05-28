"""Archive plan construction."""

from __future__ import annotations

from typing import Any, Callable

from ..email_provider import EmailMessage
from .models import (
    ArchiveCandidateItem,
    ArchiveEmailRef,
    ArchivePlan,
    ArchiveScanItem,
    EmailClassification,
    PlannedArchiveAction,
)
from .policy import ArchiveProposalPolicy


Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]


def build_archive_plan(
    *,
    emails: list[EmailMessage],
    classifier: Classifier,
    preferences: dict[str, Any] | None = None,
    policy: ArchiveProposalPolicy | None = None,
    confirmed_memory: dict[str, Any] | None = None,
    provider_name: str = "",
) -> ArchivePlan:
    """Classify and bucket emails without mutating mailbox or state."""
    active_policy = policy or ArchiveProposalPolicy()
    active_preferences = preferences or {}
    planned: list[PlannedArchiveAction] = []
    protected: list[ArchiveScanItem] = []
    candidates: list[ArchiveCandidateItem] = []
    no_action: list[ArchiveScanItem] = []

    for email in emails:
        raw_decision = classifier(email, active_preferences)
        classification = EmailClassification.from_decision(raw_decision)
        policy_decision = active_policy.evaluate_typed(
            email,
            classification,
            active_preferences,
            confirmed_memory=confirmed_memory or {},
        )
        email_ref = ArchiveEmailRef.from_message(email)

        if policy_decision.decision == "propose_archive":
            planned.append(
                PlannedArchiveAction(
                    email=email_ref,
                    classification=classification,
                    policy=policy_decision,
                )
            )
            continue

        item = ArchiveScanItem(
            email=email_ref,
            classification=classification,
            policy=policy_decision,
        )
        if policy_decision.decision == "protected":
            protected.append(item)
        elif policy_decision.decision == "candidate":
            candidates.append(
                ArchiveCandidateItem(
                    email=email_ref,
                    classification=classification,
                    policy=policy_decision,
                )
            )
        else:
            no_action.append(item)

    return ArchivePlan(
        provider=provider_name,
        fetched=len(emails),
        planned=tuple(planned),
        protected=tuple(protected),
        candidates=tuple(candidates),
        no_action=tuple(no_action),
    )
