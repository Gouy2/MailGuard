"""Redaction helpers for traces, pending calls, and tool responses."""

from __future__ import annotations

from typing import Any


SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "body",
    "content",
    "password",
    "raw_response",
    "secret",
    "token",
)
MAX_STRING_PREVIEW = 300
MAX_LIST_ITEMS = 20
MAX_DICT_ITEMS = 40


def redact_for_trace(value: Any) -> Any:
    return _redact(value, path=())


def summarize_pending_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    return _redact(arguments, path=("arguments",))


def _redact(value: Any, *, path: tuple[str, ...]) -> Any:
    key = path[-1].lower() if path else ""
    if _is_sensitive_key(key):
        return _redacted_scalar(value)

    if isinstance(value, dict):
        items = list(value.items())
        redacted = {
            str(item_key): _redact(item_value, path=(*path, str(item_key)))
            for item_key, item_value in items[:MAX_DICT_ITEMS]
        }
        if len(items) > MAX_DICT_ITEMS:
            redacted["_omitted_keys"] = len(items) - MAX_DICT_ITEMS
        return redacted

    if isinstance(value, list):
        items = [_redact(item, path=path) for item in value[:MAX_LIST_ITEMS]]
        if len(value) > MAX_LIST_ITEMS:
            items.append({"_omitted_items": len(value) - MAX_LIST_ITEMS})
        return items

    if isinstance(value, tuple):
        return [_redact(item, path=path) for item in value[:MAX_LIST_ITEMS]]

    if isinstance(value, str):
        if len(value) <= MAX_STRING_PREVIEW:
            return value
        return {
            "redacted": True,
            "type": "string",
            "chars": len(value),
            "preview": value[:MAX_STRING_PREVIEW],
        }

    return value


def _is_sensitive_key(key: str) -> bool:
    return any(part in key for part in SENSITIVE_KEY_PARTS)


def _redacted_scalar(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"redacted": True, "type": "string", "chars": len(value)}
    if isinstance(value, list):
        return {"redacted": True, "type": "list", "items": len(value)}
    if isinstance(value, dict):
        return {"redacted": True, "type": "object", "keys": len(value)}
    return {"redacted": True, "type": type(value).__name__}
