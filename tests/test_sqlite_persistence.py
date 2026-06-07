"""Regression tests for MailGuard sqlite persistence."""

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
from server.app.agent import DEFAULT_STATE_DB, _state_db_path
from server.app.agent import AgentRuntime
from server.app.auth import configured_auth_token, require_api_token
from server.app.cleaner.rules import proposed_rule
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

class SQLiteMemoryPersistenceTests(unittest.TestCase):
    def test_sqlite_notification_create_once_deduplicates_across_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            second_store = SQLiteStateStore(db_path)
            try:
                first = MemoryStore(state_store=first_store)
                second = MemoryStore(state_store=second_store)
                notification = {
                    "type": "important_email",
                    "email_id": "email-001",
                    "subject": "Important",
                    "category": "action_required",
                    "importance": "high",
                    "suggested_action": "review",
                    "reasons": ["test"],
                }

                self.assertEqual([], first.email_scheduler_state("persist")["reported_email_ids"])
                self.assertEqual([], second.email_scheduler_state("persist")["reported_email_ids"])

                first_created = first.create_email_notification_once("persist", "email-001", notification)
                second_created = second.create_email_notification_once("persist", "email-001", notification)

                self.assertIsNotNone(first_created)
                self.assertIsNone(second_created)
                persisted = second.email_scheduler_state("persist")
            finally:
                first_store.close()
                second_store.close()

        self.assertEqual(["email-001"], persisted["reported_email_ids"])
        self.assertEqual(1, len(persisted["notifications"]))
        self.assertEqual("email-001", persisted["notifications"][0]["email_id"])

    def test_email_preferences_persist_across_memory_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            first = MemoryStore(state_store=first_store)
            first.add_email_preference("persist", "important_senders", "boss@example.com")
            first.set_email_preference("persist", "timezone", "Asia/Shanghai")
            first_store.close()

            second_store = SQLiteStateStore(db_path)
            second = MemoryStore(state_store=second_store)
            preferences = second.email_preferences("persist")
            second_store.close()

        self.assertEqual(["boss@example.com"], preferences["important_senders"])
        self.assertEqual("Asia/Shanghai", preferences["timezone"])

    def test_scheduler_state_persists_across_memory_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            first = MemoryStore(state_store=first_store)
            notification = first.add_email_notification(
                "persist",
                {
                    "notification_id": "notification-test",
                    "type": "important_email",
                    "email_id": "email-001",
                    "subject": "Important",
                    "category": "action_required",
                    "importance": "high",
                    "suggested_action": "review",
                    "reasons": ["test"],
                },
            )
            first.mark_email_reported("persist", "email-001")
            first.mark_email_notification_read("persist", notification["notification_id"])
            first.record_email_scan(
                "persist",
                {
                    "scan_id": "scan-test",
                    "provider": "MockEmailProvider",
                    "fetched": 1,
                    "classified_count": 1,
                    "reportable_count": 1,
                    "ignored_count": 0,
                    "created_notification_count": 1,
                    "skipped_duplicate_count": 0,
                    "created_notification_ids": ["notification-test"],
                    "skipped_duplicate_email_ids": [],
                },
            )
            first_store.close()

            second_store = SQLiteStateStore(db_path)
            second = MemoryStore(state_store=second_store)
            state = second.email_scheduler_state("persist")
            has_reported = second.has_reported_email("persist", "email-001")
            second_store.close()

        self.assertTrue(has_reported)
        self.assertEqual(["email-001"], state["reported_email_ids"])
        self.assertEqual("read", state["notifications"][0]["status"])
        self.assertEqual("scan-test", state["scan_history"][0]["scan_id"])
        self.assertEqual(state["scan_history"][0]["created_at"], state["last_scan_at"])

    def test_action_proposals_and_audit_persist_across_memory_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            first = MemoryStore(state_store=first_store)
            created = first.create_action_proposal_once(
                "persist",
                {
                    "proposal_id": "proposal-test",
                    "action": "archive",
                    "email_id": "email-004",
                    "thread_id": "thread-004",
                    "subject": "Newsletter",
                    "from_email": "newsletter@example.com",
                    "from_name": "Newsletter",
                    "reason": "low-value mail",
                    "evidence": {"category": "newsletter"},
                },
            )
            duplicate = first.create_action_proposal_once(
                "persist",
                {
                    "proposal_id": "proposal-other",
                    "action": "archive",
                    "email_id": "email-004",
                    "reason": "duplicate",
                    "evidence": {},
                },
            )
            first.add_action_audit_event("persist", created["proposal"]["proposal_id"], "proposal_created", "policy", {})
            first.update_action_proposal("persist", created["proposal"]["proposal_id"], {"status": "approved"})
            first_store.close()

            second_store = SQLiteStateStore(db_path)
            second = MemoryStore(state_store=second_store)
            proposals = second.action_proposals("persist")
            audit = second.action_audit_events("persist")
            second_store.close()

        self.assertTrue(created["created"])
        self.assertFalse(duplicate["created"])
        self.assertEqual(1, len(proposals))
        self.assertEqual("proposal-test", proposals[0]["proposal_id"])
        self.assertEqual("approved", proposals[0]["status"])
        self.assertEqual(1, len(audit))
        self.assertEqual("proposal_created", audit[0]["event_type"])

    def test_clean_rules_persist_across_memory_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            first = MemoryStore(state_store=first_store)
            rule = proposed_rule(
                action="archive",
                scope="domain",
                value="facebookmail.com",
                source="test",
                reason="social notification cleanup",
            )
            first.save_clean_rule("persist", rule)
            approved = first.approve_clean_rule("persist", rule["rule_id"])
            first_store.close()

            second_store = SQLiteStateStore(db_path)
            second = MemoryStore(state_store=second_store)
            rules = second.clean_rules("persist", status="enabled", limit=0)
            second_store.close()

        self.assertEqual(1, len(rules))
        self.assertEqual(approved["rule_id"], rules[0]["rule_id"])
        self.assertEqual("enabled", rules[0]["status"])

    def test_clean_audit_events_persist_across_memory_store_instances(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "state.db"
            first_store = SQLiteStateStore(db_path)
            first = MemoryStore(state_store=first_store)
            event = first.add_clean_audit_event(
                "persist",
                run_id="clean-run-test",
                email_id="email-001",
                event_type="clean_execution_succeeded",
                actor="system",
                payload={"action": "archive", "email_id": "email-001"},
            )
            first_store.close()

            second_store = SQLiteStateStore(db_path)
            second = MemoryStore(state_store=second_store)
            events = second.clean_audit_events("persist", run_id="clean-run-test", limit=0)
            second_store.close()

        self.assertEqual(1, len(events))
        self.assertEqual(event["event_id"], events[0]["event_id"])
        self.assertEqual("email-001", events[0]["email_id"])

class SQLiteRuntimePersistenceTests(unittest.TestCase):
    def test_runtime_factory_uses_default_state_db_when_env_is_unset(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "default-state.db"
            with patch("server.app.agent.DEFAULT_STATE_DB", str(db_path)):
                with patch("server.app.agent.load_server_env", lambda: None), patch.dict(os.environ, {}, clear=True):
                    first = AgentRuntime.create()
                    try:
                        add = first.execute_tool_for_test(
                            "email_add_preference",
                            {"key": "important_senders", "value": "default@example.com"},
                            session_id="runtime-default-persist",
                        )
                        self.assertTrue(add["ok"])
                    finally:
                        first.close()

                    second = AgentRuntime.create()
                    try:
                        preferences = second.execute_tool_for_test(
                            "email_get_preferences",
                            {},
                            session_id="runtime-default-persist",
                        )
                        self.assertTrue(preferences["ok"])
                        self.assertEqual(
                            ["default@example.com"],
                            preferences["result"]["preferences"]["important_senders"],
                        )
                    finally:
                        second.close()

            self.assertTrue(db_path.exists())

    def test_runtime_factory_can_disable_state_db_with_empty_env(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_STATE_DB": ""}):
            runtime = AgentRuntime.create()
            try:
                self.assertIsNone(runtime.memory_store._state_store)
            finally:
                runtime.close()

    def test_runtime_factory_uses_configured_state_db(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime-state.db"
            with patch.dict(os.environ, {"MAILGUARD_STATE_DB": str(db_path)}):
                first = AgentRuntime.create()
                try:
                    add = first.execute_tool_for_test(
                        "email_add_preference",
                        {"key": "important_senders", "value": "boss@example.com"},
                        session_id="runtime-persist",
                    )
                    self.assertTrue(add["ok"])
                finally:
                    first.close()

                second = AgentRuntime.create()
                try:
                    preferences = second.execute_tool_for_test(
                        "email_get_preferences",
                        {},
                        session_id="runtime-persist",
                    )
                    self.assertTrue(preferences["ok"])
                    self.assertEqual(
                        ["boss@example.com"],
                        preferences["result"]["preferences"]["important_senders"],
                    )
                finally:
                    second.close()

    def test_state_db_path_accepts_historical_server_prefix(self) -> None:
        resolved = Path(_state_db_path("server/data/mailguard_state.db"))
        self.assertEqual(Path("server/data/mailguard_state.db").resolve(), resolved)

    def test_default_state_db_points_under_server_data(self) -> None:
        self.assertEqual(Path("server/data/mailguard_state.db").resolve(), Path(_state_db_path(DEFAULT_STATE_DB)))
