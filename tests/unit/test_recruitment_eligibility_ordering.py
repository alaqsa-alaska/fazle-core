"""Direct unit tests for recruitment_eligibility()'s active-session-before-
operational-intent ordering (Phase 1/4, 2026-08-25 recovery pass).

Real incident this closes: a mid-funnel recruitment candidate's fee/
payment-shaped question containing a bare operational keyword (টাকা,
বেতন, পেমেন্ট) used to get bumped out of recruitment into the employee-
payment-conversation module, because the OPERATIONAL_INTENTS check ran
BEFORE the active-session check. Fixed by reordering: an active session
is now checked first and, if present, always wins regardless of intent.

These tests call recruitment_eligibility() directly (not through the
full message_router pipeline) so the ordering property itself is proven
without depending on classifier/intent-detection internals -- `intent`
is passed in directly, matching real call sites where message_router has
already classified it before calling this function."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio

ACTIVE_SESSION = {"id": 1, "step": "area", "phone": "8801700000000"}


class TestActiveSessionBeatsOperationalIntent:
    """The exact ordering property: active_session_continuation must win
    over operational_intent whenever a session exists."""

    async def _eligibility(self, text: str, intent: str, *, active_session):
        from modules.recruitment_flow import recruitment_eligibility
        with (
            patch("modules.recruitment_flow.get_active_session", AsyncMock(return_value=active_session)),
            patch("modules.recruitment_flow.is_recruitment_trigger", return_value=False),
        ):
            return await recruitment_eligibility(
                "8801700000000", text, role="candidate", intent=intent, source="bridge3",
            )

    async def test_bengali_taka_keyword_mid_funnel_stays_in_recruitment(self):
        """"এই পদের জন্য কত টাকা লাগবে?" (how much money/টাকা is needed for
        this position) -- a genuine recruitment fee question, but
        intent="payment_due" (OPERATIONAL_INTENTS) because of the bare
        টাকা keyword. With an active session, this must remain eligible
        via active_session_continuation, NOT get rejected as
        operational_intent."""
        result = await self._eligibility(
            "এই পদের জন্য কত টাকা লাগবে?", "payment_due", active_session=ACTIVE_SESSION,
        )
        assert result["eligible"] is True
        assert result["reason"] == "active_session_continuation"
        assert result["autosend"] is False  # still draft-only, per the function's own comment

    async def test_bengali_betan_keyword_mid_funnel_stays_in_recruitment(self):
        """"বেতন কবে দিবেন?" mid-funnel, classified payment_due -- same
        property, different keyword."""
        result = await self._eligibility(
            "বেতন কবে দিবেন?", "payment_due", active_session=ACTIVE_SESSION,
        )
        assert result["eligible"] is True
        assert result["reason"] == "active_session_continuation"

    async def test_bengali_payment_keyword_mid_funnel_stays_in_recruitment(self):
        """"পেমেন্ট কিভাবে হবে?" mid-funnel, classified payment_due."""
        result = await self._eligibility(
            "পেমেন্ট কিভাবে হবে?", "payment_due", active_session=ACTIVE_SESSION,
        )
        assert result["eligible"] is True
        assert result["reason"] == "active_session_continuation"

    async def test_attendance_shaped_intent_mid_funnel_also_stays(self):
        """The fix is general (any OPERATIONAL_INTENTS value), not just
        payment_due -- confirm attendance-shaped intent is also
        overridden by an active session."""
        result = await self._eligibility(
            "কাল অফিসে আসব", "attendance", active_session=ACTIVE_SESSION,
        )
        assert result["eligible"] is True
        assert result["reason"] == "active_session_continuation"

    async def test_no_active_session_operational_intent_still_rejects(self):
        """Without an active session, the operational_intent rejection
        must still fire exactly as before -- this is NOT a blanket
        override of OPERATIONAL_INTENTS, only session-gated."""
        result = await self._eligibility(
            "বেতন কবে দিবেন?", "payment_due", active_session=None,
        )
        assert result["eligible"] is False
        assert result["reason"] == "operational_intent"

    async def test_active_session_with_non_operational_intent_unaffected(self):
        """Sanity: an active session with an ordinary (non-operational)
        intent behaves exactly as before this fix too."""
        result = await self._eligibility(
            "আমার বয়স ২৫", "unknown", active_session=ACTIVE_SESSION,
        )
        assert result["eligible"] is True
        assert result["reason"] == "active_session_continuation"


class TestOperationalRoleStillBlockedRegardlessOfSession:
    """The role check runs FIRST, before the active-session check --
    confirm this fix didn't accidentally let a real employee/accountant/
    etc. into recruitment just because a (nonsensical) recruitment
    session existed for their number."""

    async def test_real_employee_role_never_eligible_even_with_session(self):
        from modules.recruitment_flow import recruitment_eligibility
        with (
            patch("modules.recruitment_flow.get_active_session", AsyncMock(return_value=ACTIVE_SESSION)),
            patch("modules.recruitment_flow.is_recruitment_trigger", return_value=False),
        ):
            result = await recruitment_eligibility(
                "8801700000000", "বেতন কবে দিবেন?", role="employee", intent="payment_due", source="bridge3",
            )
        assert result["eligible"] is False
        assert result["reason"] == "operational_identity"
