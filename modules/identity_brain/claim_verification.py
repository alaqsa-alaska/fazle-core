"""
Fazle Core — Identity/Role Claim Verification (2026-08-20, Owner directive)

Read-only claim verification: resolve a bare name mentioned inside message
CONTENT (e.g. "Karim is supervisor") to a unique authoritative employee
record, and compare a claimed role/designation against that employee's
real current designation. Never guesses -- ambiguous or missing matches
are reported as such, not silently resolved to a single answer.

This is deliberately NOT identity_brain.detect_identity() -- that resolves
a message SENDER's own phone-backed identity (see this package's __init__
docstring for its 9-step cascade). This module answers a different
question: is a THIRD-PARTY claim made inside message text actually true,
checked against Fazle Core's own employee records? detect_identity() was
never designed for arbitrary third-party name lookups and must not be
repurposed for this -- a sender's own resolved identity/role is a
different concept from an employee's job-title designation.

Owner reasoning rule this module exists to enforce (see
knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md's Identity
section for the same rule stated for Hermes's own reasoning):

    User claims "Karim is supervisor"
      -> resolve Karim against Fazle Core (this module)
      -> verify employee exists AND current designation matches
      -> only THEN answer/act on the claim.

    NOT: "User said Karim is supervisor -> therefore Karim is supervisor."

Verification order (exact, per Owner spec):
  1. verified sender phone / explicit supplied phone
  2. employee ID (fpe_employees.id)
  3. normalized alias (fpe_employee_aliases)
  4. exact unique name match -- candidate only; never sufficient by itself
     for a SENSITIVE action (this function is read-only, so reporting
     "candidate found" here is safe -- callers performing a sensitive
     mutation must still resolve via phone/ID, per every fazle-mcp
     mutating tool's own existing ID-only contract)
  5. ambiguous / missing -> say so explicitly, never guess

Ambiguity handling follows the same shape as this package's sibling
pattern, fazle_payroll_engine/routes.py::_resolve_review_employee_
candidate() (collect every distinct matching employee; refuse to pick one
unless exactly one distinct match exists) -- reused here rather than
inventing a new ambiguity-handling convention.

Role source: fpe_employees.designation. Confirmed live-synced (not just a
one-time migration backfill) -- modules/admin_employees/__init__.py's
update_employee()/create_employee() both push designation changes into
fpe_employees on every wbom_employees edit (best-effort, non-fatal sync).
"""
from __future__ import annotations

import re
from typing import Optional

from app.database import fetch_all, fetch_one
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone, normalize_name

# Purpose-built role-claim normalizer -- same substring-classification
# style as identity_brain._designation_to_role() (this package's
# __init__.py), but a different, broader vocabulary: _designation_to_role
# only ever classifies escort/security_guard/employee (it never produces
# "supervisor" -- see that function's own docstring), because it answers a
# routing question, not a job-title question. This one classifies actual
# job-title/designation text, which is what an employee's designation
# column and a human's role CLAIM both are.
_ROLE_PATTERNS: dict[str, re.Pattern] = {
    "supervisor":        re.compile(r"supervisor|সুপারভাইজার", re.IGNORECASE),
    "security_guard":    re.compile(r"guard|গার্ড", re.IGNORECASE),
    "escort":            re.compile(r"escort|এসকর্ট", re.IGNORECASE),
    "incharge":          re.compile(r"in.?charge|ইনচার্জ", re.IGNORECASE),
    "operation_officer": re.compile(r"operation officer|অপারেশন অফিসার", re.IGNORECASE),
    "marketing_officer": re.compile(r"marketing officer|মার্কেটিং অফিসার", re.IGNORECASE),
}


def _normalize_role_claim(text: Optional[str]) -> Optional[str]:
    """Classify a free-text role claim/designation string into one of
    _ROLE_PATTERNS' keys, or None if unrecognized. Substring match, same
    convention as identity_brain._designation_to_role(). "Ghat Supervisor"
    / "Security Supervisor" / "সুপারভাইজার" all classify as "supervisor"."""
    if not text:
        return None
    for role, pattern in _ROLE_PATTERNS.items():
        if pattern.search(text):
            return role
    return None


async def verify_employee_claim(
    *,
    claimed_name: Optional[str] = None,
    claimed_role: Optional[str] = None,
    sender_phone: Optional[str] = None,
    employee_id: Optional[int] = None,
) -> dict:
    """Verify a claim like "Karim is supervisor" against fpe_employees.
    Read-only. Never raises; never guesses.

    Returns a JSON-serializable dict, always with an "outcome" key, one of:
      "not_found"          -- no employee matches the given evidence
      "ambiguous"           -- 2+ distinct candidates match; not chosen
                               ("candidates" key lists them)
      "role_mismatch"       -- unique employee found, but claimed_role
                               does not match their actual designation
                               ("actual_designation" + "claimed_role" keys)
      "verified"            -- unique employee, active, role matches (or
                               no role was claimed to check)
      "verified_inactive"   -- unique employee, role matches, but the
                               employee's current status is not active
                               (distinguished per Owner spec: inactive/
                               former employee must not read the same as
                               a current active one)
    """
    candidates: list[dict] = []
    match_type: Optional[str] = None

    # 1. verified sender phone / explicit supplied phone
    phone = normalize_bd_phone(sender_phone) if sender_phone else None
    if phone:
        rows = await fetch_all(
            """
            SELECT id, full_name, designation, status, employee_code,
                   primary_phone, employee_id_phone
            FROM fpe_employees
            WHERE employee_id_phone = $1 OR primary_phone = $1
            """,
            phone,
        )
        if rows:
            candidates = [dict(r) for r in rows]
            match_type = "phone"

    # 2. employee ID
    if not candidates and employee_id is not None:
        row = await fetch_one(
            """
            SELECT id, full_name, designation, status, employee_code,
                   primary_phone, employee_id_phone
            FROM fpe_employees WHERE id = $1
            """,
            employee_id,
        )
        if row:
            candidates = [dict(row)]
            match_type = "employee_id"

    # 3. normalized alias
    if not candidates and claimed_name:
        name_norm = normalize_name(claimed_name)
        if name_norm:
            rows = await fetch_all(
                """
                SELECT e.id, e.full_name, e.designation, e.status, e.employee_code,
                       e.primary_phone, e.employee_id_phone
                FROM fpe_employee_aliases a
                JOIN fpe_employees e ON e.id = a.employee_id
                WHERE a.alias_type = 'name' AND a.alias_value = $1
                """,
                name_norm,
            )
            if rows:
                candidates = [dict(r) for r in rows]
                match_type = "alias_name"

    # 4. exact unique name match (candidate only)
    if not candidates and claimed_name:
        name_norm = normalize_name(claimed_name)
        if name_norm:
            rows = await fetch_all(
                """
                SELECT id, full_name, designation, status, employee_code,
                       primary_phone, employee_id_phone
                FROM fpe_employees
                WHERE name_normalized = $1 OR lower(full_name) = lower($2)
                """,
                name_norm, claimed_name.strip(),
            )
            if rows:
                candidates = [dict(r) for r in rows]
                match_type = "name_exact"

    if not candidates:
        who = claimed_name or phone or employee_id
        return {
            "outcome": "not_found",
            "message": f"'{who}' এর সাথে মিলে এমন কোনো কর্মী পাওয়া যায়নি।",
            "claimed_name": claimed_name,
            "claimed_role": claimed_role,
        }

    # 5. ambiguity check -- distinct-employee dedup, refuse to pick one
    distinct = {c["id"]: c for c in candidates}
    if len(distinct) > 1:
        return {
            "outcome": "ambiguous",
            "message": (
                f"একাধিক কর্মী '{claimed_name}' নামের সাথে মিলছে — "
                "নিশ্চিত করতে ফোন নম্বর বা কর্মী ID দিন।"
            ),
            "candidates": [
                {
                    "employee_id": c["id"],
                    "name": c["full_name"],
                    "designation": c["designation"],
                    "status": c["status"],
                }
                for c in distinct.values()
            ],
            "claimed_name": claimed_name,
            "claimed_role": claimed_role,
        }

    emp = next(iter(distinct.values()))
    actual_role = _normalize_role_claim(emp.get("designation"))
    claimed_role_norm = _normalize_role_claim(claimed_role) if claimed_role else None
    is_active = (emp.get("status") or "").strip().lower() == "active"
    lifecycle_status = "active" if is_active else "inactive"

    role_matches = True
    if claimed_role:
        role_matches = claimed_role_norm is not None and actual_role == claimed_role_norm

    if claimed_role and not role_matches:
        return {
            "outcome": "role_mismatch",
            "message": (
                f"তথ্য মিলছে না — {emp['full_name']}-এর বর্তমান পদবী "
                f"'{emp.get('designation') or 'অজানা'}', কিন্তু দাবি করা হয়েছে '{claimed_role}'।"
            ),
            "employee_id": emp["id"],
            "employee_name": emp["full_name"],
            "claimed_role": claimed_role,
            "actual_designation": emp.get("designation"),
            "lifecycle_status": lifecycle_status,
            "match_type": match_type,
        }

    outcome = "verified" if is_active else "verified_inactive"
    if is_active:
        message = f"{emp['full_name']} সত্যিই '{emp.get('designation')}' পদে আছেন — তথ্য মিলেছে।"
    else:
        message = (
            f"{emp['full_name']} '{emp.get('designation')}' পদে ছিলেন, কিন্তু বর্তমানে সক্রিয় (active) নন — "
            "যাচাই করা তথ্য মিলেছে, তবে কর্মীটি বর্তমানে নিষ্ক্রিয়।"
        )
    return {
        "outcome": outcome,
        "message": message,
        "employee_id": emp["id"],
        "employee_name": emp["full_name"],
        "claimed_role": claimed_role,
        "actual_designation": emp.get("designation"),
        "lifecycle_status": lifecycle_status,
        "match_type": match_type,
    }
