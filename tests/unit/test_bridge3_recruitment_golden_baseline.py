"""Bridge3 Recruitment Golden Regression (2026-08-25, P0 closure).

This suite locks in the MINIMUM production-proven Bridge3 recruitment
behavior as of commit b7a302d (the P0 fix for cooldown silently dropping
active-session intake answers) as a permanent deployment gate. See
scripts/restart_gate.sh, check 4b.

Every case here traces back to either:
  - a real production failure this codebase actually experienced (the
    fast-reply-cooldown-drop incident, live-confirmed against 38 real
    bridge3 candidates on 2026-08-25), sanitized to a behavior pattern
    with no real phone numbers/candidate data, or
  - a currently-proven capability (deterministic age/step handling,
    answer-first, fact-check fail-open) that must not silently regress.

Golden Baseline principle (Owner directive, 2026-08-25): NEW CAPABILITY
>= CURRENT VERIFIED CAPABILITY. This suite does not forbid improving any
of these behaviors (better wording, richer semantic understanding, more
fields captured per turn) -- it forbids losing what is already proven.
A test failing here means a real capability the production system has
already demonstrated would be lost by the change under test, not that
the change itself is disallowed.

Do NOT weaken a test in this file to make it pass. If a genuine product
decision requires a documented behavior change, update the test with a
comment explaining the Owner-approved change, not just delete the
assertion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from modules.recruitment_flow import _parse_age, intake_message


def _session(step: str, **overrides) -> dict:
    base = {
        "id": 9001,
        "candidate_id": 9001,
        "funnel_stage": "collecting",
        "collection_step": step,
        "full_name": None,
        "age": None,
        "area": None,
        "job_preference": None,
        "experience_years": None,
        "confirmed_phone": None,
        "source_message": "চাকরি করতে চাই",
        "source_bridge": "bridge3",
    }
    base.update(overrides)
    return base


# ─── A1. Age parsing: Bengali + ASCII, plain and embedded ──────────────────

@pytest.mark.parametrize("text,expected", [
    ("32", 32), ("৩২", 32), ("35", 35), ("৩৫", 35), ("36", 36), ("৩৬", 36),
    ("আমার বয়স ৩২", 32), ("বয়স ৩৫ বছর", 35), ("আমি 36", 36),
    ("আমার বয়স প্রায় ৩২ বছর", 32), ("৩২ বছর", 32),
])
def test_age_parses_bengali_and_ascii(text, expected):
    assert _parse_age(text) == expected


def test_age_rejects_dob_shaped_text():
    """A date-of-birth string must never be interpreted as a bare age
    (e.g. "জন্ম তারিখ ১৯৯০" must not become age=1990)."""
    assert _parse_age("আমার জন্মশন 18/05/1995") is None


# ─── B1/B7 Invariant 1 & 2: fast reply under cooldown must be processed ────
# (protects the exact b7a302d regression -- see
# modules.bridge_poller.process_bridge_inbound's cooldown gate)

async def test_fast_age_reply_saves_and_advances_step():
    """A candidate answering the active age question -- regardless of how
    quickly, this suite deliberately does not simulate the cooldown gate
    itself (that's process_bridge_inbound's job, covered in
    test_recruitment_cooldown_lead_creation_fix.py) -- must have their
    answer actually persisted and the step actually advanced. This is
    the deterministic core the cooldown fix protects."""
    session = _session("age")
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()) as mock_update, \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)) as mock_audit:
        result = await intake_message("8801700000101", "৩২", "bridge3")
    assert result["action"] == "collecting"
    # the update dict passed to the audited transition must contain the
    # parsed age and the advanced step -- not just a bare "no-op" reply
    audit_call_update = mock_audit.call_args.args[6]  # (phone, cid, step, msg, src, mid, update)
    assert audit_call_update["age"] == 32
    assert audit_call_update["collection_step"] == "area"


# ─── B1/B7 Invariant 3: already-answered field is never re-requested ───────

async def test_reconfirming_the_same_age_value_does_not_reprompt():
    """cf_R013: a candidate re-sending the SAME age value the session
    already has locked must be accepted silently and advanced, not
    re-prompted with the age-range message again."""
    session = _session("age", age=32)
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._advance_past_locked_step", new=AsyncMock(
             return_value={"reply": STEP_QUESTIONS_AREA, "action": "collecting", "session_id": 9001}
         )) as mock_advance:
        result = await intake_message("8801700000102", "৩২", "bridge3")
    mock_advance.assert_awaited_once()
    assert "বয়সসীমা" not in result["reply"]  # never the re-prompt text


STEP_QUESTIONS_AREA = "আপনি কোন জেলায় থাকেন? (বর্তমান ঠিকানা)"


async def test_step_never_regresses_to_age_once_advanced():
    """Once a session is at a later step (area), an ordinary answer to
    THAT step must never re-surface the age question -- proves the
    "asks the same question repeatedly" symptom cannot recur once a
    field is genuinely saved."""
    session = _session("area", age=32)
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()), \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)):
        result = await intake_message("8801700000103", "চট্টগ্রাম", "bridge3")
    assert "বয়স কত বছর" not in result["reply"]


# ─── B1/B7 Invariant 4: a semantic/Hermes failure must not silence the ─────
# ─── deterministic recruitment continuation ────────────────────────────────

async def test_intake_continues_deterministically_even_if_semantic_v2_raises():
    """Semantic V2 (modules.recruitment_turn_decision) is shadow-only and
    must never be load-bearing for whether a candidate gets a reply --
    if it raises, the deterministic STEP_QUESTIONS/intake_message() path
    must still produce a real next-step reply, never silence."""
    session = _session("age")
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()), \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._maybe_decide_v2", new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await intake_message("8801700000104", "৩৫", "bridge3")
    assert result["reply"]  # never empty/silent
    assert result["action"] == "collecting"


# ─── B1/B7 Invariant 6: a valid intake still reaches complete/scored ───────

async def test_completing_all_required_fields_reaches_scored():
    """The last collection step transitioning must compute a score, mark
    funnel_stage='scored', collection_step='complete', and return the
    candidate-facing completion message -- the full lifecycle Task A
    confirmed is otherwise silent (no further automated workflow)."""
    from modules.recruitment_flow import COLLECTION_STEPS, INTAKE_COMPLETE_MSG
    last_step = COLLECTION_STEPS[-1]
    session = _session(
        last_step,
        full_name="Test Candidate", age=32, area="Chattogram",
        job_preference="Security Guard", experience_years=2,
    )
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()), \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)) as mock_audit:
        result = await intake_message("8801700000105", "Yes, ready to join immediately", "bridge3")
    assert result["action"] == "scored"
    assert result["reply"] == INTAKE_COMPLETE_MSG
    final_update = mock_audit.call_args.args[6]
    assert final_update["funnel_stage"] == "scored"
    assert final_update["collection_step"] == "complete"
    assert isinstance(final_update["score"], int)


# ─── B1/B7 Invariant 3 & general: brand-new visitor unaffected ────────────

async def test_mixed_age_and_salary_question_saves_age_and_answers_salary():
    """Owner-mandated acceptance case (P0, 2026-08-25), live-verified in
    production against the real pipeline: "আমার বয়স ৩৬। বেতন কত?" must
    save age=36, answer the salary question from the approved knowledge
    source, AND continue the intake session -- not ask age again, not
    silently drop the question, not route to employee payroll merely
    because "বেতন" appears (recruitment_eligibility's active_session
    check already wins over any keyword before this function is ever
    reached -- see test_recruitment_salary_context_routing_fix.py)."""
    from modules.recruitment_flow import route_recruitment_message

    session = _session("age")
    salary_answer = "বেতন পদ অনুযায়ী — প্রশিক্ষণকালীন: ৯,০০০-১৫,০০০ টাকা।"

    # Scoped overrides on the REAL (already-initialized, real-default-valued)
    # settings singleton -- never fully replace get_settings() itself, so
    # any unrelated code path this call chain touches still sees every
    # other real field (avoids leaking a bare stub into later tests, a
    # real regression caught while writing this suite).
    from app.config import get_settings as _real_get_settings
    real_settings = _real_get_settings()

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()), \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)) as mock_audit, \
         patch.object(real_settings, "recruitment_answer_first_enabled", True), \
         patch.object(real_settings, "recruitment_structured_logging_enabled", False), \
         patch.object(real_settings, "recruitment_turn_decision_v2_enabled", False), \
         patch("modules.preflight_guard.is_feature_active", return_value=True), \
         patch(
             "modules.hermes_dispatch.build_recruitment_context_with_directives",
             new=AsyncMock(return_value="kb context"),
         ), \
         patch(
             "modules.hermes_dispatch.generate_recruitment_reply",
             new=AsyncMock(return_value=salary_answer),
         ), \
         patch("modules.hermes_dispatch._looks_like_fee_question", return_value=False):
        result = await route_recruitment_message(
            phone="8801700000107", text="আমার বয়স ৩৬। বেতন কত?", source="bridge3",
        )

    assert salary_answer in (result.get("reply") or "")
    assert "বয়স কত বছর" not in (result.get("reply") or "")  # never re-asks age
    # the intake side-effect (age saved) must still have happened
    audit_update = mock_audit.call_args.args[6]
    assert audit_update["age"] == 36


async def test_new_visitor_with_no_recruitment_signal_is_ignored_not_silenced_wrongly():
    """A genuinely unrelated first message must still be cleanly ignored
    (not crash, not silently create a bogus session) -- the counterpart
    proof that the golden baseline doesn't turn every message into a
    forced recruitment reply."""
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)):
        result = await intake_message("8801700000106", "কেমন আছেন?", "bridge3")
    assert result == {"reply": "", "action": "ignored", "session_id": None}
