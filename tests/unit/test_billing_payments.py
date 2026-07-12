"""Unit tests — modules/client_billing/payments (Phase 2B, Commit D)"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _use_pool(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool


async def _make_contact(conn, phone="8801670500001"):
    return await conn.fetchval(
        "INSERT INTO wbom_contacts (whatsapp_number) VALUES ($1) RETURNING contact_id", phone
    )


_bill_seq = 0


async def _make_bill(conn, contact_id, total_amount=1000, payment_status="Pending"):
    global _bill_seq
    _bill_seq += 1
    program_id = await conn.fetchval(
        "INSERT INTO wbom_escort_programs (status, contact_id) VALUES ('completed', $1) "
        "RETURNING program_id",
        contact_id,
    )
    return await conn.fetchrow(
        """
        INSERT INTO wbom_billing_records
            (program_id, contact_id, bill_date, bill_number, service_charge, total_amount, payment_status)
        VALUES ($1, $2, CURRENT_DATE, $3, $4, $4, $5)
        RETURNING *
        """,
        program_id, contact_id, f"AQS-TEST-{_bill_seq}", total_amount, payment_status,
    )


class TestMarkBillPaid:
    async def test_marks_pending_bill_paid(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import mark_bill_paid

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn)
            bill = await _make_bill(conn, contact_id, total_amount=1500)

        result = await mark_bill_paid(
            bill["bill_id"], "test-admin",
            payment_method="Bkash", payment_reference="TX123",
        )

        assert result["payment_status"] == "Paid"
        assert float(result["paid_amount"]) == 1500.0
        assert result["payment_method"] == "Bkash"
        assert result["payment_reference"] == "TX123"
        assert result["payment_date"] is not None

    async def test_rejects_already_paid_bill(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import mark_bill_paid
        from fastapi import HTTPException

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500002")
            bill = await _make_bill(conn, contact_id, payment_status="Paid")

        with pytest.raises(HTTPException) as exc_info:
            await mark_bill_paid(bill["bill_id"], "test-admin")
        assert exc_info.value.status_code == 409

    async def test_rejects_unknown_bill(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import mark_bill_paid
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await mark_bill_paid(999999, "test-admin")
        assert exc_info.value.status_code == 404

    async def test_writes_audit_log_entry(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import mark_bill_paid, get_bill_payment_log

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500003")
            bill = await _make_bill(conn, contact_id)

        await mark_bill_paid(bill["bill_id"], "test-admin", remarks="paid via office visit")

        log = await get_bill_payment_log(bill["bill_id"])
        assert len(log) == 1
        assert log[0]["action"] == "paid"
        assert log[0]["actor"] == "test-admin"
        assert log[0]["from_status"] == "Pending"
        assert log[0]["to_status"] == "Paid"
        assert log[0]["reason"] == "paid via office visit"


class TestOutstandingBalance:
    async def test_list_outstanding_bills_excludes_paid(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import list_outstanding_bills

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500004")
            await _make_bill(conn, contact_id, total_amount=500, payment_status="Pending")
            await _make_bill(conn, contact_id, total_amount=700, payment_status="Paid")

        bills = await list_outstanding_bills(contact_id=contact_id)
        assert len(bills) == 1
        assert float(bills[0]["total_amount"]) == 500.0

    async def test_outstanding_summary_aggregates_per_client(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import get_outstanding_balance_summary

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500005")
            await _make_bill(conn, contact_id, total_amount=300, payment_status="Pending")
            await _make_bill(conn, contact_id, total_amount=400, payment_status="Pending")

        summary = await get_outstanding_balance_summary()
        match = next((s for s in summary if s["contact_id"] == contact_id), None)
        assert match is not None
        assert match["pending_bill_count"] == 2
        assert float(match["outstanding_amount"]) == 700.0

    async def test_dashboard_summary_totals(self, test_db_pool):
        _use_pool(test_db_pool)
        from modules.client_billing.payments import get_billing_dashboard_summary

        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500006")
            await _make_bill(conn, contact_id, total_amount=100, payment_status="Pending")
            await _make_bill(conn, contact_id, total_amount=200, payment_status="Paid")

        summary = await get_billing_dashboard_summary()
        assert summary["pending_bill_count"] >= 1
        assert summary["paid_bill_count"] >= 1
        assert float(summary["outstanding_amount"]) >= 100.0
        assert float(summary["collected_amount"]) >= 200.0


class TestMarkPaidRoute:
    async def test_mark_paid_route_rbac_gated_without_key(self, client):
        r = await client.post(
            "/admin/bills/999999/mark-paid", json={}, headers={"X-Internal-Key": ""}
        )
        assert r.status_code == 403

    async def test_mark_paid_route_404s_for_unknown_bill(self, client):
        # client fixture is pre-authenticated with the internal key — this
        # exercises the real "not found" path, not RBAC.
        r = await client.post("/admin/bills/999999/mark-paid", json={})
        assert r.status_code == 404

    async def test_mark_paid_route_end_to_end(self, client, test_db_pool):
        _use_pool(test_db_pool)
        async with test_db_pool.acquire() as conn:
            contact_id = await _make_contact(conn, "8801670500007")
            bill = await _make_bill(conn, contact_id, total_amount=2000)

        r = await client.post(
            f"/admin/bills/{bill['bill_id']}/mark-paid",
            json={"payment_method": "Nagad", "payment_reference": "REF-1"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["payment_status"] == "Paid"
        assert body["payment_method"] == "Nagad"
