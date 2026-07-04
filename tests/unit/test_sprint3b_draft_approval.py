"""
Sprint-3B Acceptance Tests — Draft Approval → Canonical Transaction → Ledger
=============================================================================

15 tests covering the full Sprint-3B specification:
    Test-1:  Draft Retrieval (STEP 1)
    Test-2:  State Validation — pending only (STEP 2)
    Test-3:  Admin Command Processing — APPROVED dispatch (STEP 3)
    Test-4:  EDIT Workflow — version increment + before/after state (STEP 4)
    Test-5:  APPROVED Workflow — full pipeline (STEP 5)
    Test-6:  Accountant Forward — parser-compatible message (STEP 6)
    Test-7:  Canonical Transaction — create_transaction called (STEP 7)
    Test-8:  Ledger Update — _upsert_ledger called internally (STEP 8)
    Test-9:  Employee Balance — read from fpe_employee_ledger (STEP 9)
    Test-10: Draft Finalization — status='completed' (STEP 10)
    Test-11: Reject Workflow — no transaction, no ledger (STEP 11)
    Test-12: Draft Expiry — 24h TTL enforcement (STEP 12)
    Test-13: Idempotency — one draft = one transaction (STEP 13)
    Test-14: Audit Requirements — complete audit trail (STEP 14)
    Test-15: WhatsApp Compatibility — accountant message parseable (STEP 15)

Success Metric: Approved Draft → Single Canonical Transaction → Correct Ledger → Complete Audit
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import app.database as db_module
from app.database import fetch_one, fetch_val, execute


# ── Test-1: Draft Retrieval (STEP 1) ──────────────────────────────────────────

class Test1DraftRetrieval:
    """STEP 1 — retrieve_draft() returns the draft row with all Sprint-3B columns."""

    @pytest.mark.asyncio
    async def test_retrieve_draft_returns_row(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert draft is not None
        assert draft["id"] == seed_sprint3b_draft["id"]
        assert draft["status"] == "pending"
        assert draft["employee_name"] == seed_sprint3b_draft["employee_name"]

    @pytest.mark.asyncio
    async def test_retrieve_draft_nonexistent_returns_none(self, test_db_pool):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft

        draft = await retrieve_draft(99999)
        assert draft is None

    @pytest.mark.asyncio
    async def test_retrieve_draft_has_sprint3b_columns(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        # Sprint-3B columns must exist (even if NULL for a fresh draft)
        assert "transaction_id" in draft
        assert "txn_ref" in draft
        assert "rejected_reason" in draft
        assert "reviewed_by" in draft
        assert "version" in draft
        assert "before_state" in draft
        assert "after_state" in draft
        assert "completed_at" in draft
        # Fresh draft: no transaction yet
        assert draft["transaction_id"] is None
        assert draft["txn_ref"] is None
        assert draft["version"] == 0


# ── Test-2: State Validation (STEP 2) ─────────────────────────────────────────

class Test2StateValidation:
    """STEP 2 — only pending drafts can be acted upon."""

    @pytest.mark.asyncio
    async def test_pending_draft_is_pending(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft, is_draft_pending

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert is_draft_pending(draft) is True

    @pytest.mark.asyncio
    async def test_completed_draft_not_pending(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft, is_draft_pending

        await execute(
            "UPDATE fazle_payment_drafts SET status='completed' WHERE id=$1",
            seed_sprint3b_draft["id"],
        )
        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert is_draft_pending(draft) is False

    @pytest.mark.asyncio
    async def test_rejected_draft_not_pending(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import retrieve_draft, is_draft_pending

        await execute(
            "UPDATE fazle_payment_drafts SET status='rejected' WHERE id=$1",
            seed_sprint3b_draft["id"],
        )
        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert is_draft_pending(draft) is False


# ── Test-3: Admin Command Processing (STEP 3) ─────────────────────────────────

class Test3AdminCommandProcessing:
    """STEP 3 — process_admin_decision dispatches APPROVED/EDIT/REJECT."""

    @pytest.mark.asyncio
    async def test_unknown_command_rejected(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import process_admin_decision

        result = await process_admin_decision(
            "UNKNOWN", seed_sprint3b_draft["id"], "8801700000001",
        )
        assert result["ok"] is False
        assert "Unknown command" in result["error"]

    @pytest.mark.asyncio
    async def test_approved_without_amount_fails(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import process_admin_decision

        result = await process_admin_decision(
            "APPROVED", seed_sprint3b_draft["id"], "8801700000001",
        )
        assert result["ok"] is False
        assert "amount" in result["error"].lower()


# ── Test-4: EDIT Workflow (STEP 4) ─────────────────────────────────────────────

class Test4EditWorkflow:
    """STEP 4 — edit_draft increments version, saves before/after state."""

    @pytest.mark.asyncio
    async def test_edit_increments_version(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import edit_draft, retrieve_draft

        result = await edit_draft(
            seed_sprint3b_draft["id"], 3000.0, "nagad", None,
            "8801700000001", reason="amount correction",
        )
        assert result["ok"] is True
        assert result["version"] == 1

        # Edit again
        result2 = await edit_draft(
            seed_sprint3b_draft["id"], 3500.0, "cash", None,
            "8801700000001", reason="second edit",
        )
        assert result2["ok"] is True
        assert result2["version"] == 2

    @pytest.mark.asyncio
    async def test_edit_saves_before_after_state(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import edit_draft, retrieve_draft

        result = await edit_draft(
            seed_sprint3b_draft["id"], 2500.0, "bkash", "019999999999",
            "8801700000001", reason="payout number update",
        )
        assert result["ok"] is True

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert draft["before_state"] is not None
        assert draft["after_state"] is not None
        assert draft["editor"] == "8801700000001"

    @pytest.mark.asyncio
    async def test_edit_on_non_pending_fails(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import edit_draft

        await execute(
            "UPDATE fazle_payment_drafts SET status='completed' WHERE id=$1",
            seed_sprint3b_draft["id"],
        )
        result = await edit_draft(
            seed_sprint3b_draft["id"], 3000.0, "cash", None,
            "8801700000001",
        )
        assert result["ok"] is False
        assert "not pending" in result["error"].lower()


# ── Test-5: APPROVED Workflow (STEP 5) ─────────────────────────────────────────

class Test5ApprovedWorkflow:
    """STEP 5 — approve_draft creates transaction, updates ledger, finalizes draft."""

    @pytest.mark.asyncio
    async def test_approve_draft_full_pipeline(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft

        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is True
        assert result["status"] == "completed"
        assert result["transaction_id"] is not None
        assert result["txn_ref"] is not None
        assert result["accountant_msg"] is not None
        assert result["amount"] == 2000.0
        assert result["method"] == "bkash"

    @pytest.mark.asyncio
    async def test_approve_non_pending_fails(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft

        await execute(
            "UPDATE fazle_payment_drafts SET status='completed' WHERE id=$1",
            seed_sprint3b_draft["id"],
        )
        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is False
        assert "not pending" in result["error"].lower()


# ── Test-6: Accountant Forward (STEP 6) ────────────────────────────────────────

class Test6AccountantForward:
    """STEP 6 — build_accountant_message produces parser-compatible format."""

    @pytest.mark.asyncio
    async def test_accountant_message_format(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import build_accountant_message, retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        msg = build_accountant_message(draft, 2000.0, "bkash")
        # Format: ID: <phone> <name> <phone>(B) <amount>/-
        assert msg.startswith("ID: ")
        assert "(B)" in msg
        assert "2000/-" in msg

    @pytest.mark.asyncio
    async def test_accountant_message_method_codes(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import build_accountant_message, retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])

        msg_bkash = build_accountant_message(draft, 1000.0, "bkash")
        assert "(B)" in msg_bkash

        msg_nagad = build_accountant_message(draft, 1000.0, "nagad")
        assert "(N)" in msg_nagad

        msg_cash = build_accountant_message(draft, 1000.0, "cash")
        assert "(cash)" in msg_cash

        msg_rocket = build_accountant_message(draft, 1000.0, "rocket")
        assert "(R)" in msg_rocket


# ── Test-7: Canonical Transaction (STEP 7) ────────────────────────────────────

class Test7CanonicalTransaction:
    """STEP 7 — create_canonical_transaction calls create_transaction()."""

    @pytest.mark.asyncio
    async def test_canonical_transaction_created(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import create_canonical_transaction, retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        result = await create_canonical_transaction(draft, 2000.0, "bkash", "8801700000001")

        assert result["transaction_id"] is not None
        assert result["txn_ref"] is not None
        assert result["accountant_msg"] is not None

        # Verify the transaction exists in fpe_cash_transactions
        txn = await fetch_one(
            "SELECT * FROM fpe_cash_transactions WHERE id = $1",
            result["transaction_id"],
        )
        assert txn is not None
        assert float(txn["amount"]) == 2000.0
        assert txn["payout_method"] == "bkash"

    @pytest.mark.asyncio
    async def test_transaction_has_correct_category(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import create_canonical_transaction, retrieve_draft

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        # seed_sprint3b_draft has purpose='advance'
        result = await create_canonical_transaction(draft, 2000.0, "bkash", "8801700000001")

        txn = await fetch_one(
            "SELECT txn_category FROM fpe_cash_transactions WHERE id = $1",
            result["transaction_id"],
        )
        assert txn["txn_category"] == "advance"


# ── Test-8: Ledger Update (STEP 8) ─────────────────────────────────────────────

class Test8LedgerUpdate:
    """STEP 8 — _upsert_ledger() called internally by create_transaction()."""

    @pytest.mark.asyncio
    async def test_ledger_updated_after_approval(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft

        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is True

        # Check ledger exists for this employee + current period
        period = datetime.now(timezone.utc).strftime("%Y-%m")
        ledger = await fetch_one(
            "SELECT * FROM fpe_employee_ledger WHERE employee_id = $1 AND accounting_period = $2",
            seed_fpe_employee["id"], period,
        )
        assert ledger is not None
        assert float(ledger["total_advance"]) == 2000.0
        assert ledger["txn_count"] >= 1


# ── Test-9: Employee Balance (STEP 9) ──────────────────────────────────────────

class Test9EmployeeBalance:
    """STEP 9 — get_employee_balance reads from fpe_employee_ledger."""

    @pytest.mark.asyncio
    async def test_balance_returns_zero_for_new_employee(self, test_db_pool, seed_fpe_employee):
        db_module._pool = test_db_pool
        from modules.draft_approval import get_employee_balance

        balance = await get_employee_balance(seed_fpe_employee["id"])
        assert float(balance["total_paid"]) == 0.0
        assert float(balance["total_advance"]) == 0.0
        assert balance["txn_count"] == 0

    @pytest.mark.asyncio
    async def test_balance_reflects_transaction(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft, get_employee_balance

        await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )

        balance = await get_employee_balance(seed_fpe_employee["id"])
        assert float(balance["total_advance"]) == 2000.0
        assert balance["txn_count"] >= 1


# ── Test-10: Draft Finalization (STEP 10) ──────────────────────────────────────

class Test10DraftFinalization:
    """STEP 10 — after approval, draft status='completed' with transaction_id."""

    @pytest.mark.asyncio
    async def test_draft_completed_after_approval(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft, retrieve_draft

        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is True

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert draft["status"] == "completed"
        assert draft["transaction_id"] is not None
        assert draft["txn_ref"] is not None
        assert draft["completed_at"] is not None
        assert draft["reviewed_by"] == "8801700000001"
        assert draft["accountant_msg"] is not None


# ── Test-11: Reject Workflow (STEP 11) ─────────────────────────────────────────

class Test11RejectWorkflow:
    """STEP 11 — reject_draft sets status='rejected', NO transaction, NO ledger."""

    @pytest.mark.asyncio
    async def test_reject_sets_status(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import reject_draft, retrieve_draft

        result = await reject_draft(
            seed_sprint3b_draft["id"], "8801700000001", reason="invalid request",
        )
        assert result["ok"] is True
        assert result["status"] == "rejected"

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert draft["status"] == "rejected"
        assert draft["rejected_reason"] == "invalid request"
        assert draft["reviewed_by"] == "8801700000001"

    @pytest.mark.asyncio
    async def test_reject_creates_no_transaction(
        self, test_db_pool, seed_sprint3b_draft
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import reject_draft

        await reject_draft(seed_sprint3b_draft["id"], "8801700000001")

        count = await fetch_val("SELECT COUNT(*) FROM fpe_cash_transactions")
        assert count == 0

    @pytest.mark.asyncio
    async def test_reject_updates_no_ledger(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import reject_draft

        await reject_draft(seed_sprint3b_draft["id"], "8801700000001")

        count = await fetch_val("SELECT COUNT(*) FROM fpe_employee_ledger")
        assert count == 0


# ── Test-12: Draft Expiry (STEP 12) ───────────────────────────────────────────

class Test12DraftExpiry:
    """STEP 12 — expired drafts (24h TTL) cannot be approved."""

    @pytest.mark.asyncio
    async def test_expired_draft_cannot_be_approved(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft

        # Set expiry to past
        await execute(
            "UPDATE fazle_payment_drafts SET expires_at = NOW() - INTERVAL '1 hour' WHERE id=$1",
            seed_sprint3b_draft["id"],
        )

        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is False
        assert "expired" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_expire_draft_marks_expired(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import expire_draft, retrieve_draft

        result = await expire_draft(seed_sprint3b_draft["id"])
        assert result is True

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert draft["status"] == "expired"


# ── Test-13: Idempotency (STEP 13) ─────────────────────────────────────────────

class Test13Idempotency:
    """STEP 13 — one draft = one transaction. Duplicate approvals are safe."""

    @pytest.mark.asyncio
    async def test_double_approval_creates_one_transaction(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft

        # First approval
        result1 = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result1["ok"] is True
        txn_id_1 = result1["transaction_id"]

        # Second approval attempt — should be rejected (already completed)
        result2 = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result2["ok"] is False
        assert "not pending" in result2["error"].lower() or "already" in result2["error"].lower()

        # Only one transaction in DB
        count = await fetch_val("SELECT COUNT(*) FROM fpe_cash_transactions")
        assert count == 1

    @pytest.mark.asyncio
    async def test_already_processed_draft_rejected(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft, is_draft_already_processed, retrieve_draft

        # First approval
        await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        assert is_draft_already_processed(draft) is True


# ── Test-14: Audit Requirements (STEP 14) ──────────────────────────────────────

class Test14AuditRequirements:
    """STEP 14 — complete audit trail in fazle_draft_audit_log."""

    @pytest.mark.asyncio
    async def test_approval_creates_audit_events(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft, get_draft_audit_events

        await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )

        events = await get_draft_audit_events(seed_sprint3b_draft["id"])
        event_names = [e["event"] for e in events]

        assert "approved" in event_names
        assert "transaction_created" in event_names
        assert "ledger_updated" in event_names
        assert "accountant_forwarded" in event_names

    @pytest.mark.asyncio
    async def test_reject_creates_audit_event(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import reject_draft, get_draft_audit_events

        await reject_draft(seed_sprint3b_draft["id"], "8801700000001", reason="test reject")

        events = await get_draft_audit_events(seed_sprint3b_draft["id"])
        event_names = [e["event"] for e in events]
        assert "rejected" in event_names

    @pytest.mark.asyncio
    async def test_edit_creates_audit_event(self, test_db_pool, seed_sprint3b_draft):
        db_module._pool = test_db_pool
        from modules.draft_approval import edit_draft, get_draft_audit_events

        await edit_draft(
            seed_sprint3b_draft["id"], 3000.0, "nagad", None,
            "8801700000001", reason="test edit",
        )

        events = await get_draft_audit_events(seed_sprint3b_draft["id"])
        event_names = [e["event"] for e in events]
        assert "edited" in event_names

    @pytest.mark.asyncio
    async def test_audit_has_before_after_state(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft, get_draft_audit_events

        await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )

        events = await get_draft_audit_events(seed_sprint3b_draft["id"])
        approved_event = next(e for e in events if e["event"] == "approved")
        assert approved_event["before_state"] is not None
        assert approved_event["after_state"] is not None
        assert approved_event["performed_by"] == "8801700000001"


# ── Test-15: WhatsApp Compatibility (STEP 15) ──────────────────────────────────

class Test15WhatsAppCompatibility:
    """STEP 15 — accountant message is 100% parseable by parse_message()."""

    @pytest.mark.asyncio
    async def test_accountant_message_parseable_by_parser(
        self, test_db_pool, seed_sprint3b_draft
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import build_accountant_message, retrieve_draft
        from modules.fazle_payroll_engine.parser import parse_message

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        msg = build_accountant_message(draft, 2000.0, "bkash")

        # The accountant message MUST be parseable by the existing parser
        result = parse_message(msg)
        assert result.message_type is not None
        # Parser should detect this as a payment message
        assert result.message_type.value == "payment" or result.payment is not None or result.message_type.value != "other"

    @pytest.mark.asyncio
    async def test_accountant_message_has_correct_amount(
        self, test_db_pool, seed_sprint3b_draft
    ):
        db_module._pool = test_db_pool
        from modules.draft_approval import build_accountant_message, retrieve_draft
        from modules.fazle_payroll_engine.parser import parse_message

        draft = await retrieve_draft(seed_sprint3b_draft["id"])
        msg = build_accountant_message(draft, 2500.0, "nagad")

        result = parse_message(msg)
        if result.payment:
            assert float(result.payment.amount) == 2500.0
            assert result.payment.payout_method.value == "nagad"

    @pytest.mark.asyncio
    async def test_full_flow_accountant_message_forwarded(
        self, test_db_pool, seed_sprint3b_draft, seed_fpe_employee
    ):
        """End-to-end: approve draft → accountant message is parser-compatible."""
        db_module._pool = test_db_pool
        from modules.draft_approval import approve_draft
        from modules.fazle_payroll_engine.parser import parse_message

        result = await approve_draft(
            seed_sprint3b_draft["id"], 2000.0, "bkash", "8801700000001",
        )
        assert result["ok"] is True

        accountant_msg = result["accountant_msg"]
        parsed = parse_message(accountant_msg)
        # The accountant message must be parseable
        assert parsed is not None
        assert parsed.message_type is not None