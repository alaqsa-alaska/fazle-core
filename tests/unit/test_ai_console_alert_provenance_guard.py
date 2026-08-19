"""
P1b (2026-08-19, reliability-hardening pass) — internal/system message
provenance guard.

scripts/ai_console_monitor.sh's health-check alert (own stable literal
prefix "🚨 AI Console ALERT", hardcoded in that script's send_wa_alert())
was found live-generating a nonsensical customer-style draft in response to
a system health alert -- the underlying trigger was already fixed same-day
(commit 532527d), but this guard is deliberate defense-in-depth: any inbound
text carrying that exact signature must never reach identity resolution,
intent classification, or any draft/recruitment/employee-conversation
branch. Mirrors modules.message_router's existing cross-bridge Hermes-echo
guard (test_hermes_cross_bridge_echo_guard.py) -- same content-signature
pattern, same "first statement, before any real processing" placement, same
test shape (assert a downstream function was/wasn't reached).

Acceptance tests E and F from the same-day plan, directly.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit

ALERT_TEXT = (
    "🚨 AI Console ALERT [vmi3117764]\n"
    "Wed Aug 19 19:15:07 CEST 2026\n"
    "AI Console /chat/models unreachable (5054ms)\n"
    "⚠️ READ-ONLY policy: AI Console শুধু পড়তে পারবে, লিখতে পারবে না।"
)
REAL_RECRUITMENT_TEXT = "সিকিউরিটি গার্ডের চাকরি আছে?"


def _candidate_identity() -> dict:
    return {
        "role": "candidate", "identity_role": "candidate",
        "identity_confidence": 50, "identity_source": "text_hint",
        "employee_id": None,
    }


class TestSystemProvenanceGuard:
    @pytest.mark.asyncio
    async def test_e_ai_console_alert_produces_zero_mutation(self, monkeypatch):
        """Acceptance E: an inbound message whose body starts with the AI
        Console alert's own literal signature never reaches identity
        resolution or the silent-skip/draft/recruitment/employee-
        conversation logic that follows -- chosen_action=no_action,
        empty reply, no admin_notification (so no draft gets created)."""
        import modules.message_router as router

        skip_mock = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(router, "_should_silent_skip", skip_mock)
        detect_identity_mock = AsyncMock(side_effect=AssertionError(
            "detect_identity must never be called for a system-alert message"
        ))
        monkeypatch.setattr(router, "detect_identity", detect_identity_mock)

        reply, admin_note = await router.process_message("8801880446111", ALERT_TEXT, "bridge1")

        assert (reply, admin_note) == ("", None)
        skip_mock.assert_not_awaited()
        detect_identity_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_f_real_recruitment_message_unaffected(self, monkeypatch):
        """Acceptance F: a real human recruitment message (unrelated
        content, no alert signature) is not touched by this guard --
        processing continues normally past it (proven by reaching the
        silent-skip check, the very next real step)."""
        import modules.message_router as router

        skip_mock = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(router, "_should_silent_skip", skip_mock)
        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_candidate_identity()))

        await router.process_message("8801958122329", REAL_RECRUITMENT_TEXT, "bridge3")

        skip_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_alert_signature_not_at_message_start_is_not_guarded(self, monkeypatch):
        """Narrow, content-signature match only -- the alert prefix must be
        at the START of the message (after stripping leading whitespace),
        matching the guard's own text.lstrip().startswith(...) check. A
        message that merely quotes/mentions the alert text mid-message
        (e.g. someone forwarding/asking about it) is real correspondence,
        not the system-generated alert itself, and must not be swallowed."""
        import modules.message_router as router

        skip_mock = AsyncMock(return_value=(False, ""))
        monkeypatch.setattr(router, "_should_silent_skip", skip_mock)
        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=_candidate_identity()))

        quoting_text = f"Boss, did you see this? {ALERT_TEXT}"
        await router.process_message("8801711111111", quoting_text, "bridge2")

        skip_mock.assert_awaited_once()
