"""Post-intake recruitment conversation — unit tests (Owner directive,
2026-08-27, "Reuse existing architecture" implementation pass).

Covers:
  - modules.recruitment_flow._finalize_candidate() sets post_intake_status
  - modules.recruitment_flow.get_active_session()'s scored-candidate branch
  - modules.recruitment_flow.route_recruitment_message() routing a scored,
    non-terminal candidate into _route_post_intake_message()
  - modules.recruitment_flow._classify_post_intake_turn() semantic mapping
    (agreed / salary_objection / not_ready_now / future_contact / declined /
    none-on-ambiguity)
  - modules.recruitment_flow._resolve_fee_policy_reply() — directive-first,
    escalate-otherwise
  - modules.hermes_dispatch._build_post_intake_state_block()
  - modules.admin_directives.router.trigger_recruitment_policy_clarification()
    gating (flag off / already-resolved / cooldown)

Each existing DB/LLM boundary is mocked at the same seam the rest of this
test suite already mocks (app.database.fetch_one/fetch_val/execute,
modules.hermes_dispatch.call_decision_llm) — no real DB or network call.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from modules import hermes_dispatch
from modules import recruitment_flow as rf
from modules.admin_directives import router as admin_router


# ── _finalize_candidate ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_candidate_sets_awaiting_agreement():
    execute_mock = AsyncMock()
    with patch("modules.recruitment_flow.execute", new=execute_mock):
        await rf._finalize_candidate(42, {"full_name": "Karim", "age": 30, "score": 10, "score_bucket": "warm"})
    execute_mock.assert_awaited_once()
    sql = execute_mock.call_args.args[0]
    assert "post_intake_status = COALESCE(post_intake_status, 'awaiting_agreement')" in sql
    assert "funnel_stage = 'scored'" in sql


# ── get_active_session (scored branch) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_get_active_session_scored_non_terminal_is_active():
    session_row = {
        "id": 1, "phone": "8801000000003", "funnel_stage": "scored",
        "collection_step": None, "candidate_id": 42,
    }
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session_row)), \
         patch("modules.recruitment_flow.fetch_val", new=AsyncMock(return_value="salary_objection")):
        result = await rf.get_active_session("8801000000003")
    assert result is not None
    assert result["post_intake_active"] is True
    assert result["post_intake_status"] == "salary_objection"


@pytest.mark.asyncio
async def test_get_active_session_scored_null_status_defaults_to_awaiting_agreement():
    session_row = {"id": 1, "funnel_stage": "scored", "candidate_id": 42}
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session_row)), \
         patch("modules.recruitment_flow.fetch_val", new=AsyncMock(return_value=None)):
        result = await rf.get_active_session("8801000000004")
    assert result["post_intake_status"] == "awaiting_agreement"


@pytest.mark.asyncio
async def test_get_active_session_scored_declined_is_not_active():
    session_row = {"id": 1, "funnel_stage": "scored", "candidate_id": 42}
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session_row)), \
         patch("modules.recruitment_flow.fetch_val", new=AsyncMock(return_value="declined")):
        result = await rf.get_active_session("8801000000005")
    assert result is None


@pytest.mark.asyncio
async def test_get_active_session_scored_without_candidate_link_is_none():
    session_row = {"id": 1, "funnel_stage": "scored", "candidate_id": None}
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session_row)):
        result = await rf.get_active_session("8801000000006")
    assert result is None


@pytest.mark.asyncio
async def test_get_active_session_expired_stage_unaffected():
    """Non-collecting, non-scored stages (e.g. 'expired') stay None —
    byte-identical to pre-change behavior."""
    session_row = {"id": 1, "funnel_stage": "expired", "candidate_id": 42}
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=session_row)):
        result = await rf.get_active_session("8801000000007")
    assert result is None


# ── route_recruitment_message routes to the post-intake helper ──────────────

@pytest.mark.asyncio
async def test_route_recruitment_message_dispatches_post_intake():
    active_session = {
        "id": 1, "candidate_id": 42, "funnel_stage": "scored",
        "post_intake_active": True, "post_intake_status": "awaiting_agreement",
    }
    decision = {"eligible": True, "autosend": False, "reason": "active_session_continuation",
                "active_session": active_session}
    post_intake_mock = AsyncMock(return_value={
        "eligible": True, "autosend": False, "reason": "post_intake_conversation",
        "reply": "স্বাগতম, শর্তগুলো ব্যাখ্যা করছি...",
    })
    with patch("modules.recruitment_flow.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.recruitment_flow._route_post_intake_message", new=post_intake_mock):
        result = await rf.route_recruitment_message(
            "8801000000008", "হ্যাঁ আমি রাজি", source="bridge1",
        )
    post_intake_mock.assert_awaited_once()
    assert result["reply"] == "স্বাগতম, শর্তগুলো ব্যাখ্যা করছি..."
    assert result["reason"] == "post_intake_conversation"


@pytest.mark.asyncio
async def test_route_recruitment_message_does_not_call_intake_message_for_post_intake():
    """A scored/post-intake candidate must never run field-collection
    logic — there is nothing left to collect. Owner directive (2026-08-27,
    production-readiness pass, Step 6): when the primary Hermes path is
    unavailable, the post-intake branch must go straight to
    POST_INTAKE_FALLBACK_MSG, never modules.conversation_layer.
    generate_recruitment_reply() — that generic pre-intake reply generator
    doesn't know this candidate already answered name/age/area/job_
    preference/experience and can ask for them again, destroying context
    (live-confirmed against candidate 8801403472618 during Phase-A
    regeneration)."""
    active_session = {
        "id": 1, "candidate_id": 42, "funnel_stage": "scored",
        "post_intake_active": True, "post_intake_status": "awaiting_agreement",
    }
    decision = {"eligible": True, "autosend": False, "reason": "active_session_continuation",
                "active_session": active_session}
    intake_mock = AsyncMock()
    conversation_layer_mock = AsyncMock(return_value="ঠিক আছে")
    with patch("modules.recruitment_flow.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.recruitment_flow._classify_post_intake_turn",
               new=AsyncMock(return_value={"post_intake_status": None, "experience": None})), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=conversation_layer_mock), \
         patch("modules.recruitment_flow.intake_message", new=intake_mock):
        result = await rf.route_recruitment_message(
            "8801000000009", "কি কি কাগজ লাগবে?", source="bridge1",
        )
    intake_mock.assert_not_called()
    conversation_layer_mock.assert_not_called()  # the actual Step-6 assertion
    assert result["reply"] == rf.POST_INTAKE_FALLBACK_MSG


@pytest.mark.asyncio
async def test_route_post_intake_message_falls_back_when_all_reply_layers_empty():
    """Owner directive (2026-08-27, Step 6): the ONLY fallback for this
    branch is POST_INTAKE_FALLBACK_MSG — conversation_layer's generic
    reply generator is never consulted here (see the test above)."""
    active_session = {"id": 1, "candidate_id": 42, "post_intake_status": "awaiting_agreement"}
    conversation_layer_mock = AsyncMock(return_value=None)
    with patch("modules.recruitment_flow._classify_post_intake_turn",
               new=AsyncMock(return_value={"post_intake_status": None, "experience": None})), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=AsyncMock(return_value=True)), \
         patch("modules.recruitment_flow._resolve_fee_policy_reply", new=AsyncMock(return_value=None)), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.conversation_layer.generate_recruitment_reply", new=conversation_layer_mock):
        result = await rf._route_post_intake_message(
            "8801000000010", "...", source="bridge1", message_id=None,
            active_session=active_session, contact_context="", history="",
            decision={"autosend": False},
        )
    conversation_layer_mock.assert_not_called()
    assert result["reply"] == rf.POST_INTAKE_FALLBACK_MSG


# ── _classify_post_intake_turn ───────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("raw_json,expected", [
    ('{"post_intake_status": "agreed", "confidence": 0.9}', "agreed"),
    ('{"post_intake_status": "salary_objection", "confidence": 0.85}', "salary_objection"),
    ('{"post_intake_status": "not_ready_now", "confidence": 0.8}', "not_ready_now"),
    ('{"post_intake_status": "future_contact", "confidence": 0.75}', "future_contact"),
    ('{"post_intake_status": "declined", "confidence": 0.95}', "declined"),
    ('{"post_intake_status": "joining_ready", "confidence": 0.9}', "joining_ready"),
])
async def test_classify_post_intake_turn_maps_known_categories(raw_json, expected):
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw_json)):
        result = await rf._classify_post_intake_turn("8801000000011", "some reply")
    assert result["post_intake_status"] == expected
    assert result["experience"] is None


@pytest.mark.asyncio
async def test_classify_post_intake_turn_none_on_low_confidence():
    raw = '{"post_intake_status": "agreed", "confidence": 0.2}'
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        result = await rf._classify_post_intake_turn("8801000000012", "মনে হয় হ্যাঁ?")
    assert result["post_intake_status"] is None


@pytest.mark.asyncio
async def test_classify_post_intake_turn_none_on_explicit_none_category():
    raw = '{"post_intake_status": "none", "confidence": 0.9}'
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=raw)):
        result = await rf._classify_post_intake_turn("8801000000013", "অফিস কোথায়?")
    assert result["post_intake_status"] is None


@pytest.mark.asyncio
async def test_classify_post_intake_turn_never_guesses_on_unparsable_response():
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value="not json at all")):
        result = await rf._classify_post_intake_turn("8801000000014", "hmm")
    assert result["post_intake_status"] is None


@pytest.mark.asyncio
async def test_classify_post_intake_turn_none_when_llm_unavailable():
    with patch("modules.hermes_dispatch.call_decision_llm", new=AsyncMock(return_value=None)):
        result = await rf._classify_post_intake_turn("8801000000015", "হ্যাঁ রাজি")
    assert result["post_intake_status"] is None


# ── _resolve_fee_policy_reply ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fee_policy_reply_none_when_not_a_fee_question():
    result = await rf._resolve_fee_policy_reply("আজকে কি ডিউটি আছে?")
    assert result is None


@pytest.mark.asyncio
async def test_fee_policy_reply_uses_active_directive_when_present():
    directive_row = {"directive_text": "Owner-confirmed: কোনো অগ্রিম ফি নেই, বেতন থেকে কাটা হবে।"}
    escalate_mock = AsyncMock()
    with patch("modules.hermes_dispatch._looks_like_fee_question", return_value=True), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[directive_row])), \
         patch("modules.admin_directives.router.trigger_recruitment_policy_clarification", new=escalate_mock):
        result = await rf._resolve_fee_policy_reply("জয়েনিং ফি কত?")
    assert result == directive_row["directive_text"]
    escalate_mock.assert_not_called()


@pytest.mark.asyncio
async def test_fee_policy_reply_escalates_and_returns_safe_default_when_unresolved():
    escalate_mock = AsyncMock()
    with patch("modules.hermes_dispatch._looks_like_fee_question", return_value=True), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])), \
         patch("modules.admin_directives.router.trigger_recruitment_policy_clarification", new=escalate_mock):
        result = await rf._resolve_fee_policy_reply("ভর্তি ফি কত টাকা?")
    assert result == rf.SAFE_FEE_PENDING_OWNER_DECISION
    escalate_mock.assert_awaited_once()
    kwargs = escalate_mock.call_args.kwargs
    assert kwargs["subject_key"] == rf.RECRUITMENT_FEE_POLICY_SUBJECT_KEY


@pytest.mark.asyncio
async def test_fee_policy_reply_never_blocks_candidate_on_escalation_failure():
    with patch("modules.hermes_dispatch._looks_like_fee_question", return_value=True), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])), \
         patch("modules.admin_directives.router.trigger_recruitment_policy_clarification",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        result = await rf._resolve_fee_policy_reply("প্রসেসিং ফি লাগবে?")
    assert result == rf.SAFE_FEE_PENDING_OWNER_DECISION


# ── hermes_dispatch._build_post_intake_state_block ───────────────────────────

@pytest.mark.asyncio
async def test_build_post_intake_state_block_includes_stable_facts():
    session_row = {
        "candidate_id": 42, "full_name": "Karim Uddin", "age": 28,
        "area": "Chattogram", "job_preference": "Escort",
    }
    with patch("app.database.fetch_one", new=AsyncMock(return_value={"post_intake_status": "salary_objection"})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "Karim Uddin" in block
    assert "Chattogram" in block
    assert "Escort" in block
    assert "salary_objection" in block


@pytest.mark.asyncio
async def test_build_post_intake_state_block_empty_when_no_candidate_link():
    block = await hermes_dispatch._build_post_intake_state_block({"candidate_id": None})
    assert block == ""


@pytest.mark.asyncio
async def test_build_post_intake_state_block_empty_when_terminal():
    session_row = {"candidate_id": 42, "full_name": "X"}
    with patch("app.database.fetch_one", new=AsyncMock(return_value={"post_intake_status": "declined"})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert block == ""


@pytest.mark.asyncio
async def test_build_post_intake_state_block_covers_trust_already_applied_and_ack_guidance():
    """Owner directive (2026-08-27): the rule text must explicitly cover
    the three observed quality gaps -- (1) never asking an already-scored
    candidate whether they want to START applying, (2) grounding a trust/
    fraud question in Knowledge facts instead of a bare "I am real"
    assertion, (3) treating a short acknowledgment (ওকে/ধন্যবাদ/জী
    ইনশাআল্লাহ) as a continuation cue, not a conversation-ending signal."""
    session_row = {"candidate_id": 42, "full_name": "Karim"}
    with patch("app.database.fetch_one", new=AsyncMock(return_value={"post_intake_status": "awaiting_agreement"})):
        block = await hermes_dispatch._build_post_intake_state_block(session_row)
    assert "আবেদন প্রক্রিয়া শুরু করতে রাজি" in block  # names the forbidden phrasing
    assert "রিয়েল" in block  # names the forbidden bare trust-claim
    assert "রশিদ" in block  # points to the actual grounding fact to use instead
    assert "ওকে" in block and "জী ইনশাআল্লাহ" in block  # names the ack examples
    assert "কথোপকথন শেষ করার সংকেত হিসেবে" in block  # the core instruction: don't treat as end-of-conversation


@pytest.mark.asyncio
async def test_session_state_block_dispatches_to_post_intake_for_scored():
    row = {
        "candidate_id": 42, "full_name": "Karim", "age": 28, "area": None,
        "job_preference": None, "experience_years": None, "confirmed_phone": None,
        "collection_step": None, "funnel_stage": "scored",
    }
    with patch("app.database.fetch_one", new=AsyncMock(side_effect=[row, {"post_intake_status": "agreed"}])):
        block = await hermes_dispatch._build_recruitment_session_state_block("8801000000016")
    assert "agreed" in block
    assert "Karim" in block


# ── admin_directives.router.trigger_recruitment_policy_clarification ────────

@pytest.mark.asyncio
async def test_policy_clarification_skipped_when_flag_disabled():
    settings = type("S", (), {
        "recruitment_policy_escalation_enabled": False,
        "hermes_runner_secret": "x", "admin_bridge2_number": "8801900000000",
        "hermes_alert_investigation_cooldown_seconds": 3600,
    })()
    enqueue_mock = AsyncMock()
    with patch("modules.admin_directives.router.get_settings", return_value=settings), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_recruitment_policy_clarification(
            subject_type="recruitment_policy", subject_key="recruitment_joining_fee",
            conflict_summary="conflict",
        )
    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_policy_clarification_skipped_when_already_resolved():
    settings = type("S", (), {
        "recruitment_policy_escalation_enabled": True,
        "hermes_runner_secret": "x", "admin_bridge2_number": "8801900000000",
        "hermes_alert_investigation_cooldown_seconds": 3600,
    })()
    enqueue_mock = AsyncMock()
    admin_router._policy_clarification_cooldown.clear()
    with patch("modules.admin_directives.router.get_settings", return_value=settings), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[{"id": 1}])), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_recruitment_policy_clarification(
            subject_type="recruitment_policy", subject_key="recruitment_joining_fee",
            conflict_summary="conflict",
        )
    enqueue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_policy_clarification_sends_and_remembers_session():
    settings = type("S", (), {
        "recruitment_policy_escalation_enabled": True,
        "hermes_runner_secret": "x", "admin_bridge2_number": "8801900000000",
        "hermes_alert_investigation_cooldown_seconds": 3600,
    })()
    enqueue_mock = AsyncMock()
    admin_router._policy_clarification_cooldown.clear()
    with patch("modules.admin_directives.router.get_settings", return_value=settings), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])), \
         patch("modules.admin_directives.router._call_hermes_readonly",
               new=AsyncMock(return_value=("প্রশ্নটি Boss-কে পাঠানো হয়েছে।", "sess-123"))), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_recruitment_policy_clarification(
            subject_type="recruitment_policy", subject_key="recruitment_joining_fee",
            conflict_summary="conflict",
        )
    enqueue_mock.assert_awaited_once()
    assert admin_router._get_active_hermes_session("bridge2", "8801900000000") == "sess-123"


@pytest.mark.asyncio
async def test_policy_clarification_respects_cooldown():
    settings = type("S", (), {
        "recruitment_policy_escalation_enabled": True,
        "hermes_runner_secret": "x", "admin_bridge2_number": "8801900000000",
        "hermes_alert_investigation_cooldown_seconds": 3600,
    })()
    admin_router._policy_clarification_cooldown.clear()
    admin_router._policy_clarification_cooldown["recruitment_joining_fee"] = time.monotonic()
    enqueue_mock = AsyncMock()
    with patch("modules.admin_directives.router.get_settings", return_value=settings), \
         patch("modules.admin_directives.list_directives", new=AsyncMock(return_value=[])), \
         patch("modules.admin_directives.router.outbound.enqueue", new=enqueue_mock):
        await admin_router.trigger_recruitment_policy_clarification(
            subject_type="recruitment_policy", subject_key="recruitment_joining_fee",
            conflict_summary="conflict",
        )
    enqueue_mock.assert_not_called()
    admin_router._policy_clarification_cooldown.clear()
