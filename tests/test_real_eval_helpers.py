"""Regression tests for MailGuard real eval helpers."""

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
from server.app.archive_shadow import (
    archive_shadow_record,
    build_archive_shadow_input,
    evaluate_archive_shadow_results,
    load_archive_shadow_results,
    normalize_archive_shadow_judgment,
    save_archive_shadow_result,
)
from server.app.auth import configured_auth_token, require_api_token
from server.app.email_eval import evaluate_email_classifier
from server.app.email_provider import MockEmailProvider
from server.app.email_proposals import approve_action_proposal, execute_approved_action_proposals
from server.app.email_tools import classify_email
from server.app.llm_email_classifier import _normalize_decision, _parse_json_object
from server.app.memory import MemoryStore
from server.app.memory_proposals import (
    approve_memory_proposal,
    list_memory_proposals,
    refresh_memory_proposals,
    reject_memory_proposal,
)
from server.app.proposal_eval import evaluate_archive_proposal_policy
from server.app.provider_factory import create_email_provider
from server.app.qq_imap_provider import QQImapConfig, QQImapProvider
from server.app.observed_memory import build_observed_memory_report
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

class RealProposalEvalTests(unittest.TestCase):
    def test_real_proposal_label_evaluation_tracks_false_positives(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "proposal-1": {
                    "email_id": "imap-1",
                    "label": "archive",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Newsletter",
                },
                "proposal-2": {
                    "email_id": "imap-2",
                    "label": "keep",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Important invoice",
                },
                "proposal-3": {
                    "email_id": "imap-3",
                    "label": "unsure",
                    "action": "archive",
                    "risk_level": "low",
                    "subject": "Ambiguous",
                },
                "candidate-4": {
                    "item_id": "candidate-4",
                    "item_type": "candidate",
                    "candidate_id": "candidate-4",
                    "email_id": "imap-4",
                    "label": "archive",
                    "action": "archive",
                    "risk_level": "candidate",
                    "subject": "Social notification",
                },
            },
        }

        result = evaluate_real_proposal_labels(label_data)

        self.assertEqual(4, result["sample_count"])
        self.assertEqual(3, result["decisive_count"])
        self.assertEqual({"archive": 2, "keep": 1, "unsure": 1}, result["label_counts"])
        self.assertEqual(0.6667, result["metrics"]["archive_acceptance_precision"])
        self.assertEqual(1, result["metrics"]["false_positive_count"])
        self.assertEqual(["proposal-2"], [item["proposal_id"] for item in result["false_positive_proposals"]])
        self.assertEqual(3, result["by_item_type"]["proposal"]["sample_count"])
        self.assertEqual(1, result["by_item_type"]["candidate"]["sample_count"])
        self.assertEqual(
            1.0,
            result["by_item_type"]["candidate"]["metrics"]["archive_acceptance_precision"],
        )

    def test_observed_memory_report_builds_readonly_insights(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "candidate-1": {
                    "item_id": "candidate-1",
                    "item_type": "candidate",
                    "candidate_id": "candidate-1",
                    "email_id": "imap-1",
                    "from_email": "notification@facebookmail.example",
                    "label": "archive",
                    "category": "notification",
                    "subject": "New notification",
                },
                "candidate-2": {
                    "item_id": "candidate-2",
                    "item_type": "candidate",
                    "candidate_id": "candidate-2",
                    "email_id": "imap-2",
                    "from_email": "notification@facebookmail.example",
                    "label": "archive",
                    "category": "notification",
                    "subject": "Another notification",
                },
                "proposal-3": {
                    "item_id": "proposal-3",
                    "item_type": "proposal",
                    "proposal_id": "proposal-3",
                    "email_id": "imap-3",
                    "from_email": "billing@example.com",
                    "label": "keep",
                    "category": "finance",
                    "subject": "Invoice",
                },
            },
        }

        report = build_observed_memory_report(label_data, min_samples=2)

        self.assertFalse(report["mailbox_mutation"])
        self.assertEqual(3, report["sample_count"])
        self.assertEqual(3, report["decisive_count"])
        self.assertEqual(2, len(report["groups"]["sender"]))
        self.assertEqual("notification@facebookmail.example", report["groups"]["sender"][0]["key"])
        self.assertEqual(1.0, report["groups"]["sender"][0]["archive_rate"])
        insight = report["insights"][0]
        self.assertEqual("archive_friendly", insight["kind"])
        self.assertEqual("sender", insight["group_type"])
        self.assertEqual("medium", insight["confidence"])
        self.assertEqual("archive_sender", report["proposed_preferences"][0]["proposal"])

    def test_memory_proposals_can_be_approved_and_rejected_locally(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "candidate-1": {
                    "item_id": "candidate-1",
                    "item_type": "candidate",
                    "candidate_id": "candidate-1",
                    "email_id": "imap-1",
                    "from_email": "notification@facebookmail.example",
                    "label": "archive",
                    "category": "notification",
                    "subject": "New notification",
                },
                "candidate-2": {
                    "item_id": "candidate-2",
                    "item_type": "candidate",
                    "candidate_id": "candidate-2",
                    "email_id": "imap-2",
                    "from_email": "notification@facebookmail.example",
                    "label": "archive",
                    "category": "notification",
                    "subject": "Another notification",
                },
            },
        }
        report = build_observed_memory_report(label_data, min_samples=2)
        with TemporaryDirectory() as temp_dir:
            memory_path = Path(temp_dir) / "memory_proposals.json"
            refreshed = refresh_memory_proposals(memory_path, report)
            sender = next(item for item in refreshed["proposals"] if item["memory_type"] == "archive_sender")
            domain = next(item for item in refreshed["proposals"] if item["memory_type"] == "archive_domain")

            approved = approve_memory_proposal(memory_path, sender["proposal_id"])
            rejected = reject_memory_proposal(memory_path, domain["proposal_id"], reason="too broad")
            listed = list_memory_proposals(memory_path)

        self.assertGreaterEqual(refreshed["created_count"], 2)
        self.assertEqual("approved", approved["proposal"]["status"])
        self.assertEqual(
            ["notification@facebookmail.example"],
            approved["confirmed_memory"]["archive_senders"],
        )
        self.assertEqual("rejected", rejected["proposal"]["status"])
        self.assertEqual("too broad", rejected["proposal"]["decision_reason"])
        self.assertEqual(1, len(listed["confirmed_memory"]["archive_senders"]))
        self.assertEqual([], listed["confirmed_memory"]["archive_domains"])
        self.assertTrue(approved["proposal"]["applied_to_policy"])
        self.assertFalse(rejected["proposal"]["applied_to_policy"])

    def test_archive_shadow_input_excludes_body_and_includes_memory_context(self) -> None:
        item = {
            "candidate_id": "candidate-imap-1-archive",
            "item_type": "candidate",
            "email_id": "imap-1",
            "from_email": "notification@facebookmail.example",
            "subject": "New Facebook notification",
            "category": "noise",
            "importance": "low",
            "suggested_action": "ignore",
            "policy_reason": "ignored mail did not satisfy strict archive proposal policy",
        }
        email = {
            "id": "imap-1",
            "from_name": "Facebook",
            "from_email": "notification@facebookmail.example",
            "subject": "New Facebook notification",
            "snippet": "You have new notifications.",
            "body": "Sensitive body must not be sent to the LLM.",
            "body_truncated": False,
        }

        shadow_input = build_archive_shadow_input(
            item,
            email,
            confirmed_memory={
                "archive_senders": ["notification@facebookmail.example"],
                "archive_domains": ["facebookmail.example"],
                "archive_categories": ["noise"],
            },
        )

        payload = json.dumps(shadow_input, ensure_ascii=False)
        self.assertNotIn("Sensitive body", payload)
        self.assertNotIn("body_truncated", payload)
        self.assertEqual("candidate", shadow_input["item"]["policy_bucket"])
        self.assertEqual(
            [
                "archive_sender:notification@facebookmail.example",
                "archive_domain:facebookmail.example",
                "archive_category:noise",
            ],
            shadow_input["confirmed_memory_context"]["matches"],
        )
        self.assertFalse(shadow_input["confirmed_memory_context"]["archive_category_policy_active"])
        self.assertFalse(shadow_input["safety_constraints"]["body_included"])

    def test_archive_shadow_results_evaluate_against_labels(self) -> None:
        label_data = {
            "schema_version": 1,
            "labels": {
                "candidate-1": {
                    "item_id": "candidate-1",
                    "item_type": "candidate",
                    "candidate_id": "candidate-1",
                    "email_id": "imap-1",
                    "from_email": "notification@facebookmail.example",
                    "label": "archive",
                    "category": "noise",
                    "subject": "Archive this notification",
                },
                "candidate-2": {
                    "item_id": "candidate-2",
                    "item_type": "candidate",
                    "candidate_id": "candidate-2",
                    "email_id": "imap-2",
                    "from_email": "billing@example.com",
                    "label": "keep",
                    "category": "finance",
                    "subject": "Keep this invoice",
                },
            },
        }
        with TemporaryDirectory() as temp_dir:
            shadow_path = Path(temp_dir) / "archive_shadow_results.json"
            first_input = build_archive_shadow_input(
                {"candidate_id": "candidate-1", "item_type": "candidate", "email_id": "imap-1"},
                {"id": "imap-1", "subject": "Archive this notification"},
            )
            second_input = build_archive_shadow_input(
                {"candidate_id": "candidate-2", "item_type": "candidate", "email_id": "imap-2"},
                {"id": "imap-2", "subject": "Keep this invoice"},
            )
            save_archive_shadow_result(
                shadow_path,
                archive_shadow_record(
                    shadow_input=first_input,
                    judgment=normalize_archive_shadow_judgment(
                        {
                            "archive_suitability": "yes",
                            "confidence": "high",
                            "reason_codes": ["low_value_notification"],
                            "brief_reason": "Looks safe to archive.",
                        }
                    ),
                    model="fake-model",
                    elapsed_ms=1000,
                ),
            )
            save_archive_shadow_result(
                shadow_path,
                archive_shadow_record(
                    shadow_input=second_input,
                    judgment=normalize_archive_shadow_judgment(
                        {
                            "archive_suitability": "yes",
                            "confidence": "low",
                            "reason_codes": ["mistaken"],
                            "brief_reason": "Mistakenly suggested archive.",
                        }
                    ),
                    model="fake-model",
                    elapsed_ms=1200,
                ),
            )
            evaluation = evaluate_archive_shadow_results(
                label_data=label_data,
                shadow_data=load_archive_shadow_results(shadow_path),
            )

        self.assertFalse(evaluation["mailbox_mutation"])
        self.assertFalse(evaluation["proposal_mutation"])
        self.assertEqual(2, evaluation["matched_count"])
        self.assertEqual({"yes": 2}, evaluation["prediction_counts"])
        self.assertEqual(0.5, evaluation["metrics"]["archive_yes_precision"])
        self.assertEqual(1.0, evaluation["metrics"]["archive_yes_recall"])
        self.assertEqual(1, evaluation["metrics"]["false_positive_count"])
        self.assertEqual(1100, evaluation["metrics"]["avg_latency_ms"])
        self.assertFalse(evaluation["readiness"]["ready_for_policy_experiment"])
        self.assertEqual("collect_more_labels", evaluation["readiness"]["recommendation"])

    def test_archive_shadow_readiness_requires_clean_quality_gates(self) -> None:
        labels = {}
        results = {}
        for index in range(30):
            item_id = f"candidate-{index}"
            labels[item_id] = {
                "item_id": item_id,
                "item_type": "candidate",
                "candidate_id": item_id,
                "email_id": f"imap-{index}",
                "from_email": "updates@example.com",
                "label": "archive",
                "category": "newsletter",
                "subject": f"Newsletter {index}",
            }
            shadow_input = build_archive_shadow_input(
                {"candidate_id": item_id, "item_type": "candidate", "email_id": f"imap-{index}"},
                {"id": f"imap-{index}", "subject": f"Newsletter {index}"},
            )
            results[item_id] = archive_shadow_record(
                shadow_input=shadow_input,
                judgment=normalize_archive_shadow_judgment(
                    {
                        "archive_suitability": "yes",
                        "confidence": "high",
                        "reason_codes": ["low_value_newsletter"],
                        "brief_reason": "Newsletter can be archived.",
                    }
                ),
                model="fake-model",
                elapsed_ms=1200,
            )

        evaluation = evaluate_archive_shadow_results(
            label_data={"schema_version": 1, "labels": labels},
            shadow_data={"schema_version": 1, "results": results},
        )

        self.assertEqual(30, evaluation["decisive_count"])
        self.assertEqual(1.0, evaluation["metrics"]["archive_yes_precision"])
        self.assertEqual(1200, evaluation["metrics"]["avg_latency_ms"])
        self.assertTrue(evaluation["readiness"]["ready_for_policy_experiment"])
        self.assertEqual(
            "eligible_for_guarded_policy_experiment",
            evaluation["readiness"]["recommendation"],
        )
