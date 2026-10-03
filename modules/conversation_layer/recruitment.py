"""Authority-safe unified Recruitment fallback reply layer."""
from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from modules.intent import classify
from modules.knowledge_base import get_reply as kb_get_reply, get_recruitment_reply
from modules.recruitment_authority import FactAuthority, facts_for_recruitment_question
from modules.recruitment_flow import STEP_QUESTIONS, get_active_session, is_recruitment_trigger
from modules.reply_templates import get_template

from .playbooks import analyze_recruitment_signals, classify_reply_safety
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


def _numeric_tokens(text: str) -> set[str]:
    """Normalize numeric tokens for the fallback's fail-closed check."""
    translation = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
    return set(re.findall(r"\d+", (text or "").translate(translation)))


def _authority_reply_is_safe(
    reply: str,
    *,
    message: str,
    history: str | None,
    contact_context: str | None,
    authority_facts: tuple[FactAuthority, ...],
) -> bool:
    """Reject newly invented numbers outside user/context/approved facts."""
    allowed = _numeric_tokens(message)
    # History is continuity context, never authority. In particular, an old
    # assistant answer must not whitelist a retired salary/fee/contact value.
    allowed.update(_numeric_tokens(contact_context or ""))
    for fact in authority_facts:
        if fact.answerable:
            allowed.update(_numeric_tokens(fact.value or ""))
    return _numeric_tokens(reply).issubset(allowed)


def _safe_authority_fallback(
    text: str,
    authority_facts: tuple[FactAuthority, ...],
) -> str:
    """Natural fallback unless the register has a current approved value."""
    has_bangla = bool(re.search(r"[\u0980-\u09ff]", text or ""))
    unresolved = [fact for fact in authority_facts if not fact.answerable]
    approved = [fact for fact in authority_facts if fact.answerable]
    if unresolved:
        if has_bangla:
            return (
                "এই তথ্যের নির্দিষ্ট বর্তমান মান এখনো নিশ্চিত করা হয়নি। "
                "আপনি চাইলে আপনার প্রশ্ন বা আবেদন-সংক্রান্ত বিস্তারিত বলুন; "
                "সর্বশেষ তথ্য অফিসে যাচাই করে জানানো হবে।"
            )
        return (
            "That detail is not currently confirmed. Tell me a little more about "
            "your application, or verify the latest information with the office."
        )
    if approved:
        if has_bangla:
            return "বর্তমান নিশ্চিত তথ্য:\n" + "\n".join(
                fact.value for fact in approved if fact.value
            )
        return "Current verified information:\n" + "\n".join(
            fact.value for fact in approved if fact.value
        )
    if has_bangla:
        return "এই প্রশ্নের জন্য বর্তমানে নিশ্চিত তথ্য নেই। অফিসে যাচাই করে জানানো হবে।"
    return "I do not have a currently verified answer for that. Please verify it with the office."


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
    contact_context: str | None = None,
    use_llm: bool = True,
    llm_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Generate an authority-safe Recruitment fallback reply.

    This function is deliberately read-only: no sends, no DB schema changes, no
    recruitment session mutations, and no operational workflow calls. Business
    facts come only from the Recruitment authority register.
    """
    intent = classify(text)
    signals = analyze_recruitment_signals(text)
    authority_facts = facts_for_recruitment_question(text, focus=signals.focus)
    kb_reply = await _safe_kb_reply(text, "recruitment")
    rag_context, citations = await _safe_rag_context(text)

    if any(not fact.answerable for fact in authority_facts):
        reply = _safe_authority_fallback(text, authority_facts)
        path = "authority_safe_unresolved_fact"
    elif use_llm:
        prompt_context = build_recruitment_prompt_context(
            message=text,
            signals=signals,
            kb_reply=None,
            rag_context=None,
            history=history,
            contact_context=contact_context,
            authority_facts=authority_facts,
        )
        try:
            llm_reply = await asyncio.wait_for(
                _generate_shadow_llm_reply(text, prompt_context, llm_timeout_seconds),
                timeout=llm_timeout_seconds + 5,
            )
        except TimeoutError:
            llm_reply = None
        if llm_reply:
            candidate_reply = llm_reply.strip()
            if _authority_reply_is_safe(
                candidate_reply,
                message=text,
                history=history,
                contact_context=contact_context,
                authority_facts=authority_facts,
            ):
                reply = candidate_reply
                path = "ollama_shadow_authority_context"
            else:
                reply = _safe_authority_fallback(text, authority_facts)
                path = "ollama_reply_rejected_authority_numeric_check"
        else:
            reply = _safe_authority_fallback(text, authority_facts)
            path = "authority_safe_llm_unavailable"
    else:
        reply = _safe_authority_fallback(text, authority_facts)
        path = "authority_safe_llm_disabled"

    safety = classify_reply_safety(reply)
    if safety == "restricted":
        reply = _safe_authority_fallback(text, authority_facts)
        safety = classify_reply_safety(reply)
        path = f"{path}_restricted_replaced_by_authority_safe"

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
    contact_context: str | None = None,
    use_llm: bool = True,
    llm_timeout_seconds: float = 90.0,
) -> dict[str, Any]:
    """Backward-compatible alias for older callers/tests."""
    return await generate_recruitment_reply(
        sender=sender,
        text=text,
        source=source,
        history=history,
        contact_context=contact_context,
        use_llm=use_llm,
        llm_timeout_seconds=llm_timeout_seconds,
    )
