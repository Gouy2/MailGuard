"""Tool registry and a small set of safe local tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .memory import MemoryStore


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class ToolContext:
    session_id: str
    memory_store: MemoryStore
    workspace_root: Path = WORKSPACE_ROOT
    mode: str = "agent"
    trace_id: str | None = None


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any], ToolContext], Any]
    requires_confirmation: bool = False

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self.list()]

    def execute(self, name: str, arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        tool = self.get(name)
        try:
            result = tool.handler(arguments, context)
            return {
                "ok": True,
                "tool": name,
                "result": result,
            }
        except Exception as exc:  # pragma: no cover - defensive boundary
            return {
                "ok": False,
                "tool": name,
                "error": f"{type(exc).__name__}: {exc}",
            }


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _normalize_relative_path(path: str, workspace_root: Path) -> Path:
    candidate = (workspace_root / path).expanduser().resolve()
    workspace_root = workspace_root.resolve()
    if workspace_root not in candidate.parents and candidate != workspace_root:
        raise ValueError("path must stay within workspace_root")
    return candidate


def build_default_registry(memory_store: MemoryStore) -> ToolRegistry:
    registry = ToolRegistry()

    def get_datetime(_: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        now = datetime.now().astimezone()
        return {
            "iso": now.isoformat(),
            "date": now.date().isoformat(),
            "time": now.time().replace(microsecond=0).isoformat(),
            "timezone": str(now.tzinfo),
        }

    def save_memory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        note = str(args.get("note", "")).strip()
        if not note:
            raise ValueError("note is required")
        item = context.memory_store.remember(context.session_id, note, source="save_memory")
        return {
            "saved": True,
            "note": item.note,
            "source": item.source,
        }

    def search_memory(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 5))
        return {
            "query": query,
            "results": context.memory_store.search_notes(context.session_id, query, limit=limit),
        }

    def list_files(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = str(args.get("path", ".")).strip() or "."
        target = _normalize_relative_path(path, context.workspace_root)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise NotADirectoryError(path)

        items = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            items.append(
                {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                }
            )
        return {
            "path": str(target.relative_to(context.workspace_root)),
            "items": items,
        }

    def read_text_file(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        path = str(args.get("path", "")).strip()
        if not path:
            raise ValueError("path is required")
        max_chars = int(args.get("max_chars", 8000))
        target = _normalize_relative_path(path, context.workspace_root)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_file():
            raise IsADirectoryError(path)
        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": str(target.relative_to(context.workspace_root)),
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }

    registry.register(
        ToolSpec(
            name="get_datetime",
            description="Get the current local date and time.",
            input_schema=_schema({}),
            handler=get_datetime,
        )
    )
    registry.register(
        ToolSpec(
            name="save_memory",
            description="Save a durable note about the user or the conversation.",
            input_schema=_schema(
                {
                    "note": {"type": "string", "description": "The note to store."},
                },
                required=["note"],
            ),
            handler=save_memory,
            requires_confirmation=False,
        )
    )
    registry.register(
        ToolSpec(
            name="search_memory",
            description="Search durable notes for relevant context.",
            input_schema=_schema(
                {
                    "query": {"type": "string", "description": "Search terms."},
                    "limit": {"type": "integer", "description": "Maximum results.", "default": 5},
                },
                required=["query"],
            ),
            handler=search_memory,
        )
    )
    registry.register(
        ToolSpec(
            name="list_files",
            description="List files and folders within the workspace root.",
            input_schema=_schema(
                {
                    "path": {"type": "string", "description": "Relative path under the workspace root.", "default": "."},
                }
            ),
            handler=list_files,
            requires_confirmation=False,
        )
    )
    registry.register(
        ToolSpec(
            name="read_text_file",
            description="Read a UTF-8 text file within the workspace root.",
            input_schema=_schema(
                {
                    "path": {"type": "string", "description": "Relative path under the workspace root."},
                    "max_chars": {"type": "integer", "description": "Maximum characters to return.", "default": 8000},
                },
                required=["path"],
            ),
            handler=read_text_file,
            requires_confirmation=False,
        )
    )
    return registry

