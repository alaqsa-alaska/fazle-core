"""
Phase 2A -- cross-system employee-ID resolution tests for the sibling
functions found to share compute_run()'s advance-deduction ID-space bug
(fixed 2026-07-18): create_escort_payment_draft() and
create_advance_request_draft() (modules/payment_workflow/__init__.py),
get_payroll_summary() (modules/payroll_logic/__init__.py), and
get_employee() (modules/admin_employees/__init__.py).

All four resolve fpe_cash_transactions.employee_id (FK -> fpe_employees(id))
via the linked, active fpe_employees row (WHERE wbom_employee_id=$1 AND
status='active'), matching the pattern already certified in
modules/payroll/__init__.py::compute_run(). See management_decisions.md
"Phase 2A -- Complete Remaining Money Correctness" for the full audit.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def wbom_emp(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801811111111', 'Karim Guard', 'Security Guard', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


async def _link_fpe_employee(test_db_pool, wbom_employee, code: str, status: str = "active") -> int:
    """Real, wbom_employee_id-linked fpe_employees row, with 2 decoys first
    to permanently desynchronize the two independent SERIAL sequences."""
    async with test_db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO fpe_employees (employee_code, full_name, name_normalized, status)
            VALUES ($1, 'Decoy 1', 'decoy 1', 'inactive')
        """, f"{code}-D1")
        await conn.execute("""
            INSERT INTO fpe_employees (employee_code, full_name, name_normalized, status)
            VALUES ($1, 'Decoy 2', 'decoy 2', 'inactive')
        """, f"{code}-D2")
        row = await conn.fetchrow("""
            INSERT INTO fpe_employees
                (employee_code, full_name, name_normalized, primary_phone, status, wbom_employee_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, code, wbom_employee["employee_name"], wbom_employee["employee_name"].lower().strip(),
            wbom_employee["employee_mobile"], status, wbom_employee["employee_id"])
    return row["id"]


async def _insert_program(test_db_pool, wbom_employee, **overrides):
    defaults = dict(
        mother_vessel="MV TEST", lighter_vessel="LT TEST", master_mobile="8801933333333",
        program_date=date(2026, 5, 1), shift="D", status="completed",
        end_date=date(2026, 5, 5), day_count=5.0, food_bill=0, conveyance=0,
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, escort_employee_id, escort_mobile,
                   program_date, shift, status, start_date, end_date, day_count, food_bill, conveyance)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$6,$9,$10,$11,$12)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            wbom_employee["employee_id"], wbom_employee["employee_mobile"],
            defaults["program_date"], defaults["shift"], defaults["status"],
            defaults["end_date"], defaults["day_count"], defaults["food_bill"], defaults["conveyance"],
        )
    return dict(row)


class TestCreateEscortPaymentDraftIdentity:

    async def test_cross_system_advance_deducted(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.payment_workflow import create_escort_payment_draft

        program = await _insert_program(test_db_pool, wbom_emp)
        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-D1")
        assert fpe_id != wbom_emp["employee_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, program_id, txn_category, amount,
                     txn_date, transaction_status, source, source_channel)
                VALUES ('adv-d1', $1, $2, 'advance', 500, CURRENT_DATE, 'final', 'test', 'test')
            """, fpe_id, program["program_id"])

        result = await create_escort_payment_draft(
            employee_id=wbom_emp["employee_id"], escort_program_id=program["program_id"],
            override_days=5.0,
        )
        # Formula 1: 5*400=2000 gross - advance 500 = 1500
        assert result["expected_amount"] == pytest.approx(1500.0)

    async def test_no_fpe_link_no_deduction_draft_still_created(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.payment_workflow import create_escort_payment_draft

        program = await _insert_program(test_db_pool, wbom_emp)
        result = await create_escort_payment_draft(
            employee_id=wbom_emp["employee_id"], escort_program_id=program["program_id"],
            override_days=5.0,
        )
        assert result["draft_id"]
        assert result["expected_amount"] == pytest.approx(2000.0)  # no advance to deduct


class TestCreateAdvanceRequestDraftIdentity:

    async def test_paid_this_month_resolves_cross_system(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.payment_workflow import create_advance_request_draft

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-D2")
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, txn_category, amount,
                     txn_date, transaction_status, source, source_channel)
                VALUES ('paid-d2', $1, 'salary', 4000, CURRENT_DATE, 'final', 'test', 'test')
            """, fpe_id)

        result = await create_advance_request_draft(
            employee_id=wbom_emp["employee_id"], requested_amount=1000.0,
        )
        assert "৳4,000" in result["draft_text"]


class TestGetPayrollSummaryIdentity:

    async def test_summary_resolves_cross_system(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll_logic import get_payroll_summary

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-D3")
        month_start = date.today().replace(day=1)
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, txn_category, amount,
                     txn_date, transaction_status, source, source_channel)
                VALUES ('sal-d3', $1, 'salary', 3000, $2, 'final', 'test', 'test')
            """, fpe_id, month_start)

        summary = await get_payroll_summary(wbom_emp["employee_id"])
        assert summary["paid_this_month"] == pytest.approx(3000.0)
        assert summary["total_paid_ever"] == pytest.approx(3000.0)
        assert summary["last_payment_amount"] == pytest.approx(3000.0)

    async def test_no_fpe_link_under_review(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll_logic import get_payroll_summary

        summary = await get_payroll_summary(wbom_emp["employee_id"])
        assert summary["paid_this_month"] == pytest.approx(0.0)
        assert summary["total_paid_ever"] == pytest.approx(0.0)
        assert summary["last_payment_amount"] is None


class TestAdminGetEmployeeIdentity:

    async def test_txn_count_resolves_cross_system(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.admin_employees import get_employee

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-D4")
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, txn_category, amount,
                     txn_date, transaction_status, source, source_channel)
                VALUES ('c1', $1, 'salary', 100, CURRENT_DATE, 'final', 'test', 'test'),
                       ('c2', $1, 'advance', 50, CURRENT_DATE, 'final', 'test', 'test')
            """, fpe_id)

        result = await get_employee(wbom_emp["employee_id"])
        assert result["related_counts"]["txn_count"] == 2

    async def test_no_fpe_link_zero_txn_count(self, test_db_pool, wbom_emp):
        import app.database as db
        db._pool = test_db_pool
        from modules.admin_employees import get_employee

        result = await get_employee(wbom_emp["employee_id"])
        assert result["related_counts"]["txn_count"] == 0
