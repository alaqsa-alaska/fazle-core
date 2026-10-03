"""Prompt assembly for the conversation layer -- originally recruitment-
only (build_recruitment_prompt_context, still recruitment-specific,
unchanged), now also home to build_universal_prompt_context() (Phase 2,
2026-08-06), the general-purpose builder for Tier 15's Universal
Responder. Module docstring updated to match -- see
knowledge_base/00_governance/management_decisions.md "Universal AI
Responder — Phase 2"."""
from __future__ import annotations

from modules.recruitment_authority import FactAuthority, facts_for_recruitment_question

from .playbooks import APPLICATION_FIELDS, RecruitmentSignals


def build_recruitment_prompt_context(
    message: str,
    signals: RecruitmentSignals,
    kb_reply: str | None = None,
    rag_context: str | None = None,
    history: str | None = None,
    contact_context: str | None = None,
    authority_facts: tuple[FactAuthority, ...] | None = None,
) -> str:
    """Return context text passed to the existing Ollama client as DB context.

    The live Ollama helper owns the base system prompt. This context keeps the
    richer layer scoped to public recruitment facts and reply rules.
    """
    facts = [
        "Shadow recruitment conversation layer rules:",
        "- Bangla-first, short, human, 1-4 lines.",
        "- Answer all recruitment/general inquiry questions in one reply when possible.",
        "- Ask only one next-step question or request one compact set of applicant details.",
        "- Do not discuss payroll, roster, escort program operations, vessel names, private employee data, or internal finance.",
        "- Use only APPROVED CURRENT FACT values below for business policy.",
        "- For UNRESOLVED facts, do not state a value; acknowledge that it is not currently confirmed and suggest office verification.",
        "- If user is suspicious, address trust first and invite office verification.",
        f"- Application fields to collect: {APPLICATION_FIELDS}.",
        "",
        f"Detected focus: {signals.focus}",
        f"Candidate temperature: {signals.temperature}",
        f"Risk mode: {signals.risk}",
        "",
        "Recruitment authority register (the only business-fact source):",
    ]
    if authority_facts is None:
        authority_facts = facts_for_recruitment_question(message, focus=signals.focus)
    if authority_facts:
        for authority_fact in authority_facts:
            if authority_fact.answerable:
                facts.append(
                    f"- APPROVED CURRENT FACT {authority_fact.key}: {authority_fact.value}"
                )
            else:
                facts.append(
                    f"- UNRESOLVED FACT {authority_fact.key} status={authority_fact.status}: "
                    "no approved candidate-facing value; do not state a value; "
                    f"safe action={authority_fact.safe_action}"
                )
    else:
        facts.append("- No relevant approved current fact is available for this question.")

    # KB/RAG text is deliberately not copied into this live fallback prompt:
    # those sources may contain evidence-only, historical, or conflicting
    # business facts. Their metadata remains available to the caller for
    # observability, while the register above is the sole policy source.
    if contact_context:
        facts.extend([
            "",
            "Candidate/contact context (continuity only; not business policy):",
            contact_context[:1200],
        ])
    if history:
        facts.extend(["", "Recent conversation history:", history[:1200]])

    facts.extend(["", f"Incoming message: {message[:500]}"])
    return "\n".join(facts)


def build_universal_prompt_context(
    message: str,
    rag_answer: str | None = None,
    history: str | None = None,
    owner_knowledge: str | None = None,
) -> str:
    """Phase 2 (2026-08-06, Owner-approved "Universal AI Responder") --
    general-purpose prompt context for Tier 15's upgraded reply pipeline
    (modules.conversation_layer.generate_universal_reply). Unlike
    build_recruitment_prompt_context() above, this is deliberately topic-
    agnostic: no hardcoded canonical-facts list, no recruitment signals.
    Any topic's facts come from rag_answer, per query, dynamically -- the
    faithfulness validator downstream (playbooks.validate_faithfulness())
    is what keeps the reply grounded to whatever was actually retrieved,
    not a fixed list scoped to one topic.

    S3 (2026-08-08): owner_knowledge is injected above RAG context when
    present, labeled as authoritative business rules."""
    parts = [
        "General reply rules:",
        "- Bangla-first unless the incoming message is in English; short, human, 1-4 lines.",
        "- Answer ONLY using the facts provided below. If the facts don't cover "
        "the question, say so plainly and suggest contacting the office directly "
        "-- never guess or invent a fact.",
        "- Do not discuss payroll, roster, escort program operations, vessel "
        "names, private employee data, or internal finance unless it is "
        "explicitly present in the facts below.",
    ]
    if owner_knowledge:
        parts.extend(["\nOwner-approved knowledge (business rules — authoritative):", owner_knowledge])
    if rag_answer:
        parts.extend(["", "Retrieved facts (the ONLY facts you may use):", rag_answer[:1800]])
    else:
        parts.extend([
            "",
            "No relevant facts were found for this question -- say so plainly "
            "rather than answering from general knowledge.",
        ])
    if history:
        parts.extend(["", "Recent conversation history:", history[:1200]])
    parts.extend(["", f"Incoming message: {message[:500]}"])
    return "\n".join(parts)
