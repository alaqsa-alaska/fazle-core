"""
Fazle Core — WBOM → FPE Historical Migration Adapter  (Phase 7)
================================================================

Owner Directive (2026-06-29):
  - fpe_cash_transactions is the ONLY canonical cash transaction table.
  - wbom_cash_transactions becomes legacy archive / source reference only.
  - No WBOM data is ever mutated or deleted.
  - Migration is idempotent via create_transaction() txn_ref.

This module backfills historical WBOM cash transactions into fpe_cash_transactions
using the canonical PaymentEvent → create_transaction() path.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from app.database import fetch_one, fetch_all, execute, fetch_val
from modules.fazle_payroll_engine.employee import match_or_create_employee
from modules.fazle_payroll_engine.accounting import create_transaction
from modules.fazle_payroll_engine.models import (
    PayoutMethod,
    PaymentSource,
    TransactionStatus,
    TxnCategory,
)
from modules.fazle_payroll_engine.payment_event import (
    payment_event_from_whatsapp,
    payment_event_to_request,
)
from shared.phone import normalize_bd_phone

log = logging.getLogger("fazle.wbom_fpe_sync")

# Method normalization: WBOM uses mixed-case strings; FPE expects lowercase enum
_METHOD_MAP: dict[str, str] = {
    "bkash": "bkash",
    "bKash": "bkash",
    "nagad": "nagad",
    "Nagad": "nagad",
    "cash": "cash",
    "Cash": "cash",
    "rocket": "rocket",
    "Rocket": "rocket",
    "bank": "bank",
}


def _norm_method(raw: Optional[str]) -> str:
    if not raw:
        return "cash"
    return _METHOD_MAP.get(raw.strip(), raw.strip().lower())


def _wbom_type_to_category(txn_type: Optional[str]) -> TxnCategory:
    """Map WBOM transaction_type to FPE TxnCategory."""
    t = (txn_type or "").lower()
    if "advance" in t:
        return TxnCategory.advance
    if "deduction" in t or "food" in t or "conveyance" in t:
        return TxnCategory.deduction
    if "bonus" in t:
        return TxnCategory.bonus
    if "correction" in t or "reverse" in t:
        return TxnCategory.correction
    return TxnCategory.salary


async def sync_wbom_transaction(wbom_txn_id: int) -> Optional[dict]:
    """
    Read one row from wbom_cash_transactions and create a matching row in
    fpe_cash_transactions if one does not already exist.

    Returns the FPE transaction dict (new or existing), or None on error.
    Never raises.
    """
    try:
        wbom = await fetch_one(
            """
            SELECT ct.transaction_id, ct.employee_id, ct.amount, ct.transaction_type,
                   ct.transaction_date, ct.payment_method, ct.reference_number,
                   ct.remarks, ct.created_at,
                   e.employee_name, e.employee_mobile, e.bkash_number, e.nagad_number
            FROM   wbom_cash_transactions ct
            JOIN   wbom_employees e ON e.employee_id = ct.employee_id
            WHERE  ct.transaction_id = $1
            """,
            wbom_txn_id,
        )
        if not wbom:
            log.warning("[wbom_fpe_sync] wbom_txn_id=%d not found", wbom_txn_id)
            return None

        # Determine the best phone for the employee
        raw_phone = (
            wbom.get("bkash_number")
            or wbom.get("nagad_number")
            or wbom.get("employee_mobile")
        )
        phone = normalize_bd_phone(raw_phone) if raw_phone else None

        # Resolve or create the FPE employee
        emp = await match_or_create_employee(
            name_raw=wbom.get("employee_name"),
            payout_phone=phone,
            employee_id_phone=phone,
        )
        if not emp:
            log.warning(
                "[wbom_fpe_sync] could not resolve FPE employee for wbom_emp=%d",
                wbom["employee_id"],
            )
            return None

        method_str = _norm_method(wbom.get("payment_method"))
        try:
            payout_method = PayoutMethod(method_str)
        except ValueError:
            payout_method = PayoutMethod.unknown

        amount = Decimal(str(wbom.get("amount") or 0))
        txn_date_raw = wbom.get("transaction_date") or wbom.get("created_at")
        if isinstance(txn_date_raw, datetime):
            txn_date = txn_date_raw.date()
        elif isinstance(txn_date_raw, date):
            txn_date = txn_date_raw
        else:
            txn_date = date.today()

        txn_category = _wbom_type_to_category(wbom.get("transaction_type"))

        # Build a stable synthetic source_message_id for idempotency.
        # Format: wbom:<transaction_id> — never collides with real WA message IDs.
        source_message_id = f"wbom:{wbom['transaction_id']}"

        notes = wbom.get("remarks") or ""
        source_msg = (
            f"Migrated from WBOM txn #{wbom['transaction_id']} "
            f"type={wbom.get('transaction_type') or ''} "
            f"ref={wbom.get('reference_number') or ''} "
            f"remarks={notes}"
        )

        event = payment_event_from_whatsapp(
            employee_id=emp.employee_id,
            employee_name_raw=wbom.get("employee_name"),
            employee_id_phone=phone,
            employee_phone=phone,
            payout_phone=phone,
            payout_method=payout_method,
            amount=amount,
            txn_date=txn_date,
            txn_category=txn_category,
            wa_message_id=source_message_id,
            source_channel="migration",
            source_message_text=source_msg,
            created_by="wbom_migration",
            metadata={
                "legacy_wbom_transaction_id": wbom["transaction_id"],
                "legacy_wbom_employee_id": wbom["employee_id"],
                "legacy_reference_number": wbom.get("reference_number"),
                "legacy_remarks": notes,
                "legacy_transaction_type": wbom.get("transaction_type"),
            },
        )
        # Override source to migration so reports can distinguish historical rows.
        event.source = PaymentSource.migration
        event.legacy_wbom_transaction_id = wbom["transaction_id"]
        event.transaction_status = TransactionStatus.final

        req = payment_event_to_request(event)
        txn_row = await create_transaction(req)

        log.info(
            "[wbom_fpe_sync] wbom_txn=%d → fpe_txn=%d ref=%s emp=%s amount=%s",
            wbom_txn_id,
            txn_row.id,
            txn_row.txn_ref[:16],
            emp.employee_code,
            amount,
        )
        return {
            "id": txn_row.id,
            "txn_ref": txn_row.txn_ref,
            "employee_id": txn_row.employee_id,
            "amount": float(txn_row.amount),
        }

    except Exception as exc:
        log.error("[wbom_fpe_sync] sync_wbom_transaction(%d) error: %s", wbom_txn_id, exc)
        return None


async def backfill_wbom_to_fpe(since_days: int = 30) -> Tuple[int, int]:
    """
    Backfill all WBOM cash transactions from the last `since_days` days into
    the FPE pipeline.

    Returns (synced_count, skipped_count).  Never raises.
    """
    try:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)
        rows = await fetch_all(
            """
            SELECT transaction_id FROM wbom_cash_transactions
            WHERE  created_at >= $1
            ORDER  BY transaction_id
            """,
            since,
        )
        synced = 0
        skipped = 0
        for row in rows:
            result = await sync_wbom_transaction(row["transaction_id"])
            if result:
                synced += 1
            else:
                skipped += 1

        log.info(
            "[wbom_fpe_sync] backfill complete since_days=%d synced=%d skipped=%d",
            since_days, synced, skipped,
        )
        return synced, skipped

    except Exception as exc:
        log.error("[wbom_fpe_sync] backfill_wbom_to_fpe error: %s", exc)
        return 0, 0
