"""Teach workflow for turning user cleaning preferences into proposed rules."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..archive.models import EmailClassification
from ..archive.policy import ArchiveProposalPolicy
from ..email_classifier import classify_email
from ..email_provider import EmailMessage, EmailProvider
from ..llm_email_classifier import (
    _looks_like_response_format_error,
    _parse_json_object,
)
from ..memory import MemoryStore
from ..provider_factory import create_email_provider
from ..runtime_env import load_server_env
from .rules import CleanRule, matching_rules, proposed_rule


DEFAULT_TEACH_LIMIT = 50
DEFAULT_TEACH_HOURS = 24 * 30
TeachLLMMode = str


class TeachParser(Protocol):
    def parse(self, instruction: str) -> list[dict[str, Any]]:
        ...


@dataclass(slots=True)
class HeuristicTeachParser:
    source: str = "heuristic_teach"

    def parse(self, instruction: str) -> list[dict[str, Any]]:
        text = instruction.strip()
        lower = text.lower()
        rules: list[dict[str, Any]] = []

        archive_intent = _has_any(lower, _ARCHIVE_TERMS)
        protect_intent = _has_any(lower, _PROTECT_TERMS)
        emails = _extract_emails(lower)
        domains = _extract_domains(lower)
        domains.extend(_known_domains(lower))
        domains = _dedupe(domains)
        has_specific_archive_target = bool(emails or domains)

        if archive_intent:
            for email in emails:
                rules.append(
                    proposed_rule(
                        action="archive",
                        scope="sender",
                        value=email,
                        source=self.source,
                        reason="user instruction indicates this sender is low-value",
                        metadata={"parser": "heuristic"},
                    )
                )
            for domain in domains:
                rules.append(
                    proposed_rule(
                        action="archive",
                        scope="domain",
                        value=domain,
                        source=self.source,
                        reason="user instruction indicates this domain is low-value",
                        metadata={"parser": "heuristic"},
                    )
                )
            if not has_specific_archive_target:
                for category in _archive_categories(lower):
                    rules.append(
                        proposed_rule(
                            action="archive",
                            scope="category",
                            value=category,
                            source=self.source,
                            reason="user instruction indicates this category can be cleaned",
                            metadata={"parser": "heuristic"},
                        )
                    )

        if protect_intent:
            for category in _protect_categories(lower):
                rules.append(
                    proposed_rule(
                        action="protect",
                        scope="category",
                        value=category,
                        source=self.source,
                        reason="user instruction indicates this category should be protected",
                        metadata={"parser": "heuristic"},
                    )
                )
            for keyword in _protect_keywords(lower):
                rules.append(
                    proposed_rule(
                        action="protect",
                        scope="keyword",
                        value=keyword,
                        source=self.source,
                        reason="user instruction indicates matching mail should not be archived",
                        metadata={"parser": "heuristic"},
                    )
                )

        return _dedupe_rules(rules)


class OpenAITeachParser:
    def __init__(self, *, model: str | None = None, client: Any | None = None, timeout: float = 30.0) -> None:
        load_server_env()
        self.model = (model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
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

    def parse(self, instruction: str) -> list[dict[str, Any]]:
        request = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You turn user email cleaning preferences into proposed MailGuard rules. "
                        "Return only valid JSON. Never enable rules. Never propose mailbox mutations."
                    ),
                },
                {"role": "user", "content": _openai_prompt(instruction)},
            ],
            "temperature": 0,
        }
        try:
            response = self.client.chat.completions.create(
                **request,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if not _looks_like_response_format_error(exc):
                raise
            response = self.client.chat.completions.create(**request)
        raw = response.choices[0].message.content or ""
        data = _parse_json_object(raw)
        return _normalize_llm_rules(data)


def run_teach_workflow(
    *,
    instruction: str,
    memory_store: MemoryStore,
    session_id: str,
    provider: EmailProvider | None = None,
    llm: TeachLLMMode = "heuristic",
    model: str = "",
    limit: int = DEFAULT_TEACH_LIMIT,
    hours: int = DEFAULT_TEACH_HOURS,
) -> dict[str, Any]:
    parser = _parser_for_mode(llm, model=model)
    parsed_rules = parser.parse(instruction)
    stored_rules = []
    created_count = 0
    for rule in parsed_rules:
        result = memory_store.create_clean_rule_once(session_id, rule)
        stored = dict(result["rule"])
        stored["created"] = bool(result.get("created"))
        if stored["created"]:
            created_count += 1
        stored_rules.append(stored)

    impact = _build_impact_preview(
        rules=stored_rules,
        provider=provider or create_email_provider(),
        preferences=memory_store.email_preferences(session_id),
        limit=limit,
        hours=hours,
    )
    return {
        "instruction": instruction,
        "parser": llm,
        "model": model,
        "created_count": created_count,
        "existing_count": len(stored_rules) - created_count,
        "rule_count": len(stored_rules),
        "rules": stored_rules,
        "impact": impact,
        "mailbox_mutation": False,
        "rule_mutation": created_count > 0,
        "proposal_mutation": False,
        "audit_mutation": False,
        "llm_authorization": False,
    }


def list_clean_rules(memory_store: MemoryStore, session_id: str, *, status: str = "", limit: int = 100) -> dict[str, Any]:
    rules = memory_store.clean_rules(session_id, status=status, limit=limit)
    return {
        "session_id": session_id,
        "status": status,
        "count": len(rules),
        "rules": rules,
        "mailbox_mutation": False,
        "rule_mutation": False,
    }


def approve_clean_rule(memory_store: MemoryStore, session_id: str, rule_id: str) -> dict[str, Any]:
    rule = memory_store.approve_clean_rule(session_id, rule_id)
    return {
        "session_id": session_id,
        "rule": rule,
        "mailbox_mutation": False,
        "rule_mutation": True,
    }


def disable_clean_rule(memory_store: MemoryStore, session_id: str, rule_id: str) -> dict[str, Any]:
    rule = memory_store.disable_clean_rule(session_id, rule_id)
    return {
        "session_id": session_id,
        "rule": rule,
        "mailbox_mutation": False,
        "rule_mutation": True,
    }


def _parser_for_mode(llm: TeachLLMMode, *, model: str) -> TeachParser:
    mode = str(llm or "heuristic").strip().lower()
    if mode == "heuristic":
        return HeuristicTeachParser()
    if mode == "openai":
        return OpenAITeachParser(model=model or None)
    raise ValueError(f"unsupported teach parser: {llm}")


def _build_impact_preview(
    *,
    rules: list[dict[str, Any]],
    provider: EmailProvider,
    preferences: dict[str, Any],
    limit: int,
    hours: int,
) -> dict[str, Any]:
    budget = {
        "limit": _bounded_int(limit, default=DEFAULT_TEACH_LIMIT, minimum=1, maximum=500),
        "hours": _bounded_int(hours, default=DEFAULT_TEACH_HOURS, minimum=1, maximum=24 * 365),
    }
    emails = _filter_recent(provider.list_recent(limit=budget["limit"], unread_only=False), hours=budget["hours"])
    policy = ArchiveProposalPolicy()
    rows = []
    for rule in rules:
        proposed = dict(rule)
        proposed["status"] = "enabled"
        examples = []
        match_count = 0
        blocked_count = 0
        for email in emails:
            classification = EmailClassification.from_decision(classify_email(email, preferences))
            if not matching_rules([proposed], email=email, classification=classification, status="enabled"):
                continue
            match_count += 1
            decision = policy.evaluate_typed(email, classification, preferences)
            blocked = rule.get("action") == "archive" and decision.decision == "protected"
            if blocked:
                blocked_count += 1
            if len(examples) < 5:
                examples.append(
                    {
                        "email_id": email.id,
                        "from_email": email.from_email,
                        "subject": email.subject,
                        "category": classification.category,
                        "importance": classification.importance,
                        "blocked_by_guard": blocked,
                    }
                )
        rows.append(
            {
                "rule_id": rule.get("rule_id", ""),
                "action": rule.get("action", ""),
                "scope": rule.get("scope", ""),
                "value": rule.get("value", ""),
                "status": rule.get("status", ""),
                "match_count": match_count,
                "blocked_by_guard_count": blocked_count,
                "would_auto_archive_count": max(0, match_count - blocked_count) if rule.get("action") == "archive" else 0,
                "would_protect_count": match_count if rule.get("action") == "protect" else 0,
                "examples": examples,
            }
        )
    return {
        "budget": budget,
        "fetched": len(emails),
        "rules": rows,
        "mailbox_mutation": False,
    }


def _normalize_llm_rules(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rules = data.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("teach parser JSON must contain a rules list")
    rules = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        try:
            rule = CleanRule.new(
                action=item.get("action", ""),
                scope=item.get("scope", ""),
                value=item.get("value", ""),
                status="proposed",
                source="openai_teach",
                reason=str(item.get("reason", "")),
                metadata={"parser": "openai"},
            ).to_dict()
        except ValueError:
            continue
        rules.append(rule)
    return _dedupe_rules(rules)


def _openai_prompt(instruction: str) -> str:
    payload = {
        "instruction": instruction,
        "allowed_actions": ["archive", "protect"],
        "allowed_scopes": ["sender", "domain", "keyword", "category"],
        "allowed_categories": ["newsletter", "promotion", "noise", "security", "finance", "meeting", "action_required"],
        "output_shape": {
            "rules": [
                {
                    "action": "archive|protect",
                    "scope": "sender|domain|keyword|category",
                    "value": "normalized lowercase value",
                    "reason": "short reason",
                }
            ]
        },
    }
    return (
        "Convert the user's natural-language email cleaning preference into conservative proposed rules.\n"
        "Use archive only for low-value mail the user clearly wants cleaned. Use protect for mail the user says should never be archived.\n"
        "Prefer sender/domain rules when a concrete sender or service is named. Use category rules only when the user describes a whole category.\n"
        "Return no rules when the instruction is too ambiguous.\n"
        f"Input:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_emails(text: str) -> list[str]:
    return _dedupe(re.findall(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", text))


def _extract_domains(text: str) -> list[str]:
    emails = set(_extract_emails(text))
    domains = []
    for match in re.findall(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", text):
        if match not in emails:
            domains.append(match)
    return _dedupe(domains)


def _known_domains(text: str) -> list[str]:
    domains = []
    for keyword, domain in _SERVICE_DOMAINS.items():
        if keyword in text:
            domains.append(domain)
    return domains


def _archive_categories(text: str) -> list[str]:
    categories = []
    if _has_any(text, ("promotion", "promo", "sale", "coupon", "广告", "促销", "优惠", "折扣")):
        categories.append("promotion")
    if _has_any(text, ("newsletter", "digest", "订阅", "简报", "周报")):
        categories.append("newsletter")
    if _has_any(text, ("social", "facebook", "linkedin", "twitter", "社交", "动态", "消息提醒")):
        categories.append("noise")
    return _dedupe(categories)


def _protect_categories(text: str) -> list[str]:
    categories = []
    if _has_any(text, ("security", "password", "login", "verification", "otp", "安全", "密码", "登录", "验证码", "账号")):
        categories.append("security")
    if _has_any(text, ("invoice", "billing", "payment", "receipt", "finance", "账单", "付款", "支付", "发票", "收据", "续费")):
        categories.append("finance")
    if _has_any(text, ("meeting", "interview", "calendar", "会议", "面试", "日程")):
        categories.append("meeting")
    return _dedupe(categories)


def _protect_keywords(text: str) -> list[str]:
    keywords = []
    for keyword, terms in _PROTECT_KEYWORDS.items():
        if _has_any(text, terms):
            keywords.append(keyword)
    return _dedupe(keywords)


def _filter_recent(emails: list[EmailMessage], *, hours: int) -> list[EmailMessage]:
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, hours))
    return [email for email in emails if _received_after(email.received_at, cutoff)]


def _received_after(value: str, cutoff: datetime) -> bool:
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed >= cutoff


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        normalized = str(value).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _dedupe_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for rule in rules:
        key = (rule.get("action"), rule.get("scope"), rule.get("value"))
        if key in seen:
            continue
        seen.add(key)
        result.append(rule)
    return result


_ARCHIVE_TERMS = (
    "archive",
    "clean",
    "ignore",
    "hide",
    "trash",
    "归档",
    "清理",
    "忽略",
    "过滤",
    "垃圾",
    "广告",
    "促销",
    "优惠",
    "折扣",
    "通知",
    "提醒",
)
_PROTECT_TERMS = (
    "protect",
    "keep",
    "never archive",
    "do not archive",
    "don't archive",
    "保留",
    "保护",
    "不要动",
    "别归档",
    "不要归档",
    "安全",
    "验证码",
    "付款",
    "账单",
    "发票",
    "面试",
    "会议",
)
_SERVICE_DOMAINS = {
    "facebook": "facebookmail.com",
    "linkedin": "linkedin.com",
    "github": "github.com",
    "twitter": "twitter.com",
}
_PROTECT_KEYWORDS = {
    "security": ("security", "安全", "风险"),
    "password": ("password", "密码"),
    "verification": ("verification", "verify", "验证码", "验证"),
    "login": ("login", "sign in", "登录"),
    "payment": ("payment", "付款", "支付"),
    "invoice": ("invoice", "billing", "账单", "发票"),
    "interview": ("interview", "面试"),
}
