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
import datetime
import json
from typing import Any, Optional

import re

from . import settings_store
from .chatlog import log_chat_turn
from .consolidation import maybe_consolidate
from .db import get_db
from .llm import chat_completion, chat_completion_stream
from .persona import build_system_prompt
from .reflection import reflect
from .tools import TOOL_HANDLERS, TOOL_SCHEMAS, dispatch


# --------------------------------------------------------------------------- #
# Inline tool-call extraction
# --------------------------------------------------------------------------- #

# 放宽：fence 后的换行改为可选，适配 ```json{"tool_calls":...}``` 无换行的情况
_FENCED_BLOCK_RE = re.compile(
    r"```(?:json)?\s*([\s\S]*?)```",
    re.DOTALL,
)

_BARE_TOOL_CALLS_RE = re.compile(
    r'\{[^{}]*?"tool_calls"\s*:\s*\[.*?\]\s*\}',
    re.DOTALL,
)

_SHORTHAND_RE = re.compile(
    r'\{\s*"name"\s*:\s*"(?P<name>[a-z_]+)"\s*,\s*"arguments"\s*:\s*(?P<args>\{[^{}]*\}|"[^"]*")\s*\}',
    re.DOTALL,
)

# 兜底：文本里残留 "tool_calls" 关键字时，尝试最外层 { ... } 提取
_RESIDUAL_RE = re.compile(
    r'\{[^{}]*"tool_calls"\s*:\s*\[[\s\S]*?\]\s*\}',
    re.DOTALL,
)


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """Normalise the arguments field: may be a dict, a JSON string, or None."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _extract_calls_from_data(data: Any) -> list[dict[str, Any]]:
    """Pull a list of {name, arguments} dicts from a parsed JSON structure."""
    calls: list[dict[str, Any]] = []
    if isinstance(data, dict) and "tool_calls" in data:
        raw_list = data["tool_calls"]
    elif isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict) and "name" in data:
        raw_list = [data]
    else:
        return []
    if not isinstance(raw_list, list):
        return []
    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function", entry)
        if not isinstance(fn, dict):
            continue
        name = fn.get("name", "")
        if not name or name not in TOOL_HANDLERS:
            continue
        arguments = _parse_arguments(fn.get("arguments"))
        calls.append({"name": name, "arguments": arguments})
    return calls


_LABEL_RE = re.compile(r"(?:\[[\w ]*\]|[Tt]ool\s*call\s*:?\s*)$")


async def _extract_inline_tool_calls(
    text: str,
    max_calls: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    traces: list[dict[str, Any]] = []
    cleaned = text
    dispatched = 0

    def _budget_left() -> int:
        if max_calls is None:
            return 999999
        return max(0, max_calls - dispatched)

    # ── Pass 1: fenced code blocks ──
    for m in reversed(list(_FENCED_BLOCK_RE.finditer(cleaned))):
        if _budget_left() <= 0:
            break
        body = m.group(1).strip()
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        calls = _extract_calls_from_data(data)
        if not calls:
            continue
        calls = calls[: _budget_left()]          # ← 截断
        for call in calls:
            result = await dispatch(call["name"], call["arguments"])
            traces.append(
                {"name": call["name"], "arguments": call["arguments"], "result": result}
            )
            dispatched += 1
        start = m.start()
        lm = _LABEL_RE.search(cleaned[:start])
        if lm:
            start = lm.start()
        cleaned = cleaned[:start] + cleaned[m.end():]

    # ── Pass 2: bare {"tool_calls": [...]} ──
    for m in reversed(list(_BARE_TOOL_CALLS_RE.finditer(cleaned))):
        if _budget_left() <= 0:
            break
        candidate = m.group(0)
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        calls = _extract_calls_from_data(data)
        if not calls:
            continue
        calls = calls[: _budget_left()]          # ← 截断
        for call in calls:
            result = await dispatch(call["name"], call["arguments"])
            traces.append(
                {"name": call["name"], "arguments": call["arguments"], "result": result}
            )
            dispatched += 1
        start = m.start()
        lm = _LABEL_RE.search(cleaned[:start])
        if lm:
            start = lm.start()
        cleaned = cleaned[:start] + cleaned[m.end():]

    # ── Pass 3: shorthand ──
    for m in reversed(list(_SHORTHAND_RE.finditer(cleaned))):
        if _budget_left() <= 0:
            break
        name = m.group("name")
        if name not in TOOL_HANDLERS:
            continue
        raw_args = m.group("args")
        if raw_args.startswith('"'):
            try:
                raw_args = json.loads(raw_args)
            except (json.JSONDecodeError, ValueError):
                continue
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(arguments, dict):
            continue
        result = await dispatch(name, arguments)
        traces.append({"name": name, "arguments": arguments, "result": result})
        dispatched += 1
        start = m.start()
        lm = _LABEL_RE.search(cleaned[:start])
        if lm:
            start = lm.start()
        cleaned = cleaned[:start] + cleaned[m.end():]

    # ── Pass 4: 兜底 ──
    if '"tool_calls"' in cleaned and _budget_left() > 0:
        for m in reversed(list(_RESIDUAL_RE.finditer(cleaned))):
            if _budget_left() <= 0:
                break
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                continue
            calls = _extract_calls_from_data(data)
            if not calls:
                continue
            calls = calls[: _budget_left()]
            for call in calls:
                result = await dispatch(call["name"], call["arguments"])
                traces.append(
                    {"name": call["name"], "arguments": call["arguments"], "result": result}
                )
                dispatched += 1
            cleaned = cleaned[:m.start()] + cleaned[m.end():]

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    traces.reverse()
    return cleaned, traces

# --------------------------------------------------------------------------- #
# Context snapshot
# --------------------------------------------------------------------------- #
async def _build_context_block(
    subject: Optional[str], session_id: str = ""
) -> str:
    """Render a *lightweight* index of what dint knows about this learner.

    Historically this dumped the full memory / skill / knowledge contents into
    the system prompt on every turn, which bloated each API call. Now it only
    injects a tiny index — counts plus any time-sensitive review-due items — and
    leaves the full details to be fetched on demand via the dedicated search
    tools (``recall_memory``, ``skill_report``, ``knowledge_lookup`` and
    ``concept_progress``), each of which filters by a query rather than dumping
    everything. The model decides when it actually needs to look at its notes, so
    a simple "hi" no longer pays for a full context dump.
    """
    db = await get_db()
    sections: list[str] = []

    if session_id:
        sections.append(
            f"Current session id: {session_id}\n"
            "(Use this exact value for the session_id parameter of concept_progress.)"
        )

    # Cheap counts only — enough for the model to know WHETHER it has notes worth
    # inspecting, without paying to embed all of them every turn.
    memories = await db.list_memory(limit=1)
    skills = await db.list_skills(limit=1)
    if subject:
        knowledge = await db.search_knowledge(subject, limit=1)
    else:
        sub = await db.knowledge_subgraph(limit=1)
        knowledge = sub["nodes"]

    index_bits: list[str] = []
    if memories:
        index_bits.append("long-term memory")
    if skills:
        index_bits.append("skill estimates")
    if knowledge:
        index_bits.append("known concepts")

    if index_bits:
        sections.append(
            "Your notes on this learner are NOT loaded into this prompt. You have "
            "recorded: " + ", ".join(index_bits) + ". "
            "When you need the details — at the start of a topic, or before deciding "
            "what to teach next — SEARCH for them with the dedicated tools rather "
            "than guessing: recall_memory(query=...) for memory, skill_report(query=...) "
            "for skills, knowledge_lookup(query=...) for concepts, and "
            "concept_progress(session_id, query=...) for this session's checklist. "
            "Each tool filters to matching entries — pass a focused query (e.g. the "
            "topic at hand"
            + (f", such as '{subject}'" if subject else "")
            + ") instead of dumping everything. Don't guess at what you recorded; look."
        )
    else:
        sections.append(
            "You have no notes on this learner yet. As you teach, use remember / "
            "skill_update / knowledge_add to build them up for next time."
        )

    # Spaced-repetition review-due is time-sensitive and actionable, so it stays
    # in the prompt (kept compact). Everything else is fetched on demand.
    due = await db.skills_due_for_review(limit=3)
    if due:
        names = ", ".join(s["name"] for s in due)
        sections.append(
            "Review due (spaced repetition):\n"
            f"The following skills are due for review: {names}.\n"
            "Before diving into new material, open with ONE quick recall question "
            "about the most overdue skill. After the learner answers, call "
            "review_skill with the appropriate quality (0-5). Then proceed to "
            "the learner's actual request.\n"
            "Judge quality by what they DEMONSTRATE, not by confidence or a bare "
            "'yeah I remember' — a vague or wrong answer is low quality even if "
            "they sound sure. If a skill you recorded as mastered now looks shaky, "
            "that is exactly what review is for: score it low so the estimate "
            "downgrades and you circle back. Understanding is dynamic; trust the "
            "latest behavior over your old notes."
        )

    sections.append(f"Current time: {datetime.datetime.now().strftime('%A %H:%M')}")
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

    # ── Dedup: skip writing the user message if the last stored row is
    #    already an identical user turn (e.g. a retried/double-submitted
    #    request after a stream error). Prevents duplicate rows from
    #    polluting the replayed context on subsequent turns. ────────────
    history_rows = await db.list_messages(session_id)
    is_dup = (
        history_rows
        and history_rows[-1]["role"] == "user"
        and (history_rows[-1]["content"] or "").strip() == user_message.strip()
    )
    if not is_dup:
        await db.add_message(session_id, "user", user_message)
        history_rows = await db.list_messages(session_id)

    # Auto-title the session from the first user message (no-op if already titled).
    await db.auto_title_session(session_id, user_message)

    context_block = await _build_context_block(subject, session_id)
    system_prompt = build_system_prompt(context_block)

    # The user message is the last row; drop it from the replay set because we
    # append it explicitly below (avoids a duplicate in the prompt).
    replay = _stored_to_openai(history_rows[:-1])
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(replay)
    messages.append({"role": "user", "content": user_message})

    tool_trace: list[dict[str, Any]] = []
    reply_text = ""

    # Per-turn tool budget. ``max_tool_rounds`` caps loop *iterations*; this
    # caps the *total number of individual tool calls* across all iterations,
    # so one turn can't fire 6 calls × 8 rounds = 48 operations.
    tool_budget = int(cfg.get("max_tool_calls_per_turn", 4))
    tool_calls_dispatched = 0

    for _ in range(int(cfg["max_tool_rounds"])):
        # Over budget → call without tools so the model is forced to emit a
        # final text reply instead of more tool invocations.
        over_budget = tool_calls_dispatched >= tool_budget
        resp = await chat_completion(
            messages, tools=None if over_budget else TOOL_SCHEMAS
        )
        msg = resp.choices[0].message

        # ── Structured tool_calls path ─────────────────────────────────
        if msg.tool_calls:
            # Append the assistant's tool-calling message verbatim so the
            # follow-up tool results line up with their calls.
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
                tool_calls_dispatched += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result,
                    }
                )
            continue

        # ── No structured tool_calls: check for inline (JSON-in-text) ──
        reply_text = msg.content or ""
        remaining = max(0, tool_budget - tool_calls_dispatched)
        reply_text, inline_traces = await _extract_inline_tool_calls(
            reply_text, max_calls=remaining
        )

        if inline_traces:
            # Feed the inline results back to the model (mirrors the
            # structured path) so it can react to them, then loop again.
            tool_trace.extend(inline_traces)
            tool_calls_dispatched += len(inline_traces)

            fake_tool_calls = []
            for i, trace in enumerate(inline_traces):
                tc_id = f"inline_{i}_{id(trace)}"
                fake_tool_calls.append(
                    {
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": trace["name"],
                            "arguments": json.dumps(
                                trace["arguments"], ensure_ascii=False
                            ),
                        },
                    }
                )
            messages.append(
                {
                    "role": "assistant",
                    "content": reply_text,
                    "tool_calls": fake_tool_calls,
                }
            )
            for i, trace in enumerate(inline_traces):
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": fake_tool_calls[i]["id"],
                        "content": trace["result"],
                    }
                )
            continue  # model sees the results, generates the final reply

        # No inline calls either → this is the genuine final reply.
        break

    else:
        reply_text = (
            "I went down a bit of a rabbit hole there. Let me reset — "
            "tell me again what you're trying to understand."
        )

    # ── 空回复兜底（非流式）──────────────────────────────────────────
    if not reply_text.strip():
        resp = await chat_completion(messages, tools=None)
        reply_text = resp.choices[0].message.content or ""

    if not reply_text.strip():
        reply_text = "…"

    await db.add_message(
        session_id,
        "assistant",
        reply_text,
        meta={"tool_calls": tool_trace} if tool_trace else None,
    )

    # Background reflection: update memory / skills / knowledge without blocking.
    asyncio.create_task(_safe_reflect(session_id, messages))
    log_chat_turn(session_id, user_message, reply_text, subject)
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
    """Streaming variant of :func:`respond`."""
    db = await get_db()
    cfg = settings_store.effective()

    # ── 去重 ───────────────────────────────────────────────────────────
    history_rows = await db.list_messages(session_id)
    is_dup = (
        history_rows
        and history_rows[-1]["role"] == "user"
        and (history_rows[-1]["content"] or "").strip() == user_message.strip()
    )
    if not is_dup:
        await db.add_message(session_id, "user", user_message)
        history_rows = await db.list_messages(session_id)

    await db.auto_title_session(session_id, user_message)

    context_block = await _build_context_block(subject, session_id)
    system_prompt = build_system_prompt(context_block)

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

    yield {"event": "done", "data": {"reply": reply_text, "tool_calls": tool_trace}}

    asyncio.create_task(_safe_reflect(session_id, messages))

    async def _background_consolidate_and_log():
        try:
            await maybe_consolidate()
        except Exception:  # noqa: BLE001
            pass
        log_chat_turn(session_id, user_message, reply_text, subject)

    asyncio.create_task(_background_consolidate_and_log())


async def regenerate_stream(session_id: str, subject: Optional[str] = None):
    """Regenerate dint's most recent reply (the manual "retry" flow).
    The caller must have already removed the previous assistant message from the
    DB. This replays the stored history — which now ends with the learner's
    message — and streams a fresh reply, persisting it exactly like a normal turn.
    Yields the same SSE event dicts as :func:`respond_stream`.
    """
    db = await get_db()
    cfg = settings_store.effective()
    context_block = await _build_context_block(subject, session_id)
    system_prompt = build_system_prompt(context_block)
    history_rows = await db.list_messages(session_id)
    replay = _stored_to_openai(history_rows)
    if not replay:
        # Nothing to re-answer (e.g. empty session).
        yield {"event": "done", "data": {"reply": "", "tool_calls": []}}
        return
    # Grab the last user message for the chatlog entry.
    _last_user_msg = ""
    for _row in reversed(history_rows):
        if _row.get("role") == "user":
            _last_user_msg = _row.get("content") or ""
            break
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

    yield {"event": "done", "data": {"reply": reply_text, "tool_calls": tool_trace}}
    
    # Schedule background tasks after stream is complete
    asyncio.create_task(_safe_reflect(session_id, messages))
    
    async def _background_consolidate_and_log():
        try:
            await maybe_consolidate()
        except Exception:  # noqa: BLE001 - consolidation is best-effort
            pass
        log_chat_turn(session_id, _last_user_msg, reply_text, subject)
    
    asyncio.create_task(_background_consolidate_and_log())


async def _stream_tool_loop(
    messages: list[dict[str, Any]],
    cfg: dict[str, Any],
    out: dict[str, Any],
):
    tool_trace: list[dict[str, Any]] = []
    reply_text = ""
    tool_budget = int(cfg.get("max_tool_calls_per_turn", 4))
    tool_calls_dispatched = 0

    for _ in range(int(cfg["max_tool_rounds"])):
        content_parts: list[str] = []
        tc_accum: dict[int, dict[str, Any]] = {}

        # 超预算 → 不传 tools，强制纯文本回复
        over_budget = tool_calls_dispatched >= tool_budget
        async for chunk in chat_completion_stream(
            messages, tools=None if over_budget else TOOL_SCHEMAS
        ):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield {"event": "token", "data": delta.content}
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

        if not tc_accum:
            reply_text = "".join(content_parts)
            remaining = max(0, tool_budget - tool_calls_dispatched)
            reply_text, inline_traces = await _extract_inline_tool_calls(
                reply_text, max_calls=remaining
            )
            tool_calls_dispatched += len(inline_traces)
            for trace_entry in inline_traces:
                tool_trace.append(trace_entry)
                yield {"event": "tool_call", "data": trace_entry}
            break

        # 结构化 tool_calls：本轮全部派发（模型已经吐出来了，不能吞），
        # 但计入预算，下一轮会被截断
        tool_calls_list = [tc_accum[i] for i in sorted(tc_accum)]
        messages.append(
            {
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": tool_calls_list,
            }
        )
        for tc in tool_calls_list:
            name = tc["function"]["name"]
            try:
                arguments = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            result = await dispatch(name, arguments)
            trace_entry = {"name": name, "arguments": arguments, "result": result}
            tool_trace.append(trace_entry)
            tool_calls_dispatched += 1
            yield {"event": "tool_call", "data": trace_entry}
            messages.append(
                {"role": "tool", "tool_call_id": tc["id"], "content": result}
            )
    else:
        reply_text = (
            "I went down a bit of a rabbit hole there. Let me reset — "
            "tell me again what you're trying to understand."
        )

    # ── 空回复兜底 ─────────────────────────────────────────────────────
    # 模型可能把所有轮次都花在工具调用上，从没产出最终文本；或者它的
    # "回复"整段就是被提取剥掉的内联 tool-call JSON，什么都不剩。
    # 两种情况用户都会看到空气泡。强制一次无 tools 的补全，此时 messages
    # 末尾是工具结果，模型只需要总结——总能挤出文本。
    if not reply_text.strip():
        final_parts: list[str] = []
        async for chunk in chat_completion_stream(messages, tools=None):
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                final_parts.append(delta.content)
                yield {"event": "token", "data": delta.content}
        reply_text = "".join(final_parts)

    # 最后一道保险：强制补全也空了（provider 彻底摆烂）。
    if not reply_text.strip():
        reply_text = "…"

    out["reply_text"] = reply_text
    out["tool_trace"] = tool_trace

async def _safe_reflect(session_id: str, messages: list[dict[str, Any]]) -> None:
    """Run reflection in the background.

    Best-effort: any error is swallowed so it can never surface to the user or
    block the reply.  Consolidation is handled separately in the main flow so
    the result can be reported to the frontend via the SSE done event.
    """
    try:
        await reflect(session_id, messages)
    except Exception:  # noqa: BLE001 - reflection is best-effort
        pass
