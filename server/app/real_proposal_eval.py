"""Local labeling and evaluation helpers for real archive proposals."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REAL_PROPOSAL_LABEL_SCHEMA_VERSION = 1
SUPPORTED_PROPOSAL_LABELS = {"archive", "keep", "unsure"}


def load_real_proposal_labels(path: str | Path) -> dict[str, Any]:
    label_path = Path(path)
    if not label_path.exists():
        return {
            "schema_version": REAL_PROPOSAL_LABEL_SCHEMA_VERSION,
            "labels": {},
        }
    data = json.loads(label_path.read_text(encoding="utf-8"))
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        labels = {}
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
    email_id = str(proposal.get("email_id", "")).strip()
    if not proposal_id:
        raise ValueError("proposal_id is required")
    if not email_id:
        raise ValueError("email_id is required")

    normalized_label = normalize_proposal_label(label)
    data = load_real_proposal_labels(path)
    record = {
        "proposal_id": proposal_id,
        "email_id": email_id,
        "label": normalized_label,
        "note": note.strip(),
        "action": str(proposal.get("action", "")),
        "risk_level": str(proposal.get("risk_level", "")),
        "source": str(proposal.get("source", "")),
        "subject": str(proposal.get("subject", "")).strip(),
        "from_email": str(proposal.get("from_email", "")).strip(),
        "from_name": str(proposal.get("from_name", "")).strip(),
        "reason": str(proposal.get("reason", "")).strip(),
        "labeled_at": datetime.now().astimezone().isoformat(),
    }
    data["labels"][proposal_id] = record
    label_path = Path(path)
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return record


def evaluate_real_proposal_labels(label_data: dict[str, Any]) -> dict[str, Any]:
    labels = label_data.get("labels", {})
    rows = []
    for proposal_id, raw_record in sorted(labels.items()):
        if not isinstance(raw_record, dict):
            continue
        label = normalize_proposal_label(str(raw_record.get("label", "")))
        rows.append(
            {
                "proposal_id": proposal_id,
                "email_id": str(raw_record.get("email_id", "")),
                "label": label,
                "action": str(raw_record.get("action", "")),
                "risk_level": str(raw_record.get("risk_level", "")),
                "subject": str(raw_record.get("subject", "")),
                "from_email": str(raw_record.get("from_email", "")),
                "reason": str(raw_record.get("reason", "")),
                "note": str(raw_record.get("note", "")),
                "labeled_at": str(raw_record.get("labeled_at", "")),
            }
        )

    label_counts = Counter(row["label"] for row in rows)
    decisive = [row for row in rows if row["label"] in {"archive", "keep"}]
    accepted = [row for row in rows if row["label"] == "archive"]
    rejected = [row for row in rows if row["label"] == "keep"]
    unsure = [row for row in rows if row["label"] == "unsure"]

    return {
        "schema_version": int(label_data.get("schema_version", REAL_PROPOSAL_LABEL_SCHEMA_VERSION)),
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


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
