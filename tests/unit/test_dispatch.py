"""
Dispatch Phase 2A.1 — read-only availability and conflict detection tests.
Covers window calculation, conflict detection, availability listing,
ranking, and the API layer. All read-only -- no test here ever writes
through the dispatch module itself (only via raw SQL fixtures seeding
test data, matching every other test file in this repo).
"""
from __future__ import annotations

from datetime import date

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


# ─────────────────────────────────────────────────────────────────────────────
# 14.1 Window tests (pure logic, no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignmentWindow:

    def test_day_only(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": date(2026, 6, 1), "end_shift": "D"})
        assert w.valid
        assert w.start_ord == w.end_ord

    def test_night_only(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "N",
                               "end_date": date(2026, 6, 1), "end_shift": "N"})
        assert w.valid
        assert w.start_ord == w.end_ord

    def test_day_to_night_same_day(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": date(2026, 6, 1), "end_shift": "N"})
        assert w.valid
        assert w.end_ord == w.start_ord + 1

    def test_overnight_night_to_next_day(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "N",
                               "end_date": date(2026, 6, 2), "end_shift": "D"})
        assert w.valid
        assert w.end_ord == w.start_ord + 1

    def test_multi_day(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": date(2026, 6, 5), "end_shift": "N"})
        assert w.valid
        assert w.end_ord - w.start_ord == 9  # 5 days * 2 shifts - 1

    def test_adjacent_windows_do_not_overlap(self):
        """Owner-recommended default: an assignment ending at the exact
        boundary where another starts does not conflict."""
        from modules.dispatch import AssignmentWindow
        a = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": date(2026, 6, 1), "end_shift": "N"})
        b = AssignmentWindow({"program_id": 2, "start_date": date(2026, 6, 2), "shift": "D",
                               "end_date": date(2026, 6, 2), "end_shift": "N"})
        assert not a.overlaps(b)
        assert not b.overlaps(a)

    def test_invalid_missing_end_shift(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": date(2026, 6, 3), "end_shift": None})
        assert not w.valid
        assert w.data_quality_issue == "invalid_or_missing_end_shift"

    def test_missing_end_date_treated_as_open_ended(self):
        """No end_date yet (in-progress) -- must still block, not be
        treated as invalid, matching find_active_program_for_employee()'s
        own status-based (not date-based) blocking."""
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 1), "shift": "D",
                               "end_date": None, "end_shift": None})
        assert w.valid
        assert w.end_date == date.max

    def test_end_before_start_invalid(self):
        from modules.dispatch import AssignmentWindow
        w = AssignmentWindow({"program_id": 1, "start_date": date(2026, 6, 5), "shift": "D",
                               "end_date": date(2026, 6, 1), "end_shift": "N"})
        assert not w.valid
        assert w.data_quality_issue == "end_before_start"

    def test_invalid_shift_value(self):
        from modules.dispatch import normalize_shift
        assert normalize_shift("X") is None
        assert normalize_shift("") is None
        assert normalize_shift(None) is None
        assert normalize_shift("d") == "D"
        assert normalize_shift("night") == "N"


# ─────────────────────────────────────────────────────────────────────────────
# 14.2 Conflict tests
# ─────────────────────────────────────────────────────────────────────────────

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


class TestConflictDetection:

    async def test_no_assignment_no_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is True
        assert r["conflicts"] == []

    async def test_exact_overlap(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"])
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is False
        assert len(r["conflicts"]) == 1
        assert r["conflicts"][0]["program_id"] == prog["program_id"]

    async def test_partial_overlap(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), shift="D", end_date=date(2026, 6, 3), end_shift="N")
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 2), "D", date(2026, 6, 5), "N")
        assert r["available"] is False

    async def test_contained_interval(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), shift="D", end_date=date(2026, 6, 10), end_shift="N")
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 3), "D", date(2026, 6, 5), "N")
        assert r["available"] is False

    async def test_same_employee_conflicts(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"])
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is False

    async def test_different_employee_no_conflict(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"])
        r = await check_conflict(other_escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is True

    async def test_excluded_program_no_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        prog = await _insert_program(test_db_pool, escort_employee["employee_id"])
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N",
                                  exclude_program_id=prog["program_id"])
        assert r["available"] is True

    async def test_cancelled_program_no_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"], status="cancelled")
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is True

    async def test_completed_program_no_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"], status="completed")
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 3), "N")
        assert r["available"] is True

    async def test_multiple_conflicts(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import check_conflict
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 1), end_date=date(2026, 6, 2), end_shift="N")
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 6, 4), end_date=date(2026, 6, 6), end_shift="N")
        r = await check_conflict(escort_employee["employee_id"], date(2026, 6, 1), "D", date(2026, 6, 6), "N")
        assert len(r["conflicts"]) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 14.3 Availability tests
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailability:

    async def test_active_escort_worker_listed(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        r = await list_escort_workers(employee_id=escort_employee["employee_id"])
        assert r["total"] == 1
        assert r["items"][0]["available"] is True

    async def test_inactive_worker_excluded(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                VALUES ('8801833333333', 'Inactive Guy', 'Escort', 'Inactive', 9000.00)
                RETURNING *
            """)
        r = await list_escort_workers(employee_id=row["employee_id"])
        assert r["total"] == 0

    async def test_non_escort_worker_excluded(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                VALUES ('8801844444444', 'Office Person', 'Office Assistant', 'Active', 9000.00)
                RETURNING *
            """)
        r = await list_escort_workers(employee_id=row["employee_id"])
        assert r["total"] == 0

    async def test_booked_worker_shows_unavailable(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        await _insert_program(test_db_pool, escort_employee["employee_id"])
        r = await list_escort_workers(employee_id=escort_employee["employee_id"])
        assert r["items"][0]["available"] is False
        assert r["items"][0]["current_conflict"] is not None

    async def test_free_worker_shows_available(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        await _insert_program(test_db_pool, escort_employee["employee_id"], status="completed")
        r = await list_escort_workers(employee_id=escort_employee["employee_id"])
        assert r["items"][0]["available"] is True

    async def test_no_historical_assignments(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import list_escort_workers
        r = await list_escort_workers(employee_id=escort_employee["employee_id"])
        assert r["items"][0]["last_assignment_date"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 14.4 Ranking tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRanking:

    async def test_longest_idle_ranks_first(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_suggestions
        # escort_employee had a program that ended long ago
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 1, 1), end_date=date(2026, 1, 2), end_shift="N",
                               status="completed")
        # other_escort_employee had one that ended recently
        await _insert_program(test_db_pool, other_escort_employee["employee_id"],
                               start_date=date(2026, 6, 10), end_date=date(2026, 6, 11), end_shift="N",
                               status="completed")
        r = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        ids_in_order = [e["employee_id"] for e in r["items"]]
        assert ids_in_order.index(escort_employee["employee_id"]) < ids_in_order.index(other_escort_employee["employee_id"])

    async def test_lower_workload_ranks_first(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_suggestions
        # other_escort_employee has an active (non-conflicting-window) assignment elsewhere in time
        await _insert_program(test_db_pool, other_escort_employee["employee_id"],
                               start_date=date(2026, 8, 1), end_date=date(2026, 8, 3), end_shift="N",
                               status="confirmed")
        r = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        by_id = {e["employee_id"]: e for e in r["items"]}
        assert by_id[escort_employee["employee_id"]]["active_assignment_count"] == 0
        assert by_id[other_escort_employee["employee_id"]]["active_assignment_count"] == 1

    async def test_unavailable_worker_excluded_from_suggestions(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_suggestions
        await _insert_program(test_db_pool, escort_employee["employee_id"],
                               start_date=date(2026, 7, 1), end_date=date(2026, 7, 3), end_shift="N",
                               status="confirmed")
        r = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        ids = [e["employee_id"] for e in r["items"]]
        assert escort_employee["employee_id"] not in ids

    async def test_deterministic_tie_order(self, test_db_pool, escort_employee, other_escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_suggestions
        r1 = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        r2 = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        assert [e["employee_id"] for e in r1["items"]] == [e["employee_id"] for e in r2["items"]]
        # equal idle/workload -> lower employee_id first
        ids = [e["employee_id"] for e in r1["items"]]
        assert ids == sorted(ids[:2]) or len(ids) < 2

    async def test_explanation_fields_present(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import get_suggestions
        r = await get_suggestions(date(2026, 7, 1), "D", date(2026, 7, 3), "N")
        assert all("reasons" in e and len(e["reasons"]) > 0 for e in r["items"])
        assert all("rank" in e for e in r["items"])


# ─────────────────────────────────────────────────────────────────────────────
# 14.5 API tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDispatchAPI:

    async def test_authorized_availability(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        r = await client.get("/api/dispatch/availability")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    async def test_unauthorized(self, test_db_pool, no_auth_headers):
        import app.database as db
        db._pool = test_db_pool
        from httpx import AsyncClient, ASGITransport
        from app.main import app as fastapi_app
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://testserver") as ac:
            r = await ac.get("/api/dispatch/availability", headers=no_auth_headers)
        assert r.status_code == 403

    async def test_malformed_shift_query(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        r = await client.post("/api/dispatch/check-conflict", json={
            "escort_employee_id": escort_employee["employee_id"],
            "start_date": "2026-06-01", "start_shift": "X",
            "end_date": "2026-06-03", "end_shift": "N",
        })
        assert r.status_code == 400

    async def test_empty_results(self, client, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        r = await client.get("/api/dispatch/availability")
        assert r.status_code == 200
        assert r.json()["items"] == []

    async def test_pagination(self, client, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        async with test_db_pool.acquire() as conn:
            for i in range(5):
                await conn.execute("""
                    INSERT INTO wbom_employees (employee_mobile, employee_name, designation, status, basic_salary)
                    VALUES ($1, $2, 'Escort', 'Active', 9000.00)
                """, f"88018{i:08d}", f"Guard {i}")
        r = await client.get("/api/dispatch/availability?page_size=2&page=1")
        assert r.status_code == 200
        d = r.json()
        assert len(d["items"]) == 2
        assert d["total"] == 5
        assert d["pages"] == 3

    async def test_response_schema(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        r = await client.get("/api/dispatch/availability")
        item = r.json()["items"][0]
        for key in ("employee_id", "employee_name", "available", "reason",
                    "current_conflict", "last_assignment_date", "active_assignment_count"):
            assert key in item

    async def test_no_database_mutation(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        await client.get("/api/dispatch/availability")
        await client.post("/api/dispatch/check-conflict", json={
            "escort_employee_id": escort_employee["employee_id"],
            "start_date": "2026-06-01", "start_shift": "D",
            "end_date": "2026-06-03", "end_shift": "N",
        })
        await client.get("/api/dispatch/suggestions?start_date=2026-06-01&start_shift=D&end_date=2026-06-03&end_shift=N")
        async with test_db_pool.acquire() as conn:
            prog_count = await conn.fetchval("SELECT COUNT(*) FROM wbom_escort_programs")
            roster_count = await conn.fetchval("SELECT COUNT(*) FROM escort_roster_entries")
        assert prog_count == 0
        assert roster_count == 0

    async def test_suggestions_endpoint(self, client, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        r = await client.get(
            "/api/dispatch/suggestions?start_date=2026-06-01&start_shift=D&end_date=2026-06-03&end_shift=N"
        )
        assert r.status_code == 200
        assert "items" in r.json()
