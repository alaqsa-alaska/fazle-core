"""
Fazle Core — Client Bill Payment Recording (Phase 2B, Commit D)

Mark-paid action, outstanding-balance views, and an append-only audit log
(wbom_billing_payment_log, migration 044) mirroring wbom_payroll_approval_log's
proven shape. Payment is binary per Owner Decision #3 (2026-07-10):
payment_status is 'Pending'/'Paid' only — no partial payments, so
paid_amount always equals the bill's total_amount at mark-paid time.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import HTTPException

from app.database import fetch_all, fetch_one, get_pool


async def mark_bill_paid(
    bill_id: int,
    actor: str,
    *,
    payment_method: Optional[str] = None,
    payment_reference: Optional[str] = None,
    remarks: Optional[str] = None,
) -> dict:
    """Idempotent guard: rejects (409) if the bill is already Paid rather
    than silently re-recording — a second mark-paid call for the same bill
    is almost always an operator mistake, not an intentional re-payment
    (Owner Decision #3 has no 'Partial'/re-payment concept)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            bill = await conn.fetchrow(
                "SELECT * FROM wbom_billing_records WHERE bill_id = $1 FOR UPDATE", bill_id
            )
            if not bill:
                raise HTTPException(status_code=404, detail=f"bill_id {bill_id} not found")
            if bill["payment_status"] == "Paid":
                raise HTTPException(
                    status_code=409,
                    detail=f"bill_id {bill_id} is already Paid (paid {bill['payment_date']})",
                )

            row = await conn.fetchrow(
                """
                UPDATE wbom_billing_records
                SET payment_status = 'Paid',
                    payment_date = CURRENT_DATE,
                    paid_amount = total_amount,
                    payment_method = $2,
                    payment_reference = $3,
                    updated_at = now()
                WHERE bill_id = $1
                RETURNING *
                """,
                bill_id, payment_method, payment_reference,
            )
            await conn.execute(
                """
                INSERT INTO wbom_billing_payment_log
                    (bill_id, action, actor, from_status, to_status, reason, payload_json)
                VALUES ($1, 'paid', $2, 'Pending', 'Paid', $3, $4::jsonb)
                """,
                bill_id, actor, remarks,
                json.dumps({
                    "paid_amount": float(row["total_amount"]),
                    "payment_method": payment_method,
                    "payment_reference": payment_reference,
                }),
            )
    return dict(row)


async def get_bill_payment_log(bill_id: int) -> list[dict]:
    rows = await fetch_all(
        "SELECT * FROM wbom_billing_payment_log WHERE bill_id = $1 ORDER BY log_id DESC",
        bill_id,
    )
    return [dict(r) for r in rows]


async def list_outstanding_bills(contact_id: Optional[int] = None) -> list[dict]:
    if contact_id is not None:
        rows = await fetch_all(
            """
            SELECT b.*, cbp.client_name
            FROM wbom_billing_records b
            LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
            WHERE b.payment_status = 'Pending' AND b.contact_id = $1
            ORDER BY b.bill_date ASC, b.bill_id ASC
            """,
            contact_id,
        )
    else:
        rows = await fetch_all(
            """
            SELECT b.*, cbp.client_name
            FROM wbom_billing_records b
            LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
            WHERE b.payment_status = 'Pending'
            ORDER BY b.bill_date ASC, b.bill_id ASC
            """
        )
    return [dict(r) for r in rows]


async def get_outstanding_balance_summary() -> list[dict]:
    """Per-client aggregation of unpaid bills — the outstanding-balance view."""
    rows = await fetch_all(
        """
        SELECT b.contact_id,
               COALESCE(cbp.client_name, c.display_name, 'Unknown') AS client_name,
               COUNT(*) AS pending_bill_count,
               SUM(b.total_amount) AS outstanding_amount,
               MIN(b.bill_date) AS oldest_pending_bill_date
        FROM wbom_billing_records b
        LEFT JOIN wbom_client_billing_profiles cbp ON cbp.contact_id = b.contact_id
        LEFT JOIN wbom_contacts c ON c.contact_id = b.contact_id
        WHERE b.payment_status = 'Pending'
        GROUP BY b.contact_id, cbp.client_name, c.display_name
        ORDER BY outstanding_amount DESC
        """
    )
    return [dict(r) for r in rows]


async def get_billing_dashboard_summary() -> dict:
    """Aggregate totals for the admin dashboard overview."""
    row = await fetch_one(
        """
        SELECT
            COUNT(*) FILTER (WHERE payment_status = 'Pending') AS pending_bill_count,
            COUNT(*) FILTER (WHERE payment_status = 'Paid') AS paid_bill_count,
            COALESCE(SUM(total_amount) FILTER (WHERE payment_status = 'Pending'), 0) AS outstanding_amount,
            COALESCE(SUM(total_amount) FILTER (WHERE payment_status = 'Paid'), 0) AS collected_amount,
            COUNT(*) AS total_bill_count
        FROM wbom_billing_records
        """
    )
    return dict(row) if row else {
        "pending_bill_count": 0, "paid_bill_count": 0,
        "outstanding_amount": 0, "collected_amount": 0, "total_bill_count": 0,
    }
