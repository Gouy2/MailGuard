"""Clean rule models and matching helpers."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ..archive.models import EmailClassification
from ..email_provider import EmailMessage


CleanRuleAction = Literal["archive", "protect"]
CleanRuleScope = Literal["sender", "domain", "keyword", "category"]
CleanRuleStatus = Literal["proposed", "enabled", "disabled"]

RULE_ACTIONS = {"archive", "protect"}
RULE_SCOPES = {"sender", "domain", "keyword", "category"}
RULE_STATUSES = {"proposed", "enabled", "disabled"}


@dataclass(frozen=True, slots=True)
class CleanRule:
    rule_id: str
    action: str
    scope: str
    value: str
    status: str = "proposed"
    source: str = "user_teach"
    reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    approved_at: str = ""
    disabled_at: str = ""
    examples: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        action: str,
        scope: str,
        value: str,
        status: str = "proposed",
        source: str = "user_teach",
        reason: str = "",
        examples: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CleanRule":
        normalized_action = normalize_action(action)
        normalized_scope = normalize_scope(scope)
        normalized_value = normalize_value(value)
        if not normalized_value:
            raise ValueError("clean rule value is required")
        normalized_status = normalize_status(status)
        now = _now()
        return cls(
            rule_id=rule_id(normalized_action, normalized_scope, normalized_value),
            action=normalized_action,
            scope=normalized_scope,
            value=normalized_value,
            status=normalized_status,
            source=str(source or "user_teach"),
            reason=str(reason or ""),
            created_at=now,
            updated_at=now,
            approved_at=now if normalized_status == "enabled" else "",
            disabled_at=now if normalized_status == "disabled" else "",
            examples=tuple(str(item) for item in (examples or []) if str(item).strip()),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CleanRule":
        return cls(
            rule_id=str(data.get("rule_id", "")),
            action=normalize_action(data.get("action", "")),
            scope=normalize_scope(data.get("scope", "")),
            value=normalize_value(data.get("value", "")),
            status=normalize_status(data.get("status", "proposed")),
            source=str(data.get("source", "user_teach") or "user_teach"),
            reason=str(data.get("reason", "")),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            approved_at=str(data.get("approved_at", "")),
            disabled_at=str(data.get("disabled_at", "")),
            examples=tuple(str(item) for item in (data.get("examples") or [])),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["examples"] = list(self.examples)
        return data


def proposed_rule(
    *,
    action: str,
    scope: str,
    value: str,
    source: str,
    reason: str = "",
    examples: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return CleanRule.new(
        action=action,
        scope=scope,
        value=value,
        status="proposed",
        source=source,
        reason=reason,
        examples=examples,
        metadata=metadata,
    ).to_dict()


def enable_rule(rule: dict[str, Any]) -> dict[str, Any]:
    item = CleanRule.from_dict(rule).to_dict()
    now = _now()
    item["status"] = "enabled"
    item["updated_at"] = now
    item["approved_at"] = item.get("approved_at") or now
    item["disabled_at"] = ""
    return item


def disable_rule(rule: dict[str, Any]) -> dict[str, Any]:
    item = CleanRule.from_dict(rule).to_dict()
    now = _now()
    item["status"] = "disabled"
    item["updated_at"] = now
    item["disabled_at"] = now
    return item


def rule_matches(rule: dict[str, Any], email: EmailMessage | dict[str, Any], classification: EmailClassification | None = None) -> bool:
    item = CleanRule.from_dict(rule)
    value = item.value
    if item.scope == "sender":
        return _sender(email) == value
    if item.scope == "domain":
        domain = _domain(_sender(email))
        return bool(domain and (domain == value or domain.endswith(f".{value}")))
    if item.scope == "keyword":
        return bool(value and value in _email_text(email))
    if item.scope == "category":
        category = classification.normalized_category if classification else str(_get(email, "category", "")).lower()
        return category == value
    return False


def matching_rules(
    rules: list[dict[str, Any]],
    *,
    email: EmailMessage | dict[str, Any],
    classification: EmailClassification | None = None,
    action: str = "",
    status: str = "enabled",
) -> list[dict[str, Any]]:
    selected = []
    for rule in rules:
        if status and str(rule.get("status", "")) != status:
            continue
        if action and str(rule.get("action", "")) != action:
            continue
        if rule_matches(rule, email, classification):
            selected.append(CleanRule.from_dict(rule).to_dict())
    return selected


def normalize_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    if action not in RULE_ACTIONS:
        raise ValueError(f"unsupported clean rule action: {action}")
    return action


def normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    if scope not in RULE_SCOPES:
        raise ValueError(f"unsupported clean rule scope: {scope}")
    return scope


def normalize_status(value: Any) -> str:
    status = str(value or "proposed").strip().lower()
    if status not in RULE_STATUSES:
        raise ValueError(f"unsupported clean rule status: {status}")
    return status


def normalize_value(value: Any) -> str:
    return str(value or "").strip().lower()


def rule_id(action: str, scope: str, value: str) -> str:
    digest = hashlib.sha1(f"{action}:{scope}:{value}".encode("utf-8")).hexdigest()[:10]
    return f"rule-{action}-{scope}-{digest}"


def manual_rule_id() -> str:
    return f"rule-manual-{uuid.uuid4().hex[:12]}"


def _sender(email: EmailMessage | dict[str, Any]) -> str:
    return str(_get(email, "from_email", "")).strip().lower()


def _domain(sender: str) -> str:
    if "@" not in sender:
        return ""
    return sender.rsplit("@", 1)[1].strip().lower()


def _email_text(email: EmailMessage | dict[str, Any]) -> str:
    parts = [
        _get(email, "from_name", ""),
        _get(email, "from_email", ""),
        _get(email, "subject", ""),
        _get(email, "snippet", ""),
        _get(email, "body", ""),
    ]
    labels = _get(email, "labels", [])
    if isinstance(labels, list):
        parts.extend(labels)
    return " ".join(str(part or "") for part in parts).lower()


def _get(email: EmailMessage | dict[str, Any], key: str, default: Any = "") -> Any:
    if isinstance(email, dict):
        return email.get(key, default)
    return getattr(email, key, default)


def _now() -> str:
    return datetime.now(UTC).isoformat()
