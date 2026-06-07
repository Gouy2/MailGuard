"""Regression tests for MailGuard auth provider tools."""

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
    FakeHttpTransport,
    FakeImapClient,
    FakeOpenAIClient,
    FakeToolCall,
    _raw_imap_message,
)

class AuthAndDevToolTests(unittest.TestCase):
    def test_configured_auth_token_reads_environment(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_AUTH_TOKEN": "test-token"}):
            self.assertEqual("test-token", configured_auth_token())

    def test_auth_dependency_uses_fastapi_request_object(self) -> None:
        try:
            from fastapi import Depends, FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi is not installed in this test environment")

        app = FastAPI()

        @app.post("/protected", dependencies=[Depends(require_api_token)])
        def protected(payload: dict):
            return {"ok": True, "payload": payload}

        with patch.dict(os.environ, {"MAILGUARD_AUTH_TOKEN": ""}):
            response = TestClient(app).post("/protected", json={"message": "hello"})

        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "payload": {"message": "hello"}}, response.json())

    def test_dev_tools_can_be_enabled_and_reject_sensitive_paths(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_STATE_DB": "", "MAILGUARD_DEV_TOOLS": "1"}):
            runtime = AgentRuntime.create()
            try:
                names = {tool["name"] for tool in runtime.tool_inventory()}
                self.assertIn("read_text_file", names)
                self.assertIn("run_shell_command", names)

                env_read = runtime.execute_tool_for_test("read_text_file", {"path": "server/.env.example"})
                self.assertFalse(env_read["ok"])
                self.assertIn("not readable", env_read["error"])

                shell = runtime.execute_tool_for_test("run_shell_command", {"command": "pwd && whoami"})
                self.assertFalse(shell["ok"])
                self.assertEqual("policy_error", shell["error_type"])
            finally:
                runtime.close()

    def test_provider_factory_defaults_to_mock(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_EMAIL_PROVIDER": ""}):
            self.assertIsInstance(create_email_provider(), MockEmailProvider)

    def test_provider_factory_rejects_unknown_provider(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_EMAIL_PROVIDER": "imap"}):
            with self.assertRaises(RuntimeError):
                create_email_provider()
