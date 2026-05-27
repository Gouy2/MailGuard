"""Local proposal/approval helpers for confirmed memory candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


MEMORY_PROPOSAL_SCHEMA_VERSION = 1
MEMORY_STATUSES = {"proposed", "approved", "rejected"}


def load_memory_proposals(path: str | Path) -> dict[str, Any]:
    proposal_path = Path(path)
    if not proposal_path.exists():
        return _empty_store()
    data = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposals = data.get("proposals", {})
    if not isinstance(proposals, dict):
        proposals = {}
    return {
        "schema_version": int(data.get("schema_version", MEMORY_PROPOSAL_SCHEMA_VERSION)),
        "proposals": proposals,
    }


def refresh_memory_proposals(
    path: str | Path,
    observed_report: dict[str, Any],
) -> dict[str, Any]:
    data = load_memory_proposals(path)
    proposals = dict(data.get("proposals", {}))
    created_count = 0
    updated_count = 0

    for preference in observed_report.get("proposed_preferences", []):
        proposal = _memory_proposal(preference)
        proposal_id = proposal["proposal_id"]
        existing = proposals.get(proposal_id)
        if isinstance(existing, dict):
            preserved = {
                "status": existing.get("status", "proposed"),
                "created_at": existing.get("created_at", proposal["created_at"]),
                "decided_at": existing.get("decided_at", ""),
                "decision_reason": existing.get("decision_reason", ""),
            }
            proposal.update(preserved)
            proposal["updated_at"] = _now()
            updated_count += 1
        else:
            created_count += 1
        proposal["applied_to_policy"] = _proposal_applied_to_policy(proposal)
        proposals[proposal_id] = proposal

    data = {
        "schema_version": MEMORY_PROPOSAL_SCHEMA_VERSION,
        "proposals": proposals,
    }
    _write_store(path, data)
    return {
        "schema_version": MEMORY_PROPOSAL_SCHEMA_VERSION,
        "created_count": created_count,
        "updated_count": updated_count,
        "total_count": len(proposals),
        "proposals": _sorted_proposals(proposals.values()),
        "confirmed_memory": confirmed_memory_from_store(data),
    }


def list_memory_proposals(
    path: str | Path,
    *,
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    data = load_memory_proposals(path)
    status = status.strip().lower()
    proposals = _sorted_proposals(data.get("proposals", {}).values())
    if status:
        proposals = [item for item in proposals if item.get("status") == status]
    if limit > 0:
        proposals = proposals[:limit]
    return {
        "schema_version": data["schema_version"],
        "status": status,
        "count": len(proposals),
        "proposals": proposals,
        "confirmed_memory": confirmed_memory_from_store(data),
    }


def approve_memory_proposal(path: str | Path, proposal_id: str) -> dict[str, Any]:
    return _decide_memory_proposal(path, proposal_id, status="approved", reason="")


def reject_memory_proposal(path: str | Path, proposal_id: str, *, reason: str = "") -> dict[str, Any]:
    return _decide_memory_proposal(path, proposal_id, status="rejected", reason=reason)


def confirmed_memory_from_store(data: dict[str, Any]) -> dict[str, Any]:
    confirmed = {
        "archive_senders": [],
        "archive_domains": [],
        "archive_categories": [],
    }
    for proposal in data.get("proposals", {}).values():
        if not isinstance(proposal, dict) or proposal.get("status") != "approved":
            continue
        memory_type = proposal.get("memory_type")
        value = str(proposal.get("value", "")).strip()
        if not value:
            continue
        if memory_type == "archive_sender":
            confirmed["archive_senders"].append(value)
        elif memory_type == "archive_domain":
            confirmed["archive_domains"].append(value)
        elif memory_type == "archive_category":
            confirmed["archive_categories"].append(value)
    return {key: sorted(set(values)) for key, values in confirmed.items()}


def _decide_memory_proposal(
    path: str | Path,
    proposal_id: str,
    *,
    status: str,
    reason: str,
) -> dict[str, Any]:
    proposal_id = proposal_id.strip()
    if status not in {"approved", "rejected"}:
        raise ValueError("status must be approved or rejected")
    data = load_memory_proposals(path)
    proposal = data.get("proposals", {}).get(proposal_id)
    if not isinstance(proposal, dict):
        raise KeyError(f"memory proposal not found: {proposal_id}")
    proposal = dict(proposal)
    proposal["status"] = status
    proposal["decision_reason"] = reason.strip()
    proposal["decided_at"] = _now()
    proposal["updated_at"] = proposal["decided_at"]
    proposal["applied_to_policy"] = _proposal_applied_to_policy(proposal)
    data["proposals"][proposal_id] = proposal
    _write_store(path, data)
    return {
        "proposal": proposal,
        "confirmed_memory": confirmed_memory_from_store(data),
    }


def _memory_proposal(preference: dict[str, Any]) -> dict[str, Any]:
    memory_type = str(preference.get("proposal", "")).strip().lower()
    value = str(preference.get("value", "")).strip().lower()
    now = _now()
    return {
        "proposal_id": _proposal_id(memory_type, value),
        "memory_type": memory_type,
        "value": value,
        "status": "proposed",
        "source": "observed_memory",
        "confidence": str(preference.get("confidence", "")),
        "sample_count": int(preference.get("sample_count", 0)),
        "archive_count": int(preference.get("archive_count", 0)),
        "keep_count": int(preference.get("keep_count", 0)),
        "archive_rate": float(preference.get("archive_rate", 0.0)),
        "keep_rate": float(preference.get("keep_rate", 0.0)),
        "kind": str(preference.get("kind", "")),
        "group_type": str(preference.get("group_type", "")),
        "item_type_counts": dict(preference.get("item_type_counts") or {}),
        "examples": list(preference.get("examples") or []),
        "created_at": now,
        "updated_at": now,
        "decided_at": "",
        "decision_reason": "",
        "applied_to_policy": False,
    }


def _proposal_id(memory_type: str, value: str) -> str:
    digest = hashlib.sha1(f"{memory_type}:{value}".encode("utf-8")).hexdigest()[:10]
    return f"memory-{memory_type.replace('_', '-')}-{digest}"


def _policy_active_memory_type(memory_type: str) -> bool:
    return memory_type in {"archive_sender", "archive_domain"}


def _proposal_applied_to_policy(proposal: dict[str, Any]) -> bool:
    return proposal.get("status") == "approved" and _policy_active_memory_type(str(proposal.get("memory_type", "")))


def _sorted_proposals(items) -> list[dict[str, Any]]:
    proposals = []
    for item in items:
        if not isinstance(item, dict):
            continue
        proposal = dict(item)
        proposal["applied_to_policy"] = _proposal_applied_to_policy(proposal)
        proposals.append(proposal)
    return sorted(
        proposals,
        key=lambda item: (
            _status_rank(str(item.get("status", ""))),
            -int(item.get("sample_count", 0)),
            -float(item.get("archive_rate", 0.0)),
            str(item.get("memory_type", "")),
            str(item.get("value", "")),
        ),
    )


def _status_rank(status: str) -> int:
    return {
        "proposed": 0,
        "approved": 1,
        "rejected": 2,
    }.get(status, 9)


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_PROPOSAL_SCHEMA_VERSION,
        "proposals": {},
    }


def _write_store(path: str | Path, data: dict[str, Any]) -> None:
    proposal_path = Path(path)
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _now() -> str:
    return datetime.now(UTC).isoformat()
