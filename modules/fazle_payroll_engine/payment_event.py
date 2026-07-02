"""
Payment Event normalization layer.

Converts any cash/payment source into a canonical PaymentEvent, then into a
TransactionCreateRequest for create_transaction().

Owner Directive (2026-06-29):
  - fpe_cash_transactions is the ONLY canonical cash transaction table.
  - All sources must end up in fpe_cash_transactions via create_transaction().
  - WhatsApp Admin → Accountant parser behaviour must not break.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from .models import (
    ApprovalStatus,
    PaymentEvent,
    PaymentSource,
    PayoutMethod,
    ReviewStatus,
    TransactionCreateRequest,
    TransactionStatus,
    TxnCategory,
)
from .normalizer import normalize_bd_phone as _norm_phone  # single source of truth


def _period_from_date(txn_date: date) -> str:
    return txn_date.strftime("%Y-%m")


def payment_event_from_whatsapp(
    *,
    employee_id: Optional[int],
    employee_name_raw: Optional[str],
    employee_id_phone: Optional[str],
    employee_phone: Optional[str],
    payout_phone: Optional[str],
    payout_method: PayoutMethod,
    amount: Decimal,
    txn_date: date,
    txn_category: TxnCategory = TxnCategory.salary,
    fpe_wa_message_id: Optional[int] = None,
    wa_message_id: Optional[str] = None,
    source_channel: Optional[str] = None,
    source_message_text: Optional[str] = None,
    created_by: str = "fpe_engine",
    program_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> PaymentEvent:
    """Build a PaymentEvent from a parsed WhatsApp Admin → Accountant payment."""
    return PaymentEvent(
        employee_id=employee_id,
        employee_name_raw=employee_name_raw,
        employee_id_phone=_norm_phone(employee_id_phone),
        employee_phone=_norm_phone(employee_phone),
        amount=amount,
        payout_phone=_norm_phone(payout_phone),
        payout_method=payout_method,
        txn_date=txn_date,
        txn_category=txn_category,
        source=PaymentSource.whatsapp,
        source_channel=source_channel,
        source_message_id=wa_message_id,
        source_message_text=source_message_text,
        fpe_wa_message_id=fpe_wa_message_id,
        transaction_status=TransactionStatus.final,
        accounting_period=_period_from_date(txn_date),
        created_by=created_by,
        program_id=program_id,
        metadata=metadata or {},
    )


def payment_event_from_manual(
    *,
    employee_id: int,
    employee_name_raw: Optional[str] = None,
    amount: Decimal,
    payout_method: PayoutMethod,
    payout_phone: Optional[str] = None,
    txn_date: date,
    txn_category: TxnCategory = TxnCategory.salary,
    source_message_text: Optional[str] = None,
    created_by: str = "admin",
    metadata: Optional[dict[str, Any]] = None,
) -> PaymentEvent:
    """Build a PaymentEvent from a manual/admin entry."""
    return PaymentEvent(
        employee_id=employee_id,
        employee_name_raw=employee_name_raw,
        amount=amount,
        payout_phone=_norm_phone(payout_phone),
        payout_method=payout_method,
        txn_date=txn_date,
        txn_category=txn_category,
        source=PaymentSource.manual,
        source_message_text=source_message_text,
        transaction_status=TransactionStatus.final,
        accounting_period=_period_from_date(txn_date),
        created_by=created_by,
        metadata=metadata or {},
    )


def payment_event_from_operator(
    *,
    employee_id: int,
    amount: Decimal,
    payout_method: PayoutMethod,
    payout_phone: Optional[str] = None,
    txn_date: date,
    pending_id: int,
    submitted_by: str,
    txn_category: TxnCategory = TxnCategory.salary,
    source_channel: Optional[str] = "web",
    metadata: Optional[dict[str, Any]] = None,
) -> PaymentEvent:
    """Build a PaymentEvent for an operator submission (pending approval)."""
    return PaymentEvent(
        employee_id=employee_id,
        amount=amount,
        payout_phone=_norm_phone(payout_phone),
        payout_method=payout_method,
        txn_date=txn_date,
        txn_category=txn_category,
        source=PaymentSource.operator,
        source_channel=source_channel,
        source_message_id=str(pending_id),
        transaction_status=TransactionStatus.pending,
        approval_status=ApprovalStatus.pending_review,
        review_status=ReviewStatus.pending,
        submitted_by=submitted_by,
        submitted_at=datetime.now(),
        accounting_period=_period_from_date(txn_date),
        created_by=submitted_by,
        metadata=metadata or {},
    )


def payment_event_from_employee_draft(
    *,
    employee_id: int,
    amount: Decimal,
    payout_method: PayoutMethod,
    payout_phone: Optional[str] = None,
    txn_date: date,
    draft_id: int,
    submitted_by: Optional[str] = None,
    txn_category: TxnCategory = TxnCategory.salary,
    metadata: Optional[dict[str, Any]] = None,
) -> PaymentEvent:
    """Build a PaymentEvent for an employee draft (pending admin approval)."""
    return PaymentEvent(
        employee_id=employee_id,
        amount=amount,
        payout_phone=_norm_phone(payout_phone),
        payout_method=payout_method,
        txn_date=txn_date,
        txn_category=txn_category,
        source=PaymentSource.employee_draft,
        source_message_id=str(draft_id),
        transaction_status=TransactionStatus.pending,
        approval_status=ApprovalStatus.pending_review,
        review_status=ReviewStatus.pending,
        submitted_by=submitted_by,
        submitted_at=datetime.now(),
        accounting_period=_period_from_date(txn_date),
        created_by=submitted_by or "employee",
        metadata=metadata or {},
    )


def payment_event_to_request(event: PaymentEvent) -> TransactionCreateRequest:
    """Convert a normalized PaymentEvent into the canonical TransactionCreateRequest."""
    return TransactionCreateRequest(
        fpe_wa_message_id=event.fpe_wa_message_id,
        employee_id=event.employee_id,
        employee_name_raw=event.employee_name_raw,
        amount=event.amount,
        payout_phone=event.payout_phone,
        payout_method=event.payout_method,
        txn_date=event.txn_date,
        txn_category=event.txn_category,
        source_message_text=event.source_message_text,
        accounting_period=event.accounting_period,
        created_by=event.created_by,
        # C1B canonical extensions
        source=event.source,
        source_channel=event.source_channel,
        source_message_id=event.source_message_id,
        employee_id_phone=event.employee_id_phone,
        employee_phone=event.employee_phone,
        program_id=event.program_id,
        legacy_wbom_transaction_id=event.legacy_wbom_transaction_id,
        original_payload=event.original_payload,
        metadata=event.metadata,
        transaction_status=event.transaction_status,
        approval_status=event.approval_status,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
        review_status=event.review_status,
        submitted_by=event.submitted_by,
        submitted_at=event.submitted_at,
    )
