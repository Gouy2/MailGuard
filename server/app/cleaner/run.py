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
    selected = list(run.get("auto_eligible", []))[: _bounded_int(max_execute, default=DEFAULT_MAX_EXECUTE, minimum=1, maximum=200)]
    run.update(
        {
            "execution_mode": "execute" if execute else "approval_required",
            "selected_count": len(selected),
            "max_execute": max_execute,
            "executed_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "executed": [],
            "failed": [],
            "skipped": [],
            "audit_event_count": 0,
            "mailbox_mutation": False,
            "audit_mutation": False,
            "requires_approval": bool(selected and not execute),
            "approval_hint": "Re-run clean-run with --yes to archive selected auto-eligible mail." if selected and not execute else "",
        }
    )

    if not execute or run.get("status") != "ok":
        CleanPreviewStorage(output_dir).save(run)
        return run

    audit_event_count = 0
    for item in selected:
        if item.get("action") != "archive":
            event = _add_audit(
                memory_store,
                session_id,
                run_id=run["run_id"],
                item=item,
                event_type=CLEAN_EXECUTION_SKIPPED,
                actor=actor,
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
            actor=actor,
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
                actor=actor,
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
            actor=actor,
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


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
