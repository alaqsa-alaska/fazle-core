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

from datetime import date, timedelta

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

        # The order text's "06/05/2026" date hint is stored in
        # remarks.date_hint but never parsed into wbom_escort_programs
        # .start_date (confirmed empirically 2026-07-18: start_date stays
        # NULL). _handle_release_event_tx() falls back to program_date,
        # which defaults to the row's insertion date -- i.e. "today" at
        # whatever real-world date this test runs. The previous hardcoded
        # "End Date: 2026-05-05" was therefore always in the past relative
        # to program_date and always raised ValueError; it wasn't a
        # once-correct date that went stale, it could never have passed.
        # Computed relative to date.today() so the test has no fixed date
        # dependency. +4 days at shift N gives exactly 10 shifts = 5.0 days
        # per modules.escort_roster.calculations.count_shifts()/
        # shifts_to_days(), matching this test's "Days: 5" /
        # day_count==5.0 assertion below.
        release_end_date = date.today() + timedelta(days=4)
        release_result = await handle_admin_release_confirmation(
            "[RELEASE CONFIRMED]\n"
            f"End Date: {release_end_date.isoformat()}\n"
            "Shift: N\n"
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

        # fpe_cash_transactions is the only canonical cash ledger table
        # (Owner Directive 2026-06-29, CLAUDE.md) -- finalize_payment() never
        # writes wbom_cash_transactions (legacy). Matched by phone rather than
        # employee_id since fpe_cash_transactions.employee_id FKs to
        # fpe_employees(id), a distinct ID space from wbom_employees.
        # employee_phone is stored normalized (leading "88" country code
        # stripped, e.g. "01811111111"), while seed_employee's fixture value
        # carries the "88" prefix (confirmed empirically 2026-07-18).
        normalized_mobile = seed_employee["employee_mobile"].removeprefix("88")
        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM fpe_cash_transactions WHERE employee_phone=$1 LIMIT 1",
                normalized_mobile,
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

        # Guard requests advance. modules.employee_conversation now runs this
        # as a strict 4-turn conversation state machine (trigger → amount →
        # payout → explicit "confirm") before it writes a
        # fazle_payment_drafts row -- a single combined "trigger + amount"
        # message only satisfies the trigger step and leaves the session
        # waiting for amount as a separate turn (confirmed empirically
        # 2026-07-18: a combined message never produced a draft even after
        # follow-up payout+confirm turns). Mirrors the exact 4-message
        # sequence in tests/unit/test_sprint3a_employee_conversation.py
        # ::Test4VerificationCompleteDraftCreated, driven through the real
        # /webhook/mcp1 HTTP path instead of calling the handler directly.
        for turn_text in ("অগ্রিম চাই", "২০০০", "বিকাশ 01712345678", "confirm"):
            turn_payload = make_bridge_payload(seed_employee["employee_mobile"], turn_text)
            r = await client.post("/webhook/mcp1", json=turn_payload)
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

        # fpe_cash_transactions is the only canonical cash ledger table
        # (Owner Directive 2026-06-29) -- ADVANCE approval routes through
        # admin_commands._cmd_paid() -> finalize_payment(), which never
        # writes wbom_cash_transactions (legacy).
        async with test_db_pool.acquire() as conn:
            txn = await conn.fetchrow(
                "SELECT * FROM fpe_cash_transactions WHERE txn_category='advance' LIMIT 1"
            )
        assert txn is not None
        assert float(txn["amount"]) == pytest.approx(2000.0)

    async def test_advance_deducted_in_payroll(
        self, client, test_db_pool, mock_all_services, seed_employee
    ):
        """Advance from this month must be deducted from net salary in payroll compute."""
        import app.database as db_module
        db_module._pool = test_db_pool

        # fpe_cash_transactions is the only canonical cash ledger table
        # (Owner Directive 2026-06-29) -- wbom_cash_transactions is legacy
        # and compute_run() never reads it. employee_id here FKs to
        # fpe_employees(id), NOT wbom_employees(employee_id) -- a real
        # fpe_employees row linked via wbom_employee_id is required, matching
        # the pattern in tests/unit/test_payroll.py::TestMarkPaidLedger.
        async with test_db_pool.acquire() as conn:
            # Deliberately consume an id first so fpe_employees.id cannot
            # coincidentally equal seed_employee's wbom employee_id in a
            # fresh truncated test DB (both sequences would otherwise start
            # at 1) -- that coincidence would silently mask the real bug
            # this test is meant to catch. See the KNOWN BUG note below.
            await conn.execute("""
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, status)
                VALUES ('EMP-DECOY', 'Decoy', 'decoy', 'inactive')
            """)
            fpe_row = await conn.fetchrow("""
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, primary_phone,
                     status, wbom_employee_id)
                VALUES ('EMP-ADV1', $1, $2, $3, 'active', $4)
                RETURNING id
            """, seed_employee["employee_name"],
                seed_employee["employee_name"].lower().strip(),
                seed_employee["employee_mobile"],
                seed_employee["employee_id"])
            fpe_id = fpe_row["id"]
            assert fpe_id != seed_employee["employee_id"], (
                "test setup invariant broken: fpe_id must differ from the "
                "wbom employee_id for this test to be meaningful"
            )
            await conn.execute("""
                INSERT INTO fpe_cash_transactions
                    (txn_ref, employee_id, amount, txn_date, txn_category, transaction_status)
                VALUES ('test-advance-1', $1, 1000, '2026-05-10', 'advance', 'final')
            """, fpe_id)

        from modules.payroll import compute_run
        result = await compute_run(
            employee_id=seed_employee["employee_id"],
            period_year=2026,
            period_month=5,
            computed_by="test",
        )

        # FIXED 2026-07-18 (was a KNOWN BUG documented in Phase 1G):
        # compute_run()'s advances query now resolves the linked, active
        # fpe_employees row via wbom_employee_id first (matching
        # mark_paid()'s existing pattern in the same file) before querying
        # fpe_cash_transactions.employee_id -- see
        # management_decisions.md's payroll advance identity fix record.
        # Seeded via a correctly wbom_employee_id-linked fpe_employees row
        # (not a coincidental id match, per the decoy-row invariant above),
        # so this assertion now genuinely proves the fix rather than
        # accidentally passing.
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
            # mark_paid() fails closed without an active fpe_employees row
            # linked via wbom_employee_id (2026-07-15 fix, see
            # knowledge_base/06_developer_system/payroll_module.md) --
            # matches the pattern in
            # tests/unit/test_payroll.py::TestMarkPaidLedger._link_fpe_employee.
            await conn.execute("""
                INSERT INTO fpe_employees
                    (employee_code, full_name, name_normalized, primary_phone,
                     status, wbom_employee_id)
                VALUES ('EMP-PAYROLL1', $1, $2, $3, 'active', $4)
            """, seed_employee["employee_name"],
                seed_employee["employee_name"].lower().strip(),
                seed_employee["employee_mobile"], employee_id)

        # Compute
        result = await compute_run(
            employee_id=employee_id, period_year=2026, period_month=5, computed_by="admin"
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
            attendance_date=date.today(),
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

        # First finalize the payment. finalize_payment()'s current contract
        # is (draft_id, approved_amount, method) -- no admin_phone param --
        # and it sets fazle_payment_drafts.status='sent' + .transaction_id
        # on success.
        from modules.payment_workflow import finalize_payment
        result = await finalize_payment(
            draft_id=seed_payment_draft["id"],
            approved_amount=1500.0,
            method="bkash",
        )
        assert "error" not in result, result

        # Confirm the canonical transaction exists. fpe_cash_transactions is
        # the only canonical cash ledger table (Owner Directive 2026-06-29);
        # wbom_cash_transactions is legacy and finalize_payment() never
        # writes it.
        async with test_db_pool.acquire() as conn:
            draft_row = await conn.fetchrow(
                "SELECT transaction_id FROM fazle_payment_drafts WHERE id=$1",
                seed_payment_draft["id"],
            )
        assert draft_row is not None and draft_row["transaction_id"] is not None
        original_id = draft_row["transaction_id"]

        # Now reverse. reverse_payment()'s current contract is keyed by the
        # payment draft, not a raw transaction id: (draft_id, admin_phone,
        # reason). It looks up the canonical transaction via
        # fazle_payment_drafts.transaction_id internally.
        from modules.payment_correction import reverse_payment
        rev_result = await reverse_payment(
            draft_id=seed_payment_draft["id"],
            admin_phone=ADMIN_PHONE,
            reason="Test reversal",
        )
        assert rev_result.get("ok") is True, rev_result

        async with test_db_pool.acquire() as conn:
            reversal = await conn.fetchrow(
                "SELECT * FROM fpe_cash_transactions WHERE reversed_txn_id=$1",
                original_id,
            )
        assert reversal is not None
        assert reversal["is_reversal"] is True
        assert float(reversal["amount"]) < 0
