"""
Fazle Core — Ollama Memory DB Module (Phase 5)
==============================================
AI-owned writable memory database. ONLY this module writes to
fazle_ollama_memory. Production DB is NEVER written by AI.

Connection URL: env var OLLAMA_MEMORY_DB_URL
Database:       fazle_ollama_memory
Role:           ollama_memory_owner (full DDL/DML on memory DB only)

Public API (all async):
    remember_fact(subject_type, subject_key, fact_type, fact_text, source_ref)
    recall_facts(subject_type, subject_key) -> list[dict]
    record_question(question, answer_summary, source_refs) -> int
    get_recent_questions(limit=20) -> list[dict]
    add_task(task_name, notes, source_refs) -> int
    update_task_status(task_id, status, notes)
    list_memory_stats() -> dict
    record_kb_indexed(kb_path, kb_hash, chunk_count)
    get_kb_manifest() -> list[dict]
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import asyncpg

log = logging.getLogger("fazle.ollama_memory")

_pool: Optional[asyncpg.Pool] = None


def _get_url() -> str:
    url = os.environ.get("OLLAMA_MEMORY_DB_URL", "")
    if not url:
        prod_url = os.environ.get("DATABASE_URL", "")
        pw = os.environ.get("OLLAMA_MEMORY_OWNER_PASSWORD", "")
        if prod_url and pw:
            import re as _re
            host_part = prod_url.split("@")[1]
            host_no_db = host_part.rsplit("/", 1)[0]
            url = f"postgresql://ollama_memory_owner:{pw}@{host_no_db}/fazle_ollama_memory"
    return url


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = _get_url()
        if not url:
            raise RuntimeError("OLLAMA_MEMORY_DB_URL not configured")
        try:
            _pool = await asyncpg.create_pool(url, min_size=1, max_size=3, command_timeout=15)
        except Exception as e:
            log.warning("ollama_memory pool init failed: %s", e)
            raise
    return _pool


async def remember_fact(
    subject_type: str,
    subject_key: str,
    fact_type: str,
    fact_text: str,
    source_ref: str = "",
    confidence: float = 0.80,
) -> int:
    """
    Store or update a fact about a subject.
    Returns the fact id.
    subject_type: e.g. "employee", "contact", "module", "escort_program"
    subject_key:  e.g. employee_id, phone, module_name
    fact_type:    e.g. "summary", "status", "note", "anomaly"
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            """SELECT id FROM ai_memory_facts
               WHERE subject_type=$1 AND subject_key=$2 AND fact_type=$3
               LIMIT 1""",
            subject_type, subject_key, fact_type,
        )
        if existing:
            await conn.execute(
                """UPDATE ai_memory_facts
                   SET fact_text=$1, confidence=$2, source_ref=$3, updated_at=NOW()
                   WHERE id=$4""",
                fact_text, confidence, source_ref, existing,
            )
            return existing
        else:
            row_id = await conn.fetchval(
                """INSERT INTO ai_memory_facts
                   (subject_type, subject_key, fact_type, fact_text, confidence, source_ref)
                   VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
                subject_type, subject_key, fact_type, fact_text, confidence, source_ref,
            )
            return row_id


async def recall_facts(
    subject_type: str,
    subject_key: str,
    fact_type: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Return stored facts about a subject."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if fact_type:
            rows = await conn.fetch(
                """SELECT id, fact_type, fact_text, confidence, source_ref, updated_at
                   FROM ai_memory_facts
                   WHERE subject_type=$1 AND subject_key=$2 AND fact_type=$3
                   ORDER BY updated_at DESC LIMIT $4""",
                subject_type, subject_key, fact_type, limit,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, fact_type, fact_text, confidence, source_ref, updated_at
                   FROM ai_memory_facts
                   WHERE subject_type=$1 AND subject_key=$2
                   ORDER BY updated_at DESC LIMIT $3""",
                subject_type, subject_key, limit,
            )
        return [dict(r) for r in rows]


async def record_question(
    question: str,
    answer_summary: str,
    source_refs: list[str] | None = None,
) -> int:
    """Save a Q&A pair to the AI question history. Returns record id."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        normalized = question.strip().lower()[:500]
        row_id = await conn.fetchval(
            """INSERT INTO ai_memory_questions
               (question, normalized_question, answer_summary, source_refs)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            question[:1000], normalized,
            answer_summary[:2000],
            json.dumps(source_refs or []),
        )
        return row_id


async def get_recent_questions(limit: int = 20) -> list[dict]:
    """Return recent Q&A history."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, question, answer_summary, source_refs, asked_at
               FROM ai_memory_questions
               ORDER BY asked_at DESC LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]


async def add_task(
    task_name: str,
    notes: str = "",
    source_refs: list[str] | None = None,
) -> int:
    """Add a task to AI memory task list. Returns task id."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row_id = await conn.fetchval(
            """INSERT INTO ai_memory_tasks (task_name, notes, source_refs)
               VALUES ($1, $2, $3) RETURNING id""",
            task_name[:500], notes[:2000], json.dumps(source_refs or []),
        )
        return row_id


async def update_task_status(
    task_id: int,
    status: str,
    notes: Optional[str] = None,
) -> None:
    """Update task status: open | in_progress | done | cancelled."""
    valid = {"open", "in_progress", "done", "cancelled"}
    if status not in valid:
        raise ValueError(f"status must be one of {valid}")
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if notes is not None:
            await conn.execute(
                """UPDATE ai_memory_tasks
                   SET status=$1, notes=$2, updated_at=NOW() WHERE id=$3""",
                status, notes, task_id,
            )
        else:
            await conn.execute(
                "UPDATE ai_memory_tasks SET status=$1, updated_at=NOW() WHERE id=$2",
                status, task_id,
            )


async def record_kb_indexed(
    kb_path: str,
    kb_hash: str,
    chunk_count: int,
) -> None:
    """Record that a KB article has been indexed into RAG."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO ai_kb_embeddings_manifest (kb_path, kb_hash, chunk_count, status)
               VALUES ($1, $2, $3, 'indexed')
               ON CONFLICT (kb_path) DO UPDATE
               SET kb_hash=$2, chunk_count=$3, indexed_at=NOW(), status='indexed'""",
            kb_path, kb_hash, chunk_count,
        )


async def get_kb_manifest(limit: int = 200) -> list[dict]:
    """Return the list of KB articles currently indexed."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT kb_path, kb_hash, indexed_at, chunk_count, status
               FROM ai_kb_embeddings_manifest
               ORDER BY indexed_at DESC LIMIT $1""",
            limit,
        )
        return [dict(r) for r in rows]


async def list_memory_stats() -> dict[str, Any]:
    """Return counts and summary of all memory tables."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        fact_count = await conn.fetchval("SELECT COUNT(*) FROM ai_memory_facts")
        q_count = await conn.fetchval("SELECT COUNT(*) FROM ai_memory_questions")
        task_open = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_memory_tasks WHERE status='open'"
        )
        task_done = await conn.fetchval(
            "SELECT COUNT(*) FROM ai_memory_tasks WHERE status='done'"
        )
        kb_indexed = await conn.fetchval("SELECT COUNT(*) FROM ai_kb_embeddings_manifest")
        last_q = await conn.fetchval(
            "SELECT asked_at FROM ai_memory_questions ORDER BY asked_at DESC LIMIT 1"
        )
        return {
            "facts": int(fact_count or 0),
            "questions_total": int(q_count or 0),
            "tasks_open": int(task_open or 0),
            "tasks_done": int(task_done or 0),
            "kb_articles_indexed": int(kb_indexed or 0),
            "last_question_at": last_q.isoformat() if last_q else None,
        }


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
