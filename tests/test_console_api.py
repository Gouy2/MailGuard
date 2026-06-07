"""Tests for the MailGuard console-facing API surface."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server.app.agent import AgentRuntime


class ConsoleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except ImportError:  # pragma: no cover - root python may not have FastAPI.
            self.skipTest("fastapi is not installed in this test environment")

        self._env_patcher = patch.dict(
            os.environ,
            {
                "MAILGUARD_EMAIL_PROVIDER": "mock",
                "MAILGUARD_STATE_DB": "",
                "MAILGUARD_MEMORY_PROPOSALS": "off",
                "MAILGUARD_AUTH_TOKEN": "",
                "OPENAI_API_KEY": "",
                "OPENAI_BASE_URL": "",
            },
        )
        self._env_patcher.start()
        self._preview_dir = TemporaryDirectory()
        self._storage_patcher = patch(
            "server.app.cleaner.storage.DEFAULT_CLEAN_PREVIEW_DIR",
            Path(self._preview_dir.name),
        )
        self._storage_patcher.start()

        from server.app import main as main_module

        self.main_module = main_module
        self.previous_runtime = main_module.runtime
        main_module.runtime = AgentRuntime.create()
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        self.main_module.runtime.close()
        self.main_module.runtime = self.previous_runtime
        self._storage_patcher.stop()
        self._preview_dir.cleanup()
        self._env_patcher.stop()

    def test_cleaner_api_covers_teach_rules_preview_policy_and_audit(self) -> None:
        teach = self.client.post(
            "/cleaner/teach",
            json={
                "session_id": "console",
                "instruction": "以后 Facebook 通知都归档，但安全邮件不要动",
                "limit": 10,
            },
        )
        self.assertEqual(200, teach.status_code)
        teach_body = teach.json()
        self.assertEqual("ok", teach_body["status"])
        self.assertFalse(teach_body["mailbox_mutation"])
        self.assertGreaterEqual(teach_body["rule_count"], 1)
        rule_id = teach_body["rules"][0]["rule_id"]

        rules = self.client.get("/cleaner/rules", params={"session_id": "console"})
        self.assertEqual(200, rules.status_code)
        self.assertEqual("ok", rules.json()["status"])
        self.assertGreaterEqual(rules.json()["count"], 1)

        approved = self.client.post(
            f"/cleaner/rules/{rule_id}/approve",
            json={"session_id": "console"},
        )
        self.assertEqual(200, approved.status_code)
        self.assertEqual("enabled", approved.json()["rule"]["status"])

        preview = self.client.post(
            "/cleaner/preview",
            json={"session_id": "console", "limit": 10},
        )
        self.assertEqual(200, preview.status_code)
        preview_body = preview.json()
        self.assertEqual("ok", preview_body["status"])
        self.assertFalse(preview_body["mailbox_mutation"])
        self.assertIn("auto_eligible", preview_body)
        self.assertTrue(Path(preview_body["artifact_path"]).exists())

        policy = self.client.get("/cleaner/policy", params={"session_id": "console"})
        self.assertEqual(200, policy.status_code)
        self.assertEqual("ok", policy.json()["status"])
        self.assertFalse(policy.json()["policy_mutation"])

        audit = self.client.get("/cleaner/audit", params={"session_id": "console"})
        self.assertEqual(200, audit.status_code)
        self.assertEqual("ok", audit.json()["status"])
        self.assertEqual(0, audit.json()["count"])

    def test_api_prefix_covers_console_health_chat_and_cleaner_routes(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual("ok", health.json()["status"])
        self.assertGreater(len(health.json()["tools"]), 0)

        chat = self.client.post(
            "/api/chat",
            json={
                "session_id": "console-api",
                "message": "hello",
                "system_prompt": "请用 markdown 列表回答。",
            },
        )
        self.assertEqual(200, chat.status_code)
        self.assertIn("event: status", chat.text)
        self.assertIn("event: trace", chat.text)
        self.assertIn("event: done", chat.text)

        teach = self.client.post(
            "/api/cleaner/teach",
            json={
                "session_id": "console-api",
                "instruction": "以后 Facebook 通知都归档，但安全邮件不要动",
                "limit": 10,
            },
        )
        self.assertEqual(200, teach.status_code)
        self.assertEqual("ok", teach.json()["status"])
        self.assertFalse(teach.json()["mailbox_mutation"])

        rules = self.client.get("/api/cleaner/rules", params={"session_id": "console-api"})
        self.assertEqual(200, rules.status_code)
        self.assertEqual("ok", rules.json()["status"])

        preview = self.client.post(
            "/api/cleaner/preview",
            json={"session_id": "console-api", "limit": 10},
        )
        self.assertEqual(200, preview.status_code)
        self.assertEqual("ok", preview.json()["status"])
        self.assertFalse(preview.json()["mailbox_mutation"])

    def test_api_prefix_preserves_auth_for_protected_console_routes(self) -> None:
        with patch.dict(os.environ, {"MAILGUARD_AUTH_TOKEN": "console-token"}):
            health = self.client.get("/api/health")
            self.assertEqual(200, health.status_code)

            missing = self.client.get("/api/tools/pending")
            self.assertEqual(401, missing.status_code)
            self.assertEqual("missing or invalid API token", missing.json()["detail"])

            allowed = self.client.get(
                "/api/tools/pending",
                headers={"Authorization": "Bearer console-token"},
            )
            self.assertEqual(200, allowed.status_code)
            self.assertEqual("ok", allowed.json()["status"])

    def test_cleaner_rule_api_returns_404_for_missing_rule(self) -> None:
        response = self.client.post(
            "/cleaner/rules/rule-missing/approve",
            json={"session_id": "console"},
        )
        self.assertEqual(404, response.status_code)
