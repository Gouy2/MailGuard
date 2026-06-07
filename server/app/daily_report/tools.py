"""Read-only tools available to the daily report agent loop."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..email_provider import EmailMessage, EmailProvider
from ..memory_workflow import run_confirmed_memory_workflow
from ..runtime_env import SERVER_ROOT
from .models import Action, Budget, READ_ACTIONS


DEFAULT_MEMORY_PATH = SERVER_ROOT / "data" / "memory_proposals.json"


class DailyTools:
    def __init__(
        self,
        provider: EmailProvider,
        *,
        budget: Budget,
        memory_path: str | Path | None = None,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.memory_path = Path(memory_path) if memory_path else DEFAULT_MEMORY_PATH

    def execute(self, action: Action) -> dict[str, Any]:
        if action.name not in READ_ACTIONS:
            raise ValueError(f"unsupported daily report action: {action.name}")
        if action.name == "list_recent":
            return self._list_recent(action.args)
        if action.name == "search":
            return self._search(action.args)
        if action.name == "get_detail":
            return self._get_detail(action.args)
        if action.name == "memory":
            return self._memory()
        raise ValueError(f"unsupported daily report action: {action.name}")

    def _list_recent(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = _bounded_int(args.get("limit"), default=self.budget.limit, minimum=1, maximum=self.budget.limit)
        unread_only = bool(args.get("unread_only", False))
        emails = self.provider.list_recent(limit=limit, unread_only=unread_only)
        emails = _filter_recent(emails, hours=self.budget.hours)
        return {
            "provider": type(self.provider).__name__,
            "count": len(emails),
            "emails": [_summary(email) for email in emails],
            "mailbox_mutation": False,
        }

    def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        if not query:
            raise ValueError("query is required")
        limit = _bounded_int(args.get("limit"), default=self.budget.limit, minimum=1, maximum=self.budget.limit)
        emails = self.provider.search(query=query, limit=limit)
        emails = _filter_recent(emails, hours=self.budget.hours)
        return {
            "provider": type(self.provider).__name__,
            "query": query,
            "count": len(emails),
            "emails": [_summary(email) for email in emails],
            "mailbox_mutation": False,
        }

    def _get_detail(self, args: dict[str, Any]) -> dict[str, Any]:
        email_id = str(args.get("email_id", "")).strip()
        if not email_id:
            raise ValueError("email_id is required")
        max_chars = _bounded_int(
            args.get("max_chars", args.get("max_body_chars")),
            default=self.budget.max_detail_chars,
            minimum=120,
            maximum=self.budget.max_detail_chars,
        )
        email = self.provider.get_detail(email_id)
        preview = _clip(email.body, max_chars)
        return {
            "provider": type(self.provider).__name__,
            "email": {
                **_summary(email),
                "text_preview": preview,
                "text_truncated": len(email.body) > len(preview),
            },
            "mailbox_mutation": False,
        }

    def _memory(self) -> dict[str, Any]:
        result = run_confirmed_memory_workflow(memory_path=self.memory_path)
        return {
            **result,
            "mailbox_mutation": False,
            "policy_mutation": False,
        }


def provider_summary(provider: EmailProvider) -> dict[str, Any]:
    status = getattr(provider, "status", None)
    if callable(status):
        try:
            raw = status()
        except Exception as exc:  # pragma: no cover - defensive metadata boundary
            raw = {"provider": type(provider).__name__, "status": "error", "error": str(exc)}
    else:
        raw = {"provider": type(provider).__name__, "status": "available"}
    summary_keys = (
        "provider",
        "status",
        "email",
        "host",
        "port",
        "mailbox",
        "mailbox_display",
        "selected_mailbox",
        "message_count",
        "unread_count",
        "current_mailbox_count",
        "visible_mailbox_count",
    )
    summary = {key: raw[key] for key in summary_keys if key in raw}
    summary["mailbox_mutation"] = False
    if "provider" not in summary:
        summary["provider"] = type(provider).__name__
    return summary


def _summary(email: EmailMessage) -> dict[str, Any]:
    return {
        "id": email.id,
        "thread_id": email.thread_id,
        "from_name": email.from_name,
        "from_email": email.from_email,
        "subject": email.subject,
        "snippet": email.snippet,
        "received_at": email.received_at,
        "labels": list(email.labels),
        "is_read": email.is_read,
        "has_attachments": email.has_attachments,
    }


def _filter_recent(emails: list[EmailMessage], *, hours: int) -> list[EmailMessage]:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, hours))
    return [email for email in emails if _received_after(email.received_at, cutoff)]


def _received_after(value: str, cutoff: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed >= cutoff


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
