"""Background reflection: how dint "learns" between turns.

After each learner exchange, dint runs a cheap, no-tools completion that reads
the recent conversation and emits a small JSON patch describing what changed in
its model of the learner:

* **skills** – competence estimates to upsert (status + confidence + evidence).
* **knowledge** – subject concepts to record in the knowledge graph.
* **memories** – durable learner facts worth keeping.

The patch is applied directly to the database. This is deliberately separate
from the live teaching loop so reflection never slows down a reply and never
leaks into what the learner sees.
"""
from __future__ import annotations

import json
from typing import Any

from .db import get_db
from .llm import simple_completion

_REFLECT_SYSTEM = """You are the background self-model of "dint", a Socratic tutor.
You just finished an exchange with a learner. Analyze ONLY the recent exchange
and emit a JSON object describing durable updates to your model of this learner.

Return STRICT JSON with this shape (omit any section with no updates; use empty
arrays otherwise):
{
  "skills": [
    {"name": str, "domain": str, "status": "locked"|"emerging"|"demonstrated",
     "confidence": 0.0-1.0, "evidence": str}
  ],
  "knowledge": [
    {"label": str, "subject": str, "summary": str, "relates_to": [str]}
  ],
  "memories": [
    {"kind": "preference"|"fact"|"goal"|"note", "content": str}
  ]
}

Rules:
- Only record what the exchange actually evidences. No speculation, no filler.
- "skills" track the LEARNER's competence, not the topic's difficulty.
- "knowledge" records SUBJECT concepts that were taught or clarified, with a tight
  summary and links to related concepts. Use EXACT, canonical concept names —
  if a concept was already recorded under a slightly different name, reuse the
  existing name. Do NOT create near-duplicate concepts (e.g. "for loop" vs
  "for-loop" vs "for loops" — pick ONE canonical form).
- "memories" are durable learner facts (goals, prior knowledge, preferences,
  recurring mistakes) — never transient chatter. Do NOT record a memory if the
  same fact is already likely stored. Prefer updating existing knowledge over
  adding redundant memories.
- If nothing durable happened, return exactly: {}
- Output JSON only. No prose, no markdown fences."""


def _coerce_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


async def reflect(session_id: str, transcript: list[dict[str, Any]]) -> dict[str, int]:
    """Run reflection over a transcript and persist any updates.

    ``transcript`` is a list of OpenAI-format messages (the recent window).
    Returns a small summary of how many of each kind of update were applied.
    """
    counts = {"skills": 0, "knowledge": 0, "memories": 0}
    if not transcript:
        return counts

    # Compact the transcript to plain text to keep the reflection prompt small.
    lines: list[str] = []
    for msg in transcript:
        role = msg.get("role", "?")
        content = msg.get("content")
        if not content or role == "system":
            continue
        if isinstance(content, list):  # ignore tool-call fragments
            continue
        lines.append(f"{role}: {content}")
    if not lines:
        return counts

    user_prompt = "Recent exchange:\n" + "\n".join(lines[-40:])

    try:
        raw = await simple_completion(
            [
                {"role": "system", "content": _REFLECT_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
        )
    except Exception:  # noqa: BLE001 - reflection must never break teaching
        return counts

    patch = _parse_json(raw)
    if not patch:
        return counts

    db = await get_db()

    for skill in patch.get("skills") or []:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        await db.update_skill(
            name=name,
            domain=skill.get("domain"),
            status=skill.get("status"),
            confidence=_coerce_confidence(skill.get("confidence")),
            evidence=skill.get("evidence"),
        )
        counts["skills"] += 1

    for node in patch.get("knowledge") or []:
        label = str(node.get("label") or "").strip()
        if not label:
            continue
        await db.upsert_knowledge_node(
            label, node.get("subject"), node.get("summary")
        )
        for rel in node.get("relates_to") or []:
            await db.add_knowledge_edge(
                label, str(rel), "relates_to", node.get("subject")
            )
        counts["knowledge"] += 1

    for mem in patch.get("memories") or []:
        content = str(mem.get("content") or "").strip()
        if not content:
            continue
        kind = str(mem.get("kind") or "note").strip() or "note"
        # Deduplicate: skip if a very similar memory already exists.
        existing = await db.list_memory(limit=100)
        if _is_duplicate_memory(content, existing):
            continue
        await db.add_memory(kind, content)
        counts["memories"] += 1

    return counts


def _is_duplicate_memory(new_content: str, existing: list[dict[str, Any]]) -> bool:
    """Check if a memory is a near-duplicate of an existing one.

    Uses a simple normalized-substring heuristic: if the new content (lowered,
    stripped of punctuation) is contained in an existing entry or vice-versa,
    treat it as a duplicate.
    """
    import re

    def normalize(s: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]", " ", s.lower()).strip()

    norm_new = normalize(new_content)
    if len(norm_new) < 6:
        return False
    for entry in existing:
        norm_old = normalize(str(entry.get("content", "")))
        if not norm_old:
            continue
        if norm_new in norm_old or norm_old in norm_new:
            return True
    return False


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response."""
    text = text.strip()
    if not text:
        return {}
    # Strip markdown code fences if present.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}