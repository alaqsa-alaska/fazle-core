"""Typed models for Ollama memory records."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryFact:
    subject_type: str
    subject_key: str
    fact_type: str
    fact_text: str
    source_ref: str = ""
    confidence: float = 0.80


@dataclass
class MemoryQuestion:
    question: str
    answer_summary: str
    source_refs: list[str] = field(default_factory=list)


@dataclass
class MemoryTask:
    task_name: str
    notes: str = ""
    source_refs: list[str] = field(default_factory=list)
