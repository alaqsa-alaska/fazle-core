"""Regression tests -- recruitment "Salary" context-routing bug
(2026-08-21, P1 security/reliability closure pass).

Live-reproduced against candidate "Abuhanif" (8801728129709, bridge3):
his bare "Salary" message got an "Employee ID যাচাই" verification prompt
instead of a recruitment reply. Root cause traced to
modules.message_router.process_message()'s Sprint-3A payment catch-all
(§11, ec_detect_trigger()): a bare keyword scan ("salary"/"বেতন" is one of
its TRIGGER_GROUPS) that fired for ANY non-admin role, completely blind to
the recruitment eligibility decision §7/8 had *already* computed earlier
in the same function call. Whenever route_recruitment_message() produced
no reply text that turn (e.g. a dedup/cooldown-suppressed enqueue -- a
real, not hypothetical, edge case), the message fell all the way through
to this catch-all and got misrouted into ec_handle_payment_request()'s
employee-verification flow.

Fix: reuse the already-computed `recruit_decision` (no second keyword
scan, no hardcoded "Salary" exception) -- the catch-all now skips whenever
§7/8 already judged this sender recruitment-eligible. For role_str ==
"employee", recruit_decision is unconditionally {"eligible": False} (see
process_message §7/8), so a real employee's payment trigger is completely
unaffected -- this only changes behavior for candidate/new_lead/unknown
senders §7/8 already recognized as recruitment-eligible.
"""
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestSalaryMisrouteFixAtMessageRouterLevel:
    """process_message()-level repro/regression: exact Abuhanif shape."""

    async def _run(self, monkeypatch, *, role: str, recruit_eligible: bool,
                    recruit_reply: str, text: str = "Salary"):
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value={
                "role": role, "display_name": "", "identity_confidence": 30,
                "identity_source": "text_hint" if role != "employee" else "db_employee",
                "employee_id": None,
            }),
        )
        monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="salary_query"))
        monkeypatch.setattr(router, "classify", lambda _t: "salary_query")
        monkeypatch.setattr(router, "ec_get_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_verification_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "is_attendance_message", lambda _t: False)
        monkeypatch.setattr(
            router, "recruitment_eligibility",
            AsyncMock(return_value={"eligible": recruit_eligible, "autosend": False,
                                     "reason": "test", "active_session": None}),
        )
        monkeypatch.setattr(
            router, "route_recruitment_message",
            AsyncMock(return_value={"reply": recruit_reply}),
        )
        ec_mock = AsyncMock(return_value=(
            "আপনার রিকোয়েস্ট পেয়েছি 🙏\n\nযাচাইয়ের জন্য আপনার Employee ID বা Mobile নম্বর দিন।",
            None,
        ))
        monkeypatch.setattr(router, "ec_handle_payment_request", ec_mock)
        monkeypatch.setattr(router, "ec_detect_trigger", lambda _t: True)  # "salary"/"বেতন" both trigger this

        reply, admin_note = await router.process_message("8801728129709", text, "bridge3")
        return reply, admin_note, ec_mock

    async def test_recruitment_eligible_empty_reply_never_falls_to_employee_verification(self, monkeypatch):
        """The exact live bug: §7/8 says eligible, but route_recruitment_message()
        returns no text this turn (dedup/cooldown edge case) -- must NOT
        misroute into the employee-ID-verification catch-all."""
        reply, admin_note, ec_mock = await self._run(
            monkeypatch, role="unknown", recruit_eligible=True, recruit_reply="",
        )
        ec_mock.assert_not_called()
        assert "Employee ID" not in (reply or "")

    async def test_recruitment_eligible_with_reply_returns_recruitment_text_not_employee_prompt(self, monkeypatch):
        """Normal case: §7/8 eligible AND produces reply text -- returns
        directly at §7/8, never even reaches the catch-all."""
        reply, admin_note, ec_mock = await self._run(
            monkeypatch, role="unknown", recruit_eligible=True,
            recruit_reply="আপনার আগ্রহের জন্য ধন্যবাদ! পদের নাম কী?",
        )
        ec_mock.assert_not_called()
        assert reply == "আপনার আগ্রহের জন্য ধন্যবাদ! পদের নাম কী?"

    async def test_not_recruitment_eligible_ambiguous_sender_still_gets_verification_protection(self, monkeypatch):
        """Rule 4: when §7/8 genuinely found no recruitment context
        (no active session, no bridge3, no trigger, no detector hit), the
        existing employee-verification catch-all must still fire exactly
        as before this fix -- this is not a blanket bypass."""
        reply, admin_note, ec_mock = await self._run(
            monkeypatch, role="unknown", recruit_eligible=False, recruit_reply="",
        )
        ec_mock.assert_awaited_once()
        assert "Employee ID" in reply

    async def test_real_employee_payment_trigger_completely_unaffected(self, monkeypatch):
        """Rule 3: role_str == 'employee' never reaches recruitment
        eligibility at all (process_message §7/8 only calls it for
        candidate/new_lead/unknown) -- recruit_decision is unconditionally
        {'eligible': False} for an employee, so this fix is a no-op on the
        real-employee path."""
        reply, admin_note, ec_mock = await self._run(
            monkeypatch, role="employee", recruit_eligible=False, recruit_reply="",
            text="আমার salary payment কবে হবে?",
        )
        ec_mock.assert_awaited_once()
        assert "Employee ID" in reply


class TestRecruitmentEligibilitySalaryPhraseMatrix:
    """modules.recruitment_flow.recruitment_eligibility() -- direct,
    classifier-independent matrix. intent is fixed to "salary_query" for
    every phrase below (confirmed live: app.llm.classify_intent_llm()
    classifies all 7 of the user's required regression phrases as
    "salary_query", never "payment_due" -- "salary_query" is already,
    deliberately, excluded from OPERATIONAL_INTENTS). This matrix isolates
    the two signals that actually decide these cases: active session state
    and source bridge -- not the LLM's own classification, which is
    exercised separately (test_recruitment_facebook_boost_recovery_fix.py)
    and is inherently non-deterministic to pin in a unit test.
    """

    RECRUITMENT_CONTEXT_PHRASES = [
        "Salary?",
        "বেতন কত?",
        "এই পদের বেতন কত?",
        "Escort salary কত?",
        "চাকরির salary জানতে চাই",
    ]
    EMPLOYEE_CONTEXT_PHRASES = [
        "আমার বেতন এখনো পাইনি",
        "আমার salary payment কবে হবে?",
    ]

    @pytest.mark.parametrize("text", RECRUITMENT_CONTEXT_PHRASES)
    async def test_active_session_makes_every_recruitment_phrase_eligible(self, text):
        """Rule 1: active recruitment candidate/session + salary/pay/duty/
        qualification/application question -> recruitment, regardless of
        exact phrasing."""
        from modules.recruitment_flow import recruitment_eligibility

        with patch("modules.recruitment_flow.get_active_session",
                   new=AsyncMock(return_value={"id": 99, "collection_step": "name"})):
            decision = await recruitment_eligibility(
                "8801700000050", text, role="unknown", intent="salary_query", source="bridge1",
            )
        assert decision["eligible"] is True
        assert decision["reason"] == "active_session_continuation"

    @pytest.mark.parametrize("text", EMPLOYEE_CONTEXT_PHRASES)
    async def test_active_session_also_covers_possessive_employee_style_phrasing(self, text):
        """Rule 1 still applies even to possessive framing ("আমার...") when
        there genuinely IS an active recruitment session for this phone --
        the session is the authoritative signal, not the phrase's grammar."""
        from modules.recruitment_flow import recruitment_eligibility

        with patch("modules.recruitment_flow.get_active_session",
                   new=AsyncMock(return_value={"id": 99, "collection_step": "phone_confirm"})):
            decision = await recruitment_eligibility(
                "8801700000051", text, role="unknown", intent="salary_query", source="bridge1",
            )
        assert decision["eligible"] is True
        assert decision["reason"] == "active_session_continuation"

    @pytest.mark.parametrize("text", RECRUITMENT_CONTEXT_PHRASES)
    async def test_new_bridge3_sender_asking_about_job_salary_is_eligible(self, text):
        """Rule 2: new sender, no session, on the dedicated recruitment
        channel (bridge3) -- a job-salary question reaches recruitment via
        the bridge3_dedicated_channel fallback (or an earlier detector/
        trigger layer), never the employee catch-all."""
        from modules.recruitment_flow import recruitment_eligibility

        with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)):
            decision = await recruitment_eligibility(
                "8801700000052", text, role="unknown", intent="salary_query", source="bridge3",
            )
        assert decision["eligible"] is True

    @pytest.mark.parametrize("text", EMPLOYEE_CONTEXT_PHRASES)
    async def test_new_sender_possessive_payment_claim_without_session_stays_unresolved(self, text):
        """Rule 3/4: a brand-new sender (no active recruitment session, no
        dedicated-channel context, no explicit/detector recruitment
        signal) making a possessive "my salary/payment" claim is exactly
        the ambiguous, identity-sensitive case that must retain the
        existing verification path -- recruitment_eligibility() must NOT
        wave it through just because the words "salary"/"payment" appear.
        bridge1 (not bridge3) + no session + these two low-signal phrases
        (confirmed live: detector hits 'none' for both) is deliberately
        the one combination with no recruitment-context signal at all.
        """
        from modules.recruitment_flow import recruitment_eligibility

        with patch("modules.recruitment_flow.get_active_session", new=AsyncMock(return_value=None)):
            decision = await recruitment_eligibility(
                "8801700000053", text, role="unknown", intent="salary_query", source="bridge1",
            )
        assert decision["eligible"] is False

    async def test_real_employee_role_never_reaches_recruitment_eligibility_logic(self):
        """Rule 3: message_router.process_message() only calls
        recruitment_eligibility() for role_str in (candidate, new_lead,
        unknown) -- an actual employee's role short-circuits to
        {"eligible": False} before this function is ever invoked. This
        test documents/pins that role-level gate directly against the
        function's own role check (defense-in-depth even if a future
        caller forgets the outer role filter)."""
        from modules.recruitment_flow import recruitment_eligibility

        with patch("modules.recruitment_flow.get_active_session",
                   new=AsyncMock(return_value={"id": 1, "collection_step": "name"})):
            decision = await recruitment_eligibility(
                "8801700000054", "আমার salary payment কবে হবে?",
                role="employee", intent="salary_query", source="bridge3",
            )
        assert decision["eligible"] is False
        assert decision["reason"] == "operational_identity"
