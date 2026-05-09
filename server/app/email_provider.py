"""Email provider abstraction and deterministic mock provider."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


DEFAULT_MOCK_EMAIL_PATH = Path(__file__).resolve().parents[1] / "data" / "mock_emails.json"


@dataclass(slots=True)
class EmailMessage:
    id: str
    thread_id: str
    from_name: str
    from_email: str
    to: list[str]
    subject: str
    snippet: str
    body: str
    received_at: str
    labels: list[str] = field(default_factory=list)
    is_read: bool = False
    has_attachments: bool = False
    expected_category: str | None = None
    expected_importance: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EmailMessage":
        return cls(
            id=str(data["id"]),
            thread_id=str(data["thread_id"]),
            from_name=str(data.get("from_name", "")),
            from_email=str(data["from_email"]),
            to=[str(item) for item in data.get("to", [])],
            subject=str(data.get("subject", "")),
            snippet=str(data.get("snippet", "")),
            body=str(data.get("body", "")),
            received_at=str(data.get("received_at", "")),
            labels=[str(item) for item in data.get("labels", [])],
            is_read=bool(data.get("is_read", False)),
            has_attachments=bool(data.get("has_attachments", False)),
            expected_category=data.get("expected_category"),
            expected_importance=data.get("expected_importance"),
        )

    def summary_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "from_name": self.from_name,
            "from_email": self.from_email,
            "subject": self.subject,
            "snippet": self.snippet,
            "received_at": self.received_at,
            "labels": self.labels,
            "is_read": self.is_read,
            "has_attachments": self.has_attachments,
        }

    def detail_dict(self, *, max_body_chars: int = 2000) -> dict[str, Any]:
        data = self.summary_dict()
        data["body"] = self.body[:max_body_chars]
        data["body_truncated"] = len(self.body) > max_body_chars
        return data


class EmailProvider(Protocol):
    def list_recent(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        ...

    def get_detail(self, email_id: str) -> EmailMessage:
        ...

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        ...


class MockEmailProvider:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_MOCK_EMAIL_PATH
        self._emails: list[EmailMessage] | None = None

    def _load(self) -> list[EmailMessage]:
        if self._emails is None:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._emails = [EmailMessage.from_dict(item) for item in raw]
        return self._emails

    def list_recent(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        emails = self._load()
        if unread_only:
            emails = [email for email in emails if not email.is_read]
        return sorted(emails, key=lambda item: item.received_at, reverse=True)[:limit]

    def get_detail(self, email_id: str) -> EmailMessage:
        for email in self._load():
            if email.id == email_id:
                return email
        raise KeyError(f"email not found: {email_id}")

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        query = query.strip().lower()
        if not query:
            return self.list_recent(limit=limit)
        matches = []
        for email in self._load():
            haystack = " ".join(
                [
                    email.from_name,
                    email.from_email,
                    email.subject,
                    email.snippet,
                    email.body,
                    " ".join(email.labels),
                ]
            ).lower()
            if query in haystack:
                matches.append(email)
        return sorted(matches, key=lambda item: item.received_at, reverse=True)[:limit]

