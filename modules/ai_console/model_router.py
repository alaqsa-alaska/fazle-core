"""Phase 4 FG3 model router for deterministic provider/model selection.

This module is pure decision logic and does not mutate runtime state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import logging
from typing import Any

from app.config import get_settings


log = logging.getLogger("fazle.ai_console.model_router")


class TaskClass(str, Enum):
    """Normalized task categories used for model routing decisions."""

    RUNTIME = "runtime"
    BUSINESS = "business"
    MEMORY = "memory"
    GENERAL = "general"
    DEEP_REASONING = "deep_reasoning"


@dataclass(frozen=True)
class RoutingPolicy:
    """Static policy record for a task class."""

    task_class: TaskClass
    model: str
    provider_chain: list[str]
    reason: str


@dataclass(frozen=True)
class RouterDecision:
    """Final routing decision produced by decide_model()."""

    task_class: TaskClass
    selected_model: str
    provider_chain: list[str]
    reason: str
    requested_model: str | None


DEFAULT_PROVIDER_CHAIN = ["ollama", "groq", "github_models"]


MODEL_ROUTING_POLICY: dict[TaskClass, RoutingPolicy] = {
    TaskClass.RUNTIME: RoutingPolicy(
        task_class=TaskClass.RUNTIME,
        model="hermes3:3b",
        provider_chain=DEFAULT_PROVIDER_CHAIN,
        reason="runtime_tasks_prioritize_fast_local_inference",
    ),
    TaskClass.BUSINESS: RoutingPolicy(
        task_class=TaskClass.BUSINESS,
        model="hermes3:3b",
        provider_chain=DEFAULT_PROVIDER_CHAIN,
        reason="business_tasks_need_balanced_context_reasoning",
    ),
    TaskClass.MEMORY: RoutingPolicy(
        task_class=TaskClass.MEMORY,
        model="hermes3:3b",
        provider_chain=DEFAULT_PROVIDER_CHAIN,
        reason="memory_tasks_are_lightweight_lookup_or_summarization",
    ),
    TaskClass.GENERAL: RoutingPolicy(
        task_class=TaskClass.GENERAL,
        model="hermes3:3b",
        provider_chain=DEFAULT_PROVIDER_CHAIN,
        reason="general_default_local_preference",
    ),
    TaskClass.DEEP_REASONING: RoutingPolicy(
        task_class=TaskClass.DEEP_REASONING,
        model="hermes3:3b",
        provider_chain=DEFAULT_PROVIDER_CHAIN,
        reason="deep_reasoning_prefers_larger_local_model",
    ),
}


def _configured_tier_model(task_class: TaskClass, fallback_model: str) -> str:
    settings = get_settings()
    tier_map = {
        TaskClass.RUNTIME: settings.ollama_runtime_model,
        TaskClass.BUSINESS: settings.ollama_business_model,
        TaskClass.MEMORY: settings.ollama_memory_model,
        TaskClass.GENERAL: settings.ollama_general_model,
        TaskClass.DEEP_REASONING: settings.ollama_deep_reasoning_model,
    }
    configured = (tier_map.get(task_class) or "").strip()
    return configured or fallback_model


def _normalize_task_class(task_class: str | TaskClass | None) -> TaskClass:
    if isinstance(task_class, TaskClass):
        return task_class
    if not task_class:
        return TaskClass.GENERAL
    try:
        return TaskClass(str(task_class).strip().lower())
    except ValueError:
        return TaskClass.GENERAL


def decide_model(
    *,
    task_class: str | TaskClass | None,
    requested_model: str | None = None,
) -> RouterDecision:
    """Select model/provider chain for a task class.

    A requested model is honored only when non-empty, while policy provider order
    remains unchanged for failover governance.
    """

    normalized_class = _normalize_task_class(task_class)
    policy = MODEL_ROUTING_POLICY.get(normalized_class, MODEL_ROUTING_POLICY[TaskClass.GENERAL])

    requested = (requested_model or "").strip() or None
    if requested:
        return RouterDecision(
            task_class=normalized_class,
            selected_model=requested,
            provider_chain=list(policy.provider_chain),
            reason="requested_model_override",
            requested_model=requested,
        )

    return RouterDecision(
        task_class=normalized_class,
        selected_model=_configured_tier_model(normalized_class, policy.model),
        provider_chain=list(policy.provider_chain),
        reason=policy.reason,
        requested_model=None,
    )


def log_decision(
    *,
    decision: RouterDecision,
    request_id: str | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Emit and return a structured routing-decision log payload."""

    payload = {
        "event": "model_router_decision",
        "request_id": request_id,
        **asdict(decision),
    }
    (logger or log).info("[model_router] %s", payload)
    return payload
