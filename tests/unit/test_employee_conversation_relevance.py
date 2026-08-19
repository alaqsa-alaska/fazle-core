"""P1a (2026-08-19, reliability-hardening pass): session-relevance guard for
continue_employee_conversation()'s STEP_REASON/STEP_AMOUNT branches. Maps
directly to the Owner's acceptance tests C and D from the same-day plan.

Real incident this covers: an open STEP_REASON session (from an unrelated
16:26 "টাকা লাগবে"/"রিলিজ দিছে" exchange) was still open 3 hours later and
silently swallowed "Where is your office?" -- a completely unrelated
question -- with the canned "identity not found" rejection. See
project_hermes_earth_rename_autonomy_20260819 memory for the full forensic
trace (real ~/.hermes/state.db session + fazle-core DB cross-check)."""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _step_reason_session(session_id=9001):
    return {
        "session_id": session_id,
        "step": "ec_reason",
        "context": {"purpose": "advance"},
        "source": "bridge2",
    }


def _step_amount_session(session_id=9002):
    return {
        "session_id": session_id,
        "step": "ec_amount",
        "context": {"purpose": "advance"},
        "source": "bridge2",
    }


class TestStepReasonRelevance:
    async def test_c_relevant_reply_continues_session(self):
        """Acceptance C: employee session open + a relevant reply (a real
        employee ID) -> session continues (advances to the amount step),
        not released."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_reason_session()
        identity = {
            "employee_id": 501, "employee_name": "Karim", "employee_mobile": "01712345678",
        }
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation.resolve_employee_identity", new=AsyncMock(return_value=identity)), \
             patch("modules.employee_conversation.is_employee_active", return_value=True), \
             patch("modules.employee_conversation._advance_session", new=AsyncMock()) as mock_advance, \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801958122329", "501", "bridge2", intent="payment_due",
            )

        assert reply  # non-empty -- the "amount?" prompt
        mock_advance.assert_awaited_once()
        mock_close.assert_not_awaited()

    async def test_d_unrelated_question_releases_session(self):
        """Acceptance D: employee session open (STEP_REASON) + "Where is
        your office?" -> session does NOT consume it (released, empty
        reply), so message_router's normal route can answer instead. This
        is the exact 2026-08-19 regression."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_reason_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation.resolve_employee_identity", new=AsyncMock(return_value={})), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801958122329", "Where is your office?", "bridge2", intent="unknown",
            )

        assert reply == ""
        assert admin_note is None
        mock_close.assert_awaited_once_with(session["session_id"])

    async def test_unparseable_but_no_unrelated_signal_still_reprompts(self):
        """A parse failure with neither an incompatible intent nor a
        question marker keeps today's exact pre-2026-08-19 behavior --
        still re-prompt, do not release. Guards against over-triggering."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_reason_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation.resolve_employee_identity", new=AsyncMock(return_value={})), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801958122329", "asdf random text", "bridge2", intent="unknown",
            )

        assert "আপনার তথ্য পাওয়া যায়নি" in reply
        mock_close.assert_not_awaited()

    async def test_recruitment_intent_releases_session(self):
        """An incompatible-intent signal alone (no question marker) is
        also sufficient to release."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_reason_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation.resolve_employee_identity", new=AsyncMock(return_value={})), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801958122329", "সিকিউরিটি গার্ডের চাকরি আছে", "bridge2", intent="recruitment",
            )

        assert reply == ""
        mock_close.assert_awaited_once()

    async def test_no_intent_passed_defaults_to_pre_p1a_behavior(self):
        """A caller that omits intent (backward compat) gets today's exact
        old behavior for a non-question, non-parseable message -- never
        released just because intent is unknown/absent."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_reason_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation.resolve_employee_identity", new=AsyncMock(return_value={})), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801958122329", "hmm not sure", "bridge2",
            )

        assert "আপনার তথ্য পাওয়া যায়নি" in reply
        mock_close.assert_not_awaited()


class TestStepAmountRelevance:
    async def test_incident_b_regression_not_released_by_this_fix_alone(self):
        """The original Incident B message ("Ashbo, ektu pore" -- "I'll
        come in a bit") has no question marker and no incompatible intent,
        so P1a intentionally does NOT release it -- that case is already
        covered by the separate 24h TTL fix (commit 532527d). Documenting
        this boundary explicitly, not a regression."""
        from modules.employee_conversation import continue_employee_conversation

        session = _step_amount_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801817201596", "Ashbo, ektu pore", "bridge2", intent="unknown",
            )

        assert "পরিমাণ বুঝতে পারিনি" in reply
        mock_close.assert_not_awaited()

    async def test_question_releases_step_amount_session(self):
        from modules.employee_conversation import continue_employee_conversation

        session = _step_amount_session()
        with patch("modules.employee_conversation.get_conversation_session", new=AsyncMock(return_value=session)), \
             patch("modules.employee_conversation._close_session", new=AsyncMock()) as mock_close:
            reply, admin_note = await continue_employee_conversation(
                "8801817201596", "What is your office address?", "bridge2", intent="unknown",
            )

        assert reply == ""
        mock_close.assert_awaited_once_with(session["session_id"])
