"""Regression tests for MailGuard real eval helpers."""

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

class RealEmailEvalTests(unittest.TestCase):
    def test_real_label_evaluation_tracks_mismatches(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "imap-1": {
                    "label": "important",
                    "predicted_reportable": False,
                    "predicted_ignored": True,
                    "predicted_category": "newsletter",
                    "predicted_importance": "low",
                },
                "imap-2": {
                    "label": "ignore",
                    "predicted_reportable": False,
                    "predicted_ignored": True,
                    "predicted_category": "promotion",
                    "predicted_importance": "low",
                },
            },
        }

        result = evaluate_real_labels(label_data)

        self.assertEqual(2, result["sample_count"])
        self.assertEqual({"ignore": 1, "important": 1}, result["label_counts"])
        self.assertEqual(0.0, result["metrics"]["important_recall"])
        self.assertEqual(1, result["metrics"]["false_negative_count"])
        self.assertEqual(["imap-1"], [item["email_id"] for item in result["mismatches"]])

class RealProposalEvalTests(unittest.TestCase):
    def test_real_proposal_label_evaluation_tracks_false_positives(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "proposal-1": {
                    "email_id": "imap-1",
                    "label": "archive",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Newsletter",
                },
                "proposal-2": {
                    "email_id": "imap-2",
                    "label": "keep",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Important invoice",
                },
                "proposal-3": {
                    "email_id": "imap-3",
                    "label": "unsure",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Ambiguous",
                },
            },
        }

        result = evaluate_real_proposal_labels(label_data)

        self.assertEqual(3, result["sample_count"])
        self.assertEqual(2, result["decisive_count"])
        self.assertEqual({"archive": 1, "keep": 1, "unsure": 1}, result["label_counts"])
        self.assertEqual(0.5, result["metrics"]["archive_acceptance_precision"])
        self.assertEqual(1, result["metrics"]["false_positive_count"])
        self.assertEqual(["proposal-2"], [item["proposal_id"] for item in result["false_positive_proposals"]])
