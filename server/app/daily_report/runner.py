"""Runner for manual daily read-only email report agent runs."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..email_provider import EmailProvider
from ..provider_factory import create_email_provider
from .models import FINISH_ACTION, VALID_ACTIONS, Action, Budget, Item, Run, Step, new_run_id, now_iso
from .planner import Planner, build_planner
from .storage import ReportStorage
from .tools import DEFAULT_MEMORY_PATH, DailyTools, provider_summary


def run_daily_report(
    *,
    provider: EmailProvider | None = None,
    planner: Planner | None = None,
    llm: str = "mock",
    model: str = "",
    limit: int = 20,
    hours: int = 24,
    max_steps: int = 8,
    timeout_sec: float = 120.0,
    memory_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    email_provider = provider or create_email_provider()
    budget = Budget(limit=limit, hours=hours, max_steps=max_steps, timeout_sec=timeout_sec)
    active_planner = planner or build_planner(llm, model=model, timeout=timeout_sec)
    storage = ReportStorage(output_dir)
    run = Run(
        run_id=new_run_id(),
        status="running",
        started_at=now_iso(),
        planner=active_planner.label,
        provider=provider_summary(email_provider),
        budget=budget,
    )
    tools = DailyTools(email_provider, budget=budget, memory_path=memory_path or DEFAULT_MEMORY_PATH)
    started = time.monotonic()

    try:
        _run_loop(run, active_planner, tools, started)
    finally:
        if run.status == "running":
            run.status = "error"
            run.error = "daily report did not finish"
        run.finished_at = now_iso()
        storage.save(run)
    return run.to_dict()


def _run_loop(run: Run, planner: Planner, tools: DailyTools, started: float) -> None:
    for index in range(1, run.budget.max_steps + 1):
        if time.monotonic() - started > run.budget.timeout_sec:
            run.status = "error"
            run.error = "timeout_exceeded"
            return
        try:
            action = planner.next_action(run)
        except Exception as exc:
            run.status = "error"
            run.error = f"planner_error: {type(exc).__name__}: {exc}"
            return
        if action.name not in VALID_ACTIONS:
            run.steps.append(
                Step(
                    index=index,
                    action=action.name,
                    args=dict(action.args),
                    error=f"unsupported action: {action.name}",
                )
            )
            run.status = "error"
            run.error = f"unsupported action: {action.name}"
            return
        if action.name == FINISH_ACTION:
            try:
                _finish(run, action, index)
            except Exception as exc:
                run.steps.append(
                    Step(
                        index=index,
                        action=action.name,
                        args=dict(action.args),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                run.status = "error"
                run.error = f"{type(exc).__name__}: {exc}"
            return
        _execute_step(run, tools, action, index)
        if run.status == "error":
            return

    run.status = "error"
    run.error = "max_steps_exceeded"


def _execute_step(run: Run, tools: DailyTools, action: Action, index: int) -> None:
    started = time.monotonic()
    observation: dict[str, Any] = {}
    error = ""
    try:
        observation = tools.execute(action)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = int((time.monotonic() - started) * 1000)
    run.steps.append(
        Step(
            index=index,
            action=action.name,
            args=dict(action.args),
            observation=observation,
            latency_ms=latency_ms,
            error=error,
        )
    )
    if error:
        run.status = "error"
        run.error = error


def _finish(run: Run, action: Action, index: int) -> None:
    report = str(action.args.get("report") or action.args.get("summary") or "").strip()
    items = [_item_from_raw(item) for item in _raw_items(action.args)]
    if not report:
        report = _fallback_report(items)
    run.report = report
    run.items = items
    run.status = "ok"
    run.steps.append(
        Step(
            index=index,
            action=FINISH_ACTION,
            args=dict(action.args),
            observation={"finished": True, "item_count": len(items)},
        )
    )


def _raw_items(args: dict[str, Any]) -> list[Any]:
    items = args.get("items", [])
    return items if isinstance(items, list) else []


def _item_from_raw(raw: Any) -> Item:
    item = Item.from_raw(raw)
    if not item.email_id:
        raise ValueError("finish item email_id is required")
    return item


def _fallback_report(items: list[Item]) -> str:
    if not items:
        return "No key emails were selected for this daily report."
    return "Key emails: " + "; ".join(item.subject for item in items[:5] if item.subject)
