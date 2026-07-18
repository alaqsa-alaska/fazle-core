"""
Payroll advance-deduction cross-system employee-ID resolution tests.

Owner-authorized fix (2026-07-18): compute_run()'s advance-deduction query
filtered fpe_cash_transactions.employee_id directly against the WBOM
employee_id passed into compute_run() -- but that column FKs to
fpe_employees(id), a different ID space (confirmed via schema inspection:
fpe_cash_transactions_employee_id_fkey -> fpe_employees(id)). Real advances
were silently never deducted from payroll unless the two IDs coincidentally
matched. Fixed by resolving the linked, active fpe_employees row via
wbom_employee_id first -- the same pattern mark_paid()/_transition() already
uses elsewhere in modules/payroll/__init__.py.

See knowledge_base/00_governance/management_decisions.md for the full
production-impact audit and decision record.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def wbom_emp(test_db_pool):
    """A WBOM employee whose employee_id will NOT coincidentally match its
    linked fpe_employees.id -- a decoy row is inserted first to guarantee
    the sequences diverge, exactly like the real production data (confirmed
    empirically 2026-07-18: 15/15 sampled real advance-bearing employees had
    id != wbom_employee_id)."""
    async with test_db_pool.acquire() as conn:
        # No decoy needed on the wbom_employees side -- _link_fpe_employee()
        # inserts 2 fpe_employees decoys per call, which is enough on its
        # own to permanently desynchronize the two independent SERIAL
        # sequences from this point forward.
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801811111111', 'Karim Guard', 'Security Guard', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


async def _link_fpe_employee(test_db_pool, wbom_employee, code: str, status: str = "active") -> int:
    """Create a real, wbom_employee_id-linked fpe_employees row (with 2
    decoys inserted first, permanently desynchronizing the fpe_employees
    SERIAL sequence from wbom_employees') and return its id."""
    async with test_db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO fpe_employees (employee_code, full_name, name_normalized, status)
            VALUES ($1, 'Decoy Fpe 1', 'decoy fpe 1', 'inactive')
        """, f"{code}-DECOY1")
        await conn.execute("""
            INSERT INTO fpe_employees (employee_code, full_name, name_normalized, status)
            VALUES ($1, 'Decoy Fpe 2', 'decoy fpe 2', 'inactive')
        """, f"{code}-DECOY2")
        row = await conn.fetchrow("""
            INSERT INTO fpe_employees
                (employee_code, full_name, name_normalized, primary_phone, status, wbom_employee_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """, code, wbom_employee["employee_name"], wbom_employee["employee_name"].lower().strip(),
            wbom_employee["employee_mobile"], status, wbom_employee["employee_id"])
    return row["id"]


async def _insert_advance(test_db_pool, fpe_employee_id: int, amount: float, txn_date_: date,
                           txn_ref: str, status: str = "final", is_reversal: bool = False):
    async with test_db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO fpe_cash_transactions
                (txn_ref, employee_id, amount, txn_date, txn_category, transaction_status, is_reversal)
            VALUES ($1, $2, $3, $4, 'advance', $5, $6)
        """, txn_ref, fpe_employee_id, amount, txn_date_, status, is_reversal)


class TestAdvanceIdentityResolution:

    async def test_cross_system_id_mapping_correct_deduction(self, test_db_pool, wbom_emp):
        """THE key regression test: WBOM employee_id differs from FPE
        employee_id -- the correct advance must still be deducted."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X1")
        assert fpe_id != wbom_emp["employee_id"], "test invariant: IDs must differ"
        await _insert_advance(test_db_pool, fpe_id, 1000.0, date(2026, 5, 10), "adv-x1")

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(1000.0)
        assert result["net_salary"] < result["gross_salary"]

    async def test_coincidental_matching_ids_still_correct(self, test_db_pool):
        """WBOM id == FPE id by coincidence -- must not be the only case that
        works (regression guard against a fix that only 'accidentally'
        passes when the IDs line up)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        async with test_db_pool.acquire() as conn:
            wbom_row = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                VALUES ('8801822222222', 'Coincidence Guard', 'Security Guard', 'Active', 9000.00)
                RETURNING *
            """)
            fpe_row = await conn.fetchrow("""
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, primary_phone, status, wbom_employee_id)
                VALUES ('EMP-COINCIDE', $1, $2, $3, 'active', $4)
                RETURNING id
            """, wbom_row["employee_name"], wbom_row["employee_name"].lower().strip(),
                wbom_row["employee_mobile"], wbom_row["employee_id"])
        # Not asserting fpe_row["id"] == wbom_row["employee_id"] (can't force
        # it deterministically), but whatever the relationship is, the
        # correct amount must be deducted via the real link either way.
        await _insert_advance(test_db_pool, fpe_row["id"], 750.0, date(2026, 5, 12), "adv-coincide")

        result = await compute_run(
            employee_id=wbom_row["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(750.0)

    async def test_another_employees_advance_not_deducted(self, test_db_pool, wbom_emp):
        """A different FPE employee's transaction must never leak into this
        employee's payroll run."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X2")

        # A second, unrelated employee with their own advance
        async with test_db_pool.acquire() as conn:
            other_wbom = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                VALUES ('8801833333333', 'Other Guard', 'Security Guard', 'Active', 9000.00)
                RETURNING *
            """)
        other_fpe_id = await _link_fpe_employee(test_db_pool, dict(other_wbom), "EMP-X2-OTHER")
        await _insert_advance(test_db_pool, other_fpe_id, 5000.0, date(2026, 5, 10), "adv-other")

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(0.0)

    async def test_no_fpe_link_deduction_is_zero(self, test_db_pool, wbom_emp):
        """No linked fpe_employees row at all -- deduction must be exactly
        zero, and the run must still compute successfully (not fail closed)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["ok"] is True
        assert result["total_advances"] == pytest.approx(0.0)

    async def test_inactive_fpe_link_treated_as_no_link(self, test_db_pool, wbom_emp):
        """An fpe_employees row linked via wbom_employee_id but with
        status != 'active' must not be resolved (matches mark_paid()'s own
        `AND status='active'` gate elsewhere in this file)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X3", status="inactive")
        await _insert_advance(test_db_pool, fpe_id, 2000.0, date(2026, 5, 10), "adv-inactive")

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(0.0)

    async def test_multiple_advances_correct_sum(self, test_db_pool, wbom_emp):
        """Multiple valid advances in-period must sum correctly."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X4")
        await _insert_advance(test_db_pool, fpe_id, 500.0, date(2026, 5, 3), "adv-x4-a")
        await _insert_advance(test_db_pool, fpe_id, 300.0, date(2026, 5, 15), "adv-x4-b")
        await _insert_advance(test_db_pool, fpe_id, 200.0, date(2026, 5, 28), "adv-x4-c")

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(1000.0)

    async def test_status_filtering_only_final_counted(self, test_db_pool, wbom_emp):
        """pending / reversed transactions must not be counted -- only
        transaction_status='final'. Uses the actual production vocabulary
        (TxnCategory/transaction_status columns, no CHECK constraint --
        confirmed via CANONICAL_BUSINESS_RULES.md; values validated at the
        Python/Pydantic layer, not the DB)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X5")
        await _insert_advance(test_db_pool, fpe_id, 1000.0, date(2026, 5, 10), "adv-x5-final", status="final")
        await _insert_advance(test_db_pool, fpe_id, 9999.0, date(2026, 5, 11), "adv-x5-pending", status="pending")
        await _insert_advance(test_db_pool, fpe_id, 8888.0, date(2026, 5, 12), "adv-x5-reversed",
                               status="reversed", is_reversal=True)

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(1000.0)

    async def test_period_boundaries(self, test_db_pool, wbom_emp):
        """Advances strictly before or after the payroll period must be
        excluded; the period's first and last day are inclusive
        (txn_date BETWEEN start AND end)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X6")
        await _insert_advance(test_db_pool, fpe_id, 111.0, date(2026, 4, 30), "adv-x6-before")   # excluded
        await _insert_advance(test_db_pool, fpe_id, 222.0, date(2026, 5, 1), "adv-x6-start")     # included (boundary)
        await _insert_advance(test_db_pool, fpe_id, 333.0, date(2026, 5, 15), "adv-x6-mid")      # included
        await _insert_advance(test_db_pool, fpe_id, 444.0, date(2026, 5, 31), "adv-x6-end")      # included (boundary)
        await _insert_advance(test_db_pool, fpe_id, 555.0, date(2026, 6, 1), "adv-x6-after")     # excluded

        result = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert result["total_advances"] == pytest.approx(222.0 + 333.0 + 444.0)

    async def test_idempotent_no_duplicate_transactions(self, test_db_pool, wbom_emp):
        """Repeated compute_run() for the same employee/period must return
        the same deduction and must not create a duplicate run or duplicate
        fpe_cash_transactions rows (compute_run() only reads the ledger, it
        never writes to it -- this proves the read-path fix didn't change
        that)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.payroll import compute_run

        fpe_id = await _link_fpe_employee(test_db_pool, wbom_emp, "EMP-X7")
        await _insert_advance(test_db_pool, fpe_id, 1000.0, date(2026, 5, 10), "adv-x7")

        r1 = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        # Second call hits the idempotency early-return path
        # (_existing_active_run()), whose return shape doesn't include
        # total_advances -- net_salary is present in both shapes and is
        # sufficient to prove the second call didn't recompute/duplicate.
        r2 = await compute_run(
            employee_id=wbom_emp["employee_id"], period_year=2026, period_month=5,
            computed_by="test",
        )
        assert r1["run_id"] == r2["run_id"]
        assert r1["total_advances"] == pytest.approx(1000.0)
        assert r1["net_salary"] == r2["net_salary"]

        async with test_db_pool.acquire() as conn:
            run_count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_payroll_runs WHERE employee_id=$1 AND period_year=2026 AND period_month=5",
                wbom_emp["employee_id"],
            )
            txn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM fpe_cash_transactions WHERE employee_id=$1", fpe_id
            )
        assert run_count == 1
        assert txn_count == 1
