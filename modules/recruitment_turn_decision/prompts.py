"""Decision-prompt template for RecruitmentTurnDecision (Phase C).

This is the prompt the LLM sees when Tier B is asked to make a decision.
It is deliberately strict: the LLM is asked to DESCRIBE the turn and
identify questions, not to GENERATE facts. Fact generation happens
later, in the existing fact-checked reply path.

The LLM is given:
  - canonical history (with actor_type + provenance_confidence per entry)
  - Tier A IntakeProposals (deterministic candidates)
  - current session state (which fields are already locked)
  - identity
  - approved Recruitment source-of-truth

The LLM returns:
  - one of 8 turn_types
  - one of 3 semantic_domains
  - confidence in [0, 1]
  - questions[] (with topic + must_answer_from_source_of_truth)
  - which IntakeProposals are accepted vs rejected
  - context flags (candidate_correction, candidate_frustration, references_previous_answer, manual_reply_already_answered, needs_clarification, multi_domain_review_required)

This module exports the prompt builder only; decide.py parses the LLM
response and constructs the RecruitmentTurnDecision.

Phase C directive correction 6: the decision separates from reply
generation. The LLM never gets asked to "compose an answer" -- it gets
asked to "describe the candidate's turn". Reply composition happens in
the existing _recruitment_reply_matches_source fact-checked path.
"""
from __future__ import annotations

from typing import List, Optional

from .contract import HistoryEntry, IntakeProposal, RecruitmentTurnDecision


# === Decision prompt ===

DECISION_SYSTEM_PROMPT = """You are the semantic understanding layer for a recruitment WhatsApp funnel.

You DESCRIBE what a candidate means in one inbound turn. You do NOT compose answers.
You do NOT fabricate business facts. You do NOT route to other domains.

Input sections (each turn, in this exact order):
  1. CANONICAL_HISTORY: bidirectional messages, oldest first. Each entry has actor_type + provenance_confidence.
  2. SESSION_STATE: which recruitment fields are already locked on the candidate's session.
  3. INTAKE_PROPOSALS: deterministic Tier-A parser proposals for the candidate's current message.
  4. CURRENT_TURN: the candidate's current inbound message (raw text).
  5. SOURCE_OF_TRUTH: approved recruitment knowledge. Use ONLY to label which questions
     must_answer_from_source_of_truth=true. Never invent numbers, fees, or duties not present here.

Output a single JSON object with these fields (exact names, exact types):
  - semantic_domain: one of"recruitment" | "non_recruitment" | "ambiguous"
  - turn_type: one of"PURE_INTAKE" | "PURE_QUESTION" | "MIXED" | "CORRECTION" |
              "FRUSTRATION" | "FOLLOW_UP_CONTEXTUAL" | "NON_RECRUITMENT" | "AMBIGUOUS"
  - confidence: number 0.0-1.0 (your honest confidence in this turn classification)
  - accepted_proposal_indices: array of indices (zero-based) into INTAKE_PROPOSALS that the
                              candidate is genuinely providing as a value this turn.
                              Empty array if the turn is not providing any intake value.
  - rejected_proposal_indices:  array of indices into INTAKE_PROPOSALS that the
                              parser tentatively matched but the candidate is NOT providing
                              (e.g., the candidate wrote a question that contained a phone-shaped
                              token, but they were asking about something else).
                              If unsure, REJECT (fail closed).
  - questions: array of {topic, must_answer_from_source_of_truth, evidence}
              Topic is a free-form short label (e.g., "available_posts", "office_address",
              "fee", "salary", "training", "geographic_scope"). must_answer_from_source_of_truth
              is true ONLY if the topic is covered by SOURCE_OF_TRUTH (be conservative --
              if unsure, false). evidence is the exact text span in CURRENT_TURN that triggered
              the question.
  - candidate_correction: true if the candidate is correcting a prior statement. The "proposed
              new value" is whichever IntakeProposal you accepted for the current_step.
  - candidate_frustration: true if the candidate expressed frustration (e.g., ???, frustration
              words, repeated demands). Be conservative -- only true if the signal is clear.
  - references_previous_answer: true if the candidate is following up on something said earlier.
  - manual_reply_already_answered: true ONLY if you can identify a recent outbound message in
              CANONICAL_HISTORY whose actor_type is owner_manual_admin AND provenance_confidence
              >= 0.70 AND the topic matches one of the candidate's questions. Otherwise FALSE.
              False positives here silence candidate questions; be conservative.
  - needs_clarification: true if a one-sentence clarification would help (e.g., asking the
              candidate which post they're interested in). Do not write the clarification
              yourself; just set the flag.
  - needs_human_review: true if this turn should be routed to a human operator (e.g., abuse,
              multiple conflicting signals, ambiguous intent that you cannot resolve).
  - multi_domain_review_required: true ONLY if the message plausibly contains both a
              recruitment meaning AND a payroll/attendance/slip/escort meaning. Default false.
  - reason_codes: array of short strings explaining your decision (e.g., "fee_question_detected",
              "name_field_with_question_marker", "frustration_marker_present", "do_gate_blocked").
  - intake_step_after: the field name that intake_message() should advance to. Set to one of
              "name"|"age"|"area"|"job_preference"|"experience"|"phone_confirm"|"complete"|null.
              Set null if the turn should NOT advance intake.

ABSOLUTE RULES:
  - Never invent a salary, fee, address, duty detail, or training detail.
  - If the turn looks like both recruitment AND a payroll/attendance/slip/escort question,
    set multi_domain_review_required=true. Do not try to answer the non-recruitment side.
  - If the candidate expressed frustration, set candidate_frustration=true. Do not advance
    intake purely because a question keyword matched.
  - If you're uncertain between two turn_types, pick AMBIGUOUS with confidence <= 0.6.
  - manual_reply_already_answered=true is a STRONG claim. Default false unless you can
    identify a clearly matching recent outbound from an owner/admin actor.
"""


def build_decision_prompt(
    *,
    history: List[HistoryEntry],
    intake_proposals: List[IntakeProposal],
    session: dict,
    identity: dict,
    current_text: str,
    source_of_truth: str,
    current_step: Optional[str],
) -> str:
    """Construct the user-message payload for the LLM decision call.

    Sections are numbered so the LLM can reference them by section id
    in its structured JSON response.
    """
    sections: list[str] = []

    # 1. CANONICAL_HISTORY
    history_lines: list[str] = []
    for i, h in enumerate(history):
        ts = h.timestamp or ""
        body = (h.text or "").strip()
        if len(body) > 400:
            body = body[:400].rstrip() + "..."
        history_lines.append(
            f"  [{i}] {ts} {h.direction} actor={h.actor_type} "
            f"provenance_conf={h.provenance_confidence:.2f} text={body!r}"
        )
    if not history_lines:
        history_lines.append("  (empty)")
    sections.append("1. CANONICAL_HISTORY:\n" + "\n".join(history_lines))

    # 2. SESSION_STATE
    locked = {k: v for k, v in (session or {}).items()
             if k in ('full_name','age','area','job_preference','experience_years','confirmed_phone')}
    locked_lines = (
        f"2. SESSION_STATE:\n"
        f"  current_step={current_step}\n"
        f"  locked_fields={locked}"
    )
    sections.append(locked_lines)

    # 3. INTAKE_PROPOSALS
    prop_lines: list[str] = []
    for i, p in enumerate(intake_proposals):
        prop_lines.append(
            f"  [{i}] field={p.field} value={p.value!r} "
            f"confidence={p.confidence:.2f} requires_semantic_review={p.requires_semantic_review} "
            f"evidence={p.evidence!r}"
        )
    if not prop_lines:
        prop_lines.append("  (none)")
    sections.append("3. INTAKE_PROPOSALS:\n" + "\n".join(prop_lines))

    # 4. CURRENT_TURN
    sections.append(f"4. CURRENT_TURN:\n  {(current_text or '').strip()!r}")

    # 5. SOURCE_OF_TRUTH (truncated; this is the kb slice the caller built)
    sot = (source_of_truth or "").strip()
    if len(sot) > 6000:
        sot = sot[:6000] + "\n...[truncated; call build_recruitment_context for full text]"
    sections.append(f"5. SOURCE_OF_TRUTH:\n{sot}")

    # 6. IDENTITY (lightweight)
    sections.append(
        "6. IDENTITY:\n"
        f"  role={identity.get('role','unknown')} "
        f"confidence={identity.get('confidence',0)} "
        f"source={identity.get('source','unknown')}"
    )

    return (
        "Make a JSON decision for this turn. "
        "Return ONLY valid JSON matching the schema in the system prompt. "
        "Do not include commentary.\n\n"
        + "\n\n".join(sections)
    )


__all__ = ["DECISION_SYSTEM_PROMPT", "build_decision_prompt"]