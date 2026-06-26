"""
Unit tests for new FPE Payroll/Review Queue business rules.

Uses mocked DB helpers — no real database connection required.
"""
from __future__ import annotations

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from modules.fazle_payroll_engine.validation import (
    validate_for_accounting,
    MESSAGE_RULES,
    get_rule,
)
from modules.fazle_payroll_engine.workers import _eligible_for_accounting_review
from modules.fazle_payroll_engine.employee import (
    create_employee_manual,
    _is_valid_human_name,
)
from modules.fazle_payroll_engine.reconcile import compute_reconciliation
from modules.fazle_payroll_engine.routes import (
    _cleanup_stale_review_queue_rows,
    _mark_stale_employees_inactive,
)


# ── Pure validation / eligibility rules ──────────────────────────────────────

class TestReviewQueueEligibility:
    """Incomplete parsed items must not enter the Review Queue."""

    def test_missing_name_excluded(self):
        pdata = {
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_missing_payout_phone_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_missing_payout_method_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_missing_amount_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_unknown_method_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "unknown",
            "amount": "5000",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_non_positive_amount_excluded(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "0",
        }
        assert not _eligible_for_accounting_review(pdata)

    def test_complete_item_enters_review_queue(self):
        pdata = {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        }
        assert _eligible_for_accounting_review(pdata)


class TestValidationForAccounting:
    """validate_for_accounting enforces per-type field requirements."""

    def test_payment_missing_each_field(self):
        rule = get_rule("payment")
        assert not validate_for_accounting("payment", {}).valid
        assert not validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
        }).valid
        assert not validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "amount": "5000",
        }).valid

    def test_payment_complete_is_valid(self):
        result = validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "5000",
        })
        assert result.valid

    def test_amount_non_positive_rejected(self):
        result = validate_for_accounting("payment", {
            "employee_name_raw": "Jakir",
            "payout_phone": "01712345678",
            "payout_method": "bkash",
            "amount": "0",
        })
        assert not result.valid

    def test_escort_payment_empty_entries_rejected(self):
        assert not validate_for_accounting("escort_payment", {"entries": []}).valid

    def test_escort_payment_valid_entries_accepted(self):
        result = validate_for_accounting("escort_payment", {
            "entries": [{"name": "A", "amount": "100"}, {"name": "B", "amount": "200"}]
        })
        assert result.valid


# ── Employee name / creation rules ───────────────────────────────────────────

class TestEmployeeNameValidation:
    """Name helpers reject invalid/placeholder identities."""

    def test_rejects_empty(self):
        assert not _is_valid_human_name(None)
        assert not _is_valid_human_name("")
        assert not _is_valid_human_name("   ")

    def test_rejects_short(self):
        assert not _is_valid_human_name("A")

    def test_rejects_placeholders(self):
        for p in ["unknown", "unnamed", "none", "n/a", "na"]:
            assert not _is_valid_human_name(p), f"should reject {p!r}"

    def test_rejects_pure_numeric(self):
        assert not _is_valid_human_name("01712345678")
        assert not _is_valid_human_name("+8801712345678")

    def test_rejects_no_letters(self):
        assert not _is_valid_human_name("-----")
        assert not _is_valid_human_name("()()()")

    def test_accepts_valid_names(self):
        assert _is_valid_human_name("Jakir Hossain")
        assert _is_valid_human_name("মোঃ আল মোমিন")
        assert _is_valid_human_name("Md. Jakir")


class TestCreateEmployeeManual:
    """create_employee_manual enforces existing-table-only inserts."""

    @patch("modules.fazle_payroll_engine.employee.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.employee.db_conn")
    async def test_creates_with_expected_columns(self, mock_db_conn, mock_fetch_one):
        # mock_fetch_one is the module-level fetch_one called after the transaction
        mock_fetch_one.return_value = {"id": 1, "full_name": "Test User",
                                       "primary_phone": "01712345678",
                                       "employee_id_phone": "01712345678",
                                       "employee_code": "EMP-00001",
                                       "status": "active", "department": "Staff",
                                       "created_source": "admin_manual_create"}

        class FakeTransaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeConn:
            def __init__(self):
                self.execute_calls = []
                self.fetchval_calls = []

            def transaction(self):
                return FakeTransaction()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, sql, *a, **kw):
                self.execute_calls.append(sql)

            async def fetchrow(self, sql, *a, **kw):
                # Simulate no existing employee
                return None

            async def fetchval(self, sql, *a, **kw):
                self.fetchval_calls.append(sql)
                return 1

        fake_conn = FakeConn()
        mock_db_conn.return_value = fake_conn

        emp = await create_employee_manual(
            full_name="Test User",
            employee_mobile="01712345678",
            role_or_type="Staff",
            status="active",
        )
        assert emp["id"] == 1
        # INSERT INTO fpe_employees is done via con.fetchval (RETURNING id)
        assert any("INSERT INTO fpe_employees" in sql for sql in fake_conn.fetchval_calls)
        # No ALTER TABLE should ever be issued
        assert not any("ALTER TABLE" in sql for sql in fake_conn.execute_calls)
        assert not any("ALTER TABLE" in sql for sql in fake_conn.fetchval_calls)


# ── Reconciliation parser-total-availability rule ─────────────────────────────

class TestReconcileParserTotalAvailability:
    """Delta must be None (not misleading) when parser total is unavailable."""

    @patch("modules.fazle_payroll_engine.reconcile.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.reconcile.fetch_val", new_callable=AsyncMock)
    async def test_delta_none_when_no_parser_data(self, mock_fetch_val, mock_fetch_one):
        mock_fetch_one.return_value = None
        mock_fetch_val.return_value = Decimal("0")

        result = await compute_reconciliation(period="2026-05")
        assert result["delta"] is None
        assert result["parser_total_available"] is False
        assert result["ok"] is None

    @patch("modules.fazle_payroll_engine.reconcile.fetch_one", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.reconcile.fetch_val", new_callable=AsyncMock)
    async def test_delta_calculated_when_parser_data_present(self, mock_fetch_val, mock_fetch_one):
        parser_row = {"parser_sum": Decimal("10000"), "parser_count": 5}
        mock_fetch_one.return_value = parser_row
        # side_effect: ledger_sum, unmatched_sum, pending_review, dlq_count
        mock_fetch_val.side_effect = [Decimal("9500"), Decimal("500"), 0, 0]

        result = await compute_reconciliation(period="2026-05")
        assert result["delta"] is not None
        assert result["parser_total_available"] is True


# ── Review queue cleanup rule ─────────────────────────────────────────────────

class TestReviewQueueCleanup:
    """Hard-deletes only stale unmatched rows, never touches ledger."""

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.fetch_val", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_deletes_only_stale_unmatched(self, mock_execute, mock_fetch_val, mock_fetch_all):
        cutoff = datetime.utcnow() - timedelta(hours=24)
        mock_fetch_all.return_value = [{"id": 101}, {"id": 102}]
        mock_fetch_val.return_value = 1

        result = await _cleanup_stale_review_queue_rows()

        assert result["deleted_reviews"] == 2
        sqls = [c.args[0] for c in mock_execute.call_args_list]
        assert any("DELETE FROM fpe_unmatched_messages" in str(s) for s in sqls)
        assert any("DELETE FROM fpe_review_audit_logs" in str(s) for s in sqls)
        assert not any("fpe_cash_transactions" in str(s) for s in sqls)

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    async def test_noop_when_nothing_stale(self, mock_fetch_all):
        mock_fetch_all.return_value = []

        result = await _cleanup_stale_review_queue_rows()
        assert result["deleted_reviews"] == 0


# ── 90-day inactivity rule ────────────────────────────────────────────────────

class TestMarkStaleEmployeesInactive:
    """Only employees with no payments in 90+ days become inactive."""

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_marks_only_old_employees(self, mock_execute, mock_fetch_all):
        cutoff = date.today() - timedelta(days=90)
        mock_fetch_all.return_value = [
            {"id": 1, "last_payment_date": cutoff - timedelta(days=10)},
            {"id": 2, "last_payment_date": cutoff - timedelta(days=5)},
        ]

        count = await _mark_stale_employees_inactive()
        assert count == 2
        update_sql = mock_execute.call_args[0][0]
        assert "UPDATE fpe_employees SET status = 'inactive'" in update_sql

    @patch("modules.fazle_payroll_engine.routes.fetch_all", new_callable=AsyncMock)
    @patch("modules.fazle_payroll_engine.routes.execute", new_callable=AsyncMock)
    async def test_no_inactive_when_all_recently_paid(self, mock_execute, mock_fetch_all):
        cutoff = date.today() - timedelta(days=90)
        mock_fetch_all.return_value = []  # no rows match < cutoff

        count = await _mark_stale_employees_inactive()
        assert count == 0
        assert not mock_execute.called
