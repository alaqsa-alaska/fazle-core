"""
Task 13 semantic-gate production-safety regression fix — regression tests.

Covers the reviewed regression (persistent task #13, repo /home/azim/core):

  1. modules.recruitment_flow.route_recruitment_message()'s
     `if active_session and _is_llm_priority_phone(phone)` guard was
     accidentally widened to `if active_session:` by the uncommitted
     Task-13 diff, turning on an unguarded LLM recruitment reply hot path
     (including a fallback to modules.conversation_layer.
     generate_recruitment_reply() with no fact-check) for every active
     recruitment session, for BOTH of its callers.

  2. The Task-13 semantic-gate call site in
     modules.message_router.process_message() called
     route_recruitment_message() / task13_handle_unresolved_employee_
     question() directly and could return a reply, i.e. it was NOT
     actually shadow-mode despite being described as an observability-only
     addition.

This file proves:
  (a) the guard is restored verbatim (source-level + behavioral),
  (b) the Task-13 SHADOW block (the original try/except that calls
      semantic_gate()/validate_semantic_decision() and only logs) is
      genuinely non-authoritative for every SemanticDecision.domain
      outcome -- no handler call, no reply, no clarification, no business
      write -- and always falls through to the pre-existing deterministic
      router unchanged, WITH both authoritative flags left at their
      default (OFF).

2026-08-22 (Task-13 MiniMax-primary + authoritative-mode promotion,
BUILD+VERIFY): message_router.process_message() gained a SECOND,
separately flag-gated section immediately after the shadow block --
"Task 13 AUTHORITATIVE DISPATCH" -- originally gated by a single
task13_semantic_authoritative flag, then SAME-DAY split into two
independent flags, task13_recruitment_authoritative and
task13_employee_authoritative (both default OFF, the single-flag name
removed entirely) so recruitment-domain and employee-domain authoritative
routing can be activated independently. That section legitimately calls
handlers and returns, but only for the domain(s) whose flag is explicitly
active; with both flags at their default this file's own shadow-block
assertions (never calls a handler, never returns) remain true and are
still checked here, now scoped precisely to the original shadow block via
_task13_block_source()'s updated end boundary. See
tests/unit/test_task13_authoritative_mode.py for the promoted section's
own behavioral (flag ON) coverage -- Required Tests A-S from the
promotion's spec, plus the flag-split's own cross-domain-independence
tests.
"""
from __future__ import annotations

import inspect
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.message_router as router
import modules.recruitment_flow as recruitment_flow
from modules.hermes_kernel.data_classes import SemanticDecision

# NOTE: tests/pytest.ini sets asyncio_mode = auto, so `async def test_*`
# functions are collected as asyncio tests automatically -- no
# `pytestmark`/decorator needed (and one would wrongly also apply to the
# plain sync tests in this file, e.g. the source-inspection ones below).


# ── Helpers ──────────────────────────────────────────────────────────────────

def _task13_block_source(strip_comments: bool = True) -> str:
    """Return just the Task-13 SHADOW COMPUTATION/LOGGING block's source
    text (the try/except that calls semantic_gate()/validate_semantic_
    decision() and only logs), isolated from the rest of process_message(),
    so structural assertions can't be fooled by a call elsewhere in the
    (very long) function.

    2026-08-22 (Task-13 authoritative-mode promotion): this helper's END
    boundary was moved from "if recruit_decision[\"eligible\"]:" to the new
    "# ── Task 13 AUTHORITATIVE DISPATCH" section marker that now sits
    between the shadow block and that line -- promotion added a NEW,
    separately flag-gated section (task13_recruitment_authoritative /
    task13_employee_authoritative, both default OFF) that legitimately DOES
    call handlers and DOES return, by design, only when the relevant flag
    is explicitly turned on. This helper now isolates
    just the original always-runs, log-only shadow section so its own
    "never calls a handler / never returns" invariant remains checked and
    true; see _task13_authoritative_block_source() below for the promoted
    section's own (deliberately different) assertions.

    strip_comments=True (default) removes full-line `#` comments before
    returning, so an assertion like "call X is absent" can't be defeated
    by X merely being *mentioned* in this block's own explanatory prose
    (e.g. "MUST NOT call route_recruitment_message()") -- it checks for
    an actual code reference."""
    src = inspect.getsource(router)
    start = src.index("# ── Task 13 (2026-08-22): SHADOW-ONLY semantic gate")
    end = src.index("# ── Task 13 AUTHORITATIVE DISPATCH", start)
    block = src[start:end]
    if not strip_comments:
        return block
    code_lines = [
        line for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(code_lines)


def _task13_authoritative_block_source(strip_comments: bool = True) -> str:
    """Return the Task-13 AUTHORITATIVE DISPATCH section's source text
    (2026-08-22 promotion, split into two independent flags same-day) --
    the new code that runs and reads task13_recruitment_authoritative /
    task13_employee_authoritative (both default OFF; domain-specific
    gating happens inside _task13_authoritative_dispatch() itself). Unlike
    the shadow block above, this section legitimately contains a `return`
    and calls into
    _task13_authoritative_dispatch() (which itself may call
    deliver_authoritative_recruitment_reply() / ec_handle_payment_request()
    / task13_handle_unresolved_employee_question() depending on the
    decision) -- see tests/unit/test_task13_authoritative_mode.py for the
    behavioral proof that all of this is unreachable with the flag OFF."""
    src = inspect.getsource(router)
    start = src.index("# ── Task 13 AUTHORITATIVE DISPATCH")
    end = src.index("if recruit_decision[\"eligible\"]:", start)
    block = src[start:end]
    if not strip_comments:
        return block
    code_lines = [
        line for line in block.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return "\n".join(code_lines)


def _identity(role: str = "unknown", employee_id=None) -> dict:
    out = {
        "role": role,
        "display_name": "",
        "identity_confidence": 10,
        "identity_source": "test",
        "confidence": 0.1,
    }
    if employee_id is not None:
        out["employee_id"] = employee_id
    return out


def _base_router_mocks(monkeypatch, *, identity: dict, recruit_eligible: bool, recruit_reply: str = "LEGACY_REPLY"):
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "get_contact_context", AsyncMock(return_value=""))
    monkeypatch.setattr(router, "get_recent_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(router.ai, "classify_intent_llm", AsyncMock(return_value="unknown"))
    monkeypatch.setattr(
        router,
        "recruitment_eligibility",
        AsyncMock(return_value={
            "eligible": recruit_eligible,
            "autosend": True,
            "reason": "test",
            "active_session": {"id": 1} if recruit_eligible else None,
        }),
    )
    route_mock = AsyncMock(return_value={"reply": recruit_reply, "autosend": True})
    monkeypatch.setattr(router, "route_recruitment_message", route_mock)
    return route_mock


def _shadow_stack(decision: SemanticDecision, *extra_patchers):
    """ExitStack with every patch needed to drive the Task-13 shadow block
    to a specific SemanticDecision outcome, plus any extra `patch(...)`
    objects the caller wants entered on the same stack (so a single `with`
    covers everything). Returns (stack, [mock for each extra_patcher, in
    order]). The two hermes_dispatch symbols are patched at their source
    module because the Task-13 block imports them inline on every call,
    not at module import time."""
    stack = ExitStack()
    stack.enter_context(patch("modules.hermes_dispatch.semantic_gate", AsyncMock(return_value=decision)))
    stack.enter_context(patch("modules.hermes_dispatch.validate_semantic_decision", MagicMock(return_value=decision)))
    stack.enter_context(patch("modules.recruitment_detector.detect_recruitment_intent", AsyncMock(return_value={})))
    extra_mocks = [stack.enter_context(p) for p in extra_patchers]
    return stack, extra_mocks


# ── 1. Legacy recruitment guard is restored (source + behavior) ──────────────

def test_1_legacy_guard_source_restored():
    src = inspect.getsource(recruitment_flow.route_recruitment_message)
    assert "if active_session and _is_llm_priority_phone(phone):" in src
    assert "if active_session:\n" not in src.split("if active_session and _is_llm_priority_phone(phone):")[0]


def test_1b_comment_no_longer_references_nonexistent_semantic_decision_param():
    src = inspect.getsource(recruitment_flow.route_recruitment_message)
    # The stale comment claimed a `semantic_decision` parameter that never
    # existed on this function's signature -- must not be present anymore.
    assert "caller still explicitly passes a semantic_decision" not in src
    sig = inspect.signature(recruitment_flow.route_recruitment_message)
    assert "semantic_decision" not in sig.parameters


async def test_1c_legacy_caller_still_gated_by_priority_allowlist(monkeypatch):
    """route_recruitment_message() itself: active_session=True but phone
    NOT on the (default empty) allowlist must still use the deterministic
    intake_message() path, never the LLM priority-reply path."""
    session = {"id": 9, "collection_step": "age", "funnel_stage": "collecting"}
    intake_mock = AsyncMock(return_value={"reply": "intake-reply", "action": "collecting"})
    llm_priority_mock = AsyncMock(return_value="should never be called")
    monkeypatch.setattr(
        recruitment_flow,
        "recruitment_eligibility",
        AsyncMock(return_value={
            "eligible": True, "autosend": True, "reason": "session_continuation",
            "active_session": session,
        }),
    )
    monkeypatch.setattr(recruitment_flow, "intake_message", intake_mock)
    with patch("modules.conversation_layer.generate_recruitment_reply", llm_priority_mock):
        result = await recruitment_flow.route_recruitment_message(
            "8801700000099", "25", source="bridge1", role="candidate",
        )
    assert result["reply"] == "intake-reply"
    intake_mock.assert_awaited_once()
    llm_priority_mock.assert_not_awaited()


async def test_1d_legacy_caller_allowlisted_phone_still_uses_priority_path(monkeypatch):
    """Sanity: the allowlist branch itself still works for an explicitly
    allowlisted phone (proves the revert didn't overcorrect into always-off)."""
    session = {"id": 9, "candidate_id": 17, "collection_step": "age", "funnel_stage": "collecting"}
    monkeypatch.setattr(
        recruitment_flow,
        "recruitment_eligibility",
        AsyncMock(return_value={
            "eligible": True, "autosend": True, "reason": "session_continuation",
            "active_session": session,
        }),
    )
    monkeypatch.setattr(recruitment_flow, "_is_llm_priority_phone", lambda phone: True)
    monkeypatch.setattr(recruitment_flow, "_audit_candidate_conversation", AsyncMock(return_value=True))
    with patch(
        "modules.conversation_layer.generate_recruitment_reply",
        AsyncMock(return_value="priority reply"),
    ):
        result = await recruitment_flow.route_recruitment_message(
            "8801700000099", "25", source="bridge1", role="candidate",
        )
    assert result["reply"] == "priority reply"


# ── 2. Task-13 router block cannot enable the legacy LLM hot path ────────────

def test_2_task13_block_never_calls_route_recruitment_message():
    block = _task13_block_source()
    assert "route_recruitment_message(" not in block


def test_2b_task13_block_never_imports_or_calls_unresolved_employee_handler():
    block = _task13_block_source()
    assert "task13_handle_unresolved_employee_question" not in block


def test_2c_task13_block_has_no_return_statement():
    """A `return` inside the try block would make the shadow decision
    authoritative for at least one domain -- must never happen."""
    block = _task13_block_source()
    assert "return" not in block


# ── 2d-2f. Authoritative section (2026-08-22 promotion) -- deliberately
# different invariants: it MAY call handlers and MAY return, but ONLY
# behind an is_feature_active() check, and that check must gate the
# return, not merely coexist with it. ─────────────────────────────────────

def test_2d_authoritative_block_reads_both_split_feature_flags():
    """2026-08-22 same-day split: task13_semantic_authoritative (single
    flag) was replaced by two independent flags -- confirm both are read
    via is_feature_active(), and the old combined flag name is gone."""
    block = _task13_authoritative_block_source()
    assert 'is_feature_active as _task13_flag_active' in block
    assert '_task13_flag_active("task13_recruitment_authoritative"' in block
    assert '_task13_flag_active("task13_employee_authoritative"' in block
    assert "task13_semantic_authoritative" not in block


def test_2e_authoritative_block_flag_reads_precede_dispatch_and_return():
    """Structural proof both flags are read and threaded through to
    _task13_authoritative_dispatch() BEFORE the section can return --
    domain-specific gating itself lives inside that function (each domain
    is gated by exactly one of the two flags, proven by the cross-domain-
    independence test in tests/unit/test_task13_authoritative_mode.py);
    this only proves the call site plumbs both flags through, in order,
    rather than silently dropping one."""
    block = _task13_authoritative_block_source()
    recruitment_flag_idx = block.index('_task13_flag_active("task13_recruitment_authoritative"')
    employee_flag_idx = block.index('_task13_flag_active("task13_employee_authoritative"')
    dispatch_idx = block.index("await _task13_authoritative_dispatch(")
    return_idx = block.index("return _t13_reply, _t13_admin_note")
    assert recruitment_flag_idx < dispatch_idx
    assert employee_flag_idx < dispatch_idx
    assert dispatch_idx < return_idx
    assert "recruitment_authoritative=_t13_recruitment_flag" in block
    assert "employee_authoritative=_t13_employee_flag" in block


def test_2f_authoritative_block_references_all_three_handoff_targets():
    """The promoted section's OWN dispatch call (_task13_authoritative_
    dispatch, tested in isolation in test_task13_authoritative_mode.py)
    is what actually references the three handoff targets -- confirms the
    call site itself is present and unconditional-once-flagged, i.e. no
    domain is silently dropped at the message_router call site."""
    block = _task13_authoritative_block_source()
    assert "_task13_authoritative_dispatch(" in block
    assert "sem_decision," in block


# ── 3. Example A: employee sender + other person's job → recruitment_conversation ──
# (Classification-level requirement -- already covered by
# tests/unit/test_task13_semantic_gate.py::test_A_known_employee_other_person_job_recruitment.
# Re-imported and re-run here for direct confirmation in this fix's own suite.)

async def test_3_example_a_known_employee_other_person_job_is_recruitment():
    import json
    from modules.hermes_dispatch import semantic_gate, validate_semantic_decision

    raw = json.dumps({
        "domain": "recruitment_conversation",
        "subject": "brother_guard_job",
        "intent": "job_inquiry_for_other",
        "entities": {"position": "Guard"},
        "requested_action": "inform",
        "confidence": 0.91,
        "rationale": "sender asks about sibling's guard job",
    })
    ctx = {
        "text": "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?",
        "sender_phone": "8801711111111",
        "identity_reference": _identity(role="employee", employee_id=42),
        "intent_hint": "unknown",
        "recruitment_detector_result": {"score": 4, "categories": ["job", "salary"]},
        "history": "", "db_ctx": "", "kb_ctx": "", "business_data": {}, "source": "bridge1",
    }
    with (
        patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value=raw)),
        patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "test-secret")),
    ):
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    assert decision.domain == "recruitment_conversation"
    # Sender role == employee did not force employee_conversation.
    assert decision.domain != "employee_conversation"


# ── 4-9. True shadow mode: every SemanticDecision.domain outcome ─────────────

_SCENARIOS = {
    "recruitment_conversation": SemanticDecision(
        domain="recruitment_conversation", confidence=0.9, rationale="test",
    ),
    "employee_conversation": SemanticDecision(
        domain="employee_conversation", confidence=0.9, rationale="test",
    ),
    "no_match_identity_unresolved": SemanticDecision(
        domain="no_match", confidence=0.9, rationale="identity_unresolved",
        requested_action="identity_clarification",
    ),
    "no_match_other": SemanticDecision(
        domain="no_match", confidence=0.9, rationale="low_confidence",
    ),
}


@pytest.mark.parametrize("scenario_name", list(_SCENARIOS.keys()))
async def test_4_7_9_shadow_never_changes_reply_and_calls_legacy_exactly_once(monkeypatch, scenario_name):
    """Regardless of the shadow SemanticDecision.domain, the final reply
    must come from exactly one call to the pre-existing legacy
    route_recruitment_message() call site -- never a second (shadow) call,
    never a different reply."""
    decision = _SCENARIOS[scenario_name]
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True)
    ec_patcher = patch(
        "modules.employee_conversation.task13_handle_unresolved_employee_question",
        AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None)),
    )
    stack, (ec_mock,) = _shadow_stack(decision, ec_patcher)
    with stack:
        reply, admin_note = await router.process_message("8801711111199", "চাকরি চাই", "bridge1")

    assert reply == "LEGACY_REPLY"
    route_mock.assert_awaited_once()
    ec_mock.assert_not_awaited()


@pytest.mark.parametrize("scenario_name", list(_SCENARIOS.keys()))
async def test_4_shadow_alone_cannot_call_recruitment_handler(monkeypatch, scenario_name):
    """With recruit_decision.eligible=False (legacy caller declines too),
    route_recruitment_message() must NEVER be called for ANY shadow
    domain -- proving the shadow block itself has zero ability to invoke
    it independently of the legacy path."""
    decision = _SCENARIOS[scenario_name]
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=False)
    # Everything past the recruitment tier must be inert for this text/role
    # so process_message() can return without hitting real AI/DB/network
    # calls (role="unknown" reaches Route B, which would otherwise call the
    # real Ollama endpoint).
    monkeypatch.setattr(router, "kb_get_reply", AsyncMock(return_value="kb-fallback-reply"))
    monkeypatch.setattr(
        "modules.route_b.handle_route_b_message",
        AsyncMock(return_value=("route-b-fallback-reply", None)),
    )
    ec_patcher = patch(
        "modules.employee_conversation.task13_handle_unresolved_employee_question",
        AsyncMock(return_value=("SHOULD_NOT_BE_CALLED", None)),
    )
    stack, (ec_mock,) = _shadow_stack(decision, ec_patcher)
    with stack:
        await router.process_message("8801711111198", "চাকরি চাই", "bridge1")

    route_mock.assert_not_awaited()
    ec_mock.assert_not_awaited()


async def test_6_identity_unresolved_shadow_triggers_no_clarification_reply(monkeypatch):
    """The specific 'no_match + identity_unresolved + identity_clarification'
    combination used to route directly to the clarification handler and
    return its reply. It must no longer do either."""
    decision = _SCENARIOS["no_match_identity_unresolved"]
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True, recruit_reply="LEGACY_REPLY")
    ec_patcher = patch(
        "modules.employee_conversation.task13_handle_unresolved_employee_question",
        AsyncMock(return_value=("পরিচয় নিশ্চিত করুন", None)),
    )
    stack, (ec_mock,) = _shadow_stack(decision, ec_patcher)
    with stack:
        reply, _ = await router.process_message("8801711111197", "আমার বেতন কত?", "bridge1")

    ec_mock.assert_not_awaited()
    assert "পরিচয় নিশ্চিত" not in reply
    assert reply == "LEGACY_REPLY"


async def test_8_shadow_path_writes_no_business_data(monkeypatch):
    """No draft-creation / write-capable function reachable only from the
    Task-13 block is ever invoked, for any domain."""
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True)
    ec_patcher = patch(
        "modules.employee_conversation.task13_handle_unresolved_employee_question", AsyncMock(),
    )
    pay_patcher = patch(
        "modules.employee_conversation.handle_employee_payment_request", AsyncMock(),
    )
    stack, (ec_mock, pay_mock) = _shadow_stack(
        _SCENARIOS["employee_conversation"], ec_patcher, pay_patcher,
    )
    with stack:
        await router.process_message("8801711111196", "চাকরি চাই", "bridge1")

    ec_mock.assert_not_awaited()
    pay_mock.assert_not_awaited()
    # The only recruitment-side-effect call is the pre-existing legacy one.
    route_mock.assert_awaited_once()


async def test_9_no_match_falls_through_to_legacy_unchanged(monkeypatch):
    decision = _SCENARIOS["no_match_other"]
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True, recruit_reply="LEGACY_REPLY")
    stack, _ = _shadow_stack(decision)
    with stack:
        reply, _ = await router.process_message("8801711111195", "চাকরি চাই", "bridge1")
    assert reply == "LEGACY_REPLY"
    route_mock.assert_awaited_once()


async def test_shadow_logging_failure_does_not_break_real_reply_path(monkeypatch):
    """The try/except around the Task-13 block must swallow a
    semantic_gate failure without affecting the legacy reply."""
    identity = _identity(role="unknown")
    route_mock = _base_router_mocks(monkeypatch, identity=identity, recruit_eligible=True, recruit_reply="LEGACY_REPLY")
    with patch("modules.hermes_dispatch.semantic_gate", AsyncMock(side_effect=RuntimeError("boom"))):
        reply, _ = await router.process_message("8801711111194", "চাকরি চাই", "bridge1")
    assert reply == "LEGACY_REPLY"
    route_mock.assert_awaited_once()
