"""
Fazle Core — Dispatch Phase 2A.1 (read-only availability and conflict
detection for escort workers).

Owner-approved scope (2026-07-18): escort workers assigned via
wbom_escort_programs ONLY. No office employees, supervisors, drivers, or
generalized scheduling. Read-only: no assignment writes, no reservations,
no auto-assignment, no WhatsApp messages, no LLM ranking.

Canonical assignment source: wbom_escort_programs (NOT escort_roster_entries
-- that table may be joined for display/consistency checks only, per Owner
decision). escort_roster_entries has its own, separately-fixed concurrency
history (Phase 1I/2B) unrelated to this read-only module.

Status-to-bookability (reuses the EXACT rule already proven in production
by modules.escort_lifecycle.find_active_program_for_employee() -- not
invented here):
    escort_employee_id IS NOT NULL AND status NOT IN ('completed', 'cancelled', 'escort_changed')
'draft' programs have no escort_employee_id yet (set only by the
ESCORTCONFIRM admin command, modules/escort/__init__.py) and so can never
structurally conflict with anyone -- confirmed by code inspection, not
assumed. Statuses outside the known set {draft, confirmed, running,
completed, cancelled, escort_changed} (e.g. legacy 'expired'/'assigned'
values found only in modules/escort_roster/db.py's status_map, with no
active setter found anywhere in current code) are treated as
data-quality-unknown, never silently assumed bookable or free.

'escort_changed' (Phase 2A.3, migration 049): the earlier segment of a
vessel operation, replaced by a later escort before normal completion. It
is historical and non-blocking, exactly like 'completed'/'cancelled' for
availability purposes -- never treated as cancelled (it's a real duty
segment that happened), never treated as still-active.

Shift/time model: reuses modules.escort_roster.calculations.SHIFT_ORDER
(["D", "N"]) verbatim. A (date, shift) pair maps to a single sortable
integer ordinal: date.toordinal() * 2 + shift_index. This makes inclusive
interval overlap ("does [start_a, end_a] intersect [start_b, end_b]")
match count_shifts()'s own inclusive shift-counting semantics exactly, and
naturally implements the Owner's recommended boundary rule ("an assignment
ending at the exact boundary where another starts does not conflict")
without any special-casing: an assignment ending at (day X, N) and another
starting at (day X+1, D) -- the very next discrete shift -- are adjacent,
not overlapping, because their ordinals differ by exactly 1.

Employee eligibility ("active escort worker"): wbom_employees.status='Active'
AND designation ILIKE 'Escort' -- confirmed against 5/5 sampled real
escort-assigned employees in production (all designation='Escort' exactly),
and consistent with modules.payroll.eligibility's inverse rule (office-staff
eligibility explicitly excludes designation='Escort').
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from app.database import execute, fetch_all, fetch_one, fetch_val, get_pool

log = logging.getLogger("fazle.dispatch")

SHIFT_ORDER = ["D", "N"]  # matches modules.escort_roster.calculations.SHIFT_ORDER exactly

# Statuses that structurally block an escort worker's availability, reusing
# modules.escort_lifecycle.find_active_program_for_employee()'s own proven
# rule verbatim (NOT invented here) -- extended for 'escort_changed'
# (Phase 2A.3, Owner-approved): a changed-out segment is historical, not
# blocking, same treatment as completed/cancelled.
_BOOKING_BLOCKING_EXCLUDED_STATUSES = ("completed", "cancelled", "escort_changed")

# The full set of statuses this module has confirmed, via direct code
# inspection, are actively set by current production code paths. Any other
# status value encountered is a data-quality signal, not silently treated
# as bookable or free.
_KNOWN_ACTIVE_STATUSES = {"draft", "confirmed", "running", "completed", "cancelled", "escort_changed"}

# Statuses considered "terminal" for a replacement segment itself (Phase
# 2A.3 sec.14/21): a replacement that has itself been superseded or ended
# is no longer "the" active replacement for its predecessor -- mirrors the
# partial unique index in migration 050 exactly.
_REPLACEMENT_TERMINAL_STATUSES = ("completed", "cancelled", "expired", "escort_changed")


def normalize_shift(raw: Optional[str]) -> Optional[str]:
    """Normalize a stored shift value to 'D' or 'N'. Returns None (not a
    guess) if the value cannot be confidently normalized -- callers must
    treat None as a data-quality problem, never default it."""
    if not raw:
        return None
    s = str(raw).strip().upper()[:1]
    return s if s in SHIFT_ORDER else None


def shift_ordinal(d: date, shift: str) -> int:
    """Map a (date, normalized shift) pair to a single sortable integer.
    shift must already be normalized ('D' or 'N') -- callers are
    responsible for calling normalize_shift() first and handling None."""
    return d.toordinal() * 2 + SHIFT_ORDER.index(shift)


class AssignmentWindow:
    """
    Canonical effective assignment window for one wbom_escort_programs row.
    One shared, deterministic calculation -- used by availability,
    conflict detection, and suggestions, so interval logic is never
    duplicated across API functions (per Owner instruction).
    """

    __slots__ = ("program_id", "start_date", "start_shift", "end_date", "end_shift",
                 "start_ord", "end_ord", "valid", "data_quality_issue")

    def __init__(self, program: dict):
        self.program_id = program.get("program_id")
        self.data_quality_issue: Optional[str] = None

        start_date = program.get("start_date") or program.get("program_date")
        start_shift = normalize_shift(program.get("shift"))
        end_date = program.get("end_date")
        end_shift = normalize_shift(program.get("end_shift"))

        self.start_date = start_date
        self.start_shift = start_shift
        self.end_date = end_date
        self.end_shift = end_shift

        if not start_date:
            self.data_quality_issue = "missing_start_date"
        elif start_shift is None:
            self.data_quality_issue = "invalid_or_missing_start_shift"
        elif not end_date:
            # No release/end yet -- an in-progress assignment. Treat the
            # window as open-ended: blocks availability indefinitely from
            # start_date until a real end_date is recorded (matches
            # find_active_program_for_employee()'s own status-based
            # blocking, which does not require an end_date to be set).
            end_date = date.max
            end_shift = end_shift or "N"
        elif end_shift is None:
            self.data_quality_issue = "invalid_or_missing_end_shift"
        elif end_date < start_date:
            self.data_quality_issue = "end_before_start"

        self.end_date = end_date
        self.end_shift = end_shift

        if self.data_quality_issue:
            self.valid = False
            self.start_ord = None
            self.end_ord = None
        else:
            self.valid = True
            self.start_ord = shift_ordinal(start_date, start_shift)
            self.end_ord = shift_ordinal(end_date, end_shift)

    def overlaps(self, other: "AssignmentWindow") -> bool:
        """Inclusive interval overlap. Both windows must be valid -- callers
        must check .valid on both sides first (an invalid window never
        counts as a conflict, per the data-quality policy: never assume
        booked or free when uncertain)."""
        if not (self.valid and other.valid):
            return False
        return self.start_ord <= other.end_ord and other.start_ord <= self.end_ord


def is_bookable_status(status: Optional[str]) -> Optional[bool]:
    """
    True: this status blocks availability (an active/booked assignment).
    False: this status does not block availability (terminal, non-bookable).
    None: unknown status outside the confirmed-active set -- data-quality
    signal, must never be silently treated as either.
    """
    s = (status or "").strip().lower()
    if s not in _KNOWN_ACTIVE_STATUSES:
        return None
    return s not in _BOOKING_BLOCKING_EXCLUDED_STATUSES


# ─────────────────────────────────────────────────────────────────────────────
# Query layer -- set-based SQL, no per-employee query loops.
# ─────────────────────────────────────────────────────────────────────────────

_ESCORT_WORKER_WHERE = "status = 'Active' AND designation ILIKE 'Escort'"

_PROGRAM_FIELDS = (
    "program_id, escort_employee_id, mother_vessel, lighter_vessel, "
    "program_date, shift, start_date, end_date, end_shift, status, "
    "destination, master_mobile"
)


async def _fetch_blocking_programs_for_employees(
    employee_ids: list[int], conn=None,
) -> dict[int, list[dict]]:
    """One query for all given employees' currently-blocking (per
    is_bookable_status()) programs, grouped by escort_employee_id. Also
    includes non-terminal statuses outside the known-active set so callers
    can surface data-quality warnings rather than silently dropping them.
    Accepts an optional `conn` so a caller already holding a transaction
    (Phase 2A.2's assign_program()) can compose this into it atomically,
    matching modules.escort_roster.db.sync_program_to_roster's own
    established conn= pattern."""
    if not employee_ids:
        return {}
    rows = await fetch_all(
        f"""SELECT {_PROGRAM_FIELDS}
            FROM wbom_escort_programs
            WHERE escort_employee_id = ANY($1::int[])
              AND escort_employee_id IS NOT NULL
              AND status <> 'completed' AND status <> 'cancelled' AND status <> 'escort_changed'
            ORDER BY escort_employee_id, start_date NULLS LAST, program_date NULLS LAST""",
        employee_ids, conn=conn,
    )
    by_employee: dict[int, list[dict]] = {}
    for r in rows:
        by_employee.setdefault(r["escort_employee_id"], []).append(dict(r))
    return by_employee


async def _fetch_last_assignment_dates(employee_ids: list[int]) -> dict[int, Optional[date]]:
    """One aggregate query for every given employee's most recent
    (COALESCE(end_date, program_date)) assignment, regardless of status --
    used only for the ranking factor "longest time since last assignment,"
    not for bookability."""
    if not employee_ids:
        return {}
    rows = await fetch_all(
        """SELECT escort_employee_id, MAX(COALESCE(end_date, program_date)) AS last_date
           FROM wbom_escort_programs
           WHERE escort_employee_id = ANY($1::int[])
           GROUP BY escort_employee_id""",
        employee_ids,
    )
    return {r["escort_employee_id"]: r["last_date"] for r in rows}


async def list_escort_workers(active_only: bool = True, employee_id: Optional[int] = None,
                               page: int = 1, page_size: int = 50) -> dict:
    """Page of escort workers with their current blocking assignment (if
    any), last assignment date, and active-assignment count. Exactly 3
    queries total regardless of page size (count, employee page, then two
    grouped lookups keyed off that page's ids) -- never one query per
    employee."""
    where = [_ESCORT_WORKER_WHERE] if active_only else ["designation ILIKE 'Escort'"]
    params: list = []
    if employee_id is not None:
        where.append(f"employee_id = ${len(params) + 1}")
        params.append(employee_id)
    where_sql = " AND ".join(where)

    total = await fetch_val(f"SELECT COUNT(*) FROM wbom_employees WHERE {where_sql}", *params) or 0

    offset = (page - 1) * page_size
    emp_rows = await fetch_all(
        f"""SELECT employee_id, employee_name, employee_mobile, status, designation
            FROM wbom_employees WHERE {where_sql}
            ORDER BY employee_id
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}""",
        *params, page_size, offset,
    )
    employee_ids = [r["employee_id"] for r in emp_rows]
    blocking_by_emp = await _fetch_blocking_programs_for_employees(employee_ids)
    last_by_emp = await _fetch_last_assignment_dates(employee_ids)

    items = []
    for r in emp_rows:
        eid = r["employee_id"]
        blocking = blocking_by_emp.get(eid, [])
        items.append(_build_worker_entry(dict(r), blocking, last_by_emp.get(eid)))

    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if page_size else 0}


def _build_worker_entry(employee: dict, blocking_programs: list[dict],
                         last_assignment: Optional[date]) -> dict:
    """Shared shape for one employee's dispatch entry -- reused by
    availability listing and suggestion ranking."""
    windows = [AssignmentWindow(p) for p in blocking_programs]
    data_quality_warnings = [w.data_quality_issue for w in windows if w.data_quality_issue]

    current_conflict = None
    for p, w in zip(blocking_programs, windows):
        bookable = is_bookable_status(p.get("status"))
        if bookable is True:
            current_conflict = {
                "program_id": p["program_id"], "status": p["status"],
                "start_date": str(p.get("start_date") or p.get("program_date") or ""),
                "start_shift": p.get("shift"),
                "end_date": str(p.get("end_date")) if p.get("end_date") else None,
                "end_shift": p.get("end_shift"),
                "destination": p.get("destination"),
                "mother_vessel": p.get("mother_vessel"),
            }
            break

    if data_quality_warnings and current_conflict is None:
        available = None  # availability_unknown
        reason = "availability_unknown"
    elif current_conflict is not None:
        available = False
        reason = "overlapping_assignment"
    else:
        available = True
        reason = None

    active_count = sum(1 for p in blocking_programs if is_bookable_status(p.get("status")) is True)

    return {
        "employee_id": employee["employee_id"],
        "employee_name": employee.get("employee_name"),
        "employee_mobile": employee.get("employee_mobile"),
        "status": employee.get("status"),
        "designation": employee.get("designation"),
        "available": available,
        "reason": reason,
        "current_conflict": current_conflict,
        "last_assignment_date": str(last_assignment) if last_assignment else None,
        "active_assignment_count": active_count,
        "data_quality_warnings": data_quality_warnings or None,
    }


async def check_conflict(
    escort_employee_id: int,
    start_date: date,
    start_shift: str,
    end_date: date,
    end_shift: str,
    exclude_program_id: Optional[int] = None,
) -> dict:
    """Read-only conflict check for one employee against a proposed window."""
    norm_start_shift = normalize_shift(start_shift)
    norm_end_shift = normalize_shift(end_shift)
    proposed = {
        "program_id": None, "start_date": start_date, "shift": start_shift,
        "end_date": end_date, "end_shift": end_shift,
    }
    proposed_window = AssignmentWindow(proposed)

    result = {
        "available": None,
        "escort_employee_id": escort_employee_id,
        "proposed_window": {
            "start_date": str(start_date), "start_shift": norm_start_shift,
            "end_date": str(end_date), "end_shift": norm_end_shift,
        },
        "conflicts": [],
        "reason": None,
    }

    if not proposed_window.valid:
        result["available"] = None
        result["reason"] = f"invalid_proposed_window:{proposed_window.data_quality_issue}"
        return result

    blocking = (await _fetch_blocking_programs_for_employees([escort_employee_id])).get(escort_employee_id, [])
    conflicts = []
    unknowns = []
    for p in blocking:
        if exclude_program_id is not None and p["program_id"] == exclude_program_id:
            continue
        bookable = is_bookable_status(p.get("status"))
        if bookable is not True:
            if bookable is None:
                unknowns.append(p["program_id"])
            continue
        w = AssignmentWindow(p)
        if not w.valid:
            unknowns.append(p["program_id"])
            continue
        if proposed_window.overlaps(w):
            conflicts.append({
                "program_id": p["program_id"], "status": p["status"],
                "start_date": str(p.get("start_date") or p.get("program_date") or ""),
                "start_shift": p.get("shift"),
                "end_date": str(p.get("end_date")) if p.get("end_date") else None,
                "end_shift": p.get("end_shift"),
                "destination": p.get("destination"),
                "mother_vessel": p.get("mother_vessel"),
                "master_mobile": p.get("master_mobile"),
            })

    result["conflicts"] = conflicts
    if conflicts:
        result["available"] = False
        result["reason"] = "overlapping_assignment"
    elif unknowns:
        result["available"] = None
        result["reason"] = "availability_unknown"
        result["data_quality_warnings"] = unknowns
    else:
        result["available"] = True
        result["reason"] = None
    return result


async def get_suggestions(
    start_date: date, start_shift: str, end_date: date, end_shift: str,
    destination: Optional[str] = None, limit: int = 10,
) -> dict:
    """Deterministic, explainable ranked suggestions -- no LLM. Ranking
    factors, in priority order (Owner-approved): (1) available (no
    overlap), (2) longest time since last assignment, (3) lower current
    active workload, (4) employee_id as a stable tie-breaker."""
    proposed = {"program_id": None, "start_date": start_date, "shift": start_shift,
                "end_date": end_date, "end_shift": end_shift}
    proposed_window = AssignmentWindow(proposed)
    if not proposed_window.valid:
        return {"items": [], "reason": f"invalid_proposed_window:{proposed_window.data_quality_issue}"}

    emp_rows = await fetch_all(
        f"SELECT employee_id, employee_name, employee_mobile, status, designation "
        f"FROM wbom_employees WHERE {_ESCORT_WORKER_WHERE} ORDER BY employee_id"
    )
    employee_ids = [r["employee_id"] for r in emp_rows]
    blocking_by_emp = await _fetch_blocking_programs_for_employees(employee_ids)
    last_by_emp = await _fetch_last_assignment_dates(employee_ids)

    today = date.today()
    ranked = []
    for r in emp_rows:
        eid = r["employee_id"]
        blocking = blocking_by_emp.get(eid, [])
        entry = _build_worker_entry(dict(r), blocking, last_by_emp.get(eid))

        # Re-derive availability against the PROPOSED window specifically
        # (not "any current conflict") -- an employee free today but booked
        # during the requested window must be excluded; an employee busy
        # today but free during the requested window must be included.
        conflict_for_window = None
        unknown = False
        for p in blocking:
            bookable = is_bookable_status(p.get("status"))
            if bookable is None:
                unknown = True
                continue
            if bookable is not True:
                continue
            w = AssignmentWindow(p)
            if not w.valid:
                unknown = True
                continue
            if proposed_window.overlaps(w):
                conflict_for_window = p
                break

        if conflict_for_window is not None:
            continue  # excluded: unavailable for the proposed window
        if unknown:
            continue  # excluded: availability_unknown, never guessed available

        last_date = last_by_emp.get(eid)
        idle_days = (today - last_date).days if last_date else 10_000  # never assigned -> most idle
        active_count = entry["active_assignment_count"]

        reasons = [
            "available for the proposed window",
            f"idle {idle_days} day(s) since last assignment" if last_date else "no prior assignment on record",
            f"{active_count} current active assignment(s)",
        ]
        ranked.append({
            "employee_id": eid, "employee_name": r.get("employee_name"),
            "employee_mobile": r.get("employee_mobile"),
            "last_assignment_date": str(last_date) if last_date else None,
            "idle_days": idle_days, "active_assignment_count": active_count,
            "reasons": reasons,
        })

    # Deterministic sort: most idle first, then lowest workload, then
    # employee_id as a stable tie-breaker (never random reordering).
    ranked.sort(key=lambda e: (-e["idle_days"], e["active_assignment_count"], e["employee_id"]))
    for i, e in enumerate(ranked[:limit], start=1):
        e["rank"] = i

    return {
        "items": ranked[:limit],
        "proposed_window": {
            "start_date": str(start_date), "start_shift": normalize_shift(start_shift),
            "end_date": str(end_date), "end_shift": normalize_shift(end_shift),
            "destination": destination,
        },
        "total_eligible": len(ranked),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.2 — Controlled Manual Assignment (write path)
#
# Owner-approved scope (2026-07-19): authorized human selects an employee ->
# backend rechecks eligibility and conflict -> assignment succeeds or is
# rejected. No auto-assignment, no override, no reservation, no new
# assignment table. Canonical field remains wbom_escort_programs.escort_employee_id.
#
# Status-eligibility matrix (evidence-based, from a full trace of every
# current writer of escort_employee_id and every consumer keyed on it):
#   draft      -> initial assignment only (a draft program's employee_id is
#                 NULL per every confirmed write-site; see module docstring)
#   confirmed  -> initial assignment AND reassignment
#   running    -> EXCLUDED. modules.escort_lifecycle.find_active_program_for_employee()
#                 and mark_program_running_on_attendance() both key strictly
#                 on escort_employee_id with no physical-presence check --
#                 reassigning a running program would silently misattribute
#                 a real release/attendance event to whoever now holds the
#                 FK, not whoever actually worked the shift. No ratified
#                 workflow exists for this; excluded per Owner instruction
#                 rather than assumed safe.
#   completed,
#   cancelled  -> EXCLUDED (terminal, per Owner instruction). Also protects
#                 against modules.payment_workflow.create_escort_payment_draft()'s
#                 confirmed behavior of overwriting employee_id on an EXISTING
#                 draft if re-invoked for the same program_id -- drafts are
#                 only created at release (a terminal transition), so
#                 restricting assignment to pre-release statuses structurally
#                 avoids that risk without needing to special-case it.
_ASSIGNABLE_STATUSES = {"draft", "confirmed"}


class DispatchAssignmentError(Exception):
    """Carries the HTTP status + machine-readable code the routes layer
    needs -- keeps modules/dispatch/routes.py a thin FastAPI translation
    layer instead of duplicating validation logic there."""

    def __init__(self, code: str, http_status: int, message: str, conflicts: Optional[list] = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.conflicts = conflicts


def _program_summary(p: dict) -> dict:
    return {
        "program_id": p["program_id"],
        "status": p.get("status"),
        "start_date": str(p.get("start_date") or p.get("program_date") or ""),
        "start_shift": p.get("shift"),
        "end_date": str(p.get("end_date")) if p.get("end_date") else None,
        "end_shift": p.get("end_shift"),
        "mother_vessel": p.get("mother_vessel"),
        "lighter_vessel": p.get("lighter_vessel"),
        "destination": p.get("destination"),
    }


async def assign_program(
    program_id: int,
    escort_employee_id: int,
    expected_current_employee_id: Optional[int],
    performed_by: str,
) -> dict:
    """
    Controlled manual assignment/reassignment of one escort worker to one
    wbom_escort_programs row. Transactional, conflict-checked, and
    concurrency-safe (see class docstring on DispatchAssignmentError and the
    module-level comment above for the evidence behind every guard here).

    Locking: pg_advisory_xact_lock, the established precedent in this repo
    for exactly this "read-then-write with conflict check" shape (used
    identically, keyed by escort_program_id, in
    modules.payment_workflow.create_escort_payment_draft() and by
    modules.escort_roster.db.create_roster_entry()). Two locks are always
    acquired in the SAME fixed order -- escort_employee_id first, then
    program_id -- regardless of which is "old" or "new" in a reassignment,
    so two concurrent assignment calls can never deadlock waiting on each
    other in reverse order. Locking the employee_id (not just the program_id)
    is what actually prevents "same employee double-booked through racing
    requests": two concurrent calls assigning the same employee to two
    different, mutually-overlapping programs each lock a DIFFERENT
    program_id, so a program-only lock would let both proceed to conflict-
    check concurrently against each other's not-yet-committed write; locking
    the employee_id serializes them.

    Raises DispatchAssignmentError for every rejection path -- the routes
    layer translates .http_status/.code/.message/.conflicts into the exact
    HTTP response.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Deterministic lock order (see docstring): employee, then program.
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", escort_employee_id)
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", program_id)

            program = await fetch_one(
                """SELECT program_id, escort_employee_id, status, mother_vessel, lighter_vessel,
                          start_date, program_date, shift, end_date, end_shift, destination,
                          escort_name, escort_mobile
                   FROM wbom_escort_programs WHERE program_id = $1""",
                program_id, conn=conn,
            )
            if program is None:
                raise DispatchAssignmentError("program_not_found", 404, f"program {program_id} not found")

            status = (program["status"] or "").strip().lower()
            if status not in _ASSIGNABLE_STATUSES:
                raise DispatchAssignmentError(
                    "program_not_assignable", 409,
                    f"program status {status!r} is not assignable in Phase 2A.2 "
                    f"(allowed: {sorted(_ASSIGNABLE_STATUSES)})",
                )

            current_employee_id = program["escort_employee_id"]
            if current_employee_id != expected_current_employee_id:
                raise DispatchAssignmentError(
                    "stale_assignment", 409,
                    f"expected_current_employee_id={expected_current_employee_id!r} "
                    f"but the program's current assignment is {current_employee_id!r}; refresh and retry",
                )

            employee = await fetch_one(
                "SELECT employee_id, employee_name, employee_mobile, status, designation "
                "FROM wbom_employees WHERE employee_id = $1",
                escort_employee_id, conn=conn,
            )
            if employee is None:
                raise DispatchAssignmentError("employee_not_found", 404, f"employee {escort_employee_id} not found")
            if (employee.get("status") or "").strip() != "Active":
                raise DispatchAssignmentError(
                    "employee_not_active", 422, f"employee {escort_employee_id} is not Active")
            if (employee.get("designation") or "").strip().lower() != "escort":
                raise DispatchAssignmentError(
                    "employee_not_escort", 422,
                    f"employee {escort_employee_id} designation is not Escort")

            # The proposed window is the PROGRAM's own schedule -- this
            # endpoint assigns who is on the job, never changes when the job
            # runs (out of scope, see Owner stop condition: no arbitrary
            # scheduling).
            window = AssignmentWindow(program)
            if not window.valid:
                raise DispatchAssignmentError(
                    "invalid_program_window", 422,
                    f"program {program_id} has an invalid assignment window: {window.data_quality_issue}",
                )

            blocking = (await _fetch_blocking_programs_for_employees(
                [escort_employee_id], conn=conn)).get(escort_employee_id, [])
            conflicts = []
            unknowns = []
            for p in blocking:
                if p["program_id"] == program_id:
                    continue  # a program never conflicts with itself
                bookable = is_bookable_status(p.get("status"))
                if bookable is not True:
                    if bookable is None:
                        unknowns.append(p["program_id"])
                    continue
                w = AssignmentWindow(p)
                if not w.valid:
                    unknowns.append(p["program_id"])
                    continue
                if window.overlaps(w):
                    conflicts.append(_program_summary(p))

            if conflicts:
                raise DispatchAssignmentError(
                    "dispatch_conflict", 409,
                    "escort employee has an overlapping assignment", conflicts=conflicts,
                )

            await execute(
                "UPDATE wbom_escort_programs SET escort_employee_id = $1 WHERE program_id = $2",
                escort_employee_id, program_id, conn=conn,
            )

            # Certified atomic upsert, same transaction+conn -- never
            # reproduce roster SQL independently (Owner instruction).
            from modules.escort_roster.db import sync_program_to_roster, log_audit
            await sync_program_to_roster(program_id, actor=performed_by, conn=conn)

            action = "dispatch_reassign" if current_employee_id is not None else "dispatch_assign"
            await log_audit(
                program_id=program_id,
                action=action,
                old_data={"escort_employee_id": current_employee_id},
                new_data={
                    "escort_employee_id": escort_employee_id,
                    "program_status": status,
                    "conflict_check": "passed",
                    "assignment_window": {
                        "start_date": str(window.start_date), "start_shift": window.start_shift,
                        "end_date": str(window.end_date) if window.end_date != date.max else None,
                        "end_shift": window.end_shift,
                    },
                },
                performed_by=performed_by,
                source="dispatch_ui",
                conn=conn,
            )

            return {
                "program_id": program_id,
                "previous_employee_id": current_employee_id,
                "new_employee_id": escort_employee_id,
                "employee_name": employee.get("employee_name"),
                "employee_mobile": employee.get("employee_mobile"),
                "status": status,
                "mother_vessel": program.get("mother_vessel"),
                "lighter_vessel": program.get("lighter_vessel"),
                "start_date": str(window.start_date),
                "start_shift": window.start_shift,
                "end_date": str(window.end_date) if window.end_date != date.max else None,
                "end_shift": window.end_shift,
            }


async def list_overlaps(
    employee_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """
    Read-only overlap-review facility (Phase 2A.2 sec.4/sec.9). Recomputes
    the current overlap set live using the SAME certified AssignmentWindow
    logic as availability/conflict-check/suggestions -- never a second,
    divergent overlap algorithm. Never mutates anything.
    """
    where = ["p.escort_employee_id IS NOT NULL", "p.status <> 'completed'", "p.status <> 'cancelled'",
              "p.status <> 'escort_changed'"]
    params: list = []
    if employee_id is not None:
        params.append(employee_id)
        where.append(f"p.escort_employee_id = ${len(params)}")
    if status is not None:
        params.append(status)
        where.append(f"LOWER(p.status) = LOWER(${len(params)})")
    where_sql = " AND ".join(where)

    rows = await fetch_all(
        f"""SELECT p.program_id, p.escort_employee_id, e.employee_name, e.employee_mobile,
                   p.status, p.mother_vessel, p.lighter_vessel, p.destination,
                   p.start_date, p.program_date, p.shift, p.end_date, p.end_shift
            FROM wbom_escort_programs p
            LEFT JOIN wbom_employees e ON e.employee_id = p.escort_employee_id
            WHERE {where_sql}
            ORDER BY p.escort_employee_id, COALESCE(p.start_date, p.program_date)""",
        *params,
    )

    by_employee: dict[int, list[dict]] = {}
    for r in rows:
        by_employee.setdefault(r["escort_employee_id"], []).append(dict(r))

    groups = []
    for eid, progs in by_employee.items():
        if len(progs) < 2:
            continue
        windows = [(p, AssignmentWindow(p)) for p in progs]
        pairs = []
        data_quality_warnings = []
        for i in range(len(windows)):
            p1, w1 = windows[i]
            if not w1.valid and p1["program_id"] not in data_quality_warnings:
                data_quality_warnings.append(p1["program_id"])
            for j in range(i + 1, len(windows)):
                p2, w2 = windows[j]
                if w1.overlaps(w2):
                    same_exact = (
                        w1.start_date == w2.start_date and w1.start_shift == w2.start_shift
                        and w1.end_date == w2.end_date and w1.end_shift == w2.end_shift
                        and (p1.get("mother_vessel") or "").strip().lower()
                        == (p2.get("mother_vessel") or "").strip().lower()
                    )
                    statuses = tuple(sorted([(p1["status"] or "").lower(), (p2["status"] or "").lower()]))
                    if same_exact:
                        cls = "same_exact_program_duplication"
                    elif statuses == ("confirmed", "confirmed"):
                        cls = "confirmed-confirmed"
                    elif statuses == ("running", "running"):
                        cls = "active-active"
                    elif set(statuses) == {"running", "confirmed"}:
                        cls = "active-confirmed"
                    else:
                        cls = "legacy/unknown"
                    pairs.append({
                        "program_a": _program_summary(p1), "program_b": _program_summary(p2),
                        "classification": cls,
                    })
        if pairs:
            groups.append({
                "employee_id": eid,
                "employee_name": progs[0].get("employee_name"),
                "employee_mobile": progs[0].get("employee_mobile"),
                "programs": [_program_summary(p) for p in progs],
                "conflicting_pairs": pairs,
                "conflict_count": len(pairs),
                "data_quality_warnings": data_quality_warnings or None,
            })

    groups.sort(key=lambda g: g["employee_id"])
    total = len(groups)
    offset = (page - 1) * page_size
    page_items = groups[offset: offset + page_size]

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if page_size else 0,
        "note": "Existing overlaps require manual business review. No automatic correction is performed.",
    }
