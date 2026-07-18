"""
Phase 1I -- escort-roster dual-write concurrency tests for
modules.escort_roster.db.sync_program_to_roster().

Background: modules/escort_lifecycle's own write to escort_roster_entries
(a single atomic INSERT ... ON CONFLICT) was always safe and remains
UNCHANGED -- it is the canonical, financially-authoritative writer for the
release-event path (its salary/conveyance/food_bill/total figures come
from the actual fazle_payment_drafts row, not an independent recomputation).
sync_program_to_roster() is a SEPARATE writer used by modules/escort,
modules/escort_roster/history_sync.py, and the admin
create/resync API (modules/escort_roster/routes.py) -- it had two real,
independently-confirmed concurrency bugs, both fixed here:

1. Check-then-act race: a SELECT-existing-row-then-branch (UPDATE vs
   INSERT) instead of one atomic statement. Reproduced: concurrent calls
   for the same program_id raised
   "duplicate key value violates unique constraint
   escort_roster_entries_program_id_key". Fixed: single
   INSERT ... ON CONFLICT (program_id) DO UPDATE.
2. Nested-acquire deadlock: the function never accepted/threaded a `conn`
   parameter, so every one of its DB calls independently acquired a fresh
   connection from the shared pool. Calling it from inside a caller's
   already-open `pool.acquire()` block (as the reverted 2026-07-13 refactor
   did, routing escort_lifecycle through this function) could exhaust a
   small pool and hang every concurrent caller forever -- reproduced as a
   genuine 25s+ hang. Fixed: optional `conn` parameter threaded through
   every internal DB call (matching the existing `conn=` convention already
   used throughout this codebase, e.g. escort_lifecycle's own
   find_active_program_for_employee(..., conn=conn)).

See knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md
for the original diagnostic and management_decisions.md Phase 1I for the
full reproduction evidence and decision record.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def croster_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees
                (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801811111111', 'Karim Guard', 'Security Guard', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


async def _insert_completed_program(test_db_pool, employee, mother_vessel="MV GOLDEN STAR"):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile,
                   escort_employee_id, escort_mobile,
                   program_date, shift, status, start_date, end_date, day_count)
               VALUES ($5,'LT AMENA-3','8801933333333',
                       $1, $2, $3, 'D', 'completed', $3, $4, 5.0)
               RETURNING *""",
            employee["employee_id"], employee["employee_mobile"],
            date(2026, 5, 1), date(2026, 5, 5), mother_vessel,
        )
    return dict(row)


class TestSyncProgramToRosterConcurrency:

    async def test_10_concurrent_same_program_no_duplicate_no_error(
        self, test_db_pool, croster_employee
    ):
        """10 concurrent calls for the SAME program_id: must not raise,
        must not hang, must leave exactly 1 row."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster

        program = await _insert_completed_program(test_db_pool, croster_employee)
        pid = program["program_id"]

        async def _call(i):
            return await asyncio.wait_for(
                sync_program_to_roster(pid, actor=f"c{i}"), timeout=10.0
            )

        results = await asyncio.gather(*[_call(i) for i in range(10)], return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"unexpected errors: {errors}"

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE program_id=$1", pid
            )
        assert count == 1

    async def test_10_concurrent_different_programs_no_cross_contamination(
        self, test_db_pool, croster_employee
    ):
        """10 concurrent calls across 10 DIFFERENT program_ids must all
        succeed independently with no contention between them."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster

        programs = [
            await _insert_completed_program(test_db_pool, croster_employee, f"MV SHIP {i}")
            for i in range(10)
        ]

        async def _call(pid):
            return await asyncio.wait_for(sync_program_to_roster(pid), timeout=10.0)

        results = await asyncio.gather(
            *[_call(p["program_id"]) for p in programs], return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"unexpected errors: {errors}"
        assert all(r.get("id") for r in results if isinstance(r, dict))

    async def test_nested_conn_no_deadlock(self, test_db_pool, croster_employee):
        """Calling with an explicit conn from inside an already-held
        transaction (matching how a careful future caller would compose
        this into its own transaction) must not deadlock even at 10-way
        concurrency on a small pool."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster

        program = await _insert_completed_program(test_db_pool, croster_employee)
        pid = program["program_id"]

        async def _nested_call(i):
            async with test_db_pool.acquire() as outer_conn:
                async with outer_conn.transaction():
                    return await asyncio.wait_for(
                        sync_program_to_roster(pid, actor=f"n{i}", conn=outer_conn),
                        timeout=10.0,
                    )

        results = await asyncio.wait_for(
            asyncio.gather(*[_nested_call(i) for i in range(10)], return_exceptions=True),
            timeout=20.0,
        )
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"unexpected errors/timeouts: {errors}"

    async def test_release_path_and_sync_concurrent_no_conflict(
        self, test_db_pool, croster_employee
    ):
        """escort_lifecycle's own release-confirmation write (Writer 1,
        UNCHANGED) running concurrently with an admin-triggered
        sync_program_to_roster() call (Writer 2) for the SAME program must
        not hang or corrupt the row -- exactly the "release +
        sync_program_to_roster" scenario from the Phase 1I test matrix."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster
        from modules.escort_lifecycle import handle_release_event

        program = await _insert_completed_program(test_db_pool, croster_employee)
        program["status"] = "running"
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_escort_programs SET status='running', end_date=NULL, day_count=NULL WHERE program_id=$1",
                program["program_id"],
            )

        async def _release():
            return await asyncio.wait_for(
                handle_release_event(
                    croster_employee["employee_id"],
                    extracted={"end_date": "2026-05-05", "day_count": 5.0},
                    source="race-release",
                    admin_confirmed=True,
                ),
                timeout=10.0,
            )

        async def _sync(i):
            try:
                return await asyncio.wait_for(
                    sync_program_to_roster(program["program_id"], actor=f"admin-sync-{i}"),
                    timeout=10.0,
                )
            except ValueError:
                return {"skipped": "program not found yet"}

        results = await asyncio.gather(
            _release(), *[_sync(i) for i in range(5)], return_exceptions=True
        )
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"unexpected errors/timeouts: {errors}"

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE program_id=$1",
                program["program_id"],
            )
        assert count == 1, "exactly one roster row must exist regardless of write order"

    async def test_idempotent_calc_version_increments(self, test_db_pool, croster_employee):
        """Calling twice sequentially must not create a duplicate row and
        must increment calc_version (matches Writer 1's own idempotency
        contract, e.g. calc_version=escort_roster_entries.calc_version+1)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster

        program = await _insert_completed_program(test_db_pool, croster_employee)
        pid = program["program_id"]

        r1 = await sync_program_to_roster(pid, actor="first")
        r2 = await sync_program_to_roster(pid, actor="second")

        assert r1["id"] == r2["id"]
        assert r2["calc_version"] == r1["calc_version"] + 1

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE program_id=$1", pid
            )
        assert count == 1

    async def test_rollback_of_outer_transaction_rolls_back_roster_write(
        self, test_db_pool, croster_employee
    ):
        """When conn is threaded through and the OUTER transaction rolls
        back, the roster-entry write must roll back with it (proves the
        conn-threading actually composes into one real transaction, not
        just passing the object without transactional effect)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.escort_roster.db import sync_program_to_roster

        program = await _insert_completed_program(test_db_pool, croster_employee)
        pid = program["program_id"]

        try:
            async with test_db_pool.acquire() as conn:
                async with conn.transaction():
                    await sync_program_to_roster(pid, actor="will-rollback", conn=conn)
                    raise RuntimeError("deliberate rollback")
        except RuntimeError:
            pass

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM escort_roster_entries WHERE program_id=$1", pid
            )
        assert count == 0, "roster write must have rolled back with the outer transaction"
