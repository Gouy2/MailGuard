"""Typed models for daily read-only report runs."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


READ_ACTIONS = {"list_recent", "search", "get_detail", "memory"}
FINISH_ACTION = "finish"
VALID_ACTIONS = READ_ACTIONS | {FINISH_ACTION}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"daily-{timestamp}-{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class Budget:
    limit: int = 20
    hours: int = 24
    max_steps: int = 8
    timeout_sec: float = 120.0
    max_detail_chars: int = 600

    def __post_init__(self) -> None:
        self.limit = _bounded_int(self.limit, default=20, minimum=1, maximum=100)
        self.hours = _bounded_int(self.hours, default=24, minimum=1, maximum=24 * 14)
        self.max_steps = _bounded_int(self.max_steps, default=8, minimum=1, maximum=24)
        self.timeout_sec = float(max(1.0, min(float(self.timeout_sec), 600.0)))
        self.max_detail_chars = _bounded_int(self.max_detail_chars, default=600, minimum=120, maximum=2000)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Action:
    name: str
    args: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Any) -> "Action":
        if not isinstance(raw, dict):
            raise ValueError("planner action must be a JSON object")
        name = str(raw.get("action") or raw.get("name") or "").strip()
        args = raw.get("args", {})
        if not isinstance(args, dict):
            raise ValueError("planner action args must be an object")
        return cls(name=name, args=dict(args))

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.name, "args": dict(self.args)}


@dataclass(slots=True)
class Item:
    email_id: str
    subject: str = ""
    from_email: str = ""
    from_name: str = ""
    received_at: str = ""
    reason: str = ""
    priority: str = "normal"

    @classmethod
    def from_raw(cls, raw: Any) -> "Item":
        if not isinstance(raw, dict):
            raise ValueError("report item must be an object")
        return cls(
            email_id=str(raw.get("email_id") or raw.get("id") or "").strip(),
            subject=str(raw.get("subject", "")),
            from_email=str(raw.get("from_email", "")),
            from_name=str(raw.get("from_name", "")),
            received_at=str(raw.get("received_at", "")),
            reason=str(raw.get("reason", "")),
            priority=str(raw.get("priority", "normal") or "normal"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Step:
    index: int
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    observation: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Run:
    run_id: str
    status: str
    started_at: str
    finished_at: str = ""
    planner: str = "mock"
    provider: dict[str, Any] = field(default_factory=dict)
    budget: Budget = field(default_factory=Budget)
    steps: list[Step] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    report: str = ""
    error: str = ""
    artifact_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "planner": self.planner,
            "provider": dict(self.provider),
            "budget": self.budget.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "items": [item.to_dict() for item in self.items],
            "report": self.report,
            "error": self.error,
            "artifact_path": self.artifact_path,
        }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
