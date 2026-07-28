"""dint's tool definitions and implementations.

Each tool is described in OpenAI function-calling schema format (``TOOL_SCHEMAS``)
and backed by an async handler registered in ``TOOL_HANDLERS``. The agent loop
dispatches tool calls by name.

Tools fall into two groups:

* **State tools** – read/write dint's long-term memory, skill graph, knowledge
  graph and the per-question concept-progress checklist. These are what let dint
  "remember" and "learn" across conversations.
* **Web search** – a best-effort, dependency-light web search. It tries, in
  order: a configured Brave/Serper-style key, then DuckDuckGo HTML, and finally
  falls back to a clear "search unavailable" message so teaching is never blocked.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from .config import get_settings
from .db import get_db

Handler = Callable[[dict[str, Any]], Awaitable[str]]


# --------------------------------------------------------------------------- #
# Web search
# --------------------------------------------------------------------------- #
async def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, str]]:
    """Scrape DuckDuckGo's HTML endpoint. No API key required."""
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
        resp = await client.post(url, data={"q": query})
        resp.raise_for_status()
        html = resp.text

    results: list[dict[str, str]] = []
    # Lightweight parsing without an HTML dependency: find result anchors.
    marker = 'class="result__a"'
    idx = 0
    while len(results) < max_results:
        pos = html.find(marker, idx)
        if pos == -1:
            break
        href_start = html.rfind('href="', 0, pos)
        href_end = html.find('"', href_start + 6)
        href = html[href_start + 6: href_end] if href_start != -1 else ""
        title_start = html.find(">", pos) + 1
        title_end = html.find("</a>", title_start)
        title = html[title_start:title_end].strip()
        # snippet
        snip_marker = 'class="result__snippet"'
        snip_pos = html.find(snip_marker, pos)
        snippet = ""
        if snip_pos != -1 and snip_pos < pos + 1500:
            s_start = html.find(">", snip_pos) + 1
            s_end = html.find("</a>", s_start)
            if s_end == -1:
                s_end = html.find("</td>", s_start)
            snippet = html[s_start:s_end].strip()
        if title:
            results.append({"title": _strip_tags(title), "url": href, "snippet": _strip_tags(snippet)})
        idx = pos + len(marker)
    return results


def _strip_tags(text: str) -> str:
    out, in_tag = [], False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    cleaned = "".join(out)
    # Unescape the handful of HTML entities DuckDuckGo emits in titles/snippets.
    # Entity strings are built via concatenation so source formatters cannot
    # collapse them into the literal characters they represent.
    amp = chr(38)  # '&'
    entities = {
        amp + "amp;": "&",
        amp + "lt;": "<",
        amp + "gt;": ">",
        amp + "quot;": '"',
        amp + "#x27;": "'",
        amp + "#39;": "'",
        amp + "nbsp;": " ",
    }
    for entity, char in entities.items():
        cleaned = cleaned.replace(entity, char)
    return cleaned.strip()


async def web_search(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "web_search: missing 'query'."
    max_results = get_settings().web_search_results
    try:
        results = await _search_duckduckgo(query, max_results)
    except Exception as exc:  # noqa: BLE001 - search must never crash teaching
        return (
            f"web_search: search backend unavailable ({exc.__class__.__name__}). "
            "Reason from first principles instead, or tell the learner you can't "
            "verify this right now."
        )
    if not results:
        return f"web_search: no results for {query!r}."
    lines = [f"Results for {query!r}:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Long-term memory
# --------------------------------------------------------------------------- #
async def recall_memory(args: dict[str, Any]) -> str:
    db = await get_db()
    rows = await db.list_memory(limit=int(args.get("limit", 25)))
    if not rows:
        return "No long-term memories recorded yet about this learner."
    lines = ["Long-term memory (most recent first):"]
    for r in rows:
        lines.append(f"- [{r['kind']}] {r['content']}")
    return "\n".join(lines)


async def remember(args: dict[str, Any]) -> str:
    kind = (args.get("kind") or "note").strip()
    content = (args.get("content") or "").strip()
    if not content:
        return "remember: missing 'content'."
    db = await get_db()
    await db.add_memory(kind, content)
    return f"Stored to long-term memory ({kind}): {content}"


async def memory_delete(args: dict[str, Any]) -> str:
    memory_id = (args.get("id") or "").strip()
    if not memory_id:
        return "memory_delete: missing 'id'."
    db = await get_db()
    await db.delete_memory(memory_id)
    return f"Deleted memory entry {memory_id}."


async def memory_update(args: dict[str, Any]) -> str:
    memory_id = (args.get("id") or "").strip()
    if not memory_id:
        return "memory_update: missing 'id'."
    kind = args.get("kind")
    content = args.get("content")
    if kind is None and content is None:
        return "memory_update: provide 'kind' and/or 'content'."
    db = await get_db()
    await db.update_memory(memory_id, kind=kind, content=content)
    return f"Updated memory entry {memory_id}."


# --------------------------------------------------------------------------- #
# Skill graph
# --------------------------------------------------------------------------- #
async def skill_report(args: dict[str, Any]) -> str:
    db = await get_db()
    skills = await db.list_skills(limit=int(args.get("limit", 40)))
    if not skills:
        return "No skills tracked yet for this learner."
    lines = ["Learner skill estimates:"]
    for s in skills:
        lines.append(
            f"- {s['name']} [{s['domain'] or 'general'}]: {s['status']} "
            f"(confidence {s['confidence']:.2f})"
        )
    return "\n".join(lines)


async def skill_update(args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "skill_update: missing 'name'."
    db = await get_db()
    await db.update_skill(
        name=name,
        domain=args.get("domain"),
        status=args.get("status"),
        confidence=args.get("confidence"),
        evidence=args.get("evidence"),
    )
    return f"Updated skill '{name}'."


async def skill_delete(args: dict[str, Any]) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "skill_delete: missing 'name'."
    db = await get_db()
    await db.delete_skill(name)
    return f"Deleted skill '{name}'."


# --------------------------------------------------------------------------- #
# Knowledge graph
# --------------------------------------------------------------------------- #
async def knowledge_lookup(args: dict[str, Any]) -> str:
    query = (args.get("query") or "").strip()
    db = await get_db()
    if not query:
        sub = await db.knowledge_subgraph(limit=20)
        if not sub["nodes"]:
            return "Knowledge graph is empty."
        return "Knowledge graph (recent concepts):\n" + "\n".join(
            f"- {n['label']} ({n['subject'] or 'general'})" for n in sub["nodes"]
        )
    rows = await db.search_knowledge(query)
    if not rows:
        return f"No knowledge recorded for {query!r} yet."
    return "Matching concepts:\n" + "\n".join(
        f"- {r['label']} ({r['subject'] or 'general'}): {r['summary'] or 'no summary'}"
        for r in rows
    )


async def knowledge_add(args: dict[str, Any]) -> str:
    label = (args.get("label") or "").strip()
    if not label:
        return "knowledge_add: missing 'label'."
    db = await get_db()
    await db.upsert_knowledge_node(label, args.get("subject"), args.get("summary"))
    for rel in args.get("relates_to") or []:
        await db.add_knowledge_edge(label, rel, "relates_to", args.get("subject"))
    return f"Recorded concept '{label}'."


async def knowledge_delete(args: dict[str, Any]) -> str:
    label = (args.get("label") or "").strip()
    if not label:
        return "knowledge_delete: missing 'label'."
    db = await get_db()
    ok = await db.delete_knowledge_node(label)
    if not ok:
        return f"knowledge_delete: concept '{label}' not found."
    return f"Deleted concept '{label}' and its edges."


async def knowledge_update(args: dict[str, Any]) -> str:
    old_label = (args.get("old_label") or "").strip()
    new_label = (args.get("new_label") or "").strip()
    if not old_label or not new_label:
        return "knowledge_update: missing 'old_label' or 'new_label'."
    db = await get_db()
    ok = await db.rename_knowledge_node(
        old_label, new_label, subject=args.get("subject"), summary=args.get("summary")
    )
    if not ok:
        return f"knowledge_update: concept '{old_label}' not found."
    return f"Renamed concept '{old_label}' → '{new_label}'."


# --------------------------------------------------------------------------- #
# Concept progress (per-question checklist)
# --------------------------------------------------------------------------- #
async def review_skill(args: dict[str, Any]) -> str:
    """Record the outcome of a spaced-repetition review for a skill."""
    name = (args.get("name") or "").strip()
    if not name:
        return "review_skill: missing 'name'."
    quality = int(args.get("quality", 3))
    db = await get_db()
    await db.record_review(name, quality)
    return f"Recorded review for '{name}' (quality {quality}). Next review scheduled."


async def concept_progress(args: dict[str, Any]) -> str:
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return "concept_progress: missing 'session_id'."
    db = await get_db()
    action = args.get("action", "list")
    if action == "set":
        concept = (args.get("concept") or "").strip()
        status = (args.get("status") or "demonstrated").strip()
        if not concept:
            return "concept_progress: 'set' needs a 'concept'."
        await db.set_concept_progress(session_id, concept, status)
        return f"Marked '{concept}' as {status}."
    rows = await db.list_concept_progress(session_id)
    if not rows:
        return "No concept checklist yet for this question."
    mark = {"demonstrated": "✓", "next": "○", "locked": "○"}
    return "Concept checklist:\n" + "\n".join(
        f"{mark.get(r['status'], '○')} {r['concept']} — {r['status']}" for r in rows
    )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
TOOL_HANDLERS: dict[str, Handler] = {
    "web_search": web_search,
    "recall_memory": recall_memory,
    "remember": remember,
    "memory_delete": memory_delete,
    "memory_update": memory_update,
    "skill_report": skill_report,
    "skill_update": skill_update,
    "skill_delete": skill_delete,
    "knowledge_lookup": knowledge_lookup,
    "knowledge_add": knowledge_add,
    "knowledge_delete": knowledge_delete,
    "knowledge_update": knowledge_update,
    "concept_progress": concept_progress,
    "review_skill": review_skill,
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current or specific factual information "
                "(library versions, current events, exact API details). Do NOT use "
                "this for things you can reason through, and never to fetch an "
                "answer to hand to the learner."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": (
                "Read your long-term memory about THIS learner: their goals, prior "
                "knowledge, preferences, recurring mistakes. Call this when starting "
                "a topic so you don't repeat yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max entries.", "default": 25}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store a durable fact about the learner in long-term memory. Use for "
                "goals, prior knowledge, preferences, recurring mistakes — not chatter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["preference", "fact", "goal", "note"],
                        "description": "Category of the memory.",
                    },
                    "content": {"type": "string", "description": "The fact to remember."},
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_report",
            "description": "Read your running estimate of the learner's skills and confidence.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 40}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": (
                "Update the learner's estimated competence for a skill based on "
                "evidence from the conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name."},
                    "domain": {"type": "string", "description": "Subject area."},
                    "status": {
                        "type": "string",
                        "enum": ["locked", "emerging", "demonstrated"],
                    },
                    "confidence": {"type": "number", "description": "0.0 to 1.0."},
                    "evidence": {"type": "string", "description": "Short note on why."},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_lookup",
            "description": (
                "Look up concepts in your knowledge graph of the subjects you teach, "
                "to stay consistent and connect new ideas to old ones. Empty query "
                "lists recent concepts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Concept to search for."}
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_add",
            "description": (
                "Add or update a concept in your knowledge graph, with optional links. "
                "IMPORTANT: Before adding, call knowledge_lookup to check if the concept "
                "already exists under a similar name. Reuse existing names — do NOT create "
                "near-duplicates (e.g. 'for loop' vs 'for-loop' vs 'for loops')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Concept name."},
                    "subject": {"type": "string", "description": "Subject area."},
                    "summary": {"type": "string", "description": "Tight summary."},
                    "relates_to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Other concept names this relates to.",
                    },
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_skill",
            "description": (
                "Record the outcome of a spaced-repetition review question. Call this "
                "after the learner answers a review question. quality is 0-5 on the "
                "SM-2 scale: 5 = perfect recall, 3 = correct with difficulty, "
                "0 = complete failure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name being reviewed."},
                    "quality": {
                        "type": "integer",
                        "description": "Recall quality 0-5 (SM-2 scale).",
                        "minimum": 0,
                        "maximum": 5,
                    },
                },
                "required": ["name", "quality"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": "Delete a long-term memory entry by its id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory entry id to delete."}
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_update",
            "description": "Update an existing long-term memory entry's kind and/or content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory entry id."},
                    "kind": {
                        "type": "string",
                        "enum": ["preference", "fact", "goal", "note"],
                        "description": "New category.",
                    },
                    "content": {"type": "string", "description": "New content."},
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_delete",
            "description": "Delete a skill from the learner's skill graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name to delete."}
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_delete",
            "description": "Delete a concept (and its edges) from the knowledge graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Concept label to delete."}
                },
                "required": ["label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_update",
            "description": (
                "Rename/merge a concept in the knowledge graph. If new_label already "
                "exists, the old node is merged into it and edges are preserved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_label": {"type": "string", "description": "Current concept label."},
                    "new_label": {"type": "string", "description": "New concept label."},
                    "subject": {"type": "string", "description": "Optional new subject."},
                    "summary": {"type": "string", "description": "Optional new summary."},
                },
                "required": ["old_label", "new_label"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "concept_progress",
            "description": (
                "Track the per-question concept checklist visible to the learner "
                "(✓ demonstrated / ○ next). This is the whiteboard checklist that "
                "shows the learner exactly what they've built and what's coming. "
                "IMPORTANT: Call this with action='set' EVERY TIME the learner "
                "demonstrates understanding of a concept — do not wait. When you "
                "start a new topic, seed it with the key concepts as 'next'. "
                "Use the session_id from your context block. "
                "Omit action (or use 'list') to see the current checklist state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current session id."},
                    "action": {"type": "string", "enum": ["list", "set"], "default": "list"},
                    "concept": {"type": "string", "description": "Concept name (for set)."},
                    "status": {
                        "type": "string",
                        "enum": ["locked", "next", "demonstrated"],
                        "default": "demonstrated",
                    },
                },
                "required": ["session_id"],
            },
        },
    },
]


async def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Run a tool by name, returning its string result. Never raises."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Unknown tool: {name}"
    try:
        return await handler(arguments)
    except Exception as exc:  # noqa: BLE001
        return f"Tool '{name}' failed: {exc.__class__.__name__}: {exc}"