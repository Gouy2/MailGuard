"""Regression tests for the mock email triage tools."""

from __future__ import annotations

import unittest

from server.app.agent import AgentRuntime
from server.app.email_provider import MockEmailProvider
from server.app.email_tools import classify_email


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


class EmailToolRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = AgentRuntime.create()

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

    def test_email_report_important(self) -> None:
        response = self.runtime.execute_tool_for_test("email_report_important", {"limit": 12})
        self.assertTrue(response["ok"])

        result = response["result"]
        self.assertEqual(8, result["important_count"])
        self.assertEqual(4, result["ignored_count"])
        self.assertEqual({"newsletter": 2, "noise": 1, "promotion": 1}, result["ignored_summary"])

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
        self.assertEqual(8, baseline["result"]["important_count"])
        self.assertEqual(4, baseline["result"]["ignored_count"])

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
        self.assertEqual(7, updated["result"]["important_count"])
        self.assertEqual(5, updated["result"]["ignored_count"])


if __name__ == "__main__":
    unittest.main()
