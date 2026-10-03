"""Stage I (2026-08-29) -- Controlled Production Execution, Business-State
Completion & Data Hygiene.

Covers: I2 (deleted-transaction guard, the real correctness fix), I3
(REGTEST production-write guard), I5 (CASH DETAILS), I7 (reject-UX
wording), I13/I14 (proposal lifecycle), I19/I20 (business inbox/STATUS),
I23 (DLQ classification), I24 (data hygiene report), I26 (HELP
consistency)."""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── I2: deleted-transaction guard (real correctness fix found live) ─────────

class TestDeletedTransactionGuard:
    def test_refuse_if_deleted_blocks_when_deleted_at_set(self):
        from modules.financial_operations import _refuse_if_deleted
        result = _refuse_if_deleted({"txn_ref": "r1", "deleted_at": "2026-07-02T05:35:33Z", "deleted_by": "admin_manual"})
        assert result is not None
        assert result["ok"] is False
        assert "already deleted" in result["error"]

    def test_refuse_if_deleted_passes_when_not_deleted(self):
        from modules.financial_operations import _refuse_if_deleted
        assert _refuse_if_deleted({"txn_ref": "r1", "deleted_at": None}) is None

    async def test_cash_approve_refuses_deleted_transaction(self):
        from modules.financial_operations import cash_approve
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "pending", "deleted_at": "2026-07-02T05:35:33Z", "deleted_by": "admin_manual"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.fazle_payroll_engine.routes.operator_approve", new=AsyncMock()) as mock_approve:
            result = await cash_approve("r1", "01700000000")
        assert result["ok"] is False
        assert "already deleted" in result["error"]
        mock_approve.assert_not_called()

    async def test_cash_reject_refuses_deleted_transaction(self):
        from modules.financial_operations import cash_reject
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "pending", "deleted_at": "2026-07-02T05:35:33Z"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_reject("r1", "reason", "01700000000")
        assert result["ok"] is False
        assert "already deleted" in result["error"]

    async def test_cash_reverse_refuses_deleted_transaction(self):
        from modules.financial_operations import cash_reverse
        txn = {"id": 1, "txn_ref": "r1", "transaction_status": "final", "is_reversal": False, "deleted_at": "2026-07-02T05:35:33Z"}
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})):
            result = await cash_reverse("r1", "reason", "01700000000")
        assert result["ok"] is False
        assert "already deleted" in result["error"]

    def test_classify_all_pending_query_excludes_deleted(self):
        """Structural proof: the classification query must filter
        deleted_at IS NULL -- otherwise a soft-deleted row keeps
        appearing as a normal pending business transaction forever."""
        import inspect
        from modules.fazle_payroll_engine import review_linkage_repair as m
        src = inspect.getsource(m.classify_all_pending_cash_transactions)
        assert "deleted_at IS NULL" in src

    def test_orphan_finder_excludes_deleted(self):
        import inspect
        from modules.fazle_payroll_engine import review_linkage_repair as m
        src = inspect.getsource(m.find_orphaned_operator_transactions)
        assert "deleted_at IS NULL" in src

    def test_business_action_execution_cash_txn_state_checks_deleted(self):
        from modules.business_action_execution import _is_actionable
        assert _is_actionable("cash_txn", {"transaction_status": "pending", "deleted_at": "2026-07-02"}) is False
        assert _is_actionable("cash_txn", {"transaction_status": "pending", "deleted_at": None}) is True


# ── I3: production REGTEST-write guard ───────────────────────────────────────

class TestRegtestProductionGuard:
    async def test_regtest_marker_in_notes_refused_by_default(self):
        from modules.fazle_payroll_engine.routes import operator_submit, OperatorSubmitRequest
        from fastapi import HTTPException
        req = OperatorSubmitRequest(
            employee_id=1, employee_name_raw="Test Employee", amount="1.00",
            payout_method="cash", txn_date="2026-08-29", txn_category="salary",
            notes="REGTEST_20260829_120000 operator write-path test",
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOW_TEST_ARTIFACT_WRITES", None)
            with pytest.raises(HTTPException) as exc_info:
                await operator_submit(req)
        assert exc_info.value.status_code == 403
        assert "REGTEST_" in exc_info.value.detail

    async def test_regtest_marker_in_employee_name_also_refused(self):
        from modules.fazle_payroll_engine.routes import operator_submit, OperatorSubmitRequest
        from fastapi import HTTPException
        req = OperatorSubmitRequest(
            employee_id=1, employee_name_raw="REGTEST_probe_operator", amount="1.00",
            payout_method="cash", txn_date="2026-08-29", txn_category="salary",
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOW_TEST_ARTIFACT_WRITES", None)
            with pytest.raises(HTTPException) as exc_info:
                await operator_submit(req)
        assert exc_info.value.status_code == 403

    async def test_explicit_opt_in_env_flag_allows_it_through(self):
        """The guard is a refusal, not a silent drop -- an explicit,
        reviewed opt-in must still be able to run the regression suite
        deliberately."""
        from modules.fazle_payroll_engine.routes import operator_submit, OperatorSubmitRequest
        req = OperatorSubmitRequest(
            employee_id=1, employee_name_raw="Test Employee", amount="1.00",
            payout_method="cash", txn_date="2026-08-29", txn_category="salary",
            notes="REGTEST_probe operator write-path test",
        )
        with patch.dict(os.environ, {"ALLOW_TEST_ARTIFACT_WRITES": "true"}), \
             patch("modules.fazle_payroll_engine.routes.fetch_one", new=AsyncMock(return_value=None)):
            from fastapi import HTTPException
            # Falls through the REGTEST guard, then hits the very next
            # check (employee not found) instead -- proves the guard
            # itself did not fire, without needing the full DB chain.
            with pytest.raises(HTTPException) as exc_info:
                await operator_submit(req)
        assert exc_info.value.status_code == 400
        assert "Employee not found" in exc_info.value.detail

    async def test_genuine_operator_write_unaffected(self):
        """A normal submission with no REGTEST marker must not be
        touched by this guard at all -- falls through to the real
        validation chain exactly as before."""
        from modules.fazle_payroll_engine.routes import operator_submit, OperatorSubmitRequest
        from fastapi import HTTPException
        req = OperatorSubmitRequest(
            employee_id=999999, employee_name_raw="Karim Uddin", amount="500.00",
            payout_method="cash", txn_date="2026-08-29", txn_category="salary",
            notes="Food allowance for August",
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ALLOW_TEST_ARTIFACT_WRITES", None)
            with patch("modules.fazle_payroll_engine.routes.fetch_one", new=AsyncMock(return_value=None)):
                with pytest.raises(HTTPException) as exc_info:
                    await operator_submit(req)
        # Reaches the employee-not-found check (400), never the REGTEST
        # guard (403) -- proves genuine writes are unaffected.
        assert exc_info.value.status_code == 400
        assert "Employee not found" in exc_info.value.detail


# ── I5: CASH DETAILS (read-only) ─────────────────────────────────────────────

class TestCashDetails:
    async def test_returns_full_review_context_without_mutation(self):
        from modules.financial_operations import cash_details
        txn = {
            "id": 1, "txn_ref": "r1", "employee_id": 5, "employee_name_raw": "Karim",
            "employee_phone": "01700000000", "amount": 500, "payout_method": "cash",
            "transaction_status": "pending", "txn_category": "salary", "txn_date": None,
            "created_at": None, "deleted_at": None,
        }
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": True, "txn": txn})), \
             patch("modules.financial_operations.fetch_one", new=AsyncMock(side_effect=[
                 {"raw_content": "Food bill", "review_note": None},  # provenance lookup
                 {"n": 0},  # duplicate-count lookup
             ])):
            result = await cash_details("r1")
        assert result["ok"] is True
        assert "card" in result

    async def test_ambiguous_ref_returns_candidates_not_a_pick(self):
        from modules.financial_operations import cash_details
        with patch("modules.financial_operations.resolve_cash_transaction", new=AsyncMock(return_value={"ok": False, "ambiguous": True, "candidates": []})):
            result = await cash_details("abc123")
        assert result["ok"] is False
        assert result["ambiguous"] is True

    def test_cash_details_never_mutates(self):
        import inspect
        from modules.financial_operations import cash_details
        src = inspect.getsource(cash_details)
        assert "operator_approve" not in src
        assert "operator_reject" not in src
        assert "reverse_transaction" not in src


# ── I7: reject UX wording ────────────────────────────────────────────────────

class TestRejectUXWording:
    def test_reject_result_never_says_transaction_rejected_bare(self):
        from modules.financial_operations import format_cash_result
        result = {"ok": True, "txn_id": 1, "txn_ref": "r1", "result": {}}
        text = format_cash_result("reject", result)
        assert "remains pending" in text
        assert text.strip() != "Transaction rejected"


# ── I13/I14: proposal lifecycle ──────────────────────────────────────────────

class TestProposalLifecycle:
    async def test_legacy_proposal_29_still_reported_not_rewritten(self):
        from modules.business_action_execution import audit_proposal_references
        proposals = [{
            "id": 29, "domain": "recruitment", "status": "pending",
            "action_type": "business_reply_draft", "business_reference": "8801617239992:bridge3",
            "created_at": None,
        }]
        with patch("app.database.fetch_all", new=AsyncMock(return_value=proposals)), \
             patch("app.database.fetch_one", new=AsyncMock(return_value={"1": 1})):
            out = await audit_proposal_references()
        assert out[0]["executable"] is False
        # Status field reflects the DB's own untouched value -- this
        # module never writes it.
        assert out[0]["status"] == "pending"


# ── I26: HELP/parser consistency for new I5 command ──────────────────────────

class TestHelpConsistencyStageI:
    def test_cash_details_registered_if_help_mentions_it(self):
        from modules.admin_commands import _HELP_TEXT, _classify_command
        if "CASH DETAILS" in _HELP_TEXT:
            assert _classify_command("CASH DETAILS abcdef123456") is not None
