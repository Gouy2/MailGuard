"""Typed action proposal and audit helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


ActionProposalStatus = Literal["proposed", "approved", "rejected", "executing", "executed", "failed"]
ActionAuditEventType = Literal[
    "proposal_created",
    "proposal_approved",
    "proposal_rejected",
    "execution_started",
    "execution_succeeded",
    "execution_failed",
]

STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTING = "executing"
STATUS_EXECUTED = "executed"
STATUS_FAILED = "failed"

AUDIT_PROPOSAL_CREATED = "proposal_created"
AUDIT_PROPOSAL_APPROVED = "proposal_approved"
AUDIT_PROPOSAL_REJECTED = "proposal_rejected"
AUDIT_EXECUTION_STARTED = "execution_started"
AUDIT_EXECUTION_SUCCEEDED = "execution_succeeded"
AUDIT_EXECUTION_FAILED = "execution_failed"


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: str
    created_at: str
    updated_at: str
    status: str
    source: str
    risk_level: str
    action: str
    email_id: str
    thread_id: str = ""
    subject: str = ""
    snippet: str = ""
    from_email: str = ""
    from_name: str = ""
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    decided_at: str = ""
    executed_at: str = ""
    error: str = ""
    result: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, proposal: dict[str, Any]) -> "ActionProposal":
        return cls(
            proposal_id=str(proposal.get("proposal_id", "")),
            created_at=str(proposal.get("created_at", "")),
            updated_at=str(proposal.get("updated_at", "")),
            status=str(proposal.get("status", "")),
            source=str(proposal.get("source", "")),
            risk_level=str(proposal.get("risk_level", "")),
            action=str(proposal.get("action", "")),
            email_id=str(proposal.get("email_id", "")),
            thread_id=str(proposal.get("thread_id", "")),
            subject=str(proposal.get("subject", "")),
            snippet=str(proposal.get("snippet", "")),
            from_email=str(proposal.get("from_email", "")),
            from_name=str(proposal.get("from_name", "")),
            reason=str(proposal.get("reason", "")),
            evidence=dict(proposal.get("evidence") or {}),
            decided_at=str(proposal.get("decided_at", "")),
            executed_at=str(proposal.get("executed_at", "")),
            error=str(proposal.get("error", "")),
            result=dict(proposal.get("result") or {}),
        )

    @classmethod
    def normalize(cls, proposal: dict[str, Any]) -> "ActionProposal":
        now = _now()
        return cls(
            proposal_id=str(proposal.get("proposal_id") or f"proposal-{uuid.uuid4().hex[:12]}"),
            created_at=str(proposal.get("created_at") or now),
            updated_at=str(proposal.get("updated_at") or now),
            status=str(proposal.get("status", STATUS_PROPOSED)),
            source=str(proposal.get("source", "policy_rule")),
            risk_level=str(proposal.get("risk_level", "low")),
            action=str(proposal["action"]),
            email_id=str(proposal["email_id"]),
            thread_id=str(proposal.get("thread_id", "")),
            subject=str(proposal.get("subject", "")),
            snippet=str(proposal.get("snippet", "")),
            from_email=str(proposal.get("from_email", "")),
            from_name=str(proposal.get("from_name", "")),
            reason=str(proposal.get("reason", "")),
            evidence=dict(proposal.get("evidence") or {}),
            decided_at=str(proposal.get("decided_at", "")),
            executed_at=str(proposal.get("executed_at", "")),
            error=str(proposal.get("error", "")),
            result=dict(proposal.get("result") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "source": self.source,
            "risk_level": self.risk_level,
            "action": self.action,
            "email_id": self.email_id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "from_email": self.from_email,
            "from_name": self.from_name,
            "reason": self.reason,
            "evidence": dict(self.evidence),
            "decided_at": self.decided_at,
            "executed_at": self.executed_at,
            "error": self.error,
            "result": dict(self.result),
        }

    def summary_dict(self) -> dict[str, Any]:
        evidence = dict(self.evidence)
        classification = dict(evidence.get("classification") or {})
        email = dict(evidence.get("email") or {})
        policy = dict(evidence.get("policy") or {})
        return {
            "proposal_id": self.proposal_id,
            "item_type": "proposal",
            "action": self.action,
            "status": self.status,
            "risk_level": self.risk_level,
            "source": self.source,
            "email_id": self.email_id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "snippet": self.snippet or str(email.get("snippet", "")),
            "from_email": self.from_email,
            "from_name": self.from_name,
            "category": classification.get("category", ""),
            "importance": classification.get("importance", ""),
            "suggested_action": classification.get("suggested_action", ""),
            "policy_decision": policy.get("decision", ""),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ActionAuditEvent:
    event_id: str
    proposal_id: str
    event_type: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def new(
        cls,
        proposal_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> "ActionAuditEvent":
        return cls(
            event_id=f"audit-{uuid.uuid4().hex[:12]}",
            proposal_id=proposal_id,
            event_type=event_type,
            actor=actor,
            payload=dict(payload),
            created_at=_now(),
        )

    @classmethod
    def from_dict(cls, event: dict[str, Any]) -> "ActionAuditEvent":
        return cls(
            event_id=str(event.get("event_id", "")),
            proposal_id=str(event.get("proposal_id", "")),
            event_type=str(event.get("event_type", "")),
            actor=str(event.get("actor", "")),
            payload=dict(event.get("payload") or {}),
            created_at=str(event.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "proposal_id": self.proposal_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


def normalize_action_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return ActionProposal.normalize(proposal).to_dict()


def summarize_action_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return ActionProposal.from_dict(proposal).summary_dict()


def new_action_audit_event(
    proposal_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return ActionAuditEvent.new(proposal_id, event_type, actor, payload).to_dict()


def require_action_proposal_status(proposal: dict[str, Any], allowed: set[str], action: str) -> None:
    status = str(proposal.get("status", ""))
    if status not in allowed:
        raise ValueError(f"cannot {action} proposal in status {status}")


def proposal_created_payload(proposal: dict[str, Any]) -> dict[str, Any]:
    item = ActionProposal.from_dict(proposal)
    return {"action": item.action, "email_id": item.email_id, "reason": item.reason}


def proposal_decision_payload(proposal: dict[str, Any], *, reason: str = "") -> dict[str, Any]:
    item = ActionProposal.from_dict(proposal)
    payload = {"action": item.action, "email_id": item.email_id}
    if reason:
        payload["reason"] = reason
    return payload


def proposal_execution_payload(
    proposal: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    item = ActionProposal.from_dict(proposal)
    payload: dict[str, Any] = {"action": item.action, "email_id": item.email_id}
    if result is not None:
        payload["result"] = dict(result)
    if error:
        payload["error"] = error
    return payload


def approve_action_proposal_updates() -> dict[str, Any]:
    return {"status": STATUS_APPROVED, "decided_at": _now(), "error": ""}


def reject_action_proposal_updates() -> dict[str, Any]:
    return {"status": STATUS_REJECTED, "decided_at": _now(), "error": ""}


def start_action_execution_updates() -> dict[str, Any]:
    return {"status": STATUS_EXECUTING, "error": ""}


def action_execution_succeeded_updates(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": STATUS_EXECUTED,
        "executed_at": _now(),
        "result": dict(result),
        "error": "",
    }


def action_execution_failed_updates(exc: Exception) -> dict[str, Any]:
    return {"status": STATUS_FAILED, "error": f"{type(exc).__name__}: {exc}"}


def _now() -> str:
    return datetime.now(UTC).isoformat()
