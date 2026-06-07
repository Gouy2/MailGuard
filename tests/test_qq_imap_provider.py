"""Regression tests for MailGuard qq imap provider."""

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

class QQImapProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = {
            "1": {"raw": _raw_imap_message("Newsletter", "Weekly digest"), "flags": {"\\Seen"}},
            "2": {"raw": _raw_imap_message(html=True), "flags": set()},
        }
        self.client = FakeImapClient(self.messages)
        self.provider = QQImapProvider(
            QQImapConfig(
                email_address="user@foxmail.com",
                auth_code="auth-code",
                archive_mailbox="Archive",
                drafts_mailbox="Drafts",
            ),
            client_factory=lambda: self.client,
        )

    def test_list_recent_maps_imap_messages_to_email_messages(self) -> None:
        emails = self.provider.list_recent(limit=2)

        self.assertEqual(["imap-2", "imap-1"], [item.id for item in emails])
        self.assertEqual("maya.chen@example.com", emails[0].from_email)
        self.assertEqual(["alex@example.com"], emails[0].to)
        self.assertIn("Please review", emails[0].body)
        self.assertNotIn("<script>", emails[0].body)
        self.assertFalse(emails[0].is_read)
        self.assertTrue(emails[1].is_read)
        self.assertEqual(("INBOX", True), self.client.selected)

    def test_search_scans_recent_messages_locally(self) -> None:
        matches = self.provider.search("review", limit=5)

        self.assertEqual(["imap-2"], [item.id for item in matches])

    def test_status_reports_counts_and_configured_mailboxes(self) -> None:
        status = self.provider.status()

        self.assertEqual("QQImapProvider", status["provider"])
        self.assertEqual("us***@foxmail.com", status["email"])
        self.assertEqual(2, status["message_count"])
        self.assertEqual(1, status["unread_count"])
        self.assertEqual(2, status["selected_message_count"])
        self.assertEqual(2, status["uid_search_all_count"])
        self.assertEqual(4, status["visible_mailbox_count"])
        self.assertEqual("selected_mailbox", status["diagnostics"]["message_count_scope"])
        self.assertTrue(status["archive_mailbox_exists"])
        self.assertTrue(status["drafts_mailbox_exists"])

        mailbox_counts = {item["name"]: item for item in status["mailbox_counts"]}
        self.assertEqual(2, mailbox_counts["INBOX"]["message_count"])
        self.assertEqual(1, mailbox_counts["INBOX"]["unread_count"])
        self.assertEqual(0, mailbox_counts["其他文件夹"]["message_count"])
        self.assertEqual(0, mailbox_counts["Archive"]["message_count"])
        self.assertEqual(0, mailbox_counts["Drafts"]["message_count"])
        self.assertTrue(mailbox_counts["INBOX"]["selected"])

    def test_status_reports_per_mailbox_counts(self) -> None:
        mailbox_messages = {
            "INBOX": self.messages,
            "&UXZO1mWHTvZZOQ-": {},
            "Archive": {
                "3": {"raw": _raw_imap_message("Archived", "Done"), "flags": {"\\Seen"}},
                "4": {"raw": _raw_imap_message("Unread archived", "Done"), "flags": set()},
                "5": {"raw": _raw_imap_message("Another archived", "Done"), "flags": {"\\Seen"}},
            },
            "Drafts": {},
        }
        client = FakeImapClient(self.messages, mailbox_messages=mailbox_messages)
        provider = QQImapProvider(
            QQImapConfig(
                email_address="user@foxmail.com",
                auth_code="auth-code",
                archive_mailbox="Archive",
                drafts_mailbox="Drafts",
            ),
            client_factory=lambda: client,
        )

        status = provider.status()

        mailbox_counts = {item["name"]: item for item in status["mailbox_counts"]}
        self.assertEqual(2, mailbox_counts["INBOX"]["message_count"])
        self.assertEqual(1, mailbox_counts["INBOX"]["unread_count"])
        self.assertEqual(0, mailbox_counts["其他文件夹"]["message_count"])
        self.assertEqual(3, mailbox_counts["Archive"]["message_count"])
        self.assertEqual(1, mailbox_counts["Archive"]["unread_count"])
        self.assertEqual(0, mailbox_counts["Drafts"]["message_count"])
        self.assertEqual(("INBOX", True), client.selected)

    def test_status_accepts_encoded_configured_mailbox_names(self) -> None:
        provider = QQImapProvider(
            QQImapConfig(
                email_address="user@foxmail.com",
                auth_code="auth-code",
                archive_mailbox="&UXZO1mWHTvZZOQ-",
                drafts_mailbox="Drafts",
            ),
            client_factory=lambda: self.client,
        )

        status = provider.status()

        self.assertEqual("其他文件夹", status["archive_mailbox_display"])
        self.assertTrue(status["archive_mailbox_exists"])

    def test_list_mailboxes_returns_imap_folders(self) -> None:
        result = self.provider.list_mailboxes()

        self.assertEqual("QQImapProvider", result["provider"])
        self.assertEqual(["INBOX", "其他文件夹", "Archive", "Drafts"], [item["name"] for item in result["mailboxes"]])
        self.assertEqual("Archive", result["configured"]["archive_mailbox"])

    def test_mark_read_updates_seen_flag(self) -> None:
        result = self.provider.mark_read("imap-2", is_read=True)

        self.assertTrue(result["is_read"])
        self.assertIn("\\Seen", self.messages["2"]["flags"])
        self.assertIn(("uid", "STORE", "2", "+FLAGS", r"(\Seen)"), self.client.actions)

    def test_archive_copies_then_deletes_original(self) -> None:
        result = self.provider.archive("imap-2")

        self.assertTrue(result["archived"])
        self.assertIn(("uid", "COPY", "2", "Archive"), self.client.actions)
        self.assertIn(("uid", "STORE", "2", "+FLAGS", r"(\Deleted)"), self.client.actions)
        self.assertIn(("expunge",), self.client.actions)

    def test_archive_rejects_missing_archive_mailbox(self) -> None:
        provider = QQImapProvider(
            QQImapConfig(
                email_address="user@foxmail.com",
                auth_code="auth-code",
                archive_mailbox="MissingArchive",
                drafts_mailbox="Drafts",
            ),
            client_factory=lambda: self.client,
        )

        with self.assertRaisesRegex(RuntimeError, "archive mailbox not found"):
            provider.archive("imap-2")

    def test_create_draft_appends_to_drafts_mailbox(self) -> None:
        result = self.provider.create_draft("imap-2", "Thanks, I will review.")

        self.assertFalse(result["sent"])
        self.assertEqual("Drafts", result["drafts_mailbox"])
        append_actions = [action for action in self.client.actions if action[0] == "append"]
        self.assertEqual(1, len(append_actions))
        self.assertEqual("Drafts", append_actions[0][1])
        self.assertIn(b"Thanks, I will review.", append_actions[0][4])

    def test_create_draft_rejects_missing_drafts_mailbox(self) -> None:
        provider = QQImapProvider(
            QQImapConfig(
                email_address="user@foxmail.com",
                auth_code="auth-code",
                archive_mailbox="Archive",
                drafts_mailbox="MissingDrafts",
            ),
            client_factory=lambda: self.client,
        )

        with self.assertRaisesRegex(RuntimeError, "drafts mailbox not found"):
            provider.create_draft("imap-2", "Thanks, I will review.")

    def test_provider_factory_supports_qq_imap(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MAILGUARD_EMAIL_PROVIDER": "qq-imap",
                "MAILGUARD_QQ_EMAIL": "user@foxmail.com",
                "MAILGUARD_QQ_AUTH_CODE": "auth-code",
            },
        ):
            self.assertIsInstance(create_email_provider(), QQImapProvider)


if __name__ == "__main__":
    unittest.main()
