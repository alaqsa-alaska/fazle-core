"""
Bridge1 Hermes Control Channel — keyword-shortcut bypass fix (2026-08-13).

Found during a live hardening pass, with real log evidence (not
speculative): modules.message_router.process_message()'s ADMIN branch has
older, loose substring keyword shortcuts ("draft"/"payment"/"attendance"
etc., see get_attendance_summary()/list_payment_drafts()/_cmd_admin_list())
that run BEFORE process_admin_natural_query()'s own bridge1/bridge2 Hermes
bypass. Any Admin message on the Hermes control channel that happened to
contain one of those common words (e.g. "আজকের attendance record...",
"employee attendance এবং payroll...") never reached Hermes at all -- it
was answered by the older handler instead, and on bridge1 specifically
that reply then hit bridge_poller's pre-existing (2026-08-04) cross-bridge
LOOP_GUARD, which diverted it to a silent manual-review draft instead of
sending -- so the Admin saw no reply whatsoever. This fully explains 2
requests (test ids 1b, 14a) the 2026-08-13 capability assessment reported
as unexplained hangs.

Fix: for the Admin's own phone on a dedicated Hermes control channel
(bridge1 or bridge2), skip straight to process_admin_natural_query()
BEFORE the keyword shortcuts, mirroring the precedence bridge2 self-chat
already has by construction (it never reaches this keyword-shortcut block
at all -- see bridge_poller's separate is_from_me=1 handling). Every other
sender/channel is unaffected; these tests confirm both halves.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

# The exact two phrasings that silently vanished in the original incident.
ATTENDANCE_TEXT_BN = "সাম্প্রতিক attendance record থেকে আজ কতজন employee উপস্থিত ছিল?"
ATTENDANCE_TEXT_MIXED = "আজকের employee attendance এবং payroল information মিলিয়ে কোনো operational issue আছে কি?"
PAYMENT_TEXT = "payment status একটু দেখাও তো।"
DRAFT_TEXT = "draft list দেখাও।"


def _admin_identity() -> dict:
    return {
        "role": "admin",
        "display_name": "Test Admin",
        "identity_confidence": 100,
        "identity_source": "phone_match",
        "employee_id": None,
    }


class TestHermesChannelKeywordBypass:
    @pytest.mark.parametrize("text", [ATTENDANCE_TEXT_BN, ATTENDANCE_TEXT_MIXED, PAYMENT_TEXT, DRAFT_TEXT])
    @pytest.mark.asyncio
    async def test_admin_keyword_colliding_text_via_bridge1_reaches_hermes_bypass(self, monkeypatch, text):
        """The actual bug: these exact phrasings, from the Admin, via
        bridge1, must now reach process_admin_natural_query() (and thus
        Hermes) instead of the old get_attendance_summary()/
        list_payment_drafts()/_cmd_admin_list() keyword handlers."""
        import modules.message_router as router

        settings = router.get_settings()
        admin_bridge2_number = settings.admin_bridge2_number
        assert admin_bridge2_number, "admin_bridge2_number must be configured for this test to be meaningful"

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)
        # If the bug regresses, one of these old handlers would be called
        # instead -- asserting non-invocation makes the failure mode explicit.
        attendance_mock = AsyncMock(return_value="stale attendance summary")
        monkeypatch.setattr(router, "get_attendance_summary", attendance_mock)
        payment_mock = AsyncMock(return_value="stale payment drafts")
        monkeypatch.setattr(router, "list_payment_drafts", payment_mock)
        list_mock = AsyncMock(return_value="stale admin list")
        monkeypatch.setattr(router, "_cmd_admin_list", list_mock)

        reply, admin_note = await router.process_message(admin_bridge2_number, text, "bridge1")

        natural_query_mock.assert_awaited_once_with(text, admin_bridge2_number, source_bridge="bridge1")
        attendance_mock.assert_not_called()
        payment_mock.assert_not_called()
        list_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_same_text_via_bridge3_still_uses_old_keyword_handler(self, monkeypatch):
        """Every other channel is completely unaffected -- bridge3 (not a
        Hermes control channel) must still use the original keyword
        shortcut, unchanged."""
        import modules.message_router as router

        settings = router.get_settings()
        admin_bridge2_number = settings.admin_bridge2_number
        assert admin_bridge2_number

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)
        attendance_mock = AsyncMock(return_value="attendance summary text")
        monkeypatch.setattr(router, "get_attendance_summary", attendance_mock)

        reply, admin_note = await router.process_message(admin_bridge2_number, ATTENDANCE_TEXT_BN, "bridge3")

        attendance_mock.assert_awaited_once()
        natural_query_mock.assert_not_called()
        assert reply == "attendance summary text"

    @pytest.mark.asyncio
    async def test_non_admin_phone_via_bridge1_still_uses_old_keyword_handler(self, monkeypatch):
        """A non-Admin sender (identity resolved as admin here only to
        reach this branch at all -- a real non-admin-phone message never
        gets this far, see identity_brain's own phone-exact check) on
        bridge1 must not get the Hermes bypass either -- only the exact
        admin_bridge2_number qualifies."""
        import modules.message_router as router

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)
        attendance_mock = AsyncMock(return_value="attendance summary text")
        monkeypatch.setattr(router, "get_attendance_summary", attendance_mock)

        reply, admin_note = await router.process_message("8801700000099", ATTENDANCE_TEXT_BN, "bridge1")

        attendance_mock.assert_awaited_once()
        natural_query_mock.assert_not_called()
        assert reply == "attendance summary text"
