"""Local labeling and evaluation helpers for real mailbox samples."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .artifacts import artifact_mapping, load_json_artifact, write_json_artifact


REAL_LABEL_SCHEMA_VERSION = 1
SUPPORTED_LABELS = {"important", "ignore", "later"}
REPORTABLE_LABELS = {"important", "later"}
IGNORED_LABELS = {"ignore"}


def load_real_labels(path: str | Path) -> dict[str, Any]:
    data = load_json_artifact(path, default=_empty_store())
    labels = artifact_mapping(data, "labels")
    return {
        "schema_version": int(data.get("schema_version", REAL_LABEL_SCHEMA_VERSION)),
        "labels": labels,
    }


def save_real_label(
    path: str | Path,
    *,
    email_id: str,
    label: str,
    note: str = "",
    subject: str = "",
    from_email: str = "",
    predicted_category: str = "",
    predicted_importance: str = "",
    predicted_action: str = "",
    predicted_reportable: bool | None = None,
    predicted_ignored: bool | None = None,
) -> dict[str, Any]:
    email_id = email_id.strip()
    if not email_id:
        raise ValueError("email_id is required")
    label = normalize_real_label(label)
    data = load_real_labels(path)
    record = {
        "email_id": email_id,
        "label": label,
        "note": note.strip(),
        "subject": subject.strip(),
        "from_email": from_email.strip(),
        "predicted_category": predicted_category.strip(),
        "predicted_importance": predicted_importance.strip(),
        "predicted_action": predicted_action.strip(),
        "predicted_reportable": predicted_reportable,
        "predicted_ignored": predicted_ignored,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    data["labels"][email_id] = record
    write_json_artifact(path, data)
    return record


def evaluate_real_labels(label_data: dict[str, Any]) -> dict[str, Any]:
    labels = label_data.get("labels", {})
    rows = []
    for email_id, raw_record in sorted(labels.items()):
        if not isinstance(raw_record, dict):
            continue
        label = normalize_real_label(str(raw_record.get("label", "")))
        predicted_reportable = bool(raw_record.get("predicted_reportable"))
        predicted_ignored = bool(raw_record.get("predicted_ignored"))
        expected_reportable = label in REPORTABLE_LABELS
        expected_ignored = label in IGNORED_LABELS
        rows.append(
            {
                "email_id": email_id,
                "label": label,
                "subject": str(raw_record.get("subject", "")),
                "from_email": str(raw_record.get("from_email", "")),
                "predicted_category": str(raw_record.get("predicted_category", "")),
                "predicted_importance": str(raw_record.get("predicted_importance", "")),
                "predicted_action": str(raw_record.get("predicted_action", "")),
                "expected_reportable": expected_reportable,
                "predicted_reportable": predicted_reportable,
                "expected_ignored": expected_ignored,
                "predicted_ignored": predicted_ignored,
                "reportable_correct": expected_reportable == predicted_reportable,
                "ignored_correct": expected_ignored == predicted_ignored,
                "note": str(raw_record.get("note", "")),
                "updated_at": str(raw_record.get("updated_at", "")),
            }
        )

    label_counts = Counter(row["label"] for row in rows)
    reportable_tp = sum(1 for row in rows if row["expected_reportable"] and row["predicted_reportable"])
    reportable_fn = sum(1 for row in rows if row["expected_reportable"] and not row["predicted_reportable"])
    reportable_fp = sum(1 for row in rows if not row["expected_reportable"] and row["predicted_reportable"])
    ignored_tp = sum(1 for row in rows if row["expected_ignored"] and row["predicted_ignored"])
    ignored_fp = sum(1 for row in rows if not row["expected_ignored"] and row["predicted_ignored"])
    mismatches = [
        row
        for row in rows
        if not row["reportable_correct"] or not row["ignored_correct"]
    ]
    return {
        "schema_version": int(label_data.get("schema_version", REAL_LABEL_SCHEMA_VERSION)),
        "sample_count": len(rows),
        "label_counts": dict(sorted(label_counts.items())),
        "metrics": {
            "important_recall": _ratio(reportable_tp, reportable_tp + reportable_fn),
            "important_precision": _ratio(reportable_tp, reportable_tp + reportable_fp),
            "noise_filter_precision": _ratio(ignored_tp, ignored_tp + ignored_fp),
            "false_negative_count": reportable_fn,
            "false_positive_count": reportable_fp,
            "ignored_false_positive_count": ignored_fp,
        },
        "mismatches": mismatches,
        "rows": rows,
    }


def normalize_real_label(label: str) -> str:
    normalized = label.strip().lower()
    aliases = {
        "important": "important",
        "i": "important",
        "keep": "important",
        "report": "important",
        "ignore": "ignore",
        "ignored": "ignore",
        "noise": "ignore",
        "n": "ignore",
        "later": "later",
        "review": "later",
        "defer": "later",
        "l": "later",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_LABELS:
        raise ValueError("label must be one of: important, ignore, later")
    return normalized


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _empty_store() -> dict[str, Any]:
    return {
        "schema_version": REAL_LABEL_SCHEMA_VERSION,
        "labels": {},
    }
