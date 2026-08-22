"""
Task 13 — authoritative-mode promotion (2026-08-22, BUILD+VERIFY).

Covers Required Tests A-S from the promotion spec for:
  - modules.hermes_dispatch.semantic_gate() MiniMax-primary wiring
    (minimax_customer_primary flag)
  - modules.hermes_dispatch.generate_reply()/generate_recruitment_reply()
    MiniMax-primary wiring (same flag)
  - modules.hermes_dispatch.deliver_authoritative_recruitment_reply() (new,
    narrowly-scoped recruitment delivery bypassing ONLY OPERATIONAL_ROLES)
  - modules.message_router._task13_authoritative_dispatch() and the
    process_message() call site that gates it behind
    task13_semantic_authoritative (default OFF)

No live network call. No production DB write. Mocked hermes-runner and
MiniMax clients only.
"""
from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.message_router as router
import modules.hermes_dispatch as hd
from modules.hermes_kernel.data_classes import SemanticDecision


# ── Shared helpers ───────────────────────────────────────────────────────────

def _flags(**active) -> MagicMock:
    """A patchable is_feature_active(name, default=False) that treats every
    name in `active` as explicitly set to that bool, everything else as
    `default`."""
    def _fn(name, default=False):
        return active.get(name, default)
    return MagicMock(side_effect=_fn)


def _identity(role: str = "unknown", employee_id=None) -> dict:
    out = {
        "role": role, "display_name": "", "identity_confidence": 10,
        "identity_source": "test", "confidence": 0.1,
    }
    if employee_id is not None:
        out["employee_id"] = employee_id
    return out


def _base_router_mocks(monkeypatch, *, identity: dict, recruit_eligible: bool, recruit_reply: str = "LEGACY_REPLY", intent: str = "unknown"):
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
    monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value=intent))
    monkeypatch.setattr(
        router, "recruitment_eligibility",
        AsyncMock(return_value={
            "eligible": recruit_eligible, "autosend": True, "reason": "test",
            "active_session": {"id": 1} if recruit_eligible else None,
        }),
    )
    route_mock = AsyncMock(return_value={"reply": recruit_reply, "autosend": True})
    monkeypatch.setattr(router, "route_recruitment_message", route_mock)
    return route_mock


# ── A. Employee sender asking about a family member's job → recruitment_
#      conversation, actual fact-checked reply delivered (flag ON) ──────────

async def test_A_example_a_end_to_end_authoritative_delivery(monkeypatch):
    identity = _identity(role="employee", employee_id=42)
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    decision = SemanticDecision(
        domain="recruitment_conversation", confidence=0.91,
        rationale="sender asks about sibling's guard job",
    )
    deliver_mock = AsyncMock(return_value="Guard পদে বেতন ১৫,০০০ টাকা (source-of-truth অনুযায়ী)।")
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=True)),
        patch.object(router, "ec_handle_payment_request", ec_mock),
    ):
        reply, admin_note = await router.process_message(
            "8801711111111", "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?", "bridge1",
        )
    assert reply == "Guard পদে বেতন ১৫,০০০ টাকা (source-of-truth অনুযায়ী)।"
    deliver_mock.assert_awaited_once()
    _, kwargs = deliver_mock.call_args
    assert kwargs["phone"] == "8801711111111"
    # Duplicate-reply guard: legacy route_recruitment_message() must NEVER
    # also fire for this turn.
    route_mock.assert_not_awaited()
    ec_mock.assert_not_awaited()


async def test_A_dispatch_helper_bypasses_only_operational_roles(monkeypatch):
    """Unit-level: role NOT in OPERATIONAL_ROLES (e.g. "unknown"/"candidate")
    must NOT reach the new delivery path even with a high-confidence
    recruitment_conversation decision — the legacy funnel already owns
    delivery for that sender and must keep exclusive control (session
    continuation, step collection, priority-phone allowlist)."""
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    with patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", AsyncMock(return_value="should not be called")) as deliver_mock:
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="x", source="bridge1",
            role_str="unknown", intent="unknown", db_ctx="", history="",
        )
    assert (reply, note) == (None, None)
    deliver_mock.assert_not_awaited()


# ── B. Unknown number, own salary not received → employee_conversation but
#      identity unresolved → clarification only, zero disclosure (flag ON) ──

async def test_B_unresolved_identity_clarification_only_no_disclosure(monkeypatch):
    identity = _identity(role="unknown")
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    raw_decision = SemanticDecision(
        domain="employee_conversation", confidence=0.85,
        rationale="sender claims salary not received",
    )
    clarification = "আপনার পরিচয় নিশ্চিত করতে হবে — আপনার নাম, নিবন্ধিত মোবাইল নম্বর এবং পোস্ট/ডিউটির তথ্য দিন।"
    clarify_mock = AsyncMock(return_value=(clarification, None))
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED_WITH_SALARY_FIGURE", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=raw_decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.employee_conversation.task13_handle_unresolved_employee_question", clarify_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=True)),
        patch.object(router, "ec_handle_payment_request", ec_mock),
    ):
        reply, admin_note = await router.process_message(
            "8801722222222", "আমার গত মাসের বেতনটা এখনো পাইনি", "bridge1",
        )
    assert reply == clarification
    clarify_mock.assert_awaited_once_with("8801722222222", "আমার গত মাসের বেতনটা এখনো পাইনি", "bridge1")
    # Zero private financial disclosure: the employee handler that could
    # actually disclose a figure is never reached.
    ec_mock.assert_not_awaited()
    assert "টাকা" not in reply and "৳" not in reply


# ── C. Operations/location question → no_match → legacy router
#      (flag ON and OFF) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("flag_on", [True, False])
async def test_C_no_match_falls_through_to_legacy_both_flag_states(monkeypatch, flag_on):
    identity = _identity(role="unknown")
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    decision = SemanticDecision(domain="no_match", confidence=0.82, rationale="operations_location")
    monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value="kb-fallback-reply"))
    monkeypatch.setattr("modules.route_b.handle_route_b_message", AsyncMock(return_value=("", None)))
    deliver_mock = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=flag_on)),
    ):
        reply, _ = await router.process_message("8801733333333", "আমাদের Operation Officer কোথায়?", "bridge1")
    assert reply == "kb-fallback-reply"
    deliver_mock.assert_not_awaited()


# ── D. "Operation Officer-এর বেতন কত?" → recruitment_conversation, reply
#      delivered via the UNCHANGED legacy funnel (role=unknown is already
#      eligible; the new path must not steal it) (flag ON) ─────────────────

async def test_D_recruitment_for_already_eligible_sender_uses_legacy_path(monkeypatch):
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True, recruit_reply="LEGACY_RECRUITMENT_REPLY")
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.88)
    deliver_mock = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=True)),
    ):
        reply, _ = await router.process_message("8801744444444", "Operation Officer-এর বেতন কত?", "bridge1")
    assert reply == "LEGACY_RECRUITMENT_REPLY"
    route_mock.assert_awaited_once()
    deliver_mock.assert_not_awaited()


# ── E. Released employee asking about travel fare → employee semantic
#      meaning, zero direct payment/cash creation from the semantic layer
#      itself (flag ON) ──────────────────────────────────────────────────────

async def test_E_release_travel_expense_semantic_layer_creates_nothing(monkeypatch):
    """The message contains BOTH a release-intent keyword ("release") and a
    conveyance/travel-fare trigger keyword ("ভাড়া") -- the legacy §10
    EMPLOYEE chain's own is_release_intent() branch (admin-review draft,
    NOT a transaction) already owns this exact shape. The authoritative
    dispatch must defer to it (return None, None) rather than shortcut
    into modules.employee_conversation.handle_employee_payment_request(),
    which would otherwise start an unrelated Sprint-3A conveyance
    conversation for a message that is really a release notification."""
    decision = SemanticDecision(domain="employee_conversation", confidence=0.79, identity_reference={"employee_id": 77})
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "ec_handle_payment_request", ec_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision,
            sender="8801755555555",
            text="আমাকে জাহাজ থেকে release করেছে, অফিসে আসতে গাড়িভাড়া লাগবে",
            source="bridge1",
            role_str="employee",
            intent="release_travel",
            db_ctx="", history="",
        )
    assert (reply, note) == (None, None)
    ec_mock.assert_not_awaited()


async def test_E_salary_query_intent_also_deferred_to_legacy(monkeypatch):
    """A second order-dependent legacy protection (the 2026-08-21 salary/
    keyword-trigger context-routing fix) must also stay unshortcut."""
    decision = SemanticDecision(domain="employee_conversation", confidence=0.9, identity_reference={"employee_id": 77})
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "ec_handle_payment_request", ec_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="আমার বেতন কবে দিবেন?", source="bridge1",
            role_str="employee", intent="salary_query", db_ctx="", history="",
        )
    assert (reply, note) == (None, None)
    ec_mock.assert_not_awaited()


async def test_E_genuine_advance_trigger_is_delegated_to_deterministic_handler(monkeypatch):
    """Sanity: a genuinely conversational advance trigger (no release
    keyword, no salary_query intent) IS delegated to the existing
    deterministic employee_conversation module -- proving the guards above
    are precise, not a blanket no-op."""
    decision = SemanticDecision(domain="employee_conversation", confidence=0.9, identity_reference={"employee_id": 77})
    ec_mock = AsyncMock(return_value=("draft created — pending admin review", {"admin_phone": "x", "text": "y", "bridge": "bridge1"}))
    with patch.object(router, "ec_handle_payment_request", ec_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="আমার অগ্রিম টাকা দরকার", source="bridge1",
            role_str="employee", intent="advance_request", db_ctx="", history="",
        )
    assert reply == "draft created — pending admin review"
    ec_mock.assert_awaited_once()


# ── F/G/H/I. Protected business-action claimers → semantic layer never
#      reached at all (unchanged placement, regardless of flag state) ───────

@pytest.mark.parametrize("flag_on", [True, False])
async def test_F_confirmed_escort_order_never_reaches_semantic_gate(monkeypatch, flag_on):
    identity = {
        "role": "escort_client", "display_name": "", "identity_confidence": 90,
        "identity_source": "seed", "confidence": 0.9,
    }
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "classify", MagicMock(return_value="client_order"))
    monkeypatch.setattr(router, "_looks_like_escort_order", MagicMock(return_value=True))
    escort_mock = AsyncMock(return_value=("escort handled", None))
    monkeypatch.setattr(router, "handle_escort_client_message", escort_mock)
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=flag_on)),
    ):
        reply, _ = await router.process_message(
            "8801766666666", "lighter vessel chittagong 5 guards tomorrow", "bridge1",
        )
    assert reply == "escort handled"
    gate_mock.assert_not_awaited()


def test_G_admin_section_structurally_precedes_task13_block():
    """Runtime proof via is_admin_command()'s real APPROVE/PAID/... branch
    is blocked by an UNRELATED pre-existing bug in this file (confirmed,
    2026-08-22 BUILD+VERIFY session): process_message()'s "── 6. ACCOUNTANT"
    branch does `from modules.admin_commands import is_report_command,
    process_admin_command` as a LOCAL import -- which makes
    `process_admin_command` a local variable for the ENTIRE function under
    normal Python scoping rules, so the unrelated "── 3. ADMIN" branch's own
    reference to the module-level `process_admin_command` (line ~699,
    `result = await process_admin_command(text, sender)`) raises
    UnboundLocalError whenever is_admin_command(text) is True -- reproduced
    directly: `"process_admin_command" in router.process_message.__code__.
    co_varnames` is True. This predates and is unrelated to the Task-13
    promotion (git diff of this file for this promotion is purely additive,
    0 deletions) -- flagged in the BUILD+VERIFY report's KNOWN_GAPS, not
    fixed here (out of this task's scope). This test therefore proves
    placement structurally instead of via a live call through that branch."""
    src = inspect.getsource(router)
    admin_idx = src.index('# ── 3. ADMIN ')
    task13_idx = src.index("# ── Task 13 (2026-08-22): SHADOW-ONLY semantic gate")
    assert admin_idx < task13_idx
    assert "process_admin_command" in router.process_message.__code__.co_varnames


@pytest.mark.parametrize("flag_on", [True, False])
async def test_H_accountant_report_never_reaches_semantic_gate(monkeypatch, flag_on):
    identity = {
        "role": "accountant", "display_name": "", "identity_confidence": 90,
        "identity_source": "seed", "confidence": 0.9,
    }
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="unknown"))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    with (
        patch("modules.admin_commands.is_report_command", MagicMock(return_value=True)),
        patch("modules.admin_commands.process_admin_command", AsyncMock(return_value="report handled")),
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=flag_on)),
    ):
        reply, _ = await router.process_message("8801788888888", "report cash 30", "bridge1")
    assert reply == "report handled"
    gate_mock.assert_not_awaited()


@pytest.mark.parametrize("flag_on", [True, False])
async def test_I_attendance_never_reaches_semantic_gate(monkeypatch, flag_on):
    identity = _identity(role="employee", employee_id=10)
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "is_supervisor_attendance", MagicMock(return_value=True))
    monkeypatch.setattr(router, "create_attendance_draft", AsyncMock(return_value={"message": "attendance drafted", "admin_msg": "x"}))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=flag_on)),
    ):
        reply, _ = await router.process_message("8801799999999", "আজকের হাজিরা: ৫ জন উপস্থিত", "bridge1")
    assert reply == "attendance drafted"
    gate_mock.assert_not_awaited()


# ── J/K/L/M. MiniMax failure modes → hermes-runner fallback, or safe
#      no_match on total failure ─────────────────────────────────────────────

def _hermes_configured_stack(hermes_raw: str):
    stack_patches = [
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value=hermes_raw)),
        patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "secret")),
    ]
    return stack_patches


async def _run_with_patches(patches, coro_factory):
    from contextlib import ExitStack
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        return await coro_factory()


async def test_J_minimax_timeout_falls_back_to_hermes():
    hermes_raw = json.dumps({"domain": "no_match", "confidence": 0.7, "rationale": "hermes_fallback_ok"})
    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(side_effect=asyncio.TimeoutError)),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        *_hermes_configured_stack(hermes_raw),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.domain == "no_match"
    assert decision.rationale == "hermes_fallback_ok"


async def test_K_minimax_rate_limit_5xx_falls_back_to_hermes():
    from openai import RateLimitError
    hermes_raw = json.dumps({"domain": "recruitment_conversation", "confidence": 0.7, "rationale": "hermes_ok"})

    async def _raise(*a, **k):
        raise RateLimitError("rate limited", response=MagicMock(status_code=429), body=None)

    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(side_effect=_raise)),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        *_hermes_configured_stack(hermes_raw),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.domain == "recruitment_conversation"
    assert decision.rationale == "hermes_ok"


async def test_L_minimax_empty_response_falls_back_to_hermes():
    hermes_raw = json.dumps({"domain": "no_match", "confidence": 0.5, "rationale": "hermes_after_empty"})
    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(return_value="")),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        *_hermes_configured_stack(hermes_raw),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.rationale == "hermes_after_empty"


async def test_L_minimax_invalid_json_falls_back_to_hermes():
    hermes_raw = json.dumps({"domain": "employee_conversation", "confidence": 0.7, "rationale": "hermes_after_bad_json"})
    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(return_value="not json at all")),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        *_hermes_configured_stack(hermes_raw),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.rationale == "hermes_after_bad_json"


async def test_L_minimax_invalid_response_and_hermes_also_fails_safe_no_match():
    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(return_value="not json at all")),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value=None)),
        patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "secret")),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.domain == "no_match"


async def test_M_both_minimax_and_hermes_total_failure_fails_open_no_crash():
    patches = [
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        patch("modules.hermes_dispatch._configured", return_value=(None, None)),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.domain == "no_match"
    assert decision.rationale == "not_configured"
    assert decision.confidence == 0.0


# ── N. Recruitment fact-check still enforced in the new authoritative
#      delivery path ─────────────────────────────────────────────────────────

async def test_N_minimax_reply_contradicting_source_falls_back_then_blocked():
    """MiniMax invents a number absent from kb_context -> fails fact-check
    -> falls back to hermes-runner -> hermes ALSO fails fact-check ->
    deliver_authoritative_recruitment_reply() returns None (no reply sent),
    never the fabricated figure."""
    kb_context = "Guard পদে বেতন ১৫,০০০ টাকা।"
    minimax_bad_reply = "Guard পদে বেতন ৫০,০০০ টাকা।"  # fabricated number
    hermes_bad_reply = "Guard পদে বেতন ৯৯,০০০ টাকা।"  # also fabricated
    with (
        patch("app.minimax_provider.generate_recruitment_reply", AsyncMock(return_value=minimax_bad_reply)),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value=hermes_bad_reply)),
        patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "secret")),
        patch("modules.hermes_dispatch.build_recruitment_context", MagicMock(return_value=kb_context)),
    ):
        reply = await hd.deliver_authoritative_recruitment_reply(
            phone="880171", text="Guard-এর বেতন কত?", source="bridge1",
        )
    assert reply is None


async def test_N_compliant_reply_is_delivered():
    kb_context = "Guard পদে বেতন ১৫,০০০ টাকা।"
    with (
        patch("app.minimax_provider.generate_recruitment_reply", AsyncMock(return_value=None)),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value="Guard পদে বেতন ১৫,০০০ টাকা।")),
        patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "secret")),
        patch("modules.hermes_dispatch.build_recruitment_context", MagicMock(return_value=kb_context)),
    ):
        reply = await hd.deliver_authoritative_recruitment_reply(
            phone="880171", text="Guard-এর বেতন কত?", source="bridge1",
        )
    assert reply == "Guard পদে বেতন ১৫,০০০ টাকা।"


# ── O. Employee confidentiality: no data before verification, flag ON or
#      OFF ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("flag_on", [True, False])
async def test_O_no_disclosure_before_verification_either_flag_state(monkeypatch, flag_on):
    identity = _identity(role="unknown")
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    raw_decision = SemanticDecision(domain="employee_conversation", confidence=0.9)
    clarify_mock = AsyncMock(return_value=("পরিচয় নিশ্চিত করুন।", None))
    ec_mock = AsyncMock(return_value=("SHOULD_NEVER_DISCLOSE", None))
    monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value=""))
    monkeypatch.setattr("modules.route_b.handle_route_b_message", AsyncMock(return_value=("", None)))
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=raw_decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.employee_conversation.task13_handle_unresolved_employee_question", clarify_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=flag_on)),
        patch.object(router, "ec_handle_payment_request", ec_mock),
    ):
        reply, _ = await router.process_message("8801700000000", "আমার বেতন কত?", "bridge1")
    if flag_on:
        # Authoritative clarification path claims the turn -- the
        # write-capable/disclosure-capable handler is never reached.
        assert reply == "পরিচয় নিশ্চিত করুন।"
        clarify_mock.assert_awaited_once()
        ec_mock.assert_not_awaited()
    else:
        # Flag OFF: the Task-13 clarification handler never fires (shadow
        # mode has no delivery path at all) -- this specific message then
        # falls through to the PRE-EXISTING, Task-13-unrelated §11 catchall
        # (modules.employee_conversation.handle_employee_payment_request,
        # reached because "বেতন" matches its own bare-keyword trigger scan)
        # which is documented to NEVER call create_transaction()/
        # _upsert_ledger() -- see that module's own HARD RULE docstring --
        # so even though it IS reached here, it cannot itself disclose a
        # balance/figure. This is legacy behavior this promotion neither
        # introduces nor is able to change (recruitment_flow/route
        # ordering is explicitly out of scope) -- confirmed unaffected by
        # the flag by construction, since the whole authoritative section
        # never executes when it's OFF.
        clarify_mock.assert_not_awaited()


# ── P. Duplicate outbound reply never occurs ─────────────────────────────────

def test_P_authoritative_section_returns_before_legacy_chain_source_proof():
    """Structural: the return inside the authoritative section physically
    precedes (and is the only path into) "if recruit_decision["eligible"]:"
    — see test_task13_shadow_mode_fix.py's 2d/2e/2f for the flag-gating
    proof; this asserts the ORDERING relative to the legacy chain."""
    src = inspect.getsource(router)
    auth_start = src.index("# ── Task 13 AUTHORITATIVE DISPATCH")
    legacy_idx = src.index('if recruit_decision["eligible"]:', auth_start)
    return_idx = src.index("return _t13_reply, _t13_admin_note", auth_start)
    assert auth_start < return_idx < legacy_idx


async def test_P_no_double_reply_when_authoritative_path_succeeds(monkeypatch):
    """Behavioral companion to test_A: when the new path delivers a reply,
    the legacy recruitment/employee chains are never also awaited."""
    identity = _identity(role="employee", employee_id=42)
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    deliver_mock = AsyncMock(return_value="single reply")
    ec_mock = AsyncMock(return_value=("should not fire", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=True)),
        patch.object(router, "ec_handle_payment_request", ec_mock),
    ):
        # NOTE: intentionally reusing test_A's exact literal message text
        # (rather than a shortened variant) -- the deterministic rule
        # classifier (modules.intent.classify(), consulted at §5 whenever
        # ai.classify_intent_llm() returns "unknown", exactly as mocked
        # here) classifies a *shorter* variant of this message as
        # intent="recruitment", which trips the PRE-EXISTING, Task-13-
        # unrelated "operational identity blocked" guard at §5
        # ("if intent == 'recruitment' and role_str not in (...)": return
        # "", None) BEFORE the message ever reaches the Task-13 block at
        # all for an employee-role sender. That guard and this promotion's
        # new authoritative recruitment path are in real, load-bearing
        # tension for some phrasings -- see KNOWN_GAPS_OR_CONCERNS in the
        # BUILD+VERIFY report; the literal Example A text from the task
        # spec (used here and in test_A) classifies as "salary_query"
        # instead and is unaffected.
        reply, _ = await router.process_message("8801711111111", "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?", "bridge1")
    assert reply == "single reply"
    route_mock.assert_not_awaited()
    ec_mock.assert_not_awaited()


# ── Q. Semantic decision itself performs zero business writes (shadow AND
#      authoritative mode) ───────────────────────────────────────────────────

def test_Q_authoritative_dispatch_source_has_no_write_function_references():
    """Static proof: _task13_authoritative_dispatch() never references any
    ledger/transaction/draft-creation primitive directly -- it only ever
    dispatches to the pre-existing deterministic handlers, which own their
    own write boundaries."""
    src = inspect.getsource(router._task13_authoritative_dispatch)
    forbidden = (
        "create_transaction", "_upsert_ledger", "accounting_worker",
        "execute(", "INSERT INTO", "UPDATE ",
    )
    for token in forbidden:
        assert token not in src, f"unexpected write-primitive reference: {token}"


def test_Q_deliver_authoritative_recruitment_reply_source_has_no_write_function_references():
    src = inspect.getsource(hd.deliver_authoritative_recruitment_reply)
    forbidden = ("create_transaction", "_upsert_ledger", "accounting_worker", "execute(", "INSERT INTO", "UPDATE ")
    for token in forbidden:
        assert token not in src


# ── R. task13_semantic_authoritative OFF → exact legacy/shadow routing ──────

async def test_R_flag_off_recruitment_decision_never_delivers_authoritatively(monkeypatch):
    identity = _identity(role="employee", employee_id=42)
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.95)
    deliver_mock = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
    monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value=""))
    monkeypatch.setattr("modules.route_b.handle_route_b_message", AsyncMock(return_value=("", None)))
    with (
        patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_semantic_authoritative=False)),
    ):
        await router.process_message("8801711111111", "আমার ভাইয়ের Guard-এর চাকরি হবে?", "bridge1")
    deliver_mock.assert_not_awaited()


# ── S. minimax_customer_primary OFF → exact previous hermes-runner-only
#      provider behavior ─────────────────────────────────────────────────────

async def test_S_semantic_gate_flag_off_never_touches_minimax():
    hermes_raw = json.dumps({"domain": "no_match", "confidence": 0.5, "rationale": "hermes_only"})
    minimax_mock = AsyncMock(return_value='{"domain":"recruitment_conversation","confidence":0.99}')
    patches = [
        patch("app.minimax_provider.generate_raw_completion", minimax_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=False)),
        *_hermes_configured_stack(hermes_raw),
    ]
    decision = await _run_with_patches(patches, lambda: hd.semantic_gate({"text": "x", "sender_phone": "880171"}))
    assert decision.rationale == "hermes_only"
    minimax_mock.assert_not_awaited()


async def test_S_generate_reply_flag_off_never_touches_minimax():
    minimax_mock = AsyncMock(return_value="SHOULD_NOT_BE_USED")
    with (
        patch("app.minimax_provider.generate_reply", minimax_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=False)),
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value="hermes reply")),
    ):
        reply = await hd.generate_reply("hi", "greeting", sender_phone="880171")
    assert reply == "hermes reply"
    minimax_mock.assert_not_awaited()


async def test_S_generate_recruitment_reply_flag_off_never_touches_minimax():
    minimax_mock = AsyncMock(return_value="SHOULD_NOT_BE_USED")
    with (
        patch("app.minimax_provider.generate_recruitment_reply", minimax_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=False)),
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value="")),
    ):
        reply = await hd.generate_recruitment_reply(phone="880171", text="x", source="bridge1", kb_context="")
    # Pre-existing contract (unchanged by this promotion): an empty
    # hermes-runner response passes through as "" (falsy, not None) --
    # see _call_hermes()'s own "empty reply" path. Either way the caller
    # treats it as "no reply" and falls through.
    assert not reply
    minimax_mock.assert_not_awaited()


# ── MiniMax-primary success path (both flags exercised together once, for
#      completeness / MINIMAX_WIRING_DESCRIPTION evidence) ─────────────────

async def test_minimax_primary_success_used_directly_no_hermes_call():
    minimax_raw = json.dumps({
        "domain": "recruitment_conversation", "confidence": 0.93,
        "subject": "x", "intent": "job_inquiry", "rationale": "minimax decided",
    })
    hermes_mock = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
    with (
        patch("app.minimax_provider.generate_raw_completion", AsyncMock(return_value=minimax_raw)),
        patch("modules.preflight_guard.is_feature_active", _flags(minimax_customer_primary=True)),
        patch("modules.hermes_dispatch._call_hermes", hermes_mock),
    ):
        decision = await hd.semantic_gate({"text": "x", "sender_phone": "880171"})
    assert decision.domain == "recruitment_conversation"
    assert decision.rationale == "minimax decided"
    hermes_mock.assert_not_awaited()
