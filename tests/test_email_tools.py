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


if __name__ == "__main__":
    unittest.main()
