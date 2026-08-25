"""Decide orchestrator for RecruitmentTurnDecision (Phase C).

decide() assembles the canonical-history view, the deterministic
Tier A proposals, the source-of-truth, and an LLM call (gated by
recruitment_turn_decision_v2_enabled), parses the LLM response into
RecruitmentTurnDecision, runs validate_decision, and returns.

CRITICAL FAIL-CLOSED semantics:

  - If recruitment_turn_decision_v2_enabled is False (default in app/config.py),
    decide() returns None. The caller (recruitment_flow.route_recruitment_message)
    falls through to the legacy classify_turn_intent + intake_message path.
    Phase B and earlier are byte-identical when this flag is OFF.

  - If the flag is ON BUT the LLM call raises/times-out, decide() returns
    an AMBIGUOUS fallback with needs_human_review=True, state_write=NONE.
    Never raises into the caller.

  - If the LLM returns malformed JSON, AMBIGUOUS fallback.

  - If validate_decision() raises (invariant violation), AMBIGUOUS fallback.

  - The LLM never gets to compose an answer. It only describes. Reply
    composition happens in the existing fact-checked path.

This module does NOT import Ollama/Groq/Hermes directly. The caller
supplies an `llm_call` function so the decision path is testable in
isolation. Production wiring (modules/hermes_dispatch) supplies a
function that calls the existing _call_hermes with a decision-only
policy and parses JSON.

WS1 (2026-08-25): the call-supplied `llm_call` kwarg is OPTIONAL. When
the caller does not supply one AND recruitment_turn_decision_v2_real_
llm is True (app/config.py), decide() pulls a real bridge closure from
modules.recruitment_turn_decision.llm_bridge.make_decision_llm_call()
which walks the canonical provider chain (OmniRoute -> Groq -> local
Ollama/Hermes -> Kimi). When BOTH v2_enabled AND v2_real_llm are True
AND the bridge reports a reachable chain, the decision is genuinely
LLM-generated. When either flag is off OR the bridge is unreachable,
decide() falls through to its existing _default_no_llm stub
(AMBIGUOUS) — production routing is byte-identical unless the Owner
explicitly opts in.
"""
from __future__ import annotations

import json
import logging
from typing import Awaitable, Callable, List, Optional

from .contract import (
    AMBIGUITY_CONFIDENCE_FLOOR,
    HistoryEntry,
    IntakeProposal,
    Question,
    ReasoningEvidence,
    RecruitmentTurnDecision,
    SemanticDomain,
    TurnType,
)
from .prompts import DECISION_SYSTEM_PROMPT, build_decision_prompt
from .state_rules import DecisionValidationError, validate_decision


log = logging.getLogger(__name__)


# Type alias for the injected LLM call.
DecisionLLMCall = Callable[[str, str], Awaitable[Optional[str]]]
#                                                       (system, user) -> JSON string or None


async def _default_no_llm(system: str, user: str) -> Optional[str]:
    """Default LLM stub used when no provider is configured. Returns None
    so decide() falls back to the AMBIGUOUS fallback path. Production
    callers (modules/hermes_dispatch.build_decision_context + an
    Hermes-only call) replace this with a real provider."""
    return None


def _parse_llm_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # Defensive: some LLMs wrap JSON in ```json fences.
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _build_ambiguous_fallback(
    *,
    text: str,
    intake_proposals: List[IntakeProposal],
    reason_code: str,
) -> RecruitmentTurnDecision:
    """Per Owner directive correction 3: ambiguity fallback is conservative.

    - turn_type = AMBIGUOUS
    - needs_human_review = True
    - state_write = NONE
    - NO automatic office/contact fallback reply.
    """
    return RecruitmentTurnDecision(
        semantic_domain="ambiguous",
        turn_type="AMBIGUOUS",
        confidence=0.0,
        accepted_proposals=[],
        rejected_proposals=list(intake_proposals),  # all proposals fail-closed-rejected
        questions=[],
        references_previous_answer=False,
        manual_reply_already_answered=False,
        candidate_correction=False,
        candidate_frustration=False,
        needs_human_review=True,
        needs_clarification=False,
        provenance_actor_type="unknown",
        provenance_confidence=0.0,
        multi_domain_review_required=False,
        reason_codes=("AMBIGUOUS_FALLBACK", reason_code),
        evidence=[],
        should_advance_intake=False,
        should_answer_question=False,
        intake_step_after=None,
        state_write="NONE",
        clarification_question=None,
    )


async def decide(
    *,
    text: str,
    current_step: Optional[str],
    session: dict,
    identity: dict,
    history: List[HistoryEntry],
    intake_proposals: List[IntakeProposal],
    source_of_truth: str,
    llm_call: Optional[DecisionLLMCall] = None,
    flag_enabled: bool = False,
) -> Optional[RecruitmentTurnDecision]:
    """Phase C orchestrator.

    Returns None when the V2 flag is OFF (legacy path is authoritative).
    Returns a RecruitmentTurnDecision (possibly AMBIGUOUS) when V2 is ON.

    Never raises.
    """
    if not flag_enabled:
        return None

    # Build prompt
    try:
        user_payload = build_decision_prompt(
            history=history,
            intake_proposals=intake_proposals,
            session=session or {},
            identity=identity or {},
            current_text=text or "",
            source_of_truth=source_of_truth or "",
            current_step=current_step,
        )
    except Exception as exc:
        log.warning("[recruitment_decision] prompt build failed: %s", exc)
        return _build_ambiguous_fallback(
            text=text, intake_proposals=intake_proposals, reason_code="prompt_build_failed"
        )

    # Call LLM
    #
    # Resolution order for which `llm_call` is invoked:
    #   1. Caller-supplied llm_call (test injection or explicit production
    #      wiring) — wins unconditionally.
    #   2. WS1 real-LLM bridge (modules.recruitment_turn_decision.
    #      llm_bridge.make_decision_llm_call()) — used when the caller
    #      did not supply one AND the recruitment_turn_decision_v2_real_
    #      llm config flag is on.
    #   3. _default_no_llm stub — returns None, drives decide() into the
    #      AMBIGUOUS branch (existing pre-WS1 behavior; preserves
    #      byte-identical routing when neither flag is on).
    if llm_call is not None:
        call = llm_call
    else:
        try:
            from .llm_bridge import is_real_llm_enabled, make_decision_llm_call
            if is_real_llm_enabled():
                try:
                    from app.config import get_settings
                    timeout = float(get_settings().recruitment_decision_call_timeout_seconds)
                except Exception:
                    timeout = 12.0
                call = make_decision_llm_call(timeout=timeout)
            else:
                call = _default_no_llm
        except Exception as exc:  # noqa: BLE001 -- bridge import must never raise
            log.warning("[recruitment_decision] llm_bridge resolution failed: %s", exc)
            call = _default_no_llm
    try:
        raw = await call(DECISION_SYSTEM_PROMPT, user_payload)
    except Exception as exc:
        log.warning("[recruitment_decision] LLM call raised: %s", exc)
        return _build_ambiguous_fallback(
            text=text, intake_proposals=intake_proposals, reason_code="llm_call_raised"
        )

    parsed = _parse_llm_json(raw)
    if not parsed:
        return _build_ambiguous_fallback(
            text=text, intake_proposals=intake_proposals, reason_code="llm_malformed_json"
        )

    # Build the decision
    try:
        decision = _decision_from_llm_json(
            parsed,
            text=text,
            intake_proposals=intake_proposals,
            history=history,
            identity=identity,
        )
    except Exception as exc:
        log.warning("[recruitment_decision] decision construction failed: %s", exc)
        return _build_ambiguous_fallback(
            text=text, intake_proposals=intake_proposals, reason_code="decision_build_failed"
        )

    # Validate (corrections 3, 4, 8). On violation -> AMBIGUOUS.
    try:
        validate_decision(decision)
    except DecisionValidationError as exc:
        log.warning("[recruitment_decision] validation failed: %s", exc)
        return _build_ambiguous_fallback(
            text=text, intake_proposals=intake_proposals,
            reason_code=f"validation_failed:{exc}",
        )

    return decision


# === Helpers ===

_VALID_TURN_TYPES = (
    "PURE_INTAKE",
    "PURE_QUESTION",
    "MIXED",
    "CORRECTION",
    "FRUSTRATION",
    "FOLLOW_UP_CONTEXTUAL",
    "NON_RECRUITMENT",
    "AMBIGUOUS",
)
_VALID_DOMAINS = ("recruitment", "non_recruitment", "ambiguous")


def _coerce_turn_type(v) -> str:
    if v in _VALID_TURN_TYPES:
        return v
    return "AMBIGUOUS"


def _coerce_domain(v) -> str:
    if v in _VALID_DOMAINS:
        return v
    return "ambiguous"


def _decision_from_llm_json(
    obj: dict,
    *,
    text: str,
    intake_proposals: List[IntakeProposal],
    history: List[HistoryEntry],
    identity: Optional[dict] = None,
) -> RecruitmentTurnDecision:
    semantic_domain = _coerce_domain(obj.get("semantic_domain"))
    turn_type = _coerce_turn_type(obj.get("turn_type"))
    confidence = obj.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    accepted_idx = _safe_indices(obj.get("accepted_proposal_indices"), len(intake_proposals))
    rejected_idx = _safe_indices(obj.get("rejected_proposal_indices"), len(intake_proposals))
    accepted = [intake_proposals[i] for i in accepted_idx]
    rejected_set = set(rejected_idx)
    # If neither accepted nor rejected indexed, default to all-rejected
    # (fail-closed). Otherwise union of accepted and rejected is the full
    # list of proposals.
    if not accepted_idx and not rejected_idx:
        rejected = list(intake_proposals)
        accepted = []
    else:
        rejected = [intake_proposals[i] for i in rejected_idx if i not in set(accepted_idx)]
        # Also include any proposal NOT mentioned in either list as rejected
        mentioned = set(accepted_idx) | rejected_set
        for i in range(len(intake_proposals)):
            if i not in mentioned and intake_proposals[i] not in accepted and intake_proposals[i] not in rejected:
                rejected.append(intake_proposals[i])

    questions = []
    for q in obj.get("questions", []) or []:
        if not isinstance(q, dict):
            continue
        questions.append(Question(
            topic=str(q.get("topic", ""))[:120],
            must_answer_from_source_of_truth=bool(q.get("must_answer_from_source_of_truth", False)),
            evidence=str(q.get("evidence", ""))[:200],
        ))

    references_previous_answer = bool(obj.get("references_previous_answer", False))
    manual_reply_already_answered = bool(obj.get("manual_reply_already_answered", False))
    candidate_correction = bool(obj.get("candidate_correction", False))
    candidate_frustration = bool(obj.get("candidate_frustration", False))
    needs_clarification = bool(obj.get("needs_clarification", False))
    needs_human_review = bool(obj.get("needs_human_review", False))
    multi_domain_review_required = bool(obj.get("multi_domain_review_required", False))

    reason_codes = tuple(str(rc)[:80] for rc in (obj.get("reason_codes") or []) if rc)

    # Provenance from current inbound: the LLM does not see the candidate's
    # provenance_confidence (it's the system's own metric). We compute it
    # here conservatively as 1.0 if identity is explicit and the session
    # is well-formed, 0.0 otherwise. The identity_brain module is the
    # authoritative source.
    identity_role = (identity or {}).get("role") or "unknown"
    identity_confidence = (identity or {}).get("confidence")
    try:
        identity_confidence_f = float(identity_confidence) if identity_confidence is not None else 0.0
    except (TypeError, ValueError):
        identity_confidence_f = 0.0
    provenance_confidence = max(0.0, min(1.0, identity_confidence_f / 100.0)) if identity_confidence_f > 1.0 else max(0.0, min(1.0, identity_confidence_f))

    # Compute should_advance_intake / should_answer_question:
    # - PURE_INTAKE: should_advance only if accepted_proposals is non-empty AND no question.
    # - PURE_QUESTION: should_answer only; should_advance = False.
    # - MIXED: both should be True (correction 7).
    # - CORRECTION: should_advance=False (correction 8 -- confirmation required).
    # - FRUSTRATION: should_answer=False (recap only); should_advance=False.
    # - FOLLOW_UP_CONTEXTUAL: should_answer=True; should_advance=False.
    # - NON_RECRUITMENT / AMBIGUOUS: both False.
    intake_step_after = obj.get("intake_step_after")
    if intake_step_after not in (None, "name", "age", "area", "job_preference", "experience", "phone_confirm", "complete"):
        intake_step_after = None

    if turn_type == "PURE_INTAKE":
        should_advance = bool(accepted and not questions)
        should_answer = False
    elif turn_type == "PURE_QUESTION":
        should_advance = False
        should_answer = bool(questions)
    elif turn_type == "MIXED":
        should_advance = bool(accepted)
        should_answer = bool(questions)
    elif turn_type == "CORRECTION":
        should_advance = False  # correction 8: state_rules requires confirmation
        should_answer = False   # we don't fabricate the new value here
    elif turn_type == "FRUSTRATION":
        should_advance = False
        should_answer = False   # recap only; no question answer
    elif turn_type == "FOLLOW_UP_CONTEXTUAL":
        should_advance = False
        should_answer = True
    elif turn_type == "NON_RECRUITMENT":
        should_advance = False
        should_answer = False
    else:  # AMBIGUOUS
        should_advance = False
        should_answer = False

    # State-write gate (correction 3): AMBIGUOUS + low confidence -> NONE
    state_write = "APPLY" if (turn_type not in ("AMBIGUOUS", "NON_RECRUITMENT")
                              and confidence >= AMBIGUITY_CONFIDENCE_FLOOR) else "NONE"

    # Apply correction 3 strictly: if confidence < floor, force NONE
    # even if turn_type is not AMBIGUOUS (defensive -- turn_type may have
    # been AMBIGUOUS-coerced from a malformed value).
    if confidence < AMBIGUITY_CONFIDENCE_FLOOR:
        state_write = "NONE"

    # Determine provenance_actor_type: candidate is the only safe inference
    # for an inbound turn. Outbound provenance is tracked in HistoryEntry.
    provenance_actor_type = "candidate" if identity_role in ("candidate", "guest", "unknown") else "unknown"

    # If manual_reply_already_answered is True, find the historical entry
    # and double-check its provenance_confidence. If insufficient, force
    # manual_reply_already_answered=False (correction 4 defense-in-depth).
    if manual_reply_already_answered:
        latest_outbound = None
        for h in reversed(history):
            if h.direction == "outbound":
                latest_outbound = h
                break
        if latest_outbound is None or latest_outbound.provenance_confidence < 0.70:
            manual_reply_already_answered = False
            reason_codes = reason_codes + ("manual_reply_unverified",)

    # If multi_domain_review_required, never auto-answer
    if multi_domain_review_required:
        should_answer = False
        state_write = "NONE"
        needs_human_review = True

    return RecruitmentTurnDecision(
        semantic_domain=semantic_domain,  # type: ignore[arg-type]
        turn_type=turn_type,              # type: ignore[arg-type]
        confidence=confidence,
        accepted_proposals=accepted,
        rejected_proposals=rejected,
        questions=questions,
        references_previous_answer=references_previous_answer,
        manual_reply_already_answered=manual_reply_already_answered,
        candidate_correction=candidate_correction,
        candidate_frustration=candidate_frustration,
        needs_clarification=needs_clarification,
        needs_human_review=needs_human_review,
        provenance_actor_type=provenance_actor_type,  # type: ignore[arg-type]
        provenance_confidence=provenance_confidence,
        multi_domain_review_required=multi_domain_review_required,
        reason_codes=reason_codes,
        evidence=[],
        should_advance_intake=should_advance,
        should_answer_question=should_answer,
        intake_step_after=intake_step_after,
        state_write=state_write,  # type: ignore[arg-type]
        clarification_question=None,
    )


def _safe_indices(values, n: int):
    """Return indices in [0, n) from a possibly-malformed LLM list."""
    out = []
    if not values or not isinstance(values, list):
        return out
    for v in values:
        try:
            i = int(v)
            if 0 <= i < n:
                out.append(i)
        except (TypeError, ValueError):
            pass
    return out


__all__ = [
    "decide",
    "DecisionLLMCall",
    "build_decision_fallback",
]


def build_decision_fallback(*, reason: str) -> RecruitmentTurnDecision:
    """Public helper for tests/callers that need a deterministic
    AMBIGUOUS decision without invoking the LLM."""
    return _build_ambiguous_fallback(
        text="",
        intake_proposals=[],
        reason_code=reason,
    )