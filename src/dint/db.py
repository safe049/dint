"""SQLite persistence layer for dint.

A single database file stores four logical subsystems:

* **knowledge graph** – concepts (nodes) and typed relations (edges) that dint
  builds up about the subjects it teaches.
* **long-term memory** – free-form, durable facts/observations about the
  learner, summarised by dint over time.
* **skill graph** – an estimate of the learner's competence per skill, with a
  0..1 confidence score and a status (locked / emerging / demonstrated).
* **subject history** – the per-question conversation log plus a per-question
  "concept progress" tracker (the ✓ / ○ checklist).

All access is async via :mod:`aiosqlite`.
"""
from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiosqlite

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    subject     TEXT,
    summary     TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(label, subject)
);

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id          TEXT PRIMARY KEY,
    src_id      TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    dst_id      TEXT NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE(src_id, dst_id, relation)
);

CREATE TABLE IF NOT EXISTS memory (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- 'preference' | 'fact' | 'goal' | 'note'
    content     TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE(kind, content)
);

CREATE TABLE IF NOT EXISTS skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    domain      TEXT,
    status      TEXT NOT NULL DEFAULT 'locked',  -- locked | emerging | demonstrated
    confidence  REAL NOT NULL DEFAULT 0.0,       -- 0..1
    evidence    TEXT NOT NULL DEFAULT '[]',       -- JSON list of short notes
    updated_at  REAL NOT NULL,
    ease_factor REAL NOT NULL DEFAULT 2.5,       -- SM-2 ease factor
    review_count INTEGER NOT NULL DEFAULT 0,
    next_review_at REAL                          -- epoch seconds; NULL = not scheduled
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    subject     TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool' | 'system'
    content     TEXT NOT NULL,
    tool_name   TEXT,
    meta        TEXT,                    -- JSON blob for extra info
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS concept_progress (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    concept     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'locked',  -- locked | next | demonstrated
    updated_at  REAL NOT NULL,
    UNIQUE(session_id, concept)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_edges_src ON knowledge_edges(src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON knowledge_edges(dst_id);
"""


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class Database:
    """Thin async wrapper around the SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self._conn() as db:
            await db.executescript(_SCHEMA)
            # Migrate: add spaced-repetition columns if missing (idempotent).
            for col, ddl in (
                ("ease_factor", "ALTER TABLE skills ADD COLUMN ease_factor REAL NOT NULL DEFAULT 2.5"),
                ("review_count", "ALTER TABLE skills ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0"),
                ("next_review_at", "ALTER TABLE skills ADD COLUMN next_review_at REAL"),
            ):
                try:
                    await db.execute(ddl)
                except Exception:
                    pass  # column already exists
            await db.commit()

    @asynccontextmanager
    async def _conn(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
        finally:
            await db.close()

    # ------------------------------------------------------------------ #
    # Knowledge graph
    # ------------------------------------------------------------------ #
    async def upsert_knowledge_node(
        self, label: str, subject: Optional[str], summary: Optional[str]
    ) -> str:
        label = label.strip()
        subject = (subject or "").strip() or None
        async with self._conn() as db:
            row = await db.execute_fetchall(
                "SELECT id FROM knowledge_nodes WHERE label = ? AND subject IS ?",
                (label, subject),
            )
            if row:
                node_id = row[0]["id"]
                await db.execute(
                    "UPDATE knowledge_nodes SET summary = COALESCE(?, summary), "
                    "updated_at = ? WHERE id = ?",
                    (summary, _now(), node_id),
                )
            else:
                node_id = _new_id()
                await db.execute(
                    "INSERT INTO knowledge_nodes (id, label, subject, summary, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (node_id, label, subject, summary, _now(), _now()),
                )
            await db.commit()
            return node_id

    async def add_knowledge_edge(
        self, src_label: str, dst_label: str, relation: str, subject: Optional[str] = None
    ) -> None:
        src_id = await self.upsert_knowledge_node(src_label, subject, None)
        dst_id = await self.upsert_knowledge_node(dst_label, subject, None)
        async with self._conn() as db:
            await db.execute(
                "INSERT OR IGNORE INTO knowledge_edges (id, src_id, dst_id, relation, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (_new_id(), src_id, dst_id, relation.strip(), _now()),
            )
            await db.commit()

    async def delete_knowledge_node(self, label: str) -> bool:
        """Delete a knowledge node and all its edges. Returns True if deleted."""
        label = label.strip()
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM knowledge_nodes WHERE label = ?", (label,)
            )
            if not rows:
                return False
            node_id = rows[0]["id"]
            await db.execute(
                "DELETE FROM knowledge_edges WHERE src_id = ? OR dst_id = ?",
                (node_id, node_id),
            )
            await db.execute("DELETE FROM knowledge_nodes WHERE id = ?", (node_id,))
            await db.commit()
            return True

    async def rename_knowledge_node(
        self,
        old_label: str,
        new_label: str,
        subject: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> bool:
        """Rename a knowledge node, merging into an existing node if the target
        label already exists. Edges are preserved. Returns True on success."""
        old_label = old_label.strip()
        new_label = new_label.strip()
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM knowledge_nodes WHERE label = ?", (old_label,)
            )
            if not rows:
                return False
            node_id = rows[0]["id"]
            existing = await db.execute_fetchall(
                "SELECT id FROM knowledge_nodes WHERE label = ?", (new_label,)
            )
            if existing and existing[0]["id"] != node_id:
                # Merge: re-point edges to the existing target, then drop old node.
                target_id = existing[0]["id"]
                await db.execute(
                    "UPDATE OR IGNORE knowledge_edges SET src_id = ? WHERE src_id = ?",
                    (target_id, node_id),
                )
                await db.execute(
                    "UPDATE OR IGNORE knowledge_edges SET dst_id = ? WHERE dst_id = ?",
                    (target_id, node_id),
                )
                await db.execute(
                    "DELETE FROM knowledge_edges WHERE src_id = ? OR dst_id = ?",
                    (node_id, node_id),
                )
                await db.execute("DELETE FROM knowledge_nodes WHERE id = ?", (node_id,))
                if summary:
                    await db.execute(
                        "UPDATE knowledge_nodes SET summary = ?, updated_at = ? WHERE id = ?",
                        (summary, _now(), target_id),
                    )
                if subject:
                    await db.execute(
                        "UPDATE knowledge_nodes SET subject = ?, updated_at = ? WHERE id = ?",
                        (subject, _now(), target_id),
                    )
            else:
                updates = ["label = ?", "updated_at = ?"]
                params: list[Any] = [new_label, _now()]
                if subject is not None:
                    updates.append("subject = ?")
                    params.append(subject)
                if summary is not None:
                    updates.append("summary = ?")
                    params.append(summary)
                params.append(node_id)
                await db.execute(
                    f"UPDATE knowledge_nodes SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
            await db.commit()
            return True

    async def delete_knowledge_edge(
        self, src_label: str, dst_label: str, relation: str = "relates_to"
    ) -> bool:
        """Delete a specific edge between two nodes. Returns True if deleted."""
        src_id = await self._find_node_id(src_label)
        dst_id = await self._find_node_id(dst_label)
        if src_id is None or dst_id is None:
            return False
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM knowledge_edges WHERE src_id = ? AND dst_id = ? AND relation = ?",
                (src_id, dst_id, relation.strip()),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    async def _find_node_id(self, label: str) -> Optional[str]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM knowledge_nodes WHERE label = ?", (label.strip(),)
            )
            return rows[0]["id"] if rows else None

    async def search_knowledge(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        like = f"%{query.strip()}%"
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id, label, subject, summary FROM knowledge_nodes "
                "WHERE label LIKE ? OR summary LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, like, limit),
            )
            return [dict(r) for r in rows]

    async def knowledge_subgraph(self, limit: int = 60) -> dict[str, Any]:
        async with self._conn() as db:
            nodes = await db.execute_fetchall(
                "SELECT id, label, subject, summary FROM knowledge_nodes "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            ids = {n["id"] for n in nodes}
            edges = await db.execute_fetchall(
                "SELECT src_id, dst_id, relation FROM knowledge_edges"
            )
            edges = [
                dict(e)
                for e in edges
                if e["src_id"] in ids and e["dst_id"] in ids
            ]
            return {"nodes": [dict(n) for n in nodes], "edges": edges}

    # ------------------------------------------------------------------ #
    # Long-term memory
    # ------------------------------------------------------------------ #
    async def add_memory(self, kind: str, content: str) -> str:
        content = content.strip()
        async with self._conn() as db:
            row = await db.execute_fetchall(
                "SELECT id FROM memory WHERE kind = ? AND content = ?",
                (kind, content),
            )
            if row:
                mid = row[0]["id"]
                await db.execute(
                    "UPDATE memory SET updated_at = ? WHERE id = ?",
                    (_now(), mid),
                )
            else:
                mid = _new_id()
                await db.execute(
                    "INSERT INTO memory (id, kind, content, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mid, kind, content, _now(), _now()),
                )
            await db.commit()
        return mid

    async def list_memory(self, limit: int = 40) -> list[dict[str, Any]]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id, kind, content, created_at FROM memory "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows]

    async def update_memory(self, memory_id: str, kind: str | None = None, content: str | None = None) -> None:
        """Update an existing memory entry's kind and/or content."""
        async with self._conn() as db:
            if kind is not None:
                await db.execute("UPDATE memory SET kind = ?, updated_at = ? WHERE id = ?", (kind, _now(), memory_id))
            if content is not None:
                await db.execute("UPDATE memory SET content = ?, updated_at = ? WHERE id = ?", (content, _now(), memory_id))
            await db.commit()

    async def delete_memory(self, memory_id: str) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM memory WHERE id = ?", (memory_id,))
            await db.commit()

    # ------------------------------------------------------------------ #
    # Skill graph
    # ------------------------------------------------------------------ #
    async def update_skill(
        self,
        name: str,
        domain: Optional[str],
        status: Optional[str],
        confidence: Optional[float],
        evidence: Optional[str],
    ) -> None:
        name = name.strip()
        async with self._conn() as db:
            row = await db.execute_fetchall(
                "SELECT id, evidence, confidence, status FROM skills WHERE name = ?",
                (name,),
            )
            if row:
                rec = row[0]
                ev = json.loads(rec["evidence"] or "[]")
                if evidence:
                    ev.append(evidence)
                    ev = ev[-8:]  # keep last 8 notes
                new_status = status or rec["status"]
                new_conf = confidence if confidence is not None else rec["confidence"]
                await db.execute(
                    "UPDATE skills SET domain = COALESCE(?, domain), status = ?, "
                    "confidence = ?, evidence = ?, updated_at = ? WHERE id = ?",
                    (domain, new_status, new_conf, json.dumps(ev), _now(), rec["id"]),
                )
            else:
                ev = [evidence] if evidence else []
                await db.execute(
                    "INSERT INTO skills (id, name, domain, status, confidence, "
                    "evidence, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        _new_id(),
                        name,
                        domain,
                        status or "emerging",
                        confidence if confidence is not None else 0.3,
                        json.dumps(ev),
                        _now(),
                    ),
                )
            await db.commit()

    async def delete_skill(self, name: str) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM skills WHERE name = ?", (name.strip(),))
            await db.commit()

    async def rename_skill(self, old_name: str, new_name: str) -> bool:
        """Rename a skill, preserving all its data. Returns True on success."""
        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name or old_name == new_name:
            return False
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM skills WHERE name = ?", (old_name,)
            )
            if not rows:
                return False
            await db.execute(
                "UPDATE skills SET name = ?, updated_at = ? WHERE id = ?",
                (new_name, _now(), rows[0]["id"]),
            )
            await db.commit()
            return True

    async def list_skills(self, limit: int = 60) -> list[dict[str, Any]]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT name, domain, status, confidence, evidence, updated_at "
                "FROM skills ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            out = []
            for r in rows:
                d = dict(r)
                d["evidence"] = json.loads(d["evidence"] or "[]")
                out.append(d)
            return out

    # ------------------------------------------------------------------ #
    # Sessions / messages (subject history)
    # ------------------------------------------------------------------ #
    async def create_session(self, title: Optional[str], subject: Optional[str]) -> str:
        sid = _new_id()
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO sessions (id, title, subject, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, title, subject, _now(), _now()),
            )
            await db.commit()
        return sid

    async def list_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id, title, subject, created_at, updated_at FROM sessions "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in rows]

    async def touch_session(self, session_id: str) -> None:
        async with self._conn() as db:
            await db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?", (_now(), session_id)
            )
            await db.commit()

    async def delete_session(self, session_id: str) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> str:
        mid = _new_id()
        async with self._conn() as db:
            await db.execute(
                "INSERT INTO messages (id, session_id, role, content, tool_name, "
                "meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mid, session_id, role, content, tool_name,
                 json.dumps(meta) if meta else None, _now()),
            )
            await db.commit()
        await self.touch_session(session_id)
        return mid

    async def delete_last_assistant_message(self, session_id: str) -> bool:
        """Delete the most recent assistant message in a session.

        Used by the "retry" flow so a regenerated reply can replace the previous
        one. Returns True if a row was removed.
        """
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id FROM messages WHERE session_id = ? AND role = 'assistant' "
                "ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            )
            if not rows:
                return False
            await db.execute("DELETE FROM messages WHERE id = ?", (rows[0]["id"],))
            await db.commit()
            return True

    async def list_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT id, role, content, tool_name, meta, created_at FROM messages "
                "WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
                (session_id, limit),
            )
            out = []
            for r in rows:
                d = dict(r)
                d["meta"] = json.loads(d["meta"]) if d["meta"] else None
                out.append(d)
            return out

    # ------------------------------------------------------------------ #
    # Concept progress (per-question ✓ / ○ tracker)
    # ------------------------------------------------------------------ #
    async def set_concept_progress(
        self, session_id: str, concept: str, status: str
    ) -> None:
        async with self._conn() as db:
            row = await db.execute_fetchall(
                "SELECT id FROM concept_progress WHERE session_id = ? AND concept = ?",
                (session_id, concept),
            )
            if row:
                await db.execute(
                    "UPDATE concept_progress SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _now(), row[0]["id"]),
                )
            else:
                await db.execute(
                    "INSERT INTO concept_progress (id, session_id, concept, status, "
                    "updated_at) VALUES (?, ?, ?, ?, ?)",
                    (_new_id(), session_id, concept, status, _now()),
                )
            await db.commit()

    async def list_concept_progress(self, session_id: str) -> list[dict[str, Any]]:
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT concept, status FROM concept_progress WHERE session_id = ? "
                "ORDER BY rowid ASC",
                (session_id,),
            )
            return [dict(r) for r in rows]

    async def delete_concept_progress(self, session_id: str, concept: str) -> bool:
        """Delete a single concept-progress row. Returns True if a row was removed."""
        async with self._conn() as db:
            cur = await db.execute(
                "DELETE FROM concept_progress WHERE session_id = ? AND concept = ?",
                (session_id, concept),
            )
            await db.commit()
            return (cur.rowcount or 0) > 0

    # ------------------------------------------------------------------ #
    # Spaced repetition (SM-2 lite)
    # ------------------------------------------------------------------ #
    async def skills_due_for_review(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return skills whose next_review_at is in the past (or NULL and demonstrated)."""
        now = _now()
        async with self._conn() as db:
            rows = await db.execute_fetchall(
                "SELECT name, domain, status, confidence, ease_factor, review_count, "
                "next_review_at FROM skills "
                "WHERE status != 'locked' AND (next_review_at IS NULL OR next_review_at <= ?) "
                "ORDER BY next_review_at ASC LIMIT ?",
                (now, limit),
            )
            return [dict(r) for r in rows]

    async def record_review(self, name: str, quality: int) -> None:
        """Record a review outcome (quality 0-5, SM-2 scale) and schedule the next one.

        quality >= 3 → correct recall; < 3 → lapse, reset interval.
        """
        quality = max(0, min(5, quality))
        now = _now()
        async with self._conn() as db:
            row = await db.execute_fetchall(
                "SELECT id, ease_factor, review_count FROM skills WHERE name = ?",
                (name.strip(),),
            )
            if not row:
                return
            rec = row[0]
            ef = rec["ease_factor"]
            n = rec["review_count"]

            # SM-2 ease factor update
            ef = ef + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
            ef = max(1.3, ef)

            if quality < 3:
                n = 0
                interval_days = 1
            else:
                n += 1
                if n == 1:
                    interval_days = 1
                elif n == 2:
                    interval_days = 6
                else:
                    interval_days = round(6 * (ef ** (n - 2)))

            next_at = now + interval_days * 86400
            await db.execute(
                "UPDATE skills SET ease_factor = ?, review_count = ?, "
                "next_review_at = ?, updated_at = ? WHERE id = ?",
                (ef, n, next_at, now, rec["id"]),
            )
            await db.commit()

    # ------------------------------------------------------------------ #
    # Bulk reset
    # ------------------------------------------------------------------ #
    async def clear_all_learner_data(self) -> dict[str, int]:
        """Delete ALL learner data: sessions, messages, concept progress,
        knowledge graph (nodes + edges), skills and memory.

        Settings/config are stored separately and are NOT touched.
        Returns a mapping of table name -> rows deleted.
        """
        # Order matters for FK safety: children before parents.
        tables = [
            "concept_progress",
            "messages",
            "sessions",
            "knowledge_edges",
            "knowledge_nodes",
            "skills",
            "memory",
        ]
        counts: dict[str, int] = {}
        async with self._conn() as db:
            for table in tables:
                cur = await db.execute(f"DELETE FROM {table}")
                counts[table] = cur.rowcount if cur.rowcount is not None else 0
            await db.commit()
        return counts


_db: Optional[Database] = None


async def get_db() -> Database:
    """Return the shared :class:`Database` instance, initialising it lazily."""
    global _db
    if _db is None:
        _db = Database(get_settings().db_path)
        await _db.init()
    return _db