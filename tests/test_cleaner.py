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
from server.app.cleaner.rules import enable_rule, proposed_rule
from server.app.cleaner.run import run_clean_execution
from server.app.cleaner.teach import run_teach_workflow
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

    def test_enabled_clean_rule_authorizes_auto_eligible_and_marks_blocked_protected_mail(self) -> None:
        provider = CleanerProvider()
        memory_store = MemoryStore()
        rule = enable_rule(
            proposed_rule(
                action="archive",
                scope="domain",
                value="example.com",
                source="test",
                reason="user approved example.com cleanup",
            )
        )
        memory_store.save_clean_rule("cleaner", rule)
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [])
            result = run_clean_preview(
                provider=provider,
                memory_store=memory_store,
                session_id="cleaner",
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
            )

        self.assertEqual("ok", result["status"])
        self.assertEqual(1, result["enabled_clean_rule_count"])
        self.assertEqual(1, result["auto_eligible_count"])
        self.assertEqual("clean_rule", result["auto_eligible"][0]["automation_authority"])
        self.assertEqual(rule["rule_id"], result["auto_eligible"][0]["clean_rule_match"]["rule_id"])
        blocked = next(item for item in result["protected"] if item["email_id"] == "security-001")
        self.assertTrue(blocked["auto_eligible_blocked"])
        self.assertEqual(rule["rule_id"], blocked["clean_rule_match"]["rule_id"])
        self.assertFalse(provider.archived)

    def test_protect_rule_has_priority_over_archive_rule(self) -> None:
        provider = CleanerProvider()
        memory_store = MemoryStore()
        archive_rule = enable_rule(
            proposed_rule(
                action="archive",
                scope="domain",
                value="news.example",
                source="test",
                reason="user approved newsletter cleanup",
            )
        )
        protect_rule = enable_rule(
            proposed_rule(
                action="protect",
                scope="keyword",
                value="weekly",
                source="test",
                reason="user wants weekly mail protected",
            )
        )
        memory_store.save_clean_rule("cleaner", archive_rule)
        memory_store.save_clean_rule("cleaner", protect_rule)
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [])
            result = run_clean_preview(
                provider=provider,
                memory_store=memory_store,
                session_id="cleaner",
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
            )

        self.assertEqual("ok", result["status"])
        auto_ids = {item["email_id"] for item in result["auto_eligible"]}
        self.assertNotIn("news-001", auto_ids)
        protected = next(item for item in result["protected"] if item["email_id"] == "news-001")
        self.assertTrue(protected["auto_eligible_blocked"])
        self.assertEqual(protect_rule["rule_id"], protected["clean_rule_match"]["rule_id"])

    def test_teach_workflow_creates_proposed_rules_with_impact_preview(self) -> None:
        memory_store = MemoryStore()
        result = run_teach_workflow(
            instruction="以后 Facebook 通知都归档，但安全邮件不要动",
            provider=CleanerProvider(),
            memory_store=memory_store,
            session_id="cleaner",
            limit=10,
        )

        self.assertFalse(result["mailbox_mutation"])
        self.assertFalse(result["llm_authorization"])
        self.assertGreaterEqual(result["rule_count"], 2)
        rules = memory_store.clean_rules("cleaner", limit=0)
        self.assertEqual(result["rule_count"], len(rules))
        rule_keys = {(item["action"], item["scope"], item["value"]) for item in rules}
        self.assertIn(("archive", "domain", "facebookmail.com"), rule_keys)
        self.assertNotIn(("archive", "category", "noise"), rule_keys)
        self.assertIn("impact", result)

    def test_clean_execution_requires_explicit_execute_and_writes_no_audit_in_preview_mode(self) -> None:
        provider = CleanerProvider()
        memory_store = MemoryStore()
        rule = enable_rule(
            proposed_rule(
                action="archive",
                scope="domain",
                value="example.com",
                source="test",
                reason="user approved example.com cleanup",
            )
        )
        memory_store.save_clean_rule("cleaner", rule)
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [])
            result = run_clean_execution(
                provider=provider,
                memory_store=memory_store,
                session_id="cleaner",
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
                execute=False,
            )

        self.assertEqual("approval_required", result["execution_mode"])
        self.assertTrue(result["requires_approval"])
        self.assertEqual(1, result["selected_count"])
        self.assertEqual(0, result["executed_count"])
        self.assertFalse(result["mailbox_mutation"])
        self.assertFalse(result["audit_mutation"])
        self.assertFalse(provider.archived)
        self.assertEqual([], memory_store.clean_audit_events("cleaner"))

    def test_clean_execution_archives_auto_eligible_and_records_audit_when_approved(self) -> None:
        provider = CleanerProvider()
        memory_store = MemoryStore()
        rule = enable_rule(
            proposed_rule(
                action="archive",
                scope="domain",
                value="example.com",
                source="test",
                reason="user approved example.com cleanup",
            )
        )
        memory_store.save_clean_rule("cleaner", rule)
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory.json"
            _write_memory(memory_path, [])
            result = run_clean_execution(
                provider=provider,
                memory_store=memory_store,
                session_id="cleaner",
                memory_path=memory_path,
                output_dir=temp_dir,
                limit=10,
                execute=True,
            )

        self.assertEqual("execute", result["execution_mode"])
        self.assertEqual(1, result["selected_count"])
        self.assertEqual(1, result["executed_count"])
        self.assertEqual(0, result["failed_count"])
        self.assertTrue(result["mailbox_mutation"])
        self.assertTrue(result["audit_mutation"])
        self.assertTrue(provider.archived)
        events = memory_store.clean_audit_events("cleaner", run_id=result["run_id"], limit=0)
        self.assertEqual(2, len(events))
        self.assertEqual(["clean_execution_started", "clean_execution_succeeded"], [item["event_type"] for item in events])
        self.assertEqual(rule["rule_id"], events[-1]["payload"]["clean_rule_match"]["rule_id"])

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

    def test_clean_rule_cli_commands_render(self) -> None:
        stdout = StringIO()
        runtime = FakeCliRuntime([])
        runtime.memory_store = MemoryStore()

        with patch("server.email_cli.run_teach_workflow") as mocked:
            mocked.return_value = {
                "instruction": "archive Facebook",
                "parser": "heuristic",
                "created_count": 1,
                "existing_count": 0,
                "rule_count": 1,
                "rules": [
                    {
                        "rule_id": "rule-archive-domain-test",
                        "action": "archive",
                        "scope": "domain",
                        "value": "facebookmail.com",
                        "status": "proposed",
                        "created": True,
                        "reason": "test",
                    }
                ],
                "impact": {"fetched": 0, "rules": []},
                "mailbox_mutation": False,
                "rule_mutation": True,
                "proposal_mutation": False,
                "audit_mutation": False,
                "llm_authorization": False,
            }
            exit_code = run_cli(
                ["teach", "archive Facebook", "--limit", "5"],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        mocked.assert_called_once()
        self.assertEqual(5, mocked.call_args.kwargs["limit"])
        self.assertIn("Proposed rules:", stdout.getvalue())

        rule = proposed_rule(action="archive", scope="domain", value="facebookmail.com", source="test")
        runtime.memory_store.save_clean_rule("email-cli", rule)
        approve_out = StringIO()
        approve_code = run_cli(
            ["rule", "approve", rule["rule_id"]],
            runtime_factory=lambda: runtime,
            stdout=approve_out,
            stderr=StringIO(),
        )
        self.assertEqual(0, approve_code)
        self.assertIn("Status: enabled", approve_out.getvalue())

        rules_out = StringIO()
        rules_code = run_cli(
            ["rules", "--status", "enabled"],
            runtime_factory=lambda: runtime,
            stdout=rules_out,
            stderr=StringIO(),
        )
        self.assertEqual(0, rules_code)
        self.assertIn(rule["rule_id"], rules_out.getvalue())

    def test_clean_run_cli_renders_approval_required_preview(self) -> None:
        fake_run = {
            "run_id": "clean-run-test",
            "status": "ok",
            "execution_mode": "approval_required",
            "provider": {"provider": "CleanerProvider", "mailbox": "INBOX"},
            "artifact_path": "/tmp/clean-run-test.json",
            "fetched": 1,
            "auto_eligible_count": 1,
            "protected_count": 0,
            "candidate_count": 0,
            "no_action_count": 0,
            "enabled_clean_rule_count": 1,
            "archive_rule_count": 1,
            "protect_rule_count": 0,
            "auto_eligible": [],
            "candidates": [],
            "protected": [],
            "mailbox_mutation": False,
            "proposal_mutation": False,
            "llm_authorization": False,
            "selected_count": 1,
            "executed_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "audit_mutation": False,
            "audit_event_count": 0,
            "approval_hint": "Re-run clean-run with --yes to archive selected auto-eligible mail.",
            "error": "",
        }
        runtime = FakeCliRuntime([])
        runtime.memory_store = MemoryStore()
        stdout = StringIO()

        with patch("server.email_cli.run_clean_execution", return_value=fake_run) as mocked:
            exit_code = run_cli(
                ["clean-run", "--limit", "5", "--max-execute", "2"],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        mocked.assert_called_once()
        self.assertFalse(mocked.call_args.kwargs["execute"])
        self.assertEqual(2, mocked.call_args.kwargs["max_execute"])
        output = stdout.getvalue()
        self.assertIn("Selected: 1, executed: 0", output)
        self.assertIn("Re-run clean-run with --yes", output)


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
