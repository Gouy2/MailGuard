"""Small HTTP CLI for the Wispera agent approval and trace loop."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence, TextIO

if __package__ in {None, ""}:  # pragma: no cover - runtime path bootstrap for script execution
    current_file = Path(__file__).resolve()
    sys.path.insert(0, str(current_file.parent))
    sys.path.insert(0, str(current_file.parent.parent))

try:
    from app.runtime_env import load_server_env
except ModuleNotFoundError as exc:  # pragma: no cover - used when imported from repo root
    if exc.name != "app":
        raise
    from server.app.runtime_env import load_server_env


DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SESSION_ID = "agent-cli"


class HttpTransport(Protocol):
    def open(self, request: urllib.request.Request, timeout: float): ...


class UrlLibTransport:
    def open(self, request: urllib.request.Request, timeout: float):
        return urllib.request.urlopen(request, timeout=timeout)


class AgentHttpClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        session_id: str = DEFAULT_SESSION_ID,
        auth_token: str | None = None,
        timeout: float = 30.0,
        transport: HttpTransport | None = None,
    ) -> None:
        load_server_env()
        self.base_url = (base_url or os.environ.get("WISPERA_SERVER_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.session_id = session_id
        self.auth_token = (auth_token if auth_token is not None else os.environ.get("WISPERA_AUTH_TOKEN", "")).strip()
        self.timeout = timeout
        self.transport = transport or UrlLibTransport()

    def health(self) -> dict[str, Any]:
        return self._get_json("/health")

    def chat(self, message: str) -> dict[str, Any]:
        payload = {"session_id": self.session_id, "message": message}
        request = self._request("/chat", method="POST", payload=payload)
        events: list[dict[str, Any]] = []
        try:
            with self.transport.open(request, timeout=self.timeout) as response:
                events = parse_sse_response(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_read_http_error(exc)) from exc

        done = next((event["data"] for event in reversed(events) if event["event"] == "done"), {})
        error = next((event["data"] for event in reversed(events) if event["event"] == "error"), {})
        status = str(done.get("status") or ("error" if error else "ok"))
        return {
            "ok": not error,
            "status": status,
            "trace_id": done.get("trace_id") or error.get("trace_id") or _first_trace_id(events),
            "text": done.get("text", ""),
            "tool_calls": done.get("tool_calls", 0),
            "events": events,
            "error": error.get("message", ""),
        }

    def pending(self) -> dict[str, Any]:
        return self._get_json("/tools/pending")

    def approve(self, pending_tool_call_id: str) -> dict[str, Any]:
        return self._post_json("/tools/approve", {"pending_tool_call_id": pending_tool_call_id})

    def reject(self, pending_tool_call_id: str) -> dict[str, Any]:
        return self._post_json("/tools/reject", {"pending_tool_call_id": pending_tool_call_id})

    def trace(self, trace_id: str) -> dict[str, Any]:
        return self._get_json(f"/traces/{trace_id}")

    def _get_json(self, path: str) -> dict[str, Any]:
        request = self._request(path, method="GET")
        try:
            with self.transport.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_read_http_error(exc)) from exc

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = self._request(path, method="POST", payload=payload)
        try:
            with self.transport.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(_read_http_error(exc)) from exc

    def _request(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, Any] | None = None,
    ) -> urllib.request.Request:
        headers = self._headers(include_content_type=payload is not None)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        return urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )

    def _headers(self, *, include_content_type: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Exercise the Wispera HTTP approval and trace loop.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=f"Server base URL. Defaults to WISPERA_SERVER_URL or {DEFAULT_BASE_URL}.",
    )
    parser.add_argument(
        "--session-id",
        default=DEFAULT_SESSION_ID,
        help=f"Chat session id. Defaults to {DEFAULT_SESSION_ID}.",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds.")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print raw JSON.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Check server health.")
    health.set_defaults(func=_cmd_health, display_command="health")

    chat = subparsers.add_parser("chat", help="Send one agent chat turn.")
    chat.add_argument("message", nargs="+")
    chat.set_defaults(func=_cmd_chat, display_command="chat")

    pending = subparsers.add_parser("pending", help="List pending dangerous tool calls.")
    pending.set_defaults(func=_cmd_pending, display_command="pending")

    approve = subparsers.add_parser("approve", help="Approve one pending tool call.")
    approve.add_argument("pending_tool_call_id")
    approve.set_defaults(func=_cmd_approve, display_command="approve")

    reject = subparsers.add_parser("reject", help="Reject one pending tool call.")
    reject.add_argument("pending_tool_call_id")
    reject.set_defaults(func=_cmd_reject, display_command="reject")

    trace = subparsers.add_parser("trace", help="Show a compact trace event summary.")
    trace.add_argument("trace_id")
    trace.set_defaults(func=_cmd_trace, display_command="trace")

    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    client: AgentHttpClient | None = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    http = client or AgentHttpClient(
        base_url=args.base_url,
        session_id=args.session_id,
        timeout=args.timeout,
    )
    try:
        result = args.func(args, http)
        if args.json_output:
            print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)
        else:
            _print_human(args, result, stdout=stdout, stderr=stderr)
        return 0 if _is_success(result) else 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"Error: {type(exc).__name__}: {exc}", file=stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv)


def parse_sse_response(response: Iterable[bytes]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = ""
    data_lines: list[str] = []

    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if event_name and data_lines:
                events.append(_sse_event(event_name, data_lines))
            event_name = ""
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())

    if event_name and data_lines:
        events.append(_sse_event(event_name, data_lines))
    return events


def _sse_event(event_name: str, data_lines: list[str]) -> dict[str, Any]:
    raw_data = "\n".join(data_lines)
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        data = {"raw": raw_data}
    return {"event": event_name, "data": data}


def _cmd_health(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.health()


def _cmd_chat(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.chat(" ".join(args.message))


def _cmd_pending(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.pending()


def _cmd_approve(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.approve(args.pending_tool_call_id)


def _cmd_reject(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.reject(args.pending_tool_call_id)


def _cmd_trace(args: argparse.Namespace, client: AgentHttpClient) -> dict[str, Any]:
    return client.trace(args.trace_id)


def _print_human(args: argparse.Namespace, result: dict[str, Any], *, stdout: TextIO, stderr: TextIO) -> None:
    if not _is_success(result):
        print(f"Error: {result.get('error') or result.get('detail') or 'request failed'}", file=stderr)
        if result.get("trace_id"):
            print(f"Trace ID: {result['trace_id']}", file=stderr)
        return

    command = args.display_command
    if command == "health":
        _print_health(result, stdout)
    elif command == "chat":
        _print_chat(result, stdout)
    elif command == "pending":
        _print_pending(result, stdout)
    elif command == "approve":
        _print_decision("Approved", result, stdout)
    elif command == "reject":
        _print_decision("Rejected", result, stdout)
    elif command == "trace":
        _print_trace(result, stdout)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2), file=stdout)


def _print_health(result: dict[str, Any], out: TextIO) -> None:
    print(f"Service: {result.get('service', '')}", file=out)
    print(f"Status: {result.get('status', '')}", file=out)
    tools = result.get("tools") or []
    if tools:
        print(f"Tools: {len(tools)}", file=out)


def _print_chat(result: dict[str, Any], out: TextIO) -> None:
    print(f"Status: {result.get('status', '')}", file=out)
    print(f"Trace ID: {result.get('trace_id', '')}", file=out)
    print(f"Tool calls: {result.get('tool_calls', 0)}", file=out)
    text = str(result.get("text", "")).strip()
    if text:
        print("Assistant:", file=out)
        print(text, file=out)
    if result.get("status") == "pending":
        print("Next: run `python agent_cli.py pending`, then approve or reject the pending id.", file=out)


def _print_pending(result: dict[str, Any], out: TextIO) -> None:
    pending = result.get("pending", [])
    print(f"Pending: {len(pending)}", file=out)
    for index, item in enumerate(pending, start=1):
        print(f"{index}. {item.get('id', '')} [{item.get('tool_name', '')}]", file=out)
        print(f"   Session: {item.get('session_id', '')}", file=out)
        if item.get("trace_id"):
            print(f"   Trace: {item.get('trace_id', '')}", file=out)
        arguments = item.get("arguments") or {}
        if arguments:
            print(f"   Arguments: {_compact_json(arguments)}", file=out)
        if item.get("reason"):
            print(f"   Reason: {item.get('reason')}", file=out)


def _print_decision(prefix: str, result: dict[str, Any], out: TextIO) -> None:
    status = result.get("status") or ("ok" if result.get("ok") else "error")
    print(f"{prefix}: {status}", file=out)
    if result.get("tool"):
        print(f"Tool: {result.get('tool')}", file=out)
    if result.get("pending_tool_call_id"):
        print(f"Pending ID: {result.get('pending_tool_call_id')}", file=out)
    payload = result.get("result")
    if payload is not None:
        print(f"Result: {_compact_json(payload)}", file=out)


def _print_trace(result: dict[str, Any], out: TextIO) -> None:
    trace_id = str(result.get("trace_id", ""))
    events = result.get("events") or []
    print(f"Trace: {trace_id}", file=out)
    print(f"Status: {result.get('status', '')}", file=out)
    print(f"Events: {len(events)}", file=out)
    for index, item in enumerate(events, start=1):
        event = item.get("event", "")
        payload = item.get("payload") or {}
        line = f"{index}. {event}"
        detail = _trace_detail(event, payload)
        if detail:
            line = f"{line} - {detail}"
        print(line, file=out)


def _trace_detail(event: str, payload: dict[str, Any]) -> str:
    if event == "turn_start":
        return f"session={payload.get('session_id', '')} mode={payload.get('mode', '')}"
    if event == "tool_call":
        return f"tool={payload.get('tool', '')}"
    if event == "tool_pending":
        return f"tool={payload.get('tool', '')} pending_id={payload.get('pending_tool_call_id', '')}"
    if event == "tool_result":
        result = payload.get("result") or {}
        status = "ok" if result.get("ok") else "pending" if result.get("requires_approval") else "error"
        return f"tool={payload.get('tool', '')} status={status}"
    if event == "tool_approval":
        return f"decision={payload.get('decision', '')} pending_id={payload.get('pending_tool_call_id', '')}"
    if event == "turn_end":
        return f"status={payload.get('status', '')} tool_calls={payload.get('tool_calls', 0)}"
    return ""


def _compact_json(value: Any, *, limit: int = 300) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _is_success(result: dict[str, Any]) -> bool:
    if result.get("status") == "not_found":
        return False
    if result.get("ok") is False:
        return False
    return not bool(result.get("error"))


def _first_trace_id(events: list[dict[str, Any]]) -> str:
    for event in events:
        trace_id = event.get("data", {}).get("trace_id")
        if trace_id:
            return str(trace_id)
    return ""


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    return body or f"HTTP {exc.code}: {exc.reason}"


if __name__ == "__main__":
    raise SystemExit(main())
