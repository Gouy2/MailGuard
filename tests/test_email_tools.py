"""Regression tests for the mock email triage tools."""

from __future__ import annotations

import unittest
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server.app.agent import _state_db_path
from server.app.agent import AgentRuntime
from server.app.auth import configured_auth_token
from server.app.email_eval import evaluate_email_classifier
from server.app.email_provider import MockEmailProvider
from server.app.email_tools import classify_email
from server.app.llm_email_classifier import _normalize_decision, _parse_json_object
from server.app.memory import MemoryStore
from server.app.provider_factory import create_email_provider
from server.app.sqlite_state import SQLiteStateStore


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


class EmailToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(os.environ, {"WISPERA_STATE_DB": ""})
        self._env_patcher.start()
        self.runtime = AgentRuntime.create()

    def tearDown(self) -> None:
        self.runtime.close()
        self._env_patcher.stop()

    def test_email_tools_are_registered(self) -> None:
        names = {tool["name"] for tool in self.runtime.tool_inventory()}
        self.assertIn("email_report_important", names)
        self.assertIn("email_list_ignored", names)
        self.assertIn("email_get_detail", names)
        self.assertIn("email_classify", names)
        self.assertIn("email_archive", names)
        self.assertIn("email_mark_read", names)
        self.assertIn("email_star", names)
        self.assertIn("email_create_draft", names)
        self.assertIn("email_get_preferences", names)
        self.assertIn("email_add_preference", names)
        self.assertIn("email_remove_preference", names)
        self.assertIn("email_set_preference", names)
        self.assertIn("email_scheduler_run_once", names)
        self.assertIn("email_notifications", names)
        self.assertIn("email_notification_mark_read", names)
        self.assertIn("email_daily_digest", names)
        self.assertIn("email_scheduler_state", names)
        self.assertIn("email_eval_mock", names)
        self.assertIn("email_eval_llm_shadow", names)
        self.assertIn("email_eval_report", names)
        self.assertNotIn("read_text_file", names)
        self.assertNotIn("run_shell_command", names)

    def test_email_report_important(self) -> None:
        response = self.runtime.execute_tool_for_test("email_report_important", {"limit": 12})
        self.assertTrue(response["ok"])

        result = response["result"]
        self.assertEqual(7, result["important_count"])
        self.assertEqual(5, result["ignored_count"])
        self.assertEqual({"newsletter": 3, "noise": 1, "promotion": 1}, result["ignored_summary"])

    def test_email_detail_includes_classification(self) -> None:
        response = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertTrue(response["ok"])
        self.assertEqual("email-001", response["result"]["email"]["id"])
        self.assertEqual("action_required", response["result"]["classification"]["category"])

    def test_archive_requires_approval_before_mutation(self) -> None:
        pending = self.runtime.execute_tool_for_test("email_archive", {"email_id": "email-001"})
        self.assertFalse(pending["ok"])
        self.assertTrue(pending["requires_approval"])

        before_approval = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertIn("inbox", before_approval["result"]["email"]["labels"])
        self.assertNotIn("archived", before_approval["result"]["email"]["labels"])

        approved = self.runtime.approve_tool(pending["pending_tool_call_id"])
        self.assertTrue(approved["ok"])
        self.assertEqual("archive", approved["result"]["action"])

        after_approval = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertNotIn("inbox", after_approval["result"]["email"]["labels"])
        self.assertIn("archived", after_approval["result"]["email"]["labels"])

    def test_rejected_star_does_not_mutate(self) -> None:
        pending = self.runtime.execute_tool_for_test("email_star", {"email_id": "email-002"})
        self.assertTrue(pending["requires_approval"])

        rejected = self.runtime.reject_tool(pending["pending_tool_call_id"])
        self.assertTrue(rejected["ok"])
        self.assertTrue(rejected["rejected"])

        detail = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-002"})
        self.assertNotIn("starred", detail["result"]["email"]["labels"])

    def test_mark_read_and_create_draft_require_approval(self) -> None:
        mark_read = self.runtime.execute_tool_for_test("email_mark_read", {"email_id": "email-001"})
        self.assertTrue(mark_read["requires_approval"])

        detail = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertFalse(detail["result"]["email"]["is_read"])

        approved_mark_read = self.runtime.approve_tool(mark_read["pending_tool_call_id"])
        self.assertTrue(approved_mark_read["ok"])
        detail = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertTrue(detail["result"]["email"]["is_read"])

        draft = self.runtime.execute_tool_for_test(
            "email_create_draft",
            {"email_id": "email-001", "body": "Thanks, I will review this today."},
        )
        self.assertTrue(draft["requires_approval"])

        approved_draft = self.runtime.approve_tool(draft["pending_tool_call_id"])
        self.assertTrue(approved_draft["ok"])
        self.assertEqual("create_draft", approved_draft["result"]["action"])
        self.assertFalse(approved_draft["result"]["result"]["sent"])
        self.assertEqual("email-001", approved_draft["result"]["result"]["source_email_id"])

    def test_pending_tools_redact_draft_body_but_approval_uses_original(self) -> None:
        pending = self.runtime.execute_tool_for_test(
            "email_create_draft",
            {"email_id": "email-001", "body": "Sensitive draft body should not appear in pending."},
            session_id="redact-pending",
        )
        self.assertTrue(pending["requires_approval"])

        pending_items = self.runtime.pending_tools()
        item = next(item for item in pending_items if item["id"] == pending["pending_tool_call_id"])
        self.assertTrue(item["arguments"]["body"]["redacted"])
        self.assertEqual("email-001", item["arguments"]["email_id"])

        approved = self.runtime.approve_tool(pending["pending_tool_call_id"])
        self.assertTrue(approved["ok"])
        self.assertIn("Sensitive draft body", approved["result"]["result"]["body_preview"])

    def test_preference_tools_are_structured_and_session_scoped(self) -> None:
        add = self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "important_senders", "value": "Newsletter@DesignWeekly.Example"},
            session_id="prefs-a",
        )
        self.assertTrue(add["ok"])
        self.assertEqual(["newsletter@designweekly.example"], add["result"]["preferences"]["important_senders"])

        other_session = self.runtime.execute_tool_for_test(
            "email_get_preferences",
            {},
            session_id="prefs-b",
        )
        self.assertEqual([], other_session["result"]["preferences"]["important_senders"])

        remove = self.runtime.execute_tool_for_test(
            "email_remove_preference",
            {"key": "important_senders", "value": "newsletter@designweekly.example"},
            session_id="prefs-a",
        )
        self.assertTrue(remove["ok"])
        self.assertEqual([], remove["result"]["preferences"]["important_senders"])

    def test_important_sender_preference_promotes_newsletter(self) -> None:
        baseline = self.runtime.execute_tool_for_test(
            "email_classify",
            {"email_id": "email-004"},
            session_id="promote",
        )
        self.assertEqual("newsletter", baseline["result"]["classification"]["category"])
        self.assertFalse(baseline["result"]["classification"]["is_reportable"])

        self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "important_senders", "value": "newsletter@designweekly.example"},
            session_id="promote",
        )
        promoted = self.runtime.execute_tool_for_test(
            "email_classify",
            {"email_id": "email-004"},
            session_id="promote",
        )
        classification = promoted["result"]["classification"]
        self.assertEqual("important", classification["category"])
        self.assertEqual("high", classification["importance"])
        self.assertTrue(classification["is_reportable"])
        self.assertIn("important sender preference: newsletter@designweekly.example", classification["reasons"])

    def test_ignored_sender_preference_suppresses_action_email(self) -> None:
        self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "ignored_senders", "value": "maya.chen@acme-corp.com"},
            session_id="suppress",
        )
        suppressed = self.runtime.execute_tool_for_test(
            "email_classify",
            {"email_id": "email-001"},
            session_id="suppress",
        )
        classification = suppressed["result"]["classification"]
        self.assertEqual("noise", classification["category"])
        self.assertEqual("low", classification["importance"])
        self.assertTrue(classification["is_ignored"])
        self.assertFalse(classification["is_reportable"])
        self.assertIn("ignored sender preference: maya.chen@acme-corp.com", classification["reasons"])

    def test_ignored_category_preference_changes_report_counts(self) -> None:
        baseline = self.runtime.execute_tool_for_test(
            "email_report_important",
            {"limit": 12},
            session_id="ignore-category",
        )
        self.assertEqual(7, baseline["result"]["important_count"])
        self.assertEqual(5, baseline["result"]["ignored_count"])

        self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "ignored_categories", "value": "notification"},
            session_id="ignore-category",
        )
        updated = self.runtime.execute_tool_for_test(
            "email_report_important",
            {"limit": 12},
            session_id="ignore-category",
        )
        self.assertEqual(6, updated["result"]["important_count"])
        self.assertEqual(6, updated["result"]["ignored_count"])

    def test_scheduler_creates_deduplicated_notifications(self) -> None:
        first_scan = self.runtime.execute_tool_for_test(
            "email_scheduler_run_once",
            {"limit": 12},
            session_id="scheduler",
        )
        self.assertTrue(first_scan["ok"])
        scan = first_scan["result"]["scheduler"]["scan"]
        self.assertEqual(5, scan["created_notification_count"])
        self.assertEqual(0, scan["skipped_duplicate_count"])

        second_scan = self.runtime.execute_tool_for_test(
            "email_scheduler_run_once",
            {"limit": 12},
            session_id="scheduler",
        )
        self.assertTrue(second_scan["ok"])
        scan = second_scan["result"]["scheduler"]["scan"]
        self.assertEqual(0, scan["created_notification_count"])
        self.assertEqual(5, scan["skipped_duplicate_count"])

        notifications = self.runtime.execute_tool_for_test(
            "email_notifications",
            {},
            session_id="scheduler",
        )
        self.assertEqual(5, notifications["result"]["count"])
        self.assertEqual(
            {"email-026", "email-027", "email-028", "email-034", "email-035"},
            {item["email_id"] for item in notifications["result"]["notifications"]},
        )

    def test_scheduler_concurrent_scans_do_not_duplicate_notifications(self) -> None:
        def run_scan():
            return self.runtime.execute_tool_for_test(
                "email_scheduler_run_once",
                {"limit": 12},
                session_id="scheduler-concurrent",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _index: run_scan(), range(2)))

        self.assertTrue(all(result["ok"] for result in results))
        notifications = self.runtime.execute_tool_for_test(
            "email_notifications",
            {"include_read": True},
            session_id="scheduler-concurrent",
        )
        email_ids = [item["email_id"] for item in notifications["result"]["notifications"]]
        self.assertEqual(len(email_ids), len(set(email_ids)))
        self.assertEqual(5, len(email_ids))

    def test_scheduler_notification_read_state_and_digest(self) -> None:
        self.runtime.execute_tool_for_test(
            "email_scheduler_run_once",
            {"limit": 12},
            session_id="scheduler-read",
        )
        notifications = self.runtime.execute_tool_for_test(
            "email_notifications",
            {},
            session_id="scheduler-read",
        )["result"]["notifications"]
        notification_id = notifications[0]["notification_id"]

        marked = self.runtime.execute_tool_for_test(
            "email_notification_mark_read",
            {"notification_id": notification_id},
            session_id="scheduler-read",
        )
        self.assertTrue(marked["ok"])
        self.assertEqual("read", marked["result"]["notification"]["status"])

        unread = self.runtime.execute_tool_for_test(
            "email_notifications",
            {},
            session_id="scheduler-read",
        )
        self.assertEqual(4, unread["result"]["count"])

        digest = self.runtime.execute_tool_for_test(
            "email_daily_digest",
            {},
            session_id="scheduler-read",
        )
        self.assertEqual(5, digest["result"]["notification_count"])
        self.assertEqual({"high": 5}, digest["result"]["importance_counts"])

    def test_scheduler_respects_preferences(self) -> None:
        self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "ignored_senders", "value": "maya.chen@acme-corp.com"},
            session_id="scheduler-prefs",
        )
        scan = self.runtime.execute_tool_for_test(
            "email_scheduler_run_once",
            {"limit": 12},
            session_id="scheduler-prefs",
        )
        self.assertEqual(5, scan["result"]["scheduler"]["scan"]["created_notification_count"])

        notifications = self.runtime.execute_tool_for_test(
            "email_notifications",
            {},
            session_id="scheduler-prefs",
        )
        self.assertNotIn(
            "email-001",
            {item["email_id"] for item in notifications["result"]["notifications"]},
        )

    def test_mock_evaluation_baseline(self) -> None:
        result = evaluate_email_classifier(provider=MockEmailProvider(), classifier=classify_email)
        self.assertEqual(36, result["sample_count"])
        self.assertEqual(36, result["labeled_count"])
        self.assertEqual(1.0, result["metrics"]["category_accuracy"])
        self.assertEqual(1.0, result["metrics"]["importance_accuracy"])
        self.assertEqual(1.0, result["metrics"]["action_accuracy"])
        self.assertEqual(1.0, result["metrics"]["important_recall"])
        self.assertEqual(1.0, result["metrics"]["important_precision"])
        self.assertEqual(1.0, result["metrics"]["noise_filter_precision"])
        self.assertEqual([], result["mismatches"])

    def test_mock_evaluation_tool(self) -> None:
        result = self.runtime.execute_tool_for_test("email_eval_mock", {"limit": 36})
        self.assertTrue(result["ok"])
        self.assertEqual(36, result["result"]["sample_count"])
        self.assertEqual(1.0, result["result"]["metrics"]["category_accuracy"])
        self.assertEqual([], result["result"]["mismatches"])

    def test_eval_report_tool_writes_markdown_report(self) -> None:
        output_path = "docs/test-logs/test-email-eval-report.md"
        report_file = Path(output_path)
        if report_file.exists():
            report_file.unlink()

        result = self.runtime.execute_tool_for_test(
            "email_eval_report",
            {"classifier": "rule", "limit": 36, "output_path": output_path},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(output_path, result["result"]["report"]["path"])
        self.assertEqual("rule", result["result"]["classifier"])
        self.assertEqual(36, result["result"]["evaluation"]["sample_count"])
        self.assertTrue(report_file.exists())
        content = report_file.read_text(encoding="utf-8")
        self.assertIn("# Email Triage Evaluation Report", content)
        self.assertIn("`important_recall`", content)
        report_file.unlink()

    def test_eval_report_tool_rejects_output_outside_test_logs(self) -> None:
        result = self.runtime.execute_tool_for_test(
            "email_eval_report",
            {"classifier": "rule", "limit": 1, "output_path": "README.md"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual("execution_error", result["error_type"])
        self.assertIn("docs/test-logs", result["error"])

    def test_evaluation_can_record_classifier_errors(self) -> None:
        def failing_classifier(_email, _preferences):
            raise ValueError("bad model output")

        result = evaluate_email_classifier(
            provider=MockEmailProvider(),
            classifier=failing_classifier,
            limit=1,
            continue_on_error=True,
        )

        self.assertEqual(1, result["sample_count"])
        self.assertEqual(1, len(result["errors"]))
        self.assertIn("bad model output", result["errors"][0]["classifier_error"])

    def test_llm_shadow_eval_tool_uses_mock_provider_without_real_api(self) -> None:
        class FakeLLMClassifier:
            model = "fake-model"
            init_kwargs = {}

            def __init__(self, model=None, timeout=30.0, max_retries=1):
                type(self).init_kwargs = {
                    "model": model,
                    "timeout": timeout,
                    "max_retries": max_retries,
                }
                if model:
                    self.model = model

            def classify(self, email, _preferences):
                category = email.expected_category or "notification"
                importance = email.expected_importance or "low"
                return {
                    "email_id": email.id,
                    "category": category,
                    "importance": importance,
                    "suggested_action": "ignore" if importance == "low" else "review",
                    "reasons": ["fake llm decision"],
                    "is_reportable": category not in {"newsletter", "promotion", "noise"} and importance != "low",
                    "is_ignored": category in {"newsletter", "promotion", "noise"} or importance == "low",
                }

        with patch("server.app.email_tools.LLMEmailClassifier", FakeLLMClassifier):
            result = self.runtime.execute_tool_for_test(
                "email_eval_llm_shadow",
                {"limit": 2, "model": "fake-shadow", "timeout": 45, "max_retries": 2},
            )

        self.assertTrue(result["ok"])
        self.assertEqual("llm_shadow", result["result"]["classifier"])
        self.assertEqual("fake-shadow", result["result"]["model"])
        self.assertEqual(45.0, result["result"]["timeout"])
        self.assertEqual(2, result["result"]["max_retries"])
        self.assertEqual("MockEmailProvider", result["result"]["provider"])
        self.assertFalse(result["result"]["mailbox_mutation"])
        self.assertEqual(2, result["result"]["evaluation"]["sample_count"])
        self.assertEqual([], result["result"]["evaluation"]["errors"])
        self.assertEqual(
            {"model": "fake-shadow", "timeout": 45.0, "max_retries": 2},
            FakeLLMClassifier.init_kwargs,
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


class SQLiteRuntimePersistenceTests(unittest.TestCase):
    def test_runtime_factory_uses_configured_state_db(self) -> None:
        with TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "runtime-state.db"
            with patch.dict(os.environ, {"WISPERA_STATE_DB": str(db_path)}):
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
        resolved = Path(_state_db_path("server/data/wispera_state.db"))
        self.assertEqual(Path("server/data/wispera_state.db").resolve(), resolved)


class AuthAndDevToolTests(unittest.TestCase):
    def test_configured_auth_token_reads_environment(self) -> None:
        with patch.dict(os.environ, {"WISPERA_AUTH_TOKEN": "test-token"}):
            self.assertEqual("test-token", configured_auth_token())

    def test_dev_tools_can_be_enabled_and_reject_sensitive_paths(self) -> None:
        with patch.dict(os.environ, {"WISPERA_STATE_DB": "", "WISPERA_DEV_TOOLS": "1"}):
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
        with patch.dict(os.environ, {"WISPERA_EMAIL_PROVIDER": ""}):
            self.assertIsInstance(create_email_provider(), MockEmailProvider)

    def test_provider_factory_rejects_unknown_provider(self) -> None:
        with patch.dict(os.environ, {"WISPERA_EMAIL_PROVIDER": "outlook"}):
            with self.assertRaises(RuntimeError):
                create_email_provider()


if __name__ == "__main__":
    unittest.main()
