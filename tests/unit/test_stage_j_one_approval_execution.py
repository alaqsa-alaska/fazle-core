"""Stage J (2026-08-29) -- Operational Command Closure, Management
Decisions, Single-Approval Business Execution & Monitoring Boundary
Cleanup.

Covers: J2/J3/J4/J5 (one-approval principle + proposed_operation
contract, no schema migration), J15/J16 (direct-command and proposal-
approval routes converge on the same execution function), J20 (proposal
#31's exact live semantics, never reinterpreted), J22 (proposal #30 is
review-only, same pattern), J26 (proposal reject never mutates target)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── J4/J5: proposed_operation contract (pure function) ──────────────────────

class TestGetProposedOperation:
    def test_none_when_no_evidence(self):
        from modules.business_action_execution import get_proposed_operation
        assert get_proposed_operation(None) is None
        assert get_proposed_operation([]) is None

    def test_none_when_no_proposed_operation_entry(self):
        """The exact live shape of proposal #31's evidence_refs -- must
        stay None, never inferred from txn_id/txn_ref alone."""
        from modules.business_action_execution import get_proposed_operation
        refs = [{"type": "txn_id", "value": 4171}, {"type": "txn_ref", "value": "fpe-abc"}]
        assert get_proposed_operation(refs) is None

    def test_found_when_present(self):
        from modules.business_action_execution import get_proposed_operation
        refs = [{"type": "txn_id", "value": 4171}, {"type": "proposed_operation", "value": "cash_approve"}]
        assert get_proposed_operation(refs) == "cash_approve"

    def test_handles_raw_jsonb_string(self):
        """asyncpg returns jsonb as a raw string here (confirmed live, no
        codec registered) -- must deserialize, not choke."""
        from modules.business_action_execution import get_proposed_operation
        raw = '[{"type": "proposed_operation", "value": "cash_approve"}]'
        assert get_proposed_operation(raw) == "cash_approve"

    def test_malformed_string_returns_none_not_raises(self):
        from modules.business_action_execution import get_proposed_operation
        assert get_proposed_operation("not json") is None


# ── J20: proposal #31's exact live semantics, never reinterpreted ───────────

class TestProposal31NeverReinterpreted:
    async def test_review_only_proposal_stays_no_op_never_calls_cash_approve(self):
        """The exact live shape of proposal #31 -- action_type=
        business_record_review, evidence_refs=[txn_id, txn_ref] only, NO
        proposed_operation entry. Approving/executing it must remain a
        pure acknowledgement, never touch fpe_cash_transactions."""
        from modules.business_action_execution import execute_approved_business_action
        action = {
            "id": 31, "action_type": "business_record_review", "domain": "cash",
            "business_reference": "cash_txn:4171", "status": "approved",
            "evidence_refs": '[{"type": "txn_id", "value": 4171}, {"type": "txn_ref", "value": "fpe-d8080cd35b3c6aaf"}]',
            "created_at": None,
        }
        current_txn = {"id": 4171, "transaction_status": "pending", "employee_id": 5, "amount": 90, "txn_ref": "fpe-abc", "deleted_at": None}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)), \
             patch("modules.hermes_tasks.record_execution_result", new=AsyncMock(return_value={"ok": True})), \
             patch("modules.financial_operations.cash_approve", new=AsyncMock()) as mock_cash_approve:
            result = await execute_approved_business_action(31, executor="owner")
        assert result["result"]["no_op"] is True
        mock_cash_approve.assert_not_called()


# ── J15/J16: converged execution -- proposal-approve and direct-command
# use the exact same authoritative function ──────────────────────────────────

class TestProposalCashApproveConvergesOnSameFunction:
    async def test_action_proposal_with_proposed_operation_calls_cash_approve(self):
        """The FUTURE-correct contract (J21): a proposal that DOES carry
        an explicit proposed_operation=cash_approve entry executes via
        the exact same modules.financial_operations.cash_approve() CASH
        APPROVE <ref> uses -- no duplicated mutation logic."""
        from modules.business_action_execution import execute_approved_business_action
        action = {
            "id": 99, "action_type": "business_record_review", "domain": "cash",
            "business_reference": "cash_txn:4171", "status": "approved",
            "evidence_refs": '[{"type": "proposed_operation", "value": "cash_approve"}]',
            "created_at": None,
        }
        current_txn = {"id": 4171, "transaction_status": "pending", "employee_id": 5, "amount": 90, "txn_ref": "fpe-abc", "deleted_at": None}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)), \
             patch("modules.hermes_tasks.record_execution_result", new=AsyncMock(return_value={"ok": True})), \
             patch("modules.financial_operations.cash_approve", new=AsyncMock(return_value={"ok": True, "txn_id": 4171, "txn_ref": "fpe-abc"})) as mock_cash_approve:
            result = await execute_approved_business_action(99, executor="owner")
        mock_cash_approve.assert_called_once_with("4171", "owner")
        assert result["result"]["executed"] is True
        assert result["result"]["operation"] == "cash_approve"

    async def test_stale_underlying_state_refuses_even_with_proposed_operation(self):
        """J3: revalidation is not skipped just because an exact
        operation was declared -- if the transaction moved since
        approval (e.g. already approved through the normal path), this
        must refuse, not execute a second time."""
        from modules.business_action_execution import execute_approved_business_action, StaleProposalError
        action = {
            "id": 99, "action_type": "business_record_review", "domain": "cash",
            "business_reference": "cash_txn:4171", "status": "approved",
            "evidence_refs": '[{"type": "proposed_operation", "value": "cash_approve"}]',
            "created_at": None,
        }
        current_txn = {"id": 4171, "transaction_status": "final", "employee_id": 5, "amount": 90, "txn_ref": "fpe-abc", "deleted_at": None}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)):
            with pytest.raises(StaleProposalError):
                await execute_approved_business_action(99, executor="owner")

    async def test_unsupported_proposed_operation_refused_not_guessed(self):
        from modules.business_action_execution import execute_approved_business_action, BusinessExecutionError
        action = {
            "id": 99, "action_type": "business_record_review", "domain": "cash",
            "business_reference": "cash_txn:4171", "status": "approved",
            "evidence_refs": '[{"type": "proposed_operation", "value": "cash_reverse"}]',
            "created_at": None,
        }
        current_txn = {"id": 4171, "transaction_status": "pending", "employee_id": 5, "amount": 90, "txn_ref": "fpe-abc", "deleted_at": None}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)):
            with pytest.raises(BusinessExecutionError):
                await execute_approved_business_action(99, executor="owner")

    async def test_execution_failure_from_cash_approve_raises_stale_not_silent(self):
        """If the converged cash_approve() call itself refuses (e.g. no
        linked review row), the proposal execution must surface that as
        a clear failure, never silently mark it executed."""
        from modules.business_action_execution import execute_approved_business_action, StaleProposalError
        action = {
            "id": 99, "action_type": "business_record_review", "domain": "cash",
            "business_reference": "cash_txn:4171", "status": "approved",
            "evidence_refs": '[{"type": "proposed_operation", "value": "cash_approve"}]',
            "created_at": None,
        }
        current_txn = {"id": 4171, "transaction_status": "pending", "employee_id": 5, "amount": 90, "txn_ref": "fpe-abc", "deleted_at": None}
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value=current_txn)), \
             patch("modules.financial_operations.cash_approve", new=AsyncMock(return_value={"ok": False, "error": "no linked pending operator-review row"})):
            with pytest.raises(StaleProposalError):
                await execute_approved_business_action(99, executor="owner")


# ── J22: proposal #30 is review-only, same pattern as #31 ───────────────────

class TestProposal30ReviewOnly:
    def test_no_proposed_operation_in_live_evidence_shape(self):
        from modules.business_action_execution import get_proposed_operation
        refs = '[{"type": "program_id", "value": 1087}, {"type": "status", "value": "draft"}]'
        assert get_proposed_operation(refs) is None


# ── J26: proposal reject never mutates the underlying target ────────────────

class TestProposalRejectNeverMutatesTarget:
    def test_reject_action_never_touches_business_domain_tables(self):
        import inspect
        from modules.hermes_tasks import reject_action
        src = inspect.getsource(reject_action)
        assert "fpe_cash_transactions" not in src
        assert "operator_approve" not in src
        assert "operator_reject" not in src
