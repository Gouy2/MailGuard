"""Reusable archive shadow workflow orchestration."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

from .archive_shadow import (
    ArchiveSuitabilityScorer,
    archive_shadow_input_diagnostics,
    archive_shadow_record,
    build_archive_shadow_input,
    load_archive_shadow_results,
    save_archive_shadow_result,
)
from .memory_proposals import confirmed_memory_from_store, load_memory_proposals
from .real_proposal_eval import evaluate_real_proposal_labels, load_real_proposal_labels
from .runtime_env import load_server_env


EmailFetch = Callable[[dict[str, Any]], dict[str, Any]]
ProgressCallback = Callable[[str], None]


def run_archive_shadow_workflow(
    *,
    labels_path: str | Path,
    shadow_path: str | Path,
    memory_path: str | Path,
    limit: int = 20,
    model: str = "",
    timeout: float = 30.0,
    max_retries: int = 1,
    force: bool = False,
    dry_run: bool = False,
    continue_on_error: bool = False,
    fetch_missing_snippet: bool = False,
    fetch_email: EmailFetch | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    label_data = load_real_proposal_labels(labels_path)
    label_eval = evaluate_real_proposal_labels(label_data)
    label_rows = list(label_eval.get("rows", []))[: max(1, int(limit))]
    confirmed_memory = confirmed_memory_from_store(load_memory_proposals(memory_path))
    model_name = _archive_shadow_model_name(model)

    if not label_rows:
        return _empty_result(
            labels_path=labels_path,
            shadow_path=shadow_path,
            memory_path=memory_path,
            model=model_name,
            label_count=label_eval.get("sample_count", 0),
        )

    scorer = None
    cached_results = load_archive_shadow_results(shadow_path).get("results", {})
    records = []
    errors = []
    skipped_count = 0
    scored_latencies = []
    dry_run_count = 0

    for index, label_row in enumerate(label_rows, start=1):
        item = shadow_item_from_label(label_row)
        item_id = proposal_item_id(item)
        item_started_at = time.perf_counter()
        if dry_run:
            _progress(progress, f"[{index}/{len(label_rows)}] dry-run {item_id}")
            try:
                email = email_for_archive_shadow(
                    item,
                    fetch_missing_snippet=fetch_missing_snippet,
                    fetch_email=fetch_email,
                )
                shadow_input = build_archive_shadow_input(item, email, confirmed_memory=confirmed_memory)
                diagnostics = archive_shadow_input_diagnostics(shadow_input)
                record = archive_shadow_dry_run_record(
                    item=item,
                    email=email,
                    model=model_name,
                    diagnostics=diagnostics,
                    elapsed_ms=_elapsed_ms(item_started_at),
                )
                dry_run_count += 1
            except Exception as exc:
                if not continue_on_error:
                    raise
                record = archive_shadow_dry_run_record(
                    item=item,
                    email={"id": item.get("email_id", "")},
                    model=model_name,
                    diagnostics={},
                    elapsed_ms=_elapsed_ms(item_started_at),
                    error=f"{type(exc).__name__}: {exc}",
                )
                errors.append(record)
            records.append(record)
            _progress(progress, f"[{index}/{len(label_rows)}] dry-run done {item_id} {record['elapsed_ms']}ms")
            continue

        cached = cached_results.get(item_id)
        if not force and usable_archive_shadow_cache(cached, model_name):
            record = dict(cached)
            record["cached"] = True
            records.append(record)
            skipped_count += 1
            _progress(progress, f"[{index}/{len(label_rows)}] cached {item_id}")
            continue

        if scorer is None:
            scorer = ArchiveSuitabilityScorer(
                model=model or None,
                timeout=float(timeout),
                max_retries=int(max_retries),
            )
            model_name = scorer.model
        _progress(progress, f"[{index}/{len(label_rows)}] scoring {item_id}")
        try:
            email = email_for_archive_shadow(
                item,
                fetch_missing_snippet=fetch_missing_snippet,
                fetch_email=fetch_email,
            )
            shadow_input = build_archive_shadow_input(item, email, confirmed_memory=confirmed_memory)
            judgment = scorer.score(shadow_input)
            record = archive_shadow_record(
                shadow_input=shadow_input,
                judgment=judgment,
                model=model_name,
                elapsed_ms=_elapsed_ms(item_started_at),
            )
            scored_latencies.append(record["elapsed_ms"])
            _progress(
                progress,
                (
                    f"[{index}/{len(label_rows)}] done {item_id} "
                    f"{record['elapsed_ms']}ms "
                    f"{record['judgment'].get('archive_suitability', '')}/"
                    f"{record['judgment'].get('confidence', '')}"
                ),
            )
        except Exception as exc:
            if not continue_on_error:
                raise
            shadow_input = build_archive_shadow_input(
                item,
                {"id": item.get("email_id", "")},
                confirmed_memory=confirmed_memory,
            )
            record = archive_shadow_record(
                shadow_input=shadow_input,
                judgment={
                    "archive_suitability": "",
                    "confidence": "",
                    "reason_codes": [],
                    "brief_reason": "",
                },
                model=model_name,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=_elapsed_ms(item_started_at),
            )
            errors.append(record)
            scored_latencies.append(record["elapsed_ms"])
            _progress(progress, f"[{index}/{len(label_rows)}] error {item_id} {record['elapsed_ms']}ms")

        save_archive_shadow_result(shadow_path, record)
        record["cached"] = False
        records.append(record)

    return {
        "labels_path": str(Path(labels_path)),
        "shadow_path": str(Path(shadow_path)),
        "memory_path": str(Path(memory_path)),
        "model": model_name,
        "label_count": label_eval.get("sample_count", 0),
        "selected_count": len(label_rows),
        "dry_run": bool(dry_run),
        "dry_run_count": dry_run_count,
        "scored_count": len(
            [
                item
                for item in records
                if not item.get("error") and not item.get("cached") and not item.get("dry_run")
            ]
        ),
        "skipped_count": skipped_count,
        "error_count": len(errors),
        "total_elapsed_ms": _elapsed_ms(started_at),
        "avg_latency_ms": _average_ms(scored_latencies),
        "slowest_items": _slowest_shadow_items(records),
        "records": records,
        "mailbox_mutation": False,
        "proposal_mutation": False,
    }


def shadow_item_from_label(label: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": str(label.get("item_id", "")),
        "item_type": str(label.get("item_type", "")),
        "proposal_id": str(label.get("proposal_id", "")),
        "candidate_id": str(label.get("candidate_id", "")),
        "email_id": str(label.get("email_id", "")),
        "action": str(label.get("action", "archive") or "archive"),
        "risk_level": str(label.get("risk_level", "")),
        "source": str(label.get("source", "")),
        "from_name": str(label.get("from_name", "")),
        "from_email": str(label.get("from_email", "")),
        "subject": str(label.get("subject", "")),
        "snippet": str(label.get("snippet", "")),
        "category": str(label.get("category", "")),
        "importance": str(label.get("importance", "")),
        "suggested_action": str(label.get("suggested_action", "")),
        "policy_decision": str(label.get("policy_decision", "")),
        "policy_reason": str(label.get("reason", "")),
    }


def email_for_archive_shadow(
    item: dict[str, Any],
    *,
    fetch_missing_snippet: bool = False,
    fetch_email: EmailFetch | None = None,
) -> dict[str, Any]:
    if str(item.get("snippet", "")).strip() or not fetch_missing_snippet:
        return email_summary_from_label_item(item)
    if fetch_email is None:
        raise RuntimeError("fetch_email is required when fetch_missing_snippet is enabled")
    return email_summary_without_body(fetch_email(item))


def email_summary_from_label_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("email_id", "")),
        "from_name": str(item.get("from_name", "")),
        "from_email": str(item.get("from_email", "")),
        "subject": str(item.get("subject", "")),
        "snippet": str(item.get("snippet", "")),
        "received_at": "",
        "is_read": False,
        "has_attachments": False,
    }


def usable_archive_shadow_cache(record: Any, model: str) -> bool:
    if not isinstance(record, dict):
        return False
    if str(record.get("model", "")) != model:
        return False
    if str(record.get("error", "")):
        return False
    judgment = record.get("judgment")
    if not isinstance(judgment, dict):
        return False
    return str(judgment.get("archive_suitability", "")) in {"yes", "no", "unsure"}


def archive_shadow_dry_run_record(
    *,
    item: dict[str, Any],
    email: dict[str, Any],
    model: str,
    diagnostics: dict[str, Any],
    elapsed_ms: int,
    error: str = "",
) -> dict[str, Any]:
    item_id = proposal_item_id(item)
    return {
        "result_id": "",
        "item_id": item_id,
        "item_type": str(item.get("item_type", "")),
        "email_id": str(item.get("email_id", "")),
        "subject": str(item.get("subject") or email.get("subject", "")),
        "from_email": str(item.get("from_email") or email.get("from_email", "")),
        "policy_bucket": str(item.get("policy_decision") or item.get("item_type", "")),
        "model": model,
        "scored_at": "",
        "shadow_input": {},
        "judgment": {
            "archive_suitability": "",
            "confidence": "",
            "reason_codes": [],
            "brief_reason": "",
        },
        "error": error,
        "elapsed_ms": max(0, int(elapsed_ms)),
        "diagnostics": dict(diagnostics or {}),
        "dry_run": True,
        "mailbox_mutation": False,
        "proposal_mutation": False,
    }


def email_summary_without_body(email: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(email).items() if "body" not in str(key).lower()}


def proposal_item_id(item: dict[str, Any]) -> str:
    return (
        str(item.get("proposal_id", "") or "").strip()
        or str(item.get("candidate_id", "") or "").strip()
        or str(item.get("item_id", "") or "").strip()
        or str(item.get("email_id", "") or "").strip()
    )


def _empty_result(
    *,
    labels_path: str | Path,
    shadow_path: str | Path,
    memory_path: str | Path,
    model: str,
    label_count: int,
) -> dict[str, Any]:
    return {
        "labels_path": str(Path(labels_path)),
        "shadow_path": str(Path(shadow_path)),
        "memory_path": str(Path(memory_path)),
        "model": model,
        "label_count": label_count,
        "selected_count": 0,
        "scored_count": 0,
        "skipped_count": 0,
        "error_count": 0,
        "total_elapsed_ms": 0,
        "avg_latency_ms": 0,
        "slowest_items": [],
        "records": [],
        "mailbox_mutation": False,
        "proposal_mutation": False,
    }


def _archive_shadow_model_name(model: str) -> str:
    load_server_env()
    return (model.strip() or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()


def _progress(progress: ProgressCallback | None, message: str) -> None:
    if progress is not None:
        progress(message)


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)


def _average_ms(values: list[int]) -> int:
    if not values:
        return 0
    return int(sum(values) / len(values))


def _slowest_shadow_items(records: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    scored = [
        record
        for record in records
        if not record.get("cached") and int(record.get("elapsed_ms", 0)) > 0
    ]
    slowest = sorted(scored, key=lambda item: int(item.get("elapsed_ms", 0)), reverse=True)[:limit]
    return [
        {
            "item_id": record.get("item_id", ""),
            "email_id": record.get("email_id", ""),
            "elapsed_ms": int(record.get("elapsed_ms", 0)),
            "archive_suitability": (record.get("judgment") or {}).get("archive_suitability", ""),
            "confidence": (record.get("judgment") or {}).get("confidence", ""),
            "subject": record.get("subject", ""),
            "error": record.get("error", ""),
        }
        for record in slowest
    ]
