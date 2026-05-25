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

    with TemporaryDirectory(prefix="mailguard-agent-smoke-") as temp_dir:
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

    with TemporaryDirectory(prefix="mailguard-live-agent-smoke-") as temp_dir:
        return _run_live_agent_smoke(prompt=prompt, runtime_factory=runtime_factory, trace_dir=Path(temp_dir))


def run_real_readonly_agent_smoke(
    *,
    prompt: str = "请只读检查最近未读邮件，列出最值得我关注的几封，并说明原因。不要修改邮箱。",
    runtime_factory: RuntimeFactory = AgentRuntime.create,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run one live LLM read-only agent turn against the configured provider."""
    if trace_dir is not None:
        return _run_real_readonly_agent_smoke(
            prompt=prompt,
            runtime_factory=runtime_factory,
            trace_dir=Path(trace_dir),
        )

    with TemporaryDirectory(prefix="mailguard-real-readonly-agent-smoke-") as temp_dir:
        return _run_real_readonly_agent_smoke(prompt=prompt, runtime_factory=runtime_factory, trace_dir=Path(temp_dir))


def run_real_pending_write_smoke(
    *,
    runtime_factory: RuntimeFactory = AgentRuntime.create,
    trace_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Verify real-provider dangerous email tools stop at pending and are rejected."""
    if trace_dir is not None:
        return _run_real_pending_write_smoke(runtime_factory=runtime_factory, trace_dir=Path(trace_dir))

    with TemporaryDirectory(prefix="mailguard-real-pending-write-smoke-") as temp_dir:
        return _run_real_pending_write_smoke(runtime_factory=runtime_factory, trace_dir=Path(temp_dir))


def _run_live_agent_smoke(*, prompt: str, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "MAILGUARD_EMAIL_PROVIDER": os.environ.get("MAILGUARD_EMAIL_PROVIDER"),
        "MAILGUARD_STATE_DB": os.environ.get("MAILGUARD_STATE_DB"),
        "MAILGUARD_TRACE_DIR": os.environ.get("MAILGUARD_TRACE_DIR"),
    }
    os.environ["MAILGUARD_EMAIL_PROVIDER"] = "mock"
    os.environ["MAILGUARD_STATE_DB"] = ""
    os.environ["MAILGUARD_TRACE_DIR"] = str(trace_dir)

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


def _run_real_readonly_agent_smoke(*, prompt: str, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "MAILGUARD_STATE_DB": os.environ.get("MAILGUARD_STATE_DB"),
        "MAILGUARD_TRACE_DIR": os.environ.get("MAILGUARD_TRACE_DIR"),
    }
    os.environ["MAILGUARD_STATE_DB"] = ""
    os.environ["MAILGUARD_TRACE_DIR"] = str(trace_dir)

    try:
        runtime = runtime_factory()
        try:
            provider_status = runtime.execute_tool_for_test(
                "email_provider_status",
                {},
                session_id="agent-real-readonly-smoke-status",
            )
            turn = _run_live_turn(
                runtime,
                prompt,
                session_id="agent-real-readonly-smoke",
                mode="agent_readonly",
            )
            turn.pop("assistant_preview", None)
        finally:
            runtime.close()
    finally:
        _restore_env(previous_env)

    provider_result = provider_status.get("result", {}) if provider_status.get("ok") else {}
    return {
        "ok": provider_status.get("ok") and turn.get("ok") and not turn.get("used_write_tool", False),
        "mode": "real_readonly",
        "provider": provider_result.get("provider", ""),
        "mailbox_mutation": "not_allowed",
        "trace_dir": str(trace_dir),
        "prompt": prompt,
        "provider_status_ok": bool(provider_status.get("ok")),
        "scenario": turn,
        "failure_reason": _real_readonly_failure_reason(provider_status, turn),
    }


def _run_real_pending_write_smoke(*, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "MAILGUARD_STATE_DB": os.environ.get("MAILGUARD_STATE_DB"),
        "MAILGUARD_TRACE_DIR": os.environ.get("MAILGUARD_TRACE_DIR"),
    }
    os.environ["MAILGUARD_STATE_DB"] = ""
    os.environ["MAILGUARD_TRACE_DIR"] = str(trace_dir)

    try:
        runtime = runtime_factory()
        try:
            provider_status = runtime.execute_tool_for_test(
                "email_provider_status",
                {},
                session_id="agent-real-pending-write-status",
            )
            target = _select_pending_write_target(runtime)
            scenarios = []
            if target.get("ok"):
                email_id = str(target["email_id"])
                scenarios = [
                    _scenario_pending_reject(
                        runtime,
                        name="mark_read_pending",
                        tool_name="email_mark_read",
                        arguments={"email_id": email_id, "is_read": True},
                        prompt="请把这封测试邮件标记为已读，等待我审批。",
                    ),
                    _scenario_pending_reject(
                        runtime,
                        name="archive_pending",
                        tool_name="email_archive",
                        arguments={"email_id": email_id},
                        prompt="请归档这封测试邮件，等待我审批。",
                    ),
                    _scenario_pending_reject(
                        runtime,
                        name="star_pending",
                        tool_name="email_star",
                        arguments={"email_id": email_id, "starred": True},
                        prompt="请给这封测试邮件加星，等待我审批。",
                    ),
                    _scenario_pending_reject(
                        runtime,
                        name="draft_pending",
                        tool_name="email_create_draft",
                        arguments={
                            "email_id": email_id,
                            "body": "MailGuard approval smoke draft. Do not send.",
                        },
                        prompt="请为这封测试邮件创建一封草稿，等待我审批。",
                    ),
                ]
            pending_after = runtime.pending_tools()
        finally:
            runtime.close()
    finally:
        _restore_env(previous_env)

    provider_result = provider_status.get("result", {}) if provider_status.get("ok") else {}
    ok = (
        bool(provider_status.get("ok"))
        and bool(target.get("ok"))
        and bool(scenarios)
        and all(item.get("ok") for item in scenarios)
        and not pending_after
    )
    return {
        "ok": ok,
        "mode": "real_pending_write",
        "provider": provider_result.get("provider", ""),
        "mailbox_mutation": "none_rejected",
        "trace_dir": str(trace_dir),
        "provider_status_ok": bool(provider_status.get("ok")),
        "target_email_id_present": bool(target.get("email_id")),
        "scenarios": scenarios,
        "pending_count_after": len(pending_after),
        "failure_reason": _real_pending_write_failure_reason(provider_status, target, scenarios, pending_after),
    }


def _run_agent_smoke(*, runtime_factory: RuntimeFactory, trace_dir: Path) -> dict[str, Any]:
    agent_module.load_server_env()
    previous_env = {
        "MAILGUARD_EMAIL_PROVIDER": os.environ.get("MAILGUARD_EMAIL_PROVIDER"),
        "MAILGUARD_STATE_DB": os.environ.get("MAILGUARD_STATE_DB"),
        "MAILGUARD_TRACE_DIR": os.environ.get("MAILGUARD_TRACE_DIR"),
    }
    os.environ["MAILGUARD_EMAIL_PROVIDER"] = "mock"
    os.environ["MAILGUARD_STATE_DB"] = ""
    os.environ["MAILGUARD_TRACE_DIR"] = str(trace_dir)

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


def _select_pending_write_target(runtime: AgentRuntime) -> dict[str, Any]:
    recent = runtime.execute_tool_for_test(
        "email_list_recent",
        {"limit": 1, "unread_only": False},
        session_id="agent-real-pending-write-target",
    )
    if not recent.get("ok"):
        return {
            "ok": False,
            "error": recent.get("error", "email_list_recent failed"),
        }
    emails = recent.get("result", {}).get("emails", [])
    if not emails:
        return {
            "ok": False,
            "error": "no recent email available for pending write smoke",
        }
    email_id = str(emails[0].get("id", "")).strip()
    if not email_id:
        return {
            "ok": False,
            "error": "recent email did not include an id",
        }
    return {
        "ok": True,
        "email_id": email_id,
    }


def _scenario_pending_reject(
    runtime: AgentRuntime,
    *,
    name: str,
    tool_name: str,
    arguments: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    client = ScriptedChatClient(
        [
            ScriptedResponse(
                ScriptedMessage(
                    tool_calls=[
                        ScriptedToolCall(
                            f"call-{name}",
                            tool_name,
                            arguments,
                        )
                    ]
                )
            ),
            ScriptedResponse(ScriptedMessage("should not be reached")),
        ]
    )
    turn = _run_scripted_turn(runtime, client, f"agent-real-{name}", prompt)
    pending_matches = [item for item in runtime.pending_tools() if item.get("tool_name") == tool_name]
    rejected: dict[str, Any] = {}
    if pending_matches:
        rejected = runtime.reject_tool(pending_matches[0]["id"])
    trace_events = turn.get("trace_events", [])
    ok = (
        turn["done"].get("status") == "pending"
        and len(client.calls) == 1
        and len(pending_matches) == 1
        and bool(rejected.get("ok"))
        and bool(rejected.get("rejected"))
        and "tool_pending" in trace_events
    )
    return {
        "name": name,
        "ok": ok,
        "tool": tool_name,
        "done_status": turn["done"].get("status", ""),
        "tool_calls": int(turn["done"].get("tool_calls", 0)),
        "model_calls": len(client.calls),
        "pending_created": bool(pending_matches),
        "pending_tool_call_id_present": bool(pending_matches),
        "rejected": bool(rejected.get("rejected")),
        "trace_id": turn["trace_id"],
        "trace_events": trace_events,
        "failure_reason": "" if ok else _pending_reject_failure_reason(turn, pending_matches, rejected, client),
    }


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


def _run_live_turn(
    runtime: AgentRuntime,
    prompt: str,
    *,
    session_id: str = "agent-live-smoke",
    mode: str = "agent",
) -> dict[str, Any]:
    try:
        events = _parse_sse_events(runtime.stream_chat(session_id, prompt, mode=mode))
    except Exception as exc:
        return {
            "name": "live_read_report" if mode == "agent" else "real_readonly_read_report",
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "events": [],
            "failure_reason": "live agent smoke could not start or stream the chat turn",
        }
    done_events = [event for event in events if event["event"] == "done"]
    error_events = [event for event in events if event["event"] == "error"]
    if error_events:
        return {
            "name": "live_read_report" if mode == "agent" else "real_readonly_read_report",
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
        "email_provider_status",
        "email_list_mailboxes",
        "email_report_important",
        "email_list_recent",
        "email_search",
        "email_get_detail",
        "email_classify",
        "email_list_ignored",
        "email_get_preferences",
    }
    write_tool_names = {
        "email_archive",
        "email_mark_read",
        "email_star",
        "email_create_draft",
        "email_add_preference",
        "email_remove_preference",
        "email_set_preference",
        "email_notification_mark_read",
    }
    used_read_tool = any(name in read_tool_names for name in tool_names)
    used_write_tool = any(name in write_tool_names for name in tool_names)
    done_status = str(done.get("status", ""))
    ok = done_status == "ok" and used_read_tool and (mode != "agent_readonly" or not used_write_tool)
    return {
        "name": "live_read_report" if mode == "agent" else "real_readonly_read_report",
        "ok": ok,
        "mode": mode,
        "done_status": done_status,
        "tool_calls": int(done.get("tool_calls", 0)),
        "tool_names": tool_names,
        "used_read_tool": used_read_tool,
        "used_write_tool": used_write_tool,
        "trace_id": trace_id,
        "trace_events": trace_events,
        "assistant_preview": str(done.get("text", ""))[:500],
        "failure_reason": "" if ok else _live_failure_reason(done_status, used_read_tool, tool_names, used_write_tool),
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


def _live_failure_reason(
    done_status: str,
    used_read_tool: bool,
    tool_names: list[str],
    used_write_tool: bool = False,
) -> str:
    if done_status != "ok":
        return f"expected done status ok, got {done_status}"
    if not used_read_tool:
        return f"expected at least one email read tool call, got {tool_names}"
    if used_write_tool:
        return f"read-only smoke should not use write tools, got {tool_names}"
    return ""


def _real_readonly_failure_reason(provider_status: dict[str, Any], turn: dict[str, Any]) -> str:
    if not provider_status.get("ok"):
        return str(provider_status.get("error", "provider status failed"))
    if turn.get("failure_reason"):
        return str(turn["failure_reason"])
    if turn.get("used_write_tool"):
        return f"read-only smoke used write tools: {turn.get('tool_names', [])}"
    return ""


def _pending_reject_failure_reason(
    turn: dict[str, Any],
    pending_matches: list[dict[str, Any]],
    rejected: dict[str, Any],
    client: ScriptedChatClient,
) -> str:
    if turn["done"].get("status") != "pending":
        return f"expected pending status, got {turn['done'].get('status', '')}"
    if len(client.calls) != 1:
        return f"expected one model call before pending, got {len(client.calls)}"
    if len(pending_matches) != 1:
        return f"expected exactly one pending item, got {len(pending_matches)}"
    if not rejected.get("ok") or not rejected.get("rejected"):
        return f"pending rejection failed: {rejected.get('error', rejected)}"
    if "tool_pending" not in turn.get("trace_events", []):
        return "trace did not include tool_pending"
    return ""


def _real_pending_write_failure_reason(
    provider_status: dict[str, Any],
    target: dict[str, Any],
    scenarios: list[dict[str, Any]],
    pending_after: list[dict[str, Any]],
) -> str:
    if not provider_status.get("ok"):
        return str(provider_status.get("error", "provider status failed"))
    if not target.get("ok"):
        return str(target.get("error", "target selection failed"))
    failed = [item for item in scenarios if not item.get("ok")]
    if failed:
        return "; ".join(f"{item.get('name', '')}: {item.get('failure_reason', '')}" for item in failed)
    if pending_after:
        return f"expected no pending calls after rejection, got {len(pending_after)}"
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
        "--real-readonly",
        action="store_true",
        help="Call the configured live LLM against the configured provider in agent_readonly mode.",
    )
    parser.add_argument(
        "--real-pending-write",
        action="store_true",
        help="Use the configured provider to verify dangerous email writes stop at pending, then reject them.",
    )
    parser.add_argument(
        "--prompt",
        default="请查看最近未读重要邮件，列出最值得我关注的几封，并说明原因。",
        help="Prompt used by --live.",
    )
    args = parser.parse_args(argv)

    if args.real_pending_write:
        result = run_real_pending_write_smoke(trace_dir=args.trace_dir or None)
    elif args.real_readonly:
        result = run_real_readonly_agent_smoke(prompt=args.prompt, trace_dir=args.trace_dir or None)
    elif args.live:
        result = run_live_agent_smoke(prompt=args.prompt, trace_dir=args.trace_dir or None)
    else:
        result = run_agent_smoke(trace_dir=args.trace_dir or None)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
