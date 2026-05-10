"""LLM-backed email classifier for shadow evaluation on mock data."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .email_provider import EmailMessage
from .runtime_env import load_server_env


VALID_CATEGORIES = {
    "action_required",
    "security",
    "finance",
    "meeting",
    "important",
    "newsletter",
    "promotion",
    "notification",
    "noise",
}
VALID_IMPORTANCE = {"high", "medium", "low"}
VALID_ACTIONS = {"reply", "review", "schedule", "pay_attention", "archive", "ignore", "draft_reply"}


class LLMEmailClassifier:
    def __init__(self, *, model: str | None = None, timeout: float = 30.0, max_retries: int = 1) -> None:
        load_server_env()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("openai package is not installed; run server dependency sync first") from exc

        self.model = (model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
        self.max_retries = max(0, max_retries)
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def classify(self, email: EmailMessage, preferences: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt = _classification_prompt(email, preferences or {})
        request = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an email triage classifier. Return only valid JSON. "
                        "Do not call tools. Do not include markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        response = self._create_completion_with_retries(request)
        raw = response.choices[0].message.content or ""
        data = _parse_json_object(raw)
        return _normalize_decision(email.id, data, raw)

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


def _classification_prompt(email: EmailMessage, preferences: dict[str, Any]) -> str:
    payload = {
        "email": {
            "id": email.id,
            "from_name": email.from_name,
            "from_email": email.from_email,
            "subject": email.subject,
            "snippet": email.snippet,
            "body": email.body[:2000],
            "received_at": email.received_at,
            "labels": email.labels,
            "is_read": email.is_read,
            "has_attachments": email.has_attachments,
        },
        "preferences": preferences,
        "allowed_output": {
            "category": sorted(VALID_CATEGORIES),
            "importance": sorted(VALID_IMPORTANCE),
            "suggested_action": sorted(VALID_ACTIONS),
        },
    }
    return (
        "Classify this email for a personal email triage agent.\n"
        "Choose exactly one category using this priority order when multiple labels could apply:\n"
        "1. security: account access, password reset, suspicious sign-in, suspicious transaction, fraud risk.\n"
        "2. finance: invoice, receipt, billing, payment, payout, renewal, subscription, tax, vendor finance. "
        "Finance keeps category=finance even when the user must review or confirm something.\n"
        "3. promotion: sales, discounts, coupons, deal alerts, marketing surveys, limited-time offers. "
        "Promotion stays promotion even when it says notification, deadline, or please review.\n"
        "4. noise: social notifications, profile views, reactions, friend suggestions, social digests.\n"
        "5. newsletter: subscribed newsletters, weekly roundups, product/course/content updates.\n"
        "6. meeting: calendar invites, meeting reminders, meeting changes, cancellations, scheduling updates.\n"
        "7. action_required: a person or business system asks the user to reply, confirm, review, send, or schedule, "
        "and none of the higher-priority categories apply.\n"
        "8. notification: operational, CI, monitoring, system, incident, or status notifications that are not ads, "
        "newsletters, social noise, finance, security, or meetings.\n"
        "Use importance as follows:\n"
        "- high: security risk, finance receipts/invoices/payments/renewals, finance with deadline/failure/service risk, direct user request with a deadline, "
        "recruiting or interview scheduling, or major account/business impact.\n"
        "- medium: reportable but not urgent, including normal meeting updates/reminders and operational notifications.\n"
        "- low: safe to ignore, including newsletters, promotions, social noise, and low-value automated updates.\n"
        "Use suggested_action as follows:\n"
        "- reply: the user should answer a person directly.\n"
        "- review: the user should inspect a document, invoice, alert, receipt, or system/business item.\n"
        "- schedule: the user should handle a meeting/calendar/interview time.\n"
        "- pay_attention: urgent security or suspicious-account situations only.\n"
        "- archive or ignore: low-value mail that should not be reported.\n"
        "- draft_reply: only when an actual reply draft is needed.\n"
        "Calibration rules:\n"
        "- Do not mark an ordinary meeting reminder high just because it starts soon; use meeting/medium/schedule.\n"
        "- Receipts, invoices, billing, renewals, and subscription payments are finance/high/review by default when unread. "
        "Do not archive finance receipts in triage; the user may need to inspect charges or renewals.\n"
        "- Operational incident, CI, monitoring, and status notifications should usually be notification/medium/review. "
        "Only use high for operational notifications when the email says the user is personally on call, must acknowledge now, "
        "or describes active customer/business impact that needs immediate action.\n"
        "- Social notifications are noise/low/ignore even if they mention recruiters, reactions, or notifications.\n"
        "- Newsletters remain newsletter/low/ignore even when the topic seems relevant.\n"
        "- Promotions remain promotion/low/ignore even when they include deadline or action wording.\n"
        "Important/reportable emails include action_required, security, finance, meetings, recruiting, "
        "and operational notifications with high or medium importance.\n"
        "Ignored emails include newsletters, promotions, social digests, and low-value automated updates.\n"
        "Return exactly this JSON shape:\n"
        "{"
        "\"category\":\"...\","
        "\"importance\":\"high|medium|low\","
        "\"suggested_action\":\"...\","
        "\"reasons\":[\"short reason\", \"short reason\"]"
        "}\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError(f"LLM response is not valid JSON: {exc}: {raw[:500]}") from exc
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as fallback_exc:
            raise ValueError(f"LLM response is not valid JSON: {fallback_exc}: {raw[:500]}") from fallback_exc
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON must be an object")
    return data


def _looks_like_response_format_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "response_format" in message or "json_object" in message


def _looks_like_retryable_error(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "rate limit",
        "temporarily unavailable",
        "connection",
        "server error",
        "service unavailable",
    )
    return any(marker in name or marker in message for marker in retryable_markers)


def _normalize_decision(email_id: str, data: dict[str, Any], raw: str) -> dict[str, Any]:
    category = str(data.get("category", "")).strip().lower()
    importance = str(data.get("importance", "")).strip().lower()
    suggested_action = str(data.get("suggested_action", "")).strip().lower()
    reasons = data.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]

    if category not in VALID_CATEGORIES:
        raise ValueError(f"invalid LLM category: {category}; raw={raw[:500]}")
    if importance not in VALID_IMPORTANCE:
        raise ValueError(f"invalid LLM importance: {importance}; raw={raw[:500]}")
    if suggested_action not in VALID_ACTIONS:
        raise ValueError(f"invalid LLM suggested_action: {suggested_action}; raw={raw[:500]}")
    if not reasons:
        reasons = ["LLM did not provide a reason"]

    is_ignored = category in {"newsletter", "promotion", "noise"} or importance == "low"
    is_reportable = category not in {"newsletter", "promotion", "noise"} and importance in {"high", "medium"}
    return {
        "email_id": email_id,
        "category": category,
        "importance": importance,
        "suggested_action": suggested_action,
        "reasons": reasons,
        "signals": {
            "positive": [],
            "negative": [],
            "preferences": {"important": [], "ignored": []},
            "llm": True,
        },
        "is_reportable": is_reportable,
        "is_ignored": is_ignored,
        "raw_response": raw,
    }
