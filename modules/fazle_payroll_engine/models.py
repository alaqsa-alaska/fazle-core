"""
Fazle Payroll Engine — Pydantic models (no ORM).
All DB interaction is raw asyncpg — these are for validation / serialization only.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, field_validator


# ── Enums ─────────────────────────────────────────────────────────────────────

class PayoutMethod(str, Enum):
    bkash  = "bkash"
    nagad  = "nagad"
    cash   = "cash"
    rocket = "rocket"
    bank   = "bank"
    unknown = "unknown"


class MessageType(str, Enum):
    payment         = "payment"
    balance_summary = "balance_summary"
    cash_command    = "cash_command"    # "Cash <phone> <name> <amount>" from authorized sender
    income_command  = "income_command"  # "Income <phone> <name> <amount>" from authorized sender
    escort_payment  = "escort_payment"  # multi-employee escort duty payment list (NAME=AMOUNT/ per line)
    other           = "other"


class ProcessingStatus(str, Enum):
    pending    = "pending"
    parsing    = "parsing"
    parsed     = "parsed"
    accounting = "accounting"
    done       = "done"
    failed     = "failed"
    skipped    = "skipped"


class TxnCategory(str, Enum):
    salary     = "salary"
    advance    = "advance"
    bonus      = "bonus"
    deduction  = "deduction"
    correction = "correction"


# ── Validators ────────────────────────────────────────────────────────────────

def validate_accounting_period(period: str) -> bool:
    """Validate accounting_period format: YYYY-MM"""
    import re
    if not isinstance(period, str):
        return False
    return bool(re.match(r'^\d{4}-\d{2}$', period))


def validate_date_format(date_str: str) -> bool:
    """Validate date format: YYYY-MM-DD"""
    import re
    if not isinstance(date_str, str):
        return False
    return bool(re.match(r'^\d{4}-\d{2}-\d{2}$', date_str))
    income     = "income"      # company income received from a client, linked to an employee


# ── Parser output ─────────────────────────────────────────────────────────────

class ParsedPayment(BaseModel):
    """Output of parser.parse_message() when message_type == 'payment'."""
    employee_id_phone: Optional[str] = None   # from "ID: 01XXXXXXXXX" prefix
    employee_name_raw: Optional[str] = None
    payout_phone: Optional[str] = None        # normalized 01XXXXXXXXX
    payout_method: PayoutMethod = PayoutMethod.unknown
    amount: Optional[Decimal] = None
    txn_date: Optional[date] = None
    confidence: float = 1.0
    raw_text: str = ""


class ParsedBalanceSummary(BaseModel):
    """Output when message is an accountant balance summary."""
    summary_date: Optional[date] = None
    total_due: Optional[Decimal] = None      # বাকি
    total_collected: Optional[Decimal] = None  # জমা
    raw_text: str = ""


class EscortPaymentEntry(BaseModel):
    """One escort employee's payment within an escort_payment message."""
    name_raw: str
    amount: Decimal


class ParsedEscortPayment(BaseModel):
    """Parsed multi-employee escort duty payment list (NAME=AMOUNT/ per line)."""
    entries: list[EscortPaymentEntry] = []
    shift: Optional[str] = None       # 'D' | 'N'
    duty_date: Optional[date] = None
    total_amount: Optional[Decimal] = None
    raw_text: str = ""


class ParseResult(BaseModel):
    message_type: MessageType = MessageType.other
    payment: Optional[ParsedPayment] = None
    balance_summary: Optional[ParsedBalanceSummary] = None
    escort_payment: Optional[ParsedEscortPayment] = None
    confidence: float = 1.0
    ai_enhanced: bool = False
    ai_notes: Optional[str] = None


# ── Employee ──────────────────────────────────────────────────────────────────

class EmployeeMatchResult(BaseModel):
    employee_id: int
    employee_code: str
    full_name: str
    primary_phone: Optional[str]
    match_type: str   # 'exact_phone' | 'exact_id_phone' | 'exact_name' | 'fuzzy_name' | 'auto_created'
    match_score: float = 1.0


# ── Transaction creation ──────────────────────────────────────────────────────

class TransactionCreateRequest(BaseModel):
    fpe_wa_message_id: Optional[int] = None
    employee_id: Optional[int] = None
    employee_name_raw: Optional[str] = None
    amount: Decimal
    payout_phone: Optional[str] = None
    payout_method: PayoutMethod = PayoutMethod.unknown
    txn_date: date
    txn_category: TxnCategory = TxnCategory.salary
    source_message_text: Optional[str] = None
    accounting_period: Optional[str] = None   # YYYY-MM; auto-derived if None
    created_by: str = "fpe_engine"


class TransactionRow(BaseModel):
    id: int
    txn_ref: str
    employee_id: Optional[int]
    employee_name_raw: Optional[str]
    amount: Decimal
    payout_phone: Optional[str]
    payout_method: Optional[str]
    txn_date: date
    txn_category: str
    accounting_period: Optional[str]
    is_reversal: bool
    created_at: datetime


# ── Ledger ────────────────────────────────────────────────────────────────────

class LedgerRow(BaseModel):
    employee_id: int
    accounting_period: str
    opening_balance: Decimal
    total_earned: Decimal
    total_paid: Decimal
    total_advance: Decimal
    closing_balance: Decimal
    txn_count: int
    last_updated: datetime


# ── API request/response ──────────────────────────────────────────────────────

class IngestionRequest(BaseModel):
    wa_message_id: str
    source: str              # 'bridge1' | 'bridge2' | 'meta'
    source_number: str
    chat_jid: str
    sender_phone: Optional[str] = None
    is_from_me: bool = False
    raw_content: Optional[str] = None
    media_type: Optional[str] = None
    timestamp_wa: datetime


class ManualTxnRequest(BaseModel):
    employee_id: int
    amount: Decimal
    payout_method: PayoutMethod
    payout_phone: Optional[str] = None
    txn_date: date
    txn_category: TxnCategory = TxnCategory.salary
    reason: str


class ReversalRequest(BaseModel):
    txn_id: int
    reason: str
    created_by: str = "admin"


class IncomeCreateRequest(BaseModel):
    txn_date: str                        # YYYY-MM-DD
    income_head: str
    received_from: str
    amount: float
    money_received_by: str
    mobile_number: Optional[str] = None
    money_receipt_no: Optional[str] = None
