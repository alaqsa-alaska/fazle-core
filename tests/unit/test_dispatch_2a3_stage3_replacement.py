"""
Dispatch Phase 2A.3 Stage 3 -- escort replacement segments (different-date
same-vessel-pair). Covers create_replacement_segment() directly (the core
transactional logic) plus the automatic-detection integration point in
modules.escort._create_confirmed_from_admin().
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
import pytest_asyncio

pytestmark = pytest.mark.workflow


def _set_escort_env(monkeypatch, clients: str = "8801670535255,8801955555555") -> None:
    monkeypatch.setenv("ESCORT_CLIENT_PHONES", clients)
    monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
    monkeypatch.setenv("BRIDGE1_NUMBER", "8801958122300")
    monkeypatch.setenv("BRIDGE2_NUMBER", "8801880446111")
    from app.config import get_settings
    get_settings.cache_clear()


async def _insert_program(test_db_pool, **overrides):
    defaults = dict(
        mother_vessel="MV STAGE3 TEST", lighter_vessel="LT STAGE3 TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="running",
        escort_name="Earlier Escort", escort_mobile="01811111111", escort_employee_id=None,
    )
    defaults.update(overrides)
    async with test_db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO wbom_escort_programs
                  (mother_vessel, lighter_vessel, master_mobile, program_date, shift, status,
                   escort_name, escort_mobile, escort_employee_id)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
               RETURNING *""",
            defaults["mother_vessel"], defaults["lighter_vessel"], defaults["master_mobile"],
            defaults["program_date"], defaults["shift"], defaults["status"],
            defaults["escort_name"], defaults["escort_mobile"], defaults["escort_employee_id"],
        )
    return dict(row)


class TestReplacementBoundaryRule:

    async def test_one_day_difference_is_escort_changed(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 2), start_shift="D",
        )
        assert result["earlier_status"] == "escort_changed"
        assert result["classification_rule"] == "under_7_days_escort_changed"

    async def test_six_day_difference_is_escort_changed(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 7), start_shift="D",
        )
        assert result["earlier_status"] == "escort_changed"

    async def test_exact_seven_day_difference_is_completed(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 8), start_shift="D",
        )
        assert result["earlier_status"] == "completed"
        assert result["classification_rule"] == "7_days_or_more_completed"

    async def test_eight_day_difference_is_completed(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 9), start_shift="D",
        )
        assert result["earlier_status"] == "completed"

    async def test_end_boundary_immediately_before_new_start(self, test_db_pool):
        """Later starts Day -> earlier ends previous Night boundary (sec.12 example)."""
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 3), start_shift="D",
        )
        assert result["earlier_end_date"] == "2026-06-02"
        assert result["earlier_end_shift"] == "N"

    async def test_new_segment_created_with_replaces_program_id(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 2), start_shift="D",
        )
        async with test_db_pool.acquire() as conn:
            new_row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs WHERE program_id=$1", result["new_program_id"])
        assert new_row["replaces_program_id"] == earlier["program_id"]
        assert new_row["status"] == "running"
        assert new_row["escort_name"] == "New Escort"


class TestReplacementValidation:

    async def test_explicit_completion_overrides_under_seven_rule(self, test_db_pool):
        """An already-completed/cancelled/escort_changed earlier program
        must reject automatic replacement -- explicit prior evidence
        outranks inference."""
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool, status="completed")
        with pytest.raises(DispatchAssignmentError) as exc:
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                escort_name="New Escort", escort_mobile="01822222222",
                start_date=date(2026, 6, 2), start_shift="D",
            )
        assert exc.value.code == "already_terminal"

    async def test_empty_lighter_vessel_rejected(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool, lighter_vessel="")
        with pytest.raises(DispatchAssignmentError) as exc:
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="", escort_employee_id=None,
                escort_name="New Escort", escort_mobile="01822222222",
                start_date=date(2026, 6, 2), start_shift="D",
            )
        assert exc.value.code == "insufficient_vessel_match"

    async def test_same_escort_not_treated_as_replacement(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool, escort_name="Same Guy", escort_mobile="01833333333")
        with pytest.raises(DispatchAssignmentError) as exc:
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                escort_name="Same Guy", escort_mobile="01833333333",
                start_date=date(2026, 6, 2), start_shift="D",
            )
        assert exc.value.code == "same_escort"

    async def test_not_later_start_rejected(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool, program_date=date(2026, 6, 5))
        with pytest.raises(DispatchAssignmentError) as exc:
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                escort_name="New Escort", escort_mobile="01822222222",
                start_date=date(2026, 6, 1), start_shift="D",  # earlier, not later
            )
        assert exc.value.code == "not_later"

    async def test_duplicate_replacement_prevented(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool)
        await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 2), start_shift="D",
        )
        # earlier is now escort_changed (terminal) -- a second attempt on
        # the SAME earlier program must reject via already_terminal, not
        # duplicate_active_replacement (it's checked first).
        with pytest.raises(DispatchAssignmentError) as exc:
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                escort_name="Third Escort", escort_mobile="01844444444",
                start_date=date(2026, 6, 3), start_shift="D",
            )
        assert exc.value.code == "already_terminal"


class TestReplacementConcurrency:

    async def test_concurrent_replacement_requests_one_winner(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment, DispatchAssignmentError
        earlier = await _insert_program(test_db_pool)

        async def attempt(escort_name, escort_mobile, start_date):
            try:
                return await create_replacement_segment(
                    earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                    lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                    escort_name=escort_name, escort_mobile=escort_mobile,
                    start_date=start_date, start_shift="D",
                )
            except DispatchAssignmentError as e:
                return e

        results = await asyncio.gather(
            attempt("Escort A", "01822222222", date(2026, 6, 2)),
            attempt("Escort B", "01833333333", date(2026, 6, 3)),
        )
        successes = [r for r in results if isinstance(r, dict)]
        failures = [r for r in results if isinstance(r, DispatchAssignmentError)]
        assert len(successes) == 1
        assert len(failures) == 1

    async def test_rollback_on_roster_sync_failure(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        import modules.escort_roster.db as roster_db

        async def _boom(*a, **kw):
            raise RuntimeError("simulated roster sync failure")

        earlier = await _insert_program(test_db_pool)
        monkeypatch.setattr(roster_db, "sync_program_to_roster", _boom)
        from modules.dispatch import create_replacement_segment
        with pytest.raises(RuntimeError):
            await create_replacement_segment(
                earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
                lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
                escort_name="New Escort", escort_mobile="01822222222",
                start_date=date(2026, 6, 2), start_shift="D",
            )
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1", earlier["program_id"])
            count = await conn.fetchval("SELECT COUNT(*) FROM wbom_escort_programs")
        assert row["status"] == "running"  # unchanged -- rolled back
        assert count == 1  # no orphaned new-segment row either


class TestReplacementDataIsolation:

    async def test_roster_history_preserved_both_segments(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        result = await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 2), start_shift="D",
        )
        async with test_db_pool.acquire() as conn:
            earlier_roster = await conn.fetchrow(
                "SELECT * FROM escort_roster_entries WHERE program_id=$1", earlier["program_id"])
            new_roster = await conn.fetchrow(
                "SELECT * FROM escort_roster_entries WHERE program_id=$1", result["new_program_id"])
        assert earlier_roster is not None
        assert new_roster is not None
        assert earlier_roster["escort_name"] == "Earlier Escort"
        assert new_roster["escort_name"] == "New Escort"

    async def test_no_attendance_or_payment_touched(self, test_db_pool):
        import app.database as db
        db._pool = test_db_pool
        from modules.dispatch import create_replacement_segment
        earlier = await _insert_program(test_db_pool)
        before_attendance = await test_db_pool.fetchval("SELECT COUNT(*) FROM wbom_attendance")
        before_payment = await test_db_pool.fetchval("SELECT COUNT(*) FROM fazle_payment_drafts")
        await create_replacement_segment(
            earlier_program_id=earlier["program_id"], mother_vessel="MV STAGE3 TEST",
            lighter_vessel="LT STAGE3 TEST", escort_employee_id=None,
            escort_name="New Escort", escort_mobile="01822222222",
            start_date=date(2026, 6, 2), start_shift="D",
        )
        after_attendance = await test_db_pool.fetchval("SELECT COUNT(*) FROM wbom_attendance")
        after_payment = await test_db_pool.fetchval("SELECT COUNT(*) FROM fazle_payment_drafts")
        assert before_attendance == after_attendance
        assert before_payment == after_payment


class TestAutomaticDetectionIntegration:

    async def test_admin_message_later_date_creates_replacement(self, test_db_pool, monkeypatch):
        """The _create_confirmed_from_admin() fallback path, when it finds
        a same-vessel-pair earlier program with a different escort, must
        route to create_replacement_segment() instead of a plain insert."""
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        earlier = await _insert_program(
            test_db_pool, mother_vessel="MV INTEGRATION", lighter_vessel="LT INTEGRATION",
            program_date=date(2026, 6, 1),
        )
        draft = {
            "mother_vessel": "MV INTEGRATION", "lighter_vessel": "LT INTEGRATION",
            "master_mobile": "8801933333333", "cargo_type": None, "importer": None, "capacity": None,
            "escort_name": "New Escort", "escort_mobile": "01822222222",
            "date_str": "10-06-2026", "shift": "D", "destination": None,
        }
        pid = await _create_confirmed_from_admin(draft, source="bridge2")
        assert pid != earlier["program_id"]  # a NEW program was created
        async with test_db_pool.acquire() as conn:
            earlier_row = await conn.fetchrow(
                "SELECT status FROM wbom_escort_programs WHERE program_id=$1", earlier["program_id"])
            new_row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert earlier_row["status"] == "completed"  # 9-day delta -> completed
        assert new_row["replaces_program_id"] == earlier["program_id"]

    async def test_unrelated_vessel_creates_plain_new_program(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        draft = {
            "mother_vessel": "MV COMPLETELY UNRELATED", "lighter_vessel": "LT UNRELATED",
            "master_mobile": "8801944444444", "cargo_type": None, "importer": None, "capacity": None,
            "escort_name": "Fresh Escort", "escort_mobile": "01855555555",
            "date_str": "10-06-2026", "shift": "D", "destination": None,
        }
        pid = await _create_confirmed_from_admin(draft, source="bridge2")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["replaces_program_id"] is None
        assert row["status"] == "confirmed"
