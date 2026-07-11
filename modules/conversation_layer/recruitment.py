"""Shadow-only unified recruitment/general inquiry reply layer."""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from modules.intent import classify
from modules.knowledge_base import get_reply as kb_get_reply, get_recruitment_reply
from modules.recruitment_flow import STEP_QUESTIONS, get_active_session, is_recruitment_trigger
from modules.reply_templates import get_template

from .playbooks import analyze_recruitment_signals, build_rule_reply, classify_reply_safety
from .prompting import build_recruitment_prompt_context


async def _safe_kb_reply(text: str, intent: str = "recruitment") -> str | None:
    try:
        if intent == "recruitment":
            return await get_recruitment_reply(text)
        return await kb_get_reply(text, intent)
    except (RuntimeError, OSError, ValueError):
        return None


async def _safe_rag_context(text: str) -> tuple[str | None, list[dict[str, Any]]]:
    try:
        from modules import rag
        result = await rag.safe_answer(text, k=2, min_score=2.0)
        if not result:
            return None, []
        return result.get("answer"), result.get("citations") or []
    except (RuntimeError, OSError, ValueError):
        return None, []


async def _generate_shadow_llm_reply(text: str, prompt_context: str, timeout_seconds: float) -> str | None:
    try:
        from app.config import get_settings

        settings = get_settings()
        prompt = (
            "You write short Bangla WhatsApp replies for recruitment only. "
            "Use only the provided facts. Do not mention AI, database, payroll, roster, vessel operations, or private employee data.\n\n"
            f"{prompt_context[:2200]}\n\n"
            f"Candidate message: {text[:400]}\n"
            "Reply in Bangla, 2-4 short lines:"
        )
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.25, "num_predict": 90},
                },
            )
        if response.status_code == 200:
            reply = (response.json().get("response") or "").strip()
            return reply or None
    except (RuntimeError, OSError, ValueError, ImportError, httpx.HTTPError):
        return None
    return None


async def simulate_current_core_reply(sender: str, text: str, source: str = "shadow") -> dict[str, Any]:
    """Simulate current fazle-core recruitment reply without DB mutations.

    The live router would call recruitment_flow.intake_message for new sessions,
    which writes to fazle_recruitment_sessions. Shadow mode mirrors the decision
    shape using read-only checks and static questions instead.
    """
    intent = classify(text)
    active_step = None
    try:
        active_session = await get_active_session(sender)
        if active_session:
            active_step = active_session.get("collection_step") or "name"
    except (RuntimeError, OSError, ValueError):
        active_session = None

    if active_step:
        reply = STEP_QUESTIONS.get(active_step, STEP_QUESTIONS["name"])
        return {
            "mode": "current_core_shadow",
            "intent": intent,
            "source": source,
            "reply": reply,
            "path": "active_recruitment_session_static_question",
            "mutated": False,
        }

    kb_reply = await _safe_kb_reply(text, intent if intent != "unknown" else "recruitment")
    if kb_reply:
        return {
            "mode": "current_core_shadow",
            "intent": intent,
            "source": source,
            "reply": kb_reply,
            "path": "knowledge_base",
            "mutated": False,
        }

    if intent == "recruitment" or is_recruitment_trigger(text):
        return {
            "mode": "current_core_shadow",
            "intent": intent,
            "source": source,
            "reply": STEP_QUESTIONS["name"],
            "path": "would_create_recruitment_session_question_name",
            "mutated": False,
        }

    template = get_template("recruitment", sender)
    return {
        "mode": "current_core_shadow",
        "intent": intent,
        "source": source,
        "reply": template or "আপনার বার্তা পেয়েছি। অফিস থেকে বিস্তারিত জানানো হবে।",
        "path": "template_fallback",
        "mutated": False,
    }


async def generate_recruitment_reply(
    sender: str,
    text: str,
    source: str = "shadow",
    history: str | None = None,
    use_llm: bool = True,
    llm_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Generate a simulated unified recruitment/general inquiry reply.

    This function is deliberately read-only: no sends, no DB schema changes, no
    recruitment session mutations, and no operational workflow calls.
    """
    intent = classify(text)
    signals = analyze_recruitment_signals(text)
    kb_reply = await _safe_kb_reply(text, "recruitment")
    rag_context, citations = await _safe_rag_context(text)
    rule_reply = build_rule_reply(signals)

    reply = rule_reply
    path = "rule_playbook"
    if use_llm:
        prompt_context = build_recruitment_prompt_context(
            message=text,
            signals=signals,
            kb_reply=kb_reply,
            rag_context=rag_context,
            history=history,
        )
        try:
            llm_reply = await asyncio.wait_for(
                _generate_shadow_llm_reply(text, prompt_context, llm_timeout_seconds),
                timeout=llm_timeout_seconds + 5,
            )
        except TimeoutError:
            llm_reply = None
        if llm_reply:
            reply = llm_reply.strip()
            path = "ollama_shadow_direct_playbook_context"
        else:
            path = "rule_playbook_llm_unavailable"

    safety = classify_reply_safety(reply)
    if safety == "restricted":
        reply = rule_reply
        safety = classify_reply_safety(reply)
        path = f"{path}_restricted_replaced_by_rule"

    return {
        "mode": "unified_recruitment_layer_shadow",
        "intent": intent,
        "sender": sender,
        "source": source,
        "reply": reply,
        "path": path,
        "signals": {
            "focus": signals.focus,
            "temperature": signals.temperature,
            "risk": signals.risk,
            "language": signals.language,
            "wants_application": signals.wants_application,
            "needs_trust_repair": signals.needs_trust_repair,
            "asks_multiple_questions": signals.asks_multiple_questions,
        },
        "kb_used": bool(kb_reply),
        "rag_citations": citations,
        "safety": safety,
        "mutated": False,
        "sent": False,
    }


async def generate_recruitment_reply_shadow(
    sender: str,
    text: str,
    source: str = "shadow",
    history: str | None = None,
    use_llm: bool = True,
    llm_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Backward-compatible alias for older callers/tests."""
    return await generate_recruitment_reply(
        sender=sender,
        text=text,
        source=source,
        history=history,
        use_llm=use_llm,
        llm_timeout_seconds=llm_timeout_seconds,
    )
