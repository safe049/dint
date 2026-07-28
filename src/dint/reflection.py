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
You just finished an exchange with a learner. The user message gives you your
"Current model" of this learner (before the exchange) and the "Recent exchange".
Reconcile the two: confirm what still holds, and CORRECT what the new behavior
contradicts. Emit a JSON object describing durable updates to your model.

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
  ],
  "memory_corrections": [
    {"find": str, "kind": "preference"|"fact"|"goal"|"note", "content": str}
  ],
  "concept_progress": [
    {"concept": str, "status": "locked"|"next"|"demonstrated"}
  ]
}

BEHAVIOR IS GROUND TRUTH — VERIFY, DON'T TRUST:
- A learner SAYING "I understand", "yeah I got it", "I know this" is a CLAIM,
  not evidence. Treat it as a hypothesis to check against what they actually DO.
- Real evidence of understanding is what they DEMONSTRATE: correctly tracing an
  example, explaining it back in their own words, predicting an outcome, fixing
  their own mistake, applying it to a new case.
- Signs they do NOT understand (even if they claim to): vague or wrong answers,
  asking the same thing again, copying your words without meaning, deflection,
  contradicting themselves, needing you to re-explain.
- Judge understanding DYNAMICALLY from the latest behavior, not from past notes.

CORRECTING STALE BELIEFS (the important part):
- The user message gives you the "Current model" (what you believed before this
  exchange). If it says the learner understands/demonstrated something — a skill
  at "demonstrated"/high confidence, a concept marked "demonstrated", or a memory
  like "learner understands X" — but the recent exchange shows they actually
  struggle with it, you MUST downgrade it:
    * skills: emit a lower status ("emerging"/"locked") and lower confidence, with
      evidence describing the observed struggle.
    * concept_progress: emit the concept as "next" or "locked", not "demonstrated".
    * memory_corrections: find the now-wrong memory (via "find") and replace its
      content with the corrected belief (e.g. find "learner understands sockets",
      content "learner does NOT understand sockets well — struggled to explain").
- Progress can go BACKWARDS: [understands X] -> [does not understand X well].
  Never let a stale positive belief survive just because it was recorded earlier.

OVERCLAIMING / CREDIBILITY:
- When the learner claims to understand but their behavior shows they do not,
  record a "preference" memory so future-you stays appropriately skeptical. Keep
  it factual and non-judgmental, with a soft tally, e.g. "Learner overclaimed
  understanding of sockets (said they understood but could not demonstrate).
  Verify self-reported mastery with a question before trusting. (overclaim #1)".
- If it recurs, update that memory via memory_corrections (raise the tally /
  generalize) rather than adding a near-duplicate.

Rules:
- Only record what the exchange actually evidences. No speculation, no filler.
- "skills" track the LEARNER's competence, not the topic's difficulty.
  Use consistent skill names: "binary search" not "binary_search" or
  "Binary Search". Lowercase, space-separated, no underscores.
- "knowledge" records SUBJECT concepts that were taught or clarified, with a tight
  summary and links to related concepts. Use EXACT, canonical concept names —
  if a concept was already recorded under a slightly different name, reuse the
  existing name. Do NOT create near-duplicate concepts (e.g. "for loop" vs
  "for-loop" vs "for loops" — pick ONE canonical form). Lowercase, space-separated.
- "memories" are durable learner facts (goals, prior knowledge, preferences,
  recurring mistakes) — never transient chatter. Do NOT add a memory if the same
  fact is already stored; use "memory_corrections" to fix an existing one instead.
- "memory_corrections" repair a belief the new behavior contradicts. "find" is the
  old memory text to locate (fuzzy); "content" is the corrected replacement. Use
  this to turn an outdated "understands X" into "does not understand X well".
- "concept_progress" tracks the per-question whiteboard checklist. Mark a concept
  "demonstrated" ONLY when the learner genuinely demonstrates understanding —
  a mere claim is NOT enough; mark it "next" until verified. If they previously
  showed it but now struggle, downgrade it back to "next"/"locked". Use lowercase,
  space-separated concept names consistent with the knowledge graph labels.
- If nothing durable happened, return exactly: {}
- Output JSON only. No prose, no markdown fences.
- Only output English, no other languages. """


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

    db = await get_db()

    # Snapshot the current model so reflection can detect and correct beliefs
    # that the new behavior contradicts (e.g. "learner understands sockets" when
    # they clearly do not). Understanding is judged dynamically, not frozen.
    snapshot_parts: list[str] = []
    cur_mem = await db.list_memory(limit=30)
    if cur_mem:
        snapshot_parts.append(
            "Memories:\n"
            + "\n".join(f"- [{m['kind']}] {m['content']}" for m in cur_mem)
        )
    cur_skills = await db.list_skills(limit=40)
    if cur_skills:
        snapshot_parts.append(
            "Skills:\n"
            + "\n".join(
                f"- {s['name']}: {s['status']} (confidence {s['confidence']:.2f})"
                for s in cur_skills
            )
        )
    cur_cp = await db.list_concept_progress(session_id)
    if cur_cp:
        snapshot_parts.append(
            "Concept checklist:\n"
            + "\n".join(f"- {c['concept']}: {c['status']}" for c in cur_cp)
        )
    current_model = (
        "\n".join(snapshot_parts) if snapshot_parts else "(nothing recorded yet)"
    )

    user_prompt = (
        "Current model of this learner (BEFORE this exchange):\n"
        + current_model
        + "\n\nRecent exchange:\n"
        + "\n".join(lines[-40:])
    )

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

    # --- Skills: merge into existing similar skill if found ---------------- #
    existing_skills = await db.list_skills(limit=200)
    for skill in patch.get("skills") or []:
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        match = _find_similar(name, existing_skills, "name")
        effective_name = match["name"] if match else name
        await db.update_skill(
            name=effective_name,
            domain=skill.get("domain"),
            status=skill.get("status"),
            confidence=_coerce_confidence(skill.get("confidence")),
            evidence=skill.get("evidence"),
        )
        counts["skills"] += 1

    # --- Knowledge: merge into existing similar concept if found ----------- #
    existing_knowledge = (await db.knowledge_subgraph(limit=200))["nodes"]
    for node in patch.get("knowledge") or []:
        label = str(node.get("label") or "").strip()
        if not label:
            continue
        match = _find_similar(label, existing_knowledge, "label")
        effective_label = match["label"] if match else label
        await db.upsert_knowledge_node(
            effective_label, node.get("subject"), node.get("summary")
        )
        for rel in node.get("relates_to") or []:
            await db.add_knowledge_edge(
                effective_label, str(rel), "relates_to", node.get("subject")
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

    # --- Memory corrections: repair stale beliefs the behavior contradicts - #
    # Turns an outdated "learner understands X" into "learner does not
    # understand X well" in place, so contradicting memories don't pile up.
    for corr in patch.get("memory_corrections") or []:
        content = str(corr.get("content") or "").strip()
        if not content:
            continue
        find = str(corr.get("find") or "").strip()
        kind = str(corr.get("kind") or "note").strip() or "note"
        existing = await db.list_memory(limit=100)
        match = _find_similar(find or content, existing, "content")
        if match:
            await db.update_memory(match["id"], kind=kind, content=content)
        elif not _is_duplicate_memory(content, existing):
            await db.add_memory(kind, content)
        counts["memory_corrections"] = counts.get("memory_corrections", 0) + 1

    # --- Concept progress: update the per-session whiteboard checklist ----- #
    for cp in patch.get("concept_progress") or []:
        concept = str(cp.get("concept") or "").strip()
        if not concept:
            continue
        status = str(cp.get("status") or "demonstrated").strip()
        if status not in ("locked", "next", "demonstrated"):
            status = "demonstrated"
        await db.set_concept_progress(session_id, concept, status)
        counts["concept_progress"] = counts.get("concept_progress", 0) + 1

    return counts


# --------------------------------------------------------------------------- #
# Probabilistic similarity helpers
# --------------------------------------------------------------------------- #
# Near-duplicate detection uses token-level Jaccard similarity over normalised
# text.  A pair is considered a duplicate when the score meets or exceeds the
# threshold below.  This replaces the old substring heuristic which missed
# paraphrases and over-matched on short strings.
_SIMILARITY_THRESHOLD = 0.55

import re as _re


def _stem(token: str) -> str:
    """Very lightweight English stemmer: strip common plural suffixes."""
    if token.endswith("es") and len(token) > 4:
        return token[:-2]
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _tokenize(text: str) -> set[str]:
    """Lowercase, strip punctuation, split, and lightly stem tokens."""
    raw = _re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text.lower()).split()
    return set(_stem(t) for t in raw)


def _jaccard(a: str, b: str) -> float:
    """Token-level Jaccard similarity between two strings (0.0 – 1.0)."""
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _find_similar(
    candidate: str,
    existing_items: list[dict[str, Any]],
    key: str,
    threshold: float = _SIMILARITY_THRESHOLD,
) -> dict[str, Any] | None:
    """Return the first existing item whose *key* field is similar enough to
    *candidate*, or ``None`` if nothing exceeds the threshold."""
    for item in existing_items:
        if _jaccard(candidate, str(item.get(key, ""))) >= threshold:
            return item
    return None


def _is_duplicate_memory(new_content: str, existing: list[dict[str, Any]]) -> bool:
    """Check if a memory is a near-duplicate of an existing one."""
    return _find_similar(new_content, existing, "content") is not None


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