"""Regression tests -- urgent Facebook-boost recruitment recovery pass
(2026-08-21). Two real, live-confirmed gaps in modules.recruitment_flow.
recruitment_eligibility():

1. A candidate with an active collecting session whose reply doesn't
   happen to contain a recruitment keyword (a bare district name, "Any
   post", an age/height line) fell OUT of the funnel mid-conversation and
   got answered by Route B's generic reply instead -- no lead/session
   update. Confirmed live: a real bridge3 candidate ("Abuhanif") held a
   complete conversation (name/age/height/education/position all given)
   entirely outside the funnel, zero DB rows, because no single message
   after the trigger happened to match a keyword.

2. bridge3 is a dedicated, single-purpose recruitment channel (label
   "REC2", no cross-bridge fallback by design) whose actual traffic today
   is dominated by the Meta/Facebook Click-to-WhatsApp ad opener text
   ("Hello! Can I get more info on this?") -- a Meta-generated template
   that matches no recruitment keyword or detector layer. Confirmed live:
   11 of 12 real bridge3 senders today opened with this exact pattern;
   the narrow classifier only recognized 4 of them as recruitment-eligible.

Also covers the companion fix: route_recruitment_message()/bridge_poller's
best-effort intake_message() calls now pass trigger_text to bypass
intake_message()'s own narrower re-derivation of eligibility, since by the
time those calls are reached, recruitment_eligibility() already made the
authoritative call.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─── Bug 1: active-session continuation must not re-require a keyword ──────

async def test_active_session_free_text_reply_is_eligible_without_a_keyword():
    from modules.recruitment_flow import recruitment_eligibility

    session = {"id": 99, "collection_step": "area", "funnel_stage": "collecting"}
    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=session)):
        decision = await recruitment_eligibility(
            "8801728129709", "Sylhet Sunamganj  teke kora jbe",
            role="unknown", intent="unknown", source="bridge3",
        )
    assert decision["eligible"] is True
    assert decision["reason"] == "active_session_continuation"
    assert decision["active_session"] == session


async def test_active_session_continuation_stays_draft_only():
    """Session-existing alone is the trust signal, not full autosend --
    matches the old session_followup_draft behavior this supersedes."""
    from modules.recruitment_flow import recruitment_eligibility

    session = {"id": 99, "collection_step": "name", "funnel_stage": "collecting"}
    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=session)):
        decision = await recruitment_eligibility(
            "8801700000001", "Any post", role="unknown", intent="unknown",
        )
    assert decision["eligible"] is True
    assert decision["autosend"] is False


# ─── Bug 2: bridge3 dedicated-channel rule ──────────────────────────────────

async def test_bridge3_new_sender_meta_ad_opener_is_eligible():
    from modules.recruitment_flow import recruitment_eligibility

    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_detector.detect_recruitment_intent",
               new=AsyncMock(return_value={"is_recruitment": False, "layer": "none"})):
        decision = await recruitment_eligibility(
            "8801728129709", "Hello! Can I get more info on this?",
            role="unknown", intent="unknown", source="bridge3",
        )
    assert decision["eligible"] is True
    assert decision["reason"] == "bridge3_dedicated_channel"
    assert decision["autosend"] is True


async def test_same_generic_greeting_on_bridge1_is_not_auto_eligible():
    """Scoped to bridge3 only -- a generic 'hello, can you help me' on
    bridge1/bridge2 is genuinely ambiguous, not a recruitment-line signal."""
    from modules.recruitment_flow import recruitment_eligibility

    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_detector.detect_recruitment_intent",
               new=AsyncMock(return_value={"is_recruitment": False, "layer": "none"})):
        decision = await recruitment_eligibility(
            "8801700000002", "Hello! Can I get more info on this?",
            role="unknown", intent="unknown", source="bridge1",
        )
    assert decision["eligible"] is False
    assert decision["reason"] == "no_recruitment_context"


async def test_bridge3_still_excludes_operational_roles():
    """The dedicated-channel rule never overrides the role gate -- an
    admin/employee message on bridge3 is still rejected first."""
    from modules.recruitment_flow import recruitment_eligibility

    decision = await recruitment_eligibility(
        "8801700000003", "Hello! Can I get more info on this?",
        role="admin", intent="unknown", source="bridge3",
    )
    assert decision["eligible"] is False
    assert decision["reason"] == "operational_identity"


async def test_bridge3_explicit_trigger_still_reports_its_own_reason():
    """An explicit keyword match on bridge3 keeps its more specific,
    pre-existing reason label rather than being swallowed by the new
    bridge3 fallback."""
    from modules.recruitment_flow import recruitment_eligibility

    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)):
        decision = await recruitment_eligibility(
            "8801700000004", "আমি কাজ করতে চাই",
            role="unknown", intent="unknown", source="bridge3",
        )
    assert decision["eligible"] is True
    assert decision["reason"] == "explicit_recruitment"


# ─── Companion fix: intake_message() gate bypass in route_recruitment_message ──

async def test_new_bridge3_session_actually_opens_for_a_non_keyword_opener():
    """End-to-end: route_recruitment_message() for a brand-new bridge3
    sender whose message is the Meta ad opener text must actually create
    a session, not silently no-op inside intake_message()'s own narrower
    gate (the exact live-confirmed 'Abuhanif' bug)."""
    from modules.recruitment_flow import route_recruitment_message

    create_session_mock = AsyncMock(return_value=501)
    ensure_candidate_mock = AsyncMock(return_value=77)
    audit_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_detector.detect_recruitment_intent",
               new=AsyncMock(return_value={"is_recruitment": False, "layer": "none"})), \
         patch("modules.conversation_layer.generate_recruitment_reply",
               new=AsyncMock(return_value="Hello, thanks for reaching out -- what position interests you?")), \
         patch("modules.preflight_guard.is_feature_active", return_value=False), \
         patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._create_session", new=create_session_mock), \
         patch("modules.recruitment_flow._ensure_candidate_for_session", new=ensure_candidate_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock):
        result = await route_recruitment_message(
            phone="8801728129709",
            text="Hello! Can I get more info on this?",
            source="bridge3",
            role="unknown",
            intent="unknown",
        )

    assert result["eligible"] is True
    create_session_mock.assert_awaited_once()
    assert result["reply"]
