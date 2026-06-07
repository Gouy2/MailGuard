"""Reusable observed/confirmed memory workflow orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .memory_proposals import list_memory_proposals, refresh_memory_proposals
from .observed_memory import build_observed_memory_report
from .real_proposal_eval import load_real_proposal_labels


def run_observed_memory_workflow(
    *,
    labels_path: str | Path,
    min_samples: int = 1,
    limit: int = 20,
) -> dict[str, Any]:
    label_data = load_real_proposal_labels(labels_path)
    report = build_observed_memory_report(label_data, min_samples=min_samples)
    return {
        "labels_path": str(Path(labels_path)),
        "limit": max(1, int(limit)),
        "report": report,
    }


def run_memory_proposals_workflow(
    *,
    labels_path: str | Path,
    memory_path: str | Path,
    min_samples: int = 1,
    limit: int = 20,
    status: str = "",
) -> dict[str, Any]:
    label_data = load_real_proposal_labels(labels_path)
    report = build_observed_memory_report(label_data, min_samples=min_samples)
    refreshed = refresh_memory_proposals(memory_path, report)
    listed = list_memory_proposals(memory_path, status=status, limit=limit)
    return {
        "labels_path": str(Path(labels_path)),
        "memory_path": str(Path(memory_path)),
        "created_count": refreshed["created_count"],
        "updated_count": refreshed["updated_count"],
        "total_count": refreshed["total_count"],
        "status": listed["status"],
        "count": listed["count"],
        "proposals": listed["proposals"],
        "confirmed_memory": listed["confirmed_memory"],
        "mailbox_mutation": False,
        "policy_mutation": False,
    }


def run_confirmed_memory_workflow(
    *,
    memory_path: str | Path,
    limit: int = 500,
) -> dict[str, Any]:
    listed = list_memory_proposals(memory_path, status="approved", limit=limit)
    return {
        "memory_path": str(Path(memory_path)),
        "count": listed["count"],
        "proposals": listed["proposals"],
        "confirmed_memory": listed["confirmed_memory"],
        "mailbox_mutation": False,
        "policy_mutation": False,
    }
