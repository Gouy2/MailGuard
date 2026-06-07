"""Read-only inbox cleaner preview workflow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from ..archive import build_archive_plan
from ..email_classifier import classify_email
from ..email_provider import EmailMessage, EmailProvider
from ..memory import MemoryStore
from ..memory_proposals import confirmed_memory_from_store, load_memory_proposals
from ..provider_factory import create_email_provider
from ..runtime_env import SERVER_ROOT
from .rules import matching_rules
from .storage import CleanPreviewStorage


CLEAN_PREVIEW_SCHEMA_VERSION = 1
DEFAULT_MEMORY_PATH = SERVER_ROOT / "data" / "memory_proposals.json"
DEFAULT_LIMIT = 50
DEFAULT_HOURS = 168

Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]


def run_clean_preview(
    *,
    provider: EmailProvider | None = None,
    memory_store: MemoryStore | None = None,
    session_id: str = "email-cli",
    classifier: Classifier = classify_email,
    preferences: dict[str, Any] | None = None,
    memory_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    limit: int = DEFAULT_LIMIT,
    hours: int = DEFAULT_HOURS,
) -> dict[str, Any]:
    """Build an auditable dry-run preview of emails eligible for future auto-archive."""
    budget = {
        "limit": _bounded_int(limit, default=DEFAULT_LIMIT, minimum=1, maximum=500),
        "hours": _bounded_int(hours, default=DEFAULT_HOURS, minimum=1, maximum=24 * 365),
    }
    email_provider = provider or create_email_provider()
    active_preferences = (
        dict(preferences)
        if preferences is not None
        else (memory_store.email_preferences(session_id) if memory_store is not None else {})
    )
    storage = CleanPreviewStorage(output_dir)
    run = _base_run(email_provider, budget)

    try:
        confirmed_memory = _load_confirmed_memory(memory_path or DEFAULT_MEMORY_PATH)
        clean_rules = _load_clean_rules(memory_store, session_id)
        emails = _filter_recent(
            email_provider.list_recent(limit=budget["limit"], unread_only=False),
            hours=budget["hours"],
        )
        plan = build_archive_plan(
            emails=emails,
            classifier=classifier,
            preferences=active_preferences,
            confirmed_memory=confirmed_memory,
            provider_name=type(email_provider).__name__,
        )
        _fill_plan_result(run, plan, confirmed_memory=confirmed_memory, clean_rules=clean_rules)
        run["status"] = "ok"
    except Exception as exc:
        run["status"] = "error"
        run["error"] = f"{type(exc).__name__}: {exc}"
        run["errors"].append(run["error"])
    finally:
        run["finished_at"] = _now()
        storage.save(run)

    return run


def _base_run(provider: EmailProvider, budget: dict[str, int]) -> dict[str, Any]:
    return {
        "schema_version": CLEAN_PREVIEW_SCHEMA_VERSION,
        "run_id": _new_run_id(),
        "created_at": _now(),
        "finished_at": "",
        "status": "running",
        "execution_mode": "dry_run",
        "provider": _provider_summary(provider),
        "budget": dict(budget),
        "fetched": 0,
        "scanned_count": 0,
        "auto_eligible_count": 0,
        "protected_count": 0,
        "candidate_count": 0,
        "no_action_count": 0,
        "auto_eligible": [],
        "protected": [],
        "candidates": [],
        "no_action": [],
        "errors": [],
        "error": "",
        "artifact_path": "",
        "mailbox_mutation": False,
        "proposal_mutation": False,
        "audit_mutation": False,
        "llm_authorization": False,
        "clean_rule_count": 0,
        "enabled_clean_rule_count": 0,
        "archive_rule_count": 0,
        "protect_rule_count": 0,
    }


def _fill_plan_result(
    run: dict[str, Any],
    plan: Any,
    *,
    confirmed_memory: dict[str, Any],
    clean_rules: list[dict[str, Any]],
) -> None:
    auto_eligible: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    no_action: list[dict[str, Any]] = []
    enabled_rules = [dict(rule) for rule in clean_rules if rule.get("status") == "enabled"]
    archive_rules = [rule for rule in enabled_rules if rule.get("action") == "archive"]
    protect_rules = [rule for rule in enabled_rules if rule.get("action") == "protect"]

    for planned in plan.planned:
        item = planned.to_proposal_dict()
        protect_rule = _first_rule_match(protect_rules, planned.email, planned.classification)
        if protect_rule:
            protected.append(
                _protected_from_archive_item(
                    item,
                    reason="enabled protect rule blocks automatic cleaning",
                    clean_rule_match=protect_rule,
                )
            )
            continue
        archive_rule = _first_rule_match(archive_rules, planned.email, planned.classification)
        if archive_rule:
            auto_eligible.append(_auto_item(item, clean_rule_match=archive_rule))
            continue
        memory_match = _archive_memory_match(item.get("from_email", ""), confirmed_memory)
        if memory_match:
            auto_eligible.append(_auto_item(item, memory_match=memory_match))
            continue
        candidates.append(_candidate_from_planned(item))

    for item in plan.candidates:
        protect_rule = _first_rule_match(protect_rules, item.email, item.classification)
        if protect_rule:
            protected.append(
                _protected_from_scan_item(
                    item,
                    reason="enabled protect rule blocks automatic cleaning",
                    clean_rule_match=protect_rule,
                )
            )
            continue
        archive_rule = _first_rule_match(archive_rules, item.email, item.classification)
        if archive_rule:
            auto_eligible.append(_auto_item_from_scan(item, clean_rule_match=archive_rule))
            continue
        candidates.append(item.to_dict())

    for item in plan.protected:
        archive_rule = _first_rule_match(archive_rules, item.email, item.classification)
        protect_rule = _first_rule_match(protect_rules, item.email, item.classification)
        protected.append(
            _protected_item(
                item.to_dict(),
                confirmed_memory=confirmed_memory,
                archive_rule_match=archive_rule,
                protect_rule_match=protect_rule,
            )
        )

    for item in plan.no_action:
        protect_rule = _first_rule_match(protect_rules, item.email, item.classification)
        if protect_rule:
            protected.append(
                _protected_from_scan_item(
                    item,
                    reason="enabled protect rule blocks automatic cleaning",
                    clean_rule_match=protect_rule,
                )
            )
            continue
        archive_rule = _first_rule_match(archive_rules, item.email, item.classification)
        if archive_rule:
            auto_eligible.append(_auto_item_from_scan(item, clean_rule_match=archive_rule))
            continue
        no_action.append(item.to_dict())

    run.update(
        {
            "fetched": plan.fetched,
            "scanned_count": plan.fetched,
            "auto_eligible_count": len(auto_eligible),
            "protected_count": len(protected),
            "candidate_count": len(candidates),
            "no_action_count": len(no_action),
            "auto_eligible": auto_eligible,
            "protected": protected,
            "candidates": candidates,
            "no_action": no_action,
            "clean_rule_count": len(clean_rules),
            "enabled_clean_rule_count": len(enabled_rules),
            "archive_rule_count": len(archive_rules),
            "protect_rule_count": len(protect_rules),
        }
    )


def _auto_item(
    item: dict[str, Any],
    *,
    memory_match: str = "",
    clean_rule_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(item.get("evidence") or {})
    policy = dict(evidence.get("policy") or {})
    classification = dict(evidence.get("classification") or {})
    authority = "clean_rule" if clean_rule_match else "confirmed_memory"
    policy_reason = policy.get("reason", "")
    if clean_rule_match:
        policy_reason = "enabled clean rule authorizes automatic archive"
    return {
        "item_type": "auto_eligible",
        "status": "dry_run",
        "action": item.get("action", "archive"),
        "risk_level": "auto_eligible_low",
        "source": authority,
        "email_id": item.get("email_id", ""),
        "thread_id": item.get("thread_id", ""),
        "from_name": item.get("from_name", ""),
        "from_email": item.get("from_email", ""),
        "subject": item.get("subject", ""),
        "snippet": item.get("snippet", ""),
        "category": classification.get("category", ""),
        "importance": classification.get("importance", ""),
        "suggested_action": classification.get("suggested_action", ""),
        "policy_decision": policy.get("decision", ""),
        "policy_reason": policy_reason,
        "reason": item.get("reason", ""),
        "memory_match": memory_match,
        "clean_rule_match": _rule_match(clean_rule_match),
        "automation_authority": authority,
        "mailbox_mutation": False,
    }


def _auto_item_from_scan(item: Any, *, clean_rule_match: dict[str, Any]) -> dict[str, Any]:
    data = item.to_dict()
    return _auto_item(
        {
            "action": "archive",
            "email_id": data.get("email_id", ""),
            "thread_id": getattr(item.email, "thread_id", ""),
            "from_name": data.get("from_name", ""),
            "from_email": data.get("from_email", ""),
            "subject": data.get("subject", ""),
            "snippet": getattr(item.email, "snippet", ""),
            "reason": data.get("reason", data.get("policy_reason", "")),
            "evidence": {
                "classification": item.classification.to_evidence_dict(),
                "email": item.email.to_summary_dict(),
                "policy": item.policy.to_dict(),
            },
        },
        clean_rule_match=clean_rule_match,
    )


def _candidate_from_planned(item: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(item.get("evidence") or {})
    policy = dict(evidence.get("policy") or {})
    classification = dict(evidence.get("classification") or {})
    return {
        "item_type": "candidate",
        "action": item.get("action", "archive"),
        "risk_level": "candidate",
        "source": "policy_without_confirmed_rule",
        "email_id": item.get("email_id", ""),
        "thread_id": item.get("thread_id", ""),
        "from_name": item.get("from_name", ""),
        "from_email": item.get("from_email", ""),
        "subject": item.get("subject", ""),
        "snippet": item.get("snippet", ""),
        "category": classification.get("category", ""),
        "importance": classification.get("importance", ""),
        "suggested_action": classification.get("suggested_action", ""),
        "policy_decision": policy.get("decision", ""),
        "policy_reason": "not auto-eligible without enabled clean rule or confirmed sender/domain memory",
        "reason": item.get("reason", ""),
    }


def _protected_from_archive_item(
    item: dict[str, Any],
    *,
    reason: str,
    clean_rule_match: dict[str, Any],
) -> dict[str, Any]:
    protected = _candidate_from_planned(item)
    protected["item_type"] = "protected"
    protected["policy_decision"] = "protected"
    protected["policy_reason"] = reason
    protected["clean_rule_match"] = _rule_match(clean_rule_match)
    protected["auto_eligible_blocked"] = True
    return protected


def _protected_from_scan_item(
    item: Any,
    *,
    reason: str,
    clean_rule_match: dict[str, Any],
) -> dict[str, Any]:
    protected = item.to_dict()
    protected["item_type"] = "protected"
    protected["policy_decision"] = "protected"
    protected["policy_reason"] = reason
    protected["clean_rule_match"] = _rule_match(clean_rule_match)
    protected["auto_eligible_blocked"] = True
    return protected


def _protected_item(
    item: dict[str, Any],
    *,
    confirmed_memory: dict[str, Any],
    archive_rule_match: dict[str, Any] | None = None,
    protect_rule_match: dict[str, Any] | None = None,
) -> dict[str, Any]:
    protected = dict(item)
    memory_match = _archive_memory_match(protected.get("from_email", ""), confirmed_memory)
    if memory_match:
        protected["memory_match"] = memory_match
        protected["auto_eligible_blocked"] = True
    if archive_rule_match:
        protected["clean_rule_match"] = _rule_match(archive_rule_match)
        protected["auto_eligible_blocked"] = True
    if protect_rule_match:
        protected["protect_rule_match"] = _rule_match(protect_rule_match)
    return protected


def _load_confirmed_memory(memory_path: str | Path) -> dict[str, Any]:
    return confirmed_memory_from_store(load_memory_proposals(memory_path))


def _load_clean_rules(memory_store: MemoryStore | None, session_id: str) -> list[dict[str, Any]]:
    if memory_store is None:
        return []
    try:
        return memory_store.clean_rules(session_id, limit=0)
    except AttributeError:
        return []


def _first_rule_match(rules: list[dict[str, Any]], email: Any, classification: Any) -> dict[str, Any] | None:
    matches = matching_rules(
        rules,
        email=email.to_summary_dict(),
        classification=classification,
        status="enabled",
    )
    return matches[0] if matches else None


def _rule_match(rule: dict[str, Any] | None) -> dict[str, Any]:
    if not rule:
        return {}
    return {
        "rule_id": rule.get("rule_id", ""),
        "action": rule.get("action", ""),
        "scope": rule.get("scope", ""),
        "value": rule.get("value", ""),
        "source": rule.get("source", ""),
    }


def _archive_memory_match(from_email: str, confirmed_memory: dict[str, Any]) -> str:
    sender = str(from_email or "").strip().lower()
    domain = _email_domain(sender)
    senders = _normalized_list(confirmed_memory.get("archive_senders", []))
    if sender and sender in senders:
        return f"archive_sender:{sender}"
    domains = _normalized_list(confirmed_memory.get("archive_domains", []))
    matched_domain = next((item for item in domains if _matching_domain(domain, item)), "")
    if matched_domain:
        return f"archive_domain:{matched_domain}"
    return ""


def _filter_recent(emails: list[EmailMessage], *, hours: int) -> list[EmailMessage]:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, hours))
    return [email for email in emails if _received_after(email.received_at, cutoff)]


def _received_after(value: str, cutoff: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed >= cutoff


def _provider_summary(provider: EmailProvider) -> dict[str, Any]:
    status = getattr(provider, "status", None)
    if callable(status):
        try:
            raw = status()
        except Exception as exc:  # pragma: no cover - defensive metadata boundary
            raw = {"provider": type(provider).__name__, "status": "error", "error": str(exc)}
    else:
        raw = {"provider": type(provider).__name__, "status": "available"}
    keys = (
        "provider",
        "status",
        "email",
        "host",
        "port",
        "mailbox",
        "mailbox_display",
        "selected_mailbox",
        "message_count",
        "unread_count",
        "current_mailbox_count",
        "visible_mailbox_count",
    )
    summary = {key: raw[key] for key in keys if key in raw}
    summary.setdefault("provider", type(provider).__name__)
    summary["mailbox_mutation"] = False
    return summary


def _normalized_list(value: Any) -> set[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return {str(item).strip().lower() for item in items if str(item).strip()}


def _email_domain(from_email: str) -> str:
    if "@" not in from_email:
        return ""
    return from_email.rsplit("@", 1)[1].strip().lower()


def _matching_domain(domain: str, preferred: str) -> bool:
    return bool(domain and preferred and (domain == preferred or domain.endswith(f".{preferred}")))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"clean-{timestamp}-{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return datetime.now(UTC).isoformat()
