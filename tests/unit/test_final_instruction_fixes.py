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

from types import SimpleNamespace
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


class TestRecruitmentLlmPriorityAllowlist:
    """Phase 1B (2026-08-06, Owner-approved allowlist) -- see
    knowledge_base/00_governance/management_decisions.md "Recruitment
    Phase 1B — Active-Session LLM Priority Allowlist" for the full
    decision record. Covers modules.recruitment_flow._is_llm_priority_phone()
    and the new conditional block in route_recruitment_message()."""

    @pytest.mark.asyncio
    async def test_allowlisted_number_gets_llm_path_first_during_active_session(self, monkeypatch):
        import modules.recruitment_flow as rf

        monkeypatch.setattr(rf, "_is_llm_priority_phone", lambda phone: True)
        monkeypatch.setattr(
            rf, "recruitment_eligibility",
            AsyncMock(return_value={
                "eligible": True, "autosend": False, "reason": "session_followup_draft",
                "active_session": {"id": 1, "collection_step": "age"},
            }),
        )
        generate_mock = AsyncMock(return_value="আমাদের বিজনেস সম্পর্কে জানতে চাইলে...")
        monkeypatch.setattr("modules.conversation_layer.generate_recruitment_reply", generate_mock)
        intake_mock = AsyncMock(return_value={"reply": "আপনার বয়স কত?", "action": "collecting", "session_id": 1})
        monkeypatch.setattr(rf, "intake_message", intake_mock)

        result = await rf.route_recruitment_message(
            "8801880446111", "তোমার বিজনেস কি?", source="bridge1",
        )

        assert result["reply"] == "আমাদের বিজনেস সম্পর্কে জানতে চাইলে..."
        assert result["reason"] == "llm_priority_session_continuation"
        generate_mock.assert_awaited_once()
        intake_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_allowlisted_number_still_gets_fsm_first(self, monkeypatch):
        """Byte-identical to the pre-Phase-1B behavior in
        test_active_session_calls_intake_message_directly -- a real
        (unmocked) _is_llm_priority_phone() with a real settings object
        confirms this specific phone is simply not in the allowlist."""
        import modules.recruitment_flow as rf
        from app import config as app_config

        fake_settings = SimpleNamespace(recruitment_llm_priority_phones="8801880446111")
        monkeypatch.setattr(app_config, "get_settings", lambda: fake_settings)
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

        # A different phone -- not the one in fake_settings' allowlist.
        result = await rf.route_recruitment_message("8801700000020", "25", source="bridge1")

        assert result["reply"] == "আপনার বয়স কত?"
        assert result["reason"] == "session_followup_draft"  # passed through from the mocked decision
        intake_mock.assert_awaited_once()
        generate_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_allowlist_no_behavior_change_for_anyone(self, monkeypatch):
        """Default config (empty string) must be a true no-op -- even the
        Phase A test number itself gets FSM-first behavior if the
        allowlist is empty."""
        import modules.recruitment_flow as rf
        from app import config as app_config

        fake_settings = SimpleNamespace(recruitment_llm_priority_phones="")
        monkeypatch.setattr(app_config, "get_settings", lambda: fake_settings)
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

        result = await rf.route_recruitment_message("8801880446111", "25", source="bridge1")

        assert result["reply"] == "আপনার বয়স কত?"
        assert result["reason"] == "session_followup_draft"  # passed through from the mocked decision
        intake_mock.assert_awaited_once()
        generate_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_path_returning_nothing_falls_back_to_intake(self, monkeypatch):
        """An allowlisted phone whose LLM reply comes back empty (or
        safety-restricted -- conversation_layer.generate_recruitment_reply
        already collapses both to None) must still fall through to
        intake_message(), not silently drop the message."""
        import modules.recruitment_flow as rf

        monkeypatch.setattr(rf, "_is_llm_priority_phone", lambda phone: True)
        monkeypatch.setattr(
            rf, "recruitment_eligibility",
            AsyncMock(return_value={
                "eligible": True, "autosend": False, "reason": "session_followup_draft",
                "active_session": {"id": 1, "collection_step": "age"},
            }),
        )
        generate_mock = AsyncMock(return_value=None)
        monkeypatch.setattr("modules.conversation_layer.generate_recruitment_reply", generate_mock)
        intake_mock = AsyncMock(return_value={"reply": "আপনার বয়স কত?", "action": "collecting", "session_id": 1})
        monkeypatch.setattr(rf, "intake_message", intake_mock)

        result = await rf.route_recruitment_message(
            "8801880446111", "??", source="bridge1",
        )

        assert result["reply"] == "আপনার বয়স কত?"
        assert result["reason"] == "session_followup_draft"  # passed through from the mocked decision
        generate_mock.assert_awaited_once()
        intake_mock.assert_awaited_once()


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


# ===========================================================================
# Phase 2 (2026-08-06): Universal AI Responder -- bidirectional history,
# the faithfulness validator, generate_universal_reply(), and Tier 15's
# shadow-mode wiring. See knowledge_base/00_governance/management_decisions.md
# "Universal AI Responder — Phase 2" for the full decision record.
# ===========================================================================

class TestBidirectionalHistory:
    """get_recent_history() now includes outbound (bot-authored) rows,
    not just inbound, prefixed so a caller can tell who said what."""

    @pytest.mark.asyncio
    async def test_includes_both_directions_with_prefixes(self, monkeypatch):
        import modules.message_router as mr

        # fetch_all returns DESC (newest first) per the real query --
        # the function reverses this internally to chronological order.
        rows = [
            {"message_body": "third (newest)", "direction": "outbound"},
            {"message_body": "second", "direction": "inbound"},
            {"message_body": "first (oldest)", "direction": "outbound"},
        ]
        monkeypatch.setattr(mr, "fetch_all", AsyncMock(return_value=rows))

        result = await mr.get_recent_history("8801700000099")

        assert result == [
            "You: first (oldest)",
            "Them: second",
            "You: third (newest)",
        ]

    @pytest.mark.asyncio
    async def test_skips_empty_message_bodies(self, monkeypatch):
        import modules.message_router as mr

        rows = [
            {"message_body": "   ", "direction": "inbound"},
            {"message_body": "real message", "direction": "outbound"},
        ]
        monkeypatch.setattr(mr, "fetch_all", AsyncMock(return_value=rows))

        result = await mr.get_recent_history("8801700000099")

        assert result == ["You: real message"]

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_list_not_raise(self, monkeypatch):
        import modules.message_router as mr

        monkeypatch.setattr(mr, "fetch_all", AsyncMock(side_effect=RuntimeError("db down")))

        result = await mr.get_recent_history("8801700000099")

        assert result == []


class TestFaithfulnessValidator:
    """modules.conversation_layer.playbooks.validate_faithfulness()."""

    def test_no_rag_facts_requires_exact_safe_fallback(self):
        from modules.conversation_layer.playbooks import validate_faithfulness, SAFE_FALLBACK_REPLY

        is_faithful, reason = validate_faithfulness("some freeform answer", None, None)
        assert is_faithful is False
        assert "safe fallback" in reason

        is_faithful, reason = validate_faithfulness(SAFE_FALLBACK_REPLY, None, None)
        assert is_faithful is True
        assert reason is None

    def test_numeric_grounding_rejects_unsupported_number(self):
        from modules.conversation_layer.playbooks import validate_faithfulness

        is_faithful, reason = validate_faithfulness(
            "the figure is 99999", "office facts mention 15000 and 18000", [{"n": 1}],
        )
        assert is_faithful is False
        assert "99999" in reason

    def test_numeric_grounding_accepts_supported_number(self):
        from modules.conversation_layer.playbooks import validate_faithfulness

        is_faithful, reason = validate_faithfulness(
            "the figure is 15000", "office facts mention 15000 and 18000", [{"n": 1}],
        )
        assert is_faithful is True
        assert reason is None

    def test_poison_word_uses_word_boundary_not_substring(self):
        """Regression guard for a bug caught during implementation --
        bare substring matching on "ai" would false-positive inside
        ordinary English words. Must not trigger on "again"/"contact"."""
        from modules.conversation_layer.playbooks import validate_faithfulness

        is_faithful, reason = validate_faithfulness(
            "please contact the office again", "office facts", [{"n": 1}],
        )
        assert reason != "reply contained a disallowed term: \'ai\'"

    def test_actual_poison_word_is_still_rejected(self):
        from modules.conversation_layer.playbooks import validate_faithfulness

        is_faithful, reason = validate_faithfulness(
            "I am an ai model", "office facts", [{"n": 1}],
        )
        assert is_faithful is False
        assert "disallowed term" in reason

    def test_empty_reply_rejected(self):
        from modules.conversation_layer.playbooks import validate_faithfulness

        assert validate_faithfulness("", "facts", [{"n": 1}]) == (False, "empty reply")

    def test_reply_too_long_rejected(self):
        from modules.conversation_layer.playbooks import validate_faithfulness

        is_faithful, reason = validate_faithfulness("x" * 1401, "facts", [{"n": 1}])
        assert is_faithful is False
        assert "too long" in reason


class TestUniversalReplyGenerator:
    """modules.conversation_layer.generate_universal_reply()."""

    @pytest.mark.asyncio
    async def test_happy_path_grounded_reply_returned(self, monkeypatch):
        import modules.conversation_layer as cl

        rag_mock = AsyncMock(return_value={
            "answer": "office hours are 10am-5pm, contact 01958-122300",
            "citations": [{"n": 1, "source": "kb", "title": "office", "score": 3.0}],
            "top_score": 3.0,
        })
        monkeypatch.setattr("modules.rag.safe_answer", rag_mock)
        ai_mock = AsyncMock(return_value="Office is open 10am-5pm, call 01958-122300")
        monkeypatch.setattr("app.llm.generate_reply", ai_mock)

        result = await cl.generate_universal_reply(
            phone="8801700000030", text="office hours?", source="bridge1",
        )

        assert result == "Office is open 10am-5pm, call 01958-122300"
        rag_mock.assert_awaited_once()
        ai_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_rag_hits_and_ungrounded_draft_returns_none(self, monkeypatch):
        import modules.conversation_layer as cl

        monkeypatch.setattr("modules.rag.safe_answer", AsyncMock(return_value=None))
        monkeypatch.setattr("app.llm.generate_reply", AsyncMock(return_value="I think it is probably fine"))

        result = await cl.generate_universal_reply(
            phone="8801700000031", text="random question", source="bridge1",
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_faithfulness_rejection_falls_back_to_safe_reply(self, monkeypatch):
        import modules.conversation_layer as cl
        from modules.conversation_layer.playbooks import SAFE_FALLBACK_REPLY

        monkeypatch.setattr("modules.rag.safe_answer", AsyncMock(return_value={
            "answer": "salary is 15000", "citations": [{"n": 1}], "top_score": 3.0,
        }))
        monkeypatch.setattr("app.llm.generate_reply", AsyncMock(return_value="salary is 99999"))

        result = await cl.generate_universal_reply(
            phone="8801700000032", text="salary?", source="bridge1",
        )

        assert result == SAFE_FALLBACK_REPLY

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none_not_raise(self, monkeypatch):
        import modules.conversation_layer as cl

        monkeypatch.setattr("modules.rag.safe_answer", AsyncMock(return_value=None))
        monkeypatch.setattr("app.llm.generate_reply", AsyncMock(side_effect=RuntimeError("provider down")))

        result = await cl.generate_universal_reply(
            phone="8801700000033", text="anything", source="bridge1",
        )

        assert result is None


class TestTier15ShadowMode:
    """message_router's Tier 15 (AI FALLBACK) shadow-mode wiring. A
    "vendor" role sender is used since it has no dedicated tier and no
    Route B membership, so it is guaranteed to fall all the way through
    to Tier 15 -- the same structural guarantee (nothing upstream claims
    it) the proposal relies on for gate isolation."""

    def _identity(self):
        return {
            "role": "vendor", "display_name": "", "identity_confidence": 0,
            "identity_source": "none", "employee_id": None,
        }

    @pytest.mark.asyncio
    async def test_shadow_mode_returns_old_reply_when_flag_off(self, monkeypatch):
        import modules.message_router as router
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "tier15_universal_responder_live", False)
        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=self._identity()))
        monkeypatch.setattr(router, "is_registered_employee_phone", AsyncMock(return_value=False))
        monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
        monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
        monkeypatch.setattr(router.ai, "generate_reply", AsyncMock(return_value="OLD_BARE_AI_REPLY"))
        monkeypatch.setattr(
            "modules.conversation_layer.generate_universal_reply",
            AsyncMock(return_value="NEW_UNIVERSAL_REPLY"),
        )

        reply, admin_note = await router.process_message(
            "8801700000040", "একটা প্রশ্ন ছিল", "bridge1",
        )

        assert reply == "OLD_BARE_AI_REPLY"

    @pytest.mark.asyncio
    async def test_shadow_mode_returns_new_reply_when_flag_on(self, monkeypatch):
        import modules.message_router as router
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "tier15_universal_responder_live", True)
        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=self._identity()))
        monkeypatch.setattr(router, "is_registered_employee_phone", AsyncMock(return_value=False))
        monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
        monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
        monkeypatch.setattr(router.ai, "generate_reply", AsyncMock(return_value="OLD_BARE_AI_REPLY"))
        monkeypatch.setattr(
            "modules.conversation_layer.generate_universal_reply",
            AsyncMock(return_value="NEW_UNIVERSAL_REPLY"),
        )

        reply, admin_note = await router.process_message(
            "8801700000041", "একটা প্রশ্ন ছিল", "bridge1",
        )

        assert reply == "NEW_UNIVERSAL_REPLY"

    @pytest.mark.asyncio
    async def test_universal_responder_exception_does_not_break_old_reply(self, monkeypatch):
        """The shadow computation must never take down Tier 15's existing,
        already-live reply path -- even with the flag on, an exception in
        the new pipeline must fall back to the old reply, not raise."""
        import modules.message_router as router
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "tier15_universal_responder_live", True)
        monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
        monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=self._identity()))
        monkeypatch.setattr(router, "is_registered_employee_phone", AsyncMock(return_value=False))
        monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value=None))
        monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
        monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
        monkeypatch.setattr(router.ai, "generate_reply", AsyncMock(return_value="OLD_BARE_AI_REPLY"))
        monkeypatch.setattr(
            "modules.conversation_layer.generate_universal_reply",
            AsyncMock(side_effect=RuntimeError("shadow pipeline blew up")),
        )

        reply, admin_note = await router.process_message(
            "8801700000042", "একটা প্রশ্ন ছিল", "bridge1",
        )

        assert reply == "OLD_BARE_AI_REPLY"
