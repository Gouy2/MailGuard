"""Local labeling and evaluation helpers for real archive proposals."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import artifact_mapping, load_json_artifact, write_json_artifact


REAL_PROPOSAL_LABEL_SCHEMA_VERSION = 1
SUPPORTED_PROPOSAL_LABELS = {"archive", "keep", "unsure"}


def load_real_proposal_labels(path: str | Path) -> dict[str, Any]:
    data = load_json_artifact(path, default=_empty_store())
    labels = artifact_mapping(data, "labels")
    return {
        "schema_version": int(data.get("schema_version", REAL_PROPOSAL_LABEL_SCHEMA_VERSION)),
        "labels": labels,
    }


def save_real_proposal_label(
    path: str | Path,
    *,
    proposal: dict[str, Any],
    label: str,
    note: str = "",
) -> dict[str, Any]:
    proposal_id = str(proposal.get("proposal_id", "")).strip()
    candidate_id = str(proposal.get("candidate_id", "")).strip()
    item_id = proposal_id or candidate_id
    email_id = str(proposal.get("email_id", "")).strip()
    if not item_id:
        raise ValueError("proposal_id or candidate_id is required")
    if not email_id:
        raise ValueError("email_id is required")

    normalized_label = normalize_proposal_label(label)
    data = load_real_proposal_labels(path)
    record = {
        "item_id": item_id,
        "item_type": str(proposal.get("item_type", "proposal" if proposal_id else "candidate")),
        "proposal_id": proposal_id,
        "candidate_id": candidate_id,
        "email_id": email_id,
        "label": normalized_label,
        "note": note.strip(),
        "action": str(proposal.get("action", "")),
        "risk_level": str(proposal.get("risk_level", "")),
        "source": str(proposal.get("source", "")),
        "category": str(proposal.get("category", "")),
        "importance": str(proposal.get("importance", "")),
        "suggested_action": str(proposal.get("suggested_action", "")),
        "policy_decision": str(proposal.get("policy_decision", "")),
        "subject": str(proposal.get("subject", "")).strip(),
        "snippet": str(proposal.get("snippet", "")).strip(),
        "from_email": str(proposal.get("from_email", "")).strip(),
        "from_name": str(proposal.get("from_name", "")).strip(),
        "reason": str(proposal.get("reason", "")).strip(),
        "labeled_at": datetime.now().astimezone().isoformat(),
    }
    data["labels"][item_id] = record
    write_json_artifact(path, data)
    return record


def evaluate_real_proposal_labels(label_data: dict[str, Any]) -> dict[str, Any]:
    labels = label_data.get("labels", {})
    rows = []
    for item_id, raw_record in sorted(labels.items()):
        if not isinstance(raw_record, dict):
            continue
        label = normalize_proposal_label(str(raw_record.get("label", "")))
        proposal_id = str(raw_record.get("proposal_id", "")) or item_id
        rows.append(
            {
                "item_id": str(raw_record.get("item_id", "")) or item_id,
                "item_type": str(raw_record.get("item_type", "")) or "proposal",
                "proposal_id": proposal_id,
                "candidate_id": str(raw_record.get("candidate_id", "")),
                "email_id": str(raw_record.get("email_id", "")),
                "label": label,
                "action": str(raw_record.get("action", "")),
                "risk_level": str(raw_record.get("risk_level", "")),
                "source": str(raw_record.get("source", "")),
                "category": str(raw_record.get("category", "")),
                "importance": str(raw_record.get("importance", "")),
                "suggested_action": str(raw_record.get("suggested_action", "")),
                "policy_decision": str(raw_record.get("policy_decision", "")),
                "subject": str(raw_record.get("subject", "")),
                "snippet": str(raw_record.get("snippet", "")),
                "from_email": str(raw_record.get("from_email", "")),
                "from_name": str(raw_record.get("from_name", "")),
                "reason": str(raw_record.get("reason", "")),
                "note": str(raw_record.get("note", "")),
                "labeled_at": str(raw_record.get("labeled_at", "")),
            }
        )

    summary = _label_summary(rows)
    by_item_type = {
        item_type: _label_summary([row for row in rows if row["item_type"] == item_type])
        for item_type in sorted({row["item_type"] for row in rows})
    }

    return {
        "schema_version": int(label_data.get("schema_version", REAL_PROPOSAL_LABEL_SCHEMA_VERSION)),
        **summary,
        "by_item_type": by_item_type,
        "rows": rows,
    }


def normalize_proposal_label(label: str) -> str:
    normalized = label.strip().lower()
    aliases = {
        "archive": "archive",
        "safe_archive": "archive",
        "safe": "archive",
        "yes": "archive",
        "y": "archive",
        "a": "archive",
        "keep": "keep",
        "no": "keep",
        "n": "keep",
        "k": "keep",
        "false_positive": "keep",
        "unsure": "unsure",
        "unclear": "unsure",
        "maybe": "unsure",
        "u": "unsure",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_PROPOSAL_LABELS:
        raise ValueError("label must be one of: archive, keep, unsure")
    return normalized


def _label_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    label_counts = Counter(row["label"] for row in rows)
    decisive = [row for row in rows if row["label"] in {"archive", "keep"}]
    accepted = [row for row in rows if row["label"] == "archive"]
    rejected = [row for row in rows if row["label"] == "keep"]
    unsure = [row for row in rows if row["label"] == "unsure"]
    return {
        "sample_count": len(rows),
        "decisive_count": len(decisive),
        "label_counts": dict(sorted(label_counts.items())),
        "metrics": {
            "archive_acceptance_precision": _ratio(len(accepted), len(decisive)),
            "false_positive_count": len(rejected),
            "unsure_count": len(unsure),
        },
        "false_positive_proposals": rejected,
        "unsure_proposals": unsure,
    }


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version": REAL_PROPOSAL_LABEL_SCHEMA_VERSION,
        "labels": {},
    }
