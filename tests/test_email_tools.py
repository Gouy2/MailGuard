"""Regression tests for the mock email triage tools."""

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
from server.app.provider_factory import create_email_provider
from server.app.qq_imap_provider import QQImapConfig, QQImapProvider
from server.app.redaction import redact_for_trace
from server.app.tracer import TraceLogger
from server.app.real_email_eval import evaluate_real_labels, load_real_labels, save_real_label
from server.app.sqlite_state import SQLiteStateStore


class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: str) -> None:
        self.id = tool_call_id
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeChatMessage:
    def __init__(self, content: str = "", tool_calls: list[FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChoice:
    def __init__(self, message: FakeChatMessage) -> None:
        self.message = message


class FakeChatResponse:
    def __init__(self, message: FakeChatMessage) -> None:
        self.choices = [FakeChoice(message)]


class FakeOpenAIClient:
    def __init__(self, responses: list[FakeChatResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected extra OpenAI chat completion call")
        return self.responses.pop(0)


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


class EmailToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(os.environ, {"MAILGUARD_EMAIL_PROVIDER": "mock", "MAILGUARD_STATE_DB": ""})
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
        self.assertIn("email_scan_proposals", names)
        self.assertIn("email_list_proposals", names)
        self.assertIn("email_approve_proposal", names)
        self.assertIn("email_reject_proposal", names)
        self.assertIn("email_execute_approved_proposals", names)
        self.assertIn("email_audit_log", names)
        self.assertIn("email_provider_status", names)
        self.assertIn("email_list_mailboxes", names)
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

    def test_agent_stops_when_dangerous_tool_requires_approval(self) -> None:
        fake_client = FakeOpenAIClient(
            [
                FakeChatResponse(
                    FakeChatMessage(
                        tool_calls=[
                            FakeToolCall(
                                "call-archive",
                                "email_archive",
                                '{"email_id":"email-001"}',
                            )
                        ]
                    )
                ),
                FakeChatResponse(FakeChatMessage("should not be reached")),
            ]
        )

        with patch("server.app.agent._openai_client", return_value=fake_client):
            events = list(self.runtime.stream_chat("approval-agent", "Archive email-001"))

        self.assertEqual(1, len(fake_client.calls))
        self.assertEqual(1, len(self.runtime.pending_tools()))
        self.assertIn('"status": "pending"', events[-1])
        self.assertIn("pending_tool_call_id", events[-1])

        detail = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertIn("inbox", detail["result"]["email"]["labels"])
        self.assertNotIn("archived", detail["result"]["email"]["labels"])

    def test_readonly_agent_exposes_only_read_tools(self) -> None:
        tools = self.runtime.agent_tools(mode="agent_readonly")
        names = {tool["function"]["name"] for tool in tools}

        self.assertIn("email_report_important", names)
        self.assertIn("email_get_detail", names)
        self.assertIn("email_get_preferences", names)
        self.assertNotIn("email_archive", names)
        self.assertNotIn("email_mark_read", names)
        self.assertNotIn("email_star", names)
        self.assertNotIn("email_create_draft", names)
        self.assertNotIn("email_add_preference", names)

    def test_readonly_agent_blocks_unexpected_write_tool_call(self) -> None:
        fake_client = FakeOpenAIClient(
            [
                FakeChatResponse(
                    FakeChatMessage(
                        tool_calls=[
                            FakeToolCall(
                                "call-archive",
                                "email_archive",
                                '{"email_id":"email-001"}',
                            )
                        ]
                    )
                )
            ]
        )

        with patch("server.app.agent._openai_client", return_value=fake_client):
            events = list(
                self.runtime.stream_chat(
                    "readonly-agent",
                    "请归档 email-001",
                    mode="agent_readonly",
                )
            )

        self.assertEqual(1, len(fake_client.calls))
        offered_tools = {tool["function"]["name"] for tool in fake_client.calls[0]["tools"]}
        self.assertNotIn("email_archive", offered_tools)
        self.assertEqual([], self.runtime.pending_tools())
        self.assertIn('"status": "blocked"', events[-1])

        detail = self.runtime.execute_tool_for_test("email_get_detail", {"email_id": "email-001"})
        self.assertIn("inbox", detail["result"]["email"]["labels"])
        self.assertNotIn("archived", detail["result"]["email"]["labels"])

    def test_agent_smoke_covers_read_approval_and_reject_flows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = run_agent_smoke(trace_dir=temp_dir)

        self.assertTrue(result["ok"])
        self.assertEqual("mock", result["provider"])
        self.assertEqual("mock_only", result["mailbox_mutation"])
        scenarios = {item["name"]: item for item in result["scenarios"]}
        self.assertEqual({"read_report", "archive_approve", "star_reject"}, set(scenarios))
        self.assertEqual("ok", scenarios["read_report"]["done_status"])
        self.assertEqual("pending", scenarios["archive_approve"]["done_status"])
        self.assertEqual("approved", scenarios["archive_approve"]["approval"])
        self.assertEqual("pending", scenarios["star_reject"]["done_status"])
        self.assertEqual("rejected", scenarios["star_reject"]["approval"])
        self.assertIn("tool_call", scenarios["read_report"]["trace_events"])
        self.assertIn("tool_pending", scenarios["archive_approve"]["trace_events"])

    def test_real_pending_write_smoke_rejects_all_pending_calls(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = run_real_pending_write_smoke(trace_dir=temp_dir)

        self.assertTrue(result["ok"])
        self.assertEqual("MockEmailProvider", result["provider"])
        self.assertEqual("none_rejected", result["mailbox_mutation"])
        self.assertEqual(0, result["pending_count_after"])
        scenarios = {item["name"]: item for item in result["scenarios"]}
        self.assertEqual(
            {"mark_read_pending", "archive_pending", "star_pending", "draft_pending"},
            set(scenarios),
        )
        for item in scenarios.values():
            self.assertTrue(item["ok"])
            self.assertEqual("pending", item["done_status"])
            self.assertTrue(item["pending_created"])
            self.assertTrue(item["rejected"])
            self.assertIn("tool_pending", item["trace_events"])

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
        output_path = f"docs/test-logs/test-email-eval-report-{os.getpid()}.md"
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

    def test_scan_proposals_creates_archive_candidates_and_skips_important(self) -> None:
        response = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-scan",
        )

        self.assertTrue(response["ok"])
        result = response["result"]
        proposal_ids = {item["email_id"] for item in result["proposals"]}
        important_ids = {item["email_id"] for item in result["important"]}
        self.assertIn("email-033", proposal_ids)
        self.assertIn("email-029", proposal_ids)
        self.assertIn("email-035", important_ids)
        self.assertIn("email-034", important_ids)
        self.assertEqual(result["proposal_count"], result["created_count"])

        audit = self.runtime.execute_tool_for_test(
            "email_audit_log",
            {},
            session_id="proposal-scan",
        )
        self.assertTrue(audit["ok"])
        self.assertEqual(result["created_count"], audit["result"]["count"])
        self.assertTrue(all(item["event_type"] == "proposal_created" for item in audit["result"]["events"]))

    def test_large_proposal_scan_keeps_structured_result(self) -> None:
        response = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 50, "unread_only": False},
            session_id="proposal-large-scan",
        )

        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertNotIn("truncated", result)
        self.assertEqual(7, result["proposal_count"])
        self.assertEqual(23, result["important_count"])
        self.assertEqual(10, result["important_returned_count"])
        self.assertLessEqual(result["important_returned_count"], result["important_count"])

    def test_scan_proposals_deduplicates_repeated_scans(self) -> None:
        first = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-dedupe",
        )
        second = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-dedupe",
        )

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertGreater(first["result"]["created_count"], 0)
        self.assertEqual(0, second["result"]["created_count"])
        self.assertEqual(first["result"]["proposal_count"], second["result"]["duplicate_count"])

        listed = self.runtime.execute_tool_for_test(
            "email_list_proposals",
            {},
            session_id="proposal-dedupe",
        )
        self.assertEqual(first["result"]["proposal_count"], listed["result"]["count"])

    def test_important_sender_preference_blocks_archive_proposal(self) -> None:
        self.runtime.execute_tool_for_test(
            "email_add_preference",
            {"key": "important_senders", "value": "updates@learning.example"},
            session_id="proposal-preference",
        )

        response = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-preference",
        )

        self.assertTrue(response["ok"])
        proposal_ids = {item["email_id"] for item in response["result"]["proposals"]}
        important_ids = {item["email_id"] for item in response["result"]["important"]}
        self.assertNotIn("email-033", proposal_ids)
        self.assertIn("email-033", important_ids)

    def test_approved_proposal_executes_archive_and_writes_audit(self) -> None:
        scan = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-execute",
        )
        proposal = next(item for item in scan["result"]["proposals"] if item["email_id"] == "email-033")

        pending_approval = self.runtime.execute_tool_for_test(
            "email_approve_proposal",
            {"proposal_id": proposal["proposal_id"]},
            session_id="proposal-execute",
        )
        before_user_approval = self.runtime.execute_tool_for_test(
            "email_execute_approved_proposals",
            {},
            session_id="proposal-execute",
        )
        approved = self.runtime.approve_tool(pending_approval["pending_tool_call_id"])
        executed = self.runtime.execute_tool_for_test(
            "email_execute_approved_proposals",
            {},
            session_id="proposal-execute",
        )

        self.assertTrue(pending_approval["requires_approval"])
        self.assertEqual(0, before_user_approval["result"]["executed_count"])
        self.assertTrue(approved["ok"])
        self.assertEqual("approved", approved["result"]["proposal"]["status"])
        self.assertTrue(executed["ok"])
        self.assertEqual(1, executed["result"]["executed_count"])

        detail = self.runtime.execute_tool_for_test(
            "email_get_detail",
            {"email_id": "email-033"},
            session_id="proposal-execute",
        )
        self.assertIn("archived", detail["result"]["email"]["labels"])

        audit = self.runtime.execute_tool_for_test(
            "email_audit_log",
            {"proposal_id": proposal["proposal_id"]},
            session_id="proposal-execute",
        )
        event_types = [item["event_type"] for item in audit["result"]["events"]]
        self.assertEqual(
            ["proposal_created", "proposal_approved", "execution_started", "execution_succeeded"],
            event_types,
        )

    def test_rejected_proposal_does_not_execute(self) -> None:
        scan = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-reject",
        )
        proposal = next(item for item in scan["result"]["proposals"] if item["email_id"] == "email-029")

        rejected = self.runtime.execute_tool_for_test(
            "email_reject_proposal",
            {"proposal_id": proposal["proposal_id"], "reason": "keep this sale"},
            session_id="proposal-reject",
        )
        executed = self.runtime.execute_tool_for_test(
            "email_execute_approved_proposals",
            {},
            session_id="proposal-reject",
        )

        self.assertTrue(rejected["ok"])
        self.assertEqual("rejected", rejected["result"]["proposal"]["status"])
        self.assertEqual(0, executed["result"]["executed_count"])

        detail = self.runtime.execute_tool_for_test(
            "email_get_detail",
            {"email_id": "email-029"},
            session_id="proposal-reject",
        )
        self.assertNotIn("archived", detail["result"]["email"]["labels"])

    def test_failed_proposal_execution_writes_failed_status_and_audit(self) -> None:
        class FailingArchiveProvider(MockEmailProvider):
            def archive(self, email_id: str) -> dict[str, Any]:
                raise RuntimeError("archive unavailable")

        store = MemoryStore()
        created = store.create_action_proposal_once(
            "proposal-fail",
            {
                "action": "archive",
                "email_id": "email-004",
                "thread_id": "thread-004",
                "subject": "This week in product design",
                "from_email": "newsletter@designweekly.example",
                "from_name": "Design Weekly",
                "reason": "low-value mail",
                "evidence": {"classification": {"category": "newsletter"}},
            },
        )
        proposal = created["proposal"]
        store.add_action_audit_event("proposal-fail", proposal["proposal_id"], "proposal_created", "policy", {})
        approve_action_proposal(
            memory_store=store,
            session_id="proposal-fail",
            proposal_id=proposal["proposal_id"],
        )

        result = execute_approved_action_proposals(
            provider=FailingArchiveProvider(),
            memory_store=store,
            session_id="proposal-fail",
        )

        self.assertEqual(1, result["failed_count"])
        self.assertEqual("failed", result["failed"][0]["status"])
        self.assertIn("archive unavailable", result["failed"][0]["error"])
        self.assertIn("execution_failed", [item["event_type"] for item in store.action_audit_events("proposal-fail")])

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


class FakeCliRuntime:
    def __init__(self, execute_results, approve_results=None):
        self.execute_results = list(execute_results)
        self.approve_results = approve_results or {}
        self.execute_calls = []
        self.approved = []
        self.rejected = []
        self.closed = False

    def execute_tool(self, name, arguments, session_id="default", trace_id=None):
        self.execute_calls.append((name, arguments, session_id))
        return self.execute_results.pop(0)

    def approve_tool(self, pending_tool_call_id):
        self.approved.append(pending_tool_call_id)
        return self.approve_results[pending_tool_call_id]

    def reject_tool(self, pending_tool_call_id):
        self.rejected.append(pending_tool_call_id)
        return {
            "ok": True,
            "rejected": True,
            "pending_tool_call_id": pending_tool_call_id,
        }

    def close(self):
        self.closed = True


class EmailCliTests(unittest.TestCase):
    def test_status_prints_diagnostic_counts(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_provider_status",
                    "result": {
                        "provider": "QQImapProvider",
                        "email": "us***@foxmail.com",
                        "host": "imap.qq.com",
                        "port": 993,
                        "mailbox": "INBOX",
                        "mailbox_display": "INBOX",
                        "message_count": 251,
                        "unread_count": 219,
                        "selected_message_count": 251,
                        "uid_search_all_count": 251,
                        "visible_mailbox_count": 3,
                        "archive_mailbox": "我的文件夹/Archive",
                        "archive_mailbox_display": "我的文件夹/Archive",
                        "archive_mailbox_exists": True,
                        "drafts_mailbox": "Drafts",
                        "drafts_mailbox_display": "Drafts",
                        "drafts_mailbox_exists": True,
                        "mailbox_counts": [
                            {
                                "name": "INBOX",
                                "selected": True,
                                "selectable": True,
                                "status_available": True,
                                "message_count": 251,
                                "unread_count": 219,
                            },
                            {
                                "name": "我的文件夹/Archive",
                                "selected": False,
                                "selectable": True,
                                "status_available": True,
                                "message_count": 1,
                                "unread_count": 0,
                            },
                            {
                                "name": "父文件夹",
                                "selected": False,
                                "selectable": True,
                                "status_available": False,
                                "message_count": None,
                                "unread_count": None,
                            },
                        ],
                    },
                }
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["status"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn("Selected mailbox EXISTS: 251", output)
        self.assertIn("Selected mailbox UID SEARCH ALL: 251", output)
        self.assertIn("* INBOX: 251 total, 219 unread", output)
        self.assertIn("- 我的文件夹/Archive: 1 total, 0 unread", output)
        self.assertIn("- 父文件夹: status unavailable", output)

    def test_recent_prints_compact_summary(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_list_recent",
                    "result": {
                        "provider": "MockEmailProvider",
                        "count": 1,
                        "emails": [
                            {
                                "id": "email-001",
                                "from_name": "Maya Chen",
                                "from_email": "maya.chen@example.com",
                                "subject": "Action required today",
                                "snippet": "Please review before 5 PM.",
                                "received_at": "2026-05-10T01:00:00+00:00",
                                "is_read": False,
                            }
                        ],
                    },
                }
            ]
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = run_cli(
            ["recent", "--limit", "1", "--unread"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_list_recent", {"limit": 1, "unread_only": True}, "email-cli")],
            runtime.execute_calls,
        )
        output = stdout.getvalue()
        self.assertIn("Provider: MockEmailProvider", output)
        self.assertIn("email-001 [unread]", output)
        self.assertIn("Action required today", output)
        self.assertEqual("", stderr.getvalue())
        self.assertTrue(runtime.closed)

    def test_proposal_commands_call_expected_tools(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_scan_proposals",
                    "result": {
                        "provider": "MockEmailProvider",
                        "fetched": 1,
                        "proposal_count": 1,
                        "created_count": 1,
                        "duplicate_count": 0,
                        "important_count": 0,
                        "review_count": 0,
                        "no_action_count": 0,
                        "proposals": [
                            {
                                "proposal_id": "proposal-001",
                                "status": "proposed",
                                "risk_level": "low",
                                "action": "archive",
                                "email_id": "email-004",
                                "from_name": "Design Weekly",
                                "from_email": "newsletter@example.com",
                                "subject": "Newsletter",
                                "reason": "low-value mail",
                            }
                        ],
                    },
                }
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["propose", "--limit", "1", "--all"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_scan_proposals", {"limit": 1, "unread_only": False}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertIn("proposal-001", stdout.getvalue())

    def test_approve_proposal_command_calls_expected_tool(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": False,
                    "tool": "email_approve_proposal",
                    "requires_approval": True,
                    "pending_tool_call_id": "pending-approve",
                    "reason": "dangerous tool requires explicit approval",
                }
            ],
            approve_results={
                "pending-approve": {
                    "ok": True,
                    "tool": "email_approve_proposal",
                    "result": {
                        "proposal": {
                            "proposal_id": "proposal-001",
                            "status": "approved",
                            "email_id": "email-004",
                        },
                        "audit_event": {"event_type": "proposal_approved"},
                    },
                }
            },
        )

        exit_code = run_cli(
            ["approve-proposal", "proposal-001"],
            runtime_factory=lambda: runtime,
            stdout=StringIO(),
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_approve_proposal", {"proposal_id": "proposal-001"}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertEqual(["pending-approve"], runtime.approved)

    def test_dangerous_command_without_yes_rejects_pending_preview(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": False,
                    "tool": "email_mark_read",
                    "requires_approval": True,
                    "pending_tool_call_id": "pending-001",
                    "reason": "dangerous tool requires explicit approval",
                }
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["mark-read", "imap-2"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_mark_read", {"email_id": "imap-2", "is_read": True}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertEqual(["pending-001"], runtime.rejected)
        self.assertEqual([], runtime.approved)
        self.assertIn("No mailbox mutation executed.", stdout.getvalue())

    def test_dangerous_command_with_yes_approves_pending_call(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": False,
                    "tool": "email_create_draft",
                    "requires_approval": True,
                    "pending_tool_call_id": "pending-002",
                    "reason": "dangerous tool requires explicit approval",
                }
            ],
            approve_results={
                "pending-002": {
                    "ok": True,
                    "tool": "email_create_draft",
                    "result": {
                        "action": "create_draft",
                        "result": {
                            "draft_id": "draft-001",
                            "source_email_id": "imap-2",
                            "to": ["maya.chen@example.com"],
                            "subject": "Re: Action required today",
                            "sent": False,
                            "drafts_mailbox": "Drafts",
                        },
                    },
                }
            },
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["draft", "imap-2", "--body", "收到，我会处理。", "--yes"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_create_draft", {"email_id": "imap-2", "body": "收到，我会处理。"}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertEqual(["pending-002"], runtime.approved)
        self.assertEqual([], runtime.rejected)
        self.assertIn("Draft created. It was not sent.", stdout.getvalue())

    def test_review_lists_classified_real_samples_without_bodies(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_list_recent",
                    "result": {
                        "provider": "QQImapProvider",
                        "count": 1,
                        "emails": [
                            {
                                "id": "imap-2",
                                "from_name": "Maya Chen",
                                "from_email": "maya.chen@example.com",
                                "subject": "Action required today",
                                "snippet": "Please review before 5 PM.",
                                "is_read": False,
                            }
                        ],
                    },
                },
                {
                    "ok": True,
                    "tool": "email_classify",
                    "result": {
                        "email": {
                            "id": "imap-2",
                            "from_name": "Maya Chen",
                            "from_email": "maya.chen@example.com",
                            "subject": "Action required today",
                            "snippet": "Please review before 5 PM.",
                        },
                        "classification": {
                            "category": "action_required",
                            "importance": "high",
                            "suggested_action": "review",
                            "is_reportable": True,
                            "is_ignored": False,
                            "reasons": ["asks for action"],
                        },
                    },
                },
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["review", "--limit", "1", "--unread"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [
                ("email_list_recent", {"limit": 1, "unread_only": True}, "email-cli"),
                ("email_classify", {"email_id": "imap-2"}, "email-cli"),
            ],
            runtime.execute_calls,
        )
        output = stdout.getvalue()
        self.assertIn("imap-2 [high/action_required/report]", output)
        self.assertIn("Action required today", output)
        self.assertNotIn("Body:", output)

    def test_review_interactive_labeling_saves_labels_inline(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_list_recent",
                    "result": {
                        "provider": "QQImapProvider",
                        "count": 2,
                        "emails": [
                            {
                                "id": "imap-2",
                                "from_name": "Maya Chen",
                                "from_email": "maya.chen@example.com",
                                "subject": "Action required today",
                                "snippet": "Please review before 5 PM.",
                                "body": "This body must not be saved.",
                            },
                            {
                                "id": "imap-3",
                                "from_name": "News",
                                "from_email": "news@example.com",
                                "subject": "Weekly update",
                                "snippet": "Here is the weekly update.",
                                "body": "This second body must not be saved.",
                            },
                        ],
                    },
                },
                {
                    "ok": True,
                    "tool": "email_classify",
                    "result": {
                        "email": {
                            "id": "imap-2",
                            "from_email": "maya.chen@example.com",
                            "subject": "Action required today",
                            "snippet": "Please review before 5 PM.",
                        },
                        "classification": {
                            "category": "action_required",
                            "importance": "high",
                            "suggested_action": "review",
                            "is_reportable": True,
                            "is_ignored": False,
                            "reasons": ["asks for action"],
                        },
                    },
                },
                {
                    "ok": True,
                    "tool": "email_classify",
                    "result": {
                        "email": {
                            "id": "imap-3",
                            "from_email": "news@example.com",
                            "subject": "Weekly update",
                            "snippet": "Here is the weekly update.",
                        },
                        "classification": {
                            "category": "newsletter",
                            "importance": "low",
                            "suggested_action": "ignore",
                            "is_reportable": False,
                            "is_ignored": True,
                            "reasons": ["newsletter/unsubscribe signal"],
                        },
                    },
                },
            ]
        )
        answers = iter(["i", "n"])
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_labels.json"
            stdout = StringIO()

            exit_code = run_cli(
                ["review", "--limit", "2", "--label", "--labels-path", str(labels_path)],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
                input_func=lambda _prompt: next(answers),
            )

            self.assertEqual(0, exit_code)
            data = load_real_labels(labels_path)
            self.assertEqual("important", data["labels"]["imap-2"]["label"])
            self.assertEqual("ignore", data["labels"]["imap-3"]["label"])
            raw = labels_path.read_text(encoding="utf-8")
            self.assertNotIn("This body must not be saved.", raw)
            self.assertNotIn("This second body must not be saved.", raw)

        output = stdout.getvalue()
        self.assertIn("Saved imap-2 -> important", output)
        self.assertIn("Saved imap-3 -> ignore", output)

    def test_label_saves_summary_and_prediction_without_body(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_classify",
                    "result": {
                        "email": {
                            "id": "imap-2",
                            "from_email": "maya.chen@example.com",
                            "subject": "Action required today",
                        },
                        "classification": {
                            "category": "action_required",
                            "importance": "high",
                            "suggested_action": "review",
                            "is_reportable": True,
                            "is_ignored": False,
                        },
                    },
                }
            ]
        )
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_labels.json"
            stdout = StringIO()

            exit_code = run_cli(
                ["label", "imap-2", "important", "--labels-path", str(labels_path)],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

            self.assertEqual(0, exit_code)
            self.assertEqual(
                [("email_classify", {"email_id": "imap-2"}, "email-cli")],
                runtime.execute_calls,
            )
            self.assertTrue(labels_path.exists())
            raw = labels_path.read_text(encoding="utf-8")
            self.assertIn("Action required today", raw)
            data = load_real_labels(labels_path)
            record = data["labels"]["imap-2"]
            self.assertEqual("important", record["label"])
            self.assertEqual("action_required", record["predicted_category"])

    def test_eval_real_reports_metrics_from_saved_labels(self) -> None:
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_labels.json"
            save_real_label(
                labels_path,
                email_id="imap-1",
                label="important",
                predicted_reportable=True,
                predicted_ignored=False,
                predicted_category="action_required",
                predicted_importance="high",
            )
            save_real_label(
                labels_path,
                email_id="imap-2",
                label="ignore",
                predicted_reportable=False,
                predicted_ignored=True,
                predicted_category="newsletter",
                predicted_importance="low",
            )

            stdout = StringIO()
            exit_code = run_cli(
                ["eval-real", "--labels-path", str(labels_path)],
                runtime_factory=lambda: FakeCliRuntime([]),
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn("Sample count: 2", output)
        self.assertIn("important_recall: 1.0", output)
        self.assertIn("noise_filter_precision: 1.0", output)


class FakeHttpResponse:
    def __init__(self, body=None, lines=None):
        self.body = body if body is not None else b""
        self.lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if isinstance(self.body, bytes):
            return self.body
        return json_dumps_bytes(self.body)

    def __iter__(self):
        return iter(self.lines)


class FakeHttpTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected extra HTTP request")
        return self.responses.pop(0)


def json_dumps_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


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


class SQLiteRuntimePersistenceTests(unittest.TestCase):
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


class FakeImapClient:
    def __init__(self, messages, mailbox_messages=None):
        self.messages = messages
        self.mailbox_messages = mailbox_messages or {"INBOX": messages}
        self.actions = []
        self.selected = None

    def login(self, user, password):
        self.actions.append(("login", user, password))
        return "OK", [b"logged in"]

    def logout(self):
        self.actions.append(("logout",))
        return "OK", [b"logged out"]

    def list(self, directory='""', pattern='"*"'):
        self.actions.append(("list", directory, pattern))
        return "OK", [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasNoChildren) "/" "&UXZO1mWHTvZZOQ-"',
            b'(\\HasNoChildren) "/" "Archive"',
            b'(\\HasNoChildren \\Drafts) "/" "Drafts"',
        ]

    def select(self, mailbox="INBOX", readonly=False):
        mailbox = str(mailbox).strip('"')
        self.selected = (mailbox, readonly)
        self.actions.append(("select", mailbox, readonly))
        messages = self.mailbox_messages.get(mailbox, {})
        return "OK", [str(len(messages)).encode()]

    def status(self, mailbox, names):
        mailbox = str(mailbox).strip('"')
        self.actions.append(("status", mailbox, names))
        messages = self.mailbox_messages.get(mailbox, {})
        unseen = sum(1 for item in messages.values() if "\\Seen" not in item["flags"])
        return "OK", [f'{mailbox} (MESSAGES {len(messages)} UNSEEN {unseen})'.encode()]

    def uid(self, command, *args):
        normalized = command.upper()
        self.actions.append(("uid", normalized, *args))
        if normalized == "SEARCH":
            criteria = args[1:]
            messages = self.mailbox_messages.get(self.selected[0], self.messages)
            ids = [message_id.encode("ascii") for message_id in sorted(messages, key=int)]
            if "UNSEEN" in criteria:
                ids = [
                    message_id.encode("ascii")
                    for message_id, item in sorted(messages.items(), key=lambda entry: int(entry[0]))
                    if "\\Seen" not in item["flags"]
                ]
            return "OK", [b" ".join(ids)]
        if normalized == "FETCH":
            message_set = str(args[0])
            item = self.messages[message_set]
            flags = " ".join(sorted(item["flags"]))
            return "OK", [(f'{message_set} (FLAGS ({flags}) RFC822 {{{len(item["raw"])}}}'.encode(), item["raw"])]
        if normalized == "STORE":
            message_set, store_command, flags = str(args[0]), args[1], args[2]
            target = self.messages[message_set]["flags"]
            for flag in flags.strip("()").split():
                if store_command.startswith("+"):
                    target.add(flag)
                elif store_command.startswith("-"):
                    target.discard(flag)
            return "OK", [b"stored"]
        if normalized == "COPY":
            return "OK", [b"copied"]
        raise AssertionError(f"unexpected uid command: {command}")

    def expunge(self):
        self.actions.append(("expunge",))
        return "OK", [b"expunged"]

    def append(self, mailbox, flags, date_time, message):
        self.actions.append(("append", mailbox, flags, date_time, message))
        return "OK", [b"appended"]


def _raw_imap_message(subject="Action required today", body="Please review before 5 PM.", *, html=False):
    message = OutboundEmailMessage()
    message["From"] = "Maya Chen <maya.chen@example.com>"
    message["To"] = "Alex <alex@example.com>"
    message["Subject"] = subject
    message["Date"] = "Sun, 10 May 2026 09:00:00 +0800"
    message["Message-ID"] = "<message-001@example.com>"
    if html:
        message.set_content(f"<html><body><p>{body}</p><script>x()</script></body></html>", subtype="html")
    else:
        message.set_content(body)
    return message.as_bytes()


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
