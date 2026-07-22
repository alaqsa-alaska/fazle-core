"""Unit tests — Final Instruction (2026-07-22): recruitment session
continuation, employee salary-query priority, Route B money-request role
scoping, and Route B KB-context wiring.

Each fix addressed a specific gap found by a read-only code audit:
- recruitment_flow: an active funnel session almost never actually advanced
  (intake_message() was only a fallback for an empty generic reply, which
  the KB/rule layer essentially never produces).
- recruitment_flow: OPERATIONAL_INTENTS excluded "salary_query" even for
  candidate/unknown roles, so "escort e koto salary?" never reached the
  recruitment reply path.
- message_router: a known employee's informational salary question
  ("বেতন কবে দিবেন") was intercepted by the payment-REQUEST trigger
  (bare keyword "বেতন") before the real payroll-backed answer.
- route_b: is_advance_request() fired identically for all four
  ROUTE_B_ROLES with no identity check, so a candidate/unknown sender's
  generic distress word got framed as a MONEY/ADVANCE REQUEST.
- route_b: never consulted modules.knowledge_base/RAG at all.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = pytest.mark.unit


# ===========================================================================
# recruitment_flow: active session continuation
# ===========================================================================

class TestRecruitmentSessionContinuation:

    @pytest.mark.asyncio
    async def test_active_session_calls_intake_message_directly(self, monkeypatch):
        import modules.recruitment_flow as rf

        monkeypatch.setattr(
            rf, "recruitment_eligibility",
            AsyncMock(return_value={
                "eligible": True, "autosend": False, "reason": "session_followup_draft",
                "active_session": {"id": 1, "collection_step": "age"},
            }),
        )
        intake_mock = AsyncMock(return_value={"reply": "আপনার বয়স কত?", "action": "collecting", "session_id": 1})
        monkeypatch.setattr(rf, "intake_message", intake_mock)
        generate_mock = AsyncMock(return_value="should not be called")
        monkeypatch.setattr("modules.conversation_layer.generate_recruitment_reply", generate_mock)

        result = await rf.route_recruitment_message("8801700000020", "25", source="bridge1")

        assert result["reply"] == "আপনার বয়স কত?"
        intake_mock.assert_awaited_once()
        generate_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_active_session_autosend_opens_session_in_background(self, monkeypatch):
        """A genuinely new, explicit job inquiry must still get its
        informational reply (unchanged), but must ALSO open a session in
        the background so the NEXT message has something to continue."""
        import modules.recruitment_flow as rf

        monkeypatch.setattr(
            rf, "recruitment_eligibility",
            AsyncMock(return_value={
                "eligible": True, "autosend": True, "reason": "explicit_recruitment",
                "active_session": None,
            }),
        )
        generate_mock = AsyncMock(return_value="আমাদের পদ সম্পর্কে তথ্য...")
        monkeypatch.setattr("modules.conversation_layer.generate_recruitment_reply", generate_mock)
        intake_mock = AsyncMock(return_value={"reply": "আপনার নাম কী?", "action": "created", "session_id": 2})
        monkeypatch.setattr(rf, "intake_message", intake_mock)

        result = await rf.route_recruitment_message("8801700000021", "job আছে?", source="bridge1")

        assert result["reply"] == "আমাদের পদ সম্পর্কে তথ্য..."
        intake_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_active_session_low_confidence_does_not_open_session(self, monkeypatch):
        """Lower-confidence eligibility (detector context/intent layers,
        autosend=False) must not open a session in the background — only
        explicit/entity-layer autosend does."""
        import modules.recruitment_flow as rf

        monkeypatch.setattr(
            rf, "recruitment_eligibility",
            AsyncMock(return_value={
                "eligible": True, "autosend": False, "reason": "detector_context",
                "active_session": None,
            }),
        )
        generate_mock = AsyncMock(return_value="কিছু একটা তথ্য")
        monkeypatch.setattr("modules.conversation_layer.generate_recruitment_reply", generate_mock)
        intake_mock = AsyncMock(return_value={"reply": "", "action": "ignored", "session_id": None})
        monkeypatch.setattr(rf, "intake_message", intake_mock)

        await rf.route_recruitment_message("8801700000022", "ভাই কেমন আছেন", source="bridge1")

        intake_mock.assert_not_called()


class TestRecruitmentSalaryQueryEligibility:

    def test_salary_query_intent_no_longer_blocks_candidate_eligibility(self):
        import modules.recruitment_flow as rf
        assert "salary_query" not in rf.OPERATIONAL_INTENTS

    @pytest.mark.asyncio
    async def test_candidate_salary_question_is_eligible(self, monkeypatch):
        import modules.recruitment_flow as rf
        monkeypatch.setattr(rf, "get_active_session", AsyncMock(return_value=None))
        decision = await rf.recruitment_eligibility(
            "8801700000023", "escort e monthly koto salary?",
            role="candidate", intent="salary_query",
        )
        assert decision["reason"] != "operational_intent"

    @pytest.mark.asyncio
    async def test_employee_role_still_excluded_regardless_of_intent(self, monkeypatch):
        """The role check runs first and still fully protects real
        employees — this fix only affects candidate/unknown/new_lead."""
        import modules.recruitment_flow as rf
        monkeypatch.setattr(rf, "get_active_session", AsyncMock(return_value=None))
        decision = await rf.recruitment_eligibility(
            "8801700000024", "আমার বেতন কত", role="employee", intent="salary_query",
        )
        assert decision["eligible"] is False
        assert decision["reason"] == "operational_identity"


# ===========================================================================
# message_router: employee salary-query priority over payment-request trigger
# ===========================================================================

class TestEmployeeSalaryQueryPriority:

    @pytest.mark.asyncio
    async def test_salary_query_reaches_payroll_answer_not_payment_request(self, monkeypatch):
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value={
                "role": "employee", "display_name": "Karim", "identity_confidence": 90,
                "identity_source": "db_employee", "employee_id": 501,
            }),
        )
        monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="salary_query"))
        monkeypatch.setattr(router, "classify", lambda _text: "salary_query")
        monkeypatch.setattr(router, "ec_get_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_verification_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "is_attendance_message", lambda _t: False)

        payroll_mock = AsyncMock(return_value={"basic_salary": 15000})
        monkeypatch.setattr(router, "get_payroll_summary", payroll_mock)
        monkeypatch.setattr(router, "format_payroll_context", lambda p: "payroll context")
        monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
        ai_reply_mock = AsyncMock(return_value="আপনার বেতন ১৫,০০০ টাকা, প্রতি মাসের ৫ তারিখে দেওয়া হয়।")
        monkeypatch.setattr(router.ai, "generate_reply", ai_reply_mock)

        ec_trigger_mock = AsyncMock()
        monkeypatch.setattr(router, "ec_handle_payment_request", ec_trigger_mock)

        reply, admin_note = await router.process_message(
            "8801700000030", "আমার বেতন কবে দিবেন?", "bridge1",
        )

        payroll_mock.assert_awaited_once_with(501)
        ai_reply_mock.assert_awaited_once()
        ec_trigger_mock.assert_not_called()
        assert "১৫,০০০" in reply

    @pytest.mark.asyncio
    async def test_advance_request_intent_still_reaches_payment_workflow(self, monkeypatch):
        """Regression guard: a genuine advance/payment REQUEST (different
        intent than salary_query/payment_due) must still reach the
        Sprint-3A payment-request workflow — this fix is scoped to the
        specific informational-salary-query intents only."""
        import modules.message_router as router

        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(
            router, "detect_identity",
            AsyncMock(return_value={
                "role": "employee", "display_name": "Karim", "identity_confidence": 90,
                "identity_source": "db_employee", "employee_id": 501,
            }),
        )
        monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="advance_request"))
        monkeypatch.setattr(router, "classify", lambda _text: "advance_request")
        monkeypatch.setattr(router, "ec_get_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_verification_session", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "is_attendance_message", lambda _t: False)

        payroll_mock = AsyncMock()
        monkeypatch.setattr(router, "get_payroll_summary", payroll_mock)

        ec_trigger_mock = AsyncMock(return_value=("অগ্রিম রিকোয়েস্ট শুরু হয়েছে", {"note": "x"}))
        monkeypatch.setattr(router, "ec_handle_payment_request", ec_trigger_mock)
        monkeypatch.setattr(router, "ec_detect_trigger", lambda _t: True)

        reply, admin_note = await router.process_message(
            "8801700000031", "আমার emergency টাকা লাগবে, অগ্রিম দেন", "bridge1",
        )

        ec_trigger_mock.assert_awaited_once()
        payroll_mock.assert_not_called()


# ===========================================================================
# route_b: money/advance detector scoped to real operational roles
# ===========================================================================

class TestRouteBMoneyRequestRoleScoping:

    async def _run(self, monkeypatch, role: str, text: str):
        import modules.route_b as route_b

        monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
        monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
        monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=None))
        monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value="ok"))
        draft_mock = AsyncMock(return_value={"purpose": "money-advance-review"})
        monkeypatch.setattr(route_b, "_create_money_advance_admin_draft", draft_mock)

        reply, admin_note = await route_b.handle_route_b_message(
            "8801700000040", text, "bridge1", {"identity_role": role, "role": role},
        )
        return reply, admin_note, draft_mock

    @pytest.mark.asyncio
    async def test_escort_distress_word_still_flagged(self, monkeypatch):
        _, admin_note, draft_mock = await self._run(monkeypatch, "escort", "আমার খুব বিপদ, সাহায্য লাগবে")
        draft_mock.assert_awaited_once()
        assert admin_note == {"purpose": "money-advance-review"}

    @pytest.mark.asyncio
    async def test_candidate_distress_word_not_flagged_as_money_request(self, monkeypatch):
        """A candidate/unknown sender's generic distress word must not be
        framed as an operational money/advance request — no employee/
        payroll relationship exists for the admin draft to act on."""
        _, admin_note, draft_mock = await self._run(monkeypatch, "candidate", "আমার খুব বিপদ, সাহায্য লাগবে")
        draft_mock.assert_not_called()
        assert admin_note is None

    @pytest.mark.asyncio
    async def test_unknown_sender_distress_word_not_flagged(self, monkeypatch):
        _, admin_note, draft_mock = await self._run(monkeypatch, "unknown", "সাহায্য লাগবে")
        draft_mock.assert_not_called()


# ===========================================================================
# route_b: KB context wiring
# ===========================================================================

class TestRouteBKnowledgeBaseContext:

    @pytest.mark.asyncio
    async def test_kb_hit_is_folded_into_ollama_context(self, monkeypatch):
        import modules.route_b as route_b

        monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
        monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
        monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=None))
        ollama_mock = AsyncMock(return_value="reply text")
        monkeypatch.setattr(route_b, "ollama_generate_reply", ollama_mock)
        monkeypatch.setattr(
            "modules.knowledge_base.get_reply",
            AsyncMock(return_value="অফিসের ঠিকানা: আগ্রাবাদ, চট্টগ্রাম"),
        )

        await route_b.handle_route_b_message(
            "8801700000041", "অফিস কোথায়?", "bridge1", {"identity_role": "unknown"},
        )

        ollama_mock.assert_awaited_once()
        _, kwargs = ollama_mock.call_args
        db_context = kwargs.get("db_context") if "db_context" in kwargs else ollama_mock.call_args[0][2]
        assert "আগ্রাবাদ" in db_context

    @pytest.mark.asyncio
    async def test_kb_lookup_failure_never_breaks_the_reply(self, monkeypatch):
        import modules.route_b as route_b

        monkeypatch.setattr(route_b, "_get_conversation_history", AsyncMock(return_value=""))
        monkeypatch.setattr(route_b, "_get_saved_contact", AsyncMock(return_value=None))
        monkeypatch.setattr(route_b, "_get_profile_name", AsyncMock(return_value=None))
        ollama_mock = AsyncMock(return_value="reply text")
        monkeypatch.setattr(route_b, "ollama_generate_reply", ollama_mock)
        monkeypatch.setattr(
            "modules.knowledge_base.get_reply",
            AsyncMock(side_effect=RuntimeError("kb boom")),
        )

        reply, _ = await route_b.handle_route_b_message(
            "8801700000042", "hello", "bridge1", {"identity_role": "unknown"},
        )
        assert reply == "reply text"


# ===========================================================================
# Ollama company identity
# ===========================================================================

class TestOllamaCompanyIdentity:
    """2026-07-22: company name is now centralized (Settings.company_name,
    app/config.py) and read dynamically at call time -- these assert against
    the live setting, not a hardcoded name, so they survive future rebrands
    without needing an edit here (that's the whole point of centralizing it)."""

    def test_general_identity_uses_live_company_name(self):
        import shared.reply_policy as rp
        from app.config import get_settings
        identity = rp._general_identity()
        assert get_settings().company_name in identity
        assert "আল-আকসা সিকিউরিটি সার্ভিস অ্যান্ড ট্রেডিং সেন্টার" not in identity
        assert "আল-মানারাতাইন স্কিল সলিউশনস" not in identity

    def test_recruitment_role_uses_live_company_name(self):
        import shared.reply_policy as rp
        from app.config import get_settings
        assert get_settings().company_name in rp._recruitment_role()

    def test_ollama_module_prompts_use_live_company_name(self):
        import inspect
        import app.ollama as ollama_mod
        from app.config import get_settings
        source = inspect.getsource(ollama_mod)
        assert "আল-আকসা সিকিউরিটি অ্যান্ড লজিস্টিকস সার্ভিসেস লিমিটেড" not in source
        assert "আল-মানারাতাইন স্কিল সলিউশনস" not in source
        assert "settings.company_name" in source

    def test_company_name_setting_is_the_new_brand(self):
        from app.config import get_settings
        assert get_settings().company_name == "Al-Rifa'i Integrated Services"

    def test_no_reply_policy_module_level_import_is_lazy(self):
        """Regression guard: _company_name() must never be called at bare
        module-import time (it transitively calls get_settings() ->
        Settings(), which requires DATABASE_URL and would crash any import
        of this module before the environment is fully configured). All
        three prompt builders must be functions, not pre-evaluated
        constants."""
        import shared.reply_policy as rp
        import inspect
        assert inspect.isfunction(rp._general_identity)
        assert inspect.isfunction(rp._recruitment_role)
        assert inspect.isfunction(rp._base_prompt)
