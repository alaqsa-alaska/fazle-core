"""
Bridge1 Hermes Control Channel — cross-bridge echo guard (2026-08-12,
Owner-directed).

A reply modules.admin_directives.router._deliver_hermes_reply sends out via
bridge1 to settings.admin_bridge2_number physically lands on bridge2 as a
genuine new is_from_me=0 inbound message, sender = bridge1's own number --
identity_brain._check_admin_settings() already globally recognizes
admin_bridge1_number as role="admin" regardless of source, so without this
guard that reply would re-enter modules.message_router.process_message()'s
own ADMIN branch as if it were a brand-new request from the Admin.

This guard (the first statement inside `if role_str == "admin":`) recognizes
that exact shape -- source == "bridge2", sender == bridge1's own number,
content starting with the literal "🤖 Hermes:" prefix _deliver_hermes_reply
always uses -- and silently ignores it (returns ("", None)) before RBAC,
the AI console, or Hermes itself are ever reached. It must fire on that
exact combination only; changing any one of the three conditions must let
the message fall through to normal admin processing, unaffected.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

HERMES_REPLY_TEXT = "🤖 Hermes:\nAll systems normal, Boss."
ORDINARY_ADMIN_TEXT = "System status update, please review."


def _admin_identity() -> dict:
    return {
        "role": "admin",
        "display_name": "Test Admin",
        "identity_confidence": 100,
        "identity_source": "phone_match",
        "employee_id": None,
    }


class TestHermesCrossBridgeEchoGuard:
    @pytest.mark.asyncio
    async def test_bridge1_own_reply_landing_on_bridge2_is_silently_ignored(self, monkeypatch):
        import modules.message_router as router

        settings = router.get_settings()
        bridge1_number = settings.bridge1_number
        assert bridge1_number, "bridge1_number must be configured for this test to be meaningful"

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="should not be called")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)

        reply, admin_note = await router.process_message(bridge1_number, HERMES_REPLY_TEXT, "bridge2")

        assert (reply, admin_note) == ("", None)
        natural_query_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_ordinary_admin_text_from_bridge1_number_is_not_swallowed(self, monkeypatch):
        """Same sender + same channel, but content does NOT match the
        Hermes-reply signature -- must fall through to normal admin
        processing, not be silently dropped."""
        import modules.message_router as router

        settings = router.get_settings()
        bridge1_number = settings.bridge1_number
        assert bridge1_number

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="console reply")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)

        reply, admin_note = await router.process_message(bridge1_number, ORDINARY_ADMIN_TEXT, "bridge2")

        assert reply == "console reply"
        natural_query_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hermes_signature_text_arriving_via_bridge1_is_not_swallowed(self, monkeypatch):
        """Same sender + same content signature, but arriving via bridge1
        (not bridge2) -- the guard only ever protects the bridge1->bridge2
        bounce direction, never the reverse."""
        import modules.message_router as router

        settings = router.get_settings()
        bridge1_number = settings.bridge1_number
        assert bridge1_number

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="console reply")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)

        reply, admin_note = await router.process_message(bridge1_number, HERMES_REPLY_TEXT, "bridge1")

        assert reply == "console reply"
        natural_query_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_hermes_signature_text_from_a_different_sender_is_not_swallowed(self, monkeypatch):
        """Same channel + same content signature, but sender is NOT
        bridge1's own number (e.g. the genuine Admin phone itself, or any
        other admin-recognized number) -- must not be swallowed; only
        bridge1's own account number triggers the guard."""
        import modules.message_router as router

        settings = router.get_settings()
        admin_bridge2_number = settings.admin_bridge2_number
        assert admin_bridge2_number

        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_admin_identity()))
        natural_query_mock = AsyncMock(return_value="console reply")
        monkeypatch.setattr(router, "process_admin_natural_query", natural_query_mock)

        reply, admin_note = await router.process_message(admin_bridge2_number, HERMES_REPLY_TEXT, "bridge2")

        assert reply == "console reply"
        natural_query_mock.assert_awaited_once()
