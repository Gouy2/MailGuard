"""Action proposal orchestration for email triage."""

from __future__ import annotations

from typing import Any, Callable

from .archive import (
    ARCHIVE_ACTION,
    AUDIT_EXECUTION_FAILED,
    AUDIT_EXECUTION_STARTED,
    AUDIT_EXECUTION_SUCCEEDED,
    AUDIT_PROPOSAL_APPROVED,
    AUDIT_PROPOSAL_CREATED,
    AUDIT_PROPOSAL_REJECTED,
    SCAN_PREVIEW_LIMIT,
    STATUS_APPROVED,
    STATUS_PROPOSED,
    ArchiveProposalPolicy,
    action_execution_failed_updates,
    action_execution_succeeded_updates,
    approve_action_proposal_updates,
    build_archive_plan,
    proposal_created_payload,
    proposal_decision_payload,
    proposal_execution_payload,
    reject_action_proposal_updates,
    require_action_proposal_status,
    start_action_execution_updates,
    summarize_action_proposal,
)
from .email_provider import EmailMessage, EmailProvider
from .memory import MemoryStore


Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]


def scan_action_proposals(
    *,
    provider: EmailProvider,
    memory_store: MemoryStore,
    session_id: str,
    classifier: Classifier,
    limit: int = 20,
    unread_only: bool = True,
    policy: ArchiveProposalPolicy | None = None,
    confirmed_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    preferences = memory_store.email_preferences(session_id)
    emails = provider.list_recent(limit=limit, unread_only=unread_only)
    plan = plan_archive_actions(
        emails=emails,
        classifier=classifier,
        preferences=preferences,
        policy=policy,
        confirmed_memory=confirmed_memory,
        provider_name=type(provider).__name__,
    )

    proposals = []
    created_count = 0
    duplicate_count = 0

    for planned in plan["planned"]:
        stored = memory_store.create_action_proposal_once(session_id, planned)
        stored_proposal = stored["proposal"]
        proposals.append(summarize_action_proposal(stored_proposal))
        if stored["created"]:
            created_count += 1
            memory_store.add_action_audit_event(
                session_id,
                stored_proposal["proposal_id"],
                AUDIT_PROPOSAL_CREATED,
                "policy",
                proposal_created_payload(stored_proposal),
            )
        else:
            duplicate_count += 1

    protected_items = list(plan["protected"])
    candidate_items = list(plan["candidates"])

    return {
        "provider": plan["provider"],
        "fetched": plan["fetched"],
        "proposal_count": len(proposals),
        "created_count": created_count,
        "duplicate_count": duplicate_count,
        "proposals": proposals,
        "protected_count": plan["protected_count"],
        "protected_returned_count": min(len(protected_items), SCAN_PREVIEW_LIMIT),
        "protected": protected_items[:SCAN_PREVIEW_LIMIT],
        "candidate_count": plan["candidate_count"],
        "candidate_returned_count": min(len(candidate_items), SCAN_PREVIEW_LIMIT),
        "candidates": candidate_items[:SCAN_PREVIEW_LIMIT],
        "no_action_count": plan["no_action_count"],
    }


def plan_archive_actions(
    *,
    emails: list[EmailMessage],
    classifier: Classifier,
    preferences: dict[str, Any] | None = None,
    policy: ArchiveProposalPolicy | None = None,
    confirmed_memory: dict[str, Any] | None = None,
    provider_name: str = "",
) -> dict[str, Any]:
    """Classify and bucket emails without creating proposals or audit events."""
    return build_archive_plan(
        emails=emails,
        classifier=classifier,
        preferences=preferences,
        policy=policy,
        confirmed_memory=confirmed_memory,
        provider_name=provider_name,
    ).to_dict()


def list_action_proposals(
    *,
    memory_store: MemoryStore,
    session_id: str,
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    proposals = memory_store.action_proposals(session_id, status=status, limit=limit)
    return {
        "count": len(proposals),
        "status": status,
        "proposals": [summarize_action_proposal(item) for item in proposals],
    }


def approve_action_proposal(
    *,
    memory_store: MemoryStore,
    session_id: str,
    proposal_id: str,
    actor: str = "user",
) -> dict[str, Any]:
    proposal = memory_store.get_action_proposal(session_id, proposal_id)
    require_action_proposal_status(proposal, {STATUS_PROPOSED}, "approve")
    updated = memory_store.update_action_proposal(
        session_id,
        proposal_id,
        approve_action_proposal_updates(),
    )
    event = memory_store.add_action_audit_event(
        session_id,
        proposal_id,
        AUDIT_PROPOSAL_APPROVED,
        actor,
        proposal_decision_payload(updated),
    )
    return {"proposal": updated, "audit_event": event}


def reject_action_proposal(
    *,
    memory_store: MemoryStore,
    session_id: str,
    proposal_id: str,
    actor: str = "user",
    reason: str = "",
) -> dict[str, Any]:
    proposal = memory_store.get_action_proposal(session_id, proposal_id)
    require_action_proposal_status(proposal, {STATUS_PROPOSED, STATUS_APPROVED}, "reject")
    updated = memory_store.update_action_proposal(
        session_id,
        proposal_id,
        reject_action_proposal_updates(),
    )
    event = memory_store.add_action_audit_event(
        session_id,
        proposal_id,
        AUDIT_PROPOSAL_REJECTED,
        actor,
        proposal_decision_payload(updated, reason=reason),
    )
    return {"proposal": updated, "audit_event": event}


def execute_approved_action_proposals(
    *,
    provider: EmailProvider,
    memory_store: MemoryStore,
    session_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    proposals = memory_store.action_proposals(session_id, status=STATUS_APPROVED, limit=limit)
    executed = []
    failed = []

    for proposal in proposals:
        proposal_id = proposal["proposal_id"]
        started = memory_store.update_action_proposal(
            session_id,
            proposal_id,
            start_action_execution_updates(),
        )
        memory_store.add_action_audit_event(
            session_id,
            proposal_id,
            AUDIT_EXECUTION_STARTED,
            "system",
            proposal_execution_payload(started),
        )
        try:
            if started["action"] != ARCHIVE_ACTION:
                raise ValueError(f"unsupported proposal action: {started['action']}")
            result = provider.archive(started["email_id"])
        except Exception as exc:
            updated = memory_store.update_action_proposal(
                session_id,
                proposal_id,
                action_execution_failed_updates(exc),
            )
            memory_store.add_action_audit_event(
                session_id,
                proposal_id,
                AUDIT_EXECUTION_FAILED,
                "system",
                proposal_execution_payload(started, error=updated["error"]),
            )
            failed.append(updated)
            continue

        updated = memory_store.update_action_proposal(
            session_id,
            proposal_id,
            action_execution_succeeded_updates(result),
        )
        memory_store.add_action_audit_event(
            session_id,
            proposal_id,
            AUDIT_EXECUTION_SUCCEEDED,
            "system",
            proposal_execution_payload(started, result=result),
        )
        executed.append(updated)

    return {
        "provider": type(provider).__name__,
        "selected_count": len(proposals),
        "executed_count": len(executed),
        "failed_count": len(failed),
        "executed": executed,
        "failed": failed,
    }


def action_audit_log(
    *,
    memory_store: MemoryStore,
    session_id: str,
    proposal_id: str = "",
    email_id: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    events = memory_store.action_audit_events(
        session_id,
        proposal_id=proposal_id,
        email_id=email_id,
        limit=limit,
    )
    return {
        "count": len(events),
        "proposal_id": proposal_id,
        "email_id": email_id,
        "events": events,
    }
