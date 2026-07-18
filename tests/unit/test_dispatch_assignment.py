"""
Dispatch Phase 2A.2 — controlled manual assignment tests. Covers
validation, assignment/reassignment, conflict rejection, concurrency
safety, and the read-only overlap-review endpoint. Every write here goes
through modules.dispatch.assign_program()/the /assign API -- nothing here
reproduces assignment SQL independently.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


async def _insert_program(test_db_pool, employee_id, **overrides):
    defaults = dict(
        mother_vessel="MV TEST", lighter_vessel="LT TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="confirmed",
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N",
        destination="Narayanganj",
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, escort_employee_id,
                   program_date, shift, status, start_date, end_date, end_shift, destination)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            employee_id, defaults["program_date"], defaults["shift"], defaults["status"],
            defaults["start_date"], defaults["end_date"], defaults["end_shift"], defaults["destination"],
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


@pytest_asyncio.fixture
async def other_escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801822222222', 'Rahim Escort', 'Escort', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


@pytest_asyncio.fixture
async def inactive_escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801855555555', 'Inactive Escort', 'Escort', 'Inactive', 9000.00)
            RETURNING *
        """)
    return dict(row)


@pytest_asyncio.fixture
async def non_escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801866666666', 'Office Person', 'Office Assistant', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


async def _seed_role_admin(test_db_pool, role_name: str, phone: str, raw_key: str) -> dict:
    """Real, usable API key for a specific single role -- for RBAC tests that
    need something other than the internal_api_key (which passes every
    check) or the read-only `client` fixture's superadmin-equivalent key."""
    from modules.rbac import hash_api_key
    async with test_db_pool.acquire() as conn:
        admin_row = await conn.fetchrow(
            """INSERT INTO fazle_admins (phone, name, username, status, api_key_hash)
               VALUES ($1, $2, $3, 'active', $4) RETURNING *""",
            phone, f"Test {role_name}", f"test_{role_name}_{phone[-4:]}", hash_api_key(raw_key),
        )
        await conn.execute(
            "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1, $2, 'system')",
            admin_row["id"], role_name,
        )
    return dict(admin_row)


@pytest_asyncio.fixture
async def viewer_key(test_db_pool):
    key = "test-viewer-key-2a2"
    await _seed_role_admin(test_db_pool, "viewer", "8801700000101", key)
    return key


@pytest_asyncio.fixture
async def operator_key(test_db_pool):
    key = "test-operator-key-2a2"
    await _seed_role_admin(test_db_pool, "operator", "8801700000102", key)
    return key


# ─────────────────────────────────────────────────────────────────────────────
# 11.1 Validation tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignmentValidation:

    async def test_program_not_found(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(999999, escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "program_not_found"
        assert exc.value.http_status == 404

    async def test_employee_not_found(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="draft")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], 999999, None, "tester")
        assert exc.value.code == "employee_not_found"
        assert exc.value.http_status == 404

    async def test_inactive_employee_rejected(self, test_db_pool, inactive_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="draft")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], inactive_escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "employee_not_active"
        assert exc.value.http_status == 422

    async def test_non_escort_employee_rejected(self, test_db_pool, non_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="draft")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], non_escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "employee_not_escort"
        assert exc.value.http_status == 422

    async def test_invalid_program_window_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        # end before start -> invalid window
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 5), end_date=date(2026, 6, 1), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "invalid_program_window"
        assert exc.value.http_status == 422

    async def test_terminal_completed_program_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="completed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "program_not_assignable"
        assert exc.value.http_status == 409

    async def test_terminal_cancelled_program_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="cancelled")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "program_not_assignable"

    async def test_running_program_reassignment_excluded(self, test_db_pool, escort_employee, other_escort_employee):
        """Owner-instructed exclusion, confirmed necessary by tracing
        find_active_program_for_employee()/mark_program_running_on_attendance()
        -- both key strictly on escort_employee_id with no physical-presence
        check, so reassigning a running program is unsafe."""
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"], status="running")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], other_escort_employee["employee_id"],
                                  escort_employee["employee_id"], "tester")
        assert exc.value.code == "program_not_assignable"

    async def test_malformed_request_body(self, client, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        r = await client.post("/api/dispatch/programs/1/assign", json={"escort_employee_id": "not-an-int"})
        assert r.status_code == 422

    async def test_unauthorized_viewer_role_rejected(self, test_db_pool, escort_employee, viewer_key):
        import app.database as db
        db._pool = test_db_pool
        prog = await _insert_program(test_db_pool, None, status="draft")
        from httpx import AsyncClient, ASGITransport
        from app.main import app as fastapi_app
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://testserver") as ac:
            r = await ac.post(
                f"/api/dispatch/programs/{prog['program_id']}/assign",
                json={"escort_employee_id": escort_employee["employee_id"], "expected_current_employee_id": None},
                headers={"X-Internal-Key": viewer_key},
            )
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# 11.2 Assignment tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignment:

    async def test_assign_unassigned_program(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        result = await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert result["new_employee_id"] == escort_employee["employee_id"]
        assert result["previous_employee_id"] is None
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT escort_employee_id FROM wbom_escort_programs WHERE program_id=$1",
                                       prog["program_id"])
        assert row["escort_employee_id"] == escort_employee["employee_id"]

    async def test_reassignment_on_confirmed_program(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        result = await assign_program(prog["program_id"], other_escort_employee["employee_id"],
                                       escort_employee["employee_id"], "tester")
        assert result["previous_employee_id"] == escort_employee["employee_id"]
        assert result["new_employee_id"] == other_escort_employee["employee_id"]

    async def test_current_assignment_preserved_on_conflict(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        # other_escort_employee already booked elsewhere, overlapping window
        await _insert_program(test_db_pool, other_escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"], status="confirmed",
                                      start_date=date(2026, 6, 2), end_date=date(2026, 6, 4), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], other_escort_employee["employee_id"],
                                  escort_employee["employee_id"], "tester")
        assert exc.value.code == "dispatch_conflict"
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT escort_employee_id FROM wbom_escort_programs WHERE program_id=$1",
                                       prog["program_id"])
        assert row["escort_employee_id"] == escort_employee["employee_id"]  # unchanged

    async def test_roster_updated_after_assignment(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        async with test_db_pool.acquire() as conn:
            roster_row = await conn.fetchrow(
                "SELECT escort_employee_id FROM escort_roster_entries WHERE program_id=$1", prog["program_id"])
        assert roster_row is not None
        assert roster_row["escort_employee_id"] == escort_employee["employee_id"]

    async def test_one_roster_row_remains_after_reassignment(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        await assign_program(prog["program_id"], other_escort_employee["employee_id"],
                              escort_employee["employee_id"], "tester")
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE program_id=$1", prog["program_id"])
        assert count == 1

    async def test_audit_evidence_created(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester_actor")
        async with test_db_pool.acquire() as conn:
            audit_row = await conn.fetchrow(
                "SELECT * FROM escort_roster_audit_logs WHERE program_id=$1 AND action='dispatch_assign'",
                prog["program_id"])
        assert audit_row is not None
        assert audit_row["performed_by"] == "tester_actor"
        assert audit_row["source"] == "dispatch_ui"

    async def test_response_returns_updated_employee(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        result = await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert result["employee_name"] == escort_employee["employee_name"]
        assert result["program_id"] == prog["program_id"]


# ─────────────────────────────────────────────────────────────────────────────
# 11.3 Conflict tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignmentConflicts:

    async def test_free_employee_succeeds(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        result = await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert result["new_employee_id"] == escort_employee["employee_id"]

    async def test_exact_overlap_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "dispatch_conflict"
        assert len(exc.value.conflicts) == 1

    async def test_partial_overlap_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 2), end_date=date(2026, 6, 5), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "dispatch_conflict"

    async def test_contained_overlap_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 10), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 3), end_date=date(2026, 6, 5), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "dispatch_conflict"

    async def test_adjacent_window_succeeds(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), shift="D", end_date=date(2026, 6, 1), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 2), shift="D", end_date=date(2026, 6, 2), end_shift="N")
        result = await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert result["new_employee_id"] == escort_employee["employee_id"]

    async def test_different_employee_succeeds(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        result = await assign_program(prog["program_id"], other_escort_employee["employee_id"], None, "tester")
        assert result["new_employee_id"] == other_escort_employee["employee_id"]

    async def test_terminal_historical_program_does_not_block(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        await _insert_program(test_db_pool, escort_employee["employee_id"], status="completed",
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        result = await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert result["new_employee_id"] == escort_employee["employee_id"]

    async def test_open_ended_blocking_program_rejects(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        # running elsewhere, no end_date -> open-ended block
        await _insert_program(test_db_pool, escort_employee["employee_id"], status="running",
                               start_date=date(2026, 1, 1), end_date=None, end_shift=None)
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 8, 1), end_date=date(2026, 8, 3), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert exc.value.code == "dispatch_conflict"

    async def test_multiple_conflicts_returned(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 10), end_shift="N")
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 5), end_date=date(2026, 6, 15), end_shift="N")
        prog = await _insert_program(test_db_pool, None, status="draft",
                                      start_date=date(2026, 6, 1), end_date=date(2026, 6, 15), end_shift="N")
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        assert len(exc.value.conflicts) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 11.4 Concurrency tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignmentConcurrency:

    async def test_concurrent_different_employees_same_program_one_winner(
            self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="draft")

        async def attempt(emp_id):
            try:
                return await assign_program(prog["program_id"], emp_id, None, "tester")
            except DispatchAssignmentError as e:
                return e

        results = await asyncio.gather(
            attempt(escort_employee["employee_id"]), attempt(other_escort_employee["employee_id"]),
        )
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, DispatchAssignmentError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "stale_assignment"

    async def test_concurrent_same_employee_overlapping_programs_no_double_booking(
            self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog_a = await _insert_program(test_db_pool, None, status="draft",
                                        start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        prog_b = await _insert_program(test_db_pool, None, status="draft",
                                        start_date=date(2026, 6, 2), end_date=date(2026, 6, 4), end_shift="N")

        async def attempt(program_id):
            try:
                return await assign_program(program_id, escort_employee["employee_id"], None, "tester")
            except DispatchAssignmentError as e:
                return e

        results = await asyncio.gather(attempt(prog_a["program_id"]), attempt(prog_b["program_id"]))
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, DispatchAssignmentError)]
        # The employee-level advisory lock serializes these -- exactly one
        # must succeed (the other's conflict check, run AFTER the first
        # commits, correctly sees the new overlap and rejects).
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "dispatch_conflict"

    async def test_two_operators_same_expected_current_value(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, None, status="draft")

        async def attempt(emp_id):
            try:
                return await assign_program(prog["program_id"], emp_id, None, "tester")
            except DispatchAssignmentError as e:
                return e

        results = await asyncio.gather(
            attempt(escort_employee["employee_id"]), attempt(other_escort_employee["employee_id"]),
        )
        successes = [r for r in results if isinstance(r, dict)]
        assert len(successes) == 1

    async def test_reassignment_race(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"], status="confirmed")
        async with test_db_pool.acquire() as conn:
            third_emp = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                VALUES ('8801877777777', 'Third Escort', 'Escort', 'Active', 9000.00) RETURNING *""")

        async def attempt(emp_id):
            try:
                return await assign_program(prog["program_id"], emp_id, escort_employee["employee_id"], "tester")
            except DispatchAssignmentError as e:
                return e

        results = await asyncio.gather(attempt(other_escort_employee["employee_id"]), attempt(third_emp["employee_id"]))
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, DispatchAssignmentError)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].code == "stale_assignment"

    async def test_rollback_on_roster_sync_failure(self, test_db_pool, escort_employee, monkeypatch):
        """If sync_program_to_roster raises inside the transaction, the
        escort_employee_id UPDATE must roll back too -- program and roster
        must never be left disagreeing."""
        import app.database as db
        db._pool = test_db_pool
        import modules.escort_roster.db as roster_db

        async def _boom(*a, **kw):
            raise RuntimeError("simulated roster sync failure")

        monkeypatch.setattr(roster_db, "sync_program_to_roster", _boom)
        from modules.dispatch import assign_program
        prog = await _insert_program(test_db_pool, None, status="draft")
        with pytest.raises(RuntimeError):
            await assign_program(prog["program_id"], escort_employee["employee_id"], None, "tester")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT escort_employee_id FROM wbom_escort_programs WHERE program_id=$1", prog["program_id"])
        assert row["escort_employee_id"] is None  # rolled back, not committed


# ─────────────────────────────────────────────────────────────────────────────
# 11.5 Existing overlap tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOverlapsEndpoint:

    async def test_overlap_endpoint_groups_correctly(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_overlaps
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), end_shift="N")
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 3), end_date=date(2026, 6, 8), end_shift="N")
        r = await list_overlaps()
        assert r["total"] == 1
        assert r["items"][0]["employee_id"] == escort_employee["employee_id"]
        assert r["items"][0]["conflict_count"] == 1

    async def test_multiple_programs_shown(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_overlaps
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), end_shift="N")
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 3), end_date=date(2026, 6, 8), end_shift="N")
        r = await list_overlaps()
        assert len(r["items"][0]["programs"]) == 2

    async def test_no_mutation_from_overlaps_query(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), end_shift="N")
        before = await test_db_pool.fetchval("SELECT COUNT(*) FROM wbom_escort_programs")
        r = await client.get("/api/dispatch/overlaps")
        assert r.status_code == 200
        after = await test_db_pool.fetchval("SELECT COUNT(*) FROM wbom_escort_programs")
        assert before == after

    async def test_overlaps_pagination(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_overlaps
        async with test_db_pool.acquire() as conn:
            for i in range(3):
                row = await conn.fetchrow(f"""
                    INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                    VALUES ('88019{i:07d}', 'Overlap Guy {i}', 'Escort', 'Active', 9000.00) RETURNING *""")
                await _insert_program(test_db_pool, row["employee_id"],
                                       start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), end_shift="N")
                await _insert_program(test_db_pool, row["employee_id"],
                                       start_date=date(2026, 6, 3), end_date=date(2026, 6, 8), end_shift="N")
        r = await list_overlaps(page=1, page_size=2)
        assert len(r["items"]) == 2
        assert r["total"] == 3
        assert r["pages"] == 2

    async def test_overlaps_endpoint_authorization(self, client, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        r = await client.get("/api/dispatch/overlaps")
        assert r.status_code == 200  # viewer-or-above (read-only), matches other Dispatch reads

    async def test_malformed_legacy_row_produces_warning_not_500(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_overlaps
        # invalid window (bad end_shift) alongside a normal one for the same employee
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 5), end_shift=None)
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 3), end_date=date(2026, 6, 8), end_shift="N")
        r = await list_overlaps()  # must not raise
        assert isinstance(r, dict)
