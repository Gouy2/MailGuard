"""MailGuard Server - FastAPI entrypoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import AgentRuntime
from .auth import require_api_token
from .cleaner.preview import DEFAULT_HOURS as DEFAULT_CLEAN_HOURS
from .cleaner.preview import DEFAULT_LIMIT as DEFAULT_CLEAN_LIMIT
from .cleaner.preview import run_clean_preview
from .cleaner.run import clean_audit_log, clean_policy_status
from .cleaner.teach import (
    DEFAULT_TEACH_HOURS,
    DEFAULT_TEACH_LIMIT,
    approve_clean_rule,
    disable_clean_rule,
    list_clean_rules,
    run_teach_workflow,
)
from .email_classifier import classify_email
from .provider_factory import create_email_provider

app = FastAPI(title="MailGuard Server", version="0.1.0")
api_router = APIRouter()
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
runtime = AgentRuntime.create()
Protected = Depends(require_api_token)


class ChatRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=128)
    message: str = Field(min_length=1)
    system_prompt: str = Field(default="", max_length=12000)


class ClearRequest(BaseModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class ToolDecisionRequest(BaseModel):
    pending_tool_call_id: str = Field(min_length=1)


class ToolExecuteRequest(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)
    session_id: str = Field(default="default", min_length=1, max_length=128)


class CleanerTeachRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=128)
    instruction: str = Field(min_length=1)
    llm: str = Field(default="heuristic")
    model: str = Field(default="")
    limit: int = Field(default=DEFAULT_TEACH_LIMIT, ge=1, le=500)
    hours: int = Field(default=DEFAULT_TEACH_HOURS, ge=1, le=24 * 365)


class CleanerRuleDecisionRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=128)


class CleanerPreviewRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=128)
    limit: int = Field(default=DEFAULT_CLEAN_LIMIT, ge=1, le=500)
    hours: int = Field(default=DEFAULT_CLEAN_HOURS, ge=1, le=24 * 365)


@api_router.get("/health")
def health():
    return {"service": "mailguard-server", "status": "ok", **runtime.health()}


@api_router.post("/chat", dependencies=[Protected])
def chat(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="agent", system_prompt=request.system_prompt),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@api_router.post("/chat/readonly", dependencies=[Protected])
def chat_readonly(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="agent_readonly", system_prompt=request.system_prompt),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@api_router.post("/chat/simple", dependencies=[Protected])
def chat_simple(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="simple", system_prompt=request.system_prompt),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@api_router.post("/clear", dependencies=[Protected])
def clear(request: ClearRequest):
    session_id = request.session_id.strip() if request.session_id else None
    runtime.clear(session_id)
    return JSONResponse(
        {
            "status": "ok",
            "cleared_session": session_id,
            "active_sessions": runtime.memory_store.snapshot(),
        }
    )


@api_router.get("/memory", dependencies=[Protected])
def memory(session_id: str | None = None, limit: int = 20):
    session_id = session_id.strip() if session_id else None
    limit = max(1, min(limit, 100))

    if session_id is None:
        return {
            "status": "ok",
            "sessions": runtime.memory_store.snapshot(),
        }

    return {
        "status": "ok",
        "session_id": session_id,
        "count": len(runtime.memory_store.get(session_id, limit=limit)),
        "messages": runtime.memory_store.get(session_id, limit=limit),
        "notes": runtime.memory_store.notes(session_id, limit=limit),
    }


@api_router.get("/tools", dependencies=[Protected])
def tools():
    return {"status": "ok", "tools": runtime.tool_inventory()}


@api_router.get("/tools/pending", dependencies=[Protected])
def pending_tools():
    return {"status": "ok", "pending": runtime.pending_tools()}


@api_router.post("/tools/execute", dependencies=[Protected])
def execute_tool(request: ToolExecuteRequest):
    result = runtime.execute_tool(
        request.name,
        request.arguments,
        session_id=request.session_id,
    )
    status = "ok" if result.get("ok") else "error"
    if result.get("requires_approval"):
        status = "pending"
    return {"status": status, **result}


@api_router.post("/tools/approve", dependencies=[Protected])
def approve_tool(request: ToolDecisionRequest):
    result = runtime.approve_tool(request.pending_tool_call_id)
    status = "ok" if result.get("ok") else "error"
    return {"status": status, **result}


@api_router.post("/tools/reject", dependencies=[Protected])
def reject_tool(request: ToolDecisionRequest):
    result = runtime.reject_tool(request.pending_tool_call_id)
    status = "ok" if result.get("ok") else "error"
    return {"status": status, **result}


@api_router.get("/traces/{trace_id}", dependencies=[Protected])
def trace(trace_id: str):
    events = runtime.trace(trace_id)
    return {
        "status": "ok" if events else "not_found",
        "trace_id": trace_id,
        "events": events,
    }


@api_router.post("/cleaner/teach", dependencies=[Protected])
def cleaner_teach(request: CleanerTeachRequest):
    return _ok(
        run_teach_workflow(
            instruction=request.instruction,
            provider=create_email_provider(),
            memory_store=runtime.memory_store,
            session_id=request.session_id,
            llm=request.llm,
            model=request.model,
            limit=request.limit,
            hours=request.hours,
        )
    )


@api_router.get("/cleaner/rules", dependencies=[Protected])
def cleaner_rules(
    session_id: str = Query(default="default", min_length=1, max_length=128),
    status: str = "",
    limit: int = Query(default=100, ge=1, le=500),
):
    result = list_clean_rules(runtime.memory_store, session_id, status=status.strip().lower(), limit=limit)
    result["rule_status_filter"] = result.pop("status", "")
    return _ok(result)


@api_router.post("/cleaner/rules/{rule_id}/approve", dependencies=[Protected])
def cleaner_rule_approve(rule_id: str, request: CleanerRuleDecisionRequest):
    return _ok_or_404(lambda: approve_clean_rule(runtime.memory_store, request.session_id, rule_id))


@api_router.post("/cleaner/rules/{rule_id}/disable", dependencies=[Protected])
def cleaner_rule_disable(rule_id: str, request: CleanerRuleDecisionRequest):
    return _ok_or_404(lambda: disable_clean_rule(runtime.memory_store, request.session_id, rule_id))


@api_router.post("/cleaner/preview", dependencies=[Protected])
def cleaner_preview(request: CleanerPreviewRequest):
    return _ok(
        run_clean_preview(
            provider=create_email_provider(),
            memory_store=runtime.memory_store,
            session_id=request.session_id,
            classifier=classify_email,
            limit=request.limit,
            hours=request.hours,
        )
    )


@api_router.get("/cleaner/policy", dependencies=[Protected])
def cleaner_policy(session_id: str = Query(default="default", min_length=1, max_length=128)):
    return _ok(clean_policy_status(runtime.memory_store, session_id))


@api_router.get("/cleaner/audit", dependencies=[Protected])
def cleaner_audit(
    session_id: str = Query(default="default", min_length=1, max_length=128),
    run_id: str = "",
    email_id: str = "",
    limit: int = Query(default=100, ge=1, le=500),
):
    return _ok(
        clean_audit_log(
            memory_store=runtime.memory_store,
            session_id=session_id,
            run_id=run_id,
            email_id=email_id,
            limit=limit,
        )
    )


def _ok(result: dict):
    return {"status": "ok", **result}


def _ok_or_404(callback):
    try:
        return _ok(callback())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


_CONSOLE_DIST = Path(__file__).resolve().parents[2] / "console" / "dist"
app.include_router(api_router, include_in_schema=False)
app.include_router(api_router, prefix="/api")

if _CONSOLE_DIST.exists():
    app.mount("/console", StaticFiles(directory=_CONSOLE_DIST, html=True), name="console")
