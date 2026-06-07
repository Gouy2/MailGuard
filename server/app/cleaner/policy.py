"""Automation policy for the inbox cleaner."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


DEFAULT_POLICY_MAX_EXECUTE = 5
MAX_POLICY_EXECUTE = 200
POLICY_SCHEMA_VERSION = 1
POLICY_AUTHORITIES = {
    "clean_rule": "allow_clean_rule",
    "confirmed_memory": "allow_confirmed_memory",
}


def default_clean_policy() -> dict[str, Any]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "enabled": False,
        "max_execute": DEFAULT_POLICY_MAX_EXECUTE,
        "allow_clean_rule": True,
        "allow_confirmed_memory": False,
        "updated_at": "",
        "updated_by": "",
    }


def normalize_clean_policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_clean_policy()
    if not policy:
        return normalized
    normalized["enabled"] = bool(policy.get("enabled", normalized["enabled"]))
    normalized["max_execute"] = bounded_policy_limit(policy.get("max_execute", normalized["max_execute"]))
    normalized["allow_clean_rule"] = bool(policy.get("allow_clean_rule", normalized["allow_clean_rule"]))
    normalized["allow_confirmed_memory"] = bool(
        policy.get("allow_confirmed_memory", normalized["allow_confirmed_memory"])
    )
    normalized["updated_at"] = str(policy.get("updated_at", ""))
    normalized["updated_by"] = str(policy.get("updated_by", ""))
    return normalized


def update_clean_policy(
    current: dict[str, Any] | None,
    *,
    enabled: bool | None = None,
    max_execute: int | None = None,
    allow_clean_rule: bool | None = None,
    allow_confirmed_memory: bool | None = None,
    updated_by: str = "user",
) -> dict[str, Any]:
    policy = normalize_clean_policy(current)
    if enabled is not None:
        policy["enabled"] = bool(enabled)
    if max_execute is not None:
        policy["max_execute"] = bounded_policy_limit(max_execute)
    if allow_clean_rule is not None:
        policy["allow_clean_rule"] = bool(allow_clean_rule)
    if allow_confirmed_memory is not None:
        policy["allow_confirmed_memory"] = bool(allow_confirmed_memory)
    policy["updated_at"] = _now()
    policy["updated_by"] = str(updated_by or "user")
    return policy


def select_policy_items(
    items: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = normalize_clean_policy(policy)
    allowed: list[dict[str, Any]] = []
    denied: list[dict[str, Any]] = []
    limit = bounded_policy_limit(normalized.get("max_execute"))
    for item in items:
        reason = policy_denial_reason(item, normalized)
        if reason:
            denied.append({**item, "policy_denial_reason": reason})
            continue
        if len(allowed) >= limit:
            denied.append({**item, "policy_denial_reason": f"policy max_execute limit reached: {limit}"})
            continue
        allowed.append(dict(item))
    return allowed, denied


def policy_denial_reason(item: dict[str, Any], policy: dict[str, Any]) -> str:
    if not policy.get("enabled", False):
        return "cleaner automation policy is disabled"
    authority = str(item.get("automation_authority", ""))
    key = POLICY_AUTHORITIES.get(authority)
    if not key:
        return f"unsupported automation authority: {authority}"
    if not policy.get(key, False):
        return f"automation authority is disabled by policy: {authority}"
    if str(item.get("action", "")) != "archive":
        return f"unsupported clean action: {item.get('action', '')}"
    return ""


def bounded_policy_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_POLICY_MAX_EXECUTE
    return max(1, min(parsed, MAX_POLICY_EXECUTE))


def _now() -> str:
    return datetime.now(UTC).isoformat()
