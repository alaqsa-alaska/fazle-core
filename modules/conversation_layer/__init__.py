"""
Recruitment conversation layer — Full Activation (Session 9, 2026-06-23).

Exports generate_recruitment_reply for the live message router.
Shadow/simulation helpers remain available for A/B analysis.
"""

from __future__ import annotations

from typing import Optional

from .recruitment import generate_recruitment_reply_shadow, simulate_current_core_reply

__all__ = ["generate_recruitment_reply", "generate_recruitment_reply_shadow", "simulate_current_core_reply"]


async def generate_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    contact_context: str = "",
    history: str = "",
) -> Optional[str]:
    """
    Full-mode recruitment reply: KB → RAG → Ollama LLM → safety filter.
    Replaces modules.recruitment_ai.generate_recruitment_reply in the live router.
    """
    del contact_context  # not used by conversation_layer (same as recruitment_ai)
    result = await generate_recruitment_reply_shadow(
        sender=phone,
        text=text,
        source=source,
        history=history or None,
        use_llm=True,
    )
    reply = (result.get("reply") or "").strip()
    if not reply or result.get("safety") == "restricted":
        return None
    return reply
