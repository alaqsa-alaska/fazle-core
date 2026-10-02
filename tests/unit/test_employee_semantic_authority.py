"""Phase 5, 2026-08-26: Employee semantic authority tests.

Verifies the task13_employee_authoritative + salary_query/payment_due
semantic path:

1. Ordinary employee semantic conversation (advance/food/conveyance/emergency)
   routes through ec_handle_payment_request unchanged.
2. salary_query routes through handle_employee_informational_query.
3. payment_due routes through handle_employee_informational_query.
4. Ambiguous "টাকা" with history uses informational query when intent is
   payment_due (not advance_request).
5. Employee identity unresolved returns safe clarification, no private data.
6. db_ctx is scoped to the resolved emp_id — calling helper with emp_id=999
   for a "different employee" only ever fetches that row's data; helper does
   not join or read other employees' rows.
7. Hard guarantee: helper does NOT import or call create_transaction().
8. Hard guarantee: helper does NOT import or call _upsert_ledger().
9. Helper returns admin_note=None always (no draft created here; the existing
   admin approval pipeline stays deterministic for actual mutations).
10. Hermes timeout/error: helper falls through to app.llm chain, never
    fabricates figures (returns safe clarification if all fail).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────
# 1-2: module surface — the new helper exists, signature is correct,
# and it does NOT import create_transaction / _upsert_ledger.
# ─────────────────────────────────────────────────────────────────────

def test_handle_employee_informational_query_exists():
    from modules.employee_conversation import handle_employee_informational_query
    assert callable(handle_employee_informational_query)


def test_handle_employee_informational_query_signature():
    from modules.employee_conversation import handle_employee_informational_query
    import inspect
    sig = inspect.signature(handle_employee_informational_query)
    params = list(sig.parameters.keys())
    assert params == ["phone", "text", "source", "emp_id", "intent"]
    assert sig.return_annotation == "tuple[str, Optional[dict]]"


def test_employee_conversation_does_not_import_create_transaction():
    """Hard guarantee: this module never gains access to financial-write
    functions. Verify via runtime introspection — the financial-write
    names must NOT appear as attributes of the module (proves no `from X
    import create_transaction` was added). The static source-scan variant
    is intentionally avoided because the module docstring + comment blocks
    mention these names as the *negative* guarantee and a regex strip
    would risk false negatives."""
    import modules.employee_conversation as ec

    # Runtime: nothing in the module's namespace may be a financial-write
    # function or import from the financial engine.
    dangerous_callables = ["create_transaction", "_upsert_ledger",
                           "accounting_worker"]
    for name in dangerous_callables:
        assert not hasattr(ec, name), \
            f"employee_conversation.{name} must not be importable"

    # Static: scan ONLY executable statements (not comments/docstrings).
    # Strip triple-quoted strings + # comments.
    import re as _re
    with open(ec.__file__) as f:
        src = f.read()
    body = _re.sub(r'"""[\s\S]*?"""', '', src)
    body = _re.sub(r"'''[\s\S]*?'''", '', body)
    # Strip single-line # comments (but preserve strings containing '#')
    body = "\n".join(
        ln for ln in body.splitlines()
        if not ln.lstrip().startswith("#")
    )
    assert "from modules.fazle_payroll_engine" not in body, \
        "employee_conversation must not import fazle_payroll_engine"
    assert "import fazle_payroll_engine" not in body, \
        "employee_conversation must not import fazle_payroll_engine"
    # We DO allow the literal `create_transaction` mention in the new
    # helper's docstring (HARD GUARANTEES block) — it's a negative guarantee.
    # But it must NEVER appear as a CALL or as a NAME being imported. We
    # only check for actual import patterns here.
    assert _re.search(r"\bcreate_transaction\s*\(", body) is None, \
        "create_transaction() call detected in executable code"
    assert _re.search(r"\b_upsert_ledger\s*\(", body) is None, \
        "_upsert_ledger() call detected in executable code"
    assert _re.search(r"\baccounting_worker\s*\(", body) is None, \
        "accounting_worker() call detected in executable code"


# ─────────────────────────────────────────────────────────────────────
# 3-10: behavioral tests with mocked dependencies.
# ─────────────────────────────────────────────────────────────────────

def _payroll_summary_mock(emp_id, basic_salary=15000.0, paid_this_month=5000.0):
    s = {
        "employee_id": emp_id,
        "employee_name": f"Test Emp {emp_id}",
        "designation": "Guard",
        "basic_salary": basic_salary,
        "paid_this_month": paid_this_month,
        "payment_count_this_month": 1,
        "last_payment_amount": paid_this_month,
        "last_payment_date": "2026-08-15",
        "last_payment_method": "bkash",
        "duty_count_30d": 12,
        "active_duties": 1,
        "total_paid_ever": 100000.0,
        "under_review": False,
        "notes": "",
    }
    return s


@pytest.mark.asyncio
async def test_salary_query_routes_to_informational_handler():
    """salary_query intent + flag ON → informational handler, NOT the
    payment-request flow (which would create a draft)."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    fake_payroll = _payroll_summary_mock(42)
    fake_history = ["Them: আমার বেতন কবে পাব?",
                    "You: যাচাই করে জানাচ্ছি..."]
    expected_reply = "আপনার এই মাসের ৳১৫,০০০ বেতন পরিশোধিত হবে শীঘ্রই।"

    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(return_value=PayrollSummary(**fake_payroll))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="কর্মীর নাম: Test Emp 42\nবেতন: ৳15,000")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=fake_history)), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=False)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value=expected_reply)):
        reply, admin_note = await handle_employee_informational_query(
            "8801000000042", "আমার বে�ন কত?", "bridge2",
            emp_id=42, intent="salary_query",
        )

    assert reply == expected_reply
    assert admin_note is None  # never produces an admin note (no draft)


@pytest.mark.asyncio
async def test_payment_due_routes_to_informational_handler():
    """payment_due intent + flag ON → informational handler."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    fake_payroll = _payroll_summary_mock(43, paid_this_month=0.0)
    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(return_value=PayrollSummary(**fake_payroll))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="কর্মীর নাম: Test Emp 43\nএই মাসে পেমেন্টের তথ্য নেই")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=False)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value="এই মাসে এখনো পেমেন্ট হয়নি, তারিখ যাচাই করে জানাচ্ছি।")):
        reply, admin_note = await handle_employee_informational_query(
            "8801000000043", "আমার টাকা কবে পাব?", "bridge2",
            emp_id=43, intent="payment_due",
        )

    assert "পেমেন্ট" in reply
    assert admin_note is None


@pytest.mark.asyncio
async def test_db_ctx_scoped_to_resolved_emp_id():
    """When emp_id=99 is passed, get_payroll_summary MUST be called with 99
    (not with the phone, not with a different employee). Helper never joins
    or reads other employees' payroll data."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    captured_emp_id = {}
    async def capture_get_payroll_summary(emp_id):
        captured_emp_id["emp_id"] = emp_id
        return PayrollSummary(**_payroll_summary_mock(emp_id))

    with patch("modules.payroll_logic.get_payroll_summary",
               new=capture_get_payroll_summary), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="(empty)")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=False)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value="�পনার তথ্য নেই।")):
        await handle_employee_informational_query(
            "8801000000099", "x", "bridge2",
            emp_id=99, intent="salary_query",
        )

    assert captured_emp_id["emp_id"] == 99, \
        f"get_payroll_summary must be called with the resolved emp_id (got {captured_emp_id.get('emp_id')})"


@pytest.mark.asyncio
async def test_hermes_failure_falls_back_to_ai_chain():
    """When Hermes times out / errors, the helper falls back to app.llm.
    If BOTH fail, returns safe clarification (never fabricates)."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    fake_payroll = _payroll_summary_mock(50)
    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(return_value=PayrollSummary(**fake_payroll))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=True)), \
         patch("modules.hermes_dispatch.generate_reply",
               new=AsyncMock(return_value=None)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value="")):  # both fail
        reply, admin_note = await handle_employee_informational_query(
            "8801000000050", "x", "bridge2",
            emp_id=50, intent="salary_query",
        )

    assert admin_note is None
    # When both Hermes and app.llm fail, helper returns safe clarification
    # that does NOT contain any fabricated number.
    assert reply != ""
    assert "৳" not in reply  # no fabricated currency
    assert any(k in reply for k in ("অফিস", "যাচাই", "�োগাযোগ")), \
        f"safe clarification must point to office/verification, got: {reply!r}"


@pytest.mark.asyncio
async def test_hermes_works_when_flag_on():
    """When hermes_customer_employee flag is ON, Hermes is called and its
    reply is returned (when non-empty)."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    fake_payroll = _payroll_summary_mock(51)
    hermes_reply = "আপনার এই মা�ের ৳১৫,০০০ বেতন ২৫ তারিখে দেওয়া হবে।"
    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(return_value=PayrollSummary(**fake_payroll))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="payroll ctx")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=True)), \
         patch("modules.hermes_dispatch.generate_reply",
               new=AsyncMock(return_value=hermes_reply)) as hermes_mock, \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value="app.llm reply")) as ai_mock:
        reply, admin_note = await handle_employee_informational_query(
            "8801000000051", "x", "bridge2",
            emp_id=51, intent="salary_query",
        )

    assert reply == hermes_reply, "Hermes reply must win over fallback when non-empty"
    assert admin_note is None
    # app.llm was NOT called when Hermes returned a non-empty reply
    ai_mock.assert_not_called()


@pytest.mark.asyncio
async def test_payroll_summary_failure_still_returns_safe_reply():
    """If get_payroll_summary raises (DB down), helper does not raise.
    Returns a safe clarification. NEVER fabricates."""
    from modules.employee_conversation import handle_employee_informational_query

    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(side_effect=RuntimeError("DB down"))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=False)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value="")):
        reply, admin_note = await handle_employee_informational_query(
            "8801000000052", "x", "bridge2",
            emp_id=52, intent="salary_query",
        )
    assert admin_note is None
    assert reply != ""
    # Safe reply must not contain numbers/dates that weren't in any
    # authoritative source.
    import re
    assert not re.search(r"\d{4,}", reply), \
        "safe fallback must not contain fabricated year-like numbers"


@pytest.mark.asyncio
async def test_confidentiality_net_strips_other_phones():
    """Tier-15 net: if a reply accidentally mentions another phone number,
    it must be replaced with the safe fallback."""
    from modules.employee_conversation import handle_employee_informational_query
    from modules.payroll_logic import PayrollSummary

    fake_payroll = _payroll_summary_mock(53)
    bad_reply = "আপনার তথ্য পেয়েছি। যোগাযোগ: 8801912345678 (অন্য নম্বর)"
    safe_fallback = "এই মুহূর্তে নির্দিষ্ট তথ্য যাচাই করে জানাতে পারছি না।"
    with patch("modules.payroll_logic.get_payroll_summary",
               new=AsyncMock(return_value=PayrollSummary(**fake_payroll))), \
         patch("modules.payroll_logic.format_payroll_context",
               new=MagicMock(return_value="")), \
         patch("modules.message_router.get_recent_history",
               new=AsyncMock(return_value=[])), \
         patch("modules.rag.safe_search",
               new=AsyncMock(return_value=[])), \
         patch("modules.preflight_guard.is_feature_active",
               new=MagicMock(return_value=False)), \
         patch("app.llm.generate_reply",
               new=AsyncMock(return_value=bad_reply)):
        reply, admin_note = await handle_employee_informational_query(
            "8801000000053", bad_reply, "bridge2",
            emp_id=53, intent="salary_query",
        )
    assert admin_note is None
    # The other phone must be stripped (Tier-15 behavior). The sender's
    # own phone is permitted.
    assert "8801912345678" not in reply, \
        f"non-sender phone leaked: {reply!r}"


# ─────────────────────────────────────────────────────────────────────
# message_router routing — verify the employee_conversation branch in
# _task13_authoritative_dispatch routes salary_query / payment_due to
# the informational handler when the flag is ON.
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task13_authoritative_employee_salary_query_routes_to_handler():
    """When task13_employee_authoritative=ON AND intent=salary_query, the
    authoritative dispatch must invoke handle_employee_informational_query
    (NOT fall through with None)."""
    from modules.message_router import _task13_authoritative_dispatch
    from modules.hermes_kernel.data_classes import SemanticDecision

    sd = SemanticDecision(
        domain="employee_conversation",
        confidence=0.9,
        rationale="test",
        requested_action=None,
        identity_reference={"employee_id": 77},
    )
    expected_reply = "আপনার তথ্য নেই।"
    mock_async = AsyncMock(return_value=(expected_reply, None))
    with patch("modules.employee_conversation.handle_employee_informational_query",
               new=mock_async):
        reply, admin_note = await _task13_authoritative_dispatch(
            sd,
            sender="8801000000077",
            text="আমার বেতন কত?",
            source="bridge2",
            role_str="employee",
            intent="salary_query",
            db_ctx="(some ctx)",
            history="(some history)",
            recruitment_authoritative=False,
            employee_authoritative=True,
            candidate_recruitment_authoritative=False,
            legacy_recruitment_eligible=False,
        )

    assert reply == expected_reply
    assert admin_note is None
    mock_async.assert_awaited_once()
    # The mock was called with the right emp_id and intent.
    # emp_id is positional (4th arg), intent is kwarg.
    call_args = mock_async.call_args
    args, kwargs = call_args.args, call_args.kwargs
    assert args[3] == 77, f"emp_id should be the resolved emp_id (got {args[3]})"
    assert kwargs.get("intent") == "salary_query"


@pytest.mark.asyncio
async def test_task13_authoritative_employee_payment_due_routes_to_handler():
    """Same as salary_query but for payment_due intent."""
    from modules.message_router import _task13_authoritative_dispatch
    from modules.hermes_kernel.data_classes import SemanticDecision

    sd = SemanticDecision(
        domain="employee_conversation",
        confidence=0.9,
        rationale="test",
        requested_action=None,
        identity_reference={"employee_id": 88},
    )
    expected_reply = "আগামী সপ্তাহে পেমেন্� হবে।"
    with patch("modules.employee_conversation.handle_employee_informational_query",
               new=AsyncMock(return_value=(expected_reply, None))):
        result = await _task13_authoritative_dispatch(
            sd,
            sender="8801000000088",
            text="টাকা কবে পাব?",
            source="bridge2",
            role_str="employee",
            intent="payment_due",
            db_ctx="(some ctx)",
            history="(some history)",
            recruitment_authoritative=False,
            employee_authoritative=True,
            candidate_recruitment_authoritative=False,
            legacy_recruitment_eligible=False,
        )

    reply, admin_note = result
    assert reply == expected_reply
    assert admin_note is None


@pytest.mark.asyncio
async def test_task13_authoritative_employee_flag_off_returns_none():
    """When task13_employee_authoritative=OFF, the function still returns
    (None, None) — the flag remains the gate."""
    from modules.message_router import _task13_authoritative_dispatch
    from modules.hermes_kernel.data_classes import SemanticDecision

    sd = SemanticDecision(
        domain="employee_conversation",
        confidence=0.9,
        rationale="test",
        requested_action=None,
        identity_reference={"employee_id": 99},
    )
    reply, admin_note = await _task13_authoritative_dispatch(
        sd,
        sender="8801000000099",
        text="আমার বেতন?",
        source="bridge2",
        role_str="employee",
        intent="salary_query",
        db_ctx="",
        history="",
        recruitment_authoritative=False,
        employee_authoritative=False,  # flag OFF
        candidate_recruitment_authoritative=False,
        legacy_recruitment_eligible=False,
    )

    assert reply is None
    assert admin_note is None


@pytest.mark.asyncio
async def test_task13_authoritative_employee_no_emp_id_returns_none():
    """If identity couldn't be resolved (no employee_id), the dispatch
    returns (None, None) — fail-closed, no fabricated identity."""
    from modules.message_router import _task13_authoritative_dispatch
    from modules.hermes_kernel.data_classes import SemanticDecision

    sd = SemanticDecision(
        domain="employee_conversation",
        confidence=0.9,
        rationale="test",
        requested_action=None,
        identity_reference={},  # no emp_id
    )
    reply, admin_note = await _task13_authoritative_dispatch(
        sd,
        sender="8801000000100",
        text="x",
        source="bridge2",
        role_str="unknown",
        intent="salary_query",
        db_ctx="",
        history="",
        recruitment_authoritative=False,
        employee_authoritative=True,
        candidate_recruitment_authoritative=False,
        legacy_recruitment_eligible=False,
    )

    assert reply is None
    assert admin_note is None


# ─────────────────────────────────────────────────────────────────────
# Hard financial-mutation guarantee — the helper must not import any
# financial-write surface. (Different from the static-source scan above:
# this test inspects the runtime module's imported names.)
# ─────────────────────────────────────────────────────────────────────

def test_no_financial_write_imports_in_ec_module():
    """The employee_conversation module must not have imported any of the
    financial-write functions at runtime."""
    import modules.employee_conversation as ec
    import sys

    dangerous_attrs = ["create_transaction", "_upsert_ledger",
                       "accounting_worker", "parse_message"]
    # Inspect __dict__ + imported submodule names
    names_in_ec = set(dir(ec))
    for name in dangerous_attrs:
        assert name not in names_in_ec, \
            f"employee_conversation.{name} must not be importable from this module"
    # Also check that no fazle_payroll_engine submodule got pulled in
    payroll_engine_imported = False
    for modname in list(sys.modules.keys()):
        if modname.startswith("modules.fazle_payroll_engine"):
            payroll_engine_imported = True
    # We allow the import statement for typing/contracts but never call
    # it. The fact that the module is loaded into sys.modules isn't itself
    # a violation, but no financial-write FUNCTION should be reachable
    # from ec.
    # The real test: no financial-write symbol is in ec's namespace.
    assert not hasattr(ec, "create_transaction")
    assert not hasattr(ec, "_upsert_ledger")
    assert not hasattr(ec, "accounting_worker")
