"""Observed memory insights derived from local proposal labels."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .real_proposal_eval import evaluate_real_proposal_labels


DEFAULT_MIN_SAMPLES = 1


def build_observed_memory_report(
    label_data: dict[str, Any],
    *,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict[str, Any]:
    """Build read-only memory signals from proposal/candidate labels."""
    min_samples = max(1, int(min_samples))
    evaluation = evaluate_real_proposal_labels(label_data)
    rows = evaluation.get("rows", [])
    decisive_rows = [row for row in rows if row.get("label") in {"archive", "keep"}]

    groups = {
        "sender": _summarize_groups(decisive_rows, _sender_key),
        "domain": _summarize_groups(decisive_rows, _domain_key),
        "category": _summarize_groups(decisive_rows, _category_key),
        "item_type": _summarize_groups(decisive_rows, _item_type_key),
    }
    insights = _build_insights(groups, min_samples=min_samples)

    return {
        "schema_version": int(label_data.get("schema_version", 1)),
        "sample_count": evaluation.get("sample_count", 0),
        "decisive_count": evaluation.get("decisive_count", 0),
        "label_counts": evaluation.get("label_counts", {}),
        "min_samples": min_samples,
        "groups": groups,
        "insights": insights,
        "proposed_preferences": _proposed_preferences(insights),
        "mailbox_mutation": False,
    }


def _summarize_groups(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if key:
            buckets[key].append(row)

    summaries = [_summarize_bucket(key, bucket) for key, bucket in buckets.items()]
    return sorted(
        summaries,
        key=lambda item: (
            -item["sample_count"],
            -item["archive_rate"],
            item["key"],
        ),
    )


def _summarize_bucket(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(str(row.get("label", "")) for row in rows)
    item_types = Counter(str(row.get("item_type", "")) for row in rows if row.get("item_type"))
    examples = []
    for row in rows[:3]:
        examples.append(
            {
                "item_id": str(row.get("item_id", "")),
                "email_id": str(row.get("email_id", "")),
                "item_type": str(row.get("item_type", "")),
                "label": str(row.get("label", "")),
                "subject": str(row.get("subject", "")),
            }
        )
    sample_count = len(rows)
    archive_count = labels.get("archive", 0)
    keep_count = labels.get("keep", 0)
    return {
        "key": key,
        "sample_count": sample_count,
        "archive_count": archive_count,
        "keep_count": keep_count,
        "archive_rate": _ratio(archive_count, sample_count),
        "keep_rate": _ratio(keep_count, sample_count),
        "label_counts": dict(sorted(labels.items())),
        "item_type_counts": dict(sorted(item_types.items())),
        "examples": examples,
    }


def _build_insights(groups: dict[str, list[dict[str, Any]]], *, min_samples: int) -> list[dict[str, Any]]:
    insights = []
    for group_type, items in groups.items():
        for item in items:
            if item["sample_count"] < min_samples:
                continue
            if item["archive_count"] and not item["keep_count"]:
                insights.append(_insight("archive_friendly", group_type, item))
            elif item["keep_count"] and not item["archive_count"]:
                insights.append(_insight("keep_protected", group_type, item))
            elif item["archive_rate"] >= 0.8:
                insights.append(_insight("mostly_archive", group_type, item))
            elif item["keep_rate"] >= 0.8:
                insights.append(_insight("mostly_keep", group_type, item))
    return sorted(
        insights,
        key=lambda item: (
            item["actionability_rank"],
            -item["sample_count"],
            -item["archive_rate"],
            item["group_type"],
            item["key"],
        ),
    )


def _insight(kind: str, group_type: str, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "group_type": group_type,
        "key": item["key"],
        "sample_count": item["sample_count"],
        "archive_count": item["archive_count"],
        "keep_count": item["keep_count"],
        "archive_rate": item["archive_rate"],
        "keep_rate": item["keep_rate"],
        "confidence": _confidence(item["sample_count"], item["archive_rate"], item["keep_rate"]),
        "actionability_rank": _actionability_rank(kind, group_type),
        "item_type_counts": item["item_type_counts"],
        "examples": item["examples"],
    }


def _proposed_preferences(insights: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferences = []
    for insight in insights:
        if insight["kind"] not in {"archive_friendly", "mostly_archive"}:
            continue
        if insight["group_type"] not in {"sender", "domain", "category"}:
            continue
        preferences.append(
            {
                "proposal": f"archive_{insight['group_type']}",
                "value": insight["key"],
                "confidence": insight["confidence"],
                "sample_count": insight["sample_count"],
                "archive_rate": insight["archive_rate"],
                "status": "observed_only",
            }
        )
    return preferences


def _confidence(sample_count: int, archive_rate: float, keep_rate: float) -> str:
    dominant_rate = max(archive_rate, keep_rate)
    if sample_count >= 5 and dominant_rate >= 0.9:
        return "high"
    if sample_count >= 2 and dominant_rate >= 0.8:
        return "medium"
    return "low"


def _actionability_rank(kind: str, group_type: str) -> int:
    kind_rank = {
        "archive_friendly": 0,
        "mostly_archive": 1,
        "keep_protected": 2,
        "mostly_keep": 3,
    }.get(kind, 9)
    group_rank = {
        "sender": 0,
        "domain": 1,
        "category": 2,
        "item_type": 3,
    }.get(group_type, 9)
    return kind_rank * 10 + group_rank


def _sender_key(row: dict[str, Any]) -> str:
    return str(row.get("from_email", "")).strip().lower()


def _domain_key(row: dict[str, Any]) -> str:
    sender = _sender_key(row)
    if "@" not in sender:
        return ""
    return sender.rsplit("@", 1)[1].strip().lower()


def _category_key(row: dict[str, Any]) -> str:
    return str(row.get("category", "")).strip().lower()


def _item_type_key(row: dict[str, Any]) -> str:
    return str(row.get("item_type", "")).strip().lower()


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
