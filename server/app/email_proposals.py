"""Action proposal policy, execution, and audit helpers for email triage."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from .email_provider import EmailMessage, EmailProvider
from .memory import MemoryStore


Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]
ARCHIVE_ACTION = "archive"
ARCHIVE_CATEGORIES = {"newsletter", "promotion", "noise"}
PROTECTED_CATEGORIES = {"security", "finance", "meeting", "action_required", "important"}
SCAN_PREVIEW_LIMIT = 10


class ArchiveProposalPolicy:
    """Small precision-first gate for low-risk archive proposals."""

    def evaluate(
        self,
        email: EmailMessage,
        decision: dict[str, Any],
        preferences: dict[str, Any],
    ) -> dict[str, Any]:
        category = str(decision.get("category", "")).strip().lower()
        importance = str(decision.get("importance", "")).strip().lower()
        suggested_action = str(decision.get("suggested_action", "")).strip().lower()
        positive_signals = list((decision.get("signals") or {}).get("positive") or [])
        important_preferences = list(((decision.get("signals") or {}).get("preferences") or {}).get("important") or [])

        if category in PROTECTED_CATEGORIES or bool(decision.get("is_reportable")):
            return _policy_result(
                "important",
                "protected category or reportable mail",
                category=category,
                importance=importance,
            )

        if _sender_has_important_preference(email, preferences) or important_preferences:
            return _policy_result(
                "review",
                "sender or domain is protected by important preference",
                category=category,
                importance=importance,
            )

        if positive_signals:
            return _policy_result(
                "review",
                "positive importance signal blocks automatic archive proposal",
                category=category,
                importance=importance,
            )

        if category in ARCHIVE_CATEGORIES and importance == "low" and suggested_action == "ignore":
            return _policy_result(
                "propose_archive",
                "low-value mail classified as safe to ignore",
                category=category,
                importance=importance,
            )

        if bool(decision.get("is_ignored")):
            return _policy_result(
                "review",
                "ignored mail did not satisfy strict archive proposal policy",
                category=category,
                importance=importance,
            )

        return _policy_result(
            "no_action",
            "no low-risk archive action",
            category=category,
            importance=importance,
        )


def scan_action_proposals(
    *,
    provider: EmailProvider,
    memory_store: MemoryStore,
    session_id: str,
    classifier: Classifier,
    limit: int = 20,
    unread_only: bool = True,
    policy: ArchiveProposalPolicy | None = None,
) -> dict[str, Any]:
    policy = policy or ArchiveProposalPolicy()
    preferences = memory_store.email_preferences(session_id)
    emails = provider.list_recent(limit=limit, unread_only=unread_only)

    proposals = []
    created_count = 0
    duplicate_count = 0
    important_items = []
    review_items = []
    no_action_count = 0

    for email in emails:
        decision = classifier(email, preferences)
        policy_decision = policy.evaluate(email, decision, preferences)
        bucket = policy_decision["decision"]
        if bucket == "propose_archive":
            proposal = _archive_proposal(email, decision, policy_decision)
            stored = memory_store.create_action_proposal_once(session_id, proposal)
            stored_proposal = stored["proposal"]
            proposals.append(_proposal_summary(stored_proposal))
            if stored["created"]:
                created_count += 1
                memory_store.add_action_audit_event(
                    session_id,
                    stored_proposal["proposal_id"],
                    "proposal_created",
                    "policy",
                    {
                        "action": ARCHIVE_ACTION,
                        "email_id": email.id,
                        "reason": stored_proposal["reason"],
                    },
                )
            else:
                duplicate_count += 1
            continue

        item = _scan_item(email, decision, policy_decision)
        if bucket == "important":
            important_items.append(item)
        elif bucket == "review":
            review_items.append(item)
        else:
            no_action_count += 1

    return {
        "provider": type(provider).__name__,
        "fetched": len(emails),
        "proposal_count": len(proposals),
        "created_count": created_count,
        "duplicate_count": duplicate_count,
        "proposals": proposals,
        "important_count": len(important_items),
        "important_returned_count": min(len(important_items), SCAN_PREVIEW_LIMIT),
        "important": important_items[:SCAN_PREVIEW_LIMIT],
        "review_count": len(review_items),
        "review_returned_count": min(len(review_items), SCAN_PREVIEW_LIMIT),
        "review": review_items[:SCAN_PREVIEW_LIMIT],
        "no_action_count": no_action_count,
    }


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
        "proposals": [_proposal_summary(item) for item in proposals],
    }


def approve_action_proposal(
    *,
    memory_store: MemoryStore,
    session_id: str,
    proposal_id: str,
    actor: str = "user",
) -> dict[str, Any]:
    proposal = memory_store.get_action_proposal(session_id, proposal_id)
    _require_status(proposal, {"proposed"}, "approve")
    updated = memory_store.update_action_proposal(
        session_id,
        proposal_id,
        {
            "status": "approved",
            "decided_at": _now(),
            "error": "",
        },
    )
    event = memory_store.add_action_audit_event(
        session_id,
        proposal_id,
        "proposal_approved",
        actor,
        {"action": updated["action"], "email_id": updated["email_id"]},
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
    _require_status(proposal, {"proposed", "approved"}, "reject")
    updated = memory_store.update_action_proposal(
        session_id,
        proposal_id,
        {
            "status": "rejected",
            "decided_at": _now(),
            "error": "",
        },
    )
    event = memory_store.add_action_audit_event(
        session_id,
        proposal_id,
        "proposal_rejected",
        actor,
        {"action": updated["action"], "email_id": updated["email_id"], "reason": reason},
    )
    return {"proposal": updated, "audit_event": event}


def execute_approved_action_proposals(
    *,
    provider: EmailProvider,
    memory_store: MemoryStore,
    session_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    proposals = memory_store.action_proposals(session_id, status="approved", limit=limit)
    executed = []
    failed = []

    for proposal in proposals:
        proposal_id = proposal["proposal_id"]
        started = memory_store.update_action_proposal(
            session_id,
            proposal_id,
            {"status": "executing", "error": ""},
        )
        memory_store.add_action_audit_event(
            session_id,
            proposal_id,
            "execution_started",
            "system",
            {"action": started["action"], "email_id": started["email_id"]},
        )
        try:
            if started["action"] != ARCHIVE_ACTION:
                raise ValueError(f"unsupported proposal action: {started['action']}")
            result = provider.archive(started["email_id"])
        except Exception as exc:
            updated = memory_store.update_action_proposal(
                session_id,
                proposal_id,
                {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            memory_store.add_action_audit_event(
                session_id,
                proposal_id,
                "execution_failed",
                "system",
                {"action": started["action"], "email_id": started["email_id"], "error": updated["error"]},
            )
            failed.append(updated)
            continue

        updated = memory_store.update_action_proposal(
            session_id,
            proposal_id,
            {
                "status": "executed",
                "executed_at": _now(),
                "result": result,
                "error": "",
            },
        )
        memory_store.add_action_audit_event(
            session_id,
            proposal_id,
            "execution_succeeded",
            "system",
            {"action": started["action"], "email_id": started["email_id"], "result": result},
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


def _archive_proposal(
    email: EmailMessage,
    decision: dict[str, Any],
    policy_decision: dict[str, Any],
) -> dict[str, Any]:
    reasons = list(decision.get("reasons") or [])
    reason = "; ".join([policy_decision["reason"], *reasons])
    return {
        "action": ARCHIVE_ACTION,
        "email_id": email.id,
        "thread_id": email.thread_id,
        "subject": email.subject,
        "from_email": email.from_email,
        "from_name": email.from_name,
        "source": "policy_rule",
        "risk_level": "low",
        "status": "proposed",
        "reason": reason,
        "evidence": {
            "classification": {
                "category": decision.get("category", ""),
                "importance": decision.get("importance", ""),
                "suggested_action": decision.get("suggested_action", ""),
                "is_reportable": bool(decision.get("is_reportable")),
                "is_ignored": bool(decision.get("is_ignored")),
                "reasons": reasons,
                "signals": decision.get("signals", {}),
            },
            "email": email.summary_dict(),
            "policy": policy_decision,
        },
    }


def _scan_item(
    email: EmailMessage,
    decision: dict[str, Any],
    policy_decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "email_id": email.id,
        "from_name": email.from_name,
        "from_email": email.from_email,
        "subject": email.subject,
        "category": decision.get("category", ""),
        "importance": decision.get("importance", ""),
        "suggested_action": decision.get("suggested_action", ""),
        "policy_decision": policy_decision.get("decision", ""),
        "policy_reason": policy_decision.get("reason", ""),
    }


def _proposal_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": proposal.get("proposal_id", ""),
        "action": proposal.get("action", ""),
        "status": proposal.get("status", ""),
        "risk_level": proposal.get("risk_level", ""),
        "source": proposal.get("source", ""),
        "email_id": proposal.get("email_id", ""),
        "thread_id": proposal.get("thread_id", ""),
        "subject": proposal.get("subject", ""),
        "from_email": proposal.get("from_email", ""),
        "from_name": proposal.get("from_name", ""),
        "reason": proposal.get("reason", ""),
        "created_at": proposal.get("created_at", ""),
        "updated_at": proposal.get("updated_at", ""),
        "decided_at": proposal.get("decided_at", ""),
        "executed_at": proposal.get("executed_at", ""),
        "error": proposal.get("error", ""),
    }


def _policy_result(
    decision: str,
    reason: str,
    *,
    category: str,
    importance: str,
) -> dict[str, Any]:
    return {
        "decision": decision,
        "reason": reason,
        "category": category,
        "importance": importance,
    }


def _sender_has_important_preference(email: EmailMessage, preferences: dict[str, Any]) -> bool:
    from_email = email.from_email.strip().lower()
    domain = _email_domain(from_email)
    return (
        from_email in _normalized_preferences(preferences, "important_senders")
        or _matching_domain(domain, _normalized_preferences(preferences, "important_domains"))
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


def _matching_domain(domain: str, preferred_domains: set[str]) -> bool:
    if not domain:
        return False
    return any(domain == preferred or domain.endswith(f".{preferred}") for preferred in preferred_domains)


def _require_status(proposal: dict[str, Any], allowed: set[str], action: str) -> None:
    status = str(proposal.get("status", ""))
    if status not in allowed:
        raise ValueError(f"cannot {action} proposal in status {status}")


def _now() -> str:
    return datetime.now(UTC).isoformat()
