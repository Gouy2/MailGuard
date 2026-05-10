"""Headless email scheduler core for scans, notifications, and digests."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Callable
import uuid

from .email_provider import EmailProvider
from .memory import MemoryStore


Classifier = Callable[[Any, dict[str, Any] | None], dict[str, Any]]


def run_email_scan(
    *,
    provider: EmailProvider,
    memory_store: MemoryStore,
    session_id: str,
    classifier: Classifier,
    limit: int = 20,
    unread_only: bool = True,
    important_only: bool = True,
) -> dict[str, Any]:
    preferences = memory_store.email_preferences(session_id)
    emails = provider.list_recent(limit=limit, unread_only=unread_only)
    classified = [(email, classifier(email, preferences)) for email in emails]

    reportable = [
        (email, decision)
        for email, decision in classified
        if decision["is_reportable"] and (not important_only or decision["importance"] == "high")
    ]
    created_notifications = []
    skipped_duplicate_ids = []

    for email, decision in reportable:
        notification = memory_store.create_email_notification_once(
            session_id,
            email.id,
            {
                "type": "important_email",
                "email_id": email.id,
                "thread_id": email.thread_id,
                "from_name": email.from_name,
                "from_email": email.from_email,
                "subject": email.subject,
                "snippet": email.snippet,
                "received_at": email.received_at,
                "category": decision["category"],
                "importance": decision["importance"],
                "suggested_action": decision["suggested_action"],
                "reasons": decision["reasons"],
            },
        )
        if notification is None:
            skipped_duplicate_ids.append(email.id)
            continue
        created_notifications.append(notification)

    ignored_count = sum(1 for _, decision in classified if decision["is_ignored"])
    scan = memory_store.record_email_scan(
        session_id,
        {
            "scan_id": f"scan-{uuid.uuid4().hex[:12]}",
            "created_at": _now(),
            "provider": type(provider).__name__,
            "fetched": len(emails),
            "classified_count": len(classified),
            "reportable_count": len(reportable),
            "ignored_count": ignored_count,
            "created_notification_count": len(created_notifications),
            "skipped_duplicate_count": len(skipped_duplicate_ids),
            "created_notification_ids": [item["notification_id"] for item in created_notifications],
            "skipped_duplicate_email_ids": skipped_duplicate_ids,
        },
    )

    return {
        "scan": scan,
        "created_notifications": created_notifications,
        "classified": [
            {
                "email_id": email.id,
                "category": decision["category"],
                "importance": decision["importance"],
                "suggested_action": decision["suggested_action"],
                "reasons": decision["reasons"],
                "is_reportable": decision["is_reportable"],
                "is_ignored": decision["is_ignored"],
            }
            for email, decision in classified
        ],
    }


def list_email_notifications(
    *,
    memory_store: MemoryStore,
    session_id: str,
    include_read: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    notifications = memory_store.email_notifications(session_id, include_read=include_read, limit=limit)
    return {
        "count": len(notifications),
        "notifications": notifications,
    }


def mark_email_notification_read(
    *,
    memory_store: MemoryStore,
    session_id: str,
    notification_id: str,
) -> dict[str, Any]:
    return {
        "notification": memory_store.mark_email_notification_read(session_id, notification_id),
    }


def email_daily_digest(
    *,
    memory_store: MemoryStore,
    session_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    notifications = memory_store.email_notifications(session_id, include_read=True, limit=limit)
    category_counts = Counter(item["category"] for item in notifications)
    importance_counts = Counter(item["importance"] for item in notifications)
    action_counts = Counter(item["suggested_action"] for item in notifications)
    return {
        "notification_count": len(notifications),
        "category_counts": dict(sorted(category_counts.items())),
        "importance_counts": dict(sorted(importance_counts.items())),
        "action_counts": dict(sorted(action_counts.items())),
        "items": notifications,
    }


def email_scheduler_state(*, memory_store: MemoryStore, session_id: str) -> dict[str, Any]:
    return memory_store.email_scheduler_state(session_id)


def _now() -> str:
    return datetime.now(UTC).isoformat()
