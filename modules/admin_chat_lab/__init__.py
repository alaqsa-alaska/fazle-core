"""Admin Chat Lab orchestration helpers.

This module provides a safe, traceable response envelope for future FastAPI
routes. It does not mutate production data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatLabResponse:
    answer: str
    sources: list[str] = field(default_factory=list)
    runtime_references: list[str] = field(default_factory=list)
    memory_references: list[str] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)
    escalation_required: bool = False


def classify_discussion(question: str) -> str:
    q = question.lower()
    if any(word in q for word in ("database", "table", "sql", "schema")):
        return "database_discussion"
    if any(word in q for word in ("module", "service", "route", "api")):
        return "module_discussion"
    if any(word in q for word in ("workflow", "process", "approval")):
        return "workflow_discussion"
    if any(word in q for word in ("health", "status", "failure", "down")):
        return "operational_intelligence"
    return "system_discussion"


async def analyze_admin_question(question: str) -> ChatLabResponse:
    from modules.ai_readonly_tools import detect_tools_needed

    discussion_type = classify_discussion(question)
    tools = detect_tools_needed(question)
    return ChatLabResponse(
        answer="Admin Chat Lab analysis prepared. Route this envelope through the LLM provider chain for final wording.",
        sources=["knowledge_base", "runtime_static_inventory"],
        runtime_references=tools,
        trace={"discussion_type": discussion_type, "tools_needed": tools},
        escalation_required=False,
    )
