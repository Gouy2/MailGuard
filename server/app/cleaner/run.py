"""Audited inbox cleaner execution workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..email_classifier import classify_email
from ..email_provider import EmailProvider
from ..memory import MemoryStore
from ..provider_factory import create_email_provider
from .audit import (
    CLEAN_EXECUTION_FAILED,
    CLEAN_EXECUTION_SKIPPED,
    CLEAN_EXECUTION_STARTED,
    CLEAN_EXECUTION_SUCCEEDED,
    clean_execution_payload,
)
from .policy import normalize_clean_policy, select_policy_items
from .preview import DEFAULT_HOURS, DEFAULT_LIMIT, Classifier, run_clean_preview
from .storage import CleanPreviewStorage


DEFAULT_MAX_EXECUTE = 20


def run_clean_execution(
    *,
    memory_store: MemoryStore,
    session_id: str,
    provider: EmailProvider | None = None,
    classifier: Classifier = classify_email,
    preferences: dict[str, Any] | None = None,
    memory_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    limit: int = DEFAULT_LIMIT,
    hours: int = DEFAULT_HOURS,
    max_execute: int = DEFAULT_MAX_EXECUTE,
    execute: bool = False,
    use_policy: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    """Run cleaner preview and optionally execute eligible archive actions with audit."""
    email_provider = provider or create_email_provider()
    run = run_clean_preview(
        provider=email_provider,
        memory_store=memory_store,
        session_id=session_id,
        classifier=classifier,
        preferences=preferences,
        memory_path=memory_path,
        output_dir=output_dir,
        limit=limit,
        hours=hours,
    )
    selected, policy_skipped, policy = _select_execution_items(
        memory_store=memory_store,
        session_id=session_id,
        items=list(run.get("auto_eligible", [])),
        execute=execute,
        use_policy=use_policy,
        max_execute=max_execute,
    )
    run.update(
        {
            "execution_mode": _execution_mode(execute=execute, use_policy=use_policy, selected=selected, policy=policy),
            "selected_count": len(selected),
            "max_execute": policy.get("max_execute", max_execute) if use_policy else max_execute,
            "automation_policy": policy,
            "policy_skipped_count": len(policy_skipped),
            "policy_skipped": policy_skipped,
            "executed_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "executed": [],
            "failed": [],
            "skipped": [],
            "audit_event_count": 0,
            "mailbox_mutation": False,
            "audit_mutation": False,
            "requires_approval": bool(selected and not execute and not use_policy),
            "approval_hint": _approval_hint(selected=selected, execute=execute, use_policy=use_policy, policy=policy),
        }
    )

    if (not execute and not use_policy) or not selected or run.get("status") != "ok":
        CleanPreviewStorage(output_dir).save(run)
        return run

    effective_actor = "automation_policy" if use_policy and actor == "system" else actor
    audit_event_count = 0
    for item in selected:
        if item.get("action") != "archive":
            event = _add_audit(
                memory_store,
                session_id,
                run_id=run["run_id"],
                item=item,
                event_type=CLEAN_EXECUTION_SKIPPED,
                actor=effective_actor,
                error=f"unsupported clean action: {item.get('action', '')}",
            )
            audit_event_count += 1
            run["skipped"].append({**item, "audit_event_id": event["event_id"]})
            continue

        started = _add_audit(
            memory_store,
            session_id,
            run_id=run["run_id"],
            item=item,
            event_type=CLEAN_EXECUTION_STARTED,
            actor=effective_actor,
        )
        audit_event_count += 1
        try:
            result = email_provider.archive(str(item["email_id"]))
        except Exception as exc:
            failed = _add_audit(
                memory_store,
                session_id,
                run_id=run["run_id"],
                item=item,
                event_type=CLEAN_EXECUTION_FAILED,
                actor=effective_actor,
                error=f"{type(exc).__name__}: {exc}",
            )
            audit_event_count += 1
            run["failed"].append(
                {
                    **item,
                    "error": f"{type(exc).__name__}: {exc}",
                    "started_audit_event_id": started["event_id"],
                    "audit_event_id": failed["event_id"],
                }
            )
            continue

        succeeded = _add_audit(
            memory_store,
            session_id,
            run_id=run["run_id"],
            item=item,
            event_type=CLEAN_EXECUTION_SUCCEEDED,
            actor=effective_actor,
            result=result,
        )
        audit_event_count += 1
        run["executed"].append(
            {
                **item,
                "result": result,
                "started_audit_event_id": started["event_id"],
                "audit_event_id": succeeded["event_id"],
            }
        )

    run["executed_count"] = len(run["executed"])
    run["failed_count"] = len(run["failed"])
    run["skipped_count"] = len(run["skipped"])
    run["audit_event_count"] = audit_event_count
    run["mailbox_mutation"] = bool(run["executed"])
    run["audit_mutation"] = audit_event_count > 0
    if run["failed_count"]:
        run["status"] = "partial_error" if run["executed_count"] else "error"
        run["error"] = f"{run['failed_count']} clean archive actions failed"
        run["errors"].append(run["error"])
    CleanPreviewStorage(output_dir).save(run)
    return run


def clean_policy_status(memory_store: MemoryStore, session_id: str) -> dict[str, Any]:
    policy = memory_store.clean_policy(session_id)
    return {
        "policy": policy,
        "mailbox_mutation": False,
        "policy_mutation": False,
    }


def save_clean_policy(memory_store: MemoryStore, session_id: str, policy: dict[str, Any]) -> dict[str, Any]:
    saved = memory_store.save_clean_policy(session_id, policy)
    return {
        "policy": saved,
        "mailbox_mutation": False,
        "policy_mutation": True,
    }


def clean_audit_log(
    *,
    memory_store: MemoryStore,
    session_id: str,
    run_id: str = "",
    email_id: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    events = memory_store.clean_audit_events(session_id, run_id=run_id, email_id=email_id, limit=limit)
    return {
        "count": len(events),
        "run_id": run_id,
        "email_id": email_id,
        "events": events,
    }


def _add_audit(
    memory_store: MemoryStore,
    session_id: str,
    *,
    run_id: str,
    item: dict[str, Any],
    event_type: str,
    actor: str,
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return memory_store.add_clean_audit_event(
        session_id,
        run_id=run_id,
        email_id=str(item.get("email_id", "")),
        event_type=event_type,
        actor=actor,
        payload=clean_execution_payload(item, result=result, error=error),
    )


def _select_execution_items(
    *,
    memory_store: MemoryStore,
    session_id: str,
    items: list[dict[str, Any]],
    execute: bool,
    use_policy: bool,
    max_execute: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if use_policy:
        policy = normalize_clean_policy(memory_store.clean_policy(session_id))
        selected, skipped = select_policy_items(items, policy)
        return selected, skipped, policy
    selected = items[: _bounded_int(max_execute, default=DEFAULT_MAX_EXECUTE, minimum=1, maximum=200)]
    return selected, [], {}


def _execution_mode(
    *,
    execute: bool,
    use_policy: bool,
    selected: list[dict[str, Any]],
    policy: dict[str, Any],
) -> str:
    if use_policy:
        if not policy.get("enabled", False):
            return "policy_disabled"
        return "policy_execute" if selected else "policy_noop"
    return "execute" if execute else "approval_required"


def _approval_hint(
    *,
    selected: list[dict[str, Any]],
    execute: bool,
    use_policy: bool,
    policy: dict[str, Any],
) -> str:
    if use_policy:
        if not policy.get("enabled", False):
            return "Cleaner automation policy is disabled; run clean-policy enable before using --policy."
        if not selected:
            return "Cleaner automation policy allowed no auto-eligible mail in this run."
        return ""
    if selected and not execute:
        return "Re-run clean-run with --yes to archive selected auto-eligible mail."
    return ""


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
