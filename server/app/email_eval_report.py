"""Report rendering helpers for email evaluation results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_REPORT_FORMATS = {"json", "markdown"}


def build_eval_report(
    evaluation: dict[str, Any],
    *,
    report_format: str = "markdown",
    title: str = "Email Triage Evaluation Report",
) -> str:
    report_format = _normalize_report_format(report_format)
    if report_format == "json":
        return json.dumps(_json_report(evaluation, title=title), ensure_ascii=False, indent=2)
    return _markdown_report(evaluation, title=title)


def write_eval_report(
    evaluation: dict[str, Any],
    *,
    output_path: str | Path,
    report_format: str = "markdown",
    title: str = "Email Triage Evaluation Report",
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_eval_report(evaluation, report_format=report_format, title=title),
        encoding="utf-8",
    )
    return path


def _json_report(evaluation: dict[str, Any], *, title: str) -> dict[str, Any]:
    rows = evaluation.get("rows", [])
    return {
        "title": title,
        "generated_at": datetime.now().astimezone().isoformat(),
        "classifier": evaluation.get("classifier", ""),
        "model": evaluation.get("model", ""),
        "provider": evaluation.get("provider", ""),
        "mailbox_mutation": bool(evaluation.get("mailbox_mutation", False)),
        "sample_count": evaluation.get("sample_count", 0),
        "labeled_count": evaluation.get("labeled_count", 0),
        "metrics": evaluation.get("metrics", {}),
        "error_count": len(evaluation.get("errors", [])),
        "mismatch_count": len(evaluation.get("mismatches", [])),
        "mismatches": _summarize_mismatches(evaluation.get("mismatches", [])),
        "errors": _summarize_errors(evaluation.get("errors", [])),
        "rows_omitted": len(rows),
    }


def _markdown_report(evaluation: dict[str, Any], *, title: str) -> str:
    metrics = evaluation.get("metrics", {})
    mismatches = _summarize_mismatches(evaluation.get("mismatches", []))
    errors = _summarize_errors(evaluation.get("errors", []))
    model = evaluation.get("model", "")

    lines = [
        f"# {title}",
        "",
        f"- Generated at: `{datetime.now().astimezone().isoformat()}`",
        f"- Classifier: `{evaluation.get('classifier', '')}`",
        f"- Provider: `{evaluation.get('provider', '')}`",
        f"- Mailbox mutation: `{bool(evaluation.get('mailbox_mutation', False))}`",
    ]
    if model:
        lines.append(f"- Model: `{model}`")
    lines.extend(
        [
            f"- Sample count: `{evaluation.get('sample_count', 0)}`",
            f"- Labeled count: `{evaluation.get('labeled_count', 0)}`",
            "",
            "## Metrics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ]
    )
    for key in sorted(metrics):
        lines.append(f"| `{key}` | `{metrics[key]}` |")

    lines.extend(
        [
            "",
            "## Error Summary",
            "",
            f"- Error count: `{len(errors)}`",
        ]
    )
    if errors:
        for error in errors:
            lines.append(f"- `{error['email_id']}`: {error['error']}")

    lines.extend(
        [
            "",
            "## Mismatch Summary",
            "",
            f"- Mismatch count: `{len(mismatches)}`",
        ]
    )
    if mismatches:
        lines.extend(["", "| Email | Category | Importance | Action | Reportable | Ignored |", "| --- | --- | --- | --- | --- | --- |"])
        for item in mismatches:
            lines.append(
                "| "
                f"`{item['email_id']}` "
                f"| `{item['category']}` "
                f"| `{item['importance']}` "
                f"| `{item['action']}` "
                f"| `{item['reportable']}` "
                f"| `{item['ignored']}` |"
            )

    lines.extend(
        [
            "",
            "## Interview Notes",
            "",
            "- Important recall tracks whether important mail was missed.",
            "- Important precision tracks whether reported mail was actually important.",
            "- Noise filtering precision tracks whether ignored mail was safe to ignore.",
            "- Category/action mismatches are reviewed separately from reportability errors.",
            "- LLM provider errors are tracked separately from semantic classification errors.",
            "",
        ]
    )
    return "\n".join(lines)


def _summarize_mismatches(mismatches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "email_id": row.get("email_id", ""),
            "subject": row.get("subject", ""),
            "category": _pair(row, "expected_category", "predicted_category"),
            "importance": _pair(row, "expected_importance", "predicted_importance"),
            "action": _pair(row, "expected_action", "predicted_action"),
            "reportable": _pair(row, "expected_reportable", "predicted_reportable"),
            "ignored": _pair(row, "expected_ignored", "predicted_ignored"),
            "classifier_error": row.get("classifier_error", ""),
        }
        for row in mismatches
    ]


def _summarize_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "email_id": str(row.get("email_id", "")),
            "subject": str(row.get("subject", "")),
            "error": str(row.get("classifier_error", "")),
        }
        for row in errors
    ]


def _pair(row: dict[str, Any], expected_key: str, predicted_key: str) -> str:
    return f"{row.get(expected_key, '')} -> {row.get(predicted_key, '')}"


def _normalize_report_format(report_format: str) -> str:
    normalized = report_format.strip().lower()
    if normalized not in SUPPORTED_REPORT_FORMATS:
        raise ValueError(f"unsupported report format: {report_format}")
    return normalized
