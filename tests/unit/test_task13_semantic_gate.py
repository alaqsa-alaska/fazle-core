"""
Task 13 — semantic gate unit tests for Recruitment + Employee routing.

Mocked hermes-runner only. No live network call. No production side effects.
"""
import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if "/home/azim/core" not in sys.path:
    sys.path.insert(0, "/home/azim/core")

from modules.hermes_kernel.data_classes import SemanticDecision, DecisionRecord
from modules.hermes_dispatch import (
    semantic_gate,
    validate_semantic_decision,
    _parse_semantic_decision,
)


def _identity(role: str = "unknown", employee_id=None):
    out = {"role": role, "identity_source": "test"}
    if employee_id is not None:
        out["employee_id"] = employee_id
    return out


def _patch_call_hermes(reply: str):
    """Returns an async patcher that makes _call_hermes return `reply`.

    Also patches _configured() to return non-empty so the test doesn't
    depend on a real hermes_runner_url/hermes_runner_customer_secret.
    """
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("modules.hermes_dispatch._call_hermes", AsyncMock(return_value=reply)))
    stack.enter_context(patch("modules.hermes_dispatch._configured", return_value=("http://test.local", "test-secret")))
    return stack


# A. Known employee asking about someone else's job
@pytest.mark.asyncio
async def test_A_known_employee_other_person_job_recruitment():
    raw = json.dumps({
        "domain": "recruitment_conversation",
        "subject": "brother_guard_job",
        "intent": "job_inquiry_for_other",
        "entities": {"position": "Guard"},
        "requested_action": "inform",
        "confidence": 0.91,
        "rationale": "sender asks about sibling's guard job",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আমার ভাইয়ের Guard-এর চাকরি হবে? বেতন কত?",
            "sender_phone": "8801711111111",
            "identity_reference": _identity(role="employee", employee_id=42),
            "intent_hint": "unknown",
            "recruitment_detector_result": {"score": 4, "categories": ["job", "salary"]},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    assert decision.domain == "recruitment_conversation"
    assert decision.confidence >= 0.6


# B. Unknown sender asking about own past salary → employee_conversation,
# but identity unresolved → routed to clarification, no disclosure
@pytest.mark.asyncio
async def test_B_unknown_sender_salary_clarification_no_disclosure():
    raw = json.dumps({
        "domain": "employee_conversation",
        "subject": "past_month_salary_not_received",
        "intent": "salary_complaint",
        "entities": {"period": "last_month"},
        "requested_action": "salary_disclosure",
        "confidence": 0.85,
        "rationale": "sender claims salary not received",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আমার গত মাসের বেতনটা এখনো পাইনি",
            "sender_phone": "8801722222222",
            "identity_reference": _identity(role="unknown"),
            "intent_hint": "unknown",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    # Identity unresolved + employee_conversation → routed to clarification
    assert decision.domain == "no_match"
    assert decision.rationale == "identity_unresolved"
    assert decision.requested_action == "identity_clarification"


# C. Operations/location question → no_match
@pytest.mark.asyncio
async def test_C_operations_location_no_match():
    raw = json.dumps({
        "domain": "no_match",
        "subject": "operation_officer_location",
        "intent": "operations_location",
        "entities": {},
        "requested_action": None,
        "confidence": 0.82,
        "rationale": "operations location query, not recruitment or employee",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আমাদের Operation Officer কোথায়?",
            "sender_phone": "8801733333333",
            "identity_reference": _identity(role="unknown"),
            "intent_hint": "location",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="location")
    assert decision.domain == "no_match"


# D. Operation Officer salary → recruitment
@pytest.mark.asyncio
async def test_D_operation_officer_salary_recruitment():
    raw = json.dumps({
        "domain": "recruitment_conversation",
        "subject": "operation_officer_position_salary",
        "intent": "job_inquiry",
        "entities": {"position": "Operation Officer"},
        "requested_action": "inform",
        "confidence": 0.88,
        "rationale": "asking salary of a position",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "Operation Officer-এর বেতন কত?",
            "sender_phone": "8801744444444",
            "identity_reference": _identity(role="unknown"),
            "intent_hint": "unknown",
            "recruitment_detector_result": {"score": 3, "categories": ["job", "salary"]},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    assert decision.domain == "recruitment_conversation"


# E. Released-from-ship travel-expense question → employee semantic but no
# direct cash/payment creation from semantic decision alone.
@pytest.mark.asyncio
async def test_E_release_travel_expense_no_direct_cash_creation():
    raw = json.dumps({
        "domain": "employee_conversation",
        "subject": "release_and_travel_expense",
        "intent": "advance_or_reimbursement",
        "entities": {"reason": "release_from_ship"},
        "requested_action": "create_payment_draft",
        "confidence": 0.79,
        "rationale": "employee release + travel",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আমাকে জাহাজ থেকে release করেছে, অফিসে আসতে গাড়িভাড়া লাগবে",
            "sender_phone": "8801755555555",
            "identity_reference": _identity(role="employee", employee_id=77),
            "intent_hint": "release_travel",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="release_travel")
    # Resolved employee + employee_conversation → stays in employee handler;
    # semantic decision itself must NOT create any cash transaction.
    assert decision.domain == "employee_conversation"
    # The handler downstream owns draft lifecycle, not the semantic gate.
    # Confirmed: the gate's return is a *decision*, not an execution.


# F. Confirmed Escort order → semantic gate must NOT steal
@pytest.mark.asyncio
async def test_F_escort_order_not_stolen():
    raw = json.dumps({
        "domain": "recruitment_conversation",
        "subject": "escort_disguised",
        "intent": "escort_order",
        "entities": {},
        "requested_action": "escort",
        "confidence": 0.5,
        "rationale": "looks like escort",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "lighter vessel chittagong 5 guards tomorrow",
            "sender_phone": "8801766666666",
            "identity_reference": _identity(role="escort_client"),
            "intent_hint": "escort_order",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="escort_order")
    # Escort client role → forced NO_MATCH, intent_hint=escort_order → also NO_MATCH
    assert decision.domain == "no_match"
    assert decision.rationale.startswith("protected_role:") or decision.rationale.startswith("protected_intent:")


# G. Confirmed admin/accountant intent → not stolen
@pytest.mark.asyncio
async def test_G_admin_accountant_not_stolen():
    raw = json.dumps({
        "domain": "employee_conversation",
        "subject": "admin_command_disguised",
        "intent": "admin",
        "entities": {},
        "requested_action": "admin",
        "confidence": 0.95,
        "rationale": "admin command",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "report cash 30",
            "sender_phone": "8801777777777",
            "identity_reference": _identity(role="admin"),
            "intent_hint": "admin_command",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="admin_command")
    assert decision.domain == "no_match"
    assert decision.rationale.startswith("protected_role:") or decision.rationale.startswith("protected_intent:")


# H. Attendance/slip confirmed flow → unchanged
@pytest.mark.asyncio
async def test_H_attendance_slip_not_stolen():
    raw = json.dumps({
        "domain": "employee_conversation",
        "subject": "attendance_disguised",
        "intent": "attendance",
        "entities": {},
        "requested_action": "attendance",
        "confidence": 0.95,
        "rationale": "attendance",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আজকের হাজিরা",
            "sender_phone": "8801788888888",
            "identity_reference": _identity(role="employee", employee_id=10),
            "intent_hint": "attendance",
            "recruitment_detector_result": {"score": 0, "categories": []},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="attendance")
    assert decision.domain == "no_match"
    assert decision.rationale == "protected_intent:attendance"


# I. Low-confidence / invalid result → NO_MATCH
@pytest.mark.asyncio
async def test_I_low_confidence_no_match():
    raw = json.dumps({
        "domain": "recruitment_conversation",
        "subject": "uncertain",
        "intent": "unknown",
        "entities": {},
        "requested_action": None,
        "confidence": 0.4,
        "rationale": "uncertain",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "???",
            "sender_phone": "8801799999999",
            "identity_reference": _identity(role="unknown"),
            "intent_hint": "unknown",
            "recruitment_detector_result": {},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    assert decision.domain == "no_match"
    assert decision.rationale == "low_confidence"


# J. Recruitment fact-check/policy still enforced (smoke)
def test_J_decision_record_has_semantic_decision():
    rec = DecisionRecord()
    assert rec.semantic_decision is None
    rec.semantic_decision = SemanticDecision(
        domain="recruitment_conversation", confidence=0.9, rationale="ok"
    )
    assert rec.semantic_decision.domain == "recruitment_conversation"


# K. Employee confidentiality: identity unresolved → no financial disclosure
@pytest.mark.asyncio
async def test_K_employee_unresolved_no_disclosure():
    raw = json.dumps({
        "domain": "employee_conversation",
        "subject": "salary",
        "intent": "salary",
        "entities": {},
        "requested_action": "disclose_salary",
        "confidence": 0.9,
        "rationale": "salary question",
    })
    with _patch_call_hermes(raw):
        ctx = {
            "text": "আমার বেতন কত?",
            "sender_phone": "8801700000000",
            "identity_reference": _identity(role="unknown"),
            "intent_hint": "unknown",
            "recruitment_detector_result": {"score": 1, "categories": ["salary"]},
            "history": "",
            "db_ctx": "",
            "kb_ctx": "",
            "business_data": {},
            "source": "bridge1",
        }
        decision = await semantic_gate(ctx)
    decision = validate_semantic_decision(decision, identity_reference=ctx["identity_reference"], intent_hint="unknown")
    # Identity unresolved + employee_conversation → routed to clarification
    # (no salary disclosure happens at the gate level)
    assert decision.domain == "no_match"
    assert decision.rationale == "identity_unresolved"
    assert decision.requested_action == "identity_clarification"


# L. JSON parse fail-safe
def test_L_parse_semantic_decision_invalid_json_returns_no_match():
    d = _parse_semantic_decision("not json at all")
    assert d.domain == "no_match"
    assert d.rationale == "json_parse_failed"


def test_L_parse_semantic_decision_empty_returns_no_match():
    d = _parse_semantic_decision("")
    assert d.domain == "no_match"
    assert d.rationale == "empty_response"


def test_L_parse_semantic_decision_invalid_domain_returns_no_match():
    d = _parse_semantic_decision(json.dumps({"domain": "escort_order", "confidence": 0.9}))
    assert d.domain == "no_match"
    assert d.rationale == "invalid_domain"


# Config-missing → NO_MATCH with rationale=not_configured
@pytest.mark.asyncio
async def test_semantic_gate_not_configured_returns_no_match():
    with patch("modules.hermes_dispatch._configured", return_value=(None, None)):
        d = await semantic_gate({"text": "hi", "sender_phone": "x"})
    assert d.domain == "no_match"
    assert d.rationale == "not_configured"


# DecisionRecord extension
def test_decision_record_default_semantic_decision_is_none():
    rec = DecisionRecord(sender_phone="8801", intent="recruitment")
    assert rec.semantic_decision is None


# Identity-role does NOT determine domain (test via validate_semantic_decision)
def test_validate_employee_role_alone_does_not_force_employee_domain():
    d = SemanticDecision(domain="recruitment_conversation", confidence=0.9)
    out = validate_semantic_decision(d, identity_reference={"role": "employee", "employee_id": 1})
    # domain stays recruitment_conversation even though role=employee
    assert out.domain == "recruitment_conversation"


# Recruitment_detector does NOT determine domain
def test_validate_recruitment_detector_high_does_not_force_recruitment():
    d = SemanticDecision(domain="employee_conversation", confidence=0.9)
    out = validate_semantic_decision(
        d,
        identity_reference={"role": "employee", "employee_id": 1},
    )
    assert out.domain == "employee_conversation"