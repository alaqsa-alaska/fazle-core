"""
Dispatch Phase 2A.3 Stage 6 -- controlled conflict override, conflict-pair
review (Decision 5), and the read-only same-vessel review queue.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


async def _insert_program(test_db_pool, employee_id, **overrides):
    defaults = dict(
        mother_vessel="MV TEST", lighter_vessel="LT TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="confirmed",
        start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N",
        destination="Narayanganj", escort_name="", escort_mobile="", remarks=None,
        created_at=None,
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, escort_employee_id,
                   program_date, shift, status, start_date, end_date, end_shift, destination,
                   escort_name, escort_mobile, remarks, created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,COALESCE($15, NOW()))
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            employee_id, defaults["program_date"], defaults["shift"], defaults["status"],
            defaults["start_date"], defaults["end_date"], defaults["end_shift"], defaults["destination"],
            defaults["escort_name"], defaults["escort_mobile"], defaults["remarks"], defaults["created_at"],
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


class TestControlledOverride:

    async def test_override_succeeds_despite_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program
        blocking = await _insert_program(
            test_db_pool, escort_employee["employee_id"],
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        target = await _insert_program(test_db_pool, None, start_date=date(2026, 6, 2), end_date=date(2026, 6, 4))

        with pytest.raises(Exception):
            await assign_program(
                target["program_id"], escort_employee["employee_id"], None, performed_by="tester")

        result = await assign_program(
            target["program_id"], escort_employee["employee_id"], None, performed_by="admin_tester",
            override=True, override_reason="owner-approved double booking",
            acknowledged_conflict_program_ids=[blocking["program_id"]],
        )
        assert result["authorized_override"] is True
        assert result["new_employee_id"] == escort_employee["employee_id"]

    async def test_override_requires_reason(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        blocking = await _insert_program(test_db_pool, escort_employee["employee_id"])
        target = await _insert_program(test_db_pool, None, start_date=date(2026, 6, 2), end_date=date(2026, 6, 4))
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(
                target["program_id"], escort_employee["employee_id"], None, performed_by="admin_tester",
                override=True, override_reason="  ",
                acknowledged_conflict_program_ids=[blocking["program_id"]],
            )
        assert exc.value.code == "override_reason_required"

    async def test_override_rejects_stale_acknowledgment(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        blocking = await _insert_program(test_db_pool, escort_employee["employee_id"])
        target = await _insert_program(test_db_pool, None, start_date=date(2026, 6, 2), end_date=date(2026, 6, 4))
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(
                target["program_id"], escort_employee["employee_id"], None, performed_by="admin_tester",
                override=True, override_reason="reason", acknowledged_conflict_program_ids=[999999],
            )
        assert exc.value.code == "conflict_acknowledgment_stale"
        assert any(c["program_id"] == blocking["program_id"] for c in exc.value.conflicts)

    async def test_non_override_still_rejects_conflict(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, DispatchAssignmentError
        await _insert_program(test_db_pool, escort_employee["employee_id"])
        target = await _insert_program(test_db_pool, None, start_date=date(2026, 6, 2), end_date=date(2026, 6, 4))
        with pytest.raises(DispatchAssignmentError) as exc:
            await assign_program(target["program_id"], escort_employee["employee_id"], None, performed_by="tester")
        assert exc.value.code == "dispatch_conflict"

    async def test_overlaps_surfaces_authorized_override_marker(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import assign_program, list_overlaps
        blocking = await _insert_program(
            test_db_pool, escort_employee["employee_id"],
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        target = await _insert_program(test_db_pool, None, start_date=date(2026, 6, 2), end_date=date(2026, 6, 4))
        await assign_program(
            target["program_id"], escort_employee["employee_id"], None, performed_by="admin_tester",
            override=True, override_reason="owner-approved double booking",
            acknowledged_conflict_program_ids=[blocking["program_id"]],
        )
        result = await list_overlaps(employee_id=escort_employee["employee_id"])
        assert result["total"] == 1
        pair = result["items"][0]["conflicting_pairs"][0]
        assert pair["authorized_override"] is True


class TestConflictPairReview:

    async def test_keep_both_authorized_recorded_and_visible(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review, list_overlaps
        p1 = await _insert_program(
            test_db_pool, escort_employee["employee_id"],
            start_date=date(2026, 6, 1), end_date=date(2026, 6, 3), end_shift="N")
        p2 = await _insert_program(
            test_db_pool, escort_employee["employee_id"],
            start_date=date(2026, 6, 2), end_date=date(2026, 6, 4), end_shift="N")
        result = await record_conflict_pair_review(
            p1["program_id"], p2["program_id"], "keep_both_authorized",
            note="both legitimately assigned per operations note #4", performed_by="admin_tester")
        assert result["decision"] == "keep_both_authorized"

        overlaps = await list_overlaps(employee_id=escort_employee["employee_id"])
        pair = overlaps["items"][0]["conflicting_pairs"][0]
        assert pair["review"]["decision"] == "keep_both_authorized"
        assert pair["review"]["performed_by"] == "admin_tester"

    async def test_defer_for_investigation_recorded(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review
        p1 = await _insert_program(test_db_pool, escort_employee["employee_id"])
        p2 = await _insert_program(test_db_pool, escort_employee["employee_id"], start_date=date(2026, 6, 2))
        result = await record_conflict_pair_review(
            p1["program_id"], p2["program_id"], "defer_for_investigation",
            note="need to call the ghat supervisor", performed_by="admin_tester")
        assert result["decision"] == "defer_for_investigation"

    async def test_invalid_decision_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review, DispatchAssignmentError
        p1 = await _insert_program(test_db_pool, escort_employee["employee_id"])
        p2 = await _insert_program(test_db_pool, escort_employee["employee_id"], start_date=date(2026, 6, 2))
        with pytest.raises(DispatchAssignmentError) as exc:
            await record_conflict_pair_review(
                p1["program_id"], p2["program_id"], "cancel_duplicate",
                note="x", performed_by="admin_tester")
        assert exc.value.code == "invalid_decision"

    async def test_note_required(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review, DispatchAssignmentError
        p1 = await _insert_program(test_db_pool, escort_employee["employee_id"])
        p2 = await _insert_program(test_db_pool, escort_employee["employee_id"], start_date=date(2026, 6, 2))
        with pytest.raises(DispatchAssignmentError) as exc:
            await record_conflict_pair_review(
                p1["program_id"], p2["program_id"], "keep_both_authorized",
                note="   ", performed_by="admin_tester")
        assert exc.value.code == "note_required"

    async def test_self_pair_rejected(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review, DispatchAssignmentError
        p1 = await _insert_program(test_db_pool, escort_employee["employee_id"])
        with pytest.raises(DispatchAssignmentError) as exc:
            await record_conflict_pair_review(
                p1["program_id"], p1["program_id"], "keep_both_authorized",
                note="x", performed_by="admin_tester")
        assert exc.value.code == "invalid_pair"

    async def test_program_not_found(self, test_db_pool, escort_employee):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import record_conflict_pair_review, DispatchAssignmentError
        p1 = await _insert_program(test_db_pool, escort_employee["employee_id"])
        with pytest.raises(DispatchAssignmentError) as exc:
            await record_conflict_pair_review(
                p1["program_id"], 999999, "keep_both_authorized",
                note="x", performed_by="admin_tester")
        assert exc.value.code == "program_not_found"


class TestSameVesselReviewQueue:

    async def test_under_seven_day_classification(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE1", lighter_vessel="LT QUEUE1",
            escort_name="Escort A", escort_mobile="8801900000001",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE1", lighter_vessel="LT QUEUE1",
            escort_name="Escort B", escort_mobile="8801900000002",
            program_date=date(2026, 6, 3), start_date=date(2026, 6, 3))
        result = await find_same_vessel_review_candidates()
        deltas = [c["date_delta_days"] for c in result["under_seven_day"]]
        assert 2 in deltas

    async def test_seven_plus_day_classification(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE2", lighter_vessel="LT QUEUE2",
            escort_name="Escort A", escort_mobile="8801900000003",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE2", lighter_vessel="LT QUEUE2",
            escort_name="Escort B", escort_mobile="8801900000004",
            program_date=date(2026, 6, 10), start_date=date(2026, 6, 10))
        result = await find_same_vessel_review_candidates()
        deltas = [c["date_delta_days"] for c in result["seven_plus_day"]]
        assert 9 in deltas

    async def test_insufficient_data_empty_lighter_vessel(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE3", lighter_vessel="",
            escort_name="Escort A", escort_mobile="8801900000005",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE3", lighter_vessel="",
            escort_name="Escort B", escort_mobile="8801900000006",
            program_date=date(2026, 6, 2), start_date=date(2026, 6, 2))
        result = await find_same_vessel_review_candidates()
        assert len(result["insufficient_data"]) >= 1

    async def test_same_date_historical_classification(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE4", lighter_vessel="LT QUEUE4",
            escort_name="Escort A", escort_mobile="8801900000007",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE4", lighter_vessel="LT QUEUE4",
            escort_name="Escort B", escort_mobile="8801900000008",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        result = await find_same_vessel_review_candidates()
        assert len(result["same_date_historical"]) >= 1

    async def test_bulk_import_artifact_classification(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        now = datetime.now(timezone.utc)
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE5", lighter_vessel="",
            escort_name="Escort A", escort_mobile="8801900000009",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1),
            remarks="source_bridge:auto_created", created_at=now,
        )
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE5", lighter_vessel="",
            escort_name="Escort B", escort_mobile="8801900000010",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1),
            remarks="source_bridge:auto_created", created_at=now,
        )
        result = await find_same_vessel_review_candidates()
        assert len(result["bulk_import_artifact"]) >= 1

    async def test_same_escort_no_false_positive(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import find_same_vessel_review_candidates
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE6", lighter_vessel="LT QUEUE6",
            escort_name="Same Escort", escort_mobile="8801900000011",
            program_date=date(2026, 6, 1), start_date=date(2026, 6, 1))
        await _insert_program(
            test_db_pool, None, mother_vessel="MV QUEUE6", lighter_vessel="LT QUEUE6",
            escort_name="Same Escort", escort_mobile="8801900000011",
            program_date=date(2026, 6, 5), start_date=date(2026, 6, 5))
        result = await find_same_vessel_review_candidates()
        all_earlier = [c["program_id_earlier"] for cat in
                       (result["under_seven_day"], result["seven_plus_day"], result["same_date_historical"])
                       for c in cat]
        progs = await db.fetch_all(
            "SELECT program_id FROM wbom_escort_programs WHERE mother_vessel='MV QUEUE6'")
        prog_ids = {p["program_id"] for p in progs}
        assert not (prog_ids & set(all_earlier))
