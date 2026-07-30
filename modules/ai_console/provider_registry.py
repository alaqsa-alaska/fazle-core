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


def _bn_num(n: int) -> str:
    """Convert integer to Bangla numeral string."""
    return str(n).translate(
        str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
    )


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
                # Use exact DB answers for these tools when available --
                # never let the LLM answer with a capability statement or
                # improvised summary when a real read-only lookup already
                # ran and produced (or definitively did not produce) data.
                if tool_name == "employee_search" and family == "business":
                    matches = data.get("matches") or []
                    count = data.get("count", 0)
                    query = data.get("query", "")
                    if matches:
                        lines = [f"মোট: {_bn_num(count)} জন", "---"]
                        for i, emp in enumerate(matches[:10], start=1):
                            lines.append(
                                f"{_bn_num(i)}. {emp.get('employee_name', 'অজানা')} — "
                                f"ID: {emp.get('employee_id', '-')} — "
                                f"Mobile: {emp.get('employee_mobile', '-')}"
                            )
                        if len(matches) > 10:
                            lines.append(f"... এবং আরও {_bn_num(len(matches) - 10)} জন")
                        lines.append("(সোর্স: employee database)")
                        exact_answer = "\n".join(lines)
                    elif count == 0:
                        label = query or "প্রশ্নে উল্লেখিত তথ্যে"
                        exact_answer = f"{label} নামে/নম্বরে কোনো কর্মচারী রেকর্ড পাওয়া যায়নি। (সোর্স: employee database)"
                elif tool_name == "payroll_page_summary" and family == "business":
                    rows = data.get("rows") or []
                    period = data.get("period", "all")
                    if rows:
                        total_amount = sum(float(r.get("amount") or 0) for r in rows)
                        label = "আজকের পেমেন্ট" if period == "all" else f"{period} পেমেন্ট"
                        lines = [f"{label}: {_bn_num(len(rows))} জন", f"মোট পরিমাণ: ৳{total_amount:,.0f}", "---"]
                        for i, r in enumerate(rows[:10], start=1):
                            lines.append(
                                f"{_bn_num(i)}. {r.get('employee_name', 'অজানা')} — "
                                f"৳{float(r.get('amount') or 0):,.0f} — "
                                f"{r.get('payout_method', '-')} — {r.get('txn_date', '-')}"
                            )
                        if len(rows) > 10:
                            lines.append(f"... এবং আরও {_bn_num(len(rows) - 10)}টি লেনদেন")
                        lines.append("(সোর্স: fpe_cash_transactions)")
                        exact_answer = "\n".join(lines)
                    else:
                        exact_answer = (
                            "কোনো পেমেন্ট লেনদেন পাওয়া যায়নি। "
                            "(সোর্স: fpe_cash_transactions)"
                        )
                elif tool_name == "escort_roster_page_summary" and family == "business":
                    rows = data.get("rows") or []
                    total_count = data.get("total_count", 0)
                    active_count = (data.get("by_status") or {}).get("active", {}).get("count", 0)
                    if rows or total_count:
                        lines = [f"মোট রোস্টারে: {_bn_num(total_count)} জন", f"সক্রিয়: {_bn_num(active_count)} জন", "---"]
                        for i, r in enumerate(rows[:10], start=1):
                            lines.append(
                                f"{_bn_num(i)}. {r.get('escort_name', 'অজানা')} — "
                                f"vessel: {r.get('mother_vessel') or r.get('lighter_vessel') or '-'} — "
                                f"status: {r.get('roster_status', '-')}"
                            )
                        if len(rows) > 10:
                            lines.append(f"... এবং আরও {_bn_num(len(rows) - 10)} জন")
                        lines.append("(সোর্স: escort_roster_entries)")
                        exact_answer = "\n".join(lines)
                    else:
                        exact_answer = (
                            "কোনো রোস্টার তথ্য পাওয়া যায়নি। "
                            "(সোর্স: escort_roster_entries)"
                        )
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
