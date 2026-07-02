"""Unit tests — modules/payroll"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from decimal import Decimal

pytestmark = pytest.mark.unit


class TestPayrollCompute:
    """Test monthly payroll computation."""

    async def test_basic_computation(self, test_db_pool, seed_employee, seed_escort_program):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import compute_run

        employee_id = seed_employee["employee_id"]
        program_id = seed_escort_program["program_id"]

        # Close program with 5 days
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET status='Completed', day_count=5,
                    program_date='2026-05-01', end_date='2026-05-05'
                WHERE program_id=$1
            """, program_id)

        result = await compute_run(
            employee_id=employee_id,
            period_year=2026, period_month=5,
            computed_by="test-admin",
        )

        assert result is not None
        # basic_salary=9000, program_allowance=5×400=2000, gross=11000, net=11000
        assert result.get("gross_salary") == pytest.approx(11000.0, rel=0.01)

    async def test_advances_deducted_from_net(
        self, test_db_pool, seed_employee, seed_fpe_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import compute_run

        employee_id = seed_employee["employee_id"]
        fpe_employee_id = seed_fpe_employee["id"]
        program_id = seed_escort_program["program_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET status='Completed', day_count=3,
                    program_date='2026-05-01', end_date='2026-05-03'
                WHERE program_id=$1
            """, program_id)
            # Insert 2 advances into canonical FPE table
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, txn_category, amount, payout_method,
                     txn_date, transaction_status, source, source_channel)
                VALUES
                    ($1, $2, 'advance', 1000.00, 'cash', '2026-05-02', 'final', 'test', 'test'),
                    ($3, $2, 'advance', 500.00, 'cash', '2026-05-04', 'final', 'test', 'test')
            """, f"test-adv-1-{employee_id}", fpe_employee_id, f"test-adv-2-{employee_id}")

        result = await compute_run(
            employee_id=employee_id,
            period_year=2026, period_month=5,
            computed_by="test-admin",
        )

        # net = gross(9000+1200) - advances(1500) = 8700
        assert result["total_advances"] == pytest.approx(1500.0, rel=0.01)
        assert result["net_salary"] == pytest.approx(8700.0, rel=0.01)

    async def test_net_salary_never_negative(
        self, test_db_pool, seed_employee, seed_fpe_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import compute_run

        employee_id = seed_employee["employee_id"]
        fpe_employee_id = seed_fpe_employee["id"]

        async with test_db_pool.acquire() as conn:
            # Large advance exceeding salary
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, txn_category, amount, payout_method,
                     txn_date, transaction_status, source, source_channel)
                VALUES ($1, $2, 'advance', 50000.00, 'cash', '2026-05-01', 'final', 'test', 'test')
            """, f"test-adv-big-{employee_id}", fpe_employee_id)

        result = await compute_run(
            employee_id=employee_id,
            period_year=2026, period_month=5,
            computed_by="test-admin",
        )

        assert result["net_salary"] >= 0.0

    async def test_idempotent_compute(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import compute_run

        employee_id = seed_employee["employee_id"]

        result1 = await compute_run(
            employee_id=employee_id, period_year=2026, period_month=5, computed_by="admin"
        )
        result2 = await compute_run(
            employee_id=employee_id, period_year=2026, period_month=5, computed_by="admin"
        )

        # Second run should return existing or recompute — not crash
        assert result1 is not None
        assert result2 is not None


class TestPayrollStateMachine:
    """Test payroll status transitions."""

    async def _create_run(self, test_db_pool, seed_employee) -> int:
        import app.database as db_module
        db_module._pool = test_db_pool

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_payroll_runs
                    (employee_id, period_year, period_month, status, basic_salary,
                     gross_salary, net_salary, total_programs)
                VALUES ($1, 2026, 5, 'draft', 9000, 9000, 9000, 0)
                RETURNING run_id
            """, seed_employee["employee_id"])
        return row["run_id"]

    async def test_submit_transitions_to_reviewed(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import submit_run

        run_id = await self._create_run(test_db_pool, seed_employee)
        await submit_run(run_id=run_id, actor="admin")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        assert row["status"] == "reviewed"

    async def test_invalid_transition_raises(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import approve_run

        run_id = await self._create_run(test_db_pool, seed_employee)
        # Can't approve from 'draft' directly (must be reviewed first)
        result = await approve_run(run_id=run_id, actor="admin")
        assert result.get("ok") is False or result.get("error")
