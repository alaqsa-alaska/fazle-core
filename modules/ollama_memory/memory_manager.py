"""Manager facade for Ollama memory operations."""
from __future__ import annotations

from . import add_task, list_memory_stats, recall_facts, record_question, remember_fact, update_task_status
from .memory_models import MemoryFact, MemoryQuestion, MemoryTask


async def save_fact(fact: MemoryFact) -> int:
    return await remember_fact(
        fact.subject_type,
        fact.subject_key,
        fact.fact_type,
        fact.fact_text,
        fact.source_ref,
        fact.confidence,
    )


async def save_question(question: MemoryQuestion) -> int:
    return await record_question(question.question, question.answer_summary, question.source_refs)


async def save_task(task: MemoryTask) -> int:
    return await add_task(task.task_name, task.notes, task.source_refs)


__all__ = [
    "save_fact",
    "save_question",
    "save_task",
    "recall_facts",
    "update_task_status",
    "list_memory_stats",
]
