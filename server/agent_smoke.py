"""Run agent tool-use smoke tests on the mock provider."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Iterable

if __package__ in {None, ""}:  # pragma: no cover - runtime path bootstrap for script execution
    current_file = Path(__file__).resolve()
    sys.path.insert(0, str(current_file.parent))
    sys.path.insert(0, str(current_file.parent.parent))

try:
    from app.agent import AgentRuntime
    import app.agent as agent_module
except ModuleNotFoundError as exc:  # pragma: no cover - used when imported from repo root
    if exc.name != "app":
        raise
    from server.app.agent import AgentRuntime
    import server.app.agent as agent_module


RuntimeFactory = Callable[[], AgentRuntime]


class ScriptedFunction:
    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self.name = name
        self.arguments = json.dumps(arguments, ensure_ascii=False)


class ScriptedToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: dict[str, Any]) -> None:
        self.id = tool_call_id
        self.type = "function"
        self.function = ScriptedFunction(name, arguments)


class ScriptedMessage:
    def __init__(self, content: str = "", tool_calls: list[ScriptedToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class ScriptedChoice:
    def __init__(self, message: ScriptedMessage) -> None:
        self.message = message


class ScriptedResponse:
    def __init__(self, message: ScriptedMessage) -> None:
        self.choices = [ScriptedChoice(message)]


class ScriptedChatClient:
    def __init__(self, responses: Iterable[ScriptedResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> ScriptedResponse:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra chat completion call")
        return self.responses.pop(0)


def run_agent_smoke(
    *,
    runtime_factory: RuntimeFactory = AgentRuntime.create,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run deterministic agent smoke checks without an API key or real mailbox."""
    if trace_dir is not None:
        return _run_agent_smoke(runtime_factory=runtime_factory, trace_dir=Path(trace_dir))

    with TemporaryDirectory(prefix="wispera-agent-smoke-") as temp_dir:
        return _run_agent_smoke(runtime_factory=runtime_factory, trace_dir=Path(temp_dir))


def run_live_agent_smoke(
    *,
    prompt: str = "请查看最近未读重要邮件，列出最值得我关注的几封，并说明原因。",
    runtime_factory: RuntimeFactory = AgentRuntime.create,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one live LLM agent turn against the mock provider."""
    if trace_dir is not None:
        return _run_live_agent_smoke(prompt=prompt, runtime_factory=runtime_factory, trace_dir=Path(trace_dir))

    with TemporaryDirectory(prefix="wispera-live-agent-smoke-") as temp_dir:
        return _run_live_agent_smoke(prompt=prompt, runtime_factory=runtime_factory, trace_dir=Path(temp_dir))


def _run_live_agent_smoke(*, prompt: str, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "WISPERA_EMAIL_PROVIDER": os.environ.get("WISPERA_EMAIL_PROVIDER"),
        "WISPERA_STATE_DB": os.environ.get("WISPERA_STATE_DB"),
        "WISPERA_TRACE_DIR": os.environ.get("WISPERA_TRACE_DIR"),
    }
    os.environ["WISPERA_EMAIL_PROVIDER"] = "mock"
    os.environ["WISPERA_STATE_DB"] = ""
    os.environ["WISPERA_TRACE_DIR"] = str(trace_dir)

    try:
        runtime = runtime_factory()
        try:
            turn = _run_live_turn(runtime, prompt)
        finally:
            runtime.close()
    finally:
        _restore_env(previous_env)

    result = {
        "ok": turn["ok"],
        "mode": "live",
        "provider": "mock",
        "mailbox_mutation": "none_expected",
        "trace_dir": str(trace_dir),
        "prompt": prompt,
        "scenario": turn,
    }
    return result


def _run_agent_smoke(*, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "WISPERA_EMAIL_PROVIDER": os.environ.get("WISPERA_EMAIL_PROVIDER"),
        "WISPERA_STATE_DB": os.environ.get("WISPERA_STATE_DB"),
        "WISPERA_TRACE_DIR": os.environ.get("WISPERA_TRACE_DIR"),
    }
    os.environ["WISPERA_EMAIL_PROVIDER"] = "mock"
    os.environ["WISPERA_STATE_DB"] = ""
    os.environ["WISPERA_TRACE_DIR"] = str(trace_dir)

    try:
        runtime = runtime_factory()
        try:
            scenarios = [
                _scenario_read_report(runtime),
                _scenario_archive_approve(runtime),
                _scenario_star_reject(runtime),
            ]
        finally:
            runtime.close()
    finally:
        _restore_env(previous_env)

    return {
        "ok": all(item["ok"] for item in scenarios),
        "provider": "mock",
        "mailbox_mutation": "mock_only",
        "trace_dir": str(trace_dir),
        "scenarios": scenarios,
    }


def _scenario_read_report(runtime: AgentRuntime) -> dict[str, Any]:
    client = ScriptedChatClient(
        [
            ScriptedResponse(
                ScriptedMessage(
                    tool_calls=[
                        ScriptedToolCall(
                            "call-report",
                            "email_report_important",
                            {"limit": 5, "unread_only": True},
                        )
                    ]
                )
            ),
            ScriptedResponse(ScriptedMessage("最近未读重要邮件已整理，并附上了可复核的分类原因。")),
        ]
    )
    turn = _run_scripted_turn(runtime, client, "agent-smoke-read", "请查看最近未读重要邮件")

    _require(turn["done"].get("status") == "ok", "read report turn should finish ok")
    _require(turn["done"].get("tool_calls") == 1, "read report should make one tool call")
    _require(len(client.calls) == 2, "read report should call the model twice")
    _require(
        any(message.get("role") == "tool" for message in client.calls[1]["messages"]),
        "second model call should include a tool result",
    )

    return _scenario_result(
        "read_report",
        turn,
        client,
        "read tool completed and returned a final assistant answer",
    )


def _scenario_archive_approve(runtime: AgentRuntime) -> dict[str, Any]:
    client = ScriptedChatClient(
        [
            ScriptedResponse(
                ScriptedMessage(
                    tool_calls=[
                        ScriptedToolCall(
                            "call-archive",
                            "email_archive",
                            {"email_id": "email-001"},
                        )
                    ]
                )
            ),
            ScriptedResponse(ScriptedMessage("should not be reached")),
        ]
    )
    turn = _run_scripted_turn(runtime, client, "agent-smoke-archive", "归档 email-001")

    _require(turn["done"].get("status") == "pending", "archive turn should stop at pending")
    _require(len(client.calls) == 1, "pending archive should not call the model again")
    pending = _single_pending(runtime, "email_archive")
    approved = runtime.approve_tool(pending["id"])
    _require(bool(approved.get("ok")), "approved archive should execute")

    detail = runtime.execute_tool_for_test(
        "email_get_detail",
        {"email_id": "email-001"},
        session_id="agent-smoke-archive",
    )
    labels = set(detail["result"]["email"]["labels"])
    _require("archived" in labels and "inbox" not in labels, "approved archive should mutate mock labels")

    result = _scenario_result(
        "archive_approve",
        turn,
        client,
        "dangerous archive stopped at pending and executed only after approval",
    )
    result["approval"] = "approved"
    return result


def _scenario_star_reject(runtime: AgentRuntime) -> dict[str, Any]:
    client = ScriptedChatClient(
        [
            ScriptedResponse(
                ScriptedMessage(
                    tool_calls=[
                        ScriptedToolCall(
                            "call-star",
                            "email_star",
                            {"email_id": "email-002", "starred": True},
                        )
                    ]
                )
            ),
            ScriptedResponse(ScriptedMessage("should not be reached")),
        ]
    )
    turn = _run_scripted_turn(runtime, client, "agent-smoke-star", "给 email-002 加星")

    _require(turn["done"].get("status") == "pending", "star turn should stop at pending")
    _require(len(client.calls) == 1, "pending star should not call the model again")
    pending = _single_pending(runtime, "email_star")
    rejected = runtime.reject_tool(pending["id"])
    _require(bool(rejected.get("ok")) and bool(rejected.get("rejected")), "reject should clear pending star")

    detail = runtime.execute_tool_for_test(
        "email_get_detail",
        {"email_id": "email-002"},
        session_id="agent-smoke-star",
    )
    _require("starred" not in set(detail["result"]["email"]["labels"]), "rejected star should not mutate labels")

    result = _scenario_result(
        "star_reject",
        turn,
        client,
        "dangerous star stopped at pending and rejection left mock mail unchanged",
    )
    result["approval"] = "rejected"
    return result


def _run_scripted_turn(
    runtime: AgentRuntime,
    client: ScriptedChatClient,
    session_id: str,
    message: str,
) -> dict[str, Any]:
    original_openai_client = agent_module._openai_client
    agent_module._openai_client = lambda: client
    try:
        events = _parse_sse_events(runtime.stream_chat(session_id, message, mode="agent"))
    finally:
        agent_module._openai_client = original_openai_client

    done_events = [event for event in events if event["event"] == "done"]
    _require(bool(done_events), "agent stream should include a done event")
    done = done_events[-1]["data"]
    trace_id = str(done.get("trace_id", ""))
    trace = runtime.trace(trace_id)
    return {
        "events": events,
        "done": done,
        "trace_id": trace_id,
        "trace_events": [item.get("event", "") for item in trace],
    }


def _run_live_turn(runtime: AgentRuntime, prompt: str) -> dict[str, Any]:
    try:
        events = _parse_sse_events(runtime.stream_chat("agent-live-smoke", prompt, mode="agent"))
    except Exception as exc:
        return {
            "name": "live_read_report",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "events": [],
            "failure_reason": "live agent smoke could not start or stream the chat turn",
        }
    done_events = [event for event in events if event["event"] == "done"]
    error_events = [event for event in events if event["event"] == "error"]
    if error_events:
        return {
            "name": "live_read_report",
            "ok": False,
            "error": error_events[-1]["data"].get("message", "unknown live smoke error"),
            "events": [event["event"] for event in events],
        }
    _require(bool(done_events), "live agent stream should include a done event")

    done = done_events[-1]["data"]
    trace_id = str(done.get("trace_id", ""))
    trace = runtime.trace(trace_id)
    trace_events = [item.get("event", "") for item in trace]
    tool_names = [
        str(item.get("payload", {}).get("tool", ""))
        for item in trace
        if item.get("event") == "tool_call"
    ]
    read_tool_names = {
        "email_report_important",
        "email_list_recent",
        "email_search",
        "email_get_detail",
        "email_classify",
        "email_list_ignored",
    }
    used_read_tool = any(name in read_tool_names for name in tool_names)
    done_status = str(done.get("status", ""))
    ok = done_status == "ok" and used_read_tool
    return {
        "name": "live_read_report",
        "ok": ok,
        "done_status": done_status,
        "tool_calls": int(done.get("tool_calls", 0)),
        "tool_names": tool_names,
        "used_read_tool": used_read_tool,
        "trace_id": trace_id,
        "trace_events": trace_events,
        "assistant_preview": str(done.get("text", ""))[:500],
        "failure_reason": "" if ok else _live_failure_reason(done_status, used_read_tool, tool_names),
    }


def _parse_sse_events(raw_events: Iterable[str]) -> list[dict[str, Any]]:
    parsed = []
    for raw in raw_events:
        event_name = ""
        payload: dict[str, Any] = {}
        for line in raw.splitlines():
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
        if event_name:
            parsed.append({"event": event_name, "data": payload})
    return parsed


def _scenario_result(
    name: str,
    turn: dict[str, Any],
    client: ScriptedChatClient,
    note: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": True,
        "done_status": turn["done"].get("status", ""),
        "tool_calls": int(turn["done"].get("tool_calls", 0)),
        "model_calls": len(client.calls),
        "trace_id": turn["trace_id"],
        "trace_events": turn["trace_events"],
        "note": note,
    }


def _single_pending(runtime: AgentRuntime, tool_name: str) -> dict[str, Any]:
    matches = [item for item in runtime.pending_tools() if item.get("tool_name") == tool_name]
    _require(len(matches) == 1, f"expected exactly one pending {tool_name}, got {len(matches)}")
    return matches[0]


def _restore_env(previous_env: dict[str, str | None]) -> None:
    for key, value in previous_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _live_failure_reason(done_status: str, used_read_tool: bool, tool_names: list[str]) -> str:
    if done_status != "ok":
        return f"expected done status ok, got {done_status}"
    if not used_read_tool:
        return f"expected at least one email read tool call, got {tool_names}"
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run agent tool-use smoke checks on mock mail.")
    parser.add_argument(
        "--trace-dir",
        default="",
        help="Optional trace output directory. Defaults to a temporary directory.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured live LLM against the mock provider. Requires OPENAI_API_KEY.",
    )
    parser.add_argument(
        "--prompt",
        default="请查看最近未读重要邮件，列出最值得我关注的几封，并说明原因。",
        help="Prompt used by --live.",
    )
    args = parser.parse_args(argv)

    if args.live:
        result = run_live_agent_smoke(prompt=args.prompt, trace_dir=args.trace_dir or None)
    else:
        result = run_agent_smoke(trace_dir=args.trace_dir or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
