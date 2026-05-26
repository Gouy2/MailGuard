"""Regression tests for MailGuard email classification."""

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
from server.app.email_tools import classify_email
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

class EmailClassifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MockEmailProvider()

    def test_reference_classifications(self) -> None:
        cases = {
            "email-001": ("action_required", "high"),
            "email-004": ("newsletter", "low"),
            "email-005": ("promotion", "low"),
            "email-007": ("security", "high"),
        }

        for email_id, expected in cases.items():
            with self.subTest(email_id=email_id):
                decision = classify_email(self.provider.get_detail(email_id))
                actual = (decision["category"], decision["importance"])
                self.assertEqual(expected, actual)
                self.assertTrue(decision["reasons"])

class LLMEmailClassifierParsingTests(unittest.TestCase):
    def test_parse_json_object_accepts_fenced_json(self) -> None:
        raw = '```json\n{"category":"security","importance":"high"}\n```'
        self.assertEqual({"category": "security", "importance": "high"}, _parse_json_object(raw))

    def test_parse_json_object_extracts_embedded_json(self) -> None:
        raw = 'result: {"category":"newsletter","importance":"low"}'
        self.assertEqual({"category": "newsletter", "importance": "low"}, _parse_json_object(raw))

    def test_normalize_decision_sets_reportable_flags(self) -> None:
        decision = _normalize_decision(
            "email-test",
            {
                "category": "Action_Required",
                "importance": "HIGH",
                "suggested_action": "Review",
                "reasons": ["deadline"],
            },
            "{}",
        )

        self.assertEqual("email-test", decision["email_id"])
        self.assertEqual("action_required", decision["category"])
        self.assertEqual("high", decision["importance"])
        self.assertEqual("review", decision["suggested_action"])
        self.assertTrue(decision["is_reportable"])
        self.assertFalse(decision["is_ignored"])

    def test_normalize_decision_rejects_invalid_category(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_decision(
                "email-test",
                {
                    "category": "urgent",
                    "importance": "high",
                    "suggested_action": "review",
                    "reasons": ["unsupported label"],
                },
                "{}",
            )

class RedactionTests(unittest.TestCase):
    def test_trace_redacts_user_and_assistant_text(self) -> None:
        redacted = redact_for_trace(
            {
                "user_message": "show my real mailbox",
                "assistant_text": "real mailbox summary",
                "tool": "email_report_important",
            }
        )

        self.assertTrue(redacted["user_message"]["redacted"])
        self.assertTrue(redacted["assistant_text"]["redacted"])
        self.assertEqual("email_report_important", redacted["tool"])

    def test_trace_logger_rejects_path_like_trace_ids(self) -> None:
        with TemporaryDirectory() as temp_dir:
            tracer = TraceLogger(trace_dir=Path(temp_dir))
            trace_id = tracer.start_turn("trace-test", "agent", "hello")
            self.assertTrue(tracer.read_trace(trace_id))
            self.assertEqual([], tracer.read_trace("../outside"))
