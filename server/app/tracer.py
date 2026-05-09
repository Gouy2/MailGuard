"""Tracing and lightweight JSONL logging for turns and tool calls."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_trace_dir() -> Path:
    base = os.environ.get("WISPERA_TRACE_DIR")
    if base:
        return Path(base).expanduser().resolve()
    return (Path(__file__).resolve().parents[2] / ".wispera" / "traces").resolve()


@dataclass(slots=True)
class TraceEvent:
    trace_id: str
    event: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)


class TraceLogger:
    def __init__(self, trace_dir: Path | None = None) -> None:
        self.trace_dir = (trace_dir or _default_trace_dir()).resolve()
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def start_turn(self, session_id: str, mode: str, user_message: str) -> str:
        trace_id = uuid.uuid4().hex
        self.log(
            TraceEvent(
                trace_id=trace_id,
                event="turn_start",
                timestamp=_now(),
                payload={
                    "session_id": session_id,
                    "mode": mode,
                    "user_message": user_message,
                },
            )
        )
        return trace_id

    def log(self, event: TraceEvent) -> None:
        self._append(event.trace_id, asdict(event))

    def log_event(self, trace_id: str, event: str, payload: dict[str, Any] | None = None) -> None:
        self._append(
            trace_id,
            {
                "trace_id": trace_id,
                "event": event,
                "timestamp": _now(),
                "payload": payload or {},
            },
        )

    def finish_turn(
        self,
        trace_id: str,
        *,
        status: str,
        assistant_text: str = "",
        tool_calls: int = 0,
    ) -> None:
        self.log_event(
            trace_id,
            "turn_end",
            {
                "status": status,
                "assistant_text": assistant_text,
                "tool_calls": tool_calls,
            },
        )

    def _append(self, trace_id: str, record: dict[str, Any]) -> None:
        path = self.trace_dir / f"{trace_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

