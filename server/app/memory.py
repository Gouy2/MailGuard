"""Memory storage for chat history and lightweight long-term notes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
import uuid


class StateStore(Protocol):
    def clear(self, session_id: str | None = None) -> None: ...
    def load_email_preferences(self, session_id: str) -> dict[str, Any] | None: ...
    def save_email_preferences(self, session_id: str, preferences: dict[str, Any]) -> None: ...
    def load_reported_email_ids(self, session_id: str) -> set[str]: ...
    def mark_email_reported(self, session_id: str, email_id: str) -> None: ...
    def load_notifications(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_notification(self, session_id: str, notification: dict[str, Any]) -> None: ...
    def load_scan_history(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_scan(self, session_id: str, scan: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class MemoryNote:
    session_id: str
    note: str
    source: str = "tool"


class MemoryStore:
    def __init__(self, state_store: StateStore | None = None) -> None:
        self._sessions: dict[str, list[dict[str, str]]] = defaultdict(list)
        self._notes: dict[str, list[MemoryNote]] = defaultdict(list)
        self._email_preferences: dict[str, dict[str, Any]] = defaultdict(_default_email_preferences)
        self._email_scheduler: dict[str, dict[str, Any]] = defaultdict(_default_email_scheduler_state)
        self._state_store = state_store
        self._lock = RLock()

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._sessions[session_id].append({"role": role, "content": content})

    def get(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self._lock:
            history = self._sessions.get(session_id, [])
            if limit <= 0:
                return list(history)
            return list(history[-limit:])

    def clear(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
                self._notes.clear()
                self._email_preferences.clear()
                self._email_scheduler.clear()
                if self._state_store:
                    self._state_store.clear()
                return
            self._sessions.pop(session_id, None)
            self._notes.pop(session_id, None)
            self._email_preferences.pop(session_id, None)
            self._email_scheduler.pop(session_id, None)
            if self._state_store:
                self._state_store.clear(session_id)

    def close(self) -> None:
        close = getattr(self._state_store, "close", None)
        if close:
            close()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {session_id: len(messages) for session_id, messages in self._sessions.items()}

    def remember(self, session_id: str, note: str, source: str = "tool") -> MemoryNote:
        with self._lock:
            item = MemoryNote(session_id=session_id, note=note, source=source)
            self._notes[session_id].append(item)
            return item

    def notes(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self._lock:
            items = self._notes.get(session_id, [])
            if limit <= 0:
                selected = list(items)
            else:
                selected = list(items[-limit:])
            return [asdict(item) for item in selected]

    def search_notes(self, session_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            query = query.strip().lower()
            if not query:
                return self.notes(session_id, limit=limit)

            items = self._notes.get(session_id, [])
            scored: list[tuple[int, MemoryNote]] = []
            for item in items:
                text = item.note.lower()
                score = 0
                for token in query.split():
                    if token and token in text:
                        score += 1
                if score:
                    scored.append((score, item))

            scored.sort(key=lambda entry: (-entry[0], entry[1].note))
            return [asdict(item) for _, item in scored[:limit]]

    def email_preferences(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            return _copy_preferences(self._email_preferences[session_id])

    def set_email_preference(self, session_id: str, key: str, value: Any) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            preferences = self._email_preferences[session_id]
            if key not in preferences:
                raise KeyError(f"unknown email preference: {key}")
            if key in _EMAIL_LIST_KEYS:
                preferences[key] = _normalized_list(value)
            elif key in _EMAIL_STRING_KEYS:
                preferences[key] = str(value).strip()
            else:
                preferences[key] = value
            self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def add_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            if key not in _EMAIL_LIST_KEYS:
                raise KeyError(f"email preference is not a list: {key}")
            item = _normalize_preference_value(value)
            if not item:
                raise ValueError("preference value is required")
            preferences = self._email_preferences[session_id]
            if item not in preferences[key]:
                preferences[key].append(item)
                preferences[key].sort()
                self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def remove_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            if key not in _EMAIL_LIST_KEYS:
                raise KeyError(f"email preference is not a list: {key}")
            item = _normalize_preference_value(value)
            preferences = self._email_preferences[session_id]
            preferences[key] = [current for current in preferences[key] if current != item]
            self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def email_scheduler_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return _copy_scheduler_state(self._email_scheduler[session_id])

    def has_reported_email(self, session_id: str, email_id: str) -> bool:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return email_id in self._email_scheduler[session_id]["reported_email_ids"]

    def mark_email_reported(self, session_id: str, email_id: str) -> None:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            self._email_scheduler[session_id]["reported_email_ids"].add(email_id)
            if self._state_store:
                self._state_store.mark_email_reported(session_id, email_id)

    def create_email_notification_once(self, session_id: str, email_id: str, notification: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            if email_id in self._email_scheduler[session_id]["reported_email_ids"]:
                return None
            item = _notification_item(notification)
            if self._state_store:
                create_once = getattr(self._state_store, "create_email_notification_once", None)
                if create_once and not create_once(session_id, email_id, item):
                    self._reload_email_scheduler_state(session_id)
                    return None
            self._email_scheduler[session_id]["reported_email_ids"].add(email_id)
            self._email_scheduler[session_id]["notifications"].append(item)
            if self._state_store and not getattr(self._state_store, "create_email_notification_once", None):
                self._state_store.save_notification(session_id, item)
                self._state_store.mark_email_reported(session_id, email_id)
            return dict(item)

    def add_email_notification(self, session_id: str, notification: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return self._add_email_notification_locked(session_id, notification)

    def email_notifications(self, session_id: str, include_read: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            notifications = self._email_scheduler[session_id]["notifications"]
            items = notifications if include_read else [item for item in notifications if item.get("status") != "read"]
            selected = list(items[-limit:]) if limit > 0 else list(items)
            return [dict(item) for item in selected]

    def mark_email_notification_read(self, session_id: str, notification_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            for item in self._email_scheduler[session_id]["notifications"]:
                if item["notification_id"] == notification_id:
                    item["status"] = "read"
                    item["read_at"] = _now()
                    if self._state_store:
                        self._state_store.save_notification(session_id, item)
                    return dict(item)
            raise KeyError(f"notification not found: {notification_id}")

    def record_email_scan(self, session_id: str, scan: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            state = self._email_scheduler[session_id]
            item = {
                "scan_id": scan.get("scan_id") or f"scan-{uuid.uuid4().hex[:12]}",
                "created_at": scan.get("created_at") or _now(),
                **scan,
            }
            state["scan_history"].append(item)
            state["last_scan_at"] = item["created_at"]
            if self._state_store:
                self._state_store.save_scan(session_id, item)
            return dict(item)

    def email_scan_history(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            scans = self._email_scheduler[session_id]["scan_history"]
            selected = list(scans[-limit:]) if limit > 0 else list(scans)
            return [dict(item) for item in selected]

    def _ensure_email_preferences_loaded(self, session_id: str) -> None:
        if session_id in self._email_preferences:
            return
        preferences = None
        if self._state_store:
            preferences = self._state_store.load_email_preferences(session_id)
        self._email_preferences[session_id] = _merge_preferences(preferences)

    def _ensure_email_scheduler_loaded(self, session_id: str) -> None:
        if session_id in self._email_scheduler:
            return
        self._reload_email_scheduler_state(session_id)

    def _reload_email_scheduler_state(self, session_id: str) -> None:
        state = _default_email_scheduler_state()
        if self._state_store:
            state["reported_email_ids"] = self._state_store.load_reported_email_ids(session_id)
            state["notifications"] = self._state_store.load_notifications(session_id)
            state["scan_history"] = self._state_store.load_scan_history(session_id)
            if state["scan_history"]:
                state["last_scan_at"] = str(state["scan_history"][-1].get("created_at", ""))
        self._email_scheduler[session_id] = state

    def _persist_email_preferences(self, session_id: str) -> None:
        if self._state_store:
            self._state_store.save_email_preferences(session_id, _copy_preferences(self._email_preferences[session_id]))

    def _add_email_notification_locked(self, session_id: str, notification: dict[str, Any]) -> dict[str, Any]:
        state = self._email_scheduler[session_id]
        item = _notification_item(notification)
        state["notifications"].append(item)
        if self._state_store:
            self._state_store.save_notification(session_id, item)
        return dict(item)


_EMAIL_LIST_KEYS = {
    "important_senders",
    "important_domains",
    "ignored_senders",
    "ignored_domains",
    "ignored_categories",
}
_EMAIL_STRING_KEYS = {
    "report_schedule",
    "timezone",
}


def _default_email_preferences() -> dict[str, Any]:
    return {
        "important_senders": [],
        "important_domains": [],
        "ignored_senders": [],
        "ignored_domains": [],
        "ignored_categories": [],
        "report_schedule": "",
        "timezone": "",
    }


def _merge_preferences(preferences: dict[str, Any] | None) -> dict[str, Any]:
    merged = _default_email_preferences()
    if not preferences:
        return merged
    for key, value in preferences.items():
        if key in _EMAIL_LIST_KEYS:
            merged[key] = _normalized_list(value)
        elif key in _EMAIL_STRING_KEYS:
            merged[key] = str(value).strip()
    return merged


def _default_email_scheduler_state() -> dict[str, Any]:
    return {
        "reported_email_ids": set(),
        "notifications": [],
        "scan_history": [],
        "last_scan_at": "",
    }


def _copy_preferences(preferences: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in preferences.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


def _copy_scheduler_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "reported_email_ids": sorted(state["reported_email_ids"]),
        "notifications": [dict(item) for item in state["notifications"]],
        "scan_history": [dict(item) for item in state["scan_history"]],
        "last_scan_at": state["last_scan_at"],
    }


def _normalized_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise TypeError("email preference list value must be a string or list")
    normalized = [_normalize_preference_value(item) for item in values]
    return sorted({item for item in normalized if item})


def _normalize_preference_value(value: Any) -> str:
    return str(value).strip().lower()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _notification_item(notification: dict[str, Any]) -> dict[str, Any]:
    return {
        "notification_id": notification.get("notification_id") or f"notification-{uuid.uuid4().hex[:12]}",
        "created_at": notification.get("created_at") or _now(),
        "status": notification.get("status", "unread"),
        **notification,
    }
