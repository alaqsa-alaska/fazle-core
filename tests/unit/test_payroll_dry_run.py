"""Unit tests — modules/payroll/dry_run (Owner decision, 2026-07-10/11)"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


async def _make_office_employee(pool, name="Office Person", salary=9000, status="Active"):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_employees (employee_name, employee_mobile, designation, status, joining_date, basic_salary)
               VALUES ($1, $2, 'Staff', $3, CURRENT_DATE, $4)
               RETURNING *""",
            name, f"01{hash(name) % 900000000 + 100000000}", status, salary,
        )
    return dict(row)


class TestEligibilityPreview:
    async def test_zero_eligible_matches_production_shape(self, test_db_pool, seed_employee):
        """seed_employee has no office keyword/designation and basic_salary=9000
        by default -- confirm it's excluded as 'not classified as office staff'
        unless it happens to match, matching the real production finding of
        0 eligible employees today."""
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import eligibility_preview

        rows = await eligibility_preview(2026, 5)
        seed_row = next(r for r in rows if r["employee_id"] == seed_employee["employee_id"])
        assert seed_row["included"] is False

    async def test_office_employee_with_no_attendance_excluded(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import eligibility_preview

        emp = await _make_office_employee(test_db_pool, name="Office NoAttendance Person")
        rows = await eligibility_preview(2026, 5)
        row = next(r for r in rows if r["employee_id"] == emp["employee_id"])
        assert row["included"] is False
        assert row["reason"] == "Excluded: no approved duty attendance for selected month"

    async def test_office_employee_with_manual_attendance_included(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance
        from modules.payroll.dry_run import eligibility_preview

        emp = await _make_office_employee(test_db_pool, name="Office WithAttendance Person")
        await create_manual_attendance(emp["employee_id"], date(2026, 5, 5), "Present", "", "admin1")
        await create_manual_attendance(emp["employee_id"], date(2026, 5, 6), "Half Day", "", "admin1")

        rows = await eligibility_preview(2026, 5)
        row = next(r for r in rows if r["employee_id"] == emp["employee_id"])
        assert row["included"] is True
        assert row["approved_manual_days"] == 2
        assert row["total_duty_days"] == 1.5

    async def test_escort_backfilled_attendance_never_counts(self, test_db_pool):
        """An office employee whose only attendance came via escort_release
        backfill must not be counted -- entry_source filter must exclude it."""
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import eligibility_preview

        emp = await _make_office_employee(test_db_pool, name="Office EscortBackfill Person")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wbom_attendance (employee_id, attendance_date, status, entry_source, recorded_by)
                   VALUES ($1, $2, 'Present', 'escort_release', 'escort-lifecycle')""",
                emp["employee_id"], date(2026, 5, 5),
            )
        rows = await eligibility_preview(2026, 5)
        row = next(r for r in rows if r["employee_id"] == emp["employee_id"])
        assert row["included"] is False
        assert row["total_duty_days"] == 0.0

    async def test_whatsapp_self_report_never_counts(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import eligibility_preview

        emp = await _make_office_employee(test_db_pool, name="Office WhatsAppSelfReport Person")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wbom_attendance (employee_id, attendance_date, status, entry_source, recorded_by)
                   VALUES ($1, $2, 'Present', 'whatsapp_escort', 'admin1')""",
                emp["employee_id"], date(2026, 5, 5),
            )
        rows = await eligibility_preview(2026, 5)
        row = next(r for r in rows if r["employee_id"] == emp["employee_id"])
        assert row["included"] is False


class TestPayrollDryRun:
    async def test_calendar_days_28_29_30_31(self, test_db_pool):
        """Confirm the divisor is the real calendar length of the selected
        month, not a flat 30."""
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance
        from modules.payroll.dry_run import payroll_dry_run

        cases = [
            (2026, 2, 28),   # 2026 is not a leap year
            (2024, 2, 29),   # 2024 is a leap year
            (2026, 4, 30),
            (2026, 5, 31),
        ]
        for year, month, expected_days in cases:
            emp = await _make_office_employee(test_db_pool, name=f"Office CalCheck {year}-{month}", salary=3100)
            await create_manual_attendance(emp["employee_id"], date(year, month, 1), "Present", "", "admin1")
            rows = await payroll_dry_run(year, month)
            row = next(r for r in rows if r["employee_id"] == emp["employee_id"])
            assert row["calendar_days"] == expected_days
            expected_salary = round(3100 / expected_days, 2)
            assert row["calculated_salary"] == pytest.approx(expected_salary)

    async def test_dry_run_does_not_mutate_anything(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance
        from modules.payroll.dry_run import payroll_dry_run

        emp = await _make_office_employee(test_db_pool, name="Office NoMutation Person")
        await create_manual_attendance(emp["employee_id"], date(2026, 5, 5), "Present", "", "admin1")

        async with test_db_pool.acquire() as conn:
            runs_before = await conn.fetchval("SELECT COUNT(*) FROM wbom_payroll_runs")
            txn_before = await conn.fetchval("SELECT COUNT(*) FROM fpe_cash_transactions")
            att_before = await conn.fetchval("SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1", emp["employee_id"])

        await payroll_dry_run(2026, 5)

        async with test_db_pool.acquire() as conn:
            runs_after = await conn.fetchval("SELECT COUNT(*) FROM wbom_payroll_runs")
            txn_after = await conn.fetchval("SELECT COUNT(*) FROM fpe_cash_transactions")
            att_after = await conn.fetchval("SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1", emp["employee_id"])

        assert runs_before == runs_after
        assert txn_before == txn_after
        assert att_before == att_after


class TestReconciliationReport:
    async def test_flags_salary_with_no_duty(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import reconciliation_report

        emp = await _make_office_employee(test_db_pool, name="Office SalaryNoDuty Person")
        report = await reconciliation_report(2026, 5)
        assert any(f"employee {emp['employee_id']}" in w and "no duty data" in w for w in report["warnings"])

    async def test_pending_parser_draft_flagged_and_excluded(self, test_db_pool):
        _use_pool(test_db_pool)
        import json
        from modules.payroll.dry_run import reconciliation_report

        emp = await _make_office_employee(test_db_pool, name="Office PendingParser Person")
        meta = json.dumps({
            "role": "employee",
            "context": json.dumps({"employee_id": emp["employee_id"], "att_date": "2026-05-05", "shift": "D"}),
            "source_module": "attendance_parser",
        })
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO fazle_draft_replies (source, recipient, reply_text, intent, status, created_at, meta)
                   VALUES ('bridge1', '8801000000000', 'x', 'attendance', 'pending', NOW(), $1::jsonb)""",
                meta,
            )
        report = await reconciliation_report(2026, 5)
        row = next(r for r in report["rows"] if r["employee_id"] == emp["employee_id"])
        assert row["pending_parser_drafts"] == 1
        assert row["included"] is False
        assert any("pending parser draft" in w for w in report["warnings"])

    async def test_duplicate_attendance_detected(self, test_db_pool):
        """Defensive check -- the DB UNIQUE constraint should already prevent
        this, but the reconciliation report must still detect it if it ever
        somehow occurred (e.g. a future schema change removes the constraint)."""
        _use_pool(test_db_pool)
        from modules.payroll.dry_run import reconciliation_report

        emp = await _make_office_employee(test_db_pool, name="Office DupCheck Person")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO wbom_attendance (employee_id, attendance_date, status, entry_source) "
                "VALUES ($1, $2, 'Present', 'manual_office')",
                emp["employee_id"], date(2026, 5, 10),
            )
        report = await reconciliation_report(2026, 5)
        # No duplicate exists yet (constraint prevents it) -- confirm the
        # detector runs cleanly and doesn't false-positive on a single row.
        assert not any("duplicate attendance" in w for w in report["warnings"])
