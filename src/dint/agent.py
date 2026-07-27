"""The teaching agent: context assembly + tool-calling loop.

This is the heart of dint. For each learner message it:

1. Builds a compact *context snapshot* of the learner (long-term memory, skill
   estimates, relevant knowledge) and injects it into the system prompt.
2. Replays the session's stored message history plus the new user message.
3. Runs an OpenAI-style tool-calling loop: while the model emits ``tool_calls``,
   dispatch each one through :mod:`dint.tools`, feed the results back, and ask
   again — until the model produces a final text reply.
4. Persists the user message, the assistant reply, and (optionally) a trace of
   the tool calls that happened in between.
5. Fires a background reflection pass that quietly updates memory / skills /
   knowledge. This runs as an asyncio task so it never blocks the reply.

The public entry point is :func:`respond`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from . import settings_store
from .db import get_db
from .llm import chat_completion, chat_completion_stream
from .persona import build_system_prompt
from .reflection import reflect
from .tools import TOOL_SCHEMAS, dispatch

# How many of the most recent stored messages to replay into context. Keeps the
# prompt bounded on long sessions while preserving recent conversational flow.
_HISTORY_WINDOW = 40


# --------------------------------------------------------------------------- #
# Context snapshot
# --------------------------------------------------------------------------- #
async def _build_context_block(subject: Optional[str]) -> str:
    """Render a compact snapshot of what dint knows about this learner."""
    db = await get_db()
    sections: list[str] = []

    memories = await db.list_memory(limit=15)
    if memories:
        lines = [f"- [{m['kind']}] {m['content']}" for m in memories]
        sections.append("Long-term memory:\n" + "\n".join(lines))

    skills = await db.list_skills(limit=20)
    if skills:
        lines = [
            f"- {s['name']} ({s['domain'] or 'general'}): {s['status']}, "
            f"confidence {s['confidence']:.2f}"
            for s in skills
        ]
        sections.append("Skill estimates:\n" + "\n".join(lines))

    # Pull knowledge relevant to the current subject if we have one, otherwise a
    # small recent slice so dint can connect ideas.
    if subject:
        knowledge = await db.search_knowledge(subject, limit=12)
    else:
        sub = await db.knowledge_subgraph(limit=12)
        knowledge = sub["nodes"]
    if knowledge:
        lines = [
            f"- {k['label']} ({k.get('subject') or 'general'}): "
            f"{k.get('summary') or 'no summary'}"
            for k in knowledge
        ]
        sections.append("Known concepts:\n" + "\n".join(lines))

    # Spaced-repetition: check for skills due for review.
    due = await db.skills_due_for_review(limit=3)
    if due:
        names = ", ".join(s["name"] for s in due)
        sections.append(
            "Review due (spaced repetition):\n"
            f"The following skills are due for review: {names}.\n"
            "Before diving into new material, open with ONE quick recall question "
            "about the most overdue skill. After the learner answers, call "
            "review_skill with the appropriate quality (0-5). Then proceed to "
            "the learner's actual request."
        )

    return "\n\n".join(sections)


def _stored_to_openai(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert stored message rows into OpenAI wire-format messages.

    Only ``user`` and ``assistant`` text turns are replayed; tool-call traces are
    kept in the DB for the UI but are not re-sent to the model (they'd bloat the
    prompt and the model doesn't need them to stay coherent).
    """
    out: list[dict[str, Any]] = []
    for r in rows:
        role = r.get("role")
        content = r.get("content") or ""
        if role in ("user", "assistant") and content.strip():
            out.append({"role": role, "content": content})
    return out


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
async def respond(
    session_id: str,
    user_message: str,
    subject: Optional[str] = None,
) -> dict[str, Any]:
    """Produce dint's reply to ``user_message`` within ``session_id``.

    Returns a dict with ``reply`` (the assistant text) and ``tool_calls`` (a list
    of ``{name, arguments, result}`` traces for the UI's "thinking" panel).
    """
    db = await get_db()
    cfg = settings_store.effective()

    # Persist the learner's message first so it's part of history even if the
    # model call fails partway through.
    await db.add_message(session_id, "user", user_message)

    context_block = await _build_context_block(subject)
    system_prompt = build_system_prompt(context_block)

    history_rows = await db.list_messages(session_id, limit=_HISTORY_WINDOW)
    # The just-added user message is the last row; drop it from the replay set
    # because we append it explicitly below (avoids a duplicate).
    replay = _stored_to_openai(history_rows[:-1])

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(replay)
    messages.append({"role": "user", "content": user_message})

    tool_trace: list[dict[str, Any]] = []
    reply_text = ""

    for _ in range(int(cfg["max_tool_rounds"])):
        resp = await chat_completion(messages, tools=TOOL_SCHEMAS)
        msg = resp.choices[0].message

        if not msg.tool_calls:
            reply_text = msg.content or ""
            break

        # Append the assistant's tool-calling message verbatim so the follow-up
        # tool results line up with their calls.
        messages.append(_assistant_message(msg))

        for call in msg.tool_calls:
            name = call.function.name
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = await dispatch(name, arguments)
            tool_trace.append(
                {"name": name, "arguments": arguments, "result": result}
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )
    else:
        # Exhausted the tool budget without a final answer.
        reply_text = (
            "I went down a bit of a rabbit hole there. Let me reset — "
            "tell me again what you're trying to understand."
        )

    await db.add_message(
        session_id,
        "assistant",
        reply_text,
        meta={"tool_calls": tool_trace} if tool_trace else None,
    )

    # Background reflection: update memory / skills / knowledge without blocking.
    asyncio.create_task(_safe_reflect(session_id, messages))

    return {"reply": reply_text, "tool_calls": tool_trace}


def _assistant_message(msg: Any) -> dict[str, Any]:
    """Serialise an assistant message (with tool calls) for re-submission."""
    tool_calls = [
        {
            "id": c.id,
            "type": "function",
            "function": {
                "name": c.function.name,
                "arguments": c.function.arguments,
            },
        }
        for c in (msg.tool_calls or [])
    ]
    return {
        "role": "assistant",
        "content": msg.content or "",
        "tool_calls": tool_calls,
    }


async def respond_stream(
    session_id: str,
    user_message: str,
    subject: Optional[str] = None,
):
    """Streaming variant of :func:`respond`.

    Yields dicts suitable for SSE serialisation:

    * ``{"event": "token", "data": "..."}`` – a text fragment of the reply
    * ``{"event": "tool_call", "data": {...}}`` – a completed tool call trace
    * ``{"event": "done", "data": {"reply": "...", "tool_calls": [...]}}``

    The full reply and tool trace are persisted to the DB exactly as in the
    non-streaming path.
    """
    db = await get_db()
    cfg = settings_store.effective()

    await db.add_message(session_id, "user", user_message)

    context_block = await _build_context_block(subject)
    system_prompt = build_system_prompt(context_block)

    history_rows = await db.list_messages(session_id, limit=_HISTORY_WINDOW)
    replay = _stored_to_openai(history_rows[:-1])

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(replay)
    messages.append({"role": "user", "content": user_message})

    out: dict[str, Any] = {}
    async for ev in _stream_tool_loop(messages, cfg, out):
        yield ev

    reply_text = out["reply_text"]
    tool_trace = out["tool_trace"]

    await db.add_message(
        session_id,
        "assistant",
        reply_text,
        meta={"tool_calls": tool_trace} if tool_trace else None,
    )

    asyncio.create_task(_safe_reflect(session_id, messages))

    yield {"event": "done", "data": {"reply": reply_text, "tool_calls": tool_trace}}


async def regenerate_stream(session_id: str, subject: Optional[str] = None):
    """Regenerate dint's most recent reply (the manual "retry" flow).

    The caller must have already removed the previous assistant message from the
    DB. This replays the stored history — which now ends with the learner's
    message — and streams a fresh reply, persisting it exactly like a normal turn.
    Yields the same SSE event dicts as :func:`respond_stream`.
    """
    db = await get_db()
    cfg = settings_store.effective()

    context_block = await _build_context_block(subject)
    system_prompt = build_system_prompt(context_block)

    history_rows = await db.list_messages(session_id, limit=_HISTORY_WINDOW)
    replay = _stored_to_openai(history_rows)
    if not replay:
        # Nothing to re-answer (e.g. empty session).
        yield {"event": "done", "data": {"reply": "", "tool_calls": []}}
        return

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(replay)

    out: dict[str, Any] = {}
    async for ev in _stream_tool_loop(messages, cfg, out):
        yield ev

    reply_text = out["reply_text"]
    tool_trace = out["tool_trace"]

    await db.add_message(
        session_id,
        "assistant",
        reply_text,
        meta={"tool_calls": tool_trace} if tool_trace else None,
    )

    asyncio.create_task(_safe_reflect(session_id, messages))

    yield {"event": "done", "data": {"reply": reply_text, "tool_calls": tool_trace}}


async def _stream_tool_loop(
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
    out: dict[str, Any],
):
    """Shared streaming tool-calling loop used by both reply and retry paths.

    Mutates ``messages`` in place (appending assistant/tool turns) and yields SSE
    event dicts (``token`` / ``tool_call``). On completion, stores ``reply_text``
    and ``tool_trace`` in ``out``.
    """
    tool_trace: list[dict[str, Any]] = []
    reply_text = ""

    for _ in range(int(cfg["max_tool_rounds"])):
        # Accumulate streamed deltas for this round.
        content_parts: list[str] = []
        # tool_calls arrive as fragments keyed by index
        tc_accum: dict[int, dict[str, Any]] = {}

        async for chunk in chat_completion_stream(messages, tools=TOOL_SCHEMAS):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Text content tokens
            if delta.content:
                content_parts.append(delta.content)
                yield {"event": "token", "data": delta.content}

            # Tool-call fragments
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_accum:
                        tc_accum[idx] = {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    acc = tc_accum[idx]
                    if tc_delta.id:
                        acc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            acc["function"]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            acc["function"]["arguments"] += tc_delta.function.arguments

        # If no tool calls were accumulated, this was the final text reply.
        if not tc_accum:
            reply_text = "".join(content_parts)
            break

        # Reconstruct the assistant message with tool calls for the conversation.
        tool_calls_list = [tc_accum[i] for i in sorted(tc_accum)]
        messages.append(
            {
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
            }
        )

        # Dispatch each tool call and stream the trace events.
        for tc in tool_calls_list:
            name = tc["function"]["name"]
            try:
                arguments = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = await dispatch(name, arguments)
            trace_entry = {"name": name, "arguments": arguments, "result": result}
            tool_trace.append(trace_entry)
            yield {"event": "tool_call", "data": trace_entry}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                }
            )
    else:
        reply_text = (
            "I went down a bit of a rabbit hole there. Let me reset — "
            "tell me again what you're trying to understand."
        )

    out["reply_text"] = reply_text
    out["tool_trace"] = tool_trace


async def _safe_reflect(session_id: str, messages: list[dict[str, Any]]) -> None:
    """Run reflection, swallowing any error so it can never surface to the user."""
    try:
        await reflect(session_id, messages)
    except Exception:  # noqa: BLE001 - reflection is best-effort
        pass
