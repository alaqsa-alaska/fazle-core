"""
Task-14B (2026-08-22, BUILD+VERIFY, persistent task #14) — smallest safe
foundation for future full Recruitment semantic authority.

Covers Required Tests A-S from the Task-14B spec:
  A/B/C — G6 narrowing (modules.message_router, tier §5/§6 area) no longer
          silently drops an operational sender's recruitment-shaped
          message before the semantic gate, while a residual keyword-
          collision risk this exposes (tier §10/§11 ec_detect_trigger())
          is separately guarded so a recruitment question is never
          misrouted into the sender's own payroll/advance workflow.
  D/E/F — modules.message_router._task13_authoritative_dispatch()'s NEW
          candidate/new_lead/unknown branch, gated by the NEW SEPARATE
          flag task13_candidate_recruitment_authoritative (default OFF,
          never expands the meaning of the existing
          task13_recruitment_authoritative flag), only acting when the
          legacy deterministic recruit_decision did NOT already claim the
          turn (so active-session continuation, test E, stays on its
          original code path).
  G/H/I/J — protected business-action boundaries (escort/admin/
          accountant/attendance) are structurally unaffected -- proven
          via the same "admin/attendance/escort sections precede/are
          independent of the Task-13 block" style already used in
          tests/unit/test_task13_authoritative_mode.py.
  K/L — semantic gate failure/timeout or invalid/low-confidence decision
          -> legacy fallback (the dispatch function's own domain match
          already guarantees this; covered directly here).
  M/N — modules.route_b_handoff.evaluate_handoff() Route-B dedup: at most
          ONE domain-classification LLM call per turn when a valid central
          decision already exists; existing classify_route_b_category()
          fallback unchanged when it doesn't.
  O — candidate semantic reply still goes through the existing
          fact-checked delivery chain (route_recruitment_message(),
          NEVER the OPERATIONAL_ROLES-only
          deliver_authoritative_recruitment_reply() bypass).
  P/Q/R/S — flag-matrix independence: both OFF matches legacy (except the
          deliberate G6 correction); each flag independently governs only
          its own population; both ON governs both populations without a
          second domain-classification LLM call.

No live network call. No production DB write. Mocked hermes-runner/
Ollama/DB-touching functions only.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.message_router as router
from modules.hermes_kernel.data_classes import SemanticDecision


# ── Shared helpers (mirrors tests/unit/test_task13_authoritative_mode.py) ──

def _flags(**active) -> MagicMock:
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


def _base_router_mocks(
    monkeypatch, *, identity: dict, recruit_eligible: bool,
    recruit_reply: str = "LEGACY_REPLY", intent: str = "unknown",
    active_session=None,
):
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
    monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value=intent))
    monkeypatch.setattr(
        router, "recruitment_eligibility",
        AsyncMock(return_value={
            "eligible": recruit_eligible, "autosend": True, "reason": "test",
            "active_session": active_session,
        }),
    )
    route_mock = AsyncMock(return_value={"reply": recruit_reply, "autosend": True})
    monkeypatch.setattr(router, "route_recruitment_message", route_mock)
    return route_mock


# ═══════════════════════════════════════════════════════════════════════════
# A. Employee recruitment-shaped message — G6 no longer silent-blocks —
#    semantic gate reached.
# ═══════════════════════════════════════════════════════════════════════════

async def test_A_g6_no_longer_silently_blocks_employee_recruitment_message(monkeypatch):
    identity = _identity(role="employee", employee_id=42)
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False, intent="recruitment")
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match", confidence=0.0))
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_MATTER", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.preflight_guard.is_feature_active", _flags()),
        patch.object(router, "ec_handle_payment_request", ec_mock),
    ):
        await router.process_message(
            "8801711111111", "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?", "bridge1",
        )
    # The old G6 behavior returned "" immediately and NEVER reached the
    # shadow semantic_gate() call at all. Narrowed G6 must let this turn
    # reach it.
    gate_mock.assert_awaited_once()


async def test_A_pre_narrowing_behavior_would_have_blocked_gate_ever_being_reached():
    """Documents the exact bug this test proves fixed: the OLD condition
    literally still exists in the source (unchanged predicate), only its
    *action* changed from `return "", None` to a log-only continuation."""
    import inspect
    src = inspect.getsource(router)
    assert 'intent == "recruitment" and role_str not in ("candidate", "new_lead", "unknown")' in src
    # The narrowed version must NOT return immediately from this predicate.
    idx = src.index('if intent == "recruitment" and role_str not in ("candidate", "new_lead", "unknown"):')
    following = src[idx: idx + 400]
    assert 'return "", None' not in following


# ═══════════════════════════════════════════════════════════════════════════
# B. Employee "Operation Officer-এর বেতন কত?" — a bare "বেতন" keyword scan
#    must NOT select the sender's own payroll/advance workflow merely
#    because intent classified as "recruitment".
# ═══════════════════════════════════════════════════════════════════════════

async def test_B_recruitment_intent_not_captured_by_bare_salary_keyword_trigger(monkeypatch):
    identity = _identity(role="employee", employee_id=42)
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False, intent="recruitment")
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match", confidence=0.0))
    ec_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.preflight_guard.is_feature_active", _flags()),
        patch.object(router, "ec_handle_payment_request", ec_mock),
        patch("modules.knowledge_base.get_reply", AsyncMock(return_value="")),
    ):
        await router.process_message(
            "8801711111111", "Operation Officer-এর বেতন কত?", "bridge1",
        )
    # ec_detect_trigger() would match "বেতন" as a bare salary keyword --
    # the intent=="recruitment" guard at both ec_trigger call sites (tier
    # §10 and the §11 catchall) must prevent ec_handle_payment_request()
    # from ever being invoked for this turn.
    ec_mock.assert_not_awaited()


def test_B_ec_trigger_guarded_by_recruitment_intent_at_both_call_sites():
    """Structural guard: both ec_detect_trigger(text) call sites in
    message_router must skip the bare keyword scan when intent=="recruitment"."""
    import inspect
    src = inspect.getsource(router)
    assert 'ec_trigger = ec_detect_trigger(text) if intent != "recruitment" else None' in src
    assert 'ec_trigger_catchall = ec_detect_trigger(text) if intent != "recruitment" else None' in src


# ═══════════════════════════════════════════════════════════════════════════
# C. Employee "আমি গত মাসের বেতন পাইনি" — genuine salary_query/payment_due
#    employee/payroll path remains completely unchanged.
# ═══════════════════════════════════════════════════════════════════════════

async def test_C_employee_payroll_path_unchanged(monkeypatch):
    identity = _identity(role="employee", employee_id=42)
    _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False, intent="salary_query")
    monkeypatch.setattr(router, "get_payroll_summary", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "format_payroll_context", MagicMock(return_value="payroll context"))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match", confidence=0.0))
    ai_mock = AsyncMock(return_value="আপনার বেতন ১৫,০০০ টাকা, গত ৫ তারিখে পাঠানো হয়েছে।")
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.preflight_guard.is_feature_active", _flags()),
        patch("modules.rag.safe_search", AsyncMock(return_value=[])),
        patch.object(router.ai, "generate_reply", ai_mock),
    ):
        reply, _ = await router.process_message(
            "8801711111111", "আমি গত মাসের বেতন পাইনি", "bridge1",
        )
    assert reply == "আপনার বেতন ১৫,০০০ টাকা, গত ৫ তারিখে পাঠানো হয়েছে।"
    ai_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# D. unknown "Security Guard-এর চাকরি আছে?" — new candidate flag OFF vs ON.
# ═══════════════════════════════════════════════════════════════════════════

async def test_D_candidate_flag_off_new_branch_is_a_no_op():
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    dispatch_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", dispatch_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="8801700000000", text="Security Guard-এর চাকরি আছে?",
            source="bridge1", role_str="unknown", intent="recruitment",
            db_ctx="", history="",
            recruitment_authoritative=False, employee_authoritative=False,
            candidate_recruitment_authoritative=False,
            legacy_recruitment_eligible=False,
        )
    assert (reply, note) == (None, None)
    dispatch_mock.assert_not_awaited()


async def test_D_candidate_flag_on_semantic_authority_used():
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    dispatch_mock = AsyncMock(return_value=("সিকিউরিটি গার্ড পদে আবেদন করতে আপনার নাম দিন।", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", dispatch_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="8801700000000", text="Security Guard-এর চাকরি আছে?",
            source="bridge1", role_str="unknown", intent="recruitment",
            db_ctx="", history="",
            recruitment_authoritative=False, employee_authoritative=False,
            candidate_recruitment_authoritative=True,
            legacy_recruitment_eligible=False,
        )
    assert reply == "সিকিউরিটি গার্ড পদে আবেদন করতে আপনার নাম দিন।"
    dispatch_mock.assert_awaited_once()


async def test_D_new_flag_never_bypasses_operational_roles_delivery():
    """The new candidate flag must NEVER unlock the OPERATIONAL_ROLES
    delivery path (deliver_authoritative_recruitment_reply) -- that stays
    governed exclusively by task13_recruitment_authoritative."""
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    deliver_mock = AsyncMock(return_value="SHOULD_NOT_BE_CALLED")
    with patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="x", source="bridge1",
            role_str="employee", intent="recruitment", db_ctx="", history="",
            recruitment_authoritative=False, employee_authoritative=False,
            candidate_recruitment_authoritative=True,
            legacy_recruitment_eligible=False,
        )
    assert (reply, note) == (None, None)
    deliver_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# E. candidate active session, plain funnel answer "25" — existing
#    active-session intake behavior preserved.
# ═══════════════════════════════════════════════════════════════════════════

async def test_E_active_session_continuation_not_double_handled():
    """When the legacy deterministic recruit_decision already found this
    turn eligible (e.g. active_session_continuation), the new
    candidate-population branch must be a pure no-op regardless of the
    new flag -- the legacy §7/8 path (unchanged) owns delivery."""
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    dispatch_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", dispatch_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="8801700000000", text="25",
            source="bridge1", role_str="candidate", intent="recruitment",
            db_ctx="", history="",
            recruitment_authoritative=False, employee_authoritative=False,
            candidate_recruitment_authoritative=True,
            legacy_recruitment_eligible=True,   # active_session_continuation
        )
    assert (reply, note) == (None, None)
    dispatch_mock.assert_not_awaited()


async def test_E_process_message_active_session_still_goes_through_legacy_route(monkeypatch):
    identity = _identity(role="candidate")
    route_mock = _base_router_mocks(
        monkeypatch, identity=identity, recruit_eligible=True,
        recruit_reply="আপনার বয়স নথিভুক্ত হলো।", intent="unknown",
        active_session={"id": 5, "collection_step": "age"},
    )
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="recruitment_conversation", confidence=0.9))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_candidate_recruitment_authoritative=True)),
    ):
        reply, _ = await router.process_message("8801700000001", "25", "bridge1")
    assert reply == "আপনার বয়স নথিভুক্ত হলো।"
    route_mock.assert_awaited_once()
    _, kwargs = route_mock.call_args
    # Legacy call site never passes force_eligible=True -- that kwarg is
    # exclusive to the new semantic-authoritative candidate branch.
    assert "force_eligible" not in kwargs


# ═══════════════════════════════════════════════════════════════════════════
# F. unknown "Operation Officer কোথায়?" — no_match — existing fallback.
# ═══════════════════════════════════════════════════════════════════════════

async def test_F_no_match_domain_is_a_complete_no_op():
    decision = SemanticDecision(domain="no_match", confidence=0.0, rationale="no_recruitment_context")
    dispatch_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", dispatch_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="Operation Officer কোথায়?",
            source="bridge1", role_str="unknown", intent="unknown",
            db_ctx="", history="",
            recruitment_authoritative=True, employee_authoritative=True,
            candidate_recruitment_authoritative=True,
            legacy_recruitment_eligible=False,
        )
    assert (reply, note) == (None, None)
    dispatch_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# G/H/I/J — protected business-action boundaries structurally unaffected.
# ═══════════════════════════════════════════════════════════════════════════

def test_G_escort_admin_attendance_sections_precede_g6_and_task13():
    import inspect
    src = inspect.getsource(router)
    escort_idx = src.index("# ── 2. ESCORT CLIENT roles")
    admin_idx = src.index("# ── 3. ADMIN ")
    attendance_idx = src.index("# ── 4. ATTENDANCE")
    g6_idx = src.index("# G6 (predates Task-13")
    task13_idx = src.index("# ── Task 13 (2026-08-22): SHADOW-ONLY semantic gate")
    assert escort_idx < admin_idx < attendance_idx < g6_idx < task13_idx


async def test_H_accountant_report_command_never_reaches_semantic_gate(monkeypatch):
    identity = {
        "role": "accountant", "display_name": "", "identity_confidence": 90,
        "identity_source": "seed", "confidence": 0.9,
    }
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="unknown"))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    report_mock = AsyncMock(return_value="report handled")
    with (
        patch("modules.admin_commands.is_report_command", MagicMock(return_value=True)),
        patch.object(router, "process_admin_command", report_mock),
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_candidate_recruitment_authoritative=True)),
    ):
        await router.process_message("8801788888888", "report cash 30", "bridge1")
    gate_mock.assert_not_awaited()


async def test_I_admin_command_never_reaches_semantic_gate(monkeypatch):
    identity = {
        "role": "admin", "display_name": "", "identity_confidence": 90,
        "identity_source": "seed", "confidence": 0.9,
    }
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    with (
        patch("modules.admin_commands.is_admin_command", MagicMock(return_value=True)),
        patch.object(router, "process_admin_command", AsyncMock(return_value=("APPROVED", None))),
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_candidate_recruitment_authoritative=True)),
    ):
        await router.process_message("8801799999999", "APPROVE 12", "bridge1")
    gate_mock.assert_not_awaited()


async def test_J_attendance_never_reaches_semantic_gate(monkeypatch):
    identity = _identity(role="employee", employee_id=10)
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    gate_mock = AsyncMock(return_value=SemanticDecision(domain="no_match"))
    with (
        patch("modules.message_router.is_supervisor_attendance", MagicMock(return_value=True)),
        patch("modules.message_router.create_attendance_draft", AsyncMock(return_value={"message": "ok", "admin_msg": "ok"})),
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.preflight_guard.is_feature_active", _flags(task13_candidate_recruitment_authoritative=True)),
    ):
        await router.process_message("8801711111112", "attendance today present 5", "bridge1")
    gate_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# K. semantic timeout/error → legacy routing fallback.
# ═══════════════════════════════════════════════════════════════════════════

async def test_K_semantic_gate_exception_falls_back_to_legacy(monkeypatch):
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(
        monkeypatch, identity=identity, recruit_eligible=True,
        recruit_reply="LEGACY_FALLBACK_REPLY", intent="unknown",
    )
    gate_mock = AsyncMock(side_effect=TimeoutError("semantic gate timed out"))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch(
            "modules.preflight_guard.is_feature_active",
            _flags(
                task13_recruitment_authoritative=True,
                task13_employee_authoritative=True,
                task13_candidate_recruitment_authoritative=True,
            ),
        ),
    ):
        reply, _ = await router.process_message("8801700000002", "চাকরি চাই", "bridge1")
    assert reply == "LEGACY_FALLBACK_REPLY"
    route_mock.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# L. invalid/low-confidence SemanticDecision → legacy fallback.
# ═══════════════════════════════════════════════════════════════════════════

async def test_L_low_confidence_decision_is_a_complete_no_op():
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.2, rationale="low_confidence")
    dispatch_mock = AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", dispatch_mock):
        reply, note = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="চাকরি চাই", source="bridge1",
            role_str="unknown", intent="recruitment", db_ctx="", history="",
            recruitment_authoritative=True, employee_authoritative=True,
            candidate_recruitment_authoritative=True,
            legacy_recruitment_eligible=False,
        )
    assert (reply, note) == (None, None)
    dispatch_mock.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# M/N. Route-B dedup: modules.route_b_handoff.evaluate_handoff().
# ═══════════════════════════════════════════════════════════════════════════

async def test_M_central_valid_decision_skips_second_llm_classifier(monkeypatch):
    from modules.route_b_handoff import evaluate_handoff
    import modules.route_b_handoff as rbh

    monkeypatch.setattr(rbh, "get_settings", MagicMock(return_value=MagicMock(route_b_handoff_enabled=True)))
    monkeypatch.setattr(rbh, "_check_active_recruitment_session", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_record_handoff", AsyncMock(return_value=None))
    classify_mock = AsyncMock(return_value="none")
    monkeypatch.setattr(rbh.ollama_mod, "classify_route_b_category", classify_mock)
    monkeypatch.setattr(
        rbh, "_check_recruitment_handoff",
        AsyncMock(return_value=rbh.HandoffResult(
            reply="আপনার নাম লিখুন।", admin_note=None, destination="recruitment",
            reason="central_decision_reused", confidence="central_gate", outcome="handed_off",
        )),
    )
    monkeypatch.setattr(rbh, "_check_employee_claim_handoff", AsyncMock(return_value=None))

    central_decision = SemanticDecision(domain="recruitment_conversation", confidence=0.85)
    result = await evaluate_handoff(
        sender="8801700000003", text="Security Guard job?", source="bridge1",
        identity={"identity_role": "unknown"}, intent="unknown", history="",
        message_id="m1", central_semantic_decision=central_decision,
    )
    assert result is not None
    assert result.reply == "আপনার নাম লিখুন।"
    # The whole point of the dedup: classify_route_b_category() (a second
    # domain-classification LLM call) must never fire when a valid central
    # decision already exists.
    classify_mock.assert_not_awaited()


async def test_N_no_central_decision_uses_existing_classifier_fallback(monkeypatch):
    from modules.route_b_handoff import evaluate_handoff
    import modules.route_b_handoff as rbh

    monkeypatch.setattr(rbh, "get_settings", MagicMock(return_value=MagicMock(route_b_handoff_enabled=True)))
    monkeypatch.setattr(rbh, "_check_active_recruitment_session", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_record_handoff", AsyncMock(return_value=None))
    classify_mock = AsyncMock(return_value="none")
    monkeypatch.setattr(rbh.ollama_mod, "classify_route_b_category", classify_mock)
    monkeypatch.setattr(rbh, "_check_recruitment_handoff", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_check_employee_claim_handoff", AsyncMock(return_value=None))

    result = await evaluate_handoff(
        sender="8801700000004", text="hi", source="bridge1",
        identity={"identity_role": "unknown"}, intent="unknown", history="",
        message_id="m2", central_semantic_decision=None,
    )
    assert result is None
    # No central decision available at all -- existing Route-B fallback
    # classifier must still run exactly as before this dedup existed.
    classify_mock.assert_awaited_once()


async def test_N_no_match_central_decision_also_uses_classifier_fallback(monkeypatch):
    """Per the Phase spec: 'classification returned no_match ... then
    existing Route-B fallback may run' -- a genuinely negative central
    decision does NOT suppress classify_route_b_category() (needed for
    Stage 2 employee-claim detection, which the central gate's domain enum
    has no equivalent for)."""
    from modules.route_b_handoff import evaluate_handoff
    import modules.route_b_handoff as rbh

    monkeypatch.setattr(rbh, "get_settings", MagicMock(return_value=MagicMock(route_b_handoff_enabled=True)))
    monkeypatch.setattr(rbh, "_check_active_recruitment_session", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_record_handoff", AsyncMock(return_value=None))
    classify_mock = AsyncMock(return_value="employee_claim")
    monkeypatch.setattr(rbh.ollama_mod, "classify_route_b_category", classify_mock)
    monkeypatch.setattr(rbh, "_check_recruitment_handoff", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_check_employee_claim_handoff", AsyncMock(return_value=None))

    central_decision = SemanticDecision(domain="no_match", confidence=0.0, rationale="no_recruitment_context")
    await evaluate_handoff(
        sender="8801700000005", text="my employee id is 55", source="bridge1",
        identity={"identity_role": "unknown"}, intent="unknown", history="",
        message_id="m3", central_semantic_decision=central_decision,
    )
    classify_mock.assert_awaited_once()


async def test_M_low_confidence_central_decision_still_uses_classifier(monkeypatch):
    """A central decision that exists but is below the 0.6 threshold must
    NOT be treated as a valid recruitment signal for dedup purposes."""
    from modules.route_b_handoff import evaluate_handoff
    import modules.route_b_handoff as rbh

    monkeypatch.setattr(rbh, "get_settings", MagicMock(return_value=MagicMock(route_b_handoff_enabled=True)))
    monkeypatch.setattr(rbh, "_check_active_recruitment_session", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_record_handoff", AsyncMock(return_value=None))
    classify_mock = AsyncMock(return_value="none")
    monkeypatch.setattr(rbh.ollama_mod, "classify_route_b_category", classify_mock)
    monkeypatch.setattr(rbh, "_check_recruitment_handoff", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_check_employee_claim_handoff", AsyncMock(return_value=None))

    central_decision = SemanticDecision(domain="recruitment_conversation", confidence=0.3)
    await evaluate_handoff(
        sender="8801700000006", text="hmm maybe a job?", source="bridge1",
        identity={"identity_role": "unknown"}, intent="unknown", history="",
        message_id="m4", central_semantic_decision=central_decision,
    )
    classify_mock.assert_awaited_once()


async def test_route_b_call_site_threads_sem_decision(monkeypatch):
    """message_router's ROUTE_B_ROLES call site must pass the shadow
    block's own sem_decision through to handle_route_b_message(), not a
    freshly-recomputed or hardcoded value."""
    identity = _identity(role="unknown")
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
    monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(
        router, "recruitment_eligibility",
        AsyncMock(return_value={"eligible": False, "autosend": False, "reason": "test", "active_session": None}),
    )
    decision = SemanticDecision(domain="no_match", confidence=0.1)
    gate_mock = AsyncMock(return_value=decision)
    route_b_mock = AsyncMock(return_value=("route b reply", None))
    with (
        patch("modules.hermes_dispatch.semantic_gate", gate_mock),
        patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})),
        patch("modules.preflight_guard.is_feature_active", _flags()),
        patch("modules.route_b.handle_route_b_message", route_b_mock),
    ):
        await router.process_message("8801700000007", "hi there", "bridge1")
    route_b_mock.assert_awaited_once()
    _, kwargs = route_b_mock.call_args
    # validate_semantic_decision() may return a new SemanticDecision object
    # (e.g. it rewrites the rationale on a low-confidence decision) rather
    # than the exact raw object semantic_gate() returned -- compare the
    # domain/confidence that actually reached Route B, not object identity.
    threaded = kwargs.get("central_semantic_decision")
    assert threaded is not None
    assert threaded.domain == decision.domain
    assert threaded.confidence == decision.confidence


# ═══════════════════════════════════════════════════════════════════════════
# O. Candidate semantic reply: recruitment fact-check still enforced.
# ═══════════════════════════════════════════════════════════════════════════

async def test_O_candidate_dispatch_uses_route_recruitment_message_not_ungated_bypass():
    """_dispatch_candidate_recruitment_semantic() must go through
    route_recruitment_message() (which fact-checks via generate_
    recruitment_reply()/_recruitment_reply_matches_source() internally)
    -- NEVER modules.hermes_dispatch.deliver_authoritative_recruitment_
    reply(), which is exclusively the OPERATIONAL_ROLES bypass path."""
    route_mock = AsyncMock(return_value={"reply": "আপনার নাম লিখুন।", "eligible": True})
    deliver_mock = AsyncMock(return_value="SHOULD_NEVER_BE_CALLED")
    with (
        patch.object(router, "route_recruitment_message", route_mock),
        patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock),
    ):
        reply, note = await router._dispatch_candidate_recruitment_semantic(
            sender="8801700000008", text="Security Guard-এর চাকরি আছে?",
            source="bridge1", role_str="unknown", intent="recruitment",
            db_ctx="", history="", message_id=None,
        )
    assert reply == "আপনার নাম লিখুন।"
    route_mock.assert_awaited_once()
    _, kwargs = route_mock.call_args
    assert kwargs["force_eligible"] is True
    deliver_mock.assert_not_awaited()


async def test_O_route_recruitment_message_force_eligible_still_fact_checks(monkeypatch):
    """force_eligible=True must not skip the fact-checked LLM reply
    machinery -- it must go through the SAME generate_recruitment_reply()
    call the ordinary eligible=True path already uses (which itself
    enforces _recruitment_reply_matches_source() -- covered by existing
    hermes_dispatch tests, not re-derived here)."""
    from modules.recruitment_flow import route_recruitment_message

    with (
        patch("modules.recruitment_flow.recruitment_eligibility", AsyncMock(return_value={
            "eligible": False, "autosend": False, "reason": "no_recruitment_context",
            "active_session": None,
        })),
        patch("modules.recruitment_flow.get_active_session", AsyncMock(return_value=None)),
        patch("modules.preflight_guard.is_feature_active", MagicMock(return_value=False)),
        patch("modules.conversation_layer.generate_recruitment_reply", AsyncMock(return_value="একটি তথ্যভিত্তিক, fact-checked উত্তর।")) as gen_mock,
    ):
        result = await route_recruitment_message(
            "8801700000009", "Security Guard-এর চাকরি আছে?",
            source="bridge1", role="unknown", intent="recruitment",
            force_eligible=True,
        )
    assert result["reply"] == "একটি তথ্যভিত্তিক, fact-checked উত্তর।"
    gen_mock.assert_awaited_once()


async def test_O_route_recruitment_message_without_force_eligible_unchanged():
    """force_eligible defaults to False -- every pre-existing caller
    (legacy §7/8, route_b_handoff) is byte-identical to before this
    parameter existed."""
    from modules.recruitment_flow import route_recruitment_message

    with patch("modules.recruitment_flow.recruitment_eligibility", AsyncMock(return_value={
        "eligible": False, "autosend": False, "reason": "no_recruitment_context",
        "active_session": None,
    })):
        result = await route_recruitment_message(
            "8801700000010", "hello", source="bridge1", role="unknown", intent="unknown",
        )
    assert result == {
        "eligible": False, "autosend": False, "reason": "no_recruitment_context", "reply": None,
    }


# ═══════════════════════════════════════════════════════════════════════════
# P/Q/R/S — flag-matrix independence.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "op_flag,cand_flag,expect_op_call,expect_cand_call",
    [
        (False, False, False, False),  # P: both OFF
        (True, False, True, False),    # Q: operational ON / candidate OFF
        (False, True, False, True),    # R: operational OFF / candidate ON
        (True, True, True, True),      # S: both ON
    ],
    ids=["P_both_off", "Q_operational_only", "R_candidate_only", "S_both_on"],
)
async def test_PQRS_flag_matrix_population_independence(op_flag, cand_flag, expect_op_call, expect_cand_call):
    decision = SemanticDecision(domain="recruitment_conversation", confidence=0.9)

    # Operational-role turn (employee).
    deliver_mock = AsyncMock(return_value="OP_REPLY")
    with patch("modules.hermes_dispatch.deliver_authoritative_recruitment_reply", deliver_mock):
        op_reply, _ = await router._task13_authoritative_dispatch(
            decision, sender="880171", text="x", source="bridge1",
            role_str="employee", intent="recruitment", db_ctx="", history="",
            recruitment_authoritative=op_flag, employee_authoritative=False,
            candidate_recruitment_authoritative=cand_flag,
            legacy_recruitment_eligible=False,
        )
    assert deliver_mock.await_count == (1 if expect_op_call else 0)
    assert (op_reply == "OP_REPLY") == expect_op_call

    # Candidate-population turn (unknown), legacy not already eligible.
    cand_mock = AsyncMock(return_value=("CAND_REPLY", None))
    with patch.object(router, "_dispatch_candidate_recruitment_semantic", cand_mock):
        cand_reply, _ = await router._task13_authoritative_dispatch(
            decision, sender="880172", text="y", source="bridge1",
            role_str="unknown", intent="recruitment", db_ctx="", history="",
            recruitment_authoritative=op_flag, employee_authoritative=False,
            candidate_recruitment_authoritative=cand_flag,
            legacy_recruitment_eligible=False,
        )
    assert cand_mock.await_count == (1 if expect_cand_call else 0)
    assert (cand_reply == "CAND_REPLY") == expect_cand_call


async def test_S_both_flags_on_no_duplicate_domain_classification_in_route_b(monkeypatch):
    """Even with both authority flags ON, Route-B's own dedup (M) still
    caps domain classification at one LLM call per turn for the
    candidate/unknown population."""
    from modules.route_b_handoff import evaluate_handoff
    import modules.route_b_handoff as rbh

    monkeypatch.setattr(rbh, "get_settings", MagicMock(return_value=MagicMock(route_b_handoff_enabled=True)))
    monkeypatch.setattr(rbh, "_check_active_recruitment_session", AsyncMock(return_value=None))
    monkeypatch.setattr(rbh, "_record_handoff", AsyncMock(return_value=None))
    classify_mock = AsyncMock(return_value="none")
    monkeypatch.setattr(rbh.ollama_mod, "classify_route_b_category", classify_mock)
    monkeypatch.setattr(
        rbh, "_check_recruitment_handoff",
        AsyncMock(return_value=rbh.HandoffResult(
            reply="আপনার নাম লিখুন।", admin_note=None, destination="recruitment",
            reason="central_decision_reused", confidence="central_gate", outcome="handed_off",
        )),
    )
    monkeypatch.setattr(rbh, "_check_employee_claim_handoff", AsyncMock(return_value=None))

    central_decision = SemanticDecision(domain="recruitment_conversation", confidence=0.95)
    with patch(
        "modules.preflight_guard.is_feature_active",
        _flags(task13_recruitment_authoritative=True, task13_candidate_recruitment_authoritative=True),
    ):
        result = await evaluate_handoff(
            sender="8801700000011", text="any guard vacancy?", source="bridge1",
            identity={"identity_role": "unknown"}, intent="unknown", history="",
            message_id="m5", central_semantic_decision=central_decision,
        )
    assert result is not None
    classify_mock.assert_not_awaited()
