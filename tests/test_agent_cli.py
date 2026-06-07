"""Regression tests for MailGuard agent cli."""

from __future__ import annotations

import unittest
import os
import json
from io import StringIO
from email.message import EmailMessage as OutboundEmailMessage
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from server.agent_cli import AgentHttpClient, run_cli as run_agent_cli
from server.email_cli import run_cli
from server.agent_smoke import run_agent_smoke, run_real_pending_write_smoke
from server.app.agent import _state_db_path
from server.app.agent import AgentRuntime
from server.app.auth import configured_auth_token, require_api_token
from server.app.email_eval import evaluate_email_classifier
from server.app.email_provider import MockEmailProvider
from server.app.email_proposals import approve_action_proposal, execute_approved_action_proposals
from server.app.email_classifier import classify_email
from server.app.llm_email_classifier import _normalize_decision, _parse_json_object
from server.app.memory import MemoryStore
from server.app.proposal_eval import evaluate_archive_proposal_policy
from server.app.provider_factory import create_email_provider
from server.app.qq_imap_provider import QQImapConfig, QQImapProvider
from server.app.redaction import redact_for_trace
from server.app.real_proposal_eval import (
    evaluate_real_proposal_labels,
    load_real_proposal_labels,
    save_real_proposal_label,
)
from server.app.tracer import TraceLogger
from server.app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
from server.app.sqlite_state import SQLiteStateStore
from tests.fakes import (
    FakeChatMessage,
    FakeChatResponse,
    FakeCliRuntime,
    FakeHttpResponse,
    FakeHttpTransport,
    FakeImapClient,
    FakeOpenAIClient,
    FakeToolCall,
    _raw_imap_message,
)

class AgentCliTests(unittest.TestCase):
    def test_chat_parses_sse_and_prints_pending_hint(self) -> None:
        transport = FakeHttpTransport(
            [
                FakeHttpResponse(
                    lines=[
                        b'event: status\n',
                        b'data: {"trace_id":"trace-1","session_id":"cli-test","mode":"agent"}\n',
                        b'\n',
                        b'event: token\n',
                        b'data: {"trace_id":"trace-1","delta":"needs approval","text":"needs approval"}\n',
                        b'\n',
                        b'event: done\n',
                        b'data: {"trace_id":"trace-1","text":"needs approval","tool_calls":1,"llm_calls":1,"elapsed_ms":1234.5,"status":"pending"}\n',
                        b'\n',
                    ]
                )
            ]
        )
        client = AgentHttpClient(
            base_url="http://server.test",
            session_id="cli-test",
            transport=transport,
            auth_token="token-1",
        )
        stdout = StringIO()

        exit_code = run_agent_cli(
            ["chat", "请", "归档", "email-001"],
            client=client,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        request, timeout = transport.requests[0]
        self.assertEqual("http://server.test/chat", request.full_url)
        self.assertEqual("POST", request.get_method())
        self.assertEqual("Bearer token-1", request.get_header("Authorization"))
        self.assertEqual(30.0, timeout)
        self.assertEqual(
            {"session_id": "cli-test", "message": "请 归档 email-001"},
            json.loads(request.data.decode("utf-8")),
        )
        output = stdout.getvalue()
        self.assertIn("Status: pending", output)
        self.assertIn("Trace ID: trace-1", output)
        self.assertIn("Elapsed: 1.23s", output)
        self.assertIn("LLM calls: 1", output)
        self.assertIn("Tool calls: 1", output)
        self.assertIn("agent_cli.py pending", output)

    def test_chat_readonly_uses_readonly_endpoint(self) -> None:
        transport = FakeHttpTransport(
            [
                FakeHttpResponse(
                    lines=[
                        b'event: status\n',
                        b'data: {"trace_id":"trace-ro","session_id":"cli-test","mode":"agent_readonly"}\n',
                        b'\n',
                        b'event: done\n',
                        b'data: {"trace_id":"trace-ro","text":"read only","tool_calls":1,"llm_calls":2,"elapsed_ms":900,"status":"ok"}\n',
                        b'\n',
                    ]
                )
            ]
        )
        client = AgentHttpClient(base_url="http://server.test", session_id="cli-test", transport=transport)
        stdout = StringIO()

        exit_code = run_agent_cli(
            ["chat", "--readonly", "请检查未读重要邮件"],
            client=client,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        request, _timeout = transport.requests[0]
        self.assertEqual("http://server.test/chat/readonly", request.full_url)
        output = stdout.getvalue()
        self.assertIn("Mode: readonly", output)
        self.assertIn("Trace ID: trace-ro", output)
        self.assertIn("Elapsed: 900ms", output)
        self.assertIn("LLM calls: 2", output)

    def test_pending_approve_reject_and_trace_use_expected_endpoints(self) -> None:
        transport = FakeHttpTransport(
            [
                FakeHttpResponse(
                    body={
                        "status": "ok",
                        "pending": [
                            {
                                "id": "pending-1",
                                "tool_name": "email_archive",
                                "arguments": {"email_id": "email-001"},
                                "session_id": "cli-test",
                                "trace_id": "trace-1",
                                "reason": "dangerous tool requires explicit approval",
                            }
                        ],
                    }
                ),
                FakeHttpResponse(
                    body={
                        "status": "ok",
                        "ok": True,
                        "tool": "email_archive",
                        "result": {"action": "archive", "result": {"email_id": "email-001"}},
                    }
                ),
                FakeHttpResponse(
                    body={
                        "status": "ok",
                        "ok": True,
                        "rejected": True,
                        "pending_tool_call_id": "pending-2",
                        "tool": "email_star",
                    }
                ),
                FakeHttpResponse(
                    body={
                        "status": "ok",
                        "trace_id": "trace-1",
                        "events": [
                            {
                                "event": "turn_start",
                                "payload": {"session_id": "cli-test", "mode": "agent"},
                            },
                            {
                                "event": "tool_call",
                                "payload": {"tool": "email_archive", "arguments": {"email_id": "email-001"}},
                            },
                            {
                                "event": "llm_call_end",
                                "payload": {"round": 1, "elapsed_ms": 1200, "tool_calls": 1},
                            },
                            {
                                "event": "tool_result",
                                "payload": {
                                    "tool": "email_archive",
                                    "result": {"ok": True, "latency_ms": 280, "tool": "email_archive"},
                                },
                            },
                            {
                                "event": "tool_pending",
                                "payload": {
                                    "tool": "email_archive",
                                    "pending_tool_call_id": "pending-1",
                                },
                            },
                            {
                                "event": "tool_approval",
                                "payload": {"decision": "approved", "pending_tool_call_id": "pending-1"},
                            },
                            {
                                "event": "turn_end",
                                "payload": {"status": "ok", "elapsed_ms": 1500, "llm_calls": 1, "tool_calls": 1},
                            },
                        ],
                    }
                ),
            ]
        )
        client = AgentHttpClient(base_url="http://server.test", session_id="cli-test", transport=transport)

        pending_out = StringIO()
        self.assertEqual(
            0,
            run_agent_cli(["pending"], client=client, stdout=pending_out, stderr=StringIO()),
        )
        approve_out = StringIO()
        self.assertEqual(
            0,
            run_agent_cli(["approve", "pending-1"], client=client, stdout=approve_out, stderr=StringIO()),
        )
        reject_out = StringIO()
        self.assertEqual(
            0,
            run_agent_cli(["reject", "pending-2"], client=client, stdout=reject_out, stderr=StringIO()),
        )
        trace_out = StringIO()
        self.assertEqual(
            0,
            run_agent_cli(["trace", "trace-1"], client=client, stdout=trace_out, stderr=StringIO()),
        )

        urls = [request.full_url for request, _timeout in transport.requests]
        self.assertEqual(
            [
                "http://server.test/tools/pending",
                "http://server.test/tools/approve",
                "http://server.test/tools/reject",
                "http://server.test/traces/trace-1",
            ],
            urls,
        )
        approve_payload = json.loads(transport.requests[1][0].data.decode("utf-8"))
        reject_payload = json.loads(transport.requests[2][0].data.decode("utf-8"))
        self.assertEqual({"pending_tool_call_id": "pending-1"}, approve_payload)
        self.assertEqual({"pending_tool_call_id": "pending-2"}, reject_payload)

        self.assertIn("Pending: 1", pending_out.getvalue())
        self.assertIn("pending-1 [email_archive]", pending_out.getvalue())
        self.assertIn("email-001", pending_out.getvalue())
        self.assertIn("Approved: ok", approve_out.getvalue())
        self.assertIn("Rejected: ok", reject_out.getvalue())
        trace_text = trace_out.getvalue()
        self.assertIn("Trace: trace-1", trace_text)
        self.assertIn("LLM elapsed: 1.20s across 1 call(s)", trace_text)
        self.assertIn("Tool elapsed: 280ms across 1 timed call(s)", trace_text)
        self.assertIn("Slowest tool: email_archive 280ms", trace_text)
        self.assertIn("tool=email_archive", trace_text)
        self.assertIn("status=ok elapsed=280ms", trace_text)
        self.assertIn("elapsed=1.20s", trace_text)
        self.assertIn("llm_calls=1", trace_text)
        self.assertIn("pending_id=pending-1", trace_text)
        self.assertNotIn('"arguments"', trace_text)

    def test_json_output_returns_raw_response(self) -> None:
        transport = FakeHttpTransport([FakeHttpResponse(body={"service": "mailguard-server", "status": "ok"})])
        client = AgentHttpClient(base_url="http://server.test", transport=transport)
        stdout = StringIO()

        exit_code = run_agent_cli(["--json", "health"], client=client, stdout=stdout, stderr=StringIO())

        self.assertEqual(0, exit_code)
        self.assertEqual({"service": "mailguard-server", "status": "ok"}, json.loads(stdout.getvalue()))

    def test_client_uses_auth_token_from_environment(self) -> None:
        transport = FakeHttpTransport([FakeHttpResponse(body={"service": "mailguard-server", "status": "ok"})])
        with patch.dict(os.environ, {"MAILGUARD_AUTH_TOKEN": "env-token"}):
            client = AgentHttpClient(base_url="http://server.test", transport=transport)
            client.health()

        request, _timeout = transport.requests[0]
        self.assertEqual("Bearer env-token", request.get_header("Authorization"))
