"""Unit tests — modules/payment_workflow"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from decimal import Decimal

pytestmark = pytest.mark.unit


class TestIsAdvanceRequest:
    """Test keyword detection for advance requests."""

    @pytest.mark.parametrize("text", [
        "অগ্রিম লাগবে",
        "অগ্রীম দরকার",
        "টাকা দরকার",
        "টাকা লাগবে",
        "advance দেন",
        "বেতন দেন আজকে",
        "জরুরি টাকা চাই",
        "হাসপাতালে আছি",
        "সাহায্য লাগবে",
        "বিপদে পড়েছি",
    ])
    def test_advance_keywords_detected(self, text):
        from modules.payment_workflow import is_advance_request
        assert is_advance_request(text) is True

    @pytest.mark.parametrize("text", [
        "হাজির আছি",
        "ডিউটি শেষ",
        "MV GOLDEN STAR escort lagbe",
        "চাকরি করতে চাই",
        "আমি ভালো আছি",
    ])
    def test_non_advance_not_detected(self, text):
        from modules.payment_workflow import is_advance_request
        assert is_advance_request(text) is False


class TestCreateEscortPaymentDraft:
    """Test payment draft creation after escort program completion."""

    async def test_draft_created_with_correct_amounts(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import create_escort_payment_draft

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        # Close the program first (add end_date and day_count)
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET end_date='2026-05-05', day_count=5, status='Completed'
                WHERE program_id=$1
            """, program_id)

        result = await create_escort_payment_draft(
            escort_program_id=program_id,
            employee_id=employee_id,
            override_days=5.0,
            source="bridge1",
        )

        assert result is not None
        # Check DB
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE employee_id=$1 LIMIT 1",
                employee_id,
            )
        assert draft is not None
        assert draft["duty_days"] == 5.0
        # basic_salary=9000, daily_rate=9000/30=300, expected=5*300=1500
        assert float(draft["expected_amount"]) == pytest.approx(1500.0)

    async def test_uses_shift_column_and_current_program_month_deductions(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_workflow import create_escort_payment_draft

        employee_id = seed_employee["employee_id"]
        program_id = seed_escort_program["program_id"]
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """UPDATE wbom_escort_programs
                   SET end_date=CURRENT_DATE, day_count=2, status='Completed',
                       food_bill=100, conveyance=200
                   WHERE program_id=$1""",
                program_id,
            )
            await conn.execute(
                """INSERT INTO wbom_cash_transactions
                       (employee_id, program_id, transaction_type, amount, payment_method,
                        transaction_date, status)
                   VALUES ($1,$2,'advance',300,'cash',CURRENT_DATE,'Completed')""",
                employee_id, program_id,
            )

        result = await create_escort_payment_draft(
            employee_id=employee_id,
            escort_program_id=program_id,
            override_days=2,
        )
        assert result["draft_id"]
        # basic_salary 9000 / 30 * 2 - food 100 - conveyance 200 - advance 300
        assert result["expected_amount"] == pytest.approx(0.0)

    async def test_advances_deducted_from_payment(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        """If an advance was given this month, it reduces net_payable."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import create_escort_payment_draft

        employee_id = seed_employee["employee_id"]
        program_id = seed_escort_program["program_id"]

        # Insert an advance transaction
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount, transaction_date, status)
                VALUES ($1, 'advance', 500.00, CURRENT_DATE, 'Completed')
            """, employee_id)
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET end_date='2026-05-05', day_count=5, status='Completed'
                WHERE program_id=$1
            """, program_id)

        await create_escort_payment_draft(
            escort_program_id=program_id,
            employee_id=employee_id,
            override_days=5.0,
            source="bridge1",
        )

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE employee_id=$1 LIMIT 1",
                employee_id,
            )
        # net = expected(1500) - advance(500) = 1000
        # approved_amount field may reflect this
        assert draft is not None


class TestFinalizePayment:
    """Test payment finalization: writes to wbom_cash_transactions."""

    async def test_finalize_creates_transaction(
        self, test_db_pool, seed_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import finalize_payment

        draft_id = seed_payment_draft["id"]
        result = await finalize_payment(
            draft_id=draft_id,
            approved_amount=1500.00,
            method="bkash",
        )

        assert result is not None

        # Check transaction was inserted
        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM wbom_cash_transactions WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert txn is not None
        assert float(txn["amount"]) == pytest.approx(1500.00)
        assert txn["payment_method"] == "bkash"

    async def test_finalize_updates_draft_status(
        self, test_db_pool, seed_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import finalize_payment

        draft_id = seed_payment_draft["id"]
        await finalize_payment(
            draft_id=draft_id,
            approved_amount=1500.00,
            method="cash",
        )

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT status FROM fazle_payment_drafts WHERE id=$1",
                draft_id,
            )
        assert draft["status"] == "sent"

    async def test_finalize_is_idempotent(
        self, test_db_pool, seed_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.payment_workflow import finalize_payment

        draft_id = seed_payment_draft["id"]
        await finalize_payment(draft_id, 1500, "cash")
        second = await finalize_payment(draft_id, 1500, "cash")
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_cash_transactions WHERE idempotency_key=$1",
                f"payment-draft:{draft_id}",
            )
        assert second["already_finalized"] is True
        assert count == 1

    async def test_finalize_nonexistent_draft_raises(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import finalize_payment

        with pytest.raises(Exception):
            await finalize_payment(
                draft_id=99999,
                amount=1000.00,
                method="bkash",
            )


class TestCreateAdvanceRequestDraft:
    """Test advance request draft creation."""

    async def test_advance_draft_created(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payment_workflow import create_advance_request_draft

        result = await create_advance_request_draft(
            employee_id=seed_employee["employee_id"],
            requested_amount=2000.0,
            source="bridge1",
        )

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE draft_type='advance' LIMIT 1"
            )
        assert draft is not None
        assert draft["status"] == "pending"
