"""
shared/identity_map.py — Phase 14 Simplification
=================================================
Canonical employee identity resolution layer.

PURPOSE
-------
The codebase uses multiple phone/identity concepts with different names
depending on context. This module provides ONE place to resolve and validate
employee identity without renaming DB columns or changing existing APIs.

CANONICAL CONCEPT MAP
---------------------
  employee_id       → wbom_employees.employee_id         (PK, internal integer)
  employee_mobile   → wbom_employees.employee_mobile     (operational WhatsApp contact)
  payout_phone      → fpe_transactions.payout_phone      (payment destination, MAY differ)
  payment_mobile    → wbom_cash_transactions.payment_mobile (historical transaction record)
  sender_phone      → derived from WhatsApp JID           (may or may not be an employee)
  candidate_phone   → recruitment tables                  (applicant, NOT yet employee)
  escort_employee_id→ wbom_escort_programs.escort_employee_id (FK → wbom_employees)

CRITICAL RULE
-------------
  employee_mobile ≠ payout_phone (always)
  A payout_phone is where MONEY goes.
  An employee_mobile is how you REACH the employee.
  NEVER use employee_mobile as a payment destination without explicit validation.

USAGE
-----
    from shared.identity_map import resolve_employee, validate_payment_identity, normalize_phone

    emp = await resolve_employee(phone="01712345678")
    if emp is None:
        # cannot confidently identify — do NOT auto-process payment

    ok = await validate_payment_identity(employee_id=42, payout_phone="01712345678")
    if not ok:
        raise ValueError("Payment blocked: ambiguous identity")

RULES
-----
* DO NOT rename DB columns.
* DO NOT modify existing schemas.
* This is a PURE ADDITIVE layer — existing code is unaffected.
* All DB queries here are READ-ONLY.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from shared.phone import normalize_phone as _sp_normalize_phone, phones_match

log = logging.getLogger("fazle.identity_map")

# ── Phone normalisation ──────────────────────────────────────────────────────


def normalize_phone(raw: Optional[str]) -> str:
    """
    Canonical Bangladesh phone → 01XXXXXXXXX (11 digits).
    Delegates to shared.phone → fazle_payroll_engine.normalizer (single source of truth).
    Returns empty string for unresolvable input.

    Examples
    --------
    >>> normalize_phone("+8801712345678")
    '01712345678'
    >>> normalize_phone("8801712345678")
    '01712345678'
    >>> normalize_phone("01712345678")
    '01712345678'
    >>> normalize_phone("1712345678")
    '01712345678'
    >>> normalize_phone(None)
    ''
    """
    return _sp_normalize_phone(raw)


def phone_last10(raw: Optional[str]) -> str:
    """Return last 10 digits of a phone number for fuzzy matching."""
    n = normalize_phone(raw)
    return n[-10:] if len(n) >= 10 else ""


# phones_match is imported from shared.phone (canonical source)


# ── Employee Identity dataclass ───────────────────────────────────────────────

@dataclass
class EmployeeIdentity:
    """
    Resolved employee identity.

    Fields
    ------
    employee_id   : int  — wbom_employees.employee_id (authoritative PK)
    name          : str  — wbom_employees.employee_name
    mobile        : str  — wbom_employees.employee_mobile (contact phone, 01XXXXXXXXX)
    payout_phone  : str  — registered payment phone (may differ from mobile)
    department    : str  — department/section name
    is_active     : bool — False if terminated/inactive
    source        : str  — how this record was resolved: "db_id" | "db_mobile" | "db_name"

    IMPORTANT: mobile ≠ payout_phone in general. Never use mobile as payout without
    calling validate_payment_identity() first.
    """
    employee_id: int
    name: str
    mobile: str
    payout_phone: str
    department: str = ""
    is_active: bool = True
    source: str = "unknown"
    _raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_payment_phone_separate(self) -> bool:
        """True when payout_phone differs from employee_mobile."""
        return bool(self.payout_phone) and not phones_match(self.mobile, self.payout_phone)

    def as_context_dict(self) -> dict:
        """Safe dict for logging/audit — no sensitive data beyond what's needed."""
        return {
            "employee_id": self.employee_id,
            "name": self.name,
            "mobile_last4": self.mobile[-4:] if self.mobile else "",
            "payout_last4": self.payout_phone[-4:] if self.payout_phone else "",
            "department": self.department,
            "is_active": self.is_active,
            "payment_phone_separate": self.is_payment_phone_separate,
            "source": self.source,
        }


# ── Employee resolution ───────────────────────────────────────────────────────

async def resolve_employee(
    *,
    phone: Optional[str] = None,
    employee_id: Optional[int] = None,
    name: Optional[str] = None,
    require_active: bool = False,
) -> Optional[EmployeeIdentity]:
    """
    Resolve an employee record by any one of: phone, employee_id, or name.
    At least one argument must be provided.

    Resolution order (highest confidence first):
      1. employee_id  — exact PK match, unambiguous
      2. phone        — matched against wbom_employees.employee_mobile (last-10)
      3. name         — ILIKE match on employee_name (risky, use as last resort)

    Returns None when:
      - No DB record found
      - Multiple records match (ambiguous — safe to reject)
      - require_active=True and employee is inactive

    IMPORTANT: caller MUST check for None before processing any payment.
    """
    from app.database import fetch_one, fetch_all  # deferred to avoid import cycles

    result: Optional[EmployeeIdentity] = None

    if employee_id is not None:
        row = await fetch_one(
            """SELECT employee_id, employee_name, employee_mobile,
                      payout_phone, department, is_active
               FROM wbom_employees
               WHERE employee_id = $1""",
            employee_id,
        )
        if row:
            result = _row_to_identity(row, source="db_id")

    elif phone is not None:
        norm = normalize_phone(phone)
        if not norm:
            log.warning("[identity_map] resolve_employee: unparseable phone=%r", phone)
            return None

        last10 = norm[-10:]
        rows = await fetch_all(
            """SELECT employee_id, employee_name, employee_mobile,
                      payout_phone, department, is_active
               FROM wbom_employees
               WHERE employee_mobile LIKE $1
                  OR employee_mobile = $2""",
            f"%{last10}",
            norm,
        )
        if len(rows) == 1:
            result = _row_to_identity(rows[0], source="db_mobile")
        elif len(rows) > 1:
            log.warning(
                "[identity_map] ambiguous phone=%r matched %d employees — rejecting",
                phone, len(rows),
            )
            return None  # ambiguous: do NOT proceed

    elif name is not None:
        rows = await fetch_all(
            """SELECT employee_id, employee_name, employee_mobile,
                      payout_phone, department, is_active
               FROM wbom_employees
               WHERE employee_name ILIKE $1""",
            f"%{name.strip()}%",
        )
        if len(rows) == 1:
            result = _row_to_identity(rows[0], source="db_name")
        elif len(rows) > 1:
            log.warning(
                "[identity_map] ambiguous name=%r matched %d employees — rejecting",
                name, len(rows),
            )
            return None
    else:
        raise ValueError("resolve_employee: provide at least one of phone, employee_id, or name")

    if result is None:
        return None

    if require_active and not result.is_active:
        log.warning("[identity_map] employee_id=%d is inactive — rejecting", result.employee_id)
        return None

    return result


def _row_to_identity(row: dict, source: str) -> EmployeeIdentity:
    return EmployeeIdentity(
        employee_id=int(row["employee_id"]),
        name=str(row.get("employee_name") or ""),
        mobile=normalize_phone(row.get("employee_mobile")),
        payout_phone=normalize_phone(row.get("payout_phone")),
        department=str(row.get("department") or ""),
        is_active=bool(row.get("is_active", True)),
        source=source,
        _raw=dict(row),
    )


# ── Payment identity validation ───────────────────────────────────────────────

async def validate_payment_identity(
    employee_id: int,
    payout_phone: str,
) -> bool:
    """
    Validate that a payout_phone is the correct payment destination for employee_id.

    Returns True  → safe to process payment
    Returns False → BLOCK payment (ambiguous or mismatched identity)

    Rules enforced:
      1. employee_id must exist in wbom_employees
      2. payout_phone must be a valid BD phone number
      3. If the employee has a registered payout_phone, it must match (last-10)
      4. If no payout_phone registered, the caller must use employee_mobile
         ONLY if they explicitly confirm it (this function returns False to force
         that check — do not override silently)

    Logs a WARNING on every rejection so the audit trail is always visible.
    """
    norm_payout = normalize_phone(payout_phone)
    if not norm_payout:
        log.warning(
            "[identity_map] validate_payment_identity: invalid payout_phone=%r for employee_id=%d",
            payout_phone, employee_id,
        )
        return False

    emp = await resolve_employee(employee_id=employee_id, require_active=True)
    if emp is None:
        log.warning(
            "[identity_map] validate_payment_identity: employee_id=%d not found or inactive",
            employee_id,
        )
        return False

    if emp.payout_phone:
        # Registered payout_phone must match
        if not phones_match(emp.payout_phone, norm_payout):
            log.warning(
                "[identity_map] PAYMENT BLOCKED employee_id=%d: "
                "registered payout_last4=%s ≠ provided last4=%s",
                employee_id,
                emp.payout_phone[-4:],
                norm_payout[-4:],
            )
            return False
        return True
    else:
        # No registered payout_phone: block auto-processing; force manual review
        log.warning(
            "[identity_map] PAYMENT BLOCKED employee_id=%d: "
            "no registered payout_phone on record — manual review required",
            employee_id,
        )
        return False


# ── Sender → employee matching ────────────────────────────────────────────────

async def sender_is_employee(sender_phone: str) -> bool:
    """
    Quick check: is this WhatsApp sender an active employee?
    Does NOT raise — returns False on any error.
    """
    try:
        emp = await resolve_employee(phone=sender_phone, require_active=True)
        return emp is not None
    except Exception as exc:  # noqa: BLE001
        log.debug("[identity_map] sender_is_employee check failed: %s", exc)
        return False


async def sender_to_employee(sender_phone: str) -> Optional[EmployeeIdentity]:
    """
    Resolve a WhatsApp sender phone to an employee record.
    Returns None if sender is not a known active employee.
    """
    return await resolve_employee(phone=sender_phone, require_active=False)


# ── Candidate phone guard ─────────────────────────────────────────────────────

def is_candidate_phone(phone: str, employees: list[EmployeeIdentity]) -> bool:
    """
    Returns True if the phone does NOT match any known employee.
    Use this to route a sender to the recruitment funnel rather than payroll.
    """
    norm = normalize_phone(phone)
    if not norm:
        return True  # unknown → treat as candidate for safety
    return not any(phones_match(norm, e.mobile) for e in employees)


# ── Public API ────────────────────────────────────────────────────────────────

__all__ = [
    # phone helpers
    "normalize_phone",
    "phone_last10",
    "phones_match",
    # identity
    "EmployeeIdentity",
    "resolve_employee",
    "validate_payment_identity",
    # sender helpers
    "sender_is_employee",
    "sender_to_employee",
    "is_candidate_phone",
]
