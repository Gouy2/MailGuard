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
            return
        self._sessions.pop(session_id, None)
        self._notes.pop(session_id, None)

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

