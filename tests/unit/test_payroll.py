"""Unit tests — modules/payroll"""
from __future__ import annotations

import pytest
from datetime import date
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
                SET status='completed', day_count=5,
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
                SET status='completed', day_count=3,
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


class TestProratedBasicSalary:
    """
    2026-07-15 fix: compute_run()'s basic-salary component was always the
    full flat rate, never prorated by attendance/calendar-days -- the target
    formula (basic_salary / calendar_days_in_month * approved_duty_days,
    docs/NEXT_IMPLEMENTATION_QUEUE.md) existed only in the read-only
    modules.payroll.dry_run preview, never in compute_run() itself.
    prorate_basic=True wires it in for the office-staff-only path
    (compute_all_for_period_office_only); default False is unchanged.
    """

    async def _make_office_employee(self, pool, name="Office ProrateTest", salary=9300):
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO wbom_employees
                       (employee_name, employee_mobile, designation, status, joining_date, basic_salary)
                   VALUES ($1, $2, 'Staff', 'Active', CURRENT_DATE, $3)
                   RETURNING *""",
                name, f"01{hash(name) % 900000000 + 100000000}", salary,
            )
        return dict(row)

    async def test_prorate_basic_false_is_unchanged(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        """Regression guard: the currently-live escort-inclusive path must
        not change when prorate_basic isn't passed at all."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import compute_run

        program_id = seed_escort_program["program_id"]
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET status='completed', day_count=5,
                    program_date='2026-05-01', end_date='2026-05-05'
                WHERE program_id=$1
            """, program_id)

        result = await compute_run(
            employee_id=seed_employee["employee_id"],
            period_year=2026, period_month=5, computed_by="test-admin",
        )
        # Same numbers as test_basic_computation: full flat basic_salary(9000)
        # + program_allowance(5x400=2000).
        assert result["basic_salary"] == pytest.approx(9000.0)
        assert result["gross_salary"] == pytest.approx(11000.0, rel=0.01)
        assert result["duty_days"] is None

    async def test_prorate_basic_true_uses_calendar_attendance_formula(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance.crud import create_manual_attendance
        from modules.payroll import compute_run

        emp = await self._make_office_employee(test_db_pool, salary=9300)  # 9300/31 = 300/day
        # 10 present days in May 2026 (31-day month)
        for day in range(1, 11):
            await create_manual_attendance(
                emp["employee_id"], date(2026, 5, day), "Present", "", "admin1",
            )

        result = await compute_run(
            employee_id=emp["employee_id"],
            period_year=2026, period_month=5, computed_by="test-admin",
            prorate_basic=True,
        )

        # basic_salary/calendar_days * duty_days = 9300/31 * 10 = 3000.00
        assert result["duty_days"] == pytest.approx(10.0)
        assert result["basic_salary"] == pytest.approx(3000.0)
        assert result["gross_salary"] == pytest.approx(3000.0)  # no escort programs
        assert result["net_salary"] == pytest.approx(3000.0)

        async with test_db_pool.acquire() as conn:
            item = await conn.fetchrow(
                "SELECT amount, notes FROM wbom_payroll_run_items "
                "WHERE run_id=$1 AND component_type='basic'",
                result["run_id"],
            )
        assert float(item["amount"]) == pytest.approx(3000.0)
        assert "prorated" in item["notes"]

    async def test_office_only_path_prorates_eligible_employees(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance.crud import create_manual_attendance
        from modules.payroll import compute_all_for_period_office_only

        emp = await self._make_office_employee(test_db_pool, name="Office AutoPath", salary=6200)  # 6200/31=200/day
        for day in range(1, 6):
            await create_manual_attendance(
                emp["employee_id"], date(2026, 6, day), "Present", "", "admin1",
            )

        result = await compute_all_for_period_office_only(2026, 6, "test-admin")
        assert result["created"] >= 1

        async with test_db_pool.acquire() as conn:
            run = await conn.fetchrow(
                "SELECT basic_salary FROM wbom_payroll_runs "
                "WHERE employee_id=$1 AND period_year=2026 AND period_month=6",
                emp["employee_id"],
            )
        # 6200/30 (June has 30 days) * 5 duty days = 1033.33
        assert float(run["basic_salary"]) == pytest.approx(round(6200 / 30 * 5, 2))


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


class TestMarkPaidLedger:
    """
    2026-07-15 fix: mark_paid() previously only flipped wbom_payroll_runs.status
    to 'paid' and wrote an audit-log row -- it never posted to the canonical
    fpe_cash_transactions ledger (the ONLY canonical cash ledger per
    knowledge_base/06_developer_system/daily_cash_transaction_source_of_truth.md).
    """

    async def _create_locked_run(self, test_db_pool, seed_employee, amount=9000.0) -> int:
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_payroll_runs
                    (employee_id, period_year, period_month, status, basic_salary,
                     gross_salary, net_salary, total_programs)
                VALUES ($1, 2026, 6, 'locked', $2, $2, $2, 0)
                RETURNING run_id
            """, seed_employee["employee_id"], amount)
        return row["run_id"]

    async def _link_fpe_employee(self, test_db_pool, seed_employee, code: str) -> int:
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, primary_phone,
                     status, wbom_employee_id)
                VALUES ($1, $2, $3, $4, 'active', $5)
                RETURNING id
            """, code, seed_employee["employee_name"],
                seed_employee["employee_name"].lower().strip(),
                seed_employee["employee_mobile"], seed_employee["employee_id"])
        return row["id"]

    async def test_mark_paid_posts_canonical_ledger_entry(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import mark_paid

        fpe_id = await self._link_fpe_employee(test_db_pool, seed_employee, "EMP-MARKPAID1")
        run_id = await self._create_locked_run(test_db_pool, seed_employee)

        result = await mark_paid(
            run_id=run_id, actor="admin", amount=9000.0,
            method="bkash", reference="txn-ref-001",
        )
        assert result["ok"] is True

        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM fpe_cash_transactions "
                "WHERE employee_id=$1 AND txn_category='salary'",
                fpe_id,
            )
        assert txn is not None
        assert txn["transaction_status"] == "final"
        assert txn["payout_method"] == "bkash"
        assert float(txn["amount"]) == pytest.approx(9000.0)

    async def test_mark_paid_fails_closed_without_fpe_link(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import mark_paid

        # Deliberately no fpe_employees row links to this WBOM employee.
        run_id = await self._create_locked_run(test_db_pool, seed_employee)

        result = await mark_paid(run_id=run_id, actor="admin", amount=9000.0, method="bkash")
        assert result["ok"] is False

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        # Run must stay 'locked' -- never silently marked paid with no ledger entry.
        assert row["status"] == "locked"

    async def test_double_mark_paid_does_not_double_post(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import mark_paid

        fpe_id = await self._link_fpe_employee(test_db_pool, seed_employee, "EMP-MARKPAID2")
        run_id = await self._create_locked_run(test_db_pool, seed_employee)

        r1 = await mark_paid(run_id=run_id, actor="admin", amount=9000.0, method="bkash")
        assert r1["ok"] is True
        r2 = await mark_paid(run_id=run_id, actor="admin", amount=9000.0, method="bkash")
        assert r2["ok"] is False  # state machine blocks re-transition from 'paid'

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM fpe_cash_transactions "
                "WHERE employee_id=$1 AND txn_category='salary'",
                fpe_id,
            )
        assert count == 1
