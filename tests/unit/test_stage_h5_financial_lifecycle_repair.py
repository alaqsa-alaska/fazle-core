"""Stage H.5 (2026-08-29) -- Financial Lifecycle Repair, Proposal
Reference Normalization & Live Execution Closure.

Covers: H5.1 orphan detection/classification, H5.5 evidence-integrity
(no fake data, test-artifact exclusion), H5.6 full classification,
H5.12/13 typed reference contract (including the new "conversation" and
"escort_program" kinds), H5.14 legacy proposal handling, H5.16 review
card enrichment, H5.18 accounting invariants (replay-safety, already
partly covered in test_stage_h_financial_operations.py -- extended
here for the new pieces only)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ── H5.1/H5.5: orphan detection + classification (pure function) ────────────

class TestClassifyOrphan:
    def test_test_artifact_pattern_detected(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "REGTEST_20260702_061236 operator write-path test", "fpe_wa_message_id": 1, "employee_id": 5}
        assert classify_orphan(row) == "TEST_ARTIFACT_SUSPECT"

    def test_probe_marker_detected(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "REGTEST_probe_operator", "fpe_wa_message_id": 1, "employee_id": 5}
        assert classify_orphan(row) == "TEST_ARTIFACT_SUSPECT"

    def test_real_submission_with_full_evidence_is_recoverable(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "Food bill", "fpe_wa_message_id": 10102, "employee_id": 1325}
        assert classify_orphan(row) == "RECOVERABLE"

    def test_missing_wa_message_is_insufficient_evidence(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "Food bill", "fpe_wa_message_id": None, "employee_id": 1325}
        assert classify_orphan(row) == "INSUFFICIENT_EVIDENCE"

    def test_missing_employee_link_is_insufficient_evidence(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "Food bill", "fpe_wa_message_id": 10102, "employee_id": None}
        assert classify_orphan(row) == "INSUFFICIENT_EVIDENCE"

    def test_a_word_containing_test_is_not_misclassified(self):
        """The regex is narrow (exact markers), not a broad 'test'
        substring match -- a real submission mentioning e.g. 'contest
        bonus' must not be misclassified as a test artifact."""
        from modules.fazle_payroll_engine.review_linkage_repair import classify_orphan
        row = {"raw_content": "Contest bonus payout", "fpe_wa_message_id": 1, "employee_id": 5}
        assert classify_orphan(row) == "RECOVERABLE"


class TestBackfillNeverInventsData:
    async def test_dry_run_writes_nothing(self):
        from modules.fazle_payroll_engine.review_linkage_repair import backfill_orphaned_review_links
        orphan_row = {
            "txn_id": 4171, "txn_ref": "fpe-abc", "source_message_id": "7225",
            "employee_id": 1325, "employee_name_raw": None, "amount": 90,
            "payout_method": "cash", "payout_phone": None, "txn_category": "deduction",
            "txn_date": None, "fpe_wa_message_id": 10102, "raw_content": "Food bill",
            "existing_review_id": None,
        }
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.find_orphaned_operator_transactions",
            new=AsyncMock(return_value=[orphan_row]),
        ), patch("modules.fazle_payroll_engine.review_linkage_repair.execute", new=AsyncMock()) as mock_exec:
            result = await backfill_orphaned_review_links(dry_run=True)
        assert result["dry_run"] is True
        assert result["recoverable"] == 1
        mock_exec.assert_not_called()

    async def test_test_artifact_never_backfilled(self):
        from modules.fazle_payroll_engine.review_linkage_repair import backfill_orphaned_review_links
        orphan_row = {
            "txn_id": 2548, "txn_ref": "fpe-497b", "source_message_id": "6978",
            "employee_id": None, "employee_name_raw": None, "amount": 1,
            "payout_method": "cash", "payout_phone": None, "txn_category": "salary",
            "txn_date": None, "fpe_wa_message_id": 9302,
            "raw_content": "REGTEST_20260702_061236 operator write-path test",
            "existing_review_id": None,
        }
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.find_orphaned_operator_transactions",
            new=AsyncMock(return_value=[orphan_row]),
        ), patch("modules.fazle_payroll_engine.review_linkage_repair.execute", new=AsyncMock()) as mock_exec:
            result = await backfill_orphaned_review_links(dry_run=False)
        assert result["recoverable"] == 0
        assert len(result["test_artifact_suspect"]) == 1
        mock_exec.assert_not_called()

    async def test_reconstructed_row_uses_only_already_recorded_fields(self):
        """The INSERT's bound parameters must come from the transaction's
        own already-recorded fields and the unmodified fpe_wa_messages
        raw_content -- never a literal/invented value for amount/
        employee/content."""
        from modules.fazle_payroll_engine.review_linkage_repair import backfill_orphaned_review_links
        orphan_row = {
            "txn_id": 4171, "txn_ref": "fpe-abc", "source_message_id": "7225",
            "employee_id": 1325, "employee_name_raw": None, "amount": 90,
            "payout_method": "cash", "payout_phone": None, "txn_category": "deduction",
            "txn_date": None, "fpe_wa_message_id": 10102, "raw_content": "Food bill",
            "existing_review_id": None,
        }
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.find_orphaned_operator_transactions",
            new=AsyncMock(return_value=[orphan_row]),
        ), patch("modules.fazle_payroll_engine.review_linkage_repair.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.fazle_payroll_engine.review_linkage_repair.execute", new=AsyncMock()) as mock_exec:
            await backfill_orphaned_review_links(dry_run=False)
        args = mock_exec.call_args[0]
        # id, fpe_wa_message_id, raw_content, amount, payout_phone, name, method, date, employee_id, txn_id, note
        assert args[1] == 7225  # review_id == source_message_id, deterministic
        assert args[2] == 10102  # fpe_wa_message_id unchanged
        assert args[3] == "Food bill"  # raw_content unchanged, not invented
        assert args[4] == 90  # amount from the transaction's own field
        assert args[9] == 1325  # resolved_employee_id from the transaction's own field
        assert args[10] == 4171  # promoted_txn_id points at the real transaction

    async def test_replay_is_idempotent_no_double_insert(self):
        from modules.fazle_payroll_engine.review_linkage_repair import backfill_orphaned_review_links
        orphan_row = {
            "txn_id": 4171, "txn_ref": "fpe-abc", "source_message_id": "7225",
            "employee_id": 1325, "employee_name_raw": None, "amount": 90,
            "payout_method": "cash", "payout_phone": None, "txn_category": "deduction",
            "txn_date": None, "fpe_wa_message_id": 10102, "raw_content": "Food bill",
            "existing_review_id": None,
        }
        with patch(
            "modules.fazle_payroll_engine.review_linkage_repair.find_orphaned_operator_transactions",
            new=AsyncMock(return_value=[orphan_row]),
        ), patch("modules.fazle_payroll_engine.review_linkage_repair.fetch_one", new=AsyncMock(return_value={"id": 7225})), \
             patch("modules.fazle_payroll_engine.review_linkage_repair.execute", new=AsyncMock()) as mock_exec:
            result = await backfill_orphaned_review_links(dry_run=False)
        mock_exec.assert_not_called()
        assert result["repaired"] == []


class TestClassifyAllPendingCash:
    async def test_valid_reviewable_when_review_row_linked(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_all_pending_cash_transactions, VALID_PENDING_REVIEWABLE
        row = {
            "txn_id": 1, "txn_ref": "r1", "source": "operator", "source_message_id": "100",
            "employee_id": 5, "employee_name_raw": None, "amount": 100, "payout_method": "cash",
            "txn_date": None, "created_at": None, "fpe_wa_message_id": 1, "raw_content": "x",
            "existing_review_id": 100, "existing_review_status": "operator_pending",
        }
        with patch("modules.fazle_payroll_engine.review_linkage_repair.fetch_all", new=AsyncMock(side_effect=[[row], []])):
            out = await classify_all_pending_cash_transactions()
        assert out[0]["classification"] == VALID_PENDING_REVIEWABLE

    async def test_direct_when_not_operator_sourced(self):
        from modules.fazle_payroll_engine.review_linkage_repair import classify_all_pending_cash_transactions, VALID_PENDING_DIRECT
        row = {
            "txn_id": 2, "txn_ref": "r2", "source": "manual", "source_message_id": None,
            "employee_id": 5, "employee_name_raw": None, "amount": 100, "payout_method": "cash",
            "txn_date": None, "created_at": None, "fpe_wa_message_id": 1, "raw_content": "x",
            "existing_review_id": None, "existing_review_status": None,
        }
        with patch("modules.fazle_payroll_engine.review_linkage_repair.fetch_all", new=AsyncMock(side_effect=[[row], []])):
            out = await classify_all_pending_cash_transactions()
        assert out[0]["classification"] == VALID_PENDING_DIRECT

    async def test_duplicate_suspect_overridden_by_test_artifact(self):
        """Test-artifact classification takes priority over the
        duplicate-amount heuristic -- the more specific, more actionable
        signal wins (this exactly matches the 4 live REGTEST rows, which
        also share amount=1.00/no employee/the same date)."""
        from modules.fazle_payroll_engine.review_linkage_repair import classify_all_pending_cash_transactions, TEST_ARTIFACT_SUSPECT
        row = {
            "txn_id": 3, "txn_ref": "r3", "source": "operator", "source_message_id": "200",
            "employee_id": None, "employee_name_raw": None, "amount": 1, "payout_method": "cash",
            "txn_date": "2026-07-02", "created_at": None, "fpe_wa_message_id": 1,
            "raw_content": "REGTEST_probe_operator", "existing_review_id": None, "existing_review_status": None,
        }
        dupe = {"employee_id": None, "amount": 1, "txn_date": "2026-07-02", "n": 2}
        with patch("modules.fazle_payroll_engine.review_linkage_repair.fetch_all", new=AsyncMock(side_effect=[[row], [dupe]])):
            out = await classify_all_pending_cash_transactions()
        assert out[0]["classification"] == TEST_ARTIFACT_SUSPECT


# ── H5.13: typed reference contract, new "conversation"/"escort_program" kinds ──

class TestConversationReferenceKind:
    async def test_conversation_state_no_new_activity_is_actionable(self):
        from modules.business_action_execution import _current_state_for_reference, _is_actionable
        proposed_at = datetime(2026, 8, 28, 16, 54, tzinfo=timezone.utc)
        with patch("modules.business_action_execution.fetch_one", new=AsyncMock(
            return_value={"message_count": 3, "last_received_at": proposed_at - timedelta(minutes=5)},
        )):
            state = await _current_state_for_reference("conversation", "01700000000:bridge3")
        assert _is_actionable("conversation", state, proposed_at=proposed_at) is True

    async def test_conversation_new_activity_since_proposal_is_stale(self):
        """The exact live situation found for proposal #29: messages
        continued arriving in the conversation after the proposal was
        created -- must be treated as stale."""
        from modules.business_action_execution import _current_state_for_reference, _is_actionable
        proposed_at = datetime(2026, 8, 28, 16, 54, tzinfo=timezone.utc)
        with patch("modules.business_action_execution.fetch_one", new=AsyncMock(
            return_value={"message_count": 5, "last_received_at": proposed_at + timedelta(minutes=8)},
        )):
            state = await _current_state_for_reference("conversation", "01700000000:bridge3")
        assert _is_actionable("conversation", state, proposed_at=proposed_at) is False

    async def test_business_reply_draft_over_conversation_kind_refused(self):
        """business_reply_draft can only execute against a real draft id
        -- a conversation-kind reference (no draft created yet) must be
        refused, not silently treated as if it were a draft."""
        from modules.business_action_execution import execute_approved_business_action, BusinessExecutionError
        action = {
            "id": 29, "action_type": "business_reply_draft",
            "business_reference": "01700000000:bridge3", "status": "approved",
            "created_at": datetime(2026, 8, 28, tzinfo=timezone.utc),
        }
        with patch("modules.hermes_tasks.get_action", new=AsyncMock(return_value=action)), \
             patch("modules.hermes_tasks.is_action_approved", new=AsyncMock(return_value=True)):
            with pytest.raises(BusinessExecutionError):
                await execute_approved_business_action(29)


class TestEscortProgramReferenceKind:
    async def test_draft_or_confirmed_is_actionable(self):
        from modules.business_action_execution import _is_actionable
        assert _is_actionable("escort_program", {"status": "draft"}) is True
        assert _is_actionable("escort_program", {"status": "confirmed"}) is True

    async def test_completed_or_cancelled_is_stale(self):
        from modules.business_action_execution import _is_actionable
        assert _is_actionable("escort_program", {"status": "completed"}) is False
        assert _is_actionable("escort_program", {"status": "cancelled"}) is False


# ── H5.12: proposal reference audit ─────────────────────────────────────────

class TestAuditProposalReferences:
    async def test_untyped_conversation_key_reported_migratable_not_executable(self):
        from modules.business_action_execution import audit_proposal_references
        proposals = [{
            "id": 29, "domain": "recruitment", "status": "pending",
            "action_type": "business_reply_draft", "business_reference": "01700000000:bridge3",
            "created_at": None,
        }]
        with patch("app.database.fetch_all", new=AsyncMock(return_value=proposals)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value={"1": 1})):
            out = await audit_proposal_references()
        assert out[0]["executable"] is False
        assert out[0]["migratable"] is True

    async def test_typed_reference_reported_executable(self):
        from modules.business_action_execution import audit_proposal_references
        proposals = [{
            "id": 31, "domain": "cash", "status": "pending",
            "action_type": "business_record_review", "business_reference": "cash_txn:100",
            "created_at": None,
        }]
        with patch("app.database.fetch_all", new=AsyncMock(return_value=proposals)), \
             patch("modules.business_action_execution.fetch_one", new=AsyncMock(return_value={"id": 100, "transaction_status": "pending"})):
            out = await audit_proposal_references()
        assert out[0]["executable"] is True
        assert out[0]["reference_kind"] == "cash_txn"

    async def test_never_mutates_proposal_status(self):
        """H5.14: auditing must never write anything -- structural proof."""
        import inspect
        from modules.business_action_execution import audit_proposal_references
        src = inspect.getsource(audit_proposal_references)
        assert "UPDATE " not in src
        assert "DELETE FROM" not in src
        assert "reject_action" not in src
        assert "approve_action" not in src
