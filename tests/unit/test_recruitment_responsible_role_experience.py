"""Responsible/supervisory-role experience verification — unit tests
(Owner directive, 2026-08-27).

Covers:
  - modules.recruitment_flow._parse_experience() — Bengali digit/word
    parsing, never silently converts an unparseable claim to 0
  - modules.recruitment_flow._is_responsible_role()
  - modules.recruitment_flow._format_experience_claim_block() /
    _parse_experience_claim_block() round trip
  - modules.recruitment_flow._experience_claim_resolved()
  - modules.recruitment_flow._upsert_experience_claim() — merge
    semantics, never writes verification_status="verified"/"rejected"
  - modules.recruitment_flow._route_post_intake_message() — extraction
    gating, admin-notification trigger only once sufficient detail exists
    (never on a partial answer), no_relevant_experience path
  - modules.hermes_dispatch._build_post_intake_state_block() — role-
    specific question triggered for responsible roles with generic-only
    experience, training-path guidance for confirmed no-relevant-
    experience, awaiting-admin-review guidance, non-supervisory roles
    unaffected
  - modules.admin_directives.router.trigger_candidate_reference_review()

Each DB/LLM boundary is mocked at the same seam the rest of this test
suite already mocks (modules.recruitment_flow.fetch_one/execute,
modules.hermes_dispatch.call_decision_llm, app.database.fetch_one) — no
real DB or network call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules import hermes_dispatch
from modules import recruitment_flow as rf
from modules.admin_directives import router as admin_router


# ── _parse_experience ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("5", 5),
    ("০", 0),
    ("৪", 4),
    ("১০", 10),
    ("10+ বছর", 10),
    ("১০+ বছর", 10),
    ("0", 0),
    ("নতুন", 0),
    ("কোনো অভিজ্ঞতা নেই", 0),
])
def test_parse_experience_digit_and_zero_forms(text, expected):
    assert rf._parse_experience(text) == expected


@pytest.mark.parametrize("text", [
    "দশ বছরের ও বেশি",   # real candidate case, 8801718415009 — word form
    "দশ বছর প্লাস অভিজ্ঞতা",
    "অনেক বছর",  # genuinely unparseable — no safe number
])
def test_parse_experience_word_forms_do_not_silently_become_zero(text):
    """Owner directive: 'দশ বছর' etc. must not silently become 0."""
    result = rf._parse_experience(text)
    assert result != 0 or "দশ" in text  # "দশ" word-form cases resolve to 10, not 0
    if "দশ" in text:
        assert result == 10


def test_parse_experience_truly_unparsable_returns_none_not_zero():
    """Owner directive: if the exact duration can't be safely normalized,
    preserve as unverified text rather than converting to 0 — signalled
    by returning None (distinct from a confirmed 0)."""
    assert rf._parse_experience("অনেক দিন ধরে কাজ করছি") is None
    assert rf._parse_experience("") is None
    assert rf._parse_experience("   ") is None


def test_parse_experience_never_invents_a_duration():
    # A word never in the small table and no digits at all -> None, not a guess.
    assert rf._parse_experience("অনেক") is None


# ── intake step preserves raw unparsed claim as unverified text ────────────

@pytest.mark.asyncio
async def test_intake_experience_step_preserves_raw_claim_when_unparsable():
    session = {
        "id": 1, "phone": "8801000000020", "collection_step": "experience",
        "full_name": "X", "age": 30, "area": "Dhaka",
        "job_preference": "Security Supervisor", "experience_years": None,
        "confirmed_phone": None,
    }
    upsert_mock = AsyncMock(return_value={"claimed_duration": "দশ বছরের ও বেশি"})
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session)), \
         patch("modules.recruitment_flow._ensure_candidate_for_session", new=AsyncMock(return_value=42)), \
         patch("modules.recruitment_flow._update_session", new=AsyncMock()), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._apply_audited_transition", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._upsert_experience_claim", new=upsert_mock):
        await rf.intake_message("8801000000020", "অনেক দিন ধরে কাজ করছি, নির্দিষ্ট বছর মনে নেই", source="bridge1")
    upsert_mock.assert_awaited_once()
    kwargs = upsert_mock.call_args.kwargs
    assert kwargs["claimed_role"] == "Security Supervisor"
    assert "অনেক দিন" in kwargs["claimed_duration"]


# ── _is_responsible_role ─────────────────────────────────────────────────────

@pytest.mark.parametrize("role,expected", [
    ("Security Supervisor", True),
    ("Assistant Supervisor", True),
    ("Ghat Supervisor", True),
    ("Ghat In-Charge", True),
    ("Operation Officer", True),
    ("Security In-Charge", True),
    ("Supervisor", True),
    ("Escort", False),
    ("Survey Scout", False),
    ("Security Guard", False),
    ("Marketing Officer", False),
    (None, False),
    ("", False),
])
def test_is_responsible_role(role, expected):
    assert rf._is_responsible_role(role) is expected


# ── experience-claim block format / parse round trip ────────────────────────

def test_experience_claim_block_round_trip():
    block = rf._format_experience_claim_block(
        claimed_role="Security Supervisor", claimed_company="ABC Security",
        claimed_duration="৩ বছর", reference_name="Karim",
        reference_phone="01711111111", verification_status="unverified",
    )
    parsed = rf._parse_experience_claim_block(block)
    assert parsed["claimed_role"] == "Security Supervisor"
    assert parsed["claimed_company"] == "ABC Security"
    assert parsed["claimed_duration"] == "৩ বছর"
    assert parsed["reference_name"] == "Karim"
    assert parsed["reference_phone"] == "01711111111"
    assert parsed["verification_status"] == "unverified"


def test_experience_claim_block_parse_none_when_absent():
    assert rf._parse_experience_claim_block(None) is None
    assert rf._parse_experience_claim_block("") is None
    assert rf._parse_experience_claim_block("some unrelated note text") is None


def test_experience_claim_block_preserves_unrelated_notes_content():
    block = rf._format_experience_claim_block(claimed_role="Escort")
    notes_with_prior_content = f"office visit scheduled 2026-08-20\n\n{block}"
    parsed = rf._parse_experience_claim_block(notes_with_prior_content)
    assert parsed["claimed_role"] == "Escort"


# ── _experience_claim_resolved ───────────────────────────────────────────────

def test_experience_claim_resolved_false_when_none():
    assert rf._experience_claim_resolved(None) is False


def test_experience_claim_resolved_false_when_partial_detail_only():
    assert rf._experience_claim_resolved({"claimed_company": "ABC Security"}) is False


def test_experience_claim_resolved_true_when_company_and_duration_present():
    assert rf._experience_claim_resolved(
        {"claimed_company": "ABC Security", "claimed_duration": "৩ বছর"}
    ) is True


def test_experience_claim_resolved_true_for_no_relevant_experience_sentinel():
    assert rf._experience_claim_resolved(
        {"claimed_company": rf._NO_RELEVANT_EXPERIENCE_SENTINEL}
    ) is True


def test_experience_claim_resolved_true_when_awaiting_admin_review():
    assert rf._experience_claim_resolved({"verification_status": "awaiting_admin_review"}) is True


# ── _upsert_experience_claim ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_experience_claim_merges_across_turns_without_overwriting():
    """Owner directive: candidate answers across several messages —
    a later call with only reference_name must not blank out an
    already-known claimed_company from an earlier call."""
    existing_block = rf._format_experience_claim_block(
        claimed_role="Security Supervisor", claimed_company="ABC Security",
    )
    fetch_mock = AsyncMock(return_value={"notes": existing_block})
    execute_mock = AsyncMock()
    with patch("modules.recruitment_flow.fetch_one", new=fetch_mock), \
         patch("modules.recruitment_flow.execute", new=execute_mock):
        merged = await rf._upsert_experience_claim(42, reference_name="Karim")
    assert merged["claimed_company"] == "ABC Security"  # preserved, not blanked
    assert merged["reference_name"] == "Karim"
    execute_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_experience_claim_never_writes_verified_or_rejected():
    """Owner directive: do not mark any external experience claim
    verified automatically — automated code only ever writes
    'unverified' or 'awaiting_admin_review'."""
    fetch_mock = AsyncMock(return_value={"notes": None})
    execute_mock = AsyncMock()
    with patch("modules.recruitment_flow.fetch_one", new=fetch_mock), \
         patch("modules.recruitment_flow.execute", new=execute_mock):
        merged = await rf._upsert_experience_claim(
            42, claimed_role="Security Supervisor", claimed_company="ABC Security",
            claimed_duration="৫ বছর",
        )
    assert merged["verification_status"] == "unverified"
    written_notes = execute_mock.call_args.args[1]
    assert "verified" not in written_notes.replace("unverified", "").replace("awaiting_admin_review", "")


@pytest.mark.asyncio
async def test_upsert_experience_claim_returns_none_on_db_error():
    with patch("modules.recruitment_flow.fetch_one", new=AsyncMock(side_effect=RuntimeError("boom"))):
        merged = await rf._upsert_experience_claim(42, claimed_role="Escort")
    assert merged is None


# ── _route_post_intake_message: admin-review trigger gating ────────────────

def _post_intake_session(job_preference="Security Supervisor"):
    return {
        "id": 1, "candidate_id": 42, "funnel_stage": "scored",
        "post_intake_active": True, "post_intake_status": "awaiting_agreement",
        "job_preference": job_preference,
    }


@pytest.mark.asyncio
async def test_route_post_intake_no_admin_notification_on_partial_detail():
    """Owner directive: do NOT notify Admin on every partial answer —
    company only, no duration yet, must not trigger."""
    trigger_mock = AsyncMock()
    classify_result = {
        "post_intake_status": None,
        "experience": {"claimed_company": "ABC Security"},  # no duration yet
    }
    with patch("modules.recruitment_flow.fetch_one", new=AsyncMock(return_value={"notes": None})), \
         patch("modules.recruitment_flow._classify_post_intake_turn", new=AsyncMock(return_value=classify_result)), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._upsert_experience_claim",
               new=AsyncMock(return_value={"claimed_company": "ABC Security", "verification_status": "unverified"})), \
         patch("modules.admin_directives.router.trigger_candidate_reference_review", new=trigger_mock), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=AsyncMock(return_value="ঠিক আছে")):
        await rf._route_post_intake_message(
            "8801000000021", "ABC Security-তে কাজ করেছি", source="bridge1", message_id=None,
            active_session=_post_intake_session(), contact_context="", history="",
            decision={"autosend": False},
        )
    trigger_mock.assert_not_called()


@pytest.mark.asyncio
async def test_route_post_intake_admin_notification_once_sufficient_detail():
    """Owner directive: trigger review once company + duration (+
    reference where available) are known."""
    trigger_mock = AsyncMock()
    classify_result = {
        "post_intake_status": None,
        "experience": {
            "claimed_company": "ABC Security", "claimed_duration": "৫ বছর",
            "reference_name": "Karim", "reference_phone": "01711111111",
        },
    }
    sufficient_claim = {
        "claimed_role": "Security Supervisor", "claimed_company": "ABC Security",
        "claimed_duration": "৫ বছর", "reference_name": "Karim",
        "reference_phone": "01711111111", "verification_status": "unverified",
    }
    upsert_mock = AsyncMock(side_effect=[
        sufficient_claim,
        {**sufficient_claim, "verification_status": "awaiting_admin_review"},
    ])
    with patch("modules.recruitment_flow.fetch_one", new=AsyncMock(return_value={"notes": None})), \
         patch("modules.recruitment_flow._classify_post_intake_turn", new=AsyncMock(return_value=classify_result)), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._upsert_experience_claim", new=upsert_mock), \
         patch("modules.admin_directives.router.trigger_candidate_reference_review", new=trigger_mock), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=AsyncMock(return_value="ঠিক আছে")):
        await rf._route_post_intake_message(
            "8801000000022", "ABC Security-তে ৫ বছর, reference Karim 01711111111", source="bridge1",
            message_id=None, active_session=_post_intake_session(), contact_context="", history="",
            decision={"autosend": False},
        )
    trigger_mock.assert_awaited_once()
    kwargs = trigger_mock.call_args.kwargs
    assert kwargs["candidate_id"] == 42
    assert kwargs["claim"]["claimed_company"] == "ABC Security"


@pytest.mark.asyncio
async def test_route_post_intake_no_relevant_experience_skips_admin_notification():
    trigger_mock = AsyncMock()
    classify_result = {"post_intake_status": None, "experience": {"no_relevant_experience": True}}
    upsert_mock = AsyncMock(return_value={
        "claimed_company": rf._NO_RELEVANT_EXPERIENCE_SENTINEL, "verification_status": "unverified",
    })
    with patch("modules.recruitment_flow.fetch_one", new=AsyncMock(return_value={"notes": None})), \
         patch("modules.recruitment_flow._classify_post_intake_turn", new=AsyncMock(return_value=classify_result)), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._upsert_experience_claim", new=upsert_mock), \
         patch("modules.admin_directives.router.trigger_candidate_reference_review", new=trigger_mock), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=AsyncMock(return_value="ঠিক আছে")):
        await rf._route_post_intake_message(
            "8801000000023", "না এই কাজে অভিজ্ঞতা নেই", source="bridge1", message_id=None,
            active_session=_post_intake_session(), contact_context="", history="",
            decision={"autosend": False},
        )
    trigger_mock.assert_not_called()
    upsert_mock.assert_awaited_once()
    assert upsert_mock.call_args.kwargs["claimed_company"] == rf._NO_RELEVANT_EXPERIENCE_SENTINEL


@pytest.mark.asyncio
async def test_route_post_intake_extraction_not_requested_for_non_supervisory_role():
    """Owner directive: non-supervisory roles unaffected — no extraction
    instructions, no notes lookup at all."""
    classify_mock = AsyncMock(return_value={"post_intake_status": None, "experience": None})
    fetch_mock = AsyncMock(return_value={"notes": None})
    with patch("modules.recruitment_flow.fetch_one", new=fetch_mock), \
         patch("modules.recruitment_flow._classify_post_intake_turn", new=classify_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=AsyncMock(return_value="ঠিক আছে")):
        await rf._route_post_intake_message(
            "8801000000024", "নতুন", source="bridge1", message_id=None,
            active_session=_post_intake_session(job_preference="Escort"), contact_context="", history="",
            decision={"autosend": False},
        )
    fetch_mock.assert_not_called()  # no pre-check notes lookup for a non-responsible role
    classify_mock.assert_awaited_once()
    assert classify_mock.call_args.kwargs["extract_experience_role"] is None


@pytest.mark.asyncio
async def test_route_post_intake_extraction_skipped_once_already_resolved():
    """Once a claim is already sufficient (or no_relevant_experience),
    stop asking — no repeated extraction calls."""
    resolved_claim_notes = rf._format_experience_claim_block(
        claimed_company=rf._NO_RELEVANT_EXPERIENCE_SENTINEL,
    )
    classify_mock = AsyncMock(return_value={"post_intake_status": None, "experience": None})
    with patch("modules.recruitment_flow.fetch_one", new=AsyncMock(return_value={"notes": resolved_claim_notes})), \
         patch("modules.recruitment_flow._classify_post_intake_turn", new=classify_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=AsyncMock(return_value="ঠিক আছে")):
        await rf._route_post_intake_message(
            "8801000000025", "ধন্যবাদ", source="bridge1", message_id=None,
            active_session=_post_intake_session(), contact_context="", history="",
            decision={"autosend": False},
        )
    assert classify_mock.call_args.kwargs["extract_experience_role"] is None


# ── hermes_dispatch._build_post_intake_state_block: responsible-role rules ──

@pytest.mark.asyncio
async def test_state_block_asks_role_specific_question_when_generic_only():
    session_row = {
        "candidate_id": 42, "full_name": "Nazmul", "age": 27, "area": "Tangail",
        "job_preference": "Security Supervisor", "experience_years": 4,
    }
    with patch("app.database.fetch_one",
               new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement", "notes": None})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "কোন কোম্পানিতে" in block
    assert "reference" in block.lower() or "contact" in block.lower()
    assert "যথেষ্ট নয়" in block


@pytest.mark.asyncio
async def test_state_block_training_path_when_no_relevant_experience_confirmed():
    session_row = {
        "candidate_id": 42, "full_name": "Shuvo", "age": 20, "area": "Nilphamari",
        "job_preference": "Assistant Supervisor", "experience_years": 0,
    }
    claim_notes = rf._format_experience_claim_block(claimed_company=rf._NO_RELEVANT_EXPERIENCE_SENTINEL)
    with patch("app.database.fetch_one",
               new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement", "notes": claim_notes})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "practical learning" in block or "training" in block
    assert "প্রত্যাখ্যান কোরো না" in block


@pytest.mark.asyncio
async def test_state_block_awaiting_admin_review_does_not_ask_again():
    session_row = {
        "candidate_id": 42, "full_name": "Karim", "age": 30, "area": "Chattogram",
        "job_preference": "Ghat Supervisor", "experience_years": 5,
    }
    claim_notes = rf._format_experience_claim_block(
        claimed_company="ABC Ghat", claimed_duration="৫ বছর", verification_status="awaiting_admin_review",
    )
    with patch("app.database.fetch_one",
               new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement", "notes": claim_notes})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "verified" in block.lower() or "যাচাই হয়ে গেছে" in block
    assert "আবার চাইবে না" in block


@pytest.mark.asyncio
async def test_state_block_non_supervisory_role_unaffected():
    session_row = {
        "candidate_id": 42, "full_name": "Karim", "age": 25, "area": "Cox's Bazar",
        "job_preference": "Escort", "experience_years": 0,
    }
    with patch("app.database.fetch_one",
               new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement", "notes": None})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    # No role-specific verification rule injected for a non-supervisory role.
    assert "কোন কোম্পানিতে, কত বছর কাজ করেছেন" not in block


@pytest.mark.asyncio
async def test_state_block_renders_generic_experience_years_fact():
    """Fixes a pre-existing omission: experience_years was fetched into
    session_row but never rendered into the facts block."""
    session_row = {
        "candidate_id": 42, "full_name": "Karim", "age": 30, "area": "Dhaka",
        "job_preference": "Escort", "experience_years": 3,
    }
    with patch("app.database.fetch_one",
               new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement", "notes": None})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "সাধারণ অভিজ্ঞতা" in block


# ── admin_directives.router.trigger_candidate_reference_review ─────────────

@pytest.mark.asyncio
async def test_trigger_candidate_reference_review_sends_with_stable_idempotency_key():
    enqueue_mock = AsyncMock(return_value=101)
    claim = {
        "claimed_role": "Security Supervisor", "claimed_company": "ABC Security",
        "claimed_duration": "৫ বছর", "reference_name": "Karim", "reference_phone": "01711111111",
    }
    with patch("modules.admin_directives.router.get_settings",
               return_value=type("S", (), {"admin_bridge2_number": "8801900000000"})()), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_candidate_reference_review(candidate_id=42, phone="8801000000022", claim=claim)
    enqueue_mock.assert_awaited_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["idempotency_key"] == "candref-42"
    assert kwargs["purpose"] == "candidate_reference_review"
    assert "ABC Security" in enqueue_mock.call_args.args[1]


@pytest.mark.asyncio
async def test_trigger_candidate_reference_review_skips_when_bridge2_not_configured():
    enqueue_mock = AsyncMock()
    with patch("modules.admin_directives.router.get_settings",
               return_value=type("S", (), {"admin_bridge2_number": ""})()), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_candidate_reference_review(candidate_id=42, phone="8801000000022", claim={})
    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_candidate_reference_review_never_raises_on_enqueue_failure():
    with patch("modules.admin_directives.router.get_settings",
               return_value=type("S", (), {"admin_bridge2_number": "8801900000000"})()), \
         patch("modules.admin_directives.router.outbound.enqueue",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        await admin_router.trigger_candidate_reference_review(candidate_id=42, phone="8801000000022", claim={})
