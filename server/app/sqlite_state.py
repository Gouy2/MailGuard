"""SQLite-backed state persistence for email triage runtime state."""

from __future__ import annotations

import json
import sqlite3
from threading import RLock
from pathlib import Path
from typing import Any


class SQLiteStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def clear(self, session_id: str | None = None) -> None:
        tables = (
            "email_preferences",
            "email_reported",
            "email_notifications",
            "email_scans",
            "email_action_proposals",
            "email_action_audit_events",
        )
        with self._lock, self._connection:
            for table in tables:
                if session_id is None:
                    self._connection.execute(f"DELETE FROM {table}")
                else:
                    self._connection.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))

    def load_email_preferences(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT preferences_json FROM email_preferences WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row["preferences_json"])
        return data if isinstance(data, dict) else None

    def save_email_preferences(self, session_id: str, preferences: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO email_preferences(session_id, preferences_json, updated_at)
                VALUES(?, ?, datetime('now'))
                ON CONFLICT(session_id) DO UPDATE SET
                    preferences_json = excluded.preferences_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(preferences, ensure_ascii=False, sort_keys=True)),
            )

    def load_reported_email_ids(self, session_id: str) -> set[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT email_id FROM email_reported WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        return {str(row["email_id"]) for row in rows}

    def mark_email_reported(self, session_id: str, email_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO email_reported(session_id, email_id, created_at)
                VALUES(?, ?, datetime('now'))
                """,
                (session_id, email_id),
            )

    def load_notifications(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT notification_json
                FROM email_notifications
                WHERE session_id = ?
                ORDER BY created_at, notification_id
                """,
                (session_id,),
            ).fetchall()
        return [_json_object(row["notification_json"]) for row in rows]

    def save_notification(self, session_id: str, notification: dict[str, Any]) -> None:
        notification_id = str(notification["notification_id"])
        created_at = str(notification.get("created_at", ""))
        status = str(notification.get("status", ""))
        with self._lock, self._connection:
            self._save_notification_locked(session_id, notification_id, notification, status, created_at)

    def create_email_notification_once(self, session_id: str, email_id: str, notification: dict[str, Any]) -> bool:
        notification_id = str(notification["notification_id"])
        created_at = str(notification.get("created_at", ""))
        status = str(notification.get("status", ""))
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO email_reported(session_id, email_id, created_at)
                VALUES(?, ?, datetime('now'))
                """,
                (session_id, email_id),
            )
            if cursor.rowcount == 0:
                return False
            self._save_notification_locked(session_id, notification_id, notification, status, created_at)
            return True

    def load_scan_history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT scan_json
                FROM email_scans
                WHERE session_id = ?
                ORDER BY created_at, scan_id
                """,
                (session_id,),
            ).fetchall()
        return [_json_object(row["scan_json"]) for row in rows]

    def save_scan(self, session_id: str, scan: dict[str, Any]) -> None:
        scan_id = str(scan["scan_id"])
        created_at = str(scan.get("created_at", ""))
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO email_scans(session_id, scan_id, scan_json, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(session_id, scan_id) DO UPDATE SET
                    scan_json = excluded.scan_json,
                    created_at = excluded.created_at
                """,
                (
                    session_id,
                    scan_id,
                    json.dumps(scan, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )

    def load_action_proposals(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT proposal_json
                FROM email_action_proposals
                WHERE session_id = ?
                ORDER BY created_at, proposal_id
                """,
                (session_id,),
            ).fetchall()
        return [_json_object(row["proposal_json"]) for row in rows]

    def save_action_proposal(self, session_id: str, proposal: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._save_action_proposal_locked(session_id, proposal)

    def create_action_proposal_once(self, session_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO email_action_proposals(
                    session_id,
                    proposal_id,
                    email_id,
                    action,
                    status,
                    proposal_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(proposal["proposal_id"]),
                    str(proposal["email_id"]),
                    str(proposal["action"]),
                    str(proposal.get("status", "")),
                    json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                    str(proposal.get("created_at", "")),
                    str(proposal.get("updated_at", "")),
                ),
            )
            if cursor.rowcount == 1:
                return {"created": True, "proposal": dict(proposal)}

            row = self._connection.execute(
                """
                SELECT proposal_json
                FROM email_action_proposals
                WHERE session_id = ? AND email_id = ? AND action = ?
                """,
                (session_id, str(proposal["email_id"]), str(proposal["action"])),
            ).fetchone()
            return {
                "created": False,
                "proposal": _json_object(row["proposal_json"]) if row else dict(proposal),
            }

    def load_action_audit_events(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_json
                FROM email_action_audit_events
                WHERE session_id = ?
                ORDER BY created_at, event_id
                """,
                (session_id,),
            ).fetchall()
        return [_json_object(row["event_json"]) for row in rows]

    def save_action_audit_event(self, session_id: str, event: dict[str, Any]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO email_action_audit_events(session_id, event_id, proposal_id, event_type, actor, event_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, event_id) DO UPDATE SET
                    proposal_id = excluded.proposal_id,
                    event_type = excluded.event_type,
                    actor = excluded.actor,
                    event_json = excluded.event_json,
                    created_at = excluded.created_at
                """,
                (
                    session_id,
                    str(event["event_id"]),
                    str(event["proposal_id"]),
                    str(event.get("event_type", "")),
                    str(event.get("actor", "")),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                    str(event.get("created_at", "")),
                ),
            )

    def _initialize(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_preferences (
                    session_id TEXT PRIMARY KEY,
                    preferences_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_reported (
                    session_id TEXT NOT NULL,
                    email_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, email_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_notifications (
                    session_id TEXT NOT NULL,
                    notification_id TEXT NOT NULL,
                    notification_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, notification_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_scans (
                    session_id TEXT NOT NULL,
                    scan_id TEXT NOT NULL,
                    scan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, scan_id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_action_proposals (
                    session_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    email_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, proposal_id),
                    UNIQUE(session_id, email_id, action)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_action_audit_events (
                    session_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(session_id, event_id)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_notifications_session_status ON email_notifications(session_id, status)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_scans_session_created ON email_scans(session_id, created_at)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_action_proposals_session_status ON email_action_proposals(session_id, status)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_email_action_audit_session_proposal ON email_action_audit_events(session_id, proposal_id)"
            )

    def _save_notification_locked(
        self,
        session_id: str,
        notification_id: str,
        notification: dict[str, Any],
        status: str,
        created_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO email_notifications(session_id, notification_id, notification_json, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(session_id, notification_id) DO UPDATE SET
                notification_json = excluded.notification_json,
                status = excluded.status,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                notification_id,
                json.dumps(notification, ensure_ascii=False, sort_keys=True),
                status,
                created_at,
            ),
        )

    def _save_action_proposal_locked(self, session_id: str, proposal: dict[str, Any]) -> None:
        self._connection.execute(
            """
            INSERT INTO email_action_proposals(session_id, proposal_id, email_id, action, status, proposal_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, proposal_id) DO UPDATE SET
                email_id = excluded.email_id,
                action = excluded.action,
                status = excluded.status,
                proposal_json = excluded.proposal_json,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                str(proposal["proposal_id"]),
                str(proposal["email_id"]),
                str(proposal["action"]),
                str(proposal.get("status", "")),
                json.dumps(proposal, ensure_ascii=False, sort_keys=True),
                str(proposal.get("created_at", "")),
                str(proposal.get("updated_at", "")),
            ),
        )


def _json_object(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("stored JSON value must be an object")
    return data
