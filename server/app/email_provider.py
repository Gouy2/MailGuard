"""Email provider abstraction and deterministic mock provider."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    expected_action: str | None = None
    expected_reportable: bool | None = None
    expected_ignored: bool | None = None
    difficulty: str = ""
    notes: str = ""

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
            expected_action=data.get("expected_action"),
            expected_reportable=data.get("expected_reportable"),
            expected_ignored=data.get("expected_ignored"),
            difficulty=str(data.get("difficulty", "")),
            notes=str(data.get("notes", "")),
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

    def archive(self, email_id: str) -> dict[str, Any]:
        ...

    def mark_read(self, email_id: str, is_read: bool = True) -> dict[str, Any]:
        ...

    def star(self, email_id: str, starred: bool = True) -> dict[str, Any]:
        ...

    def create_draft(self, email_id: str, body: str, to: list[str] | None = None) -> dict[str, Any]:
        ...


class MockEmailProvider:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_MOCK_EMAIL_PATH
        self._emails: list[EmailMessage] | None = None
        self._drafts: list[dict[str, Any]] = []

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
        return self._find(email_id)

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

    def archive(self, email_id: str) -> dict[str, Any]:
        email = self._find(email_id)
        labels_before = list(email.labels)
        email.labels = [label for label in email.labels if label.lower() != "inbox"]
        if "archived" not in {label.lower() for label in email.labels}:
            email.labels.append("archived")
        return {
            "email_id": email.id,
            "archived": True,
            "labels_before": labels_before,
            "labels_after": list(email.labels),
        }

    def mark_read(self, email_id: str, is_read: bool = True) -> dict[str, Any]:
        email = self._find(email_id)
        was_read = email.is_read
        email.is_read = is_read
        return {
            "email_id": email.id,
            "is_read": email.is_read,
            "was_read": was_read,
        }

    def star(self, email_id: str, starred: bool = True) -> dict[str, Any]:
        email = self._find(email_id)
        labels_before = list(email.labels)
        normalized = {label.lower() for label in email.labels}
        if starred and "starred" not in normalized:
            email.labels.append("starred")
        if not starred:
            email.labels = [label for label in email.labels if label.lower() != "starred"]
        return {
            "email_id": email.id,
            "starred": starred,
            "labels_before": labels_before,
            "labels_after": list(email.labels),
        }

    def create_draft(self, email_id: str, body: str, to: list[str] | None = None) -> dict[str, Any]:
        email = self._find(email_id)
        body = body.strip()
        if not body:
            raise ValueError("draft body is required")
        draft = {
            "draft_id": f"draft-{uuid.uuid4().hex[:12]}",
            "source_email_id": email.id,
            "thread_id": email.thread_id,
            "to": to or [email.from_email],
            "subject": _reply_subject(email.subject),
            "body": body,
            "created_at": datetime.now(UTC).isoformat(),
            "sent": False,
        }
        self._drafts.append(draft)
        return {
            "draft_id": draft["draft_id"],
            "source_email_id": draft["source_email_id"],
            "thread_id": draft["thread_id"],
            "to": draft["to"],
            "subject": draft["subject"],
            "body_preview": draft["body"][:500],
            "sent": False,
        }

    def drafts(self) -> list[dict[str, Any]]:
        return list(self._drafts)

    def _find(self, email_id: str) -> EmailMessage:
        for email in self._load():
            if email.id == email_id:
                return email
        raise KeyError(f"email not found: {email_id}")


def _reply_subject(subject: str) -> str:
    subject = subject.strip()
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"
