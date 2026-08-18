"""
Fazle Core — User Role Detection (Phase 4A)

DEPRECATED (2026-08-18, `HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md`):
superseded by `modules.identity_brain.detect_identity()`, which is what
message_router/bridge_poller/app.main actually call in production. A
repo-wide grep confirmed `detect_role()` has zero callers anywhere in this
codebase. Kept only because `archive/deprecated/employee_utils` still
imports it. Do not use this module for new code — use
`modules.identity_brain.detect_identity()` instead.

Detects who is sending a message by looking up their phone number
against the company database. Returns a role context dict used by
the reply engine to choose the right tone and DB facts.

Phone normalization:
  Delegates to shared.phone → fazle_payroll_engine.normalizer (single source of truth).
  Canonical storage: 01XXXXXXXXX (11-digit local format).
"""

import logging
from shared.phone import normalize_phone as _sp_normalize_phone
from typing import TypedDict, Optional

from app.database import fetch_one

log = logging.getLogger("fazle.user_role")

# Admin phones (set in .env as comma-separated list)
# Loaded lazily from settings on first call
_admin_phones: set[str] | None = None
_accountant_phone: str | None = None


def _get_admin_phones() -> set[str]:
    global _admin_phones
    if _admin_phones is None:
        try:
            from app.config import get_settings
            s = get_settings()
            phones = set(normalize_phone(p) for p in s.admin_number_list if p)
            # Also add bridge-specific admin numbers
            for attr in ("admin_meta_number", "admin_bridge1_number", "admin_bridge2_number"):
                v = getattr(s, attr, "")
                if v:
                    phones.add(normalize_phone(v))
            _admin_phones = phones
        except Exception:
            _admin_phones = set()
    return _admin_phones


def _get_accountant_phone() -> str:
    global _accountant_phone
    if _accountant_phone is None:
        try:
            from app.config import get_settings
            s = get_settings()
            _accountant_phone = normalize_phone(s.accountant_phone) if s.accountant_phone else ""
        except Exception:
            _accountant_phone = ""
    return _accountant_phone


def normalize_phone(phone: str) -> str:
    """
    Normalize phone to 11-digit local BD format (01XXXXXXXXX).
    Delegates to shared.phone → fazle_payroll_engine.normalizer.normalize_bd_phone.
    """
    return _sp_normalize_phone(phone)


class UserRole(TypedDict):
    role: str           # employee | client | escort_client | vendor | partner | known_contact | new_lead | admin
    employee_id: Optional[int]
    employee_name: Optional[str]
    designation: Optional[str]
    basic_salary: Optional[float]
    bkash_number: Optional[str]
    contact_id: Optional[int]
    display_name: Optional[str]
    company_name: Optional[str]
    relation_name: Optional[str]
    confidence: float   # 1.0 = exact match, 0.5 = partial


async def detect_role(raw_phone: str) -> UserRole:
    """
    Detect role for incoming phone number.
    Priority: admin → employee → client/contact → new_lead
    """
    phone = normalize_phone(raw_phone)
    if not phone:
        return _new_lead(raw_phone)

    # 1. Admin check (from settings)
    if phone in _get_admin_phones():
        emp = await _lookup_employee(phone)
        return UserRole(
            role="admin",
            employee_id=emp.get("employee_id") if emp else None,
            employee_name=emp.get("employee_name") if emp else None,
            designation=emp.get("designation") if emp else None,
            basic_salary=float(emp["basic_salary"]) if emp and emp.get("basic_salary") else None,
            bkash_number=emp.get("bkash_number") if emp else None,
            contact_id=None,
            display_name=None,
            company_name=None,
            relation_name="Admin",
            confidence=1.0,
        )

    # 1b. Accountant check (from settings.accountant_phone)
    acct_phone = _get_accountant_phone()
    if acct_phone and phone == acct_phone:
        emp = await _lookup_employee(phone)
        return UserRole(
            role="accountant",
            employee_id=emp.get("employee_id") if emp else None,
            employee_name=emp.get("employee_name") if emp else None,
            designation=emp.get("designation") if emp else "Accountant",
            basic_salary=float(emp["basic_salary"]) if emp and emp.get("basic_salary") else None,
            bkash_number=emp.get("bkash_number") if emp else None,
            contact_id=None,
            display_name=None,
            company_name=None,
            relation_name="Accountant",
            confidence=1.0,
        )

    # 2. Employee lookup
    emp = await _lookup_employee(phone)
    if emp:
        log.debug(f"[user_role] {phone} → employee: {emp['employee_name']}")
        return UserRole(
            role="employee",
            employee_id=emp["employee_id"],
            employee_name=emp["employee_name"],
            designation=emp.get("designation"),
            basic_salary=float(emp["basic_salary"]) if emp.get("basic_salary") else None,
            bkash_number=emp.get("bkash_number"),
            contact_id=None,
            display_name=emp["employee_name"],
            company_name=None,
            relation_name="Employee",
            confidence=1.0,
        )

    # 3. Contact lookup (client/vendor/partner)
    contact = await _lookup_contact(phone)
    if contact:
        relation = contact.get("relation_name", "")
        role = _map_relation_to_role(relation)
        log.debug(f"[user_role] {phone} → {role}: {contact.get('display_name')}")
        return UserRole(
            role=role,
            employee_id=None,
            employee_name=None,
            designation=None,
            basic_salary=None,
            bkash_number=None,
            contact_id=contact["contact_id"],
            display_name=contact.get("display_name"),
            company_name=contact.get("company_name"),
            relation_name=relation,
            confidence=1.0,
        )

    # 4. Unknown — new lead
    log.debug(f"[user_role] {phone} → new_lead")
    return _new_lead(phone)


def _map_relation_to_role(relation: str) -> str:
    r = (relation or "").lower()
    if r in ("escort_client", "escort client", "escort buyer"):
        return "escort_client"
    if r == "client":
        return "client"
    if r == "vendor":
        return "vendor"
    if r == "partner":
        return "partner"
    if r == "employee":
        return "employee"
    if r in ("accountant", "accounts"):
        return "accountant"
    return "known_contact"


def _new_lead(phone: str) -> UserRole:
    return UserRole(
        role="new_lead",
        employee_id=None,
        employee_name=None,
        designation=None,
        basic_salary=None,
        bkash_number=None,
        contact_id=None,
        display_name=None,
        company_name=None,
        relation_name=None,
        confidence=0.0,
    )


async def _lookup_employee(phone: str):
    """Try exact and stripped variants."""
    # Try '01XXXXXXXXXX' and '8801XXXXXXXXXX'
    variants = [phone]
    if phone.startswith("0"):
        variants.append("880" + phone[1:])   # 8801...
        variants.append("88" + phone)        # 8801... (some stored with 88 prefix)
    for v in variants:
        row = await fetch_one(
            """SELECT employee_id, employee_name, designation, basic_salary,
                      bkash_number, nagad_number, status
               FROM wbom_employees
               WHERE employee_mobile = $1""",
            v,
        )
        if row:
            return row
    return None


async def _lookup_contact(phone: str):
    """Lookup contact with relation type join."""
    variants = [phone]
    if phone.startswith("0"):
        variants.append("880" + phone[1:])
        variants.append("+880" + phone[1:])
        variants.append("88" + phone)
    for v in variants:
        row = await fetch_one(
            """SELECT c.contact_id, c.display_name, c.company_name,
                      c.whatsapp_number, rt.relation_name
               FROM wbom_contacts c
               LEFT JOIN wbom_relation_types rt
                      ON rt.relation_type_id = c.relation_type_id
               WHERE c.whatsapp_number = $1 AND c.is_active = true""",
            v,
        )
        if row:
            return row
    return None


def role_summary(ur: UserRole) -> str:
    """One-line summary for logging."""
    name = ur.get("employee_name") or ur.get("display_name") or "?"
    company = f" ({ur['company_name']})" if ur.get("company_name") else ""
    return f"[{ur['role'].upper()}] {name}{company}"
