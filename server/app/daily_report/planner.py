"""Planners for the daily read-only report action loop."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol

from ..runtime_env import load_server_env
from .models import Action, Run, VALID_ACTIONS


class Planner(Protocol):
    label: str

    def next_action(self, run: Run) -> Action:
        ...


class MockPlanner:
    label = "mock"

    def next_action(self, run: Run) -> Action:
        if not run.steps:
            return Action("list_recent", {"limit": run.budget.limit, "unread_only": False})
        return Action("finish", _mock_finish_args(run))


class OpenAIPlanner:
    label = "openai"

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
        self.max_retries = max(0, int(max_retries))
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

    def next_action(self, run: Run) -> Action:
        response = self._create_completion_with_retries(_request(run, self.model))
        raw = response.choices[0].message.content or ""
        data = _parse_json_object(raw)
        action = Action.from_raw(data)
        if action.name not in VALID_ACTIONS:
            return action
        return action

    def _create_completion_with_retries(self, request: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._create_completion_once(request)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
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
            if "response_format" not in str(exc):
                raise
            return self.client.chat.completions.create(**request)


def build_planner(
    llm: str,
    *,
    model: str = "",
    timeout: float = 30.0,
    max_retries: int = 1,
) -> Planner:
    key = llm.strip().lower() or "mock"
    if key == "mock":
        return MockPlanner()
    if key == "openai":
        return OpenAIPlanner(model=model or None, timeout=timeout, max_retries=max_retries)
    raise ValueError(f"unsupported daily report llm: {llm}")


def _request(run: Run, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are MailGuard's read-only daily email report planner. "
                    "Return one JSON object only. Do not include markdown, chain-of-thought, or hidden reasoning. "
                    "You may choose only these actions: list_recent, search, get_detail, memory, finish. "
                    "Never propose or execute mailbox mutations. "
                    "Use finish when you have enough evidence to produce a concise daily report."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(_context(run), ensure_ascii=False),
            },
        ],
        "temperature": 0,
    }


def _context(run: Run) -> dict[str, Any]:
    return {
        "task": "Generate a read-only daily email report for important mail.",
        "required_output": {
            "action": "one of list_recent/search/get_detail/memory/finish",
            "args": "object; for finish include report and items",
        },
        "finish_item_shape": {
            "email_id": "message id",
            "subject": "message subject",
            "from_email": "sender address",
            "from_name": "sender name",
            "received_at": "timestamp",
            "reason": "why this email matters",
            "priority": "high|normal",
        },
        "budget": run.budget.to_dict(),
        "provider": run.provider,
        "steps": [step.to_dict() for step in run.steps[-6:]],
    }


def _mock_finish_args(run: Run) -> dict[str, Any]:
    emails = _last_emails(run)
    items = [_mock_item(email) for email in emails if _looks_important(email)]
    report = _mock_report(items, len(emails))
    return {"report": report, "items": items}


def _last_emails(run: Run) -> list[dict[str, Any]]:
    for step in reversed(run.steps):
        emails = step.observation.get("emails")
        if isinstance(emails, list):
            return [email for email in emails if isinstance(email, dict)]
    return []


def _looks_important(email: dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(email.get("subject", "")),
            str(email.get("snippet", "")),
            str(email.get("from_email", "")),
        ]
    ).lower()
    keywords = (
        "action",
        "required",
        "invoice",
        "payment",
        "security",
        "password",
        "interview",
        "meeting",
        "due",
        "review",
    )
    return any(keyword in text for keyword in keywords)


def _mock_item(email: dict[str, Any]) -> dict[str, Any]:
    return {
        "email_id": str(email.get("id", "")),
        "subject": str(email.get("subject", "")),
        "from_email": str(email.get("from_email", "")),
        "from_name": str(email.get("from_name", "")),
        "received_at": str(email.get("received_at", "")),
        "reason": "Matched important daily-report keywords.",
        "priority": "normal",
    }


def _mock_report(items: list[dict[str, Any]], total: int) -> str:
    if not items:
        return f"Checked {total} recent emails. No key emails were selected by the mock planner."
    subjects = "; ".join(str(item.get("subject", "")) for item in items[:5])
    return f"Checked {total} recent emails. Key emails: {subjects}"


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
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON root must be an object")
    return data
