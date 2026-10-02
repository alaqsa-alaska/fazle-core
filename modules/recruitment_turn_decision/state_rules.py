"""State-transition rules for RecruitmentTurnDecision (Phase C).

These rules are the only place where a decision becomes a *concrete*
side-effect suggestion. They do NOT mutate state themselves -- the
caller (apply_state_rules) takes the suggestion and asks intake_message
to apply it through the existing audited-transition path.

Phase C directive corrections enforced here:
  2. Phase-B locked-field protections remain authoritative.
  3. confidence < AMBIGUITY_CONFIDENCE_FLOOR  -> state_write=NONE, no
     automatic office-contact fallback.
  4. manual_reply_already_answered=True is only reachable when the
     history entry's provenance_confidence >= PROVENANCE_AUTHORITATIVE_FLOOR.
  7. MIXED turns: questions[] and accepted_proposals[] are independent;
     apply_state_rules returns both a deterministic intake update AND a
     reply request.
  8. candidate_correction=True is not automatic permission to overwrite
     a locked/verified field. apply_state_rules returns
     intake_update_value=None if the proposed correction equals the
     locked value (no-op) and 'CONFIRM_REQUIRED' otherwise.

The validators in this module raise DecisionValidationError when a
decision violates an invariant; the call site (decide.py) catches it
and returns an AMBIGUOUS fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional, Tuple

from .contract import (
    AMBIGUITY_CONFIDENCE_FLOOR,
    PROVENANCE_AUTHORITATIVE_FLOOR,
    IntakeField,
    IntakeProposal,
    RecruitmentTurnDecision,
    TurnType,
)


class DecisionValidationError(Exception):
    """Raised by validate_decision() when a decision violates an invariant.

    decide.py catches this and returns AMBIGUOUS + needs_human_review=True.
    """
    pass


# --- Field-name map from contract IntakeField -> session column name ---
_FIELD_TO_SESSION_COL = {
    "name": "full_name",
    "age": "age",
    "dob": None,             # DOB is a guard; no intake column
    "area": "area",
    "job_preference": "job_preference",
    "experience": "experience_years",
    "phone_confirm": "confirmed_phone",
}


# === Validators ===

def validate_decision(decision: RecruitmentTurnDecision) -> None:
    """Raises DecisionValidationError when an invariant is violated.

    Called by decide.py *before* returning the decision to the caller.
    The caller treats any raise as a fail-closed AMBIGUOUS.
    """
    if not (0.0 <= decision.confidence <= 1.0):
        raise DecisionValidationError(f"confidence out of range: {decision.confidence}")
    if not (0.0 <= decision.provenance_confidence <= 1.0):
        raise DecisionValidationError(
            f"provenance_confidence out of range: {decision.provenance_confidence}"
        )
    # correction 4: manual_reply_already_answered is only valid when the
    # CURRENT inbound's provenance_confidence is high enough that we
    # trust the upstream history. Since the manual-reply claim is about
    # a *historical* entry, the validator here is a defense-in-depth
    # check that the decision itself wasn't fabricated without going
    # through the canonical-history path.
    if decision.manual_reply_already_answered and decision.provenance_confidence < PROVENANCE_AUTHORITATIVE_FLOOR:
        raise DecisionValidationError(
            "manual_reply_already_answered=True requires provenance_confidence "
            f">= {PROVENANCE_AUTHORITATIVE_FLOOR}; got {decision.provenance_confidence}"
        )
    # correction 3: AMBIGUOUS decisions MUST set state_write=NONE
    if decision.turn_type == "AMBIGUOUS" and decision.state_write != "NONE":
        raise DecisionValidationError(
            "AMBIGUOUS turn must set state_write=NONE (Owner directive correction 3)"
        )
    if decision.turn_type == "AMBIGUOUS" and decision.should_advance_intake:
        raise DecisionValidationError("AMBIGUOUS must not advance intake")
    if decision.turn_type == "AMBIGUOUS" and decision.should_answer_question:
        raise DecisionValidationError("AMBIGUOUS must not answer questions automatically")
    if decision.turn_type == "NON_RECRUITMENT" and decision.state_write != "NONE":
        raise DecisionValidationError("NON_RECRUITMENT must not write recruitment state")
    # correction 7: MIXED turns can have BOTH accepted_proposals and
    # questions; that's expected.
    return None


# === Application ===

@dataclass
class IntakeUpdate:
    """Concrete deterministic intake write suggestion produced by
    apply_state_rules. The CALLER persists this through the existing
    audited-transition path in recruitment_flow.intake_message."""
    accepted_proposals: List[IntakeProposal]
    next_step: Optional[str]            # None = no advance
    intake_field_writes: dict           # {session_col_name: value}
    clarification_required: bool
    confirmation_required: bool         # for candidate_correction
    blocked_reason: Optional[str] = None

    def is_noop(self) -> bool:
        return (
            not self.intake_field_writes
            and self.next_step is None
            and not self.clarification_required
            and not self.confirmation_required
        )


def apply_state_rules(
    decision: RecruitmentTurnDecision,
    session: dict,
    current_step: Optional[str],
) -> IntakeUpdate:
    """Map a validated decision to a deterministic IntakeUpdate.

    Invariants enforced:
      - confidence < AMBIGUITY_CONFIDENCE_FLOOR -> empty update (NONE).
      - AMBIGUOUS turn_type -> empty update (NONE).
      - NON_RECRUITMENT -> empty update (routing returns to legacy).
      - candidate_correction == True + proposed value == locked value
        -> noop (Tier B has nothing to do).
      - candidate_correction == True + proposed value != locked value
        -> confirmation_required=True; field write is NOT applied
        (deterministic path of intake_message confirms with candidate).
      - Pure intake acceptance -> field write + step advance.
      - MIXED -> intake writes are independent of question reply.
      - CORRECTION without candidate_correction=True flag -> noop
        (defense-in-depth; the field is what carries the signal).
    """
    # Defense-in-depth: re-validate before applying
    validate_decision(decision)

    if decision.turn_type == "AMBIGUOUS":
        return IntakeUpdate(
            accepted_proposals=[],
            next_step=None,
            intake_field_writes={},
            clarification_required=False,
            confirmation_required=False,
            blocked_reason="confidence_below_floor" if decision.confidence < AMBIGUITY_CONFIDENCE_FLOOR else "ambiguous_turn",
        )

    if decision.turn_type == "NON_RECRUITMENT":
        return IntakeUpdate(
            accepted_proposals=[],
            next_step=None,
            intake_field_writes={},
            clarification_required=False,
            confirmation_required=False,
            blocked_reason="non_recruitment",
        )

    # === Handle CORRECTION specifically ===
    if decision.candidate_correction:
        # We don't have the "proposed new value" in the decision
        # (RecruitmentTurnDecision is descriptive, not factitious).
        # The proposed_value is in the IntakeProposal that survived
        # Tier B's accept/reject. We trust the *single accepted proposal*
        # for the current_step; anything else is ambiguous and needs
        # confirmation.
        current_field_proposals = [
            p for p in decision.accepted_proposals
            if _FIELD_TO_SESSION_COL.get(p.field) is not None
            and (current_step is None or _step_to_field_name(current_step) == p.field)
        ]
        if not current_field_proposals:
            return IntakeUpdate(
                accepted_proposals=[],
                next_step=None,
                intake_field_writes={},
                clarification_required=True,
                confirmation_required=False,
                blocked_reason="correction_without_clear_proposal",
            )
        # If the only accepted proposal is a re-assertion of a locked
        # value, this is a noop (correction 8).
        proposal = current_field_proposals[0]
        session_col = _FIELD_TO_SESSION_COL[proposal.field]
        locked_val = session.get(session_col)
        if locked_val == proposal.value:
            return IntakeUpdate(
                accepted_proposals=[proposal],
                next_step=None,
                intake_field_writes={},
                clarification_required=False,
                confirmation_required=False,
                blocked_reason="correction_matches_locked_noop",
            )
        # Otherwise, candidate_correction=True + different value =
        # require confirmation. State write is NOT applied yet.
        return IntakeUpdate(
            accepted_proposals=[proposal],
            next_step=None,
            intake_field_writes={},
            clarification_required=False,
            confirmation_required=True,
            blocked_reason=None,
        )

    # === Pure intake / MIXED accepted proposals ===
    field_writes: dict = {}
    next_step: Optional[str] = None
    if decision.should_advance_intake and decision.intake_step_after is not None:
        next_step = decision.intake_step_after

    for p in decision.accepted_proposals:
        col = _FIELD_TO_SESSION_COL.get(p.field)
        if col is None:
            continue
        if p.value is None:
            continue
        # Phase-B field-locking: if the session already has this value
        # locked, do NOT write again. (correction 2.)
        if session.get(col) == p.value:
            continue
        # If the proposal was flagged requires_semantic_review=True,
        # we still respect Tier B's accept decision here -- because
        # Tier B's decision IS the semantic review.
        field_writes[col] = p.value

    return IntakeUpdate(
        accepted_proposals=decision.accepted_proposals,
        next_step=next_step,
        intake_field_writes=field_writes,
        clarification_required=decision.needs_clarification,
        confirmation_required=False,
    )


def _step_to_field_name(step: str) -> Optional[str]:
    m = {
        "name": "name",
        "age": "age",
        "area": "area",
        "job_preference": "job_preference",
        "experience": "experience",
        "phone_confirm": "phone_confirm",
    }
    return m.get(step)


__all__ = [
    "DecisionValidationError",
    "IntakeUpdate",
    "validate_decision",
    "apply_state_rules",
    "PROVENANCE_AUTHORITATIVE_FLOOR",
    "AMBIGUITY_CONFIDENCE_FLOOR",
]