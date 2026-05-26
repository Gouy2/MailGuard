"""Regression tests for MailGuard email cli."""

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
                        "protected_count": 0,
                        "candidate_count": 0,
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

    def test_review_proposals_can_show_protected_items(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_scan_proposals",
                    "result": {
                        "provider": "MockEmailProvider",
                        "fetched": 1,
                        "proposal_count": 0,
                        "created_count": 0,
                        "duplicate_count": 0,
                        "protected_count": 1,
                        "candidate_count": 0,
                        "no_action_count": 0,
                        "proposals": [],
                        "protected": [
                            {
                                "email_id": "email-035",
                                "from_name": "Domain Registrar",
                                "from_email": "billing@domains.example",
                                "subject": "Domain renewal invoice due tomorrow",
                                "category": "finance",
                                "importance": "high",
                                "suggested_action": "review",
                                "policy_reason": "protected category or reportable mail",
                            }
                        ],
                    },
                }
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["review-proposals", "--limit", "1", "--all", "--show-protected"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn("Protected:", output)
        self.assertIn("email-035 [high/finance/review]", output)
        self.assertIn("protected category or reportable mail", output)

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

    def test_eval_proposals_command_calls_expected_tool(self) -> None:
        runtime = FakeCliRuntime(
            execute_results=[
                {
                    "ok": True,
                    "tool": "email_eval_proposals",
                    "result": {
                        "provider": "MockEmailProvider",
                        "classifier": "rule",
                        "mailbox_mutation": False,
                        "sample_count": 36,
                        "proposal_count": 7,
                        "eligible_safe_archive_count": 13,
                        "metrics": {
                            "archive_proposal_precision": 1.0,
                            "archive_proposal_recall": 0.5385,
                            "false_positive_count": 0,
                            "missed_safe_archive_count": 6,
                            "important_false_positive_count": 0,
                        },
                        "false_positive_proposals": [],
                        "missed_safe_archive": [],
                    },
                }
            ]
        )
        stdout = StringIO()

        exit_code = run_cli(
            ["eval-proposals", "--limit", "36", "--unread"],
            runtime_factory=lambda: runtime,
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_eval_proposals", {"limit": 36, "unread_only": True, "include_rows": False}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertIn("archive_proposal_precision: 1.0", stdout.getvalue())

    def test_review_proposals_interactive_labeling_saves_labels_inline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_proposal_labels.json"
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
                            "protected_count": 0,
                            "candidate_count": 1,
                            "no_action_count": 0,
                            "proposals": [
                                {
                                    "proposal_id": "proposal-001",
                                    "status": "proposed",
                                    "risk_level": "low",
                                    "source": "policy_rule",
                                    "action": "archive",
                                    "email_id": "email-004",
                                    "from_name": "Design Weekly",
                                    "from_email": "newsletter@example.com",
                                    "subject": "Newsletter",
                                    "reason": "low-value mail",
                                }
                            ],
                            "candidates": [
                                {
                                    "candidate_id": "candidate-email-031-archive",
                                    "item_type": "candidate",
                                    "risk_level": "candidate",
                                    "source": "policy_candidate",
                                    "action": "archive",
                                    "email_id": "email-031",
                                    "from_name": "No Reply Surveys",
                                    "from_email": "noreply@survey.example",
                                    "subject": "Please review your shopping experience",
                                    "reason": "low-value mail has positive signals",
                                }
                            ],
                        },
                    }
                ]
            )
            answers = iter(["a", "k"])
            stdout = StringIO()

            exit_code = run_cli(
                ["review-proposals", "--limit", "1", "--all", "--label", "--labels-path", str(labels_path)],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
                input_func=lambda _prompt: next(answers),
            )
            saved = load_real_proposal_labels(labels_path)

        self.assertEqual(0, exit_code)
        self.assertEqual(
            [("email_scan_proposals", {"limit": 1, "unread_only": False}, "email-cli")],
            runtime.execute_calls,
        )
        self.assertEqual("archive", saved["labels"]["proposal-001"]["label"])
        self.assertEqual("keep", saved["labels"]["candidate-email-031-archive"]["label"])
        self.assertEqual("candidate", saved["labels"]["candidate-email-031-archive"]["item_type"])
        self.assertIn("Saved proposal-001 -> archive", stdout.getvalue())
        self.assertIn("Saved candidate-email-031-archive -> keep", stdout.getvalue())

    def test_eval_real_proposals_command_prints_saved_metrics(self) -> None:
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_proposal_labels.json"
            save_real_proposal_label(
                labels_path,
                proposal={
                    "proposal_id": "proposal-001",
                    "email_id": "email-004",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Newsletter",
                },
                label="archive",
            )
            save_real_proposal_label(
                labels_path,
                proposal={
                    "proposal_id": "proposal-002",
                    "email_id": "email-010",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Important",
                },
                label="keep",
            )
            stdout = StringIO()

            exit_code = run_cli(
                ["eval-real-proposals", "--labels-path", str(labels_path)],
                runtime_factory=lambda: FakeCliRuntime([]),
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        output = stdout.getvalue()
        self.assertIn("archive_acceptance_precision: 0.5", output)
        self.assertIn("false_positive_count: 1", output)

    def test_observed_memory_command_prints_insights_without_runtime_tools(self) -> None:
        with TemporaryDirectory() as temp_dir:
            labels_path = Path(temp_dir) / "real_proposal_labels.json"
            save_real_proposal_label(
                labels_path,
                proposal={
                    "candidate_id": "candidate-001",
                    "item_type": "candidate",
                    "email_id": "email-031",
                    "from_email": "notification@facebookmail.example",
                    "subject": "Facebook notification",
                    "category": "notification",
                    "action": "archive",
                },
                label="archive",
            )
            save_real_proposal_label(
                labels_path,
                proposal={
                    "candidate_id": "candidate-002",
                    "item_type": "candidate",
                    "email_id": "email-032",
                    "from_email": "notification@facebookmail.example",
                    "subject": "Another Facebook notification",
                    "category": "notification",
                    "action": "archive",
                },
                label="archive",
            )
            runtime = FakeCliRuntime([])
            stdout = StringIO()

            exit_code = run_cli(
                ["observed-memory", "--labels-path", str(labels_path), "--min-samples", "2"],
                runtime_factory=lambda: runtime,
                stdout=stdout,
                stderr=StringIO(),
            )

        self.assertEqual(0, exit_code)
        self.assertEqual([], runtime.execute_calls)
        output = stdout.getvalue()
        self.assertIn("Mailbox mutation: False", output)
        self.assertIn("archive_friendly sender=notification@facebookmail.example", output)
        self.assertIn("archive_sender=notification@facebookmail.example", output)

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
