"""
Recruitment conversation layer — Full Activation (Session 9, 2026-06-23).

Exports generate_recruitment_reply for the live message router.
Shadow/simulation helpers remain available for A/B analysis.

Phase 2 (2026-08-06, Owner-approved "Universal AI Responder" -- see
knowledge_base/00_governance/management_decisions.md "Universal AI
Responder — Phase 2"): also exports generate_universal_reply(), the
role-agnostic sibling of generate_recruitment_reply() originally used by
message_router's Tier 15 (AI fallback) in shadow mode. Deliberately
separate from the recruitment path -- does not touch
recruitment_eligibility(), OPERATIONAL_ROLES, or
route_recruitment_message().

RETIRED FROM PRODUCTION ROUTING (2026-08-12, Owner decision -- see the
Tier-15 shadow forensics report): message_router no longer calls
generate_universal_reply() at all. It reasoned from its own
independently-fetched context rather than the identity/intent/routing
context Tier 15 had already resolved, and issued a second, redundant
ai.generate_reply() call on every Tier-15 message purely for shadow
comparison. The function itself is kept, not deleted -- its own unit
tests (TestUniversalReplyGenerator) still exercise it directly -- in
case it's repurposed later (e.g. as an Owner-reviewed evaluator rather
than a parallel responder), but nothing in the live message-processing
path calls it as of this date.
"""

from __future__ import annotations

import logging
from typing import Optional

from .playbooks import validate_faithfulness, SAFE_FALLBACK_REPLY
from .prompting import build_universal_prompt_context
from .recruitment import (
    generate_recruitment_reply as _generate_recruitment_reply,
    generate_recruitment_reply_shadow,
    simulate_current_core_reply,
)

log = logging.getLogger("fazle.conversation_layer")

__all__ = [
    "generate_recruitment_reply",
    "generate_recruitment_reply_shadow",
    "simulate_current_core_reply",
    "generate_universal_reply",
]


async def generate_recruitment_reply(
    *,
    phone: str,
    text: str,
    source: str,
    contact_context: str = "",
    history: str = "",
    decision_context: str = "",  # Phase C (2026-08-23) optional: pre-decided semantic context
) -> Optional[str]:
    """
    Full-mode recruitment reply: KB -> RAG -> Ollama LLM -> safety filter.
    Replaces modules.recruitment_ai.generate_recruitment_reply in the live router.

    Phase C (2026-08-23) extension: when decision_context is non-empty,
    it is prepended to the prompt the LLM consumes. This is the path
    by which RecruitmentTurnDecision's structured output (turn_type,
    questions, accepted fields, etc.) reaches the LLM. The decision
    itself is purely descriptive; the LLM does NOT generate salary/fee
    facts that aren't in source-of-truth. When decision_context is the
    empty string, behavior is byte-identical to before.
    """
    effective_history = history or None
    if decision_context:
        # Decision-context is an additive policy block; history remains
        # the conversation log. Both reach the LLM.
        if effective_history:
            effective_history = decision_context + "\n\n" + effective_history
        else:
            effective_history = decision_context
    result = await _generate_recruitment_reply(
        sender=phone,
        text=text,
        source=source,
        history=effective_history,
        contact_context=contact_context or None,
        use_llm=True,
    )
    reply = (result.get("reply") or "").strip()
    if not reply or result.get("safety") == "restricted":
        return None
    return reply


async def generate_universal_reply(
    *,
    phone: str,
    text: str,
    source: str,
    intent: str = "unknown",
    role: str = "unknown",
    history: str = "",
    db_ctx: str = "",
) -> Optional[str]:
    """Phase 2 (2026-08-06) -- role-agnostic reply generator, same
    KB->RAG->LLM->faithfulness-validate shape generate_recruitment_reply()
    already has, generalized to any topic rather than recruitment
    specifically.

    RETIRED FROM PRODUCTION ROUTING (2026-08-12) -- no longer called by
    message_router's Tier 15; see this module's own top-of-file docstring.
    Kept for its own unit tests and any future, separately-authorized reuse.

    Reuses app.llm's existing multi-provider ai.generate_reply() for the
    actual text generation -- same reliability/fallback-chain Tier 15
    already has today. This function's value-add is entirely in the RAG-
    grounded prompt context fed into it (build_universal_prompt_context())
    and the faithfulness check run on its output afterward
    (validate_faithfulness()), not a different LLM-calling mechanism.

    Never raises -- returns None on any failure (RAG unavailable, LLM
    call fails, empty draft, faithfulness check rejects with nothing to
    fall back to), exactly like generate_recruitment_reply() already
    behaves for its own callers. Deliberately does NOT touch
    recruitment_eligibility(), OPERATIONAL_ROLES, or
    route_recruitment_message() -- see module docstring."""
    # S3: fetch active owner-approved governance facts (business rules).
    # Filter: status='active' AND deprecated_at IS NULL. Bounded at 30.
    owner_knowledge: str | None = None
    try:
        from app.database import fetch_all as _db_fetch
        gov_rows = await _db_fetch(
            """SELECT category, fact_key, fact_value
               FROM fazle_knowledge_governance
               WHERE status = 'active' AND deprecated_at IS NULL
               ORDER BY category, fact_key
               LIMIT 30"""
        )
        if gov_rows:
            owner_knowledge = "\n".join(
                f"[{r['category']}] {r['fact_key']}: {r['fact_value']}"
                for r in gov_rows
            )
    except Exception as _e:
        log.debug("[universal_reply] owner knowledge fetch skipped: %s", _e)

    try:
        from modules import rag as _rag
        rag_result = await _rag.safe_answer(text, k=3, min_score=1.0, role="candidate")
    except (RuntimeError, OSError, ValueError, ImportError):
        rag_result = None

    rag_answer = rag_result.get("answer") if rag_result else None
    citations = rag_result.get("citations") if rag_result else None

    universal_context = build_universal_prompt_context(
        message=text, rag_answer=rag_answer, history=history, owner_knowledge=owner_knowledge,
    )
    combined_ctx = f"{db_ctx}\n\n{universal_context}".strip() if db_ctx else universal_context

    try:
        from app import llm as _ai
        draft = await _ai.generate_reply(
            text, intent, combined_ctx, history=history, role=role, source=source,
        )
    except Exception as e:
        log.debug("[universal_reply] LLM call failed: %s", e)
        return None

    draft = (draft or "").strip()
    if not draft:
        return None

    is_faithful, reason = validate_faithfulness(draft, rag_answer, citations)
    if not is_faithful:
        log.info("[universal_reply] rejected sender=%s reason=%s", phone, reason)
        return SAFE_FALLBACK_REPLY if (rag_answer or citations) else None

    return draft
