"""Audit models for inbox cleaner executions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


CleanAuditEventType = Literal[
    "clean_execution_started",
    "clean_execution_succeeded",
    "clean_execution_failed",
    "clean_execution_skipped",
]

CLEAN_EXECUTION_STARTED = "clean_execution_started"
CLEAN_EXECUTION_SUCCEEDED = "clean_execution_succeeded"
CLEAN_EXECUTION_FAILED = "clean_execution_failed"
CLEAN_EXECUTION_SKIPPED = "clean_execution_skipped"


@dataclass(frozen=True, slots=True)
class CleanAuditEvent:
    event_id: str
    run_id: str
    email_id: str
    event_type: str
    actor: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        email_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> "CleanAuditEvent":
        return cls(
            event_id=f"clean-audit-{uuid.uuid4().hex[:12]}",
            run_id=str(run_id),
            email_id=str(email_id),
            event_type=str(event_type),
            actor=str(actor),
            payload=dict(payload),
            created_at=_now(),
        )

    @classmethod
    def from_dict(cls, event: dict[str, Any]) -> "CleanAuditEvent":
        return cls(
            event_id=str(event.get("event_id", "")),
            run_id=str(event.get("run_id", "")),
            email_id=str(event.get("email_id", "")),
            event_type=str(event.get("event_type", "")),
            actor=str(event.get("actor", "")),
            payload=dict(event.get("payload") or {}),
            created_at=str(event.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "email_id": self.email_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "payload": dict(self.payload),
            "created_at": self.created_at,
        }


def new_clean_audit_event(
    *,
    run_id: str,
    email_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return CleanAuditEvent.new(
        run_id=run_id,
        email_id=email_id,
        event_type=event_type,
        actor=actor,
        payload=payload,
    ).to_dict()


def clean_execution_payload(
    item: dict[str, Any],
    *,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": item.get("action", ""),
        "email_id": item.get("email_id", ""),
        "subject": item.get("subject", ""),
        "from_email": item.get("from_email", ""),
        "automation_authority": item.get("automation_authority", ""),
        "memory_match": item.get("memory_match", ""),
        "clean_rule_match": dict(item.get("clean_rule_match") or {}),
        "category": item.get("category", ""),
        "importance": item.get("importance", ""),
        "policy_reason": item.get("policy_reason", ""),
    }
    if result is not None:
        payload["result"] = dict(result)
    if error:
        payload["error"] = error
    return payload


def _now() -> str:
    return datetime.now(UTC).isoformat()
