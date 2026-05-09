"""Wispera Server - FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .agent import AgentRuntime

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


@app.get("/health")
def health():
    return {"service": "wispera-server", "status": "ok", **runtime.health()}


@app.post("/chat")
def chat(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="agent"),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@app.post("/chat/simple")
def chat_simple(request: ChatRequest):
    return StreamingResponse(
        runtime.stream_chat(request.session_id, request.message, mode="simple"),
        media_type="text/event-stream",
        headers=STREAM_HEADERS,
    )


@app.post("/clear")
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


@app.get("/memory")
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


@app.get("/tools")
def tools():
    return {"status": "ok", "tools": runtime.tool_inventory()}
