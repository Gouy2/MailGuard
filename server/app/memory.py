"""Memory storage for chat history and lightweight long-term notes."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import Any, Protocol
import uuid

from .archive import new_action_audit_event, normalize_action_proposal
from .cleaner.audit import new_clean_audit_event
from .cleaner.policy import default_clean_policy, normalize_clean_policy
from .cleaner.rules import disable_rule, enable_rule


class StateStore(Protocol):
    def clear(self, session_id: str | None = None) -> None: ...
    def load_email_preferences(self, session_id: str) -> dict[str, Any] | None: ...
    def save_email_preferences(self, session_id: str, preferences: dict[str, Any]) -> None: ...
    def load_reported_email_ids(self, session_id: str) -> set[str]: ...
    def mark_email_reported(self, session_id: str, email_id: str) -> None: ...
    def load_notifications(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_notification(self, session_id: str, notification: dict[str, Any]) -> None: ...
    def load_scan_history(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_scan(self, session_id: str, scan: dict[str, Any]) -> None: ...
    def load_action_proposals(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_action_proposal(self, session_id: str, proposal: dict[str, Any]) -> None: ...
    def create_action_proposal_once(self, session_id: str, proposal: dict[str, Any]) -> dict[str, Any]: ...
    def load_action_audit_events(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_action_audit_event(self, session_id: str, event: dict[str, Any]) -> None: ...
    def load_clean_rules(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_clean_rule(self, session_id: str, rule: dict[str, Any]) -> None: ...
    def load_clean_policy(self, session_id: str) -> dict[str, Any] | None: ...
    def save_clean_policy(self, session_id: str, policy: dict[str, Any]) -> None: ...
    def load_clean_audit_events(self, session_id: str) -> list[dict[str, Any]]: ...
    def save_clean_audit_event(self, session_id: str, event: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class MemoryNote:
    session_id: str
    note: str
    source: str = "tool"


class MemoryStore:
    def __init__(self, state_store: StateStore | None = None) -> None:
        self._sessions: dict[str, list[dict[str, str]]] = defaultdict(list)
        self._notes: dict[str, list[MemoryNote]] = defaultdict(list)
        self._email_preferences: dict[str, dict[str, Any]] = defaultdict(_default_email_preferences)
        self._email_scheduler: dict[str, dict[str, Any]] = defaultdict(_default_email_scheduler_state)
        self._action_proposals: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._action_audit_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._clean_rules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._clean_policies: dict[str, dict[str, Any]] = defaultdict(default_clean_policy)
        self._clean_audit_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._state_store = state_store
        self._lock = RLock()

    def append(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._sessions[session_id].append({"role": role, "content": content})

    def get(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self._lock:
            history = self._sessions.get(session_id, [])
            if limit <= 0:
                return list(history)
            return list(history[-limit:])

    def clear(self, session_id: str | None = None) -> None:
        with self._lock:
            if session_id is None:
                self._sessions.clear()
                self._notes.clear()
                self._email_preferences.clear()
                self._email_scheduler.clear()
                self._action_proposals.clear()
                self._action_audit_events.clear()
                self._clean_rules.clear()
                self._clean_policies.clear()
                self._clean_audit_events.clear()
                if self._state_store:
                    self._state_store.clear()
                return
            self._sessions.pop(session_id, None)
            self._notes.pop(session_id, None)
            self._email_preferences.pop(session_id, None)
            self._email_scheduler.pop(session_id, None)
            self._action_proposals.pop(session_id, None)
            self._action_audit_events.pop(session_id, None)
            self._clean_rules.pop(session_id, None)
            self._clean_policies.pop(session_id, None)
            self._clean_audit_events.pop(session_id, None)
            if self._state_store:
                self._state_store.clear(session_id)

    def close(self) -> None:
        close = getattr(self._state_store, "close", None)
        if close:
            close()

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {session_id: len(messages) for session_id, messages in self._sessions.items()}

    def remember(self, session_id: str, note: str, source: str = "tool") -> MemoryNote:
        with self._lock:
            item = MemoryNote(session_id=session_id, note=note, source=source)
            self._notes[session_id].append(item)
            return item

    def notes(self, session_id: str, limit: int = 20) -> list[dict[str, str]]:
        with self._lock:
            items = self._notes.get(session_id, [])
            if limit <= 0:
                selected = list(items)
            else:
                selected = list(items[-limit:])
            return [asdict(item) for item in selected]

    def search_notes(self, session_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        with self._lock:
            query = query.strip().lower()
            if not query:
                return self.notes(session_id, limit=limit)

            items = self._notes.get(session_id, [])
            scored: list[tuple[int, MemoryNote]] = []
            for item in items:
                text = item.note.lower()
                score = 0
                for token in query.split():
                    if token and token in text:
                        score += 1
                if score:
                    scored.append((score, item))

            scored.sort(key=lambda entry: (-entry[0], entry[1].note))
            return [asdict(item) for _, item in scored[:limit]]

    def email_preferences(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            return _copy_preferences(self._email_preferences[session_id])

    def set_email_preference(self, session_id: str, key: str, value: Any) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            preferences = self._email_preferences[session_id]
            if key not in preferences:
                raise KeyError(f"unknown email preference: {key}")
            if key in _EMAIL_LIST_KEYS:
                preferences[key] = _normalized_list(value)
            elif key in _EMAIL_STRING_KEYS:
                preferences[key] = str(value).strip()
            else:
                preferences[key] = value
            self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def add_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            if key not in _EMAIL_LIST_KEYS:
                raise KeyError(f"email preference is not a list: {key}")
            item = _normalize_preference_value(value)
            if not item:
                raise ValueError("preference value is required")
            preferences = self._email_preferences[session_id]
            if item not in preferences[key]:
                preferences[key].append(item)
                preferences[key].sort()
                self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def remove_email_preference(self, session_id: str, key: str, value: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_preferences_loaded(session_id)
            if key not in _EMAIL_LIST_KEYS:
                raise KeyError(f"email preference is not a list: {key}")
            item = _normalize_preference_value(value)
            preferences = self._email_preferences[session_id]
            preferences[key] = [current for current in preferences[key] if current != item]
            self._persist_email_preferences(session_id)
            return _copy_preferences(preferences)

    def email_scheduler_state(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return _copy_scheduler_state(self._email_scheduler[session_id])

    def has_reported_email(self, session_id: str, email_id: str) -> bool:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return email_id in self._email_scheduler[session_id]["reported_email_ids"]

    def mark_email_reported(self, session_id: str, email_id: str) -> None:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            self._email_scheduler[session_id]["reported_email_ids"].add(email_id)
            if self._state_store:
                self._state_store.mark_email_reported(session_id, email_id)

    def create_email_notification_once(self, session_id: str, email_id: str, notification: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            if email_id in self._email_scheduler[session_id]["reported_email_ids"]:
                return None
            item = _notification_item(notification)
            if self._state_store:
                create_once = getattr(self._state_store, "create_email_notification_once", None)
                if create_once and not create_once(session_id, email_id, item):
                    self._reload_email_scheduler_state(session_id)
                    return None
            self._email_scheduler[session_id]["reported_email_ids"].add(email_id)
            self._email_scheduler[session_id]["notifications"].append(item)
            if self._state_store and not getattr(self._state_store, "create_email_notification_once", None):
                self._state_store.save_notification(session_id, item)
                self._state_store.mark_email_reported(session_id, email_id)
            return dict(item)

    def add_email_notification(self, session_id: str, notification: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            return self._add_email_notification_locked(session_id, notification)

    def email_notifications(self, session_id: str, include_read: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            notifications = self._email_scheduler[session_id]["notifications"]
            items = notifications if include_read else [item for item in notifications if item.get("status") != "read"]
            selected = list(items[-limit:]) if limit > 0 else list(items)
            return [dict(item) for item in selected]

    def mark_email_notification_read(self, session_id: str, notification_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            for item in self._email_scheduler[session_id]["notifications"]:
                if item["notification_id"] == notification_id:
                    item["status"] = "read"
                    item["read_at"] = _now()
                    if self._state_store:
                        self._state_store.save_notification(session_id, item)
                    return dict(item)
            raise KeyError(f"notification not found: {notification_id}")

    def record_email_scan(self, session_id: str, scan: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            state = self._email_scheduler[session_id]
            item = {
                "scan_id": scan.get("scan_id") or f"scan-{uuid.uuid4().hex[:12]}",
                "created_at": scan.get("created_at") or _now(),
                **scan,
            }
            state["scan_history"].append(item)
            state["last_scan_at"] = item["created_at"]
            if self._state_store:
                self._state_store.save_scan(session_id, item)
            return dict(item)

    def email_scan_history(self, session_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_email_scheduler_loaded(session_id)
            scans = self._email_scheduler[session_id]["scan_history"]
            selected = list(scans[-limit:]) if limit > 0 else list(scans)
            return [dict(item) for item in selected]

    def create_action_proposal_once(self, session_id: str, proposal: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            item = _action_proposal_item(proposal)
            existing = self._find_action_proposal_locked(session_id, item["email_id"], item["action"])
            if existing is not None:
                return {"created": False, "proposal": dict(existing)}

            if self._state_store:
                result = self._state_store.create_action_proposal_once(session_id, item)
                if not result.get("created"):
                    self._reload_action_state(session_id)
                    return {
                        "created": False,
                        "proposal": dict(result.get("proposal") or self._find_action_proposal_by_id_locked(session_id, item["proposal_id"]) or item),
                    }
                item = dict(result.get("proposal") or item)

            self._action_proposals[session_id].append(item)
            return {"created": True, "proposal": dict(item)}

    def action_proposals(self, session_id: str, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            proposals = self._action_proposals[session_id]
            if status:
                proposals = [item for item in proposals if item.get("status") == status]
            selected = list(proposals[-limit:]) if limit > 0 else list(proposals)
            return [dict(item) for item in selected]

    def get_action_proposal(self, session_id: str, proposal_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            proposal = self._find_action_proposal_by_id_locked(session_id, proposal_id)
            if proposal is None:
                raise KeyError(f"action proposal not found: {proposal_id}")
            return dict(proposal)

    def update_action_proposal(self, session_id: str, proposal_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            proposal = self._find_action_proposal_by_id_locked(session_id, proposal_id)
            if proposal is None:
                raise KeyError(f"action proposal not found: {proposal_id}")
            proposal.update(updates)
            proposal["updated_at"] = _now()
            if self._state_store:
                self._state_store.save_action_proposal(session_id, proposal)
            return dict(proposal)

    def add_action_audit_event(
        self,
        session_id: str,
        proposal_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            event = _action_audit_event(proposal_id, event_type, actor, payload or {})
            self._action_audit_events[session_id].append(event)
            if self._state_store:
                self._state_store.save_action_audit_event(session_id, event)
            return dict(event)

    def action_audit_events(
        self,
        session_id: str,
        *,
        proposal_id: str = "",
        email_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_action_state_loaded(session_id)
            events = self._action_audit_events[session_id]
            if proposal_id:
                events = [item for item in events if item.get("proposal_id") == proposal_id]
            if email_id:
                proposal_ids = {
                    item["proposal_id"]
                    for item in self._action_proposals[session_id]
                    if item.get("email_id") == email_id
                }
                events = [item for item in events if item.get("proposal_id") in proposal_ids]
            selected = list(events[-limit:]) if limit > 0 else list(events)
            return [dict(item) for item in selected]

    def clean_rules(self, session_id: str, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_clean_rules_loaded(session_id)
            rules = self._clean_rules[session_id]
            if status:
                rules = [item for item in rules if item.get("status") == status]
            selected = list(rules[-limit:]) if limit > 0 else list(rules)
            return [dict(item) for item in selected]

    def create_clean_rule_once(self, session_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_clean_rules_loaded(session_id)
            rule_id = str(rule["rule_id"])
            existing = self._find_clean_rule_locked(session_id, rule_id)
            if existing is not None:
                return {"created": False, "rule": dict(existing)}
            item = dict(rule)
            if self._state_store:
                self._state_store.save_clean_rule(session_id, item)
            self._clean_rules[session_id].append(item)
            return {"created": True, "rule": dict(item)}

    def save_clean_rule(self, session_id: str, rule: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_clean_rules_loaded(session_id)
            rule_id = str(rule["rule_id"])
            existing = self._find_clean_rule_locked(session_id, rule_id)
            item = dict(rule)
            if existing is None:
                self._clean_rules[session_id].append(item)
            else:
                existing.clear()
                existing.update(item)
            if self._state_store:
                self._state_store.save_clean_rule(session_id, item)
            return dict(item)

    def get_clean_rule(self, session_id: str, rule_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_clean_rules_loaded(session_id)
            rule = self._find_clean_rule_locked(session_id, rule_id)
            if rule is None:
                raise KeyError(f"clean rule not found: {rule_id}")
            return dict(rule)

    def approve_clean_rule(self, session_id: str, rule_id: str) -> dict[str, Any]:
        rule = self.get_clean_rule(session_id, rule_id)
        return self.save_clean_rule(session_id, enable_rule(rule))

    def disable_clean_rule(self, session_id: str, rule_id: str) -> dict[str, Any]:
        rule = self.get_clean_rule(session_id, rule_id)
        return self.save_clean_rule(session_id, disable_rule(rule))

    def clean_policy(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_clean_policy_loaded(session_id)
            return dict(self._clean_policies[session_id])

    def save_clean_policy(self, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            item = normalize_clean_policy(policy)
            self._clean_policies[session_id] = item
            if self._state_store:
                save_clean_policy = getattr(self._state_store, "save_clean_policy", None)
                if save_clean_policy:
                    save_clean_policy(session_id, item)
            return dict(item)

    def add_clean_audit_event(
        self,
        session_id: str,
        *,
        run_id: str,
        email_id: str,
        event_type: str,
        actor: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._ensure_clean_audit_loaded(session_id)
            event = new_clean_audit_event(
                run_id=run_id,
                email_id=email_id,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
            )
            self._clean_audit_events[session_id].append(event)
            if self._state_store:
                self._state_store.save_clean_audit_event(session_id, event)
            return dict(event)

    def clean_audit_events(
        self,
        session_id: str,
        *,
        run_id: str = "",
        email_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_clean_audit_loaded(session_id)
            events = self._clean_audit_events[session_id]
            if run_id:
                events = [item for item in events if item.get("run_id") == run_id]
            if email_id:
                events = [item for item in events if item.get("email_id") == email_id]
            selected = list(events[-limit:]) if limit > 0 else list(events)
            return [dict(item) for item in selected]

    def _ensure_email_preferences_loaded(self, session_id: str) -> None:
        if session_id in self._email_preferences:
            return
        preferences = None
        if self._state_store:
            preferences = self._state_store.load_email_preferences(session_id)
        self._email_preferences[session_id] = _merge_preferences(preferences)

    def _ensure_email_scheduler_loaded(self, session_id: str) -> None:
        if session_id in self._email_scheduler:
            return
        self._reload_email_scheduler_state(session_id)

    def _reload_email_scheduler_state(self, session_id: str) -> None:
        state = _default_email_scheduler_state()
        if self._state_store:
            state["reported_email_ids"] = self._state_store.load_reported_email_ids(session_id)
            state["notifications"] = self._state_store.load_notifications(session_id)
            state["scan_history"] = self._state_store.load_scan_history(session_id)
            if state["scan_history"]:
                state["last_scan_at"] = str(state["scan_history"][-1].get("created_at", ""))
        self._email_scheduler[session_id] = state

    def _persist_email_preferences(self, session_id: str) -> None:
        if self._state_store:
            self._state_store.save_email_preferences(session_id, _copy_preferences(self._email_preferences[session_id]))

    def _add_email_notification_locked(self, session_id: str, notification: dict[str, Any]) -> dict[str, Any]:
        state = self._email_scheduler[session_id]
        item = _notification_item(notification)
        state["notifications"].append(item)
        if self._state_store:
            self._state_store.save_notification(session_id, item)
        return dict(item)

    def _ensure_action_state_loaded(self, session_id: str) -> None:
        if session_id in self._action_proposals and session_id in self._action_audit_events:
            return
        self._reload_action_state(session_id)

    def _reload_action_state(self, session_id: str) -> None:
        proposals: list[dict[str, Any]] = []
        audit_events: list[dict[str, Any]] = []
        if self._state_store:
            proposals = self._state_store.load_action_proposals(session_id)
            audit_events = self._state_store.load_action_audit_events(session_id)
        self._action_proposals[session_id] = [dict(item) for item in proposals]
        self._action_audit_events[session_id] = [dict(item) for item in audit_events]

    def _ensure_clean_rules_loaded(self, session_id: str) -> None:
        if session_id in self._clean_rules:
            return
        rules: list[dict[str, Any]] = []
        if self._state_store:
            load_clean_rules = getattr(self._state_store, "load_clean_rules", None)
            if load_clean_rules:
                rules = load_clean_rules(session_id)
        self._clean_rules[session_id] = [dict(item) for item in rules]

    def _ensure_clean_policy_loaded(self, session_id: str) -> None:
        if session_id in self._clean_policies:
            return
        policy = None
        if self._state_store:
            load_clean_policy = getattr(self._state_store, "load_clean_policy", None)
            if load_clean_policy:
                policy = load_clean_policy(session_id)
        self._clean_policies[session_id] = normalize_clean_policy(policy)

    def _ensure_clean_audit_loaded(self, session_id: str) -> None:
        if session_id in self._clean_audit_events:
            return
        events: list[dict[str, Any]] = []
        if self._state_store:
            load_clean_audit_events = getattr(self._state_store, "load_clean_audit_events", None)
            if load_clean_audit_events:
                events = load_clean_audit_events(session_id)
        self._clean_audit_events[session_id] = [dict(item) for item in events]

    def _find_action_proposal_locked(self, session_id: str, email_id: str, action: str) -> dict[str, Any] | None:
        for item in self._action_proposals[session_id]:
            if item.get("email_id") == email_id and item.get("action") == action:
                return item
        return None

    def _find_action_proposal_by_id_locked(self, session_id: str, proposal_id: str) -> dict[str, Any] | None:
        for item in self._action_proposals[session_id]:
            if item.get("proposal_id") == proposal_id:
                return item
        return None

    def _find_clean_rule_locked(self, session_id: str, rule_id: str) -> dict[str, Any] | None:
        for item in self._clean_rules[session_id]:
            if item.get("rule_id") == rule_id:
                return item
        return None


_EMAIL_LIST_KEYS = {
    "important_senders",
    "important_domains",
    "ignored_senders",
    "ignored_domains",
    "ignored_categories",
}
_EMAIL_STRING_KEYS = {
    "report_schedule",
    "timezone",
}


def _default_email_preferences() -> dict[str, Any]:
    return {
        "important_senders": [],
        "important_domains": [],
        "ignored_senders": [],
        "ignored_domains": [],
        "ignored_categories": [],
        "report_schedule": "",
        "timezone": "",
    }


def _merge_preferences(preferences: dict[str, Any] | None) -> dict[str, Any]:
    merged = _default_email_preferences()
    if not preferences:
        return merged
    for key, value in preferences.items():
        if key in _EMAIL_LIST_KEYS:
            merged[key] = _normalized_list(value)
        elif key in _EMAIL_STRING_KEYS:
            merged[key] = str(value).strip()
    return merged


def _default_email_scheduler_state() -> dict[str, Any]:
    return {
        "reported_email_ids": set(),
        "notifications": [],
        "scan_history": [],
        "last_scan_at": "",
    }


def _copy_preferences(preferences: dict[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for key, value in preferences.items():
        copied[key] = list(value) if isinstance(value, list) else value
    return copied


def _copy_scheduler_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "reported_email_ids": sorted(state["reported_email_ids"]),
        "notifications": [dict(item) for item in state["notifications"]],
        "scan_history": [dict(item) for item in state["scan_history"]],
        "last_scan_at": state["last_scan_at"],
    }


def _normalized_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        raise TypeError("email preference list value must be a string or list")
    normalized = [_normalize_preference_value(item) for item in values]
    return sorted({item for item in normalized if item})


def _normalize_preference_value(value: Any) -> str:
    return str(value).strip().lower()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _notification_item(notification: dict[str, Any]) -> dict[str, Any]:
    return {
        "notification_id": notification.get("notification_id") or f"notification-{uuid.uuid4().hex[:12]}",
        "created_at": notification.get("created_at") or _now(),
        "status": notification.get("status", "unread"),
        **notification,
    }


def _action_proposal_item(proposal: dict[str, Any]) -> dict[str, Any]:
    return normalize_action_proposal(proposal)


def _action_audit_event(
    proposal_id: str,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return new_action_audit_event(proposal_id, event_type, actor, payload)
