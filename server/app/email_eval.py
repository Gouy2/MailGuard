"""Deterministic evaluation for mock email triage."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Callable

from .email_provider import EmailMessage, EmailProvider


Classifier = Callable[[EmailMessage, dict[str, Any] | None], dict[str, Any]]


def evaluate_email_classifier(
    *,
    provider: EmailProvider,
    classifier: Classifier,
    preferences: dict[str, Any] | None = None,
    limit: int = 100,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    emails = provider.list_recent(limit=limit, unread_only=False)
    rows = []
    category_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    importance_confusion: dict[str, Counter[str]] = defaultdict(Counter)

    for email in emails:
        classifier_error = ""
        try:
            decision = classifier(email, preferences or {})
        except Exception as exc:
            if not continue_on_error:
                raise
            classifier_error = f"{type(exc).__name__}: {exc}"
            decision = {
                "category": "",
                "importance": "",
                "is_reportable": False,
                "is_ignored": False,
                "reasons": [classifier_error],
            }
        expected_category = email.expected_category or ""
        expected_importance = email.expected_importance or ""
        expected_action = email.expected_action or ""
        predicted_category = str(decision["category"])
        predicted_importance = str(decision["importance"])
        predicted_action = str(decision.get("suggested_action", ""))
        expected_reportable = (
            email.expected_reportable
            if email.expected_reportable is not None
            else _expected_reportable(expected_category, expected_importance)
        )
        predicted_reportable = bool(decision["is_reportable"])
        expected_ignored = (
            email.expected_ignored
            if email.expected_ignored is not None
            else _expected_ignored(expected_category, expected_importance)
        )
        predicted_ignored = bool(decision["is_ignored"])

        category_confusion[expected_category][predicted_category] += 1
        importance_confusion[expected_importance][predicted_importance] += 1
        rows.append(
            {
                "email_id": email.id,
                "subject": email.subject,
                "expected_category": expected_category,
                "predicted_category": predicted_category,
                "category_correct": expected_category == predicted_category,
                "expected_importance": expected_importance,
                "predicted_importance": predicted_importance,
                "importance_correct": expected_importance == predicted_importance,
                "expected_action": expected_action,
                "predicted_action": predicted_action,
                "action_correct": not expected_action or expected_action == predicted_action,
                "expected_reportable": expected_reportable,
                "predicted_reportable": predicted_reportable,
                "expected_ignored": expected_ignored,
                "predicted_ignored": predicted_ignored,
                "difficulty": email.difficulty,
                "notes": email.notes,
                "reasons": decision["reasons"],
                "classifier_error": classifier_error,
            }
        )

    labeled = [row for row in rows if row["expected_category"] and row["expected_importance"]]
    category_correct = sum(1 for row in labeled if row["category_correct"])
    importance_correct = sum(1 for row in labeled if row["importance_correct"])
    action_labeled = [row for row in labeled if row["expected_action"]]
    action_correct = sum(1 for row in action_labeled if row["action_correct"])
    reportable_tp = sum(1 for row in labeled if row["expected_reportable"] and row["predicted_reportable"])
    reportable_fn = sum(1 for row in labeled if row["expected_reportable"] and not row["predicted_reportable"])
    reportable_fp = sum(1 for row in labeled if not row["expected_reportable"] and row["predicted_reportable"])
    ignored_tp = sum(1 for row in labeled if row["expected_ignored"] and row["predicted_ignored"])
    ignored_fp = sum(1 for row in labeled if not row["expected_ignored"] and row["predicted_ignored"])

    return {
        "sample_count": len(emails),
        "labeled_count": len(labeled),
        "metrics": {
            "category_accuracy": _ratio(category_correct, len(labeled)),
            "importance_accuracy": _ratio(importance_correct, len(labeled)),
            "action_accuracy": _ratio(action_correct, len(action_labeled)),
            "important_recall": _ratio(reportable_tp, reportable_tp + reportable_fn),
            "important_precision": _ratio(reportable_tp, reportable_tp + reportable_fp),
            "noise_filter_precision": _ratio(ignored_tp, ignored_tp + ignored_fp),
            "false_negative_count": reportable_fn,
            "false_positive_count": reportable_fp,
        },
        "confusion": {
            "category": _counter_matrix(category_confusion),
            "importance": _counter_matrix(importance_confusion),
        },
        "mismatches": [
            row
            for row in labeled
            if not row["category_correct"]
            or not row["importance_correct"]
            or not row["action_correct"]
            or row["expected_reportable"] != row["predicted_reportable"]
            or row["expected_ignored"] != row["predicted_ignored"]
        ],
        "errors": [row for row in rows if row["classifier_error"]],
        "rows": rows,
    }


def _expected_reportable(category: str, importance: str) -> bool:
    return category not in {"newsletter", "promotion", "noise"} and importance in {"high", "medium"}


def _expected_ignored(category: str, importance: str) -> bool:
    return category in {"newsletter", "promotion", "noise"} or importance == "low"


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _counter_matrix(matrix: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    return {
        expected: dict(sorted(predicted.items()))
        for expected, predicted in sorted(matrix.items())
    }
