"""
Dispatch Phase 2A.3 Stage 2 -- same-date vessel-match correction guard.
Integration-style tests against the real disposable test DB (mocking every
internal DB call of _create_confirmed_from_admin() individually would be
fragile given how many distinct queries it makes -- this matches the
established pattern in tests/unit/test_dispatch_assignment.py and
test_dispatch_2a3_schema.py instead).
"""
from __future__ import annotations

from datetime import date

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
        mother_vessel="MV STAGE2 TEST", lighter_vessel="LT STAGE2 TEST", master_mobile="8801933333333",
        program_date=date(2026, 6, 1), shift="D", status="confirmed",
        escort_name="Original Escort", escort_mobile="01811111111", escort_employee_id=None,
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


def _draft(mother_vessel="MV STAGE2 TEST", lighter_vessel="LT STAGE2 TEST", master_mobile="8801933333333",
           escort_name="New Escort", escort_mobile="01822222222", date_str="01-06-2026", shift="D"):
    return {
        "mother_vessel": mother_vessel, "lighter_vessel": lighter_vessel, "master_mobile": master_mobile,
        "cargo_type": None, "importer": None, "capacity": None,
        "escort_name": escort_name, "escort_mobile": escort_mobile,
        "date_str": date_str, "shift": shift, "destination": None,
    }


class TestSameDateCorrectionGuard:

    async def test_later_admin_correction_updates_one_row_no_duty_evidence(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="confirmed")

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")
        assert pid == prog["program_id"]

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
            count = await conn.fetchval("SELECT COUNT(*) FROM wbom_escort_programs")
        assert row["escort_name"] == "New Escort"
        assert row["escort_mobile"] == "01822222222"
        assert count == 1  # no duplicate row created

    async def test_old_and_new_values_audited(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="confirmed")

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")

        async with test_db_pool.acquire() as conn:
            audit = await conn.fetchrow(
                "SELECT * FROM escort_roster_audit_logs WHERE program_id=$1 AND action='vessel_match_correction'",
                pid,
            )
        assert audit is not None
        assert audit["source"] == "admin_message"
        import json as _json
        old_data = _json.loads(audit["old_data"]) if isinstance(audit["old_data"], str) else audit["old_data"]
        new_data = _json.loads(audit["new_data"]) if isinstance(audit["new_data"], str) else audit["new_data"]
        assert old_data["escort_name"] == "Original Escort"
        assert new_data["escort_name"] == "New Escort"

    async def test_running_row_not_overwritten(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="running")

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")
        assert pid == prog["program_id"]

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["status"] == "running"  # not reverted to confirmed
        assert row["escort_name"] == "Original Escort"  # not overwritten
        assert row["escort_mobile"] == "01811111111"

    async def test_completed_row_not_overwritten(self, test_db_pool, monkeypatch):
        """'completed' is treated as attendance-gated evidence too (only
        reachable via the running->completed lifecycle)."""
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="completed")

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
        assert row["status"] == "completed"
        assert row["escort_name"] == "Original Escort"

    async def test_running_row_creates_manual_review_request(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="running")

        await _create_confirmed_from_admin(_draft(), source="bridge2")

        async with test_db_pool.acquire() as conn:
            review = await conn.fetchrow(
                "SELECT * FROM escort_roster_change_requests WHERE program_id=$1 "
                "AND action='manual_review_same_date_escort_change'",
                prog["program_id"],
            )
        assert review is not None
        assert review["status"] == "pending"

    async def test_empty_lighter_vessel_goes_to_manual_review(self, test_db_pool, monkeypatch):
        """Decision 3: insufficient lighter-vessel evidence blocks automatic
        escort-identity resolution even without running/completed status."""
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="confirmed", lighter_vessel="")

        pid = await _create_confirmed_from_admin(
            _draft(lighter_vessel=""), source="bridge2",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
            review = await conn.fetchrow(
                "SELECT * FROM escort_roster_change_requests WHERE program_id=$1 "
                "AND action='manual_review_same_date_escort_change'",
                pid,
            )
        assert row["escort_name"] == "Original Escort"  # not overwritten
        assert review is not None
        import json as _json
        payload = _json.loads(review["payload"]) if isinstance(review["payload"], str) else review["payload"]
        assert payload["reason"] == "insufficient_data_empty_lighter_vessel"

    async def test_same_escort_repeated_confirmation_idempotent(self, test_db_pool, monkeypatch):
        """A repeat/resend of the SAME escort on an already-running program
        must not trip the guard -- no real change is being requested."""
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(
            test_db_pool, status="running", escort_name="Same Guy", escort_mobile="01833333333")

        pid = await _create_confirmed_from_admin(
            _draft(escort_name="Same Guy", escort_mobile="01833333333"), source="bridge2",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
            review = await conn.fetchrow(
                "SELECT * FROM escort_roster_change_requests WHERE program_id=$1", prog["program_id"])
        assert row["status"] == "running"  # still preserved (guard's status-CASE applies regardless)
        assert review is None  # no manual review needed -- nothing actually changed

    async def test_same_date_different_escort_without_duty_evidence_is_correction(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="confirmed")  # not running/completed

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
            review = await conn.fetchrow(
                "SELECT * FROM escort_roster_change_requests WHERE program_id=$1", prog["program_id"])
        assert row["escort_name"] == "New Escort"  # correction applied
        assert review is None

    async def test_same_date_different_escort_with_duty_evidence_is_not_correction(self, test_db_pool, monkeypatch):
        import app.database as db
        db._pool = test_db_pool
        _set_escort_env(monkeypatch)
        from modules.escort import _create_confirmed_from_admin
        prog = await _insert_program(test_db_pool, status="running")

        pid = await _create_confirmed_from_admin(_draft(), source="bridge2")

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM wbom_escort_programs WHERE program_id=$1", pid)
            review = await conn.fetchrow(
                "SELECT * FROM escort_roster_change_requests WHERE program_id=$1", prog["program_id"])
        assert row["escort_name"] == "Original Escort"  # NOT corrected in place
        assert review is not None
