"""
Fazle Core — Office-Payroll Eligibility (Owner decision, 2026-07-10/11)

Centralized classification of which wbom_employees rows may participate in
monthly office payroll. This is the single source of truth for that
question — do not duplicate this logic in routes, payroll jobs, tests, or
frontend code.

Canonical separation (Owner decision): escort employees are paid exclusively
through Escort Programs -> Release Confirmation -> Escort Payment Draft ->
Cash Transaction. They must never enter this eligibility path, and this
module's classification never runs escort payment logic or touches
wbom_escort_programs.

Structural eligibility (this module) is necessary but not sufficient for a
payable payroll amount — a structurally-eligible employee still needs
approved duty-day attendance for the selected month (a separate, later
check against wbom_attendance, done by the eligibility-preview/dry-run
layer, not here).
"""
from __future__ import annotations

from typing import Optional

# Section 1.2 "Name keyword classification" — case-insensitive substring,
# may appear anywhere in the name (first/middle/last/anywhere).
OFFICE_NAME_KEYWORDS = ("al-aqsa", "al-manaratayn", "office", "operation", "sv")

# Section 1.2 "Designation classification" — an approved non-Escort
# designation is sufficient on its own, independent of the name check.
OFFICE_DESIGNATIONS = {
    "staff", "guard", "labor", "labour", "day labour", "day laborer",
    "supervisor", "operation officer", "office assistant", "security guard",
}

# Best-effort placeholder/bookkeeping/test-record detector (Section 1.1.5).
# wbom_employees has no dedicated "is this a real person" flag, so this is
# a disclosed heuristic, not a guarantee — flagged for Owner refinement.
# Concrete production evidence this was built against: "OfficeExpance"
# (a bookkeeping row, designation=Labor, salary=0) and "Phase18B Test" (a
# dev/test row, designation=Guard, salary=15000, status=Inactive).
PLACEHOLDER_NAME_HINTS = ("test", "phase", "sample", "demo", "expense", "expance")


def classify_office_staff(employee: dict) -> dict:
    """
    Structural office-payroll eligibility for one wbom_employees row.

    Returns {"eligible": bool, "is_office_staff": Optional[bool], "reason": Optional[str]}
    `reason` is populated only when eligible is False, using one of the
    canonical exclusion strings from the Owner's spec. `eligible=True`
    carries no reason — the eligibility-preview layer fills in the
    canonical inclusion reason once it also knows the attendance result.
    """
    status = (employee.get("status") or "").strip().lower()
    if status != "active":
        return {"eligible": False, "is_office_staff": None, "reason": "Excluded: inactive employee"}

    designation = (employee.get("designation") or "").strip()
    designation_lower = designation.lower()
    if designation_lower == "escort":
        return {"eligible": False, "is_office_staff": False, "reason": "Excluded: Escort-only employee"}

    name = (employee.get("employee_name") or "").strip()
    name_lower = name.lower()
    if any(hint in name_lower for hint in PLACEHOLDER_NAME_HINTS):
        return {"eligible": False, "is_office_staff": None, "reason": "Excluded: bookkeeping/test/system record"}

    salary = employee.get("basic_salary")
    salary_value = float(salary) if salary is not None else 0.0
    if salary_value <= 0:
        return {"eligible": False, "is_office_staff": None, "reason": "Excluded: missing or zero basic salary"}

    name_match = any(kw in name_lower for kw in OFFICE_NAME_KEYWORDS)
    designation_match = designation_lower in OFFICE_DESIGNATIONS
    if not (name_match or designation_match):
        return {"eligible": False, "is_office_staff": False, "reason": "Excluded: not classified as office staff"}

    return {"eligible": True, "is_office_staff": True, "reason": None}
