"""Unit tests — modules/escort_lifecycle"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from datetime import date

pytestmark = pytest.mark.unit


class TestIsReleaseIntent:
    """Test Bengali/English release keyword detection."""

    @pytest.mark.parametrize("text", [
        "ডিউটি শেষ",
        "রিলিজ হয়েছি",
        "ছুটি দিন",
        "ভেসেল ছেড়েছি",
        "release",
        "duty done",
        "duty finished",
        "program completed",
        "ডিউটি শেষ হয়েছে আলহামদুলিল্লাহ",
    ])
    def test_release_keywords_detected(self, text):
        from modules.escort_lifecycle import is_release_intent
        assert is_release_intent(text) is True

    @pytest.mark.parametrize("text", [
        "হাজির আছি",
        "অগ্রিম লাগবে",
        "চাকরি করতে চাই",
        "MV GOLDEN STAR escort lagbe",
        "hello world",
    ])
    def test_non_release_not_detected(self, text):
        from modules.escort_lifecycle import is_release_intent
        assert is_release_intent(text) is False


class TestReleaseAuthorization:
    async def test_employee_release_cannot_finalize(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import handle_release_event

        result = await handle_release_event(seed_employee["employee_id"])
        assert result["status"] == "admin_confirmation_required"
        async with test_db_pool.acquire() as conn:
            status = await conn.fetchval(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1",
                seed_escort_program["program_id"],
            )
        assert status != "Completed"

    async def test_admin_confirmation_updates_release_state_atomically(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import handle_release_event

        result = await handle_release_event(
            seed_employee["employee_id"],
            extracted={
                "end_date": "2026-05-02",
                "end_shift": "N",
                "food_bill": "100",
                "conveyance": "200",
                "release_point": "Test Port",
            },
            source="release-admin-confirm",
            admin_confirmed=True,
        )
        assert result["ok"] is True
        async with test_db_pool.acquire() as conn:
            program = await conn.fetchrow(
                "SELECT status, food_bill, conveyance FROM wbom_escort_programs WHERE program_id=$1",
                seed_escort_program["program_id"],
            )
            attendance = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE escort_program_id=$1",
                seed_escort_program["program_id"],
            )
            roster = await conn.fetchrow(
                "SELECT * FROM escort_roster_entries WHERE program_id=$1",
                seed_escort_program["program_id"],
            )
        assert program["status"] == "completed"
        assert attendance == 2
        assert draft is not None
        assert roster is not None
        assert float(roster["total_days"]) == pytest.approx(2.0)


class TestCloseProgram:
    """Test program closure: status update, day_count computation."""

    async def test_close_sets_completed_status(
        self, test_db_pool, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import close_program

        program_id = seed_escort_program["program_id"]
        result = await close_program(
            program_id=program_id,
            end_date_v=date(2026, 5, 5),
            end_shift="D",
            release_point="Ctg Port",
            day_count=None,
            completed_by="8801700000001",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, day_count FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["status"] == "completed"

    async def test_day_count_computed_correctly(
        self, test_db_pool, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import close_program

        program_id = seed_escort_program["program_id"]
        # program_date = 2026-05-01, end_date = 2026-05-05 → 5 days
        await close_program(
            program_id=program_id,
            end_date_v=date(2026, 5, 5),
            end_shift="D",
            release_point="Test Port",
            day_count=None,
            completed_by="8801700000001",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT day_count FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["day_count"] == pytest.approx(5.0)

    async def test_minimum_one_day(
        self, test_db_pool, seed_escort_program
    ):
        """Same start and end date = 1 day (not 0)."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import close_program

        program_id = seed_escort_program["program_id"]
        # Same day release
        await close_program(
            program_id=program_id,
            end_date_v=date(2026, 5, 1),  # same as start
            end_shift="D",
            release_point="Test Port",
            day_count=None,
            completed_by="8801700000001",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT day_count FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["day_count"] >= 1.0

    async def test_idempotent_on_already_completed(
        self, test_db_pool, seed_escort_program
    ):
        """Calling close_program twice should not fail."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import close_program

        program_id = seed_escort_program["program_id"]
        await close_program(
            program_id=program_id,
            end_date_v=date(2026, 5, 5),
            end_shift="D",
            release_point="Ctg",
            day_count=None,
            completed_by="8801700000001",
        )
        # Second call
        result = await close_program(
            program_id=program_id,
            end_date_v=date(2026, 5, 5),
            end_shift="D",
            release_point="Ctg",
            day_count=None,
            completed_by="8801700000001",
        )
        assert result.get("already_closed") is True


class TestAttendanceBackfill:
    """Test backfill_attendance_for_program()."""

    async def test_backfill_creates_daily_rows(
        self, test_db_pool, seed_escort_program, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import backfill_attendance_for_program

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        # Set up program with dates
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET start_date='2026-05-01', end_date='2026-05-05',
                    status='completed', day_count=5
                WHERE program_id=$1
            """, program_id)

        await backfill_attendance_for_program(program_id=program_id)

        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM wbom_attendance WHERE employee_id=$1 ORDER BY attendance_date",
                employee_id,
            )
        assert len(rows) == 5, f"Expected 5 attendance rows, got {len(rows)}"

    async def test_backfill_does_not_overwrite_existing_rows(
        self, test_db_pool, seed_escort_program, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import backfill_attendance_for_program

        employee_id = seed_employee["employee_id"]
        program_id = seed_escort_program["program_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET start_date='2026-05-01', end_date='2026-05-03',
                    status='completed', day_count=3
                WHERE program_id=$1
            """, program_id)
            # Pre-insert 2026-05-02 with Absent manually
            await conn.execute("""
                INSERT INTO wbom_attendance
                    (employee_id, attendance_date, status, recorded_by)
                VALUES ($1, '2026-05-02', 'Absent', 'manual')
            """, employee_id)

        await backfill_attendance_for_program(program_id=program_id)

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_attendance WHERE employee_id=$1 AND attendance_date='2026-05-02'",
                employee_id,
            )
        # ON CONFLICT DO NOTHING — manual row preserved
        assert row["status"] == "Absent"

    async def test_backfill_sets_recorded_by_lifecycle(
        self, test_db_pool, seed_escort_program, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.escort_lifecycle import backfill_attendance_for_program

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET start_date='2026-05-01', end_date='2026-05-02',
                    status='completed'
                WHERE program_id=$1
            """, program_id)

        await backfill_attendance_for_program(program_id=program_id)

        async with test_db_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT recorded_by FROM wbom_attendance WHERE employee_id=$1",
                employee_id,
            )
        for row in rows:
            assert row["recorded_by"] == "escort-lifecycle"


class TestMarkProgramRunningOnAttendance:
    """Owner decision 2026-07-10: confirmed -> running fires on the first
    accepted escort attendance, not a date-only background job."""

    async def test_confirmed_program_transitions_to_running(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import mark_program_running_on_attendance

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_escort_programs SET status='confirmed' WHERE program_id=$1",
                program_id,
            )

        result = await mark_program_running_on_attendance(employee_id)
        assert result is not None
        assert result["status"] == "running"
        assert result["program_id"] == program_id

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["status"] == "running"

    async def test_idempotent_on_repeated_attendance(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import mark_program_running_on_attendance

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_escort_programs SET status='confirmed' WHERE program_id=$1",
                program_id,
            )

        first = await mark_program_running_on_attendance(employee_id)
        assert first is not None and first["status"] == "running"

        # Second (and any later) accepted attendance for the same guard must
        # be a harmless no-op -- the program stays running, no error, no
        # second "transition" event.
        second = await mark_program_running_on_attendance(employee_id)
        assert second is None

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["status"] == "running"

    async def test_no_active_confirmed_program_is_noop(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        """seed_escort_program defaults to 'running' already -- no 'confirmed'
        program exists for this employee, so the call must no-op."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import mark_program_running_on_attendance

        employee_id = seed_employee["employee_id"]
        result = await mark_program_running_on_attendance(employee_id)
        assert result is None

    async def test_completed_program_not_reopened(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        """An attendance approval arriving after release must never revert
        a completed program back to running."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.escort_lifecycle import mark_program_running_on_attendance

        program_id = seed_escort_program["program_id"]
        employee_id = seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_escort_programs SET status='completed' WHERE program_id=$1",
                program_id,
            )

        result = await mark_program_running_on_attendance(employee_id)
        assert result is None

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert row["status"] == "completed"
