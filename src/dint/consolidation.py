"""Probability-triggered memory consolidation.

Every conversation turn increments a probability counter by 10%.  When the
counter reaches 100% (≥ 1.0), a full consolidation pass is triggered:

1. All long-term memories, skills, and knowledge nodes are loaded from the DB.
2. The LLM analyses every item and identifies groups that share *basically the
   same concept* — even if worded differently.
3. Merge operations are applied: duplicate knowledge nodes are renamed into a
   single canonical node (edges preserved), duplicate skills are renamed, and
   redundant memories are merged into one entry.
4. The probability counter is reset to 0.

This keeps dint's internal model compact and free of near-duplicate clutter
that would otherwise accumulate over long learning sessions.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import scope
from .db import get_db
from .llm import simple_completion

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Probability state (module-level, survives across turns within a process)
#
# Keyed per storage scope so that, in host mode, each user's consolidation
# cycle is independent — one tenant's turns can't push another tenant over the
# 100% trigger. In local mode there is a single "local" key.
# --------------------------------------------------------------------------- #
_consolidation_probability: dict[str, float] = {}
_INCREMENT: float = 0.1  # 10 % per turn


def get_consolidation_probability() -> float:
    """Return the current consolidation probability (0.0 – 1.0+) for this scope."""
    return _consolidation_probability.get(scope.scope_key(), 0.0)


def reset_consolidation_probability() -> None:
    """Reset this scope's probability counter to zero."""
    _consolidation_probability[scope.scope_key()] = 0.0


def tick_consolidation_probability() -> float:
    """Advance this scope's probability by one increment and return the new value."""
    key = scope.scope_key()
    current = _consolidation_probability.get(key, 0.0)
    new = round(current + _INCREMENT, 10)
    _consolidation_probability[key] = new
    return new


# --------------------------------------------------------------------------- #
# LLM consolidation prompt
# --------------------------------------------------------------------------- #
_CONSOLIDATE_SYSTEM = """\
You are the memory-consolidation engine of "dint", a Socratic tutor.
You will receive the FULL inventory of dint's long-term memory, skill estimates,
and knowledge-graph concepts.  Your job is to find items that represent
**basically the same concept** and merge them.

Return STRICT JSON with this shape (omit any section with no merges):
{
  "knowledge_merges": [
    {
      "canonical_label": str,
      "canonical_subject": str | null,
      "canonical_summary": str,
      "duplicates": [str]
    }
  ],
  "skill_merges": [
    {
      "canonical_name": str,
      "duplicates": [str]
    }
  ],
  "concept_merges": [
    {
      "canonical_name": str,
      "duplicates": [str]
    }
  ],
  "memory_merges": [
    {
      "merged_kind": "preference"|"fact"|"goal"|"note",
      "merged_content": str,
      "duplicate_ids": [str]
    }
  ]
}

Rules:
- Only merge items that are genuinely the SAME concept expressed differently
  (e.g. "for loop" / "for-loop" / "for loops", or "variables" / "variable
  assignment" when they clearly refer to the same thing).
- Do NOT merge items that are merely related or in the same domain.
- "canonical_label" / "canonical_name" is the single best name to keep.
- "duplicates" lists the labels/names that should be merged INTO the canonical.
- For concept_merges, the canonical_name and duplicates are concept-progress
  checklist item names (the ✓ / ○ tracker shown per session).
- For memories, "duplicate_ids" are the DB ids to delete after creating the
  merged entry.
- If nothing needs merging, return exactly: {}
- Output JSON only.  No prose, no markdown fences.
- Only output English, no other languages.
"""


def _build_inventory_prompt(
    memories: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    knowledge: list[dict[str, Any]],
    concepts: list[str] | None = None,
) -> str:
    """Render the full inventory as a compact text block for the LLM."""
    parts: list[str] = []

    if memories:
        lines = [
            f"- [id={m['id']}] ({m['kind']}) {m['content']}" for m in memories
        ]
        parts.append("=== LONG-TERM MEMORY ===\n" + "\n".join(lines))

    if skills:
        lines = [
            f"- {s['name']} (domain={s.get('domain') or 'general'}, "
            f"status={s['status']}, confidence={s['confidence']:.2f})"
            for s in skills
        ]
        parts.append("=== SKILLS ===\n" + "\n".join(lines))

    if knowledge:
        lines = [
            f"- {k['label']} (subject={k.get('subject') or 'general'}): "
            f"{k.get('summary') or 'no summary'}"
            for k in knowledge
        ]
        parts.append("=== KNOWLEDGE CONCEPTS ===\n" + "\n".join(lines))

    if concepts:
        lines = [f"- {c}" for c in concepts]
        parts.append("=== CONCEPT PROGRESS ITEMS ===\n" + "\n".join(lines))

    return "\n\n".join(parts)


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction from an LLM response."""
    text = text.strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# Main consolidation entry point
# --------------------------------------------------------------------------- #
async def consolidate() -> dict[str, int]:
    """Run a full consolidation pass over all stored learner data.

    Returns a summary dict with counts of merges applied per category.
    """
    counts = {"knowledge": 0, "skills": 0, "concepts": 0, "memories": 0}
    db = await get_db()

    # 1. Load everything ---------------------------------------------------- #
    memories = await db.list_memory(limit=500)
    skills = await db.list_skills(limit=500)
    knowledge = (await db.knowledge_subgraph(limit=500))["nodes"]
    concepts = await db.list_all_concepts()

    if not memories and not skills and not knowledge and not concepts:
        return counts

    inventory = _build_inventory_prompt(memories, skills, knowledge, concepts)
    if not inventory.strip():
        return counts

    # 2. Ask the LLM to find merges ----------------------------------------- #
    try:
        raw = await simple_completion(
            [
                {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                {"role": "user", "content": inventory},
            ],
            temperature=0.1,
        )
    except Exception:
        logger.exception("Consolidation LLM call failed")
        return counts

    patch = _parse_json(raw)
    if not patch:
        return counts

    # 3. Apply knowledge merges --------------------------------------------- #
    for merge in patch.get("knowledge_merges") or []:
        canonical = str(merge.get("canonical_label") or "").strip()
        if not canonical:
            continue
        subject = merge.get("canonical_subject")
        summary = merge.get("canonical_summary")
        for dup in merge.get("duplicates") or []:
            dup = str(dup).strip()
            if not dup or dup == canonical:
                continue
            try:
                await db.rename_knowledge_node(
                    dup, canonical, subject=subject, summary=summary
                )
                counts["knowledge"] += 1
            except Exception:
                logger.warning("Failed to merge knowledge '%s' -> '%s'", dup, canonical)

    # 4. Apply skill merges ------------------------------------------------- #
    for merge in patch.get("skill_merges") or []:
        canonical = str(merge.get("canonical_name") or "").strip()
        if not canonical:
            continue
        for dup in merge.get("duplicates") or []:
            dup = str(dup).strip()
            if not dup or dup == canonical:
                continue
            try:
                await db.rename_skill(dup, canonical)
                counts["skills"] += 1
            except Exception:
                logger.warning("Failed to merge skill '%s' -> '%s'", dup, canonical)

    # 5. Apply concept-progress merges -------------------------------------- #
    for merge in patch.get("concept_merges") or []:
        canonical = str(merge.get("canonical_name") or "").strip()
        if not canonical:
            continue
        for dup in merge.get("duplicates") or []:
            dup = str(dup).strip()
            if not dup or dup == canonical:
                continue
            try:
                affected = await db.rename_concept_progress(dup, canonical)
                if affected:
                    counts["concepts"] += 1
            except Exception:
                logger.warning(
                    "Failed to merge concept progress '%s' -> '%s'", dup, canonical
                )

    # 6. Apply memory merges ------------------------------------------------ #
    for merge in patch.get("memory_merges") or []:
        merged_content = str(merge.get("merged_content") or "").strip()
        merged_kind = str(merge.get("merged_kind") or "note").strip() or "note"
        dup_ids = merge.get("duplicate_ids") or []
        if not merged_content or not dup_ids:
            continue
        try:
            # Create the merged entry first, then delete the originals.
            await db.add_memory(merged_kind, merged_content)
            for did in dup_ids:
                await db.delete_memory(str(did))
            counts["memories"] += 1
        except Exception:
            logger.warning("Failed to merge memories %s", dup_ids)

    return counts


async def maybe_consolidate() -> dict[str, Any]:
    """Tick the probability counter and run consolidation if triggered.

    Returns a dict with ``triggered`` (bool), ``probability`` (float before
    reset), and ``merges`` (the consolidation counts, if triggered).
    """
    prob = tick_consolidation_probability()
    if prob < 1.0:
        return {"triggered": False, "probability": prob, "merges": None}

    # Triggered — run consolidation then reset.
    logger.info("Consolidation triggered (probability=%.2f)", prob)
    merges = await consolidate()
    reset_consolidation_probability()
    return {"triggered": True, "probability": prob, "merges": merges}