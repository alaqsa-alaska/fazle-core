"""DB consistency tests — constraints, FK integrity, invariants"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.db


class TestEmployeeTableConstraints:
    """wbom_employees constraints."""

    async def test_employee_mobile_unique(self, test_db_pool):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_employees (employee_mobile, employee_name)
                VALUES ('8801800000001', 'Guard A')
            """)
            with pytest.raises(Exception, match="unique|duplicate"):
                await conn.execute("""
                    INSERT INTO wbom_employees (employee_mobile, employee_name)
                    VALUES ('8801800000001', 'Guard B')
                """)

    async def test_employee_mobile_required(self, test_db_pool):
        async with test_db_pool.acquire() as conn:
            with pytest.raises(Exception):
                await conn.execute("""
                    INSERT INTO wbom_employees (employee_name)
                    VALUES ('Guard No Phone')
                """)

    async def test_basic_salary_default_zero(self, test_db_pool):
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name)
                VALUES ('8801800000002', 'Default Salary Guard')
                RETURNING basic_salary
            """)
        assert float(row["basic_salary"]) == 0.0


class TestAttendanceConstraints:
    """wbom_attendance unique constraint and FK."""

    async def test_unique_attendance_per_day(self, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_attendance (employee_id, attendance_date, status)
                VALUES ($1, '2026-05-01', 'Present')
            """, seed_employee["employee_id"])

            with pytest.raises(Exception, match="unique|duplicate"):
                await conn.execute("""
                    INSERT INTO wbom_attendance (employee_id, attendance_date, status)
                    VALUES ($1, '2026-05-01', 'Absent')
                """, seed_employee["employee_id"])

    async def test_attendance_cascade_delete_with_employee(self, test_db_pool, seed_employee):
        """Deleting employee cascades to attendance rows."""
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_attendance (employee_id, attendance_date, status)
                VALUES ($1, CURRENT_DATE, 'Present')
            """, seed_employee["employee_id"])

            await conn.execute(
                "DELETE FROM wbom_employees WHERE employee_id=$1",
                seed_employee["employee_id"],
            )

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert count == 0

    async def test_invalid_employee_fk_rejected(self, test_db_pool):
        async with test_db_pool.acquire() as conn:
            with pytest.raises(Exception, match="foreign key|violates"):
                await conn.execute("""
                    INSERT INTO wbom_attendance (employee_id, attendance_date, status)
                    VALUES (999999, CURRENT_DATE, 'Present')
                """)


class TestCashTransactionConstraints:
    """wbom_cash_transactions FK and reversal integrity."""

    async def test_transaction_requires_valid_employee(self, test_db_pool):
        async with test_db_pool.acquire() as conn:
            with pytest.raises(Exception, match="foreign key|violates"):
                await conn.execute("""
                    INSERT INTO wbom_cash_transactions
                        (employee_id, transaction_type, amount)
                    VALUES (999999, 'advance', 1000)
                """)

    async def test_reversal_self_reference_integrity(
        self, test_db_pool, seed_employee
    ):
        async with test_db_pool.acquire() as conn:
            original = await conn.fetchrow("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount)
                VALUES ($1, 'advance', 1000)
                RETURNING transaction_id
            """, seed_employee["employee_id"])

            # Reversal should reference original
            rev = await conn.fetchrow("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount,
                     reversal_of)
                VALUES ($1, 'advance', -1000, $2)
                RETURNING reversal_of
            """, seed_employee["employee_id"], original["transaction_id"])

        assert rev["reversal_of"] == original["transaction_id"]

    async def test_transaction_cascade_delete(self, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount)
                VALUES ($1, 'advance', 500)
            """, seed_employee["employee_id"])

            await conn.execute(
                "DELETE FROM wbom_employees WHERE employee_id=$1",
                seed_employee["employee_id"],
            )

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_cash_transactions WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert count == 0


class TestPaymentDraftConstraints:
    async def test_draft_default_status_pending(self, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO fazle_payment_drafts
                    (draft_type, employee_id, employee_name, employee_mobile,
                     expected_amount)
                VALUES ('advance', $1, 'Guard', '8801811111111', 1000)
                RETURNING status
            """, seed_employee["employee_id"])
        assert row["status"] == "pending"


class TestPayrollRunConstraints:
    async def test_payroll_run_default_status_draft(self, test_db_pool, seed_employee):
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_payroll_runs
                    (employee_id, period_year, period_month,
                     basic_salary, gross_salary, net_salary)
                VALUES ($1, 2026, 5, 9000, 9000, 9000)
                RETURNING status
            """, seed_employee["employee_id"])
        assert row["status"] == "draft"


class TestDbInvariants:
    """Cross-table business rule invariants."""

    async def test_net_salary_non_negative_in_all_runs(self, test_db_pool):
        """All payroll run rows should have net_salary >= 0."""
        async with test_db_pool.acquire() as conn:
            neg = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_payroll_runs WHERE net_salary < 0"
            )
        assert neg == 0

    async def test_completed_programs_have_day_count(self, test_db_pool, seed_escort_program):
        """All Completed escort programs should have day_count > 0."""
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET status='Completed', day_count=5
                WHERE program_id=$1
            """, seed_escort_program["program_id"])

            bad = await conn.fetchval("""
                SELECT COUNT(*) FROM wbom_escort_programs
                WHERE status='Completed' AND (day_count IS NULL OR day_count <= 0)
            """)
        assert bad == 0

    async def test_reversal_chain_consistent(self, test_db_pool, seed_employee):
        """Every reversal transaction must reference a valid original transaction."""
        async with test_db_pool.acquire() as conn:
            original = await conn.fetchrow("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount)
                VALUES ($1, 'advance', 1000)
                RETURNING transaction_id
            """, seed_employee["employee_id"])

            await conn.execute("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount, reversal_of)
                VALUES ($1, 'advance', -1000, $2)
            """, seed_employee["employee_id"], original["transaction_id"])

            # Query: all reversal rows should have a valid reversal_of
            orphans = await conn.fetchval("""
                SELECT COUNT(*) FROM wbom_cash_transactions r
                WHERE r.reversal_of IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM wbom_cash_transactions o
                    WHERE o.transaction_id = r.reversal_of
                )
            """)
        assert orphans == 0

    async def test_escort_program_employee_fk_consistent(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        """escort_employee_id must reference a real employee."""
        async with test_db_pool.acquire() as conn:
            broken = await conn.fetchval("""
                SELECT COUNT(*) FROM wbom_escort_programs p
                WHERE p.escort_employee_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1 FROM wbom_employees e
                    WHERE e.employee_id = p.escort_employee_id
                )
            """)
        assert broken == 0
