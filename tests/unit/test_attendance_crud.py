"""Unit tests — modules/attendance/crud (Owner decision, 2026-07-11, manual Attendance CRUD)"""
from __future__ import annotations

from datetime import date

import pytest

pytestmark = pytest.mark.unit


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


class TestDeriveDutyDays:
    def test_present_is_1(self):
        from modules.attendance.crud import derive_duty_days
        assert derive_duty_days("Present") == 1.0

    def test_half_day_is_half(self):
        from modules.attendance.crud import derive_duty_days
        assert derive_duty_days("Half Day") == 0.5

    def test_absent_is_zero(self):
        from modules.attendance.crud import derive_duty_days
        assert derive_duty_days("Absent") == 0.0

    def test_invalid_status_rejected(self):
        from fastapi import HTTPException
        from modules.attendance.crud import derive_duty_days
        with pytest.raises(HTTPException) as exc:
            derive_duty_days("Something Else")
        assert exc.value.status_code == 400


class TestCreateManualAttendance:
    async def test_create_sets_correct_audit_fields(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance

        row = await create_manual_attendance(
            seed_employee["employee_id"], date(2026, 5, 5), "Present", "on site", "admin1",
        )
        assert row["entry_source"] == "manual_office"
        assert row["approval_channel"] == "manual_direct"
        assert row["approved_by"] == "admin1"
        assert row["approved_at"] is not None
        assert row["source_draft_id"] is None
        assert row["status"] == "Present"

    async def test_duplicate_create_rejected_not_overwritten(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.attendance.crud import create_manual_attendance

        await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 5), "Present", "", "admin1")
        with pytest.raises(HTTPException) as exc:
            await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 5), "Absent", "", "admin2")
        assert exc.value.status_code == 409

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, approved_by FROM wbom_attendance WHERE employee_id=$1 AND attendance_date=$2",
                seed_employee["employee_id"], date(2026, 5, 5),
            )
        assert row["status"] == "Present", "The rejected duplicate create must not overwrite the original"
        assert row["approved_by"] == "admin1"

    async def test_invalid_employee_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.attendance.crud import create_manual_attendance

        with pytest.raises(HTTPException) as exc:
            await create_manual_attendance(999999, date(2026, 5, 5), "Present", "", "admin1")
        assert exc.value.status_code == 400


class TestUpdateManualAttendance:
    async def test_edit_updates_status_and_audit_fields(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance, update_manual_attendance

        created = await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 6), "Present", "", "admin1")
        updated = await update_manual_attendance(created["attendance_id"], "Half Day", "corrected", "admin2")
        assert updated["status"] == "Half Day"
        assert updated["remarks"] == "corrected"
        assert updated["approved_by"] == "admin2"

    async def test_edit_nonexistent_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.attendance.crud import update_manual_attendance

        with pytest.raises(HTTPException) as exc:
            await update_manual_attendance(999999, "Present", None, "admin1")
        assert exc.value.status_code == 404


class TestDeleteManualAttendance:
    async def test_delete_removes_row(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance, delete_manual_attendance, get_attendance

        created = await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 7), "Present", "", "admin1")
        await delete_manual_attendance(created["attendance_id"], "admin1")
        assert await get_attendance(created["attendance_id"]) is None

    async def test_delete_nonexistent_rejected(self, test_db_pool):
        _use_pool(test_db_pool)
        from fastapi import HTTPException
        from modules.attendance.crud import delete_manual_attendance

        with pytest.raises(HTTPException) as exc:
            await delete_manual_attendance(999999, "admin1")
        assert exc.value.status_code == 404


class TestListAttendance:
    async def test_filters_by_employee_and_date(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance, list_attendance

        await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 1), "Present", "", "admin1")
        await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, 2), "Absent", "", "admin1")

        result = await list_attendance(employee_id=seed_employee["employee_id"], date_from=date(2026, 5, 2))
        assert result["total"] == 1
        assert result["items"][0]["attendance_date"] == date(2026, 5, 2)

    async def test_pagination(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import create_manual_attendance, list_attendance

        for day in range(1, 6):
            await create_manual_attendance(seed_employee["employee_id"], date(2026, 5, day), "Present", "", "admin1")

        page1 = await list_attendance(employee_id=seed_employee["employee_id"], page=1, page_size=2)
        assert len(page1["items"]) == 2
        assert page1["total"] == 5
        assert page1["pages"] == 3


class TestEmployeeLookup:
    async def test_lookup_by_id(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import lookup_employee_by_id

        result = await lookup_employee_by_id(seed_employee["employee_id"])
        assert result["employee_name"] == seed_employee["employee_name"]
        assert "basic_salary" in result

    async def test_lookup_by_id_not_found(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.attendance.crud import lookup_employee_by_id

        assert await lookup_employee_by_id(999999) is None

    async def test_suggest_by_name(self, test_db_pool, seed_employee):
        _use_pool(test_db_pool)
        from modules.attendance.crud import suggest_employees_by_name

        results = await suggest_employees_by_name(seed_employee["employee_name"][:4])
        assert any(r["employee_id"] == seed_employee["employee_id"] for r in results)

    async def test_suggest_duplicate_names_returns_all(self, test_db_pool, seed_employee, seed_employee2):
        _use_pool(test_db_pool)
        from modules.attendance.crud import suggest_employees_by_name

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_employees SET employee_name='Same Name' WHERE employee_id=$1", seed_employee["employee_id"]
            )
            await conn.execute(
                "UPDATE wbom_employees SET employee_name='Same Name' WHERE employee_id=$1", seed_employee2["employee_id"]
            )
        results = await suggest_employees_by_name("Same Name")
        ids = {r["employee_id"] for r in results}
        assert seed_employee["employee_id"] in ids
        assert seed_employee2["employee_id"] in ids


class TestAttendanceRoleGate:
    """Attendance/payroll data includes basic_salary -- admin+superadmin
    only, matching modules.client_billing.routes's precedent."""

    async def test_operator_rejected(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        from modules.attendance.routes import _require_admin_or_above

        with patch(
            "modules.attendance.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["operator"]},
        ):
            with pytest.raises(HTTPException) as exc:
                await _require_admin_or_above("fake-operator-key")
        assert exc.value.status_code == 403

    async def test_admin_accepted(self):
        from unittest.mock import patch
        from modules.attendance.routes import _require_admin_or_above

        with patch(
            "modules.attendance.routes._resolve_actor",
            return_value={"is_internal": False, "roles": ["admin"]},
        ):
            actor = await _require_admin_or_above("fake-admin-key")
        assert actor["roles"] == ["admin"]
