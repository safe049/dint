"""FastAPI application for dint.

Exposes a REST API that the web UI talks to:

* ``POST /api/chat`` – send a message, get dint's reply + tool trace
* ``GET  /api/sessions`` / ``POST /api/sessions`` – session management
* ``GET  /api/sessions/{id}/messages`` – conversation history
* ``GET  /api/memory`` – long-term memory
* ``GET  /api/skills`` – skill estimates
* ``GET  /api/knowledge`` – knowledge graph (nodes + edges)
* ``GET  /api/sessions/{id}/progress`` – per-session concept checklist

The app also serves the static frontend from ``../frontend``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import json

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import settings_store
from .agent import regenerate_stream, respond, respond_stream
from .db import get_db
from .llm import reset_client

app = FastAPI(title="dint", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #
class ChatRequest(BaseModel):
    session_id: str
    message: str
    subject: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]] = []


class RegenerateRequest(BaseModel):
    session_id: str
    subject: Optional[str] = None


class SessionCreate(BaseModel):
    title: Optional[str] = None
    subject: Optional[str] = None


class SettingsUpdate(BaseModel):
    settings: dict[str, Any] = {}


class SkillUpsert(BaseModel):
    name: str
    domain: Optional[str] = None
    status: Optional[str] = None
    confidence: Optional[float] = None
    evidence: Optional[str] = None


class MemoryAdd(BaseModel):
    kind: str = "note"
    content: str


class MemoryUpdate(BaseModel):
    kind: Optional[str] = None
    content: Optional[str] = None


class KnowledgeNodeDelete(BaseModel):
    label: str


class KnowledgeNodeRename(BaseModel):
    old_label: str
    new_label: str
    subject: Optional[str] = None
    summary: Optional[str] = None


class KnowledgeEdgeDelete(BaseModel):
    src_label: str
    dst_label: str
    relation: str = "relates_to"


class SkillRename(BaseModel):
    old_name: str
    new_name: str


class ProgressUpsert(BaseModel):
    concept: str
    status: str = "locked"


class ProgressDelete(BaseModel):
    concept: str


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    result = await respond(req.session_id, req.message, req.subject)
    return ChatResponse(reply=result["reply"], tool_calls=result["tool_calls"])


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE streaming variant of the chat endpoint.

    Emits ``event: token`` for each text fragment, ``event: tool_call`` for
    completed tool invocations, and ``event: done`` with the final payload.
    """
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")

    async def event_generator():
        async for evt in respond_stream(req.session_id, req.message, req.subject):
            event_name = evt["event"]
            data = json.dumps(evt["data"], ensure_ascii=False)
            yield f"event: {event_name}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/regenerate")
async def chat_regenerate(req: RegenerateRequest) -> StreamingResponse:
    """SSE streaming "retry" endpoint.

    Removes dint's most recent assistant reply for the session and streams a
    freshly generated replacement, using the same SSE event format as
    ``/api/chat/stream``.
    """
    db = await get_db()
    removed = await db.delete_last_assistant_message(req.session_id)
    if not removed:
        raise HTTPException(status_code=404, detail="no assistant message to regenerate")

    async def event_generator():
        async for evt in regenerate_stream(req.session_id, req.subject):
            event_name = evt["event"]
            data = json.dumps(evt["data"], ensure_ascii=False)
            yield f"event: {event_name}\ndata: {data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
@app.get("/api/sessions")
async def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    db = await get_db()
    return await db.list_sessions(limit=limit)


@app.post("/api/sessions")
async def create_session(body: SessionCreate) -> dict[str, str]:
    db = await get_db()
    sid = await db.create_session(body.title, body.subject)
    return {"id": sid}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, str]:
    db = await get_db()
    await db.delete_session(session_id)
    return {"status": "deleted"}


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str, limit: int = 200) -> list[dict[str, Any]]:
    db = await get_db()
    return await db.list_messages(session_id, limit=limit)


@app.get("/api/sessions/{session_id}/progress")
async def get_progress(session_id: str) -> list[dict[str, Any]]:
    db = await get_db()
    return await db.list_concept_progress(session_id)


@app.put("/api/sessions/{session_id}/progress")
async def upsert_progress(session_id: str, body: ProgressUpsert) -> dict[str, str]:
    if not body.concept.strip():
        raise HTTPException(status_code=400, detail="concept must not be empty")
    if body.status not in ("locked", "next", "demonstrated"):
        raise HTTPException(status_code=400, detail="invalid status")
    db = await get_db()
    await db.set_concept_progress(session_id, body.concept.strip(), body.status)
    return {"status": "saved"}


@app.delete("/api/sessions/{session_id}/progress")
async def delete_progress(session_id: str, body: ProgressDelete) -> dict[str, str]:
    db = await get_db()
    ok = await db.delete_concept_progress(session_id, body.concept.strip())
    if not ok:
        raise HTTPException(status_code=404, detail="concept not found")
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
@app.get("/api/settings")
async def get_settings_api() -> dict[str, Any]:
    cfg = settings_store.effective()
    out = dict(cfg)
    out["openai_api_key"] = settings_store.mask_key(cfg.get("openai_api_key", ""))
    return out


@app.put("/api/settings")
async def put_settings_api(body: SettingsUpdate) -> dict[str, Any]:
    try:
        cfg = settings_store.save(body.settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Rebuild the OpenAI client so key/base-url changes apply immediately.
    reset_client()
    out = dict(cfg)
    out["openai_api_key"] = settings_store.mask_key(cfg.get("openai_api_key", ""))
    return out


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
@app.get("/api/memory")
async def get_memory(limit: int = 40) -> list[dict[str, Any]]:
    db = await get_db()
    return await db.list_memory(limit=limit)


@app.post("/api/memory")
async def add_memory(body: MemoryAdd) -> dict[str, str]:
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")
    db = await get_db()
    mid = await db.add_memory(body.kind, body.content)
    return {"id": mid}


@app.put("/api/memory/{memory_id}")
async def update_memory(memory_id: str, body: MemoryUpdate) -> dict[str, str]:
    if body.kind is None and body.content is None:
        raise HTTPException(status_code=400, detail="provide kind and/or content")
    if body.content is not None and not body.content.strip():
        raise HTTPException(status_code=400, detail="content must not be empty")
    db = await get_db()
    await db.update_memory(memory_id, kind=body.kind, content=body.content)
    return {"status": "updated"}


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str) -> dict[str, str]:
    db = await get_db()
    await db.delete_memory(memory_id)
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Skills
# --------------------------------------------------------------------------- #
@app.get("/api/skills")
async def get_skills(limit: int = 60) -> list[dict[str, Any]]:
    db = await get_db()
    return await db.list_skills(limit=limit)


@app.put("/api/skills")
async def upsert_skill(body: SkillUpsert) -> dict[str, str]:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name must not be empty")
    db = await get_db()
    await db.update_skill(
        body.name, body.domain, body.status, body.confidence, body.evidence
    )
    return {"status": "saved"}


@app.put("/api/skills/rename")
async def rename_skill(body: SkillRename) -> dict[str, str]:
    db = await get_db()
    ok = await db.rename_skill(body.old_name, body.new_name)
    if not ok:
        raise HTTPException(status_code=404, detail="skill not found or invalid rename")
    return {"status": "renamed"}


@app.delete("/api/skills/{name}")
async def delete_skill(name: str) -> dict[str, str]:
    db = await get_db()
    await db.delete_skill(name)
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Knowledge graph
# --------------------------------------------------------------------------- #
@app.get("/api/knowledge")
async def get_knowledge(limit: int = 60) -> dict[str, Any]:
    db = await get_db()
    return await db.knowledge_subgraph(limit=limit)


@app.delete("/api/knowledge/nodes")
async def delete_knowledge_node(body: KnowledgeNodeDelete) -> dict[str, str]:
    db = await get_db()
    ok = await db.delete_knowledge_node(body.label)
    if not ok:
        raise HTTPException(status_code=404, detail="node not found")
    return {"status": "deleted"}


@app.put("/api/knowledge/nodes/rename")
async def rename_knowledge_node(body: KnowledgeNodeRename) -> dict[str, str]:
    db = await get_db()
    ok = await db.rename_knowledge_node(
        body.old_label, body.new_label, subject=body.subject, summary=body.summary
    )
    if not ok:
        raise HTTPException(status_code=404, detail="node not found")
    return {"status": "renamed"}


@app.delete("/api/knowledge/edges")
async def delete_knowledge_edge(body: KnowledgeEdgeDelete) -> dict[str, str]:
    db = await get_db()
    ok = await db.delete_knowledge_edge(body.src_label, body.dst_label, body.relation)
    if not ok:
        raise HTTPException(status_code=404, detail="edge not found")
    return {"status": "deleted"}


# --------------------------------------------------------------------------- #
# Reset (clear all learner data, keep settings/config)
# --------------------------------------------------------------------------- #
@app.post("/api/reset")
async def reset_all_data() -> dict[str, Any]:
    """Wipe ALL learner data: sessions, messages, concept progress, knowledge
    graph, skills and memory. Settings/config are preserved."""
    db = await get_db()
    counts = await db.clear_all_learner_data()
    return {"status": "reset", "deleted": counts}


# --------------------------------------------------------------------------- #
# Static frontend
# --------------------------------------------------------------------------- #
# Prefer the package-bundled frontend (pip-installable), fall back to the
# project-root ``frontend/`` directory during development.
_frontend_dir = Path(__file__).resolve().parent / "frontend"
if not _frontend_dir.is_dir():
    _frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
