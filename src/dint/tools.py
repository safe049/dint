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

Batch support: write/update/delete tools accept an optional ``items`` (or
``ids`` / ``names`` / ``labels`` / ``concepts``) array so the model can combine
multiple operations into a single tool call, reducing API round-trips.  The
original single-item parameters are kept for backward compatibility.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from .config import get_settings
from .db import get_db

Handler = Callable[[dict[str, Any]], Awaitable[str]]


def _matches(query: str, *fields: Any) -> bool:
    """Case-insensitive substring match of ``query`` against any of ``fields``.

    An empty query matches everything, so callers can treat it as an optional
    filter without extra branching.
    """
    q = query.strip().lower()
    if not q:
        return True
    return any(q in str(f).lower() for f in fields if f is not None)


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
    query = (args.get("query") or "").strip()
    rows = await db.list_memory(limit=int(args.get("limit", 50)))
    if query:
        rows = [r for r in rows if _matches(query, r["kind"], r["content"])]
    if not rows:
        if query:
            return f"No long-term memories match {query!r}."
        return "No long-term memories recorded yet about this learner."
    header = (
        f"Long-term memory matching {query!r}:"
        if query
        else "Long-term memory (most recent first):"
    )
    lines = [header]
    for r in rows:
        lines.append(f"- [{r['kind']}] {r['content']}")
    return "\n".join(lines)


async def remember(args: dict[str, Any]) -> str:
    """Store one or more facts in long-term memory.

    Accepts either the legacy single-item form (``kind`` + ``content``) or a
    batch ``items`` array of ``{kind, content}`` objects.
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        # Backward-compatible single-item path.
        content = (args.get("content") or "").strip()
        if not content:
            return "remember: missing 'content' (or provide 'items' array)."
        items = [{"kind": args.get("kind") or "note", "content": content}]

    results: list[str] = []
    for item in items:
        kind = (item.get("kind") or "note").strip()
        content = (item.get("content") or "").strip()
        if not content:
            results.append("remember: skipped item with empty 'content'.")
            continue
        await db.add_memory(kind, content)
        results.append(f"Stored ({kind}): {content}")
    return "remember: " + " | ".join(results)


async def memory_delete(args: dict[str, Any]) -> str:
    """Delete one or more memory entries by id.

    Accepts a single ``id`` or a batch ``ids`` array.
    """
    db = await get_db()
    ids: list[str] = args.get("ids") or []
    if not ids:
        single = (args.get("id") or "").strip()
        if not single:
            return "memory_delete: missing 'id' (or provide 'ids' array)."
        ids = [single]

    results: list[str] = []
    for memory_id in ids:
        memory_id = memory_id.strip()
        if not memory_id:
            continue
        await db.delete_memory(memory_id)
        results.append(f"Deleted {memory_id}")
    return "memory_delete: " + " | ".join(results) if results else "memory_delete: no valid ids."


async def memory_update(args: dict[str, Any]) -> str:
    """Update one or more memory entries.

    Accepts the legacy single-item form (``id`` + ``kind``/``content``) or a
    batch ``items`` array of ``{id, kind?, content?}`` objects.
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        memory_id = (args.get("id") or "").strip()
        if not memory_id:
            return "memory_update: missing 'id' (or provide 'items' array)."
        kind = args.get("kind")
        content = args.get("content")
        if kind is None and content is None:
            return "memory_update: provide 'kind' and/or 'content'."
        items = [{"id": memory_id, "kind": kind, "content": content}]

    results: list[str] = []
    for item in items:
        memory_id = (item.get("id") or "").strip()
        if not memory_id:
            results.append("memory_update: skipped item with empty 'id'.")
            continue
        kind = item.get("kind")
        content = item.get("content")
        if kind is None and content is None:
            results.append(f"memory_update: skipped {memory_id} (no kind/content).")
            continue
        await db.update_memory(memory_id, kind=kind, content=content)
        results.append(f"Updated {memory_id}")
    return "memory_update: " + " | ".join(results)


# --------------------------------------------------------------------------- #
# Skill graph
# --------------------------------------------------------------------------- #
async def skill_report(args: dict[str, Any]) -> str:
    db = await get_db()
    query = (args.get("query") or "").strip()
    skills = await db.list_skills(limit=int(args.get("limit", 80)))
    if query:
        skills = [s for s in skills if _matches(query, s["name"], s["domain"], s["status"])]
    if not skills:
        if query:
            return f"No skills match {query!r}."
        return "No skills tracked yet for this learner."
    header = f"Skill estimates matching {query!r}:" if query else "Learner skill estimates:"
    lines = [header]
    for s in skills:
        lines.append(
            f"- {s['name']} [{s['domain'] or 'general'}]: {s['status']} "
            f"(confidence {s['confidence']:.2f})"
        )
    return "\n".join(lines)


async def skill_update(args: dict[str, Any]) -> str:
    """Update one or more skill estimates.

    Accepts the legacy single-skill form or a batch ``items`` array of skill
    objects (each with ``name``, ``domain``, ``status``, ``confidence``,
    ``evidence``).
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        name = (args.get("name") or "").strip()
        if not name:
            return "skill_update: missing 'name' (or provide 'items' array)."
        items = [args]

    results: list[str] = []
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            results.append("skill_update: skipped item with empty 'name'.")
            continue
        await db.update_skill(
            name=name,
            domain=item.get("domain"),
            status=item.get("status"),
            confidence=item.get("confidence"),
            evidence=item.get("evidence"),
        )
        results.append(f"Updated '{name}'")
    return "skill_update: " + " | ".join(results)


async def skill_delete(args: dict[str, Any]) -> str:
    """Delete one or more skills.

    Accepts a single ``name`` or a batch ``names`` array.
    """
    db = await get_db()
    names: list[str] = args.get("names") or []
    if not names:
        single = (args.get("name") or "").strip()
        if not single:
            return "skill_delete: missing 'name' (or provide 'names' array)."
        names = [single]

    results: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        await db.delete_skill(name)
        results.append(f"Deleted '{name}'")
    return "skill_delete: " + " | ".join(results) if results else "skill_delete: no valid names."


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
    """Add or update one or more concepts in the knowledge graph.

    Accepts the legacy single-concept form or a batch ``items`` array of
    concept objects (each with ``label``, ``subject``, ``summary``,
    ``relates_to``).
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        label = (args.get("label") or "").strip()
        if not label:
            return "knowledge_add: missing 'label' (or provide 'items' array)."
        items = [args]

    results: list[str] = []
    for item in items:
        label = (item.get("label") or "").strip()
        if not label:
            results.append("knowledge_add: skipped item with empty 'label'.")
            continue
        await db.upsert_knowledge_node(label, item.get("subject"), item.get("summary"))
        for rel in item.get("relates_to") or []:
            await db.add_knowledge_edge(label, rel, "relates_to", item.get("subject"))
        results.append(f"Recorded '{label}'")
    return "knowledge_add: " + " | ".join(results)


async def knowledge_delete(args: dict[str, Any]) -> str:
    """Delete one or more concepts from the knowledge graph.

    Accepts a single ``label`` or a batch ``labels`` array.
    """
    db = await get_db()
    labels: list[str] = args.get("labels") or []
    if not labels:
        single = (args.get("label") or "").strip()
        if not single:
            return "knowledge_delete: missing 'label' (or provide 'labels' array)."
        labels = [single]

    results: list[str] = []
    for label in labels:
        label = label.strip()
        if not label:
            continue
        ok = await db.delete_knowledge_node(label)
        if ok:
            results.append(f"Deleted '{label}'")
        else:
            results.append(f"'{label}' not found")
    return "knowledge_delete: " + " | ".join(results) if results else "knowledge_delete: no valid labels."


async def knowledge_update(args: dict[str, Any]) -> str:
    """Rename/merge one or more concepts in the knowledge graph.

    Accepts the legacy single form (``old_label`` + ``new_label``) or a batch
    ``items`` array of ``{old_label, new_label, subject?, summary?}`` objects.
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        old_label = (args.get("old_label") or "").strip()
        new_label = (args.get("new_label") or "").strip()
        if not old_label or not new_label:
            return "knowledge_update: missing 'old_label'/'new_label' (or provide 'items' array)."
        items = [args]

    results: list[str] = []
    for item in items:
        old_label = (item.get("old_label") or "").strip()
        new_label = (item.get("new_label") or "").strip()
        if not old_label or not new_label:
            results.append("knowledge_update: skipped item missing old_label/new_label.")
            continue
        ok = await db.rename_knowledge_node(
            old_label, new_label, subject=item.get("subject"), summary=item.get("summary")
        )
        if ok:
            results.append(f"'{old_label}' → '{new_label}'")
        else:
            results.append(f"'{old_label}' not found")
    return "knowledge_update: " + " | ".join(results)


# --------------------------------------------------------------------------- #
# Concept progress (per-question checklist)
# --------------------------------------------------------------------------- #
async def review_skill(args: dict[str, Any]) -> str:
    """Record the outcome of one or more spaced-repetition reviews.

    Accepts the legacy single form (``name`` + ``quality``) or a batch
    ``items`` array of ``{name, quality}`` objects.
    """
    db = await get_db()
    items: list[dict[str, Any]] = args.get("items") or []
    if not items:
        name = (args.get("name") or "").strip()
        if not name:
            return "review_skill: missing 'name' (or provide 'items' array)."
        items = [{"name": name, "quality": args.get("quality", 3)}]

    results: list[str] = []
    for item in items:
        name = (item.get("name") or "").strip()
        if not name:
            results.append("review_skill: skipped item with empty 'name'.")
            continue
        quality = int(item.get("quality", 3))
        await db.record_review(name, quality)
        results.append(f"Reviewed '{name}' (quality {quality})")
    return "review_skill: " + " | ".join(results)


async def concept_progress(args: dict[str, Any]) -> str:
    """Track the per-question concept checklist.

    For ``action='set'``, accepts either a single ``concept``/``status`` pair
    or a batch ``concepts`` array of ``{concept, status}`` objects so multiple
    checklist items can be updated in one call.
    """
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return "concept_progress: missing 'session_id'."
    db = await get_db()
    action = args.get("action", "list")

    if action == "set":
        # Batch path: ``concepts`` array.
        concepts: list[dict[str, Any]] = args.get("concepts") or []
        if not concepts:
            # Backward-compatible single-item path.
            concept = (args.get("concept") or "").strip()
            status = (args.get("status") or "demonstrated").strip()
            if not concept:
                return "concept_progress: 'set' needs a 'concept' (or 'concepts' array)."
            concepts = [{"concept": concept, "status": status}]

        results: list[str] = []
        for entry in concepts:
            concept = (entry.get("concept") or "").strip()
            status = (entry.get("status") or "demonstrated").strip()
            if not concept:
                results.append("skipped empty concept")
                continue
            await db.set_concept_progress(session_id, concept, status)
            results.append(f"'{concept}' → {status}")
        return "concept_progress: " + " | ".join(results)

    # action == "list"
    query = (args.get("query") or "").strip()
    rows = await db.list_concept_progress(session_id)
    if query:
        rows = [r for r in rows if _matches(query, r["concept"], r["status"])]
    if not rows:
        if query:
            return f"No checklist concepts match {query!r}."
        return "No concept checklist yet for this question."
    mark = {"demonstrated": "✓", "next": "○", "locked": "○"}
    header = f"Concept checklist matching {query!r}:" if query else "Concept checklist:"
    return header + "\n" + "\n".join(
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
                "SEARCH your long-term memory about THIS learner (goals, prior "
                "knowledge, preferences, recurring mistakes). Pass a 'query' to "
                "filter to matching entries rather than dumping everything — e.g. "
                "query='recursion' or query='preference'. Call this when starting a "
                "topic so you don't repeat yourself. Omit 'query' to list the most "
                "recent entries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional keyword to filter memories (case-insensitive substring match).",
                    },
                    "limit": {"type": "integer", "description": "Max entries to scan.", "default": 50},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store one or more durable facts about the learner in long-term "
                "memory. Use for goals, prior knowledge, preferences, recurring "
                "mistakes — not chatter. To store multiple facts in one call, pass "
                "an 'items' array of {kind, content} objects instead of calling "
                "this tool repeatedly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["preference", "fact", "goal", "note"],
                        "description": "Category of the memory (single-item mode).",
                    },
                    "content": {"type": "string", "description": "The fact to remember (single-item mode)."},
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of {kind, content} objects to store in one call.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["preference", "fact", "goal", "note"],
                                },
                                "content": {"type": "string"},
                            },
                            "required": ["content"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_report",
            "description": (
                "SEARCH your running estimate of the learner's skills and confidence. "
                "Pass a 'query' to filter to matching skills (by name, domain or "
                "status) instead of listing everything — e.g. query='loop'. Omit "
                "'query' to list all tracked skills."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional keyword to filter skills (case-insensitive substring match).",
                    },
                    "limit": {"type": "integer", "default": 80},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_update",
            "description": (
                "Update the learner's estimated competence for one or more skills "
                "based on evidence from the conversation. To update multiple skills "
                "in one call, pass an 'items' array of skill objects instead of "
                "calling this tool repeatedly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name (single-item mode)."},
                    "domain": {"type": "string", "description": "Subject area."},
                    "status": {
                        "type": "string",
                        "enum": ["locked", "emerging", "demonstrated"],
                    },
                    "confidence": {"type": "number", "description": "0.0 to 1.0."},
                    "evidence": {"type": "string", "description": "Short note on why."},
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of skill objects to update in one call.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "domain": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["locked", "emerging", "demonstrated"],
                                },
                                "confidence": {"type": "number"},
                                "evidence": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                },
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
                "Add or update one or more concepts in your knowledge graph, with "
                "optional links. IMPORTANT: Before adding, call knowledge_lookup to "
                "check if the concept already exists under a similar name. Reuse "
                "existing names — do NOT create near-duplicates. To add multiple "
                "concepts in one call, pass an 'items' array of concept objects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Concept name (single-item mode)."},
                    "subject": {"type": "string", "description": "Subject area."},
                    "summary": {"type": "string", "description": "Tight summary."},
                    "relates_to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Other concept names this relates to.",
                    },
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of concept objects to add in one call.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "subject": {"type": "string"},
                                "summary": {"type": "string"},
                                "relates_to": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["label"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_skill",
            "description": (
                "Record the outcome of one or more spaced-repetition review "
                "questions. Call this after the learner answers review questions. "
                "quality is 0-5 on the SM-2 scale: 5 = perfect recall, 3 = correct "
                "with difficulty, 0 = complete failure. To record multiple reviews "
                "in one call, pass an 'items' array of {name, quality} objects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name being reviewed (single-item mode)."},
                    "quality": {
                        "type": "integer",
                        "description": "Recall quality 0-5 (SM-2 scale).",
                        "minimum": 0,
                        "maximum": 5,
                    },
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of {name, quality} objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quality": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "maximum": 5,
                                },
                            },
                            "required": ["name", "quality"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": (
                "Delete one or more long-term memory entries by id. To delete "
                "multiple entries in one call, pass an 'ids' array."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory entry id to delete (single-item mode)."},
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Batch mode: array of memory entry ids to delete.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_update",
            "description": (
                "Update one or more long-term memory entries' kind and/or content. "
                "To update multiple entries in one call, pass an 'items' array of "
                "{id, kind?, content?} objects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Memory entry id (single-item mode)."},
                    "kind": {
                        "type": "string",
                        "enum": ["preference", "fact", "goal", "note"],
                        "description": "New category.",
                    },
                    "content": {"type": "string", "description": "New content."},
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of {id, kind?, content?} objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["preference", "fact", "goal", "note"],
                                },
                                "content": {"type": "string"},
                            },
                            "required": ["id"],
                        },
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skill_delete",
            "description": (
                "Delete one or more skills from the learner's skill graph. To "
                "delete multiple skills in one call, pass a 'names' array."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name to delete (single-item mode)."},
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Batch mode: array of skill names to delete.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_delete",
            "description": (
                "Delete one or more concepts (and their edges) from the knowledge "
                "graph. To delete multiple concepts in one call, pass a 'labels' "
                "array."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Concept label to delete (single-item mode)."},
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Batch mode: array of concept labels to delete.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_update",
            "description": (
                "Rename/merge one or more concepts in the knowledge graph. If "
                "new_label already exists, the old node is merged into it and edges "
                "are preserved. To rename multiple concepts in one call, pass an "
                "'items' array of {old_label, new_label, subject?, summary?} objects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_label": {"type": "string", "description": "Current concept label (single-item mode)."},
                    "new_label": {"type": "string", "description": "New concept label."},
                    "subject": {"type": "string", "description": "Optional new subject."},
                    "summary": {"type": "string", "description": "Optional new summary."},
                    "items": {
                        "type": "array",
                        "description": "Batch mode: array of rename objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_label": {"type": "string"},
                                "new_label": {"type": "string"},
                                "subject": {"type": "string"},
                                "summary": {"type": "string"},
                            },
                            "required": ["old_label", "new_label"],
                        },
                    },
                },
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
                "To set multiple concepts in one call, pass a 'concepts' array of "
                "{concept, status} objects. "
                "Omit action (or use 'list') to see the current checklist state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "Current session id."},
                    "action": {"type": "string", "enum": ["list", "set"], "default": "list"},
                    "concept": {"type": "string", "description": "Concept name (single-item set)."},
                    "status": {
                        "type": "string",
                        "enum": ["locked", "next", "demonstrated"],
                        "default": "demonstrated",
                    },
                    "concepts": {
                        "type": "array",
                        "description": "Batch mode: array of {concept, status} objects to set in one call.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "concept": {"type": "string"},
                                "status": {
                                    "type": "string",
                                    "enum": ["locked", "next", "demonstrated"],
                                },
                            },
                            "required": ["concept"],
                        },
                    },
                    "query": {
                        "type": "string",
                        "description": "Optional keyword to filter the listed checklist (case-insensitive substring match on concept/status).",
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