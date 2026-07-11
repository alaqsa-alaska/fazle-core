"""Unit tests — modules/attendance"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.unit


class TestIsAttendanceMessage:
    """Test keyword detection for attendance messages."""

    @pytest.mark.parametrize("text", [
        "হাজির",
        "হাজির আছি",
        "উপস্থিত",
        "present",
        "on duty",
        "check in",
        "checked in",
        "আমি হাজির MV GOLDEN STAR",
        "Present sir",
    ])
    def test_attendance_keywords_detected(self, text):
        from modules.attendance import is_attendance_message
        assert is_attendance_message(text) is True

    @pytest.mark.parametrize("text", [
        "অগ্রিম লাগবে",
        "ডিউটি শেষ",
        "MV GOLDEN STAR escort lagbe",
        "চাকরি করতে চাই",
        "ধন্যবাদ",
        "hello",
    ])
    def test_non_attendance_not_detected(self, text):
        from modules.attendance import is_attendance_message
        assert is_attendance_message(text) is False


class TestHandleAttendanceMessage:
    """Test attendance draft creation flow."""

    async def test_creates_draft_for_known_employee(
        self, test_db_pool, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import handle_attendance_message

        result = await handle_attendance_message(
            sender_phone=seed_employee["employee_mobile"],
            text="হাজির আছি MV TEST",
            source="bridge1",
        )

        # A draft should be created
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None

    async def test_returns_none_for_unknown_sender(self, test_db_pool):
        """Unknown sender not in wbom_employees — should not crash."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import handle_attendance_message

        result = await handle_attendance_message(
            sender_phone="8801999999999",
            text="হাজির",
            source="bridge1",
        )
        # No crash — returns (reply_str, None) or a string
        assert result is None or isinstance(result, (str, tuple))

    async def test_phone_normalisation_0x(self, test_db_pool, seed_employee):
        """Employee stored as 8801811111111; lookup with 01811111111 (0X) should match via normalization."""
        import app.database as db_module
        db_module._pool = test_db_pool

        # seed_employee is already stored as 8801811111111 (880X format)
        # Call with 0X format — attendance module should normalize 0X → 880X
        from modules.attendance import handle_attendance_message

        await handle_attendance_message(
            sender_phone="01811111111",   # 0X format — should find 8801811111111
            text="হাজির আছি",
            source="bridge1",
        )

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None


class TestSaveAttendance:
    """Test attendance saving after admin APPROVE."""

    async def test_saves_present_row(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import save_attendance

        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=__import__('datetime').date.today(),
            status="Present",
            location="MV TEST VESSEL",
            recorded_by="8801700000001",
        )

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_attendance WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert row is not None
        assert row["status"] == "Present"

    async def test_duplicate_date_prevented(self, test_db_pool, seed_employee):
        """UNIQUE(employee_id, attendance_date) — second insert for same date should not raise."""
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.attendance import save_attendance

        # First insert
        today = __import__('datetime').date.today()
        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=today,
            status="Present",
            location="MV TEST",
            recorded_by="system",
        )
        # Second insert same day — should not raise
        await save_attendance(
            employee_id=seed_employee["employee_id"],
            attendance_date=today,
            status="Present",
            location="MV TEST",
            recorded_by="system",
        )

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert count == 1, "Duplicate attendance row should not be inserted"


async def _insert_attendance_draft(pool, *, intent="attendance", status="pending",
                                    recipient="8801811111111", source="bridge1",
                                    context=None, source_module="attendance_parser",
                                    flat_meta=None):
    """Insert a fazle_draft_replies row matching real production meta shapes.

    context: dict -> wrapped/current shape (meta.context is a JSON string,
             matching shared.draft_reply.create_draft_reply's real output).
    flat_meta: dict -> legacy/flat shape (fields directly at meta top level,
               no role/context/source_module wrapper) -- some historical
               production rows use this shape.
    """
    import json
    if flat_meta is not None:
        meta = json.dumps(flat_meta)
    else:
        meta = json.dumps({
            "role": "employee",
            "context": json.dumps(context or {}),
            "source_module": source_module,
        })
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """INSERT INTO fazle_draft_replies
                   (source, recipient, reply_text, intent, status, created_at, meta)
               VALUES ($1, $2, 'draft body', $3, $4, NOW(), $5::jsonb)
               RETURNING id""",
            source, recipient, intent, status, meta,
        )


class TestFinalizeAttendanceDraft:
    """Owner decision 2026-07-11: shared attendance-finalization service used
    by both the WhatsApp APPROVE <id> channel and the frontend Review Queue.
    """

    async def test_finalizes_context_wrapped_parser_draft(self, test_db_pool, seed_employee):
        """The real bug this fixes: attendance_parser drafts store fields
        double-nested inside meta.context as a JSON string, not top-level."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={
                "employee_id": seed_employee["employee_id"],
                "employee_name": seed_employee["employee_name"],
                "shift": "D",
                "att_date": "2026-05-05",
            },
            source_module="attendance_parser",
        )

        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is True
        assert result["already_approved"] is False
        assert result["employee_id"] == seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, entry_source, approval_channel, approved_by, source_draft_id "
                "FROM wbom_attendance WHERE employee_id=$1", seed_employee["employee_id"],
            )
            draft_row = await conn.fetchrow("SELECT status FROM fazle_draft_replies WHERE id=$1", draft_id)
        assert row["status"] == "Present"
        assert row["entry_source"] == "message_parser"
        assert row["approval_channel"] == "whatsapp_message"
        assert row["approved_by"] == "admin1"
        assert row["source_draft_id"] == draft_id
        assert draft_row["status"] == "approved"

    async def test_idempotent_on_repeated_approval(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={"employee_id": seed_employee["employee_id"], "att_date": "2026-05-05", "shift": "D"},
        )

        first = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert first["ok"] is True and first["already_approved"] is False

        second = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert second["ok"] is True and second["already_approved"] is True

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1", seed_employee["employee_id"],
            )
        assert count == 1, "Repeated approval must not create duplicate attendance"

    async def test_wrong_intent_rejected(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(test_db_pool, intent="payment_due", context={})
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is False
        assert "not an attendance draft" in result["error"]

    async def test_draft_not_found(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        result = await finalize_attendance_draft(999999, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is False
        assert "not found" in result["error"]

    async def test_already_rejected_draft_cannot_be_approved(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(test_db_pool, status="rejected", context={})
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is False
        assert "rejected" in result["error"]

    async def test_unresolvable_employee_does_not_auto_create(self, test_db_pool):
        """Owner decision: a parser draft must not create an independent
        duplicate employee identity -- unresolvable means hard failure."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={"employee_name": "Totally Unknown Person", "att_date": "2026-05-05", "shift": "D"},
        )
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is False
        assert "could not be resolved" in result["error"]

        async with test_db_pool.acquire() as conn:
            emp_count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_employees WHERE employee_name='Totally Unknown Person'"
            )
        assert emp_count == 0, "No employee should have been auto-created"

    async def test_inactive_employee_rejected(self, test_db_pool, seed_employee):
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE wbom_employees SET status='Inactive' WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={"employee_id": seed_employee["employee_id"], "att_date": "2026-05-05", "shift": "D"},
        )
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is False
        assert "not Active" in result["error"]

    async def test_self_report_resolves_via_sender_phone(self, test_db_pool, seed_employee):
        """modules.attendance.handle_attendance_message's drafts carry no
        employee_id/name in meta at all -- only the sender's own phone."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            recipient=seed_employee["employee_mobile"],
            context={},
            source_module="attendance",
        )
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is True
        assert result["employee_id"] == seed_employee["employee_id"]

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT entry_source FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert row["entry_source"] == "whatsapp_escort"

    async def test_legacy_flat_meta_shape_still_works(self, test_db_pool, seed_employee):
        """Historical rows stored fields directly at the meta top level, no
        role/context/source_module wrapper -- must remain supported."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            flat_meta={"employee_id": seed_employee["employee_id"], "att_date": "2026-05-05", "shift": "N"},
        )
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is True
        assert result["employee_id"] == seed_employee["employee_id"]

    async def test_office_assistant_draft_maps_to_message_parser_entry_source(self, test_db_pool, seed_employee):
        """office_assistant's manually-typed draft (Owner decision, 2026-07-11)
        gets the same approval-gated entry_source as a parser draft -- both
        need admin sign-off before counting toward office payroll (see
        payroll/dry_run.py's OFFICE_PAYROLL_SOURCES)."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={"employee_id": seed_employee["employee_id"], "att_date": "2026-05-05", "duty_status": "Half Day"},
            source_module="office_assistant_manual",
        )
        result = await finalize_attendance_draft(draft_id, channel="frontend_review_queue", actor="admin1")
        assert result["ok"] is True

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, entry_source FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert row["entry_source"] == "message_parser"
        assert row["status"] == "Half Day"

    async def test_draft_without_duty_status_still_defaults_to_present(self, test_db_pool, seed_employee):
        """Backward compatibility: older parser/self-report drafts never
        carried a duty_status field and always meant a plain presence
        confirmation -- must keep defaulting to Present."""
        import app.database as db_module
        db_module._pool = test_db_pool
        from modules.attendance import finalize_attendance_draft

        draft_id = await _insert_attendance_draft(
            test_db_pool,
            context={"employee_id": seed_employee["employee_id"], "att_date": "2026-05-05", "shift": "D"},
            source_module="attendance_parser",
        )
        result = await finalize_attendance_draft(draft_id, channel="whatsapp_message", actor="admin1")
        assert result["ok"] is True

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_attendance WHERE employee_id=$1", seed_employee["employee_id"],
            )
        assert row["status"] == "Present"


class TestOfficePayrollEligibility:
    """Owner decision 2026-07-10/11: centralized office-staff classification."""

    def test_inactive_excluded(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Office Rahim", "designation": "Staff",
            "status": "Inactive", "basic_salary": 10000,
        })
        assert result["eligible"] is False
        assert result["reason"] == "Excluded: inactive employee"

    def test_escort_designation_excluded(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "SV Rasel", "designation": "Escort",
            "status": "Active", "basic_salary": 10000,
        })
        assert result["eligible"] is False
        assert result["reason"] == "Excluded: Escort-only employee"

    def test_zero_salary_excluded(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Office Karim", "designation": "Staff",
            "status": "Active", "basic_salary": 0,
        })
        assert result["eligible"] is False
        assert result["reason"] == "Excluded: missing or zero basic salary"

    def test_placeholder_record_excluded(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "OfficeExpance", "designation": "Labor",
            "status": "Active", "basic_salary": 5000,
        })
        assert result["eligible"] is False
        assert result["reason"] == "Excluded: bookkeeping/test/system record"

    def test_not_office_staff_excluded(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Manik Mea", "designation": "Cook",
            "status": "Active", "basic_salary": 8000,
        })
        assert result["eligible"] is False
        assert result["reason"] == "Excluded: not classified as office staff"

    def test_name_keyword_match_included(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Md Operation Hasan", "designation": "Cook",
            "status": "Active", "basic_salary": 12000,
        })
        assert result["eligible"] is True
        assert result["is_office_staff"] is True

    def test_designation_match_included(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Kamal Uddin", "designation": "Supervisor",
            "status": "Active", "basic_salary": 15000,
        })
        assert result["eligible"] is True

    def test_al_aqsa_keyword_anywhere_in_name(self):
        from modules.payroll.eligibility import classify_office_staff
        result = classify_office_staff({
            "employee_name": "Md Hasan Al-Aqsa", "designation": "Cook",
            "status": "Active", "basic_salary": 9000,
        })
        assert result["eligible"] is True
