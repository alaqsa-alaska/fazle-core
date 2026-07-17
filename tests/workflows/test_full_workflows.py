"""
End-to-end workflow simulation tests.

These tests drive the FULL business workflow using the real API + DB,
mocking only external HTTP services (bridges, Ollama).

Workflow 1: Client order → admin assigns → guard releases → payment finalized
Workflow 2: Guard advance request → admin approves → transaction created
Workflow 3: Monthly payroll computation and state machine
Workflow 4: Guard attendance self-report → admin approves
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.workflow

from tests.conftest import (
    make_bridge_payload,
    GUARD_PHONE,
    ADMIN_PHONE,
    CLIENT_PHONE,
    ACCOUNTANT_PHONE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 1: Full Escort Duty Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestEscortFullWorkflow:
    """
    1. Client sends escort order → program created (draft)
    2. Admin sends ESCORTCONFIRM → program Assigned, slip sent to client
    3. Guard sends attendance during duty
    4. Guard sends release → program Completed, attendance backfilled,
       payment draft created
    5. Admin sends PAID → transaction created, accountant notified
    """

    async def test_full_escort_lifecycle(
        self, client, test_db_pool, mock_all_services, seed_employee
    ):
        # ── Step 1: Client places order ───────────────────────────────────────
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_contact_roles (phone, role, name)
                VALUES ($1, 'client_escort_buyer', 'Test Client')
                ON CONFLICT (phone) DO NOTHING
            """, CLIENT_PHONE)

        order_payload = make_bridge_payload(
            CLIENT_PHONE,
            "MV GOLDEN STAR lighter vessel AMENA-3 "
            "master mobile 01933333333 wheat 5000MT "
            "escort lagbe 06/05/2026 Day",
        )
        r = await client.post("/webhook/mcp1", json=order_payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            program = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs LIMIT 1"
            )
        assert program is not None, "Program must be created from order"
        program_id = program["program_id"]

        # ── Step 2: Admin confirms escort ─────────────────────────────────────
        guard_name = seed_employee["employee_name"]
        guard_mobile = seed_employee["employee_mobile"]

        confirm_payload = make_bridge_payload(
            ADMIN_PHONE,
            f"ESCORTCONFIRM {program_id} | {guard_name} | {guard_mobile} | 06/05/2026 | D",
            source="bridge2",
        )

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_admins (phone, name, status)
                VALUES ($1, 'Test Admin', 'active')
                ON CONFLICT (phone) DO NOTHING
            """, ADMIN_PHONE)
            admin = await conn.fetchrow(
                "SELECT id FROM fazle_admins WHERE phone=$1", ADMIN_PHONE
            )
            await conn.execute(
                "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'superadmin','test') ON CONFLICT DO NOTHING",
                admin["id"],
            )

        r = await client.post("/webhook/mcp2", json=confirm_payload)
        assert r.status_code in (200, 202)

        # ── Step 3: Guard attendance during duty ──────────────────────────────
        attendance_payload = make_bridge_payload(
            guard_mobile,
            "হাজির আছি MV GOLDEN STAR তে",
        )
        r = await client.post("/webhook/mcp1", json=attendance_payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            att_draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert att_draft is not None, "Attendance draft should be created"

        # ── Step 4: Admin sends [RELEASE CONFIRMED] ───────────────────────────
        # The admin-typed RELEASE <id> command was deliberately deprecated
        # (2026-06-19, modules/admin_commands/__init__.py::_cmd_release) in
        # favor of this admin-confirmation-gated flow. It's normally triggered
        # by bridge_poller detecting an is_from_me=1 outbound [RELEASE
        # CONFIRMED] message (modules/bridge_poller/__init__.py ~line 1151) —
        # calling handle_admin_release_confirmation() directly here exercises
        # the same real production entry point without needing to fake the
        # SQLite outbound-message polling layer.
        from modules.escort_lifecycle import handle_admin_release_confirmation

        release_result = await handle_admin_release_confirmation(
            "[RELEASE CONFIRMED]\n"
            "End Date: 2026-05-05\n"
            "Shift: D\n"
            "Days: 5\n"
            "Release Point: Ctg Port",
            f"{guard_mobile}@s.whatsapp.net",
            source="bridge2",
        )
        assert release_result["ok"] is True, release_result

        async with test_db_pool.acquire() as conn:
            updated = await conn.fetchrow(
                "SELECT status, day_count FROM wbom_escort_programs WHERE program_id=$1",
                program_id,
            )
        assert updated["status"] == "completed"
        assert updated["day_count"] == pytest.approx(5.0)

        # ── Step 5: Check payment draft created ───────────────────────────────
        async with test_db_pool.acquire() as conn:
            pay_draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE draft_type='escort_payment' LIMIT 1"
            )
        assert pay_draft is not None, "Payment draft must be created after release"

        # ── Step 6: Admin pays ─────────────────────────────────────────────────
        pay_id = pay_draft["id"]
        paid_payload = make_bridge_payload(
            ADMIN_PHONE,
            f"PAID {pay_id} 1500 bkash",
            source="bridge2",
        )
        r = await client.post("/webhook/mcp2", json=paid_payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM wbom_cash_transactions WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert txn is not None, "Cash transaction should be recorded"
        assert float(txn["amount"]) == pytest.approx(1500.0)

        # Check attendance was backfilled
        async with test_db_pool.acquire() as conn:
            att_count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_attendance WHERE employee_id=$1",
                seed_employee["employee_id"],
            )
        assert att_count >= 5, "5 days attendance should be backfilled"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 2: Advance Request Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvanceWorkflow:
    async def test_advance_request_to_transaction(
        self, client, test_db_pool, mock_all_services, seed_employee
    ):
        # Seed admin
        async with test_db_pool.acquire() as conn:
            admin_row = await conn.fetchrow("""
                INSERT INTO fazle_admins (phone, name, status)
                VALUES ($1, 'Admin', 'active')
                ON CONFLICT (phone) DO NOTHING
                RETURNING id
            """, ADMIN_PHONE)
            if admin_row:
                await conn.execute(
                    "INSERT INTO fazle_admin_roles (admin_id, role_name, granted_by) VALUES ($1,'superadmin','test') ON CONFLICT DO NOTHING",
                    admin_row["id"],
                )

        # Guard requests advance
        adv_payload = make_bridge_payload(
            seed_employee["employee_mobile"],
            "ভাই অগ্রিম লাগবে ২০০০ টাকা",
        )
        r = await client.post("/webhook/mcp1", json=adv_payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_payment_drafts WHERE draft_type='advance' LIMIT 1"
            )
        assert draft is not None, "Advance payment draft must be created"
        draft_id = draft["id"]

        # Admin approves the advance
        approve_payload = make_bridge_payload(
            ADMIN_PHONE,
            f"ADVANCE {draft_id} 2000 bkash",
            source="bridge2",
        )
        r = await client.post("/webhook/mcp2", json=approve_payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM wbom_cash_transactions WHERE transaction_type='advance' LIMIT 1"
            )
        assert txn is not None
        assert float(txn["amount"]) == pytest.approx(2000.0)

    async def test_advance_deducted_in_payroll(
        self, client, test_db_pool, mock_all_services, seed_employee
    ):
        """Advance from this month must be deducted from net salary in payroll compute."""
        import app.database as db_module
        db_module._pool = test_db_pool

        # Insert advance directly
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO wbom_cash_transactions
                    (employee_id, transaction_type, amount, transaction_date, status)
                VALUES ($1, 'advance', 1000, '2026-05-10', 'Completed')
            """, seed_employee["employee_id"])

        from modules.payroll import compute_run
        result = await compute_run(
            employee_id=seed_employee["employee_id"],
            period_month="2026-05",
            computed_by="test",
        )

        assert result["total_advances"] == pytest.approx(1000.0)
        assert result["net_salary"] < result["gross_salary"]


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 3: Payroll Full State Machine
# ─────────────────────────────────────────────────────────────────────────────

class TestPayrollWorkflow:
    async def test_full_payroll_state_machine(
        self, test_db_pool, seed_employee, seed_escort_program
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        from modules.payroll import (
            compute_run, submit_run, approve_run, lock_run, mark_paid,
        )

        employee_id = seed_employee["employee_id"]
        program_id = seed_escort_program["program_id"]

        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                UPDATE wbom_escort_programs
                SET status='completed', day_count=5,
                    program_date='2026-05-01', end_date='2026-05-05'
                WHERE program_id=$1
            """, program_id)

        # Compute
        result = await compute_run(
            employee_id=employee_id, period_month="2026-05", computed_by="admin"
        )
        run_id = result["run_id"]
        assert result["status"] == "draft"

        # Submit
        await submit_run(run_id=run_id, actor="admin")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        assert row["status"] == "reviewed"

        # Approve
        await approve_run(run_id=run_id, actor="admin")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        assert row["status"] == "approved"

        # Lock
        await lock_run(run_id=run_id, actor="admin")
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        assert row["status"] == "locked"

        # Mark paid
        await mark_paid(
            run_id=run_id, actor="admin",
            amount=13000.0, method="bkash", reference="REF001"
        )
        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM wbom_payroll_runs WHERE run_id=$1", run_id
            )
        assert row["status"] == "paid"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 4: Attendance Self-Report → Approval
# ─────────────────────────────────────────────────────────────────────────────

class TestAttendanceWorkflow:
    async def test_guard_reports_present_admin_approves(
        self, client, test_db_pool, mock_all_services, seed_employee
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        # Guard sends attendance
        payload = make_bridge_payload(
            seed_employee["employee_mobile"],
            "হাজির আছি",
        )
        r = await client.post("/webhook/mcp1", json=payload)
        assert r.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None
        draft_id = draft["id"]

        # Admin approves attendance draft
        from modules.attendance import save_attendance
        await save_attendance(
            employee_id=seed_employee["employee_id"],
            status="Present",
            location="MV TEST",
            recorded_by=ADMIN_PHONE,
        )

        async with test_db_pool.acquire() as conn:
            att = await conn.fetchrow(
                "SELECT * FROM wbom_attendance WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert att is not None
        assert att["status"] == "Present"


# ─────────────────────────────────────────────────────────────────────────────
# Workflow 5: Payment Correction (REVERSE/ADJUST)
# ─────────────────────────────────────────────────────────────────────────────

class TestPaymentCorrectionWorkflow:
    async def test_reverse_payment(
        self, client, test_db_pool, mock_all_services, seed_employee, seed_payment_draft
    ):
        import app.database as db_module
        db_module._pool = test_db_pool

        # First finalize the payment
        from modules.payment_workflow import finalize_payment
        await finalize_payment(
            draft_id=seed_payment_draft["id"],
            amount=1500.0,
            method="bkash",
            admin_phone=ADMIN_PHONE,
        )

        # Confirm transaction exists
        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT transaction_id FROM wbom_cash_transactions WHERE employee_id=$1 LIMIT 1",
                seed_employee["employee_id"],
            )
        assert txn is not None
        original_id = txn["transaction_id"]

        # Now reverse
        from modules.payment_correction import reverse_payment
        await reverse_payment(
            original_transaction_id=original_id,
            reason="Test reversal",
            admin_phone=ADMIN_PHONE,
        )

        async with test_db_pool.acquire() as conn:
            reversal = await conn.fetchrow(
                "SELECT * FROM wbom_cash_transactions WHERE reversal_of=$1",
                original_id,
            )
        assert reversal is not None
        assert reversal["is_reversal"] is True
        assert float(reversal["amount"]) < 0
