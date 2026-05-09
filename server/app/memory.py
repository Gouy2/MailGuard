"""Memory storage for chat history and lightweight long-term notes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class MemoryNote:
    session_id: str
    note: str
    source: str = "tool"


class MemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, list[dict[str, str]]] = defaultdict(list)
        self._notes: dict[str, list[MemoryNote]] = defaultdict(list)
        self._email_preferences: dict[str, dict[str, Any]] = defaultdict(_default_email_preferences)

    def append(self, session_id: str, role: str, content: str) -> None:
        self._sessions[session_id].append({"role": role, "content": content})

    def get(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        history = self._sessions.get(session_id, [])
        if limit <= 0:
            return list(history)
        return list(history[-limit:])

    def clear(self, session_id: str | None = None) -> None:
        if session_id is None:
            self._sessions.clear()
            self._notes.clear()
            self._email_preferences.clear()
            return
        self._sessions.pop(session_id, None)
        self._notes.pop(session_id, None)
        self._email_preferences.pop(session_id, None)

    def snapshot(self) -> dict[str, int]:
        return {session_id: len(messages) for session_id, messages in self._sessions.items()}

    def remember(self, session_id: str, note: str, source: str = "tool") -> MemoryNote:
        item = MemoryNote(session_id=session_id, note=note, source=source)
        self._notes[session_id].append(item)
        return item

    def notes(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        items = self._notes.get(session_id, [])
        if limit <= 0:
            selected = list(items)
        else:
            selected = list(items[-limit:])
        return [asdict(item) for item in selected]

    def search_notes(self, session_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
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
        return _copy_preferences(self._email_preferences[session_id])

    def set_email_preference(self, session_id: str, key: str, value: Any) -> dict[str, Any]:
        preferences = self._email_preferences[session_id]
        if key not in preferences:
            raise KeyError(f"unknown email preference: {key}")
        if key in _EMAIL_LIST_KEYS:
            preferences[key] = _normalized_list(value)
        elif key in _EMAIL_STRING_KEYS:
            preferences[key] = str(value).strip()
        else:
            preferences[key] = value
        return self.email_preferences(session_id)

    def add_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        if key not in _EMAIL_LIST_KEYS:
            raise KeyError(f"email preference is not a list: {key}")
        item = _normalize_preference_value(value)
        if not item:
            raise ValueError("preference value is required")
        preferences = self._email_preferences[session_id]
        if item not in preferences[key]:
            preferences[key].append(item)
            preferences[key].sort()
        return self.email_preferences(session_id)

    def remove_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        if key not in _EMAIL_LIST_KEYS:
            raise KeyError(f"email preference is not a list: {key}")
        item = _normalize_preference_value(value)
        preferences = self._email_preferences[session_id]
        preferences[key] = [current for current in preferences[key] if current != item]
        return self.email_preferences(session_id)


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


def _copy_preferences(preferences: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in preferences.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


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
