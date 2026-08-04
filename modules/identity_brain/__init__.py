"""
Fazle Core — Identity Brain
Unified identity detection: who is messaging and what workflow should start?

Resolution order (highest priority wins):
  admin → family → accountant → vip_client → client_escort_buyer
  → repeat_client → vendor → employee → supervisor → candidate → unknown

Sources (checked in order):
  1. seed_rule      — static entries in fazle_contact_roles (highest trust)
  2. db_employee     — wbom_employees.employee_mobile match
  3. escort_roster   — wbom_escort_programs active duty (Assigned/Running)
  3.7. env_phone     — ESCORT_CLIENT_PHONES explicit match (Phase 1B.2,
                       source A — verified client_escort_buyer; empty list
                       never matches, see _matches_env_escort_phone)
  4. db_contact       — wbom_contacts.relation match, OR (checked first,
                       Phase 1B.2 source B) a contact-name "escort" token
                       match — both verified client_escort_buyer
  5. text_hint (candidate) — keyword analysis (candidate detection)
  6. text_hint (escort content) — UNVERIFIED keyword-only fallback (Phase
                       1B.2 Owner Decision 5); never persisted as identity,
                       see resolve_escort_client_identity()
  7. unknown        — no match found

Returns a dict compatible with the legacy UserRole TypedDict plus:
  identity_role, identity_confidence, identity_source, identity_priority

Phase 1B.2 (escort-client identity convergence, 2026-07-17): see
resolve_escort_client_identity() below for the canonical, verified/unverified-
aware resolver that webhook, poller, and message_router should use instead of
re-deriving escort-client trust independently. Full design in
knowledge_base/00_governance/management_decisions.md, Session 21 Phase 1B.2.
"""

import logging
import re
from typing import Optional

from app.database import fetch_one

log = logging.getLogger("fazle.identity_brain")

# ── Role priority table ────────────────────────────────────────────────────────
_ROLE_PRIORITY: dict[str, int] = {
    "admin":               200,
    "family":              100,
    "accountant":           95,
    "internal_coordinator": 93,  # dispatch/payroll/paperwork coordinator — Route A, seed_rule sourced
    "vip_client":           92,
    "client_escort_buyer":  90,
    "repeat_client":        75,
    "vendor":               70,
    "employee":             88,
    "escort":               88,  # same tier as employee — a wbom_employees row with an escort designation
    "security_guard":       88,  # same tier as employee — a wbom_employees row with a guard designation
    "supervisor":           80,
    "candidate":            30,
    "unknown":               0,
}

# ── Route B roles (2026-07-21 identity-first routing) ──────────────────────────
# Roles that message_router routes to the Ollama-only handler instead of the
# KB/template/multi-provider machinery. Kept here (not re-derived per caller)
# so bridge_poller's gate and message_router's branch can never drift apart.
ROUTE_B_ROLES = frozenset({"escort", "security_guard", "candidate", "unknown"})

# ── Designation → role mapping (escort/security_guard split) ──────────────────
_ESCORT_DESIGNATION_RE = re.compile(r"escort", re.IGNORECASE)
_GUARD_DESIGNATION_RE = re.compile(r"guard", re.IGNORECASE)


def _designation_to_role(designation: Optional[str]) -> str:
    """Map a wbom_employees.designation string to escort/security_guard/employee.
    Read-only classification of an already-fetched row — no new query."""
    d = (designation or "").strip()
    if not d:
        return "employee"
    if _ESCORT_DESIGNATION_RE.search(d):
        return "escort"
    if _GUARD_DESIGNATION_RE.search(d):
        return "security_guard"
    return "employee"

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

# ── Phase 1B.2 — escort-client identity convergence ────────────────────────────
# Contact-book name marker (source B/priority 3): a recognizable "escort"
# token, word-boundary matched so "escorted"/"escorting"/any name that merely
# contains the letters as part of a longer word never matches.
_ESCORT_NAME_MARKER_RE = re.compile(r"\bescort\b", re.IGNORECASE)


def _matches_escort_name_marker(name: str) -> bool:
    """True if a resolved contact-book name contains the "escort" token as a
    recognizable word (case-insensitive, punctuation/whitespace-tolerant via
    \\b word boundaries) — not a bare substring. Matches "Escort ABC Shipping",
    "ABC Escort Client", "MV Agent - Escort", "Client (Escort)",
    "escort-client"; rejects "escorted", "escorting", or any name where the
    letters are part of a different word."""
    return bool(name) and bool(_ESCORT_NAME_MARKER_RE.search(name))


def _matches_env_escort_phone(norm_phone: str) -> bool:
    """Phase 1B.2 source A — explicit ESCORT_CLIENT_PHONES membership.
    Empty list = no match (never "every phone matches"); that "open mode"
    semantics belongs solely to modules.escort's parser-stage gate
    (_is_allowed_escort_client), not to identity resolution. Reuses the one
    canonical, actually-used phone-list parser
    (modules.escort._escort_client_phone_list) rather than the dead,
    divergent shared/env.py implementation — see Session 21 Phase 1B.2 audit.
    Function-local import to avoid a circular import with modules.escort.

    2026-08-01 fix: modules.escort._escort_client_phone_list() returns
    canonical 01XXXXXXXXX form (Owner Directive 2026-07-01, commit 6aca81e
    2026-07-20 — that fix realigned the function's *other* two callers,
    which compare 01-format, but this caller's `norm_phone` is always
    8801XXXXXXXXX form, per this module's own _normalize_phone()). Direct
    equality could therefore never match any configured client from that
    commit onward — the 2026-07-20 audit's claim that identity_brain
    "already compares self-consistently" was wrong for this one call site.
    Confirmed live: a verified ESCORT_CLIENT_PHONES number was silently
    resolving to the wrong role because of exactly this. Compare against
    every canonical phone-variant instead of assuming a shared format."""
    if not norm_phone:
        return False
    try:
        from modules.escort import _escort_client_phone_list
    except ImportError:
        return False
    clients = _escort_client_phone_list()
    if not clients:
        return False
    return any(variant in clients for variant in _phone_variants(norm_phone))


def _normalize_phone(phone: str) -> Optional[str]:
    """Canonical 8801XXXXXXXXX form. Delegates to the documented single
    source of truth (PHONE_NORMALIZER_CONVENTION.md) instead of
    re-implementing digit-parsing/operator validation here."""
    from modules.phone_normalizer import normalize_phone as _pn
    return _pn(phone)


def _phone_variants(phone: str) -> list[str]:
    """[01XXXXXXXXX, 8801XXXXXXXXX, +8801XXXXXXXXX] for `= ANY($1)` lookups."""
    from modules.number_identity import normalize_phone as _variants
    return _variants(phone)


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
            role = _designation_to_role(emp.get("designation"))
            lifecycle = await _lookup_lifecycle(norm, emp)
            log.info("[identity] %s → %s (db_employee, lifecycle=%s)", norm, role, lifecycle)
            return _build(_phone=norm, role=role, confidence=88, source="db_employee",
                          priority=_ROLE_PRIORITY[role], emp=emp, lifecycle=lifecycle)

        # ── Step 3.5: operational evidence lookups ─────────────────────────────
        # 2026-08-01 fix: a one-off cash payment or attendance record must
        # never override an explicit, Owner-configured ESCORT_CLIENT_PHONES
        # entry. Confirmed live incident: a verified escort-client buyer
        # received a single incidental cash payment months into their
        # ongoing client relationship (fpe_cash_transactions auto-creates an
        # employee stub for any payout recipient — see the 2026-08-01 FPE
        # parser fix); every message from them was then misidentified as
        # role="employee" (source=cash_payment) forever after, silently
        # routing their real escort orders into the generic AI-reply/draft
        # path instead of the escort-order pipeline. A genuine Step 3
        # wbom_employees/fpe_employees record (above) is a materially
        # stronger signal and is intentionally still allowed to override —
        # only the weaker cash/attendance "operational evidence" signals are
        # gated here.
        _is_escort_client_phone = _matches_env_escort_phone(norm)

        if not _is_escort_client_phone:
            cash_emp = await _lookup_cash_identity(norm)
            if cash_emp:
                role = _designation_to_role(cash_emp.get("designation"))
                lifecycle = await _lookup_lifecycle(norm, cash_emp)
                log.info("[identity] %s → %s (cash_payment, lifecycle=%s)", norm, role, lifecycle)
                return _build(_phone=norm, role=role, confidence=86, source="cash_payment",
                              priority=_ROLE_PRIORITY[role], emp=cash_emp, lifecycle=lifecycle)

            attendance_emp = await _lookup_attendance_identity(norm)
            if attendance_emp:
                role = _designation_to_role(attendance_emp.get("designation"))
                lifecycle = await _lookup_lifecycle(norm, attendance_emp)
                log.info("[identity] %s → %s (attendance, lifecycle=%s)", norm, role, lifecycle)
                return _build(_phone=norm, role=role, confidence=86, source="attendance",
                              priority=_ROLE_PRIORITY[role], emp=attendance_emp, lifecycle=lifecycle)

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
                lifecycle="active",  # a live wbom_escort_programs row implies active duty
            )

        # ── Step 3.7: explicit ESCORT_CLIENT_PHONES match (Phase 1B.2, source A) ──
        # Runs only once no seed row, employee record, or active escort-roster
        # duty already matched above (cash/attendance operational evidence is
        # now gated ahead of this, not a peer of it — see the 2026-08-01 fix
        # note at Step 3.5) — an explicit .env allowlist entry is the
        # highest-priority *escort-client* source, but must never shadow a
        # real employee/admin/family/seed-role identity. Empty list = no
        # match here (never "every phone matches") — that "open mode"
        # semantics belongs solely to modules.escort's parser-stage gate
        # (_is_allowed_escort_client), not to identity resolution.
        if _is_escort_client_phone:
            log.info("[identity] %s → client_escort_buyer (env_phone)", norm)
            return _build(_phone=norm, role="client_escort_buyer", confidence=95, source="env_phone",
                          priority=_ROLE_PRIORITY["client_escort_buyer"])

        # ── Step 4: contact DB lookup ──────────────────────────────────────────
        contact = await _lookup_contact(norm)
        if contact:
            display_name = contact.get("display_name") or ""
            if _matches_escort_name_marker(display_name):
                # Phase 1B.2, source B — contact-book name contains a
                # recognizable "escort" token. Checked before the legacy
                # relation-based mapping below since it's a named, ratified
                # source (Owner priority #3); the relation column is a
                # separate, largely-unpopulated legacy mechanism.
                log.info("[identity] %s → client_escort_buyer (contact_name_marker)", norm)
                return _build_contact(
                    _phone=norm, role="client_escort_buyer", confidence=85,
                    source="contact_name_marker", priority=_ROLE_PRIORITY["client_escort_buyer"],
                    contact=contact,
                )
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
        # UNVERIFIED (Phase 1B.2, Owner Decision 5) — keyword-only inference
        # from message content, not a verified source (A/B/C above never
        # matched or this step wouldn't be reached). role/source deliberately
        # left as "repeat_client"/"text_hint" exactly as before this change —
        # repeat_client has other legitimate, unrelated uses elsewhere
        # (message_router KB/AI routing, DRAFT_ALWAYS_ROLES) that must not
        # regress. Callers that need the explicit verified/unverified split
        # should use resolve_escort_client_identity() below, which recognizes
        # this exact (role="repeat_client", source="text_hint") combination
        # as is_verified=False, source="unverified_message_content" — never
        # to be persisted as a fazle_contact_roles row from this signal alone.
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
        # admin_bridge3_number deliberately excluded (Owner decision 2026-08-04):
        # bridge3/REC2 is a technical WhatsApp bridge identity, not a human Admin —
        # reversing the same-day earlier bridge3-admin change. Do not re-add without
        # a fresh Owner decision. See knowledge_base/00_governance/management_decisions.md.
        for attr in ("admin_meta_number", "admin_bridge1_number", "admin_bridge2_number"):
            v = getattr(s, attr, "")
            if v:
                admin_phones.add(_normalize_phone(v))
        return phone in admin_phones
    except (AttributeError, LookupError, TypeError, ValueError):
        return False


async def _lookup_seed(phone: str) -> Optional[dict]:
    """Lookup highest-priority seed rule for this phone.
    Matches against all phone variants (01XXXXXXXXX, 8801XXXXXXXXX,
    +8801XXXXXXXXX) — fazle_contact_roles.phone is not guaranteed to be
    stored in the canonical form the caller passes in. Same class of bug
    as the 2026-08-01 env_phone incident documented above, a different
    call site. See identity_brain.md's standing rule."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT role, confidence, source, priority, notes
           FROM fazle_contact_roles
           WHERE phone = ANY($1)
             AND COALESCE((to_jsonb(fazle_contact_roles)->>'is_active')::boolean, true) = true
             AND LOWER(COALESCE(to_jsonb(fazle_contact_roles)->>'status', 'active')) = 'active'
           ORDER BY priority DESC
           LIMIT 1""",
        variants,
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
                  COALESCE(NULLIF(w.designation, ''), 'Staff') AS designation,
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


async def is_registered_employee_phone(phone: str) -> bool:
    """Public wrapper over _lookup_employee() -- True for ANY phone on file
    in wbom_employees/fpe_employees, active or inactive, regardless of the
    caller's own role/text context.

    2026-07-22 (no-reply gate exemption): message_router's saved-contact-name
    silent-skip gate (_should_silent_skip()) must never silence a phone that
    is genuinely a registered employee just because someone once saved their
    contact as "Escort Karim" or "Office Runner" -- that phone still needs
    to reach the payroll/Tier 10 flow. This is a status-blind existence
    check only; the caller (message_router) is responsible for routing
    Active vs Inactive differently afterward via the normal identity_brain/
    Tier 10 path -- this function does not itself decide access level.
    """
    return await _lookup_employee(phone) is not None


async def _lookup_cash_identity(phone: str) -> Optional[dict]:
    """Resolve operational identity from existing cash/payment records."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT COALESCE(e.id, t.employee_id) AS employee_id,
                  COALESCE(NULLIF(e.full_name, ''), NULLIF(w.employee_name, '')) AS employee_name,
                  COALESCE(NULLIF(w.designation, ''), 'Staff') AS designation,
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


async def _lookup_lifecycle(phone: str, emp: dict, *, on_leave_days: int = 14) -> str:
    """Read-only former/on-leave/active heuristic (2026-07-21 routing work).

    No new DB columns (Owner directive) — derived entirely from existing
    wbom_employees.status plus wbom_attendance recency:
      - status not 'Active' (e.g. 'Inactive')      → "former"
      - status 'Active' but no attendance row in the
        last `on_leave_days` days                  → "on_leave"
      - status 'Active' with recent attendance      → "active"
    Only meaningful for escort/security_guard/employee roles; callers for
    other roles don't need this and can ignore the field.
    """
    status = (emp.get("status") or "").strip().lower()
    if status and status != "active":
        return "former"
    employee_id = emp.get("employee_id")
    if not employee_id:
        return "active"
    try:
        recent = await fetch_one(
            """SELECT 1 FROM wbom_attendance
               WHERE employee_id = $1
                 AND attendance_date >= (CURRENT_DATE - $2::int)
               LIMIT 1""",
            employee_id, on_leave_days,
        )
    except (AttributeError, LookupError, TypeError, ValueError):
        return "active"  # fail open to "active" — this is context, not a gate
    return "active" if recent else "on_leave"


async def _lookup_escort_roster(phone: str) -> Optional[dict]:
    """Return any existing escort/ship identity before candidate classification."""
    variants = _phone_variants(phone)
    if not variants:
        return None
    row = await fetch_one(
        """SELECT program_id, escort_name, escort_mobile, master_mobile,
                  status, program_date,
                  CASE WHEN escort_mobile = ANY($1)
                       THEN 'escort' ELSE 'repeat_client' END AS identity_role
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
        """SELECT c.contact_id,
                  c.display_name,
                  COALESCE(c.relation, 'repeat_client') AS relation_name
              FROM wbom_contacts c
              WHERE c.whatsapp_number = ANY($1)
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
    lifecycle: Optional[str] = None,
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
        # 2026-07-21 routing work: context only, not a routing input — Route
        # B treats active/former/on_leave escort/guard identically per Owner
        # spec. None for roles where lifecycle isn't meaningful (family,
        # accountant, etc.).
        "identity_lifecycle":  lifecycle,
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
        "identity_lifecycle":  None,
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
        "identity_lifecycle":  None,
    }


# ── Phase 1B.2 — canonical escort-client identity resolver ────────────────────
# Single source of truth for "is this sender a verified escort client, and via
# which source" — a thin translation layer over detect_identity() (itself
# already the one place webhook and poller both call), so no ingestion path
# or business module re-implements escort-client detection independently.
# See knowledge_base/00_governance/management_decisions.md, Session 21 Phase
# 1B.2, for the full design and the Owner's ratified priority order.
_VERIFIED_ESCORT_SOURCES = frozenset({"env_phone", "contact_name_marker"})


async def resolve_escort_client_identity(
    phone: str,
    text: str = "",
    original_jid: str = "",
) -> dict:
    """
    Resolve the canonical escort-client identity for a sender, converging the
    three verified sources (env phone allowlist, stored contact-role,
    contact-name marker — checked by detect_identity() in that priority
    order) plus an explicitly *unverified* message-content fallback.

    Returns:
        is_escort_client: bool
        canonical_role: "client_escort_buyer" or None
        is_verified: bool — True only for a genuine verified source; the
            keyword-only Step 6 fallback is never verified, per Owner
            Decision 5, and must never be persisted as a contact-role row.
        source: "env_phone" | "stored_contact_role" | "contact_name_marker"
            | "unverified_message_content" | None
        normalized_phone, original_jid, resolved_contact_name: evidence
        evidence: dict with the raw identity_source/confidence for audit
    """
    identity = await detect_identity(phone, text)
    role = identity.get("identity_role") or identity.get("role")
    raw_source = identity.get("identity_source")
    norm = _normalize_phone(phone)
    base = {
        "normalized_phone": norm,
        "original_jid": original_jid or None,
        "resolved_contact_name": identity.get("display_name"),
    }

    if role == "client_escort_buyer":
        source = raw_source if raw_source in _VERIFIED_ESCORT_SOURCES else "stored_contact_role"
        return {
            "is_escort_client": True,
            "canonical_role": "client_escort_buyer",
            "is_verified": True,
            "source": source,
            "evidence": {"identity_source": raw_source, "identity_confidence": identity.get("identity_confidence")},
            **base,
        }

    if role == "repeat_client" and raw_source == "text_hint":
        # The keyword-only Step 6 fallback — a real, previously-unseen
        # sender whose message merely looked escort-shaped. Never verified,
        # never persisted, regardless of ESCORT_CLIENT_PHONES list state.
        return {
            "is_escort_client": False,
            "canonical_role": None,
            "is_verified": False,
            "source": "unverified_message_content",
            "evidence": {"identity_source": raw_source, "identity_confidence": identity.get("identity_confidence")},
            **base,
        }

    return {
        "is_escort_client": False,
        "canonical_role": None,
        "is_verified": False,
        "source": None,
        "evidence": {},
        **base,
    }
