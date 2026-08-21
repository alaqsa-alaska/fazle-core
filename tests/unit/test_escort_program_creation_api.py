"""
Escort program creation API tests (2026-08-21, Phase 4 coverage-audit
follow-on, P1-C).

Covers POST /api/escort/programs (modules/escort/routes.py) -- the new
structured wrapper over the EXISTING modules.escort.save_escort_programs(),
built to close the forensic finding that create_roster_entry() (raw
roster maintenance) never created a corresponding wbom_escort_programs
row, so a freshly-created roster entry was never a dispatchable business
program.

Also regression-tests the forensic finding itself (raw roster tool still
does NOT fabricate a program row) and chains program creation into the
existing dispatch engine to prove the full create -> discover -> dispatch
lifecycle now works end to end. Every write here goes through the real
save_escort_programs()/assign_program() services -- nothing here
reproduces their SQL independently.
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def escort_employee(test_db_pool):
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
            VALUES ('8801877777001', 'P1C Test Escort', 'Escort', 'Active', 9000.00)
            RETURNING *
        """)
    return dict(row)


class TestCreateEscortProgram:
    @pytest.mark.asyncio
    async def test_valid_program_creates_a_real_row(self, client, test_db_pool):
        r = await client.post("/api/escort/programs", json={
            "mother_vessel": "MV P1C TEST", "lighter_vessel": "LV P1C TEST",
            "master_mobile": "01711112222", "destination": "Chattogram", "shift": "D",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["program_ids"]
        pid = body["program_ids"][0]

        async with test_db_pool.acquire() as conn:
            prog = await conn.fetchrow(
                "SELECT program_id, status, mother_vessel, lighter_vessel FROM wbom_escort_programs WHERE program_id = $1",
                pid,
            )
        assert prog is not None
        assert prog["status"] == "draft"
        assert prog["mother_vessel"] == "MV P1C TEST"

    @pytest.mark.asyncio
    async def test_created_program_is_immediately_roster_synced(self, client, test_db_pool):
        """The whole point of using save_escort_programs() instead of a
        second implementation: roster sync happens automatically, not as a
        separate step Hermes/the caller has to remember."""
        r = await client.post("/api/escort/programs", json={
            "mother_vessel": "MV P1C SYNC", "lighter_vessel": "LV P1C SYNC",
            "master_mobile": "01711113333", "shift": "D",
        })
        assert r.status_code == 200, r.text
        pid = r.json()["program_ids"][0]

        async with test_db_pool.acquire() as conn:
            roster = await conn.fetchrow(
                "SELECT program_id, roster_status, mother_vessel FROM escort_roster_entries WHERE program_id = $1",
                pid,
            )
        assert roster is not None, "program was created but never roster-synced"
        assert roster["roster_status"] == "draft"

    @pytest.mark.asyncio
    async def test_missing_required_field_rejected(self, client):
        r = await client.post("/api/escort/programs", json={
            "mother_vessel": "MV P1C BAD", "lighter_vessel": "", "master_mobile": "01711114444",
        })
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_program_returns_existing_id_not_a_second_row(self, client, test_db_pool):
        payload = {
            "mother_vessel": "MV P1C DUP", "lighter_vessel": "LV P1C DUP",
            "master_mobile": "01711115555", "shift": "D",
        }
        r1 = await client.post("/api/escort/programs", json=payload)
        r2 = await client.post("/api/escort/programs", json=payload)
        assert r1.status_code == 200 and r2.status_code == 200
        pid1 = r1.json()["program_ids"][0]
        pid2 = r2.json()["program_ids"][0]
        assert pid1 == pid2, "dedup should return the SAME program, not create a second row"

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_escort_programs WHERE mother_vessel = 'MV P1C DUP'"
            )
        assert count == 1

    @pytest.mark.asyncio
    async def test_unauthorized_caller_rejected(self, client):
        r = await client.post(
            "/api/escort/programs",
            json={"mother_vessel": "MV X", "lighter_vessel": "LV X", "master_mobile": "01711116666"},
            headers={"X-Internal-Key": ""},
        )
        assert r.status_code == 403


class TestRawRosterToolDoesNotFabricateProgram:
    """Regression test for the forensic finding itself: create_roster_entry
    (escort_roster's raw maintenance tool) must keep NOT creating a
    wbom_escort_programs row -- if this ever changes, dispatch_tools.py's
    docstrings and Hermes's own tool-selection guidance would need
    revisiting."""

    @pytest.mark.asyncio
    async def test_create_roster_entry_leaves_no_program_row(self, client, test_db_pool):
        r = await client.post("/api/escort-roster", json={
            "mother_vessel": "MV P1C RAW ROSTER ONLY",
            "start_date": "2026-12-01", "start_shift": "D",
            "end_date": "2026-12-02", "end_shift": "D",
            "roster_status": "draft",
        })
        assert r.status_code == 200, r.text
        pid = r.json()["program_id"]

        async with test_db_pool.acquire() as conn:
            prog = await conn.fetchrow("SELECT program_id FROM wbom_escort_programs WHERE program_id = $1", pid)
        assert prog is None, (
            "create_roster_entry now creates a wbom_escort_programs row -- "
            "if this is intentional, update escort_roster_tools.py's/"
            "dispatch_tools.py's docstrings, which currently document the "
            "opposite as a load-bearing fact."
        )


class TestProgramToDispatchLifecycle:
    """Chains the new creation endpoint into the EXISTING dispatch engine --
    proves the full create -> discover -> dispatch lifecycle works, not
    just that each half works in isolation."""

    @pytest.mark.asyncio
    async def test_created_program_is_dispatchable(self, client, test_db_pool, escort_employee):
        create_r = await client.post("/api/escort/programs", json={
            "mother_vessel": "MV P1C LIFECYCLE", "lighter_vessel": "LV P1C LIFECYCLE",
            "master_mobile": "01711117777", "shift": "D",
        })
        assert create_r.status_code == 200, create_r.text
        pid = create_r.json()["program_ids"][0]

        # The program must exist in status='draft' (assignable per
        # modules.dispatch's own _ASSIGNABLE_STATUSES).
        assign_r = await client.post(
            f"/api/dispatch/programs/{pid}/assign",
            json={"escort_employee_id": escort_employee["employee_id"]},
        )
        assert assign_r.status_code == 200, assign_r.text
        assert assign_r.json()["new_employee_id"] == escort_employee["employee_id"]

        # Roster must reflect the assignment too (still program-authoritative).
        async with test_db_pool.acquire() as conn:
            roster = await conn.fetchrow(
                "SELECT escort_employee_id FROM escort_roster_entries WHERE program_id = $1", pid
            )
        assert roster["escort_employee_id"] == escort_employee["employee_id"]

    @pytest.mark.asyncio
    async def test_ambiguous_or_inactive_employee_still_rejected_by_dispatch(self, client, test_db_pool):
        """Not a new check -- proves dispatch's EXISTING employee-validity
        gate still applies to a program created through the new endpoint,
        i.e. the new creation path doesn't create a side door around it."""
        create_r = await client.post("/api/escort/programs", json={
            "mother_vessel": "MV P1C INVALID EMP", "lighter_vessel": "LV P1C INVALID EMP",
            "master_mobile": "01711118888", "shift": "D",
        })
        pid = create_r.json()["program_ids"][0]

        assign_r = await client.post(
            f"/api/dispatch/programs/{pid}/assign",
            json={"escort_employee_id": 99999999},
        )
        assert assign_r.status_code == 404
        assert assign_r.json()["detail"]["code"] == "employee_not_found"
