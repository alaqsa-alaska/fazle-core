"""Regression tests — Owner Correction 1 / recruitment identity investigation
(2026-08-21), real case: sender 8801966011609 sent an explicit
"আমি কাজ করতে চাই" ("I want to work") 1 second after their first message,
well inside REPLY_COOLDOWN=60s. Live-confirmed on production: zero reply,
zero recruitment lead. Two independent bugs combined to cause this:

1. modules.bridge_poller.process_bridge_inbound() ran recruitment_eligibility()
   (and therefore any lead/session creation) AFTER its cooldown early-return —
   so a cooldown-suppressed message never even got a chance to open a
   recruitment session, not just never got a reply sent.

2. modules.recruitment_flow.intake_message()'s own new-session gate checked
   only the narrower INTAKE_KEYWORDS set, not the broader
   RECRUITMENT_TRIGGER_PHRASES / is_recruitment_trigger() superset that
   recruitment_eligibility() itself uses — so "কাজ করতে চাই" (which IS an
   explicit RECRUITMENT_TRIGGER_PHRASES entry) still silently no-opped
   ("action": "ignored") even when intake_message() was reached directly.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─── Bug 2: intake_message()'s new-session gate ────────────────────────────

async def test_intake_message_opens_session_for_explicit_trigger_phrase_not_in_intake_keywords():
    """'আমি কাজ করতে চাই' matches RECRUITMENT_TRIGGER_PHRASES but no bare
    INTAKE_KEYWORDS substring — must still open a new session, not be
    silently ignored."""
    from modules.recruitment_flow import intake_message, INTAKE_KEYWORDS

    text = "আমি কাজ করতে চাই"
    lower = text.lower()
    # Sanity-check the premise: no INTAKE_KEYWORDS entry is a substring of
    # this message (if this ever becomes false, the bug this test guards
    # against no longer exists via this exact phrase — the fix must still
    # hold via is_recruitment_trigger()'s broader check either way).
    assert not any(kw in lower for kw in INTAKE_KEYWORDS)

    create_session_mock = AsyncMock(return_value=101)
    ensure_candidate_mock = AsyncMock(return_value=55)
    audit_mock = AsyncMock(return_value=True)

    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._create_session", new=create_session_mock), \
         patch("modules.recruitment_flow._ensure_candidate_for_session", new=ensure_candidate_mock), \
         patch("modules.recruitment_flow._audit_candidate_conversation", new=audit_mock):
        result = await intake_message("8801966011609", text, "bridge3", role="unknown")

    create_session_mock.assert_awaited_once()
    assert result["action"] != "ignored"
    assert result["session_id"] == 101


async def test_intake_message_still_ignores_genuinely_unrelated_text():
    """A message with no recruitment signal at all (neither INTAKE_KEYWORDS
    nor RECRUITMENT_TRIGGER_PHRASES) must still be ignored — the fix widens
    the gate, it does not remove it."""
    from modules.recruitment_flow import intake_message

    create_session_mock = AsyncMock(return_value=101)
    with patch("modules.recruitment_flow._get_session", new=AsyncMock(return_value=None)), \
         patch("modules.recruitment_flow._create_session", new=create_session_mock):
        result = await intake_message("8801700000001", "কেমন আছেন?", "bridge3", role="unknown")

    create_session_mock.assert_not_awaited()
    assert result == {"reply": "", "action": "ignored", "session_id": None}


# ─── Bug 1: process_bridge_inbound()'s cooldown-vs-eligibility ordering ────

async def test_cooldown_suppressed_reply_still_opens_recruitment_funnel():
    """When _can_reply() is False (cooldown active) for a phone whose message
    is an explicit, autosend-eligible recruitment trigger with no existing
    session, process_bridge_inbound() must still open the funnel via
    intake_message() — it may suppress the SEND, never the bookkeeping."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {
        "eligible": True,
        "autosend": True,
        "reason": "explicit_recruitment",
        "active_session": None,
    }
    intake_mock = AsyncMock(return_value={"reply": "...", "action": "created", "session_id": 202})
    process_message_mock = AsyncMock(return_value=("should not be reached", None))

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=False)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="unknown"), \
         patch("modules.recruitment_flow.intake_message", new=intake_mock), \
         patch("modules.bridge_poller.process_message", new=process_message_mock):
        await process_bridge_inbound(
            "bridge3", "8801966011609", "আমি কাজ করতে চাই",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-1609",
        )

    intake_mock.assert_awaited_once_with(
        "8801966011609", "আমি কাজ করতে চাই", "bridge3", role="unknown", message_id="msg-1609",
        trigger_text="চাকরি",
    )
    # Cooldown must still fully suppress the reply/send path.
    process_message_mock.assert_not_awaited()


async def test_cooldown_suppressed_reply_does_not_open_funnel_when_session_already_active():
    """If a session is already open for this phone, the cooldown-path
    best-effort intake_message(trigger_text="চাকরি") FORCE-OPEN call must
    not fire again (idempotency — repeated messages must not create
    duplicate leads/sessions via that specific force-open mechanism)."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {
        "eligible": True,
        "autosend": True,
        "reason": "explicit_recruitment",
        "active_session": {"id": 202, "collection_step": "age"},
    }
    intake_mock = AsyncMock()
    # 2026-08-25 P0 fix: an active session now falls through to full
    # processing instead of returning early — process_message() must be
    # mocked here too so this test still only exercises the gate itself.
    process_message_mock = AsyncMock(return_value=("...", None))

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=False)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="unknown"), \
         patch("modules.recruitment_flow.intake_message", new=intake_mock), \
         patch("modules.bridge_poller.process_message", new=process_message_mock):
        await process_bridge_inbound(
            "bridge3", "8801966011609", "২৫",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-1609-2",
        )

    # The force-open trigger_text="চাকরি" path is specifically for opening
    # a NEW funnel and must not fire again for an already-active session.
    intake_mock.assert_not_awaited()


# ─── P0 production incident (2026-08-25): cooldown was dropping a direct  ──
# ─── answer to an active recruitment session entirely, not just its send ──

async def test_cooldown_blocked_active_session_still_gets_full_processing():
    """Real production bug: a candidate answering an active intake
    question (e.g. "আপনার বয়স কত বছর?" -> "32") within REPLY_COOLDOWN=60s
    of being asked previously got silently dropped — no field saved, no
    step advance, no reply, ever, for that turn. Live-confirmed: every
    checked real bridge3 case replied 13-50s after the question. This is
    a direct expected answer, not spam — full processing (which is what
    actually saves the field, advances the step, and sends the reply)
    must still run, not be skipped."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {
        "eligible": True,
        "autosend": True,
        "reason": "active_session_continuation",
        "active_session": {"id": 202, "collection_step": "age"},
    }
    process_message_mock = AsyncMock(return_value=("ধন্যবাদ। আপনার বয়স নোট করা হয়েছে।", None))

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=False)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="unknown"), \
         patch("modules.bridge_poller.process_message", new=process_message_mock):
        await process_bridge_inbound(
            "bridge3", "8801966011609", "৩২",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-age-1",
        )

    # The whole point of the fix: full processing (field save + step
    # advance + reply generation) must run instead of being skipped.
    process_message_mock.assert_awaited_once()


async def test_cooldown_blocked_no_session_still_returns_early_unchanged():
    """The exact prior behavior (no active session yet) must be completely
    unaffected by this fix — process_message() must still be skipped."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {
        "eligible": True,
        "autosend": True,
        "reason": "explicit_recruitment",
        "active_session": None,
    }
    intake_mock = AsyncMock(return_value={"reply": "...", "action": "created", "session_id": 202})
    process_message_mock = AsyncMock(return_value=("should not be reached", None))

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=False)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="unknown"), \
         patch("modules.recruitment_flow.intake_message", new=intake_mock), \
         patch("modules.bridge_poller.process_message", new=process_message_mock):
        await process_bridge_inbound(
            "bridge3", "8801700000099", "আমি কাজ করতে চাই",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-no-session",
        )

    process_message_mock.assert_not_awaited()


async def test_no_cooldown_active_session_unaffected():
    """When cooldown is NOT active at all, behavior (full processing) is
    completely unchanged by this fix regardless of active_session."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {
        "eligible": True,
        "autosend": True,
        "reason": "active_session_continuation",
        "active_session": {"id": 202, "collection_step": "age"},
    }
    process_message_mock = AsyncMock(return_value=("...", None))

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=True)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="unknown"), \
         patch("modules.bridge_poller.process_message", new=process_message_mock):
        await process_bridge_inbound(
            "bridge3", "8801966011609", "৩২",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-age-2",
        )

    process_message_mock.assert_awaited_once()


async def test_cooldown_suppressed_reply_does_not_open_funnel_when_not_eligible():
    """An ordinary, non-recruitment message suppressed by cooldown must not
    trigger any recruitment side effect at all."""
    from modules.bridge_poller import process_bridge_inbound

    decision = {"eligible": False, "autosend": False, "reason": "no_recruitment_context", "active_session": None}
    intake_mock = AsyncMock()

    with patch("modules.bridge_poller._can_reply", new=AsyncMock(return_value=False)), \
         patch("modules.bridge_poller.recruitment_eligibility", new=AsyncMock(return_value=decision)), \
         patch("modules.bridge_poller.classify_intent", return_value="greeting"), \
         patch("modules.recruitment_flow.intake_message", new=intake_mock):
        await process_bridge_inbound(
            "bridge3", "8801700000002", "Hello!",
            id_role="unknown", id_conf=0, id_name="", message_id="msg-hello",
        )

    intake_mock.assert_not_awaited()
