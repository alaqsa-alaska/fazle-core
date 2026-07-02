"""
Fazle Payroll Engine — Accounting engine.

Responsibilities:
- Create immutable cash_transactions rows
- Upsert employee_ledger (running totals per employee per period)
- Write audit log entries on every state change
- Create reversal entries (never mutate existing rows)

All amounts are NUMERIC(12,2) in DB — Python uses Decimal to avoid
floating-point drift.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from app.database import execute, fetch_one, fetch_val, db_conn
from .models import TransactionCreateRequest, TransactionRow, TxnCategory

log = logging.getLogger("fazle.fpe.accounting")


# ── Transaction creation ──────────────────────────────────────────────────────

async def create_transaction(req: TransactionCreateRequest) -> TransactionRow:
    """
    Insert one immutable cash transaction and update the employee ledger.
    Returns the created TransactionRow.

    Idempotency is enforced by txn_ref = sha256 of
    (source_message_id + fpe_wa_message_id + employee_id + amount + period + method + source).
    For manual entries, source_message_id falls back to source_message_text.
    """
    period = req.accounting_period or _period_from_date(req.txn_date)
    txn_ref = _build_txn_ref(req, period)

    # Idempotency check
    existing = await fetch_one(
        "SELECT id, txn_ref, employee_id, amount, payout_method, txn_date, "
        "txn_category, accounting_period, is_reversal, created_at, source, source_message_id "
        "FROM fpe_cash_transactions WHERE txn_ref = $1",
        txn_ref,
    )
    if existing:
        log.info("[fpe.acct] idempotent hit txn_ref=%s", txn_ref)
        return _row_to_model(existing)

    async with db_conn() as conn:
        new_id: int = await conn.fetchval(
            """
            INSERT INTO fpe_cash_transactions
                (txn_ref, fpe_wa_message_id, employee_id, employee_name_raw,
                 employee_id_phone, employee_phone,
                 amount, payout_phone, payout_method, txn_date, txn_category,
                 source_message_text, accounting_period, created_by,
                 source, source_channel, source_message_id,
                 program_id, original_payload, metadata,
                 transaction_status, approval_status, approved_by, approved_at,
                 review_status, submitted_by, submitted_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27)
            RETURNING id
            """,
            txn_ref,
            req.fpe_wa_message_id,
            req.employee_id,
            req.employee_name_raw,
            req.employee_id_phone,
            req.employee_phone,
            req.amount,
            req.payout_phone,
            req.payout_method.value if req.payout_method else None,
            req.txn_date,
            req.txn_category.value,
            req.source_message_text,
            period,
            req.created_by,
            req.source.value,
            req.source_channel,
            req.source_message_id,
            req.program_id,
            json.dumps(req.original_payload) if req.original_payload else None,
            json.dumps(req.metadata) if req.metadata else '{}',
            req.transaction_status.value,
            req.approval_status.value if req.approval_status else None,
            req.approved_by,
            req.approved_at,
            req.review_status.value if req.review_status else None,
            req.submitted_by,
            req.submitted_at,
        )

        # Audit log
        await conn.execute(
            """
            INSERT INTO fpe_accounting_audit_logs
                (entity_type, entity_id, action, after_state, performed_by)
            VALUES ('transaction', $1, 'create', $2, $3)
            """,
            new_id,
            json.dumps(_txn_to_audit_json(req, txn_ref, period)),
            req.created_by,
        )

    log.info(
        "[fpe.acct] created txn id=%d ref=%s emp=%s amount=%s method=%s source=%s",
        new_id, txn_ref[:12], req.employee_id, req.amount, req.payout_method, req.source.value,
    )

    # Update ledger only for final (approved) transactions
    if req.employee_id and req.transaction_status.value == "final":
        await _upsert_ledger(req.employee_id, period, req.amount, req.txn_category)

    row = await fetch_one(
        "SELECT id, txn_ref, employee_id, employee_name_raw, employee_id_phone, employee_phone, "
        "amount, payout_phone, payout_method, txn_date, txn_category, accounting_period, "
        "is_reversal, source, source_channel, source_message_id, transaction_status, "
        "approval_status, approved_by, approved_at, review_status, submitted_by, submitted_at, "
        "program_id, legacy_wbom_transaction_id, original_payload, metadata, created_at "
        "FROM fpe_cash_transactions WHERE id = $1",
        new_id,
    )
    return _row_to_model(row)


async def reverse_transaction(txn_id: int, reason: str, created_by: str = "admin") -> TransactionRow:
    """
    Create a reversal entry (negative amount) for an existing transaction.
    The original row is NEVER mutated.
    """
    orig = await fetch_one(
        "SELECT * FROM fpe_cash_transactions WHERE id = $1",
        txn_id,
    )
    if not orig:
        raise ValueError(f"Transaction {txn_id} not found")
    if orig["is_reversal"]:
        raise ValueError(f"Transaction {txn_id} is already a reversal")

    reversal_ref = f"REV-{orig['txn_ref']}"

    # Check idempotency for reversal
    existing_rev = await fetch_one(
        "SELECT id FROM fpe_cash_transactions WHERE txn_ref = $1",
        reversal_ref,
    )
    if existing_rev:
        raise ValueError(f"Reversal already exists for txn {txn_id}")

    async with db_conn() as conn:
        new_id: int = await conn.fetchval(
            """
            INSERT INTO fpe_cash_transactions
                (txn_ref, fpe_wa_message_id, employee_id, employee_name_raw,
                 employee_id_phone, employee_phone,
                 amount, payout_phone, payout_method, txn_date, txn_category,
                 source_message_text, accounting_period, is_reversal,
                 reversed_txn_id, created_by,
                 source, source_channel, source_message_id,
                 transaction_status, approval_status, approved_by, approved_at,
                 review_status, submitted_by, submitted_at,
                 program_id, original_payload, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,TRUE,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28)
            RETURNING id
            """,
            reversal_ref,
            orig["fpe_wa_message_id"],
            orig["employee_id"],
            orig["employee_name_raw"],
            orig["employee_id_phone"],
            orig["employee_phone"],
            -orig["amount"],
            orig["payout_phone"],
            orig["payout_method"],
            orig["txn_date"],
            orig["txn_category"],
            f"REVERSAL: {reason}",
            orig["accounting_period"],
            txn_id,
            created_by,
            orig.get("source", "whatsapp"),
            orig.get("source_channel"),
            orig.get("source_message_id"),
            "reversed",
            orig.get("approval_status"),
            orig.get("approved_by"),
            orig.get("approved_at"),
            orig.get("review_status"),
            orig.get("submitted_by"),
            orig.get("submitted_at"),
            orig.get("program_id"),
            json.dumps({"reversal_reason": reason, "original_id": txn_id}),
            orig.get("metadata", '{}'),
        )

        await conn.execute(
            """
            INSERT INTO fpe_accounting_audit_logs
                (entity_type, entity_id, action, before_state, after_state, performed_by, reason)
            VALUES ('transaction', $1, 'reverse', $2, $3, $4, $5)
            """,
            txn_id,
            json.dumps({"id": txn_id, "amount": str(orig["amount"])}),
            json.dumps({"reversal_id": new_id, "reversal_ref": reversal_ref}),
            created_by,
            reason,
        )

    # Update ledger (negative amount cancels original)
    if orig["employee_id"]:
        await _upsert_ledger(
            orig["employee_id"],
            orig["accounting_period"],
            -orig["amount"],
            TxnCategory(orig["txn_category"]),
        )

    log.info("[fpe.acct] reversed txn %d → new reversal id=%d", txn_id, new_id)
    row = await fetch_one(
        "SELECT id, txn_ref, employee_id, employee_name_raw, employee_id_phone, employee_phone, "
        "amount, payout_phone, payout_method, txn_date, txn_category, accounting_period, "
        "is_reversal, source, source_channel, source_message_id, transaction_status, "
        "approval_status, approved_by, approved_at, review_status, submitted_by, submitted_at, "
        "program_id, legacy_wbom_transaction_id, original_payload, metadata, created_at "
        "FROM fpe_cash_transactions WHERE id = $1",
        new_id,
    )
    return _row_to_model(row)


# ── Ledger upsert ─────────────────────────────────────────────────────────────

async def _upsert_ledger(
    employee_id: int,
    period: str,
    amount: Decimal,
    category: TxnCategory,
) -> None:
    """
    Atomically increment the appropriate ledger bucket and recompute closing_balance.
    Uses INSERT ... ON CONFLICT DO UPDATE for atomicity.
    """
    # Determine which column to increment
    if category == TxnCategory.advance:
        col = "total_advance"
    elif category in (TxnCategory.deduction, TxnCategory.correction):
        col = "total_paid"  # corrections also count as payments out
    else:
        col = "total_paid"  # salary, bonus

    await execute(
        f"""
        INSERT INTO fpe_employee_ledger
            (employee_id, accounting_period, {col}, txn_count, last_updated)
        VALUES ($1, $2, $3, 1, NOW())
        ON CONFLICT (employee_id, accounting_period) DO UPDATE
        SET {col}      = fpe_employee_ledger.{col} + EXCLUDED.{col},
            txn_count  = fpe_employee_ledger.txn_count + 1,
            closing_balance = fpe_employee_ledger.opening_balance
                            + fpe_employee_ledger.total_earned
                            - fpe_employee_ledger.total_paid
                            - fpe_employee_ledger.total_advance
                            - EXCLUDED.{col},
            last_updated = NOW()
        """,
        employee_id,
        period,
        amount,
    )

    log.debug("[fpe.acct] ledger upserted emp=%d period=%s col=%s amount=%s", employee_id, period, col, amount)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _period_from_date(d: date) -> str:
    return d.strftime("%Y-%m")


def _build_txn_ref(req: TransactionCreateRequest, period: str) -> str:
    # Prefer source_message_id for idempotency; fallback to source_message_text for manual entries
    idempotency_seed = req.source_message_id or req.source_message_text or ""
    parts = [
        idempotency_seed,
        str(req.fpe_wa_message_id or ""),
        str(req.employee_id or ""),
        str(req.amount),
        period,
        req.payout_method.value if req.payout_method else "",
        req.source.value,
    ]
    key = "|".join(parts)
    return "fpe-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _txn_to_audit_json(req: TransactionCreateRequest, txn_ref: str, period: str) -> dict:
    return {
        "txn_ref": txn_ref,
        "employee_id": req.employee_id,
        "employee_id_phone": req.employee_id_phone,
        "employee_phone": req.employee_phone,
        "amount": str(req.amount),
        "payout_method": req.payout_method.value if req.payout_method else None,
        "txn_date": req.txn_date.isoformat(),
        "accounting_period": period,
        "txn_category": req.txn_category.value,
        "source": req.source.value,
        "source_channel": req.source_channel,
        "source_message_id": req.source_message_id,
        "transaction_status": req.transaction_status.value,
        "approval_status": req.approval_status.value if req.approval_status else None,
        "program_id": req.program_id,
        "metadata": req.metadata,
    }


def _row_to_model(row: dict) -> TransactionRow:
    return TransactionRow(
        id=row["id"],
        txn_ref=row["txn_ref"],
        employee_id=row.get("employee_id"),
        employee_name_raw=row.get("employee_name_raw"),
        amount=Decimal(str(row["amount"])),
        payout_phone=row.get("payout_phone"),
        payout_method=row.get("payout_method"),
        txn_date=row["txn_date"],
        txn_category=row["txn_category"],
        accounting_period=row.get("accounting_period"),
        is_reversal=row.get("is_reversal", False),
        created_at=row["created_at"],
    )


# ── Income transaction creation ───────────────────────────────────────────────

async def create_income_transaction(
    fpe_wa_message_id: Optional[int],
    employee_id: Optional[int],
    employee_name_raw: Optional[str],
    amount: Decimal,
    txn_date: date,
    reported_by_phone: Optional[str] = None,
    source_message_text: Optional[str] = None,
) -> int:
    """
    Insert one income transaction into fpe_income_transactions.
    Does NOT touch fpe_employee_ledger or fpe_cash_transactions.

    Idempotency: sha256(wa_message_id | employee_id | amount | period).
    Returns the new (or existing) row id.
    """
    period = _period_from_date(txn_date)
    key = "|".join([
        str(fpe_wa_message_id or ""),
        str(employee_id or ""),
        str(amount),
        period,
    ])
    txn_ref = "inc-" + hashlib.sha256(key.encode()).hexdigest()[:16]

    existing_id = await fetch_val(
        "SELECT id FROM fpe_income_transactions WHERE txn_ref = $1",
        txn_ref,
    )
    if existing_id:
        log.info("[fpe.acct] income idempotent hit txn_ref=%s", txn_ref)
        return existing_id

    async with db_conn() as conn:
        new_id: int = await conn.fetchval(
            """
            INSERT INTO fpe_income_transactions
                (txn_ref, fpe_wa_message_id, employee_id, employee_name_raw,
                 amount, txn_date, accounting_period,
                 reported_by_phone, source_message_text)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            RETURNING id
            """,
            txn_ref,
            fpe_wa_message_id,
            employee_id,
            employee_name_raw,
            amount,
            txn_date,
            period,
            reported_by_phone,
            source_message_text,
        )

    log.info(
        "[fpe.acct] income txn id=%d ref=%s emp=%s amount=%s",
        new_id, txn_ref[:12], employee_id, amount,
    )
    return new_id
