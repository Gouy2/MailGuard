"""LLM shadow scoring for archive suitability on proposal candidates."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import artifact_mapping, load_json_artifact, write_json_artifact
from .llm_email_classifier import _looks_like_response_format_error, _looks_like_retryable_error, _parse_json_object
from .real_proposal_eval import evaluate_real_proposal_labels
from .runtime_env import load_server_env


ARCHIVE_SHADOW_SCHEMA_VERSION = 1
VALID_ARCHIVE_SUITABILITY = {"yes", "no", "unsure"}
VALID_SHADOW_CONFIDENCE = {"high", "medium", "low"}
DEFAULT_ARCHIVE_SHADOW_MIN_DECISIVE_LABELS = 30
DEFAULT_ARCHIVE_SHADOW_TARGET_PRECISION = 0.95
DEFAULT_ARCHIVE_SHADOW_MAX_FALSE_POSITIVES = 0
DEFAULT_ARCHIVE_SHADOW_MAX_AVG_LATENCY_MS = 5000


class ArchiveSuitabilityScorer:
    """LLM scorer that runs only in shadow mode and never mutates proposals."""

    def __init__(
        self,
        *,
        model: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 1,
        client: Any | None = None,
    ) -> None:
        load_server_env()
        self.model = (model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        self.max_retries = max(0, max_retries)
        if client is not None:
            self.client = client
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is not installed; run server dependency sync first") from exc

        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def score(self, shadow_input: dict[str, Any]) -> dict[str, Any]:
        request = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an email archive suitability evaluator running in shadow mode. "
                        "Return only valid JSON. Do not call tools. Do not include markdown. "
                        "You cannot approve, reject, archive, or mutate email."
                    ),
                },
                {"role": "user", "content": _shadow_prompt(shadow_input)},
            ],
            "temperature": 0,
        }
        diagnostics = archive_shadow_input_diagnostics(shadow_input, request=request)
        request_started_at = time.perf_counter()
        response = self._create_completion_with_retries(request)
        request_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
        raw = response.choices[0].message.content or ""
        judgment = normalize_archive_shadow_judgment(_parse_json_object(raw), raw=raw)
        judgment["diagnostics"] = {
            **diagnostics,
            "request_elapsed_ms": request_elapsed_ms,
            "raw_response_chars": len(raw),
        }
        return judgment

    def _create_completion_with_retries(self, request: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._create_completion_once(request)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries or not _looks_like_retryable_error(exc):
                    raise
                time.sleep(min(0.5 * (2**attempt), 2.0))
        if last_error is not None:
            raise last_error
        raise RuntimeError("LLM completion failed before making a request")

    def _create_completion_once(self, request: dict[str, Any]) -> Any:
        try:
            return self.client.chat.completions.create(
                **request,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if not _looks_like_response_format_error(exc):
                raise
            return self.client.chat.completions.create(**request)


def build_archive_shadow_input(
    item: dict[str, Any],
    email: dict[str, Any],
    *,
    confirmed_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the LLM input without including email body content."""
    item_type = _item_type(item)
    policy_bucket = _policy_bucket(item_type, item)
    from_email = str(item.get("from_email") or email.get("from_email", "")).strip().lower()
    category = str(item.get("category", "")).strip().lower()
    memory_context = _confirmed_memory_context(
        from_email=from_email,
        category=category,
        confirmed_memory=confirmed_memory or {},
    )
    return {
        "schema_version": ARCHIVE_SHADOW_SCHEMA_VERSION,
        "task": "archive_suitability_shadow",
        "item": {
            "item_id": archive_shadow_item_id(item),
            "item_type": item_type,
            "email_id": str(item.get("email_id") or email.get("id", "")),
            "action": str(item.get("action", "archive") or "archive"),
            "risk_level": str(item.get("risk_level", "")),
            "source": str(item.get("source", "")),
            "policy_bucket": policy_bucket,
            "policy_reason": str(item.get("policy_reason") or item.get("reason", "")),
        },
        "email": {
            "id": str(email.get("id") or item.get("email_id", "")),
            "from_name": str(item.get("from_name") or email.get("from_name", "")),
            "from_email": from_email,
            "subject": str(item.get("subject") or email.get("subject", "")),
            "snippet": str(email.get("snippet", "")),
            "received_at": str(email.get("received_at", "")),
            "is_read": bool(email.get("is_read", False)),
            "has_attachments": bool(email.get("has_attachments", False)),
        },
        "rule_classification": {
            "category": category,
            "importance": str(item.get("importance", "")).strip().lower(),
            "suggested_action": str(item.get("suggested_action", "")).strip().lower(),
        },
        "confirmed_memory_context": memory_context,
        "safety_constraints": {
            "shadow_only": True,
            "proposal_mutation": False,
            "mailbox_mutation": False,
            "protected_cannot_be_overridden": True,
            "body_included": False,
        },
    }


def normalize_archive_shadow_judgment(data: dict[str, Any], *, raw: str = "") -> dict[str, Any]:
    suitability = _normalize_suitability(str(data.get("archive_suitability", "")))
    confidence = _normalize_confidence(str(data.get("confidence", "")))
    reason_codes = data.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        reason_codes = [reason_codes]
    normalized_codes = []
    for code in reason_codes:
        normalized = _normalize_code(str(code))
        if normalized and normalized not in normalized_codes:
            normalized_codes.append(normalized)
    brief_reason = " ".join(str(data.get("brief_reason", "")).split())[:240]
    if not brief_reason:
        brief_reason = "LLM did not provide a brief reason"
    if not normalized_codes:
        normalized_codes = ["unspecified"]
    return {
        "archive_suitability": suitability,
        "confidence": confidence,
        "reason_codes": normalized_codes[:8],
        "brief_reason": brief_reason,
        "raw_response": raw,
    }


def archive_shadow_input_diagnostics(
    shadow_input: dict[str, Any],
    *,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = json.dumps(shadow_input, ensure_ascii=False)
    prompt = _shadow_prompt(shadow_input)
    request_payload = json.dumps(request, ensure_ascii=False) if request is not None else ""
    email = dict(shadow_input.get("email") or {})
    return {
        "input_chars": len(payload),
        "prompt_chars": len(prompt),
        "request_chars": len(request_payload),
        "snippet_chars": len(str(email.get("snippet", ""))),
        "body_included": bool((shadow_input.get("safety_constraints") or {}).get("body_included", False)),
        "memory_match_count": len((shadow_input.get("confirmed_memory_context") or {}).get("matches") or []),
    }


def load_archive_shadow_results(path: str | Path) -> dict[str, Any]:
    data = load_json_artifact(path, default=_empty_shadow_store())
    results = artifact_mapping(data, "results")
    return {
        "schema_version": int(data.get("schema_version", ARCHIVE_SHADOW_SCHEMA_VERSION)),
        "results": results,
    }


def save_archive_shadow_result(path: str | Path, record: dict[str, Any]) -> dict[str, Any]:
    item_id = str(record.get("item_id", "")).strip()
    if not item_id:
        raise ValueError("shadow result item_id is required")
    data = load_archive_shadow_results(path)
    data["results"][item_id] = record
    write_json_artifact(path, data)
    return record


def archive_shadow_record(
    *,
    shadow_input: dict[str, Any],
    judgment: dict[str, Any],
    model: str,
    error: str = "",
    elapsed_ms: int = 0,
) -> dict[str, Any]:
    item = shadow_input.get("item", {})
    email = shadow_input.get("email", {})
    record = {
        "result_id": _shadow_result_id(str(item.get("item_id", "")), model),
        "item_id": str(item.get("item_id", "")),
        "item_type": str(item.get("item_type", "")),
        "email_id": str(item.get("email_id", "")),
        "subject": str(email.get("subject", "")),
        "from_email": str(email.get("from_email", "")),
        "policy_bucket": str(item.get("policy_bucket", "")),
        "model": model,
        "scored_at": datetime.now(UTC).isoformat(),
        "shadow_input": shadow_input,
        "judgment": _stored_judgment(judgment),
        "error": error,
        "elapsed_ms": max(0, int(elapsed_ms)),
        "diagnostics": dict(judgment.get("diagnostics") or {}),
        "mailbox_mutation": False,
        "proposal_mutation": False,
    }
    return record


def evaluate_archive_shadow_results(
    *,
    label_data: dict[str, Any],
    shadow_data: dict[str, Any],
    min_decisive_labels: int = DEFAULT_ARCHIVE_SHADOW_MIN_DECISIVE_LABELS,
    target_precision: float = DEFAULT_ARCHIVE_SHADOW_TARGET_PRECISION,
    max_false_positives: int = DEFAULT_ARCHIVE_SHADOW_MAX_FALSE_POSITIVES,
    max_avg_latency_ms: int = DEFAULT_ARCHIVE_SHADOW_MAX_AVG_LATENCY_MS,
) -> dict[str, Any]:
    label_eval = evaluate_real_proposal_labels(label_data)
    labels = label_eval.get("rows", [])
    results = shadow_data.get("results", {})
    rows = []
    for label_row in labels:
        item_id = str(label_row.get("item_id", ""))
        shadow = results.get(item_id)
        if not isinstance(shadow, dict):
            continue
        judgment = dict(shadow.get("judgment") or {})
        prediction = str(judgment.get("archive_suitability", ""))
        diagnostics = dict(shadow.get("diagnostics") or {})
        rows.append(
            {
                "item_id": item_id,
                "item_type": str(label_row.get("item_type", "")),
                "email_id": str(label_row.get("email_id", "")),
                "label": str(label_row.get("label", "")),
                "prediction": prediction,
                "confidence": str(judgment.get("confidence", "")),
                "reason_codes": list(judgment.get("reason_codes") or []),
                "brief_reason": str(judgment.get("brief_reason", "")),
                "subject": str(label_row.get("subject", "")),
                "from_email": str(label_row.get("from_email", "")),
                "policy_bucket": str(shadow.get("policy_bucket", "")),
                "error": str(shadow.get("error", "")),
                "elapsed_ms": _safe_int(shadow.get("elapsed_ms", 0)),
                "request_elapsed_ms": _safe_int(diagnostics.get("request_elapsed_ms", 0)),
            }
        )

    decisive = [row for row in rows if row["label"] in {"archive", "keep"} and not row["error"]]
    predicted_yes = [row for row in decisive if row["prediction"] == "yes"]
    accepted_yes = [row for row in predicted_yes if row["label"] == "archive"]
    false_positive = [row for row in predicted_yes if row["label"] == "keep"]
    archive_labels = [row for row in decisive if row["label"] == "archive"]
    missed_archive = [row for row in archive_labels if row["prediction"] == "no"]
    unsure_predictions = [row for row in decisive if row["prediction"] == "unsure"]
    errors = [row for row in rows if row["error"]]
    latency_values = [row["elapsed_ms"] for row in rows if row["elapsed_ms"] > 0 and not row["error"]]
    avg_latency_ms = _average_int(latency_values)
    metrics = {
        "archive_yes_precision": _ratio(len(accepted_yes), len(predicted_yes)),
        "archive_yes_recall": _ratio(len(accepted_yes), len(archive_labels)),
        "false_positive_count": len(false_positive),
        "missed_archive_count": len(missed_archive),
        "unsure_prediction_count": len(unsure_predictions),
        "error_count": len(errors),
        "avg_latency_ms": avg_latency_ms,
        "latency_sample_count": len(latency_values),
    }

    return {
        "schema_version": int(shadow_data.get("schema_version", ARCHIVE_SHADOW_SCHEMA_VERSION)),
        "label_sample_count": label_eval.get("sample_count", 0),
        "matched_count": len(rows),
        "decisive_count": len(decisive),
        "prediction_counts": _counts(row["prediction"] or "error" for row in rows),
        "metrics": metrics,
        "readiness": _archive_shadow_readiness(
            decisive_count=len(decisive),
            predicted_yes_count=len(predicted_yes),
            metrics=metrics,
            min_decisive_labels=min_decisive_labels,
            target_precision=target_precision,
            max_false_positives=max_false_positives,
            max_avg_latency_ms=max_avg_latency_ms,
        ),
        "false_positive_shadow": false_positive,
        "missed_archive_shadow": missed_archive,
        "unsure_shadow": unsure_predictions,
        "errors": errors,
        "rows": rows,
        "mailbox_mutation": False,
        "proposal_mutation": False,
    }


def archive_shadow_item_id(item: dict[str, Any]) -> str:
    return (
        str(item.get("proposal_id", "") or "").strip()
        or str(item.get("candidate_id", "") or "").strip()
        or str(item.get("item_id", "") or "").strip()
        or f"protected-{str(item.get('email_id', '')).strip()}-archive"
    )


def _shadow_prompt(shadow_input: dict[str, Any]) -> str:
    return (
        "Evaluate whether this email is suitable for an archive proposal in a personal email management agent.\n"
        "This is shadow evaluation only: your output must not approve, execute, or mutate anything.\n"
        "Use only the provided metadata and snippet. The email body is intentionally not included.\n"
        "Return exactly this JSON shape:\n"
        "{"
        "\"archive_suitability\":\"yes|no|unsure\","
        "\"confidence\":\"high|medium|low\","
        "\"reason_codes\":[\"short_snake_case_code\"],"
        "\"brief_reason\":\"one concise sentence\""
        "}\n"
        "Guidance:\n"
        "- yes: likely safe and useful to propose archive to the user.\n"
        "- no: likely should be kept, reported, reviewed, or protected.\n"
        "- unsure: metadata/snippet is insufficient or policy context is mixed.\n"
        "- Protected mail remains protected even if you say yes; this result is only for evaluation.\n"
        "- Finance, security, meetings, personal requests, and action-required mail should usually be no or unsure.\n"
        "- Promotions, newsletters, social noise, and low-value automated updates can be yes.\n"
        f"Input:\n{json.dumps(shadow_input, ensure_ascii=False)}"
    )


def _item_type(item: dict[str, Any]) -> str:
    explicit = str(item.get("item_type", "")).strip().lower()
    if explicit:
        return explicit
    if item.get("proposal_id"):
        return "proposal"
    if item.get("candidate_id"):
        return "candidate"
    return "protected" if str(item.get("policy_decision", "")).lower() == "protected" else "item"


def _policy_bucket(item_type: str, item: dict[str, Any]) -> str:
    if item_type in {"proposal", "candidate", "protected"}:
        return item_type
    decision = str(item.get("policy_decision", "")).strip().lower()
    return decision or item_type or "item"


def _confirmed_memory_context(
    *,
    from_email: str,
    category: str,
    confirmed_memory: dict[str, Any],
) -> dict[str, Any]:
    domain = from_email.rsplit("@", 1)[1].strip().lower() if "@" in from_email else ""
    archive_senders = _normalized_values(confirmed_memory.get("archive_senders", []))
    archive_domains = _normalized_values(confirmed_memory.get("archive_domains", []))
    archive_categories = _normalized_values(confirmed_memory.get("archive_categories", []))
    domain_match = next((item for item in archive_domains if domain == item or domain.endswith(f".{item}")), "")
    matches = []
    if from_email and from_email in archive_senders:
        matches.append(f"archive_sender:{from_email}")
    if domain_match:
        matches.append(f"archive_domain:{domain_match}")
    if category and category in archive_categories:
        matches.append(f"archive_category:{category}")
    return {
        "matches": matches,
        "archive_sender_match": from_email in archive_senders,
        "archive_domain_match": bool(domain_match),
        "archive_category_match": category in archive_categories,
        "archive_category_policy_active": False,
    }


def _normalize_suitability(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "archive": "yes",
        "safe": "yes",
        "suitable": "yes",
        "true": "yes",
        "keep": "no",
        "unsafe": "no",
        "false": "no",
        "maybe": "unsure",
        "unclear": "unsure",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_ARCHIVE_SUITABILITY:
        raise ValueError(f"invalid archive_suitability: {value}")
    return normalized


def _normalize_confidence(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in VALID_SHADOW_CONFIDENCE:
        return "low"
    return normalized


def _normalize_code(value: str) -> str:
    normalized = "_".join(value.strip().lower().replace("-", "_").split())
    return "".join(ch for ch in normalized if ch.isalnum() or ch == "_")[:40]


def _normalized_values(value: Any) -> set[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    return {str(item).strip().lower() for item in items if str(item).strip()}


def _stored_judgment(judgment: dict[str, Any]) -> dict[str, Any]:
    return {
        "archive_suitability": str(judgment.get("archive_suitability", "")),
        "confidence": str(judgment.get("confidence", "")),
        "reason_codes": list(judgment.get("reason_codes") or []),
        "brief_reason": str(judgment.get("brief_reason", "")),
    }


def _shadow_result_id(item_id: str, model: str) -> str:
    digest = hashlib.sha1(f"{item_id}:{model}".encode("utf-8")).hexdigest()[:10]
    return f"shadow-{digest}"


def _empty_shadow_store() -> dict[str, Any]:
    return {
        "schema_version": ARCHIVE_SHADOW_SCHEMA_VERSION,
        "results": {},
    }


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _archive_shadow_readiness(
    *,
    decisive_count: int,
    predicted_yes_count: int,
    metrics: dict[str, Any],
    min_decisive_labels: int,
    target_precision: float,
    max_false_positives: int,
    max_avg_latency_ms: int,
) -> dict[str, Any]:
    min_decisive_labels = max(1, int(min_decisive_labels))
    target_precision = max(0.0, min(1.0, float(target_precision)))
    max_false_positives = max(0, int(max_false_positives))
    max_avg_latency_ms = max(0, int(max_avg_latency_ms))
    archive_yes_precision = float(metrics.get("archive_yes_precision", 0.0))
    false_positive_count = int(metrics.get("false_positive_count", 0))
    error_count = int(metrics.get("error_count", 0))
    latency_sample_count = int(metrics.get("latency_sample_count", 0))
    avg_latency_ms = int(metrics.get("avg_latency_ms", 0))

    decisive_labels_ready = decisive_count >= min_decisive_labels
    prediction_ready = predicted_yes_count > 0
    precision_ready = prediction_ready and archive_yes_precision >= target_precision
    false_positive_ready = false_positive_count <= max_false_positives
    error_ready = error_count == 0
    latency_ready = latency_sample_count > 0 and avg_latency_ms <= max_avg_latency_ms
    ready_for_policy_experiment = all(
        [
            decisive_labels_ready,
            precision_ready,
            false_positive_ready,
            error_ready,
            latency_ready,
        ]
    )
    recommendation, notes = _archive_shadow_recommendation(
        decisive_labels_ready=decisive_labels_ready,
        prediction_ready=prediction_ready,
        precision_ready=precision_ready,
        false_positive_ready=false_positive_ready,
        error_ready=error_ready,
        latency_ready=latency_ready,
        ready_for_policy_experiment=ready_for_policy_experiment,
        decisive_count=decisive_count,
        min_decisive_labels=min_decisive_labels,
        archive_yes_precision=archive_yes_precision,
        target_precision=target_precision,
        false_positive_count=false_positive_count,
        max_false_positives=max_false_positives,
        error_count=error_count,
        latency_sample_count=latency_sample_count,
        avg_latency_ms=avg_latency_ms,
        max_avg_latency_ms=max_avg_latency_ms,
    )
    return {
        "ready_for_policy_experiment": ready_for_policy_experiment,
        "recommendation": recommendation,
        "decisive_labels_ready": decisive_labels_ready,
        "prediction_ready": prediction_ready,
        "precision_ready": precision_ready,
        "false_positive_ready": false_positive_ready,
        "error_ready": error_ready,
        "latency_ready": latency_ready,
        "thresholds": {
            "min_decisive_labels": min_decisive_labels,
            "target_archive_yes_precision": target_precision,
            "max_false_positive_count": max_false_positives,
            "max_avg_latency_ms": max_avg_latency_ms,
        },
        "notes": notes,
    }


def _archive_shadow_recommendation(
    *,
    decisive_labels_ready: bool,
    prediction_ready: bool,
    precision_ready: bool,
    false_positive_ready: bool,
    error_ready: bool,
    latency_ready: bool,
    ready_for_policy_experiment: bool,
    decisive_count: int,
    min_decisive_labels: int,
    archive_yes_precision: float,
    target_precision: float,
    false_positive_count: int,
    max_false_positives: int,
    error_count: int,
    latency_sample_count: int,
    avg_latency_ms: int,
    max_avg_latency_ms: int,
) -> tuple[str, list[str]]:
    if ready_for_policy_experiment:
        return (
            "eligible_for_guarded_policy_experiment",
            ["Shadow quality is good enough to discuss a guarded policy experiment, not automatic execution."],
        )
    if not decisive_labels_ready:
        return (
            "collect_more_labels",
            [f"Need at least {min_decisive_labels} decisive labels before using shadow results for policy decisions."],
        )
    if not prediction_ready:
        return (
            "inspect_overly_conservative_shadow",
            ["No archive=yes predictions were produced, so precision is not meaningful yet."],
        )
    if not error_ready:
        return (
            "fix_shadow_errors",
            [f"Shadow scoring has {error_count} error rows; fix or re-run them before comparing policy quality."],
        )
    if not false_positive_ready:
        return (
            "inspect_false_positives",
            [
                (
                    f"False positives are {false_positive_count}, above the allowed "
                    f"{max_false_positives}; inspect these before tuning recall."
                )
            ],
        )
    if not precision_ready:
        return (
            "tune_prompt_or_memory_context",
            [f"archive_yes_precision is {archive_yes_precision}, below target {target_precision}."],
        )
    if not latency_ready:
        if latency_sample_count == 0:
            note = "No latency samples were observed; re-run shadow scoring so performance can be evaluated."
        else:
            note = f"Average latency is {avg_latency_ms}ms, above the target {max_avg_latency_ms}ms."
        return ("rerun_or_optimize_shadow_latency", [note])
    return ("review_shadow_readiness", ["Shadow readiness gates are mixed; inspect the metric rows."])


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _average_int(values: list[int]) -> int:
    if not values:
        return 0
    return int(sum(values) / len(values))
