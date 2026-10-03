"""Phase C v2 — RecruitmentTurnDecision contract tests.

Validates: schema-required fields, type-literal enforcement, ambiguity
fallback (correction 3), confidence-floor / state-write gate,
provenance-authoritative floor (correction 4), MIXED-turn first-class
acceptance (correction 7), CORRECTION-requires-confirmation (correction 8).
"""
from __future__ import annotations

import pytest

from modules.recruitment_turn_decision import (
    AMBIGUITY_CONFIDENCE_FLOOR,
    PROVENANCE_AUTHORITATIVE_FLOOR,
    IntakeProposal,
    Question,
    RecruitmentTurnDecision,
    apply_state_rules,
    validate_decision,
)
from modules.recruitment_turn_decision.state_rules import DecisionValidationError


# --- Required fields ---

def test_decision_has_required_fields():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
    )
    for f in [
        "semantic_domain", "turn_type", "confidence",
        "accepted_proposals", "rejected_proposals", "questions",
        "references_previous_answer", "manual_reply_already_answered",
        "candidate_correction", "candidate_frustration",
        "needs_clarification", "needs_human_review",
        "provenance_actor_type", "provenance_confidence",
        "multi_domain_review_required", "reason_codes",
        "evidence", "should_advance_intake", "should_answer_question",
        "intake_step_after", "state_write", "clarification_question",
    ]:
        assert hasattr(d, f), f"missing field {f}"


# --- Confidence floors ---

def test_ambiguity_floor_is_0_60():
    assert AMBIGUITY_CONFIDENCE_FLOOR == 0.60


def test_provenance_authoritative_floor_is_0_70():
    assert PROVENANCE_AUTHORITATIVE_FLOOR == 0.70


# --- Ambiguity fallback (correction 3) ---

def test_low_confidence_forces_state_write_none():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.55,
        provenance_confidence=0.9,
        should_advance_intake=False,
        should_answer_question=False,
    )
    # apply_state_rules is called externally and forces NONE.
    # But validate_decision requires state_write=NONE for AMBIGUOUS;
    # for non-AMBIGUOUS low-confidence, the caller (decide.py) overrides.
    update = apply_state_rules(d, session={}, current_step="name")
    assert update.intake_field_writes == {}
    assert update.next_step is None


def test_ambiguous_turn_state_write_must_be_none():
    d = RecruitmentTurnDecision(
        semantic_domain="ambiguous",
        turn_type="AMBIGUOUS",
        confidence=0.0,
        state_write="NONE",
        should_advance_intake=False,
        should_answer_question=False,
    )
    validate_decision(d)  # must not raise


def test_ambiguous_turn_with_advance_raises():
    d = RecruitmentTurnDecision(
        semantic_domain="ambiguous",
        turn_type="AMBIGUOUS",
        confidence=0.0,
        state_write="APPLY",
        should_advance_intake=True,
        should_answer_question=False,
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


def test_ambiguous_turn_with_answer_raises():
    d = RecruitmentTurnDecision(
        semantic_domain="ambiguous",
        turn_type="AMBIGUOUS",
        confidence=0.0,
        state_write="APPLY",
        should_advance_intake=False,
        should_answer_question=True,
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


# --- Provenance gate (correction 4) ---

def test_manual_reply_with_low_provenance_raises():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        manual_reply_already_answered=True,
        provenance_confidence=0.5,  # below PROVENANCE_AUTHORITATIVE_FLOOR
        should_answer_question=False,
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


def test_manual_reply_with_high_provenance_ok():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        manual_reply_already_answered=True,
        provenance_confidence=0.75,
        should_answer_question=False,
    )
    validate_decision(d)


# --- Confidence range ---

def test_confidence_out_of_range_raises():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=1.5,
        state_write="NONE",
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


def test_provenance_confidence_out_of_range_raises():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        provenance_confidence=-0.1,
        state_write="NONE",
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


# --- MIXED first-class (correction 7) ---

def test_mixed_turn_has_both_proposals_and_questions():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="MIXED",
        confidence=0.85,
        accepted_proposals=[IntakeProposal(
            field="area", value="Narayanganj", confidence=0.7,
            evidence="text contains Narayanganj", requires_semantic_review=False,
        )],
        questions=[Question(
            topic="salary", must_answer_from_source_of_truth=True,
            evidence="candidate asked কত বেতন",
        )],
        should_advance_intake=True,
        should_answer_question=True,
        state_write="APPLY",
        intake_step_after="job_preference",
    )
    validate_decision(d)  # must not raise


def test_apply_state_rules_mixed_does_not_destroy_either_side():
    session = {}
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="MIXED",
        confidence=0.85,
        accepted_proposals=[IntakeProposal(
            field="area", value="Narayanganj", confidence=0.7,
            evidence="text contains Narayanganj", requires_semantic_review=False,
        )],
        questions=[Question(
            topic="salary", must_answer_from_source_of_truth=True,
            evidence="candidate asked কত বেতন",
        )],
        should_advance_intake=True,
        should_answer_question=True,
        state_write="APPLY",
        intake_step_after="job_preference",
    )
    update = apply_state_rules(d, session, current_step="area")
    # The accepted intake field is preserved (correction 7).
    assert "area" in update.intake_field_writes
    assert update.intake_field_writes["area"] == "Narayanganj"
    # The question is preserved (correction 7: neither side destroys the other).
    assert len(d.questions) == 1
    assert d.questions[0].topic == "salary"


# --- CORRECTION locked-field protection (correction 8) ---

def test_correction_with_locked_value_match_is_noop():
    session = {"full_name": "Md Karim"}
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="CORRECTION",
        confidence=0.9,
        accepted_proposals=[IntakeProposal(
            field="name", value="Md Karim", confidence=0.9,
            evidence="re-assertion", requires_semantic_review=False,
        )],
        candidate_correction=True,
        state_write="APPLY",
    )
    update = apply_state_rules(d, session, current_step="name")
    # No-op: the proposed value equals the locked value.
    assert update.intake_field_writes == {}
    assert update.next_step is None
    assert not update.confirmation_required


def test_correction_with_new_value_requires_confirmation():
    session = {"full_name": "Md Karim"}
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="CORRECTION",
        confidence=0.9,
        accepted_proposals=[IntakeProposal(
            field="name", value="Md Karim Hossain", confidence=0.9,
            evidence="new value", requires_semantic_review=False,
        )],
        candidate_correction=True,
        state_write="APPLY",
    )
    update = apply_state_rules(d, session, current_step="name")
    # Correction 8: deterministic state rules require confirmation; no
    # write yet.
    assert update.intake_field_writes == {}
    assert update.confirmation_required is True


def test_correction_without_clear_proposal_clarifies():
    session = {}
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="CORRECTION",
        confidence=0.9,
        accepted_proposals=[],
        candidate_correction=True,
        state_write="APPLY",
    )
    update = apply_state_rules(d, session, current_step="name")
    assert update.clarification_required is True


# --- Locked-field protection (Phase B preserved) ---

def test_phase_b_locked_field_not_overwritten_by_same_value():
    """If the session already has the value, Phase B field-locking
    prevents re-write. apply_state_rules honors this."""
    session = {"area": "Narayanganj"}
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_INTAKE",
        confidence=0.9,
        accepted_proposals=[IntakeProposal(
            field="area", value="Narayanganj", confidence=0.9,
            evidence="re-assertion", requires_semantic_review=False,
        )],
        should_advance_intake=True,
        state_write="APPLY",
    )
    update = apply_state_rules(d, session, current_step="area")
    assert update.intake_field_writes == {}


# --- NON_RECRUITMENT ---

def test_non_recruitment_must_not_write_recruitment_state():
    d = RecruitmentTurnDecision(
        semantic_domain="non_recruitment",
        turn_type="NON_RECRUITMENT",
        confidence=0.9,
        state_write="APPLY",
        should_advance_intake=True,
    )
    with pytest.raises(DecisionValidationError):
        validate_decision(d)


# --- Reason codes ---

def test_reason_codes_are_tuple():
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        reason_codes=("fee_question_detected", "name_field_question_marker"),
    )
    assert isinstance(d.reason_codes, tuple)


# --- log dict ---

def test_to_log_dict_does_not_include_text():
    """The decision itself NEVER includes message text. Structured log
    lines must not inadvertently serialize the candidate's text."""
    d = RecruitmentTurnDecision(
        semantic_domain="recruitment",
        turn_type="PURE_QUESTION",
        confidence=0.9,
        reason_codes=("test",),
    )
    ld = d.to_log_dict()
    for v in ld.values():
        assert not isinstance(v, str) or len(v) < 1000
    # No field carries arbitrary text in production:
    assert "text" not in ld
    assert "message" not in ld