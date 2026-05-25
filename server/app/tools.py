"""Tool registry and a small set of safe local tools."""

from __future__ import annotations

import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal

from .memory import MemoryStore
from .redaction import summarize_pending_arguments
from .runtime_env import env_flag
from .provider_factory import create_email_provider


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
MAX_TOOL_RESULT_CHARS = 12000
SENSITIVE_PATH_PARTS = {
    ".env",
    ".git",
    ".venv",
    ".mailguard",
    "__pycache__",
    "uv.lock",
}
SENSITIVE_SUFFIXES = {
    ".key",
    ".pem",
    ".p12",
    ".pfx",
    ".sqlite",
    ".db",
}
SHELL_DENIED_TOKENS = ("&", "||", ";", "|", ">", "<", "`", "$(", "\n", "\r")
SHELL_ALLOWED_COMMANDS = {
    "cat",
    "dir",
    "findstr",
    "git",
    "ls",
    "more",
    "pwd",
    "type",
    "where",
    "whoami",
}
SHELL_DENIED_COMMANDS = {
    "attrib",
    "chmod",
    "chown",
    "copy",
    "cp",
    "del",
    "erase",
    "format",
    "kill",
    "move",
    "mv",
    "rd",
    "reg",
    "ren",
    "rename",
    "rm",
    "rmdir",
    "shutdown",
    "sudo",
    "takeown",
    "taskkill",
}
SHELL_DENIED_PATTERNS = (
    re.compile(r"\brm\s+-[^\n]*r", re.IGNORECASE),
    re.compile(r"\bdel\s+(/[sq]|-[^\n]*r)", re.IGNORECASE),
    re.compile(r"\brmdir\s+(/[sq]|-[^\n]*r)", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\bsudo\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b", re.IGNORECASE),
)


class ToolPermission(StrEnum):
    READ = "read"
    WRITE = "write"
    DANGEROUS = "dangerous"


@dataclass(slots=True)
class PendingToolCall:
    id: str
    tool_name: str
    arguments: dict[str, Any]
    session_id: str
    trace_id: str | None
    reason: str
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())


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
    permission: ToolPermission = ToolPermission.READ

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
        self._pending: dict[str, PendingToolCall] = {}
        self._lock = RLock()

    def register(self, spec: ToolSpec) -> None:
        with self._lock:
            self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        with self._lock:
            return self._tools[name]

    def list(self) -> list[ToolSpec]:
        with self._lock:
            return list(self._tools.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.as_openai_tool() for tool in self.list()]

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": item.id,
                    "tool_name": item.tool_name,
                    "arguments": summarize_pending_arguments(item.arguments),
                    "session_id": item.session_id,
                    "trace_id": item.trace_id,
                    "reason": item.reason,
                    "created_at": item.created_at,
                }
                for item in self._pending.values()
            ]

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        approval: Literal["auto", "require", "approved"] = "auto",
    ) -> dict[str, Any]:
        with self._lock:
            tool = self._tools.get(name)
        if tool is None:
            return {
                "ok": False,
                "tool": name,
                "error": "unknown tool",
            }
        validation_error = self._validate(tool.input_schema, arguments)
        if validation_error:
            return {
                "ok": False,
                "tool": name,
                "error": validation_error,
                "error_type": "validation_error",
            }

        policy_error = self._policy_check(tool, arguments)
        if policy_error:
            return {
                "ok": False,
                "tool": name,
                "error": policy_error,
                "error_type": "policy_error",
            }

        if tool.permission == ToolPermission.DANGEROUS and approval != "approved":
            pending = PendingToolCall(
                id=uuid.uuid4().hex,
                tool_name=name,
                arguments=arguments,
                session_id=context.session_id,
                trace_id=context.trace_id,
                reason="dangerous tool requires explicit approval",
            )
            with self._lock:
                self._pending[pending.id] = pending
            return {
                "ok": False,
                "tool": name,
                "requires_approval": True,
                "pending_tool_call_id": pending.id,
                "reason": pending.reason,
            }

        started_at = time.perf_counter()
        try:
            result = tool.handler(arguments, context)
            return {
                "ok": True,
                "tool": name,
                "permission": tool.permission.value,
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "result": _truncate_result(result),
            }
        except Exception as exc:  # pragma: no cover - defensive boundary
            return {
                "ok": False,
                "tool": name,
                "permission": tool.permission.value,
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "error": f"{type(exc).__name__}: {exc}",
                "error_type": "execution_error",
            }

    def approve(self, pending_id: str, context: ToolContext) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(pending_id, None)
        if pending is None:
            return {
                "ok": False,
                "error": "pending tool call not found",
                "pending_tool_call_id": pending_id,
            }
        return self.execute(pending.tool_name, pending.arguments, context, approval="approved")

    def pop_pending(self, pending_id: str) -> PendingToolCall | None:
        with self._lock:
            return self._pending.pop(pending_id, None)

    def reject(self, pending_id: str) -> dict[str, Any]:
        with self._lock:
            pending = self._pending.pop(pending_id, None)
        if pending is None:
            return {
                "ok": False,
                "error": "pending tool call not found",
                "pending_tool_call_id": pending_id,
            }
        return {
            "ok": True,
            "rejected": True,
            "pending_tool_call_id": pending_id,
            "tool": pending.tool_name,
        }

    def _validate(self, schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
        if not isinstance(arguments, dict):
            return "tool arguments must be an object"

        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional_allowed = schema.get("additionalProperties", True)

        for name in required:
            if name not in arguments:
                return f"missing required argument: {name}"

        if not additional_allowed:
            extra = sorted(set(arguments) - set(properties))
            if extra:
                return f"unexpected argument(s): {', '.join(extra)}"

        for name, value in arguments.items():
            if name not in properties:
                continue
            expected = properties[name].get("type")
            if expected and not _matches_type(value, expected):
                return f"argument {name} must be {expected}"

            minimum = properties[name].get("minimum")
            maximum = properties[name].get("maximum")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if minimum is not None and value < minimum:
                    return f"argument {name} must be >= {minimum}"
                if maximum is not None and value > maximum:
                    return f"argument {name} must be <= {maximum}"

        return None

    def _policy_check(self, tool: ToolSpec, arguments: dict[str, Any]) -> str | None:
        if tool.name == "run_shell_command":
            command = str(arguments.get("command", ""))
            return _shell_policy_error(command)
        return None


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _truncate_result(result: Any) -> Any:
    text = str(result)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return result
    return {
        "truncated": True,
        "preview": text[:MAX_TOOL_RESULT_CHARS],
        "original_type": type(result).__name__,
    }


def _normalize_relative_path(path: str, workspace_root: Path) -> Path:
    candidate = (workspace_root / path).expanduser().resolve()
    workspace_root = workspace_root.resolve()
    if workspace_root not in candidate.parents and candidate != workspace_root:
        raise ValueError("path must stay within workspace_root")
    return candidate


def _safe_workspace_path(path: str, workspace_root: Path) -> Path:
    candidate = _normalize_relative_path(path, workspace_root)
    if _is_sensitive_workspace_path(candidate, workspace_root):
        raise PermissionError("path is not readable by this tool")
    return candidate


def _is_sensitive_workspace_path(path: Path, workspace_root: Path) -> bool:
    try:
        relative = path.resolve().relative_to(workspace_root.resolve())
    except ValueError:
        return True
    parts = set(relative.parts)
    if parts & SENSITIVE_PATH_PARTS:
        return True
    if any(part.startswith(".env") for part in relative.parts):
        return True
    return path.suffix.lower() in SENSITIVE_SUFFIXES


def _shell_policy_error(command: str) -> str | None:
    command = command.strip()
    if not command:
        return "command is required"

    if any(token in command for token in SHELL_DENIED_TOKENS):
        return "command contains denied shell syntax"

    for pattern in SHELL_DENIED_PATTERNS:
        if pattern.search(command):
            return "command matches denied high-risk pattern"

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return f"command parse error: {exc}"

    if not parts:
        return "command is required"

    executable = Path(parts[0].strip("\"'")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]

    if executable in SHELL_DENIED_COMMANDS:
        return f"command is explicitly denied: {executable}"
    if executable not in SHELL_ALLOWED_COMMANDS:
        return f"command is not in allowlist: {executable}"
    return None


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
        target = _safe_workspace_path(path, context.workspace_root)
        if not target.exists():
            raise FileNotFoundError(path)
        if not target.is_dir():
            raise NotADirectoryError(path)

        items = []
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if _is_sensitive_workspace_path(child, context.workspace_root):
                continue
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
        target = _safe_workspace_path(path, context.workspace_root)
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

    def run_shell_command(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        command = str(args.get("command", "")).strip()
        timeout_seconds = int(args.get("timeout_seconds", 10))
        policy_error = _shell_policy_error(command)
        if policy_error:
            raise PermissionError(policy_error)

        completed = subprocess.run(
            command,
            cwd=context.workspace_root,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-6000:],
            "stderr": completed.stderr[-6000:],
        }

    registry.register(
        ToolSpec(
            name="get_datetime",
            description="Get the current local date and time.",
            input_schema=_schema({}),
            handler=get_datetime,
            permission=ToolPermission.READ,
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
            permission=ToolPermission.WRITE,
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
            permission=ToolPermission.READ,
        )
    )
    if env_flag("MAILGUARD_DEV_TOOLS"):
        registry.register(
            ToolSpec(
                name="list_files",
                description="List non-sensitive files and folders within the workspace root.",
                input_schema=_schema(
                    {
                        "path": {
                            "type": "string",
                            "description": "Relative path under the workspace root.",
                            "default": ".",
                        },
                    }
                ),
                handler=list_files,
                permission=ToolPermission.READ,
            )
        )
        registry.register(
            ToolSpec(
                name="read_text_file",
                description="Read a non-sensitive UTF-8 text file within the workspace root.",
                input_schema=_schema(
                    {
                        "path": {"type": "string", "description": "Relative path under the workspace root."},
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to return.",
                            "default": 8000,
                            "minimum": 1,
                            "maximum": 50000,
                        },
                    },
                    required=["path"],
                ),
                handler=read_text_file,
                permission=ToolPermission.READ,
            )
        )
        registry.register(
            ToolSpec(
                name="run_shell_command",
                description="Run a restricted shell command in the workspace root. This is dangerous and requires explicit user approval.",
                input_schema=_schema(
                    {
                        "command": {"type": "string", "description": "Shell command to execute."},
                        "timeout_seconds": {
                            "type": "integer",
                            "description": "Timeout in seconds.",
                            "default": 10,
                            "minimum": 1,
                            "maximum": 30,
                        },
                    },
                    required=["command"],
                ),
                handler=run_shell_command,
                permission=ToolPermission.DANGEROUS,
            )
        )
    from .email_tools import register_email_tools

    register_email_tools(registry, provider=create_email_provider())
    return registry
