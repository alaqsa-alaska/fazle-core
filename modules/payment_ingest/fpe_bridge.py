"""
WBOM → FPE bridge for payment ingest.

Owner Directive (2026-06-29):
  - fpe_cash_transactions is the ONLY canonical cash transaction table.
  - payment_ingest must no longer write new rows to wbom_cash_transactions.
  - WBOM employee lookup rules stay unchanged; we only map the resulting WBOM
    employee to an FPE employee before calling create_transaction().
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.database import fetch_one, fetch_val
from modules.fazle_payroll_engine.accounting import create_transaction
from modules.fazle_payroll_engine.models import (
    PayoutMethod,
    TransactionCreateRequest,
    TxnCategory,
)
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone as _norm_phone
from modules.fazle_payroll_engine.payment_event import (
    payment_event_from_whatsapp,
    payment_event_to_request,
)


async def resolve_fpe_employee_for_wbom_employee(
    wbom_employee_id: int,
    wbom_employee_name: str,
    wbom_employee_mobile: str,
) -> int:
    """
    Return the FPE employee id linked to a WBOM employee.
    If no link exists, create a minimal FPE employee and link it.
    """
    # 1. Existing link
    row = await fetch_one(
        "SELECT id FROM fpe_employees WHERE wbom_employee_id = $1",
        wbom_employee_id,
    )
    if row:
        return int(row["id"])

    # 2. Match by canonical phone (last 10 digits) on existing FPE employee
    canonical = _norm_phone(wbom_employee_mobile)
    if canonical:
        row = await fetch_one(
            "SELECT id FROM fpe_employees "
            "WHERE primary_phone = $1 OR employee_id_phone = $1 "
            "LIMIT 1",
            canonical,
        )
        if row:
            fpe_id = int(row["id"])
            await fetch_one(
                """UPDATE fpe_employees
                   SET wbom_employee_id = $1,
                       resolution_status = CASE WHEN resolution_status = 'unresolved'
                                                 THEN 'resolved' ELSE resolution_status END
                   WHERE id = $2""",
                wbom_employee_id,
                fpe_id,
            )
            return fpe_id

    # 3. Create minimal FPE employee and link it
    name_normalized = wbom_employee_name.strip().lower()
    fpe_id = await fetch_val(
        """INSERT INTO fpe_employees
               (full_name, name_normalized, primary_phone, employee_id_phone,
                wbom_employee_id, status, created_source, resolution_status)
           VALUES ($1, $2, $3, $3, $4, 'active', 'payment_ingest_bridge', 'resolved')
           ON CONFLICT (primary_phone) WHERE primary_phone IS NOT NULL DO UPDATE
              SET wbom_employee_id = COALESCE(fpe_employees.wbom_employee_id, EXCLUDED.wbom_employee_id),
                  status = 'active',
                  resolution_status = CASE WHEN fpe_employees.resolution_status = 'unresolved'
                                            THEN 'resolved' ELSE fpe_employees.resolution_status END
           RETURNING id""",
        wbom_employee_name,
        name_normalized,
        canonical,
        wbom_employee_id,
    )
    return int(fpe_id)


async def create_fpe_transaction_from_ingest(
    *,
    wbom_employee_id: int,
    wbom_employee_name: str,
    wbom_employee_mobile: str,
    amount: Decimal,
    payout_method: str,
    payout_phone: Optional[str] = None,
    employee_id_phone: Optional[str] = None,
    txn_date: date,
    txn_category: TxnCategory = TxnCategory.salary,
    source_message_text: Optional[str] = None,
    source_message_id: Optional[str] = None,
    source_channel: Optional[str] = None,
    fpe_wa_message_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict:
    """
    Create a canonical FPE cash transaction from a payment ingest event.
    Returns {"transaction_id": int, "fpe_employee_id": int, "txn_ref": str}.
    """
    fpe_employee_id = await resolve_fpe_employee_for_wbom_employee(
        wbom_employee_id,
        wbom_employee_name,
        wbom_employee_mobile,
    )

    method = PayoutMethod(payout_method) if payout_method in PayoutMethod._value2member_map_ else PayoutMethod.unknown

    event = payment_event_from_whatsapp(
        employee_id=fpe_employee_id,
        employee_name_raw=wbom_employee_name,
        employee_id_phone=employee_id_phone or wbom_employee_mobile,
        employee_phone=_norm_phone(wbom_employee_mobile),
        payout_phone=payout_phone or wbom_employee_mobile,
        payout_method=method,
        amount=amount,
        txn_date=txn_date,
        txn_category=txn_category,
        fpe_wa_message_id=fpe_wa_message_id,
        wa_message_id=source_message_id,
        source_channel=source_channel,
        source_message_text=source_message_text,
        created_by="payment_ingest_bridge",
        metadata=metadata or {},
    )

    req = payment_event_to_request(event)
    txn = await create_transaction(req)

    return {
        "transaction_id": txn.id,
        "fpe_employee_id": fpe_employee_id,
        "txn_ref": txn.txn_ref,
    }
