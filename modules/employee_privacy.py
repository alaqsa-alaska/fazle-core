"""Stage G3/G17 (2026-08-28) -- formalizes the CRITICAL PRIVACY RULE: an
employee may receive only information belonging to that employee unless
an existing role/permission explicitly grants wider access.

Discovery, not invention: this protection already exists structurally in
production, traced across two independent mechanisms this stage found by
reading the actual code (not assumed):

  1. modules.route_b (the Ollama-only reply generator for escort/
     security_guard/candidate/unknown roles) never queries wbom_employees/
     fpe_cash_transactions/wbom_escort_programs/attendance tables at all
     (confirmed by its own module docstring) -- it structurally cannot
     leak private business data because it has no DB access to it.

  2. modules.employee_conversation.resolve_employee_identity()'s "4. Name
     match" branch explicitly sets verified=False ("name match is not
     fully verified") -- a sender claiming another employee's name in
     free text can never reach `verified=True`; modules.route_b_handoff.
     _check_employee_claim_handoff() routes every non-phone-verified claim
     to ADMIN REVIEW ONLY, never to actual data access.

Both mechanisms anchor identity to the WhatsApp-verified sender phone
(modules.identity_brain._lookup_employee(phone) /
_lookup_attendance_identity(phone) / _lookup_cash_identity(phone) --
Stage E6/E7's own proven linkage), never to a name/employee_id a sender
merely typed. This module doesn't replace any of that -- it exposes the
same guarantee as one small, directly-testable, reusable function so any
NEW Stage G code (or a future caller) has one obvious, auditable place to
call instead of re-deriving "is this allowed" from role strings inline.
"""
from __future__ import annotations

from typing import Optional


def is_authorized_for_employee_data(
    requester_employee_id: Optional[int],
    requester_identity_role: str,
    target_employee_id: Optional[int],
) -> bool:
    """The CRITICAL PRIVACY RULE, as one testable function.

    requester_employee_id: the id resolved from the REQUESTER's own
    WhatsApp-verified sender phone (modules.identity_brain._lookup_employee
    (phone) -- never from a name/id the requester merely typed in message
    text; a name-match-only resolution, per resolve_employee_identity()'s
    own verified=False contract, must never be passed here as if it were
    a real requester_employee_id).

    requester_identity_role: 'admin' (or 'accountant', which -- per
    modules.identity_brain._ROLE_PRIORITY -- sits above ordinary employee
    tiers) retains broader access per existing RBAC, unchanged by this
    rule (Stage G17's own explicit carve-out). Every other role is scoped
    to its own data only.

    target_employee_id: whose data is about to be disclosed.

    Returns True only when:
      - requester is admin/accountant (existing broader RBAC), OR
      - requester_employee_id is known (identity was actually resolved --
        unknown/ambiguous identity is never authorized, Stage G3's
        explicit rule) AND equals target_employee_id.

    Never returns True merely because a target_employee_id could not be
    resolved either (both None) -- an unresolved target is not "no
    restriction", it's "nothing to disclose"."""
    if requester_identity_role in ("admin", "accountant"):
        return True
    if requester_employee_id is None:
        return False
    if target_employee_id is None:
        return False
    return requester_employee_id == target_employee_id


async def resolve_requester_employee_id(phone: str) -> Optional[int]:
    """Thin, explicit wrapper over the existing, already-proven
    phone-anchored resolver (Stage E6's own linkage) -- the ONLY
    legitimate source for requester_employee_id above. Never call this
    with anything but the WhatsApp-verified sender phone."""
    from modules.identity_brain import _lookup_employee
    emp = await _lookup_employee(phone)
    return emp.get("employee_id") if emp else None


async def get_own_payroll_summary(requester_phone: str) -> dict:
    """Stage G6.8 -- Payroll/Salary READ, promoted from PARTIAL to
    SUPPORTED: the strongest possible privacy guarantee by construction --
    there is no parameter through which a caller can ask for anyone
    else's payroll data. requester_phone is resolved via the same
    phone-anchored identity_brain._lookup_employee() this module already
    uses for attendance/cash (Stage E6/E7's proven linkage); the returned
    fields come from that SAME resolved row, never a second lookup keyed
    on a name/id the requester supplied. Bounded, no raw dump, no write.

    Deliberately separate from recruitment's own salary-policy text
    (modules.recruitment_flow's templates, e.g. "বয়সসীমা ১৮-৫৫...
    বেতন...") -- that answers "what does this JOB pay", a different
    question from "what is MY payroll record", a structurally different
    data source (wbom_employees.basic_salary vs a policy-text template),
    so there is no comingling risk between the two."""
    employee_id = await resolve_requester_employee_id(requester_phone)
    if employee_id is None:
        return {
            "ok": False,
            "error": "identity not resolved from this phone -- cannot disclose payroll data to an unverified sender",
        }
    from modules.identity_brain import _lookup_employee
    emp = await _lookup_employee(requester_phone)
    if not emp:
        return {"ok": False, "error": "employee record not found"}
    return {
        "ok": True,
        "employee_id": emp.get("employee_id"),
        "employee_name": emp.get("employee_name"),
        "designation": emp.get("designation"),
        "basic_salary": float(emp["basic_salary"]) if emp.get("basic_salary") is not None else None,
        "status": emp.get("status"),
        "note": "own record only -- resolved from the requester's own WhatsApp-verified phone",
    }
