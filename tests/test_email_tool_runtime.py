"""Regression tests for MailGuard email tool runtime."""

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
from server.app.archive import ActionAuditEvent, ActionProposal, ArchivePlan, build_archive_plan
from server.app.email_eval import evaluate_email_classifier
from server.app.email_provider import MockEmailProvider
from server.app.email_proposals import (
    approve_action_proposal,
    execute_approved_action_proposals,
    plan_archive_actions,
    scan_action_proposals,
)
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

class EmailToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_patcher = patch.dict(
            os.environ,
            {
                "MAILGUARD_EMAIL_PROVIDER": "mock",
                "MAILGUARD_STATE_DB": "",
                "MAILGUARD_MEMORY_PROPOSALS": "off",
            },
        )
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
        self.assertIn("email_eval_proposals", names)
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

    def test_archive_proposal_policy_eval_baseline(self) -> None:
        result = evaluate_archive_proposal_policy(
            provider=MockEmailProvider(),
            classifier=classify_email,
            limit=36,
        )

        self.assertEqual(36, result["sample_count"])
        self.assertEqual(7, result["proposal_count"])
        self.assertEqual(13, result["eligible_safe_archive_count"])
        self.assertEqual(1.0, result["metrics"]["archive_proposal_precision"])
        self.assertEqual(0.5385, result["metrics"]["archive_proposal_recall"])
        self.assertEqual(0, result["metrics"]["false_positive_count"])
        self.assertEqual(0, result["metrics"]["important_false_positive_count"])
        self.assertEqual(
            ["email-033", "email-029", "email-025", "email-022", "email-011", "email-005", "email-004"],
            [item["email_id"] for item in result["proposals"]],
        )

    def test_archive_proposal_policy_eval_respects_important_preferences(self) -> None:
        result = evaluate_archive_proposal_policy(
            provider=MockEmailProvider(),
            classifier=classify_email,
            preferences={"important_senders": ["updates@learning.example"]},
            limit=36,
        )

        self.assertNotIn("email-033", [item["email_id"] for item in result["proposals"]])
        self.assertEqual(6, result["proposal_count"])
        self.assertEqual(12, result["eligible_safe_archive_count"])
        self.assertEqual(1.0, result["metrics"]["archive_proposal_precision"])

    def test_proposal_evaluation_tool_is_readonly_and_compact(self) -> None:
        result = self.runtime.execute_tool_for_test("email_eval_proposals", {"limit": 36})

        self.assertTrue(result["ok"])
        self.assertEqual("read", result["permission"])
        self.assertEqual("MockEmailProvider", result["result"]["provider"])
        self.assertFalse(result["result"]["mailbox_mutation"])
        self.assertEqual(7, result["result"]["proposal_count"])
        self.assertEqual(36, result["result"]["rows_omitted"])
        self.assertNotIn("rows", result["result"])

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

    def test_scan_proposals_creates_archive_candidates_and_protected_items(self) -> None:
        response = self.runtime.execute_tool_for_test(
            "email_scan_proposals",
            {"limit": 12, "unread_only": False},
            session_id="proposal-scan",
        )

        self.assertTrue(response["ok"])
        result = response["result"]
        proposal_ids = {item["email_id"] for item in result["proposals"]}
        protected_ids = {item["email_id"] for item in result["protected"]}
        candidate_ids = {item["email_id"] for item in result["candidates"]}
        self.assertIn("email-033", proposal_ids)
        self.assertIn("email-029", proposal_ids)
        self.assertIn("email-035", protected_ids)
        self.assertIn("email-034", protected_ids)
        self.assertIn("email-036", candidate_ids)
        self.assertIn("email-031", candidate_ids)
        self.assertEqual(result["proposal_count"], result["created_count"])

        audit = self.runtime.execute_tool_for_test(
            "email_audit_log",
            {},
            session_id="proposal-scan",
        )
        self.assertTrue(audit["ok"])
        self.assertEqual(result["created_count"], audit["result"]["count"])
        self.assertTrue(all(item["event_type"] == "proposal_created" for item in audit["result"]["events"]))

    def test_plan_archive_actions_is_readonly_and_does_not_persist_proposals(self) -> None:
        store = MemoryStore()
        provider = MockEmailProvider()
        plan = plan_archive_actions(
            emails=provider.list_recent(limit=12, unread_only=False),
            classifier=classify_email,
            preferences=store.email_preferences("plan-only"),
            provider_name=type(provider).__name__,
        )

        self.assertEqual("MockEmailProvider", plan["provider"])
        self.assertEqual(12, plan["fetched"])
        self.assertEqual(3, plan["planned_count"])
        self.assertIn("planned", plan)
        self.assertFalse(plan["mailbox_mutation"])
        self.assertFalse(plan["state_mutation"])
        self.assertEqual([], store.action_proposals("plan-only"))
        self.assertEqual([], store.action_audit_events("plan-only"))
        self.assertTrue(all("proposal_id" not in item for item in plan["planned"]))

    def test_archive_core_plan_has_typed_boundary_and_dict_compatibility(self) -> None:
        provider = MockEmailProvider()
        emails = provider.list_recent(limit=12, unread_only=False)

        core_plan = build_archive_plan(
            emails=emails,
            classifier=classify_email,
            preferences={},
            provider_name=type(provider).__name__,
        )

        self.assertIsInstance(core_plan, ArchivePlan)
        self.assertEqual(12, core_plan.fetched)
        self.assertEqual(3, core_plan.planned_count)
        self.assertEqual("email-033", core_plan.planned[0].email.email_id)
        self.assertEqual("propose_archive", core_plan.planned[0].policy.decision)

        compatible = core_plan.to_dict()
        self.assertEqual(3, compatible["planned_count"])
        self.assertFalse(compatible["mailbox_mutation"])
        self.assertFalse(compatible["state_mutation"])
        self.assertTrue(all("proposal_id" not in item for item in compatible["planned"]))

    def test_archive_core_keeps_raw_classification_evidence_separate_from_policy_normalization(self) -> None:
        provider = MockEmailProvider()

        def uppercase_classifier(email: Any, preferences: dict[str, Any] | None) -> dict[str, Any]:
            return {
                "category": "Newsletter",
                "importance": "LOW",
                "suggested_action": "IGNORE",
                "is_reportable": False,
                "is_ignored": True,
                "reasons": ["Synthetic uppercase classifier output"],
                "signals": {},
            }

        core_plan = build_archive_plan(
            emails=provider.list_recent(limit=1, unread_only=False),
            classifier=uppercase_classifier,
            preferences={},
            provider_name=type(provider).__name__,
        )
        planned = core_plan.to_dict()["planned"][0]

        self.assertEqual("Newsletter", planned["evidence"]["classification"]["category"])
        self.assertEqual("newsletter", planned["evidence"]["policy"]["category"])

    def test_archive_action_models_define_formal_state_boundary(self) -> None:
        proposal = ActionProposal.normalize(
            {
                "action": "archive",
                "email_id": "email-004",
                "thread_id": "thread-004",
                "subject": "Newsletter",
                "from_email": "newsletter@example.com",
                "from_name": "Newsletter",
                "reason": "low-value mail",
                "evidence": {
                    "classification": {"category": "newsletter"},
                    "policy": {"decision": "propose_archive"},
                    "email": {"snippet": "Weekly product notes."},
                },
            }
        )
        audit_event = ActionAuditEvent.new(
            proposal.proposal_id,
            "proposal_created",
            "policy",
            {"action": proposal.action, "email_id": proposal.email_id},
        )

        self.assertEqual("proposed", proposal.status)
        self.assertEqual("proposal", proposal.summary_dict()["item_type"])
        self.assertEqual("Weekly product notes.", proposal.summary_dict()["snippet"])
        self.assertEqual("proposal_created", audit_event.event_type)
        self.assertEqual(proposal.proposal_id, audit_event.proposal_id)

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
        self.assertEqual(23, result["protected_count"])
        self.assertEqual(10, result["protected_returned_count"])
        self.assertLessEqual(result["protected_returned_count"], result["protected_count"])
        self.assertEqual(6, result["candidate_count"])
        self.assertEqual(6, result["candidate_returned_count"])
        self.assertLessEqual(result["candidate_returned_count"], result["candidate_count"])
        candidate_ids = {item["email_id"] for item in result["candidates"]}
        self.assertIn("email-024", candidate_ids)
        self.assertIn("email-009", candidate_ids)

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
        protected_ids = {item["email_id"] for item in response["result"]["protected"]}
        self.assertNotIn("email-033", proposal_ids)
        self.assertIn("email-033", protected_ids)

    def test_confirmed_memory_promotes_candidate_but_not_protected_mail(self) -> None:
        result = scan_action_proposals(
            provider=MockEmailProvider(),
            memory_store=MemoryStore(),
            session_id="confirmed-memory-scan",
            classifier=classify_email,
            limit=12,
            unread_only=False,
            confirmed_memory={
                "archive_senders": ["noreply@survey.example", "billing@domains.example"],
                "archive_domains": [],
                "archive_categories": ["finance"],
            },
        )

        proposal_ids = {item["email_id"] for item in result["proposals"]}
        candidate_ids = {item["email_id"] for item in result["candidates"]}
        protected_ids = {item["email_id"] for item in result["protected"]}
        promoted = next(item for item in result["proposals"] if item["email_id"] == "email-031")

        self.assertIn("email-031", proposal_ids)
        self.assertNotIn("email-031", candidate_ids)
        self.assertIn("confirmed memory promotes", promoted["reason"])
        self.assertNotIn("email-035", proposal_ids)
        self.assertIn("email-035", protected_ids)

    def test_scan_proposals_reads_confirmed_memory_file_at_runtime(self) -> None:
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory_proposals.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "proposals": {
                            "memory-archive-sender": {
                                "status": "approved",
                                "memory_type": "archive_sender",
                                "value": "noreply@survey.example",
                            },
                            "memory-archive-category": {
                                "status": "approved",
                                "memory_type": "archive_category",
                                "value": "finance",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"MAILGUARD_MEMORY_PROPOSALS": str(memory_path)}):
                response = self.runtime.execute_tool_for_test(
                    "email_scan_proposals",
                    {"limit": 12, "unread_only": False},
                    session_id="proposal-memory-file",
                )

        self.assertTrue(response["ok"])
        proposal_ids = {item["email_id"] for item in response["result"]["proposals"]}
        protected_ids = {item["email_id"] for item in response["result"]["protected"]}
        promoted = next(item for item in response["result"]["proposals"] if item["email_id"] == "email-031")
        self.assertIn("email-031", proposal_ids)
        self.assertIn("confirmed memory promotes", promoted["reason"])
        self.assertNotIn("email-035", proposal_ids)
        self.assertIn("email-035", protected_ids)

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
