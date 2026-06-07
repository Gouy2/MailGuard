"""Tests for the inbox cleaner dry-run workflow."""

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
from server.app.cleaner.preview import run_clean_preview
from server.app.email_provider import EmailMessage
from server.app.memory import MemoryStore
from tests.fakes import FakeCliRuntime


class CleanerProvider:
    def __init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.archived = False
        self.emails = [
            EmailMessage(
                id="promo-001",
                thread_id="thread-promo-001",
                from_name="Deals",
                from_email="deals@example.com",
                to=["alex@example.com"],
                subject="Flash sale coupon 50% off",
                snippet="Limited time promotion. Unsubscribe here.",
                body="Promotion body",
                received_at=now,
                labels=["inbox", "promotion"],
                is_read=True,
            ),
            EmailMessage(
                id="security-001",
                thread_id="thread-security-001",
                from_name="Deals Security",
                from_email="deals@example.com",
                to=["alex@example.com"],
                subject="Security password reset required today",
                snippet="Please verify your account access.",
                body="Security body",
                received_at=now,
                labels=["inbox"],
                is_read=False,
            ),
            EmailMessage(
                id="news-001",
                thread_id="thread-news-001",
                from_name="Weekly News",
                from_email="weekly@news.example",
                to=["alex@example.com"],
                subject="Weekly newsletter roundup",
                snippet="Product updates and unsubscribe link.",
                body="Newsletter body",
                received_at=now,
                labels=["inbox", "newsletter"],
                is_read=True,
            ),
        ]

    def status(self) -> dict[str, Any]:
        return {
            "provider": "CleanerProvider",
            "status": "available",
            "mailbox": "INBOX",
            "message_count": len(self.emails),
            "unread_count": 1,
        }

    def list_recent(self, limit: int = 20, unread_only: bool = False) -> list[EmailMessage]:
        emails = [email for email in self.emails if not unread_only or not email.is_read]
        return emails[:limit]

    def archive(self, email_id: str) -> dict[str, Any]:
        self.archived = True
        return {"archived": True, "email_id": email_id}


class CleanerTests(unittest.TestCase):
    def test_confirmed_sender_is_auto_eligible_but_protected_mail_is_blocked(self) -> None:
        provider = CleanerProvider()
        memory_store = MemoryStore()
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [{"memory_type": "archive_sender", "value": "deals@example.com"}])
            result = run_clean_preview(
                provider=provider,
                memory_store=memory_store,
                session_id="cleaner",
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
            )
            artifact = json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["auto_eligible_count"])
        self.assertEqual("promo-001", result["auto_eligible"][0]["email_id"])
        self.assertEqual("archive_sender:deals@example.com", result["auto_eligible"][0]["memory_match"])
        self.assertEqual("confirmed_memory", result["auto_eligible"][0]["automation_authority"])
        protected_ids = {item["email_id"] for item in result["protected"]}
        self.assertIn("security-001", protected_ids)
        blocked = next(item for item in result["protected"] if item["email_id"] == "security-001")
        self.assertTrue(blocked["auto_eligible_blocked"])
        self.assertFalse(provider.archived)
        self.assertFalse(artifact["mailbox_mutation"])
        self.assertFalse(artifact["proposal_mutation"])
        self.assertFalse(artifact["audit_mutation"])
        self.assertFalse(artifact["llm_authorization"])
        self.assertEqual([], memory_store.action_proposals("cleaner"))
        self.assertEqual([], memory_store.action_audit_events("cleaner"))

    def test_category_memory_and_strict_policy_do_not_authorize_auto_eligible(self) -> None:
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [{"memory_type": "archive_category", "value": "newsletter"}])
            result = run_clean_preview(
                provider=CleanerProvider(),
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(0, result["auto_eligible_count"])
        candidate_ids = {item["email_id"] for item in result["candidates"]}
        self.assertIn("promo-001", candidate_ids)
        self.assertIn("news-001", candidate_ids)

    def test_important_preference_blocks_confirmed_archive_sender(self) -> None:
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [{"memory_type": "archive_sender", "value": "deals@example.com"}])
            result = run_clean_preview(
                provider=CleanerProvider(),
                preferences={"important_senders": ["deals@example.com"]},
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(0, result["auto_eligible_count"])
        self.assertGreaterEqual(result["protected_count"], 2)

    def test_clean_cli_and_preset_render_dry_run(self) -> None:
        fake_run = {
            "run_id": "clean-test",
            "status": "ok",
            "execution_mode": "dry_run",
            "provider": {"provider": "CleanerProvider", "mailbox": "INBOX"},
            "artifact_path": "/tmp/clean-test.json",
            "fetched": 1,
            "auto_eligible_count": 1,
            "protected_count": 0,
            "candidate_count": 0,
            "no_action_count": 0,
            "auto_eligible": [
                {
                    "item_type": "auto_eligible",
                    "status": "dry_run",
                    "risk_level": "auto_eligible_low",
                    "action": "archive",
                    "email_id": "promo-001",
                    "from_email": "deals@example.com",
                    "subject": "Flash sale coupon",
                    "reason": "confirmed memory",
                }
            ],
            "candidates": [],
            "protected": [],
            "mailbox_mutation": False,
            "proposal_mutation": False,
            "llm_authorization": False,
            "error": "",
        }
        stdout = StringIO()
        runtime = FakeCliRuntime([])

        with patch("server.email_cli.run_clean_preview", return_value=fake_run) as mocked:
            exit_code = run_cli(
                ["clean", "--limit", "5", "--hours", "48"],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        mocked.assert_called_once()
        self.assertEqual(5, mocked.call_args.kwargs["limit"])
        self.assertEqual(48, mocked.call_args.kwargs["hours"])
        self.assertEqual([], runtime.execute_calls)
        output = stdout.getvalue()
        self.assertIn("Run: clean-test [ok]", output)
        self.assertIn("auto-eligible: 1", output)
        self.assertIn("promo-001", output)


def _write_memory(path: Path, entries: list[dict[str, str]]) -> None:
    proposals = {}
    for index, entry in enumerate(entries, start=1):
        proposal_id = f"memory-{index}"
        proposals[proposal_id] = {
            "proposal_id": proposal_id,
            "memory_type": entry["memory_type"],
            "value": entry["value"],
            "status": "approved",
        }
    path.write_text(json.dumps({"schema_version": 1, "proposals": proposals}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
