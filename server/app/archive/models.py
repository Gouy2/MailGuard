"""Typed archive planning models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..email_provider import EmailMessage


ARCHIVE_ACTION = "archive"
ARCHIVE_CATEGORIES = {"newsletter", "promotion", "noise"}
PROTECTED_CATEGORIES = {"security", "finance", "meeting", "action_required", "important"}
SCAN_PREVIEW_LIMIT = 10

ArchivePolicyBucket = Literal["propose_archive", "candidate", "protected", "no_action"]


@dataclass(frozen=True, slots=True)
class EmailClassification:
    category: str = ""
    importance: str = ""
    suggested_action: str = ""
    is_reportable: bool = False
    is_ignored: bool = False
    reasons: tuple[str, ...] = ()
    signals: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_decision(cls, decision: dict[str, Any]) -> "EmailClassification":
        return cls(
            category=str(decision.get("category", "")).strip(),
            importance=str(decision.get("importance", "")).strip(),
            suggested_action=str(decision.get("suggested_action", "")).strip(),
            is_reportable=bool(decision.get("is_reportable")),
            is_ignored=bool(decision.get("is_ignored")),
            reasons=tuple(str(item) for item in (decision.get("reasons") or [])),
            signals=dict(decision.get("signals") or {}),
        )

    @property
    def normalized_category(self) -> str:
        return self.category.lower()

    @property
    def normalized_importance(self) -> str:
        return self.importance.lower()

    @property
    def normalized_suggested_action(self) -> str:
        return self.suggested_action.lower()

    @property
    def positive_signals(self) -> list[Any]:
        return list((self.signals.get("positive") or []))

    @property
    def important_preferences(self) -> list[Any]:
        preferences = dict(self.signals.get("preferences") or {})
        return list(preferences.get("important") or [])

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "importance": self.importance,
            "suggested_action": self.suggested_action,
            "is_reportable": self.is_reportable,
            "is_ignored": self.is_ignored,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


@dataclass(frozen=True, slots=True)
class ArchivePolicyDecision:
    decision: ArchivePolicyBucket
    reason: str
    category: str = ""
    importance: str = ""
    memory_match: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "category": self.category,
            "importance": self.importance,
            "memory_match": self.memory_match,
        }


@dataclass(frozen=True, slots=True)
class ArchiveEmailRef:
    email_id: str
    thread_id: str
    from_name: str
    from_email: str
    subject: str
    snippet: str
    received_at: str
    labels: tuple[str, ...]
    is_read: bool
    has_attachments: bool

    @classmethod
    def from_message(cls, email: EmailMessage) -> "ArchiveEmailRef":
        return cls(
            email_id=email.id,
            thread_id=email.thread_id,
            from_name=email.from_name,
            from_email=email.from_email,
            subject=email.subject,
            snippet=email.snippet,
            received_at=email.received_at,
            labels=tuple(email.labels),
            is_read=email.is_read,
            has_attachments=email.has_attachments,
        )

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "id": self.email_id,
            "thread_id": self.thread_id,
            "from_name": self.from_name,
            "from_email": self.from_email,
            "subject": self.subject,
            "snippet": self.snippet,
            "received_at": self.received_at,
            "labels": list(self.labels),
            "is_read": self.is_read,
            "has_attachments": self.has_attachments,
        }


@dataclass(frozen=True, slots=True)
class PlannedArchiveAction:
    email: ArchiveEmailRef
    classification: EmailClassification
    policy: ArchivePolicyDecision
    source: str = "policy_rule"
    risk_level: str = "low"

    def to_proposal_dict(self) -> dict[str, Any]:
        reason = _joined_reason(self.policy.reason, self.classification.reasons)
        return {
            "action": ARCHIVE_ACTION,
            "email_id": self.email.email_id,
            "thread_id": self.email.thread_id,
            "subject": self.email.subject,
            "from_email": self.email.from_email,
            "from_name": self.email.from_name,
            "snippet": self.email.snippet,
            "source": self.source,
            "risk_level": self.risk_level,
            "reason": reason,
            "evidence": {
                "classification": self.classification.to_evidence_dict(),
                "email": self.email.to_summary_dict(),
                "policy": self.policy.to_dict(),
            },
        }


@dataclass(frozen=True, slots=True)
class ArchiveScanItem:
    email: ArchiveEmailRef
    classification: EmailClassification
    policy: ArchivePolicyDecision

    def to_dict(self) -> dict[str, Any]:
        return {
            "email_id": self.email.email_id,
            "from_name": self.email.from_name,
            "from_email": self.email.from_email,
            "subject": self.email.subject,
            "category": self.classification.category,
            "importance": self.classification.importance,
            "suggested_action": self.classification.suggested_action,
            "policy_decision": self.policy.decision,
            "policy_reason": self.policy.reason,
        }


@dataclass(frozen=True, slots=True)
class ArchiveCandidateItem:
    email: ArchiveEmailRef
    classification: EmailClassification
    policy: ArchivePolicyDecision
    source: str = "policy_candidate"
    risk_level: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": f"candidate-{self.email.email_id}-archive",
            "item_type": "candidate",
            "action": ARCHIVE_ACTION,
            "risk_level": self.risk_level,
            "source": self.source,
            "email_id": self.email.email_id,
            "thread_id": self.email.thread_id,
            "from_name": self.email.from_name,
            "from_email": self.email.from_email,
            "subject": self.email.subject,
            "snippet": self.email.snippet,
            "category": self.classification.category,
            "importance": self.classification.importance,
            "suggested_action": self.classification.suggested_action,
            "reason": _joined_reason(self.policy.reason, self.classification.reasons),
            "policy_decision": self.policy.decision,
        }


@dataclass(frozen=True, slots=True)
class ArchivePlan:
    provider: str
    fetched: int
    planned: tuple[PlannedArchiveAction, ...] = ()
    protected: tuple[ArchiveScanItem, ...] = ()
    candidates: tuple[ArchiveCandidateItem, ...] = ()
    no_action: tuple[ArchiveScanItem, ...] = ()

    @property
    def planned_count(self) -> int:
        return len(self.planned)

    @property
    def protected_count(self) -> int:
        return len(self.protected)

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @property
    def no_action_count(self) -> int:
        return len(self.no_action)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "fetched": self.fetched,
            "planned_count": self.planned_count,
            "planned": [item.to_proposal_dict() for item in self.planned],
            "protected_count": self.protected_count,
            "protected": [item.to_dict() for item in self.protected],
            "candidate_count": self.candidate_count,
            "candidates": [item.to_dict() for item in self.candidates],
            "no_action_count": self.no_action_count,
            "no_action": [item.to_dict() for item in self.no_action],
            "mailbox_mutation": False,
            "state_mutation": False,
        }


def _joined_reason(primary: str, reasons: tuple[str, ...]) -> str:
    return "; ".join([primary, *reasons])
