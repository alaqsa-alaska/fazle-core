"""Provider registry and optional provider response adapter."""

from __future__ import annotations

import logging
from typing import Any

from app import llm as ai
from app.config import get_settings
from modules.ai_console.adapters import ProviderAdapter
from modules.ai_console.envelopes import EvidenceEnvelope
from modules.ai_console.registry import RegistryLoader, build_registry_loader

log = logging.getLogger("fazle.ai_console.provider_registry")


def _purpose_for_model(model_name: str) -> tuple[str, str]:
    lowered = model_name.lower()
    if "phi4-mini" in lowered or "phi4_mini" in lowered:
        return "primary_local_operator", "Fast primary local model — best for speed"
    if "14b" in lowered:
        return "deep_reasoning", "Deep reasoning and heavier analysis"
    if "8b" in lowered:
        return "balanced_local", "Balanced local model for complex admin queries"
    if "3b" in lowered or "mini" in lowered:
        return "fast_fallback", "Fast lightweight fallback"
    return "general", "General purpose assistant model"


_MODEL_ORDER = ["phi4-mini", "phi4-mini:latest", "qwen3:8b"]


def _model_sort_key(model_name: str) -> int:
    for i, m in enumerate(_MODEL_ORDER):
        if model_name == m or model_name.startswith(m):
            return i
    return len(_MODEL_ORDER)


class ProviderRegistry:
    """Expose safe provider and model inventory without leaking secrets."""

    def __init__(self, registry: RegistryLoader | None = None) -> None:
        self.registry = registry or build_registry_loader()

    async def list_models(self) -> dict[str, Any]:
        settings = get_settings()
        provider_health = await ai.check_health()
        ollama_health = provider_health.get("ollama") or {}
        models: list[dict[str, Any]] = []

        for name in sorted(ollama_health.get("models", []), key=_model_sort_key):
            purpose, recommended_use = _purpose_for_model(name)
            models.append(
                {
                    "provider": "ollama",
                    "model_name": name,
                    "availability": bool(ollama_health.get("status") == "ok"),
                    "purpose_label": purpose,
                    "recommended_use": recommended_use,
                    "health_status": ollama_health.get("status", "unknown"),
                    "active": name == settings.ollama_model,
                }
            )

        github_enabled = bool(settings.github_token)
        models.append(
            {
                "provider": "github_models",
                "model_name": settings.github_model_name,
                "availability": github_enabled,
                "purpose_label": "cloud_fallback",
                "recommended_use": "Cloud fallback for higher-latency or overflow cases",
                "health_status": (provider_health.get("github_models") or {}).get("status", "disabled" if not github_enabled else "unknown"),
                "active": False,
            }
        )

        groq_enabled = bool(settings.groq_api_key)
        models.append(
            {
                "provider": "groq",
                "model_name": settings.groq_model_name,
                "availability": groq_enabled,
                "purpose_label": "fast_cloud_fallback",
                "recommended_use": "Fast cloud fallback when local model is unavailable",
                "health_status": (provider_health.get("groq") or {}).get("status", "disabled" if not groq_enabled else "unknown"),
                "active": False,
            }
        )

        return {
            "default_model": settings.ollama_model,
            "primary_provider": settings.primary_ai_provider,
            "models": models,
            "providers": {
                "ollama": {
                    "status": ollama_health.get("status", "unknown"),
                    "active_model": ollama_health.get("active_model") or settings.ollama_model,
                },
                "github_models": {
                    "status": (provider_health.get("github_models") or {}).get("status", "disabled" if not github_enabled else "unknown"),
                    "configured": github_enabled,
                },
                "groq": {
                    "status": (provider_health.get("groq") or {}).get("status", "disabled" if not groq_enabled else "unknown"),
                    "configured": groq_enabled,
                },
            },
        }


class LLMProviderAdapter(ProviderAdapter):
    """Optional internal response path using the existing LLM router."""

    def __init__(self, registry: RegistryLoader | None = None) -> None:
        self.registry = registry or build_registry_loader()

    async def respond(self, prompt: str, evidence: EvidenceEnvelope) -> dict[str, Any]:
        model = evidence.classification.get("requested_model")
        history = evidence.classification.get("history") or []
        context_lines: list[str] = []
        exact_answer: str | None = None
        for family in ("runtime", "business", "memory", "knowledge"):
            for item in getattr(evidence, family, []):
                tool_name = item.get("tool_name") or item.get("tool_id")
                data = item.get("data") or {}
                context_lines.append(f"[{family}:{tool_name}] {data}")
                # Use exact DB answer for employee_search when available.
                if tool_name == "employee_search" and family == "business":
                    matches = data.get("matches") or []
                    count = data.get("count", 0)
                    query = data.get("query", "")
                    if matches:
                        emp = matches[0]
                        lines = [
                            "কর্মচারী রেকর্ড পাওয়া গেছে:",
                            f"- নাম: {emp.get('employee_name', 'অজানা')}",
                            f"- মোবাইল: {emp.get('employee_mobile', '-')}",
                            f"- পদবি: {emp.get('designation', '-')}",
                            f"- অবস্থা: {emp.get('status', '-')}",
                            f"- সোর্স: read-only employee DB/view",
                        ]
                        exact_answer = "\n".join(lines)
                    elif count == 0 and query:
                        exact_answer = f"{query} নম্বরের কোনো কর্মচারী রেকর্ড পাওয়া যায়নি।"
        context = "\n".join(context_lines)[:12000]
        if exact_answer:
            reply = exact_answer
            log.info("[ai_console.provider] using exact_answer for employee_search")
        else:
            reply = await ai.generate_chat_reply(prompt, context, history, model=model)
            log.info("[ai_console.provider] generated_chat_reply used=%s", bool(reply))
        log.info(
            "[ai_console.provider] final_answer_mode=%s context_length=%d",
            "exact" if exact_answer else "llm",
            len(context),
        )
        return {
            "provider": "llm.py",
            "model": model or get_settings().ollama_model,
            "response": reply,
            "status": "ok" if reply else "partial",
            "traceability": ["ORCH-005", "ORCH-061"],
        }
