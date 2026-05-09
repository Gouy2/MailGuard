"""Agent orchestration for chat, tool use, and turn tracing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

from .memory import MemoryStore
from .prompts import build_system_prompt
from .tools import ToolContext, ToolRegistry, build_default_registry
from .tracer import TraceLogger

if TYPE_CHECKING:
    from openai import OpenAI


def _chunks(text: str, size: int = 24) -> Iterable[str]:
    if not text:
        yield ""
        return
    for index in range(0, len(text), size):
        yield text[index : index + size]


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _openai_client() -> "OpenAI | None":
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    from openai import OpenAI

    base_url = os.environ.get("OPENAI_BASE_URL") or None
    return OpenAI(api_key=api_key, base_url=base_url)


def _model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _safe_json_loads(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return data if isinstance(data, dict) else {"_value": data}


@dataclass(slots=True)
class AgentRuntime:
    memory_store: MemoryStore
    tool_registry: ToolRegistry
    tracer: TraceLogger

    @classmethod
    def create(cls) -> "AgentRuntime":
        memory_store = MemoryStore()
        tool_registry = build_default_registry(memory_store)
        tracer = TraceLogger()
        return cls(memory_store=memory_store, tool_registry=tool_registry, tracer=tracer)

    def simple_tools(self) -> list[dict[str, Any]]:
        return []

    def agent_tools(self) -> list[dict[str, Any]]:
        return self.tool_registry.openai_tools()

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "sessions": self.memory_store.snapshot(),
            "tools": [tool.name for tool in self.tool_registry.list()],
        }

    def clear(self, session_id: str | None = None) -> None:
        self.memory_store.clear(session_id)

    def memory_snapshot(self, session_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        if session_id is None:
            return {"sessions": self.memory_store.snapshot()}
        return {
            "session_id": session_id,
            "messages": self.memory_store.get(session_id, limit=limit),
            "notes": self.memory_store.notes(session_id, limit=limit),
        }

    def tool_inventory(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "permission": tool.permission.value,
                "requires_confirmation": tool.permission.value == "dangerous",
                "schema": tool.input_schema,
            }
            for tool in self.tool_registry.list()
        ]

    def pending_tools(self) -> list[dict[str, Any]]:
        return self.tool_registry.pending()

    def trace(self, trace_id: str) -> list[dict[str, Any]]:
        return self.tracer.read_trace(trace_id)

    def approve_tool(self, pending_tool_call_id: str) -> dict[str, Any]:
        pending_items = [item for item in self.tool_registry.pending() if item["id"] == pending_tool_call_id]
        if not pending_items:
            return {
                "ok": False,
                "error": "pending tool call not found",
                "pending_tool_call_id": pending_tool_call_id,
            }

        pending = pending_items[0]
        context = ToolContext(
            session_id=pending["session_id"],
            memory_store=self.memory_store,
            trace_id=pending["trace_id"],
        )
        result = self.tool_registry.approve(pending_tool_call_id, context)
        if pending["trace_id"]:
            self.tracer.log_event(
                pending["trace_id"],
                "tool_approval",
                {
                    "pending_tool_call_id": pending_tool_call_id,
                    "decision": "approved",
                    "result": result,
                },
            )
            self.tracer.finish_turn(
                pending["trace_id"],
                status="ok" if result.get("ok") else "error",
                tool_calls=1,
            )
        return result

    def reject_tool(self, pending_tool_call_id: str) -> dict[str, Any]:
        pending_items = [item for item in self.tool_registry.pending() if item["id"] == pending_tool_call_id]
        trace_id = pending_items[0]["trace_id"] if pending_items else None
        result = self.tool_registry.reject(pending_tool_call_id)
        if trace_id:
            self.tracer.log_event(
                trace_id,
                "tool_approval",
                {
                    "pending_tool_call_id": pending_tool_call_id,
                    "decision": "rejected",
                    "result": result,
                },
            )
            self.tracer.finish_turn(trace_id, status="rejected", tool_calls=1)
        return result

    def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        session_id: str = "default",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        created_trace = trace_id is None
        if trace_id is None:
            trace_id = self.tracer.start_turn(
                session_id,
                "tool",
                f"manual tool execution: {name}",
            )

        context = ToolContext(
            session_id=session_id,
            memory_store=self.memory_store,
            trace_id=trace_id,
        )
        result = self.tool_registry.execute(name, arguments, context)
        self.tracer.log_event(
            trace_id,
            "tool_result",
            {
                "tool": name,
                "arguments": arguments,
                "result": result,
            },
        )
        if created_trace and not result.get("requires_approval"):
            self.tracer.finish_turn(
                trace_id,
                status="ok" if result.get("ok") else "error",
                tool_calls=1,
            )
        result["trace_id"] = trace_id
        return result

    def execute_tool_for_test(
        self,
        name: str,
        arguments: dict[str, Any],
        session_id: str = "default",
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        return self.execute_tool(name, arguments, session_id=session_id, trace_id=trace_id)

    def stream_chat(self, session_id: str, message: str, mode: str = "agent"):
        session_id = session_id.strip() or "default"
        self.memory_store.append(session_id, "user", message)
        trace_id = self.tracer.start_turn(session_id, mode, message)
        yield _sse_event("status", {"trace_id": trace_id, "session_id": session_id, "mode": mode})

        client = _openai_client()
        if client is None:
            assistant_text = self._fallback_reply(mode, message)
            for chunk in _chunks(assistant_text):
                yield _sse_event("token", {"trace_id": trace_id, "delta": chunk, "text": assistant_text})
            self.memory_store.append(session_id, "assistant", assistant_text)
            self.tracer.finish_turn(trace_id, status="fallback", assistant_text=assistant_text, tool_calls=0)
            yield _sse_event("done", {"trace_id": trace_id, "text": assistant_text})
            return

        try:
            if mode == "simple":
                assistant_text = self._run_simple(client, session_id)
                for chunk in _chunks(assistant_text):
                    yield _sse_event("token", {"trace_id": trace_id, "delta": chunk, "text": assistant_text})
                self.memory_store.append(session_id, "assistant", assistant_text)
                self.tracer.finish_turn(trace_id, status="ok", assistant_text=assistant_text, tool_calls=0)
                yield _sse_event("done", {"trace_id": trace_id, "text": assistant_text})
                return

            assistant_text, tool_calls = self._run_agent(client, session_id, trace_id)
            for chunk in _chunks(assistant_text):
                yield _sse_event("token", {"trace_id": trace_id, "delta": chunk, "text": assistant_text})
            self.memory_store.append(session_id, "assistant", assistant_text)
            self.tracer.finish_turn(
                trace_id,
                status="ok",
                assistant_text=assistant_text,
                tool_calls=tool_calls,
            )
            yield _sse_event("done", {"trace_id": trace_id, "text": assistant_text, "tool_calls": tool_calls})
        except Exception as exc:  # pragma: no cover - defensive boundary
            self.tracer.finish_turn(trace_id, status="error")
            yield _sse_event(
                "error",
                {
                    "trace_id": trace_id,
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )

    def _build_messages(self, session_id: str, mode: str) -> list[dict[str, Any]]:
        messages = [{"role": "system", "content": build_system_prompt(mode, self.tool_inventory())}]
        messages.extend(self.memory_store.get(session_id, limit=20))
        return messages

    def _fallback_reply(self, mode: str, message: str) -> str:
        if mode == "agent":
            return f"我先记下了：{message}"
        return f"收到：{message}"

    def _run_simple(self, client: "OpenAI", session_id: str) -> str:
        messages = self._build_messages(session_id, mode="simple")
        response = client.chat.completions.create(
            model=_model_name(),
            messages=messages,
        )
        message = response.choices[0].message
        return (message.content or "").strip() or self._fallback_reply("simple", "")

    def _run_agent(self, client: "OpenAI", session_id: str, trace_id: str) -> tuple[str, int]:
        messages = self._build_messages(session_id, mode="agent")
        tool_calls_total = 0
        context = ToolContext(
            session_id=session_id,
            memory_store=self.memory_store,
            trace_id=trace_id,
        )

        for _ in range(4):
            response = client.chat.completions.create(
                model=_model_name(),
                messages=messages,
                tools=self.agent_tools(),
                tool_choice="auto",
            )
            message = response.choices[0].message
            tool_calls = message.tool_calls or []
            if tool_calls:
                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                        for tool_call in tool_calls
                    ],
                }
                messages.append(assistant_message)
                for tool_call in tool_calls:
                    tool_calls_total += 1
                    arguments = _safe_json_loads(tool_call.function.arguments or "{}")
                    self.tracer.log_event(
                        trace_id,
                        "tool_call",
                        {
                            "tool": tool_call.function.name,
                            "arguments": arguments,
                            "tool_call_id": tool_call.id,
                        },
                    )
                    result = self.tool_registry.execute(tool_call.function.name, arguments, context)
                    if result.get("requires_approval"):
                        self.tracer.log_event(
                            trace_id,
                            "tool_pending",
                            {
                                "tool": tool_call.function.name,
                                "pending_tool_call_id": result.get("pending_tool_call_id"),
                                "reason": result.get("reason"),
                            },
                        )
                    self.tracer.log_event(
                        trace_id,
                        "tool_result",
                        {
                            "tool": tool_call.function.name,
                            "result": result,
                            "tool_call_id": tool_call.id,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                continue

            assistant_text = (message.content or "").strip()
            if assistant_text:
                return assistant_text, tool_calls_total

            return self._fallback_reply("agent", ""), tool_calls_total

        return "我暂时没法把任务收束成一个结果。", tool_calls_total
