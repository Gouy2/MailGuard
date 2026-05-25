"""HTTP/SSE client for the MailGuard server."""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request


class ServerClient:
    def __init__(self, base_url: str | None = None, session_id: str = "default"):
        self.base_url = (base_url or os.environ.get("MAILGUARD_SERVER_URL") or "http://127.0.0.1:8000").rstrip("/")
        self.session_id = session_id
        self.auth_token = os.environ.get("MAILGUARD_AUTH_TOKEN", "").strip()
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def clear_history(self):
        payload = {"session_id": self.session_id}
        self._post_json("/clear", payload)

    def chat_stream(self, user_text, on_chunk, on_done, on_error):
        self._cancelled = False

        def _run():
            try:
                payload = {"session_id": self.session_id, "message": user_text}
                request = urllib.request.Request(
                    f"{self.base_url}/chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers=self._headers(),
                    method="POST",
                )
                full_text = ""
                with urllib.request.urlopen(request, timeout=60) as response:
                    event = None
                    data_lines: list[str] = []
                    for raw_line in response:
                        if self._cancelled:
                            return
                        line = raw_line.decode("utf-8").rstrip("\n")
                        if not line:
                            if event and data_lines:
                                payload = json.loads("\n".join(data_lines))
                                if event == "token":
                                    full_text = payload.get("text", full_text)
                                    on_chunk(full_text)
                                elif event == "done":
                                    on_done(payload.get("text", full_text))
                                elif event == "error":
                                    on_error(payload.get("message", "server error"))
                            event = None
                            data_lines = []
                            continue
                        if line.startswith("event:"):
                            event = line.removeprefix("event:").strip()
                        elif line.startswith("data:"):
                            data_lines.append(line.removeprefix("data:").strip())
            except Exception as exc:
                on_error(str(exc))

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def health(self) -> dict:
        return self._get_json("/health")

    def tools(self) -> dict:
        return self._get_json("/tools")

    def pending_tools(self) -> dict:
        return self._get_json("/tools/pending")

    def execute_tool(self, name: str, arguments: dict | None = None) -> dict:
        return self._post_json(
            "/tools/execute",
            {
                "name": name,
                "arguments": arguments or {},
                "session_id": self.session_id,
            },
        )

    def approve_tool(self, pending_tool_call_id: str) -> dict:
        return self._post_json(
            "/tools/approve",
            {"pending_tool_call_id": pending_tool_call_id},
        )

    def reject_tool(self, pending_tool_call_id: str) -> dict:
        return self._post_json(
            "/tools/reject",
            {"pending_tool_call_id": pending_tool_call_id},
        )

    def trace(self, trace_id: str) -> dict:
        return self._get_json(f"/traces/{trace_id}")

    def _get_json(self, path: str) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers=self._headers(include_content_type=False),
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8")) from exc

    def _headers(self, *, include_content_type: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers
