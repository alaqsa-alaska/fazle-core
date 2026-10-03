"""
Owner decision 2026-07-22 — "Route A: no Auto Reply" business rule.

If the sender's SAVED CONTACT NAME contains Client/Escort/Office/Operation/
Accountant/Security Guard/Al-Aqsa/Al-Manaratayn anywhere (substring, not
prefix-only), Route B (Ollama) must never auto-reply to them:
  - Escort-named contact + a genuine order-shaped message (mother vessel +
    lighter vessel + a mobile number) → structured admin draft, no reply
    to the sender (reuses the existing mature escort-order pipeline).
  - Anything else from a name-matched contact → complete silence, no reply,
    no draft.

Covers both enforcement points:
  1. _should_silent_skip() — the early gate keyed off wbom_contacts.display_name.
  2. The Route B branch backstop — keyed off identity_brain's own resolved
     display_name, for a contact not yet present in wbom_contacts.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

ORDER_TEXT = (
    "Mother Vessel: MV GOLDEN STAR\n"
    "02) MV SULTANA-02\n"
    "01711-273432\n"
    "Destination: N.Gonj\n"
    "Capacity: 1200"
)
CASUAL_TEXT = "কেমন আছেন ভাই, একটু জানতে চাচ্ছিলাম"


def _base_identity(role: str, display_name: str) -> dict:
    return {
        "role": role,
        "display_name": display_name,
        "identity_confidence": 50,
        "identity_source": "text_hint",
        "employee_id": None,
    }


class TestShouldSilentSkipNameMatch:
    """_should_silent_skip() reads wbom_contacts.display_name directly."""

    @pytest.mark.asyncio
    async def test_new_keywords_matched_even_when_env_names_configured(self, monkeypatch):
        """Regression: previously, as soon as settings.draft_always_name_list
        was non-empty (true in production today), the static keyword list
        was silently skipped entirely via a same `list(...) is list(...)`
        identity check that's always False. A generic "Accountant Firm ABC"
        contact would never match. Fixed to be a union of both lists."""
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            router,
            "fetch_one",
            AsyncMock(return_value={"display_name": "Accountant Firm ABC"}),
        )
        settings = router.get_settings()
        assert settings.draft_always_name_list, (
            "test assumes .env has configured draft-always names, "
            "exactly the condition that triggered the old bug"
        )

        skip, reason = await router._should_silent_skip("8801700000001")
        assert skip is True
        assert "accountant" in reason.lower()

    @pytest.mark.asyncio
    async def test_security_guard_and_rebrand_tokens_match(self, monkeypatch):
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=False),
        )
        for name in ("Security Guard Karim", "Al-Manaratayn Office", "আল-আকসা Office Chattogram"):
            monkeypatch.setattr(
                router, "fetch_one", AsyncMock(return_value={"display_name": name})
            )
            skip, reason = await router._should_silent_skip("8801700000002")
            assert skip is True, f"{name!r} should have matched"

    @pytest.mark.asyncio
    async def test_substring_match_not_prefix_only(self, monkeypatch):
        """'Md Karim Escort' — keyword in the middle/end, not a prefix."""
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            router, "fetch_one", AsyncMock(return_value={"display_name": "Md Karim Escort"})
        )
        skip, reason = await router._should_silent_skip("8801700000003")
        assert skip is True
        assert "escort" in reason.lower()

    @pytest.mark.asyncio
    async def test_owner_managed_phone_uses_same_gate_even_without_saved_contact(self, monkeypatch):
        import modules.message_router as router

        monkeypatch.setattr(router, "is_registered_employee_phone", AsyncMock(return_value=False))
        monkeypatch.setattr(router, "fetch_one", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "modules.owner_contact_registry.is_owner_managed_contact",
            lambda phone: phone == "8801700000008",
        )
        skip, reason = await router._should_silent_skip("8801700000008")
        assert skip is True
        assert "owner-managed contact" in reason

    @pytest.mark.asyncio
    async def test_unmatched_name_not_skipped(self, monkeypatch):
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            router, "fetch_one", AsyncMock(return_value={"display_name": "Rahim Uddin"})
        )
        skip, reason = await router._should_silent_skip("8801700000004")
        assert skip is False

    @pytest.mark.asyncio
    async def test_registered_employee_phone_bypasses_name_gate_entirely(self, monkeypatch):
        """Final Instruction §2 (2026-07-22): a phone verified in the
        canonical employee tables must not be silenced only because its
        saved name contains a protected term. 'Escort Rahim' is exactly the
        example given in the instruction's own required-tests list."""
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            router, "fetch_one", AsyncMock(return_value={"display_name": "Escort Rahim"})
        )
        skip, reason = await router._should_silent_skip("8801700000009")
        assert skip is False
        assert reason == ""

    @pytest.mark.asyncio
    async def test_non_employee_with_same_name_still_silenced(self, monkeypatch):
        """The exemption is phone-specific, not name-specific — a different,
        non-employee phone saved under the identical name text must still
        be silenced exactly as before."""
        import modules.message_router as router

        monkeypatch.setattr(
            router, "is_registered_employee_phone", AsyncMock(return_value=False),
        )
        monkeypatch.setattr(
            router, "fetch_one", AsyncMock(return_value={"display_name": "Escort Rahim"})
        )
        skip, reason = await router._should_silent_skip("8801700000010")
        assert skip is True


class TestRouteBBackstop:
    """The Route B branch itself must never auto-reply to a name-matched
    contact even if _should_silent_skip() didn't already catch them (e.g. a
    brand-new contact identity_brain has seen but wbom_contacts hasn't)."""

    @pytest.mark.asyncio
    async def test_escort_named_casual_message_gets_full_silence(self, monkeypatch):
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value=_base_identity("escort", "Escort Masud WA")),
        )
        route_b_mock = AsyncMock(return_value=("should not be called", None))
        monkeypatch.setattr("modules.route_b.handle_route_b_message", route_b_mock)
        escort_mock = AsyncMock(return_value=("should not be called either", {"note": "x"}))
        monkeypatch.setattr(router, "handle_escort_client_message", escort_mock)

        reply, admin_note = await router.process_message(
            "8801700000005", CASUAL_TEXT, "bridge1"
        )
        assert reply == ""
        assert admin_note is None
        route_b_mock.assert_not_called()
        escort_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_escort_named_order_shaped_message_creates_admin_draft(self, monkeypatch):
        """Intent classification is deliberately forced away from
        client_order/escort_duty so Tier 9's existing safety net
        (`intent in (...) and _looks_like_escort_order`) does NOT fire —
        this isolates and proves the new Route B backstop's own
        escort-exception (keyed only off the name + `_looks_like_escort_order`,
        no intent dependency), which exists precisely to catch order-shaped
        text an intent misclassification might otherwise let slip through
        to an Ollama auto-reply."""
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value=_base_identity("unknown", "Escort Rahim")),
        )
        monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="general_chat"))
        monkeypatch.setattr(router, "classify", lambda _text: "general_chat")
        route_b_mock = AsyncMock(return_value=("should not be called", None))
        monkeypatch.setattr("modules.route_b.handle_route_b_message", route_b_mock)
        escort_mock = AsyncMock(return_value=("", {"purpose": "escort-order-draft"}))
        monkeypatch.setattr(router, "handle_escort_client_message", escort_mock)

        reply, admin_note = await router.process_message(
            "8801700000006", ORDER_TEXT, "bridge1"
        )
        assert reply == ""
        assert admin_note == {"purpose": "escort-order-draft"}
        escort_mock.assert_awaited_once()
        route_b_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_client_named_contact_order_shaped_text_still_no_auto_reply(self, monkeypatch):
        """The Route-B-branch escort exception (line ~757) is Escort-name-
        specific by design, but a "Client"-named contact sending genuinely
        order-shaped text never reaches that backstop at all — it's already
        caught earlier by the existing Tier 9 escort-order safety net
        (`intent in ("client_order","escort_duty") and _looks_like_escort_order`),
        which every sender hits regardless of name. Same required invariant
        either way: never an auto-reply to the sender for order content."""
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value=_base_identity("unknown", "Client Nizam Office")),
        )
        route_b_mock = AsyncMock(return_value=("should not be called", None))
        monkeypatch.setattr("modules.route_b.handle_route_b_message", route_b_mock)
        escort_mock = AsyncMock(return_value=("", {"purpose": "tier9-order-draft"}))
        monkeypatch.setattr(router, "handle_escort_client_message", escort_mock)

        reply, admin_note = await router.process_message(
            "8801700000007", ORDER_TEXT, "bridge1"
        )
        assert reply == ""
        assert admin_note == {"purpose": "tier9-order-draft"}
        escort_mock.assert_awaited_once()
        route_b_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_unmatched_unknown_contact_still_reaches_route_b(self, monkeypatch):
        """Regression guard: the new gate must not swallow the population
        Route B was actually built for (2026-07-21 Route A/B redesign)."""
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value=_base_identity("unknown", "Rahim Uddin")),
        )
        route_b_mock = AsyncMock(return_value=("hi, how can I help?", None))
        monkeypatch.setattr("modules.route_b.handle_route_b_message", route_b_mock)

        reply, admin_note = await router.process_message(
            "8801700000008", CASUAL_TEXT, "bridge1"
        )
        assert reply == "hi, how can I help?"
        route_b_mock.assert_awaited_once()
