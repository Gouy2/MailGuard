"""Tests for the daily read-only report agent."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from server.email_cli import run_cli
from server.app.daily_report.models import Action, Budget, Run, now_iso
from server.app.daily_report.planner import OpenAIPlanner
from server.app.daily_report.runner import run_daily_report
from server.app.daily_report.tools import DailyTools
from server.app.email_provider import EmailMessage
from tests.fakes import FakeChatMessage, FakeChatResponse, FakeCliRuntime, FakeOpenAIClient


class DailyReportProvider:
    def __init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.archived = False
        self.marked = False
        self.starred = False
        self.drafted = False
        self.emails = [
            EmailMessage(
                id="email-001",
                thread_id="thread-001",
                from_name="Maya Chen",
                from_email="maya@example.com",
                to=["alex@example.com"],
                subject="Action required today",
                snippet="Please review the interview plan before 5 PM.",
                body="SECRET " + ("long text " * 80) + "UNSAFE_TAIL",
                received_at=now,
                labels=["inbox"],
                is_read=False,
            ),
            EmailMessage(
                id="email-002",
                thread_id="thread-002",
                from_name="Design Weekly",
                from_email="newsletter@example.com",
                to=["alex@example.com"],
                subject="Weekly links",
                snippet="A normal newsletter.",
                body="Newsletter body",
                received_at=now,
                labels=["inbox"],
                is_read=False,
            ),
        ]

    def status(self) -> dict[str, Any]:
        return {
            "provider": "DailyReportProvider",
            "status": "available",
            "mailbox": "INBOX",
            "message_count": len(self.emails),
            "unread_count": len(self.emails),
        }

    def list_recent(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        return self.emails[:limit]

    def get_detail(self, email_id: str) -> EmailMessage:
        for email in self.emails:
            if email.id == email_id:
                return email
        raise KeyError(email_id)

    def search(self, query: str, limit: int = 20) -> list[EmailMessage]:
        query = query.lower()
        return [
            email
            for email in self.emails
            if query in email.subject.lower() or query in email.snippet.lower()
        ][:limit]

    def archive(self, email_id: str) -> dict[str, Any]:
        self.archived = True
        return {"archived": True}

    def mark_read(self, email_id: str, is_read: bool = True) -> dict[str, Any]:
        self.marked = True
        return {"is_read": is_read}

    def star(self, email_id: str, starred: bool = True) -> dict[str, Any]:
        self.starred = True
        return {"starred": starred}

    def create_draft(self, email_id: str, body: str, to: list[str] | None = None) -> dict[str, Any]:
        self.drafted = True
        return {"drafted": True}


class InvalidPlanner:
    label = "invalid"

    def next_action(self, run):
        return Action("archive", {"email_id": "email-001"})


class LoopPlanner:
    label = "loop"

    def next_action(self, run):
        return Action("list_recent", {"limit": 1})


class DetailPlanner:
    label = "detail"

    def next_action(self, run):
        if not run.steps:
            return Action("get_detail", {"email_id": "email-001", "max_chars": 120})
        return Action(
            "finish",
            {
                "report": "One key email needs review.",
                "items": [
                    {
                        "email_id": "email-001",
                        "subject": "Action required today",
                        "from_email": "maya@example.com",
                        "reason": "Interview plan needs review.",
                        "priority": "high",
                    }
                ],
            },
        )


class DailyReportTests(unittest.TestCase):
    def test_mock_planner_generates_report_artifact_without_mutation(self) -> None:
        provider = DailyReportProvider()
        with TemporaryDirectory() as temp_dir:
            result = run_daily_report(provider=provider, llm="mock", output_dir=temp_dir, limit=2)
            artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))

        self.assertEqual("ok", result["status"])
        self.assertEqual("mock", result["planner"])
        self.assertEqual(2, len(result["steps"]))
        self.assertEqual("list_recent", result["steps"][0]["action"])
        self.assertEqual("finish", result["steps"][1]["action"])
        self.assertEqual("email-001", result["items"][0]["email_id"])
        self.assertEqual("INBOX", artifact["provider"]["mailbox"])
        self.assertFalse(provider.archived)
        self.assertFalse(provider.marked)
        self.assertFalse(provider.starred)
        self.assertFalse(provider.drafted)

    def test_invalid_action_is_rejected_and_saved(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = run_daily_report(
                provider=DailyReportProvider(),
                planner=InvalidPlanner(),
                output_dir=temp_dir,
            )
            artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))

        self.assertEqual("error", result["status"])
        self.assertIn("unsupported action: archive", result["error"])
        self.assertEqual("archive", artifact["steps"][0]["action"])
        self.assertIn("unsupported action", artifact["steps"][0]["error"])

    def test_max_steps_exceeded_is_recorded(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = run_daily_report(
                provider=DailyReportProvider(),
                planner=LoopPlanner(),
                output_dir=temp_dir,
                max_steps=1,
            )
            artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))

        self.assertEqual("error", result["status"])
        self.assertEqual("max_steps_exceeded", result["error"])
        self.assertEqual(1, len(artifact["steps"]))

    def test_daily_tools_expose_no_mailbox_mutations(self) -> None:
        provider = DailyReportProvider()
        tools = DailyTools(provider, budget=Budget(limit=1))

        with self.assertRaises(ValueError):
            tools.execute(Action("email_archive", {"email_id": "email-001"}))

        self.assertFalse(provider.archived)
        self.assertFalse(provider.marked)
        self.assertFalse(provider.starred)
        self.assertFalse(provider.drafted)

    def test_detail_artifact_does_not_store_full_body_or_thought(self) -> None:
        with TemporaryDirectory() as temp_dir:
            result = run_daily_report(
                provider=DailyReportProvider(),
                planner=DetailPlanner(),
                output_dir=temp_dir,
                max_steps=3,
            )
            raw = Path(result["artifact_path"]).read_text(encoding="utf-8")

        self.assertEqual("ok", result["status"])
        self.assertNotIn("UNSAFE_TAIL", raw)
        self.assertNotIn('"body"', raw)
        self.assertNotIn("Thought", raw)

    def test_openai_planner_returns_structured_action(self) -> None:
        client = FakeOpenAIClient(
            [
                FakeChatResponse(
                    FakeChatMessage(
                        json.dumps(
                            {
                                "action": "finish",
                                "args": {
                                    "report": "No urgent mail.",
                                    "items": [],
                                },
                            }
                        )
                    )
                )
            ]
        )
        planner = OpenAIPlanner(client=client, model="test-model")
        action = planner.next_action(
            Run(
                run_id="daily-test",
                status="running",
                started_at=now_iso(),
                provider={"provider": "DailyReportProvider"},
                budget=Budget(limit=1),
            )
        )

        self.assertEqual("finish", action.name)
        self.assertEqual("No urgent mail.", action.args["report"])
        self.assertEqual("test-model", client.calls[0]["model"])
        self.assertIn("response_format", client.calls[0])

    def test_daily_report_cli_and_preset_render_mock_result(self) -> None:
        fake_run = {
            "run_id": "daily-test",
            "status": "ok",
            "started_at": "2026-06-07T00:00:00+00:00",
            "finished_at": "2026-06-07T00:00:01+00:00",
            "planner": "mock",
            "provider": {"provider": "DailyReportProvider", "mailbox": "INBOX"},
            "budget": {"limit": 5, "hours": 24, "max_steps": 8},
            "steps": [{"action": "list_recent"}, {"action": "finish"}],
            "items": [],
            "report": "Checked 0 recent emails.",
            "error": "",
            "artifact_path": "/tmp/daily-test.json",
        }
        runtime = FakeCliRuntime([])
        stdout = StringIO()

        with patch("server.email_cli.run_daily_report", return_value=fake_run) as mocked:
            exit_code = run_cli(
                ["daily", "--llm", "mock", "--limit", "5"],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        mocked.assert_called_once()
        self.assertEqual("mock", mocked.call_args.kwargs["llm"])
        self.assertEqual(5, mocked.call_args.kwargs["limit"])
        self.assertEqual([], runtime.execute_calls)
        self.assertIn("Run: daily-test [ok]", stdout.getvalue())
        self.assertIn("Artifact: /tmp/daily-test.json", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
