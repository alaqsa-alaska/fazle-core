"""
Fazle Core — Identity Brain
Unified identity detection: who is messaging and what workflow should start?

Resolution order (highest priority wins):
  admin → family → accountant → vip_client → client_escort_buyer
  → repeat_client → vendor → employee → supervisor → candidate → unknown

Sources (checked in order):
  1. seed_rule     — static entries in fazle_contact_roles (highest trust)
  2. db_employee   — wbom_employees.employee_mobile match
  3. escort_roster — wbom_escort_programs active duty (Assigned/Running)
  4. db_contact    — wbom_contacts / wbom_relation_types match
  5. text_hint     — keyword analysis of message text (candidate detection)
  6. unknown       — no match found

Returns a dict compatible with the legacy UserRole TypedDict plus:
  identity_role, identity_confidence, identity_source, identity_priority
"""

import logging
import re
from typing import Optional

from app.database import fetch_one

log = logging.getLogger("fazle.identity_brain")

# ── Role priority table ────────────────────────────────────────────────────────
_ROLE_PRIORITY: dict[str, int] = {
    "admin":              200,
    "family":             100,
    "accountant":          95,
    "vip_client":          92,
    "client_escort_buyer": 90,
    "repeat_client":       75,
    "vendor":              70,
    "employee":            88,
    "supervisor":          80,
    "candidate":           30,
    "unknown":              0,
}

# ── Candidate keyword triggers ─────────────────────────────────────────────────
_CANDIDATE_KEYWORDS = [
    "চাকরি", "চাকরী", "কাজ চাই", "job", "apply", "বেতন কত",
    "নিয়োগ", "recruitment", "vacancy", "আবেদন",
]

# ── Escort-buyer content patterns ─────────────────────────────────────────────
_ESCORT_CONTENT_RE = re.compile(
    r"\b(m\.?v\.?|mother\s*vessel|lighter|escort\s*lagbe|m\.?t\.|এমভি|"
    r"destination|lighter\s*vessel)\b",
    re.IGNORECASE,
)


def _normalize_phone(phone: str) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if digits.startswith("880") and len(digits) == 13:
        canonical = digits
    elif digits.startswith("0") and len(digits) == 11:
        canonical = "880" + digits[1:]
    elif digits.startswith("1") and len(digits) == 10:
        canonical = "880" + digits
    else:
        return None
    return canonical if canonical[3:5] in {"11", "12", "13", "14", "15", "16", "17", "18", "19"} else None


def _phone_variants(phone: str) -> list[str]:
    canonical = _normalize_phone(phone)
    if not canonical:
        return []
    return ["0" + canonical[3:], canonical, "+" + canonical]


async def detect_identity(phone: str, text: str = "") -> dict:
    """
    Unified identity detection. Returns a dict with role, confidence, source,
    priority and full context (employee_id, employee_name, etc.).

    Always safe to call — never raises, falls back to unknown.
    """
    try:
        norm = _normalize_phone(phone)
        if not norm:
            return _unknown(phone)

        # ── Step 1: admin check from settings ─────────────────────────────────
        admin_result = _check_admin_settings(norm)
        if admin_result:
            emp = await _lookup_employee(norm)
            return _build(
                _phone=norm, role="admin", confidence=100, source="settings",
                priority=200, emp=emp,
            )

        # ── Step 2: seed rule lookup (fazle_contact_roles) ────────────────────
        seed = await _lookup_seed(norm)
        if seed:
            role = seed["role"]
            confidence = seed["confidence"]
            priority = seed["priority"]
            source = seed["source"] or "seed_rule"
            notes = seed.get("notes", "")

            # For employee / supervisor seed roles — enrich with DB record
            emp = await _lookup_employee(norm) if role in ("employee", "supervisor", "admin") else None
            log.info("[identity] %s → %s (seed, conf=%s, pri=%s)", norm, role, confidence, priority)
            return _build(_phone=norm, role=role, confidence=confidence, source=source,
                          priority=priority, emp=emp, notes=notes)

        # ── Step 3: employee DB lookup ─────────────────────────────────────────
        emp = await _lookup_employee(norm)
        if emp:
            log.info("[identity] %s → employee (db_employee)", norm)
            return _build(_phone=norm, role="employee", confidence=88, source="db_employee",
                          priority=_ROLE_PRIORITY["employee"], emp=emp)

        # ── Step 3.5: operational evidence lookups ─────────────────────────────
        cash_emp = await _lookup_cash_identity(norm)
        if cash_emp:
            log.info("[identity] %s → employee (cash_payment)", norm)
            return _build(_phone=norm, role="employee", confidence=86, source="cash_payment",
                          priority=_ROLE_PRIORITY["employee"], emp=cash_emp)

        attendance_emp = await _lookup_attendance_identity(norm)
        if attendance_emp:
            log.info("[identity] %s → employee (attendance)", norm)
            return _build(_phone=norm, role="employee", confidence=86, source="attendance",
                          priority=_ROLE_PRIORITY["employee"], emp=attendance_emp)

        escort_row = await _lookup_escort_roster(norm)
        if escort_row:
            escort_role = escort_row.get("identity_role") or "employee"
            _esc_name = escort_row.get("escort_name") or ""
            _esc_status = escort_row.get("status") or ""
            _esc_date = escort_row.get("program_date") or ""
            log.info(
                "[identity] %s → %s (escort_roster, name=%r, status=%r, date=%s)",
                norm, escort_role, _esc_name, _esc_status, _esc_date,
            )
            return _build(
                _phone=norm, role=escort_role, confidence=85, source="escort_roster",
                priority=_ROLE_PRIORITY.get(escort_role, _ROLE_PRIORITY["employee"]),
                notes=f"escort_duty:{_esc_status}:{_esc_date}",
            )

        # ── Step 4: contact DB lookup ──────────────────────────────────────────
        contact = await _lookup_contact(norm)
        if contact:
            relation = (contact.get("relation_name") or "").lower()
            role = _map_relation(relation)
            pri = _ROLE_PRIORITY.get(role, 50)
            log.info("[identity] %s → %s (db_contact, relation=%s)", norm, role, relation)
            return _build_contact(_phone=norm, role=role, confidence=80, source="db_contact",
                                  priority=pri, contact=contact)

        # ── Step 5: text-hint candidate detection ─────────────────────────────
        if text and _is_candidate_text(text):
            log.info("[identity] %s → candidate (text_hint)", norm)
            return _build(_phone=norm, role="candidate", confidence=50, source="text_hint",
                          priority=_ROLE_PRIORITY["candidate"])

        # ── Step 6: escort content from unknown sender ─────────────────────────
        if text and _ESCORT_CONTENT_RE.search(text):
            log.info("[identity] %s → repeat_client (text_hint, escort content)", norm)
            return _build(_phone=norm, role="repeat_client", confidence=40, source="text_hint",
                          priority=_ROLE_PRIORITY["repeat_client"])

        log.info("[identity] %s → unknown", norm)
        return _unknown(norm)

    except (AttributeError, LookupError, TypeError, ValueError) as e:
        log.error("[identity] detect_identity error for %s: %s", phone, e)
        return _unknown(phone)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _check_admin_settings(phone: str) -> bool:
    try:
        from app.config import get_settings
        s = get_settings()
        admin_phones = set(_normalize_phone(p) for p in s.admin_number_list if p)
        for attr in ("admin_meta_number", "admin_bridge1_number", "admin_bridge2_number"):
            v = getattr(s, attr, "")
            if v:
                admin_phones.add(_normalize_phone(v))
        return phone in admin_phones
    except (AttributeError, LookupError, TypeError, ValueError):
        return False


async def _lookup_seed(phone: str) -> Optional[dict]:
    """Lookup highest-priority seed rule for this phone."""
    row = await fetch_one(
        """SELECT role, confidence, source, priority, notes
           FROM fazle_contact_roles
           WHERE phone = $1 AND is_active = true
           ORDER BY priority DESC
           LIMIT 1""",
        phone,
    )
    return dict(row) if row else None


async def _lookup_employee(phone: str) -> Optional[dict]:
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT employee_id, employee_name, designation,
                  basic_salary, bkash_number, status
           FROM wbom_employees
           WHERE employee_mobile = ANY($1)
           ORDER BY employee_id DESC
           LIMIT 1""",
        variants,
    )
    if row:
        return dict(row)

    # Fallback to FPE-linked employees created through payroll/admin/escort flows.
    row = await fetch_one(
        """SELECT w.employee_id,
                  COALESCE(NULLIF(w.employee_name, ''), NULLIF(e.full_name, '')) AS employee_name,
                  COALESCE(NULLIF(w.designation, ''), NULLIF(e.department, ''), 'Staff') AS designation,
                  w.basic_salary,
                  w.bkash_number,
                  COALESCE(w.status, CASE WHEN e.status = 'active' THEN 'Active' ELSE 'Inactive' END) AS status
           FROM fpe_employees e
           LEFT JOIN wbom_employees w ON w.employee_id = e.wbom_employee_id
           WHERE e.status = 'active'
             AND (e.primary_phone = ANY($1) OR e.employee_id_phone = ANY($1))
           ORDER BY e.id DESC
           LIMIT 1""",
        variants,
    )
    return dict(row) if row and row.get("employee_id") else None


async def _lookup_cash_identity(phone: str) -> Optional[dict]:
    """Resolve operational identity from existing cash/payment records."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT COALESCE(e.id, t.employee_id) AS employee_id,
                  COALESCE(NULLIF(e.full_name, ''), NULLIF(w.employee_name, '')) AS employee_name,
                  COALESCE(NULLIF(e.designation, ''), NULLIF(w.designation, '')) AS designation,
                  COALESCE(e.basic_salary, w.basic_salary) AS basic_salary,
                  w.bkash_number,
                  COALESCE(e.status, w.status) AS status
           FROM fpe_cash_transactions t
           LEFT JOIN fpe_employees e ON e.id = t.employee_id
           LEFT JOIN wbom_employees w ON w.employee_id = e.wbom_employee_id
           WHERE t.employee_phone = ANY($1)
              OR t.employee_id_phone = ANY($1)
              OR t.payout_phone = ANY($1)
              OR e.primary_phone = ANY($1)
           ORDER BY t.id DESC
           LIMIT 1""",
        variants,
    )
    if not row:
        return None
    result = dict(row)
    result.setdefault("employee_id", None)
    result.setdefault("employee_name", None)
    result.setdefault("designation", None)
    result.setdefault("basic_salary", None)
    result.setdefault("bkash_number", None)
    result.setdefault("status", None)
    return result


async def _lookup_attendance_identity(phone: str) -> Optional[dict]:
    """Resolve an employee through attendance joined to the employee register."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT e.employee_id, e.employee_name, e.designation,
                  e.basic_salary, e.bkash_number, e.status
           FROM wbom_attendance a
           JOIN wbom_employees e ON e.employee_id = a.employee_id
           WHERE e.employee_mobile = ANY($1)
           ORDER BY a.attendance_date DESC, a.attendance_id DESC
           LIMIT 1""",
        variants,
    )
    return dict(row) if row else None


async def _lookup_escort_roster(phone: str) -> Optional[dict]:
    """Return any existing escort/ship identity before candidate classification."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT program_id, escort_name, escort_mobile, master_mobile,
                  status, program_date,
                  CASE WHEN escort_mobile = ANY($1)
                       THEN 'employee' ELSE 'repeat_client' END AS identity_role
           FROM wbom_escort_programs
           WHERE escort_mobile = ANY($1) OR master_mobile = ANY($1)
           ORDER BY program_date DESC NULLS LAST, program_id DESC
           LIMIT 1""",
        variants,
    )
    return dict(row) if row else None


async def _lookup_contact(phone: str) -> Optional[dict]:
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT c.contact_id, c.display_name, c.company_name,
                  COALESCE(rt.relation_name, c.relation) AS relation_name
           FROM wbom_contacts c
           LEFT JOIN wbom_relation_types rt
                  ON rt.relation_type_id = c.relation_type_id
           WHERE c.whatsapp_number = ANY($1) AND c.is_active = true
           ORDER BY c.contact_id DESC
           LIMIT 1""",
        variants,
    )
    return dict(row) if row else None


def _map_relation(relation: str) -> str:
    r = relation.lower()
    if r in ("escort_client", "escort client", "escort buyer", "client_escort_buyer"):
        return "client_escort_buyer"
    if r in ("vip", "vip_client"):
        return "vip_client"
    if r == "vendor":
        return "vendor"
    if r == "accountant":
        return "accountant"
    if r in ("employee", "guard", "staff"):
        return "employee"
    if r in ("supervisor", "manager"):
        return "supervisor"
    if r == "family":
        return "family"
    return "repeat_client"


def _is_candidate_text(text: str) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in _CANDIDATE_KEYWORDS)


def _build(
    _phone: str,
    role: str,
    confidence: int,
    source: str,
    priority: int,
    emp: Optional[dict] = None,
    notes: str = "",
) -> dict:
    return {
        # Legacy UserRole fields (backward compatible)
        "role":           role,
        "employee_id":    emp["employee_id"] if emp else None,
        "employee_name":  emp["employee_name"] if emp else None,
        "designation":    emp["designation"] if emp else None,
        "basic_salary":   float(emp["basic_salary"]) if emp and emp.get("basic_salary") else None,
        "bkash_number":   emp.get("bkash_number") if emp else None,
        "contact_id":     None,
        "display_name":   emp["employee_name"] if emp else None,
        "company_name":   None,
        "relation_name":  role.replace("_", " ").title(),
        "confidence":     float(confidence) / 100,
        # Identity brain extensions
        "identity_role":       role,
        "identity_confidence": confidence,
        "identity_source":     source,
        "identity_priority":   priority,
        "identity_notes":      notes,
    }


def _build_contact(
    _phone: str,
    role: str,
    confidence: int,
    source: str,
    priority: int,
    contact: dict,
) -> dict:
    return {
        "role":           role,
        "employee_id":    None,
        "employee_name":  None,
        "designation":    None,
        "basic_salary":   None,
        "bkash_number":   None,
        "contact_id":     contact.get("contact_id"),
        "display_name":   contact.get("display_name"),
        "company_name":   contact.get("company_name"),
        "relation_name":  contact.get("relation_name", role),
        "confidence":     float(confidence) / 100,
        "identity_role":       role,
        "identity_confidence": confidence,
        "identity_source":     source,
        "identity_priority":   priority,
        "identity_notes":      "",
    }


def _unknown(_phone: str) -> dict:
    return {
        "role":           "unknown",
        "employee_id":    None,
        "employee_name":  None,
        "designation":    None,
        "basic_salary":   None,
        "bkash_number":   None,
        "contact_id":     None,
        "display_name":   None,
        "company_name":   None,
        "relation_name":  None,
        "confidence":     0.0,
        "identity_role":       "unknown",
        "identity_confidence": 0,
        "identity_source":     "none",
        "identity_priority":   0,
        "identity_notes":      "",
    }
