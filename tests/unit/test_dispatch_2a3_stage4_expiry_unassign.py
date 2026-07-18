"""
Dispatch Phase 2A.3 Stage 4 -- tightened incomplete-draft expiry and safe
unassignment.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


async def _insert_draft_program(test_db_pool, **overrides):
    """Insert directly into both tables (mirroring what sync_program_to_roster
    would produce) so expire_stale_drafts()'s query against
    escort_roster_entries has something to find. created_at is settable
    directly for TTL-boundary testing."""
    defaults = dict(
        mother_vessel="MV STAGE4 TEST", lighter_vessel="LT STAGE4 TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="draft",
        escort_name="", escort_mobile="", created_at=datetime.now(timezone.utc) - timedelta(hours=25),
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        prog = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status,
                   escort_name, escort_mobile)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["program_date"], defaults["shift"], defaults["status"],
            defaults["escort_name"], defaults["escort_mobile"],
        )
        await conn.execute(
            """INSERT INTO escort_roster_entries
                  (program_id, mother_vessel, lighter_vessel, master_mobile, escort_name, escort_mobile,
                   start_date, start_shift, roster_status, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'draft',$9)""",
            prog["program_id"], defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["escort_name"], defaults["escort_mobile"], defaults["program_date"], defaults["shift"],
            defaults["created_at"],
        )
    return dict(prog)


class TestDraftExpiryTightened:

    async def test_over_24h_incomplete_draft_deleted(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(
            test_db_pool, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
        result = await expire_stale_drafts(hours=24, actor="test")
        assert result["expired"] >= 1
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is None

    async def test_23h59m_preserved(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(
            test_db_pool, created_at=datetime.now(timezone.utc) - timedelta(hours=23, minutes=59))
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None

    async def test_draft_with_escort_name_preserved_past_24h(self, test_db_pool):
        """Decision/sec.17: a draft that already has usable escort
        identity is not 'incomplete', even past TTL."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(
            test_db_pool, escort_name="Some Escort", escort_mobile="",
            created_at=datetime.now(timezone.utc) - timedelta(hours=48))
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None

    async def test_draft_with_escort_mobile_preserved_past_24h(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(
            test_db_pool, escort_name="", escort_mobile="01811111111",
            created_at=datetime.now(timezone.utc) - timedelta(hours=48))
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None

    async def test_confirmed_program_preserved_regardless_of_age(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(
            test_db_pool, status="confirmed", created_at=datetime.now(timezone.utc) - timedelta(hours=100))
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE escort_roster_entries SET roster_status='confirmed' WHERE program_id=$1",
                prog["program_id"])
        await expire_stale_drafts(hours=24, actor="test")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None

    async def test_cleanup_idempotent(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        await _insert_draft_program(test_db_pool, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
        r1 = await expire_stale_drafts(hours=24, actor="test")
        r2 = await expire_stale_drafts(hours=24, actor="test")
        assert r1["expired"] >= 1
        assert r2["expired"] == 0  # nothing left to expire the second time

    async def test_audit_trail_recorded(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import expire_stale_drafts
        prog = await _insert_draft_program(test_db_pool, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
        await expire_stale_drafts(hours=24, actor="test_scheduler")
        async with test_db_pool.acquire() as conn:
            audit = await conn.fetchrow(
                "SELECT * FROM escort_roster_audit_logs WHERE program_id=$1 AND action='draft_ttl_deleted'",
                prog["program_id"])
        assert audit is not None
        assert audit["performed_by"] == "test_scheduler"


async def _insert_assignable_program(test_db_pool, employee_id, **overrides):
    defaults = dict(
        mother_vessel="MV UNASSIGN TEST", lighter_vessel="LT UNASSIGN TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="confirmed",
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status, escort_employee_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["program_date"], defaults["shift"], defaults["status"], employee_id,
        )
    return dict(row)


@pytest_asyncio.fixture
async def escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801811111111', 'Karim Escort', 'Escort', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


class TestSafeUnassignment:

    async def test_unassign_draft_succeeds(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="draft")
        result = await unassign_program(
            prog["program_id"], escort_employee["employee_id"], reason="wrong assignment", performed_by="tester")
        assert result["previous_employee_id"] == escort_employee["employee_id"]
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT escort_employee_id FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row["escort_employee_id"] is None

    async def test_unassign_confirmed_succeeds(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        result = await unassign_program(
            prog["program_id"], escort_employee["employee_id"], reason="operator error", performed_by="tester")
        assert result["previous_employee_id"] == escort_employee["employee_id"]

    async def test_stale_current_employee_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program, DispatchAssignmentError
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await unassign_program(prog["program_id"], 999999, reason="x", performed_by="tester")
        assert exc.value.code == "stale_assignment"

    async def test_running_status_blocks(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program, DispatchAssignmentError
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="running")
        with pytest.raises(DispatchAssignmentError) as exc:
            await unassign_program(
                prog["program_id"], escort_employee["employee_id"], reason="x", performed_by="tester")
        assert exc.value.code == "program_not_unassignable"

    async def test_attendance_blocks(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program, DispatchAssignmentError
        prog = await _insert_assignable_program(
            test_db_pool, escort_employee["employee_id"], status="confirmed", program_date=date(2026, 6, 1))
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO wbom_attendance (employee_id, attendance_date, status, recorded_by)
                   VALUES ($1, $2, 'Present', 'test')""",
                escort_employee["employee_id"], date(2026, 6, 1),
            )
        with pytest.raises(DispatchAssignmentError) as exc:
            await unassign_program(
                prog["program_id"], escort_employee["employee_id"], reason="x", performed_by="tester")
        assert exc.value.code == "attendance_exists"

    async def test_payment_draft_blocks(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program, DispatchAssignmentError
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO fazle_payment_drafts
                       (draft_type, employee_id, employee_name, employee_mobile, escort_program_id,
                        duty_days, expected_amount, payment_method, payout_mobile, status, source, draft_text)
                   VALUES ('escort_payment', $1, 'Karim Escort', '8801811111111', $2, 5.0, 1500.00,
                           'bkash', '01811111111', 'pending', 'test', 'test draft')""",
                escort_employee["employee_id"], prog["program_id"],
            )
        with pytest.raises(DispatchAssignmentError) as exc:
            await unassign_program(
                prog["program_id"], escort_employee["employee_id"], reason="x", performed_by="tester")
        assert exc.value.code == "payment_draft_exists"

    async def test_reason_required(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program, DispatchAssignmentError
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await unassign_program(
                prog["program_id"], escort_employee["employee_id"], reason="  ", performed_by="tester")
        assert exc.value.code == "reason_required"

    async def test_audit_evidence_created(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        await unassign_program(
            prog["program_id"], escort_employee["employee_id"], reason="test reason", performed_by="tester_actor")
        async with test_db_pool.acquire() as conn:
            audit = await conn.fetchrow(
                "SELECT * FROM escort_roster_audit_logs WHERE program_id=$1 AND action='dispatch_unassign'",
                prog["program_id"])
        assert audit is not None
        assert audit["performed_by"] == "tester_actor"

    async def test_roster_synced_after_unassign(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        await unassign_program(
            prog["program_id"], escort_employee["employee_id"], reason="x", performed_by="tester")
        async with test_db_pool.acquire() as conn:
            roster_row = await conn.fetchrow(
                "SELECT escort_employee_id FROM escort_roster_entries WHERE program_id=$1", prog["program_id"])
        assert roster_row is not None
        assert roster_row["escort_employee_id"] is None

    async def test_program_not_hard_deleted(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import unassign_program
        prog = await _insert_assignable_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        await unassign_program(
            prog["program_id"], escort_employee["employee_id"], reason="x", performed_by="tester")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row is not None
        assert row["status"] == "confirmed"  # status preserved, only employee cleared
