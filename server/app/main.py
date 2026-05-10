"""Wispera Server - FastAPI entrypoint."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import AgentRuntime
from .auth import require_api_token

app = FastAPI(title="Wispera Server", version="0.1.0")
STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
runtime = AgentRuntime.create()


class ChatRequest(BaseModel):
    session_id: str = Field(default="default", min_length=1, max_length=128)
    message: str = Field(min_length=1)


class ClearRequest(BaseModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class ToolDecisionRequest(BaseModel):
    pending_tool_call_id: str = Field(min_length=1)


class ToolExecuteRequest(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)
    session_id: str = Field(default="default", min_length=1, max_length=128)


@app.get("/health")
def health():
    return {"service": "wispera-server", "status": "ok", **runtime.health()}


Protected = Depends(require_api_token)


@app.post("/chat", dependencies=[Protected])
def chat(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="agent"),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@app.post("/chat/simple", dependencies=[Protected])
def chat_simple(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="simple"),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@app.post("/clear", dependencies=[Protected])
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


@app.get("/memory", dependencies=[Protected])
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


@app.get("/tools", dependencies=[Protected])
def tools():
    return {"status": "ok", "tools": runtime.tool_inventory()}


@app.get("/tools/pending", dependencies=[Protected])
def pending_tools():
    return {"status": "ok", "pending": runtime.pending_tools()}


@app.post("/tools/execute", dependencies=[Protected])
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


@app.post("/tools/approve", dependencies=[Protected])
def approve_tool(request: ToolDecisionRequest):
    result = runtime.approve_tool(request.pending_tool_call_id)
    status = "ok" if result.get("ok") else "error"
    return {"status": status, **result}


@app.post("/tools/reject", dependencies=[Protected])
def reject_tool(request: ToolDecisionRequest):
    result = runtime.reject_tool(request.pending_tool_call_id)
    status = "ok" if result.get("ok") else "error"
    return {"status": status, **result}


@app.get("/traces/{trace_id}", dependencies=[Protected])
def trace(trace_id: str):
    events = runtime.trace(trace_id)
    return {
        "status": "ok" if events else "not_found",
        "trace_id": trace_id,
        "events": events,
    }
