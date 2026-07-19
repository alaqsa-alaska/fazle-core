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

import json
import logging
import re
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


def ordinal_to_shift(ordinal: int) -> tuple[date, str]:
    """Inverse of shift_ordinal(). Phase 2A.3 sec.12: computing the earlier
    segment's end boundary as "the shift immediately before the later
    segment's start" is just ordinal_to_shift(later_start_ordinal - 1) --
    no special-casing for day-vs-night, matches the exact same boundary
    arithmetic already certified for conflict detection (2A.1)."""
    return date.fromordinal(ordinal // 2), SHIFT_ORDER[ordinal % 2]


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


async def get_missing_end_date_programs(limit: int = 50) -> dict:
    """Read-only report: programs with a recorded start_date but no
    end_date, excluding terminal statuses (completed/cancelled/
    escort_changed) where an open end is normal/expected. This is a
    review/data-quality signal ("missing_completion_data"), not a
    lifecycle status change and not itself a conflict -- callers (including
    the AI console) must not auto-close, auto-cancel, or treat this as a
    confirmed double-booking. Matches the corrected business rule that an
    earlier program with no end_date may simply be missing completion
    data, not proof of overlap (see CANONICAL_BUSINESS_RULES.md, Dispatch
    section)."""
    limit = max(1, min(limit, 100))
    rows = await fetch_all(
        f"""SELECT {_PROGRAM_FIELDS}
            FROM wbom_escort_programs
            WHERE end_date IS NULL
              AND start_date IS NOT NULL
              AND status NOT IN ('completed', 'cancelled', 'escort_changed')
            ORDER BY start_date ASC
            LIMIT $1""",
        limit,
    )
    items = []
    for r in rows:
        row = dict(r)
        items.append({
            "program_id": row.get("program_id"),
            "mother_vessel": row.get("mother_vessel"),
            "lighter_vessel": row.get("lighter_vessel"),
            "escort_employee_id": row.get("escort_employee_id"),
            "start_date": str(row["start_date"]) if row.get("start_date") else None,
            "shift": row.get("shift"),
            "status": row.get("status"),
            "destination": row.get("destination"),
        })
    return {
        "items": items,
        "count": len(items),
        "note": "missing_completion_data — review/data-quality signal, not a confirmed conflict",
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
    override: bool = False,
    override_reason: Optional[str] = None,
    acknowledged_conflict_program_ids: Optional[list] = None,
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

    Phase 2A.3 Stage 6 controlled override (override=True): admin/superadmin
    only -- enforced by the route layer routing override requests through
    _require_admin_or_above, never checked here since this function has no
    role context. Requires a mandatory override_reason AND
    acknowledged_conflict_program_ids naming exactly the conflicts the
    caller is knowingly overriding -- if the freshly recomputed conflict set
    (inside this transaction, under lock) contains any program_id NOT in the
    acknowledged set, the override is rejected as stale rather than silently
    accepted, the same optimistic-concurrency discipline expected_current_
    employee_id already uses. Overridden assignments are audited with an
    explicit authorized_override marker and stay visible to
    list_overlaps() (never hidden from review)."""
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

            overridden = False
            if conflicts:
                if not override:
                    raise DispatchAssignmentError(
                        "dispatch_conflict", 409,
                        "escort employee has an overlapping assignment", conflicts=conflicts,
                    )
                if not override_reason or not override_reason.strip():
                    raise DispatchAssignmentError(
                        "override_reason_required", 422,
                        "a mandatory reason is required to override a detected conflict", conflicts=conflicts,
                    )
                conflict_ids = {c["program_id"] for c in conflicts}
                acknowledged_ids = set(acknowledged_conflict_program_ids or [])
                if not conflict_ids.issubset(acknowledged_ids):
                    raise DispatchAssignmentError(
                        "conflict_acknowledgment_stale", 409,
                        "the conflict set changed since it was last shown -- re-review and re-acknowledge "
                        "before overriding", conflicts=conflicts,
                    )
                overridden = True

            await execute(
                "UPDATE wbom_escort_programs SET escort_employee_id = $1 WHERE program_id = $2",
                escort_employee_id, program_id, conn=conn,
            )

            # Certified atomic upsert, same transaction+conn -- never
            # reproduce roster SQL independently (Owner instruction).
            from modules.escort_roster.db import sync_program_to_roster, log_audit
            await sync_program_to_roster(program_id, actor=performed_by, conn=conn)

            if overridden:
                action = "dispatch_override_assign"
            else:
                action = "dispatch_reassign" if current_employee_id is not None else "dispatch_assign"
            audit_new_data = {
                "escort_employee_id": escort_employee_id,
                "program_status": status,
                "conflict_check": "overridden" if overridden else "passed",
                "assignment_window": {
                    "start_date": str(window.start_date), "start_shift": window.start_shift,
                    "end_date": str(window.end_date) if window.end_date != date.max else None,
                    "end_shift": window.end_shift,
                },
            }
            if overridden:
                audit_new_data["authorized_override"] = True
                audit_new_data["override_reason"] = override_reason
                audit_new_data["overridden_conflicts"] = conflicts
            await log_audit(
                program_id=program_id,
                action=action,
                old_data={"escort_employee_id": current_employee_id},
                new_data=audit_new_data,
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
                "authorized_override": overridden,
            }


async def _fetch_pair_review_markers(program_ids: list) -> dict:
    """Phase 2A.3 Stage 6 -- batch-fetch the latest override/review audit
    marker per program_id, keyed for pair enrichment in list_overlaps().
    Read-only, never mutates anything."""
    if not program_ids:
        return {}
    rows = await fetch_all(
        """SELECT DISTINCT ON (program_id, action) program_id, action, new_data, performed_by, created_at
           FROM escort_roster_audit_logs
           WHERE program_id = ANY($1::int[])
             AND action IN ('dispatch_override_assign', 'dispatch_conflict_review')
           ORDER BY program_id, action, created_at DESC""",
        program_ids,
    )
    by_program: dict = {}
    for r in rows:
        by_program.setdefault(r["program_id"], []).append(dict(r))
    return by_program


def _enrich_pair_with_review(pair_markers: dict, p1_id: int, p2_id: int) -> dict:
    """Returns {'authorized_override': bool, 'review': dict|None} for one
    conflicting pair, derived purely from prior audit entries -- never a
    new mutation."""
    result = {"authorized_override": False, "review": None}
    for pid, other_id in ((p1_id, p2_id), (p2_id, p1_id)):
        for entry in pair_markers.get(pid, []):
            new_data = entry.get("new_data") or {}
            if isinstance(new_data, str):
                try:
                    new_data = json.loads(new_data)
                except Exception:
                    new_data = {}
            if entry["action"] == "dispatch_override_assign":
                overridden = new_data.get("overridden_conflicts") or []
                if any(c.get("program_id") == other_id for c in overridden):
                    result["authorized_override"] = True
            elif entry["action"] == "dispatch_conflict_review":
                if new_data.get("pair_program_id") == other_id and result["review"] is None:
                    # record_conflict_pair_review() writes one identical entry to
                    # EACH side of the pair in the same transaction -- both sides
                    # match here, so the first hit (either side) is authoritative.
                    result["review"] = {
                        "decision": new_data.get("decision"), "note": new_data.get("note"),
                        "performed_by": entry.get("performed_by"), "recorded_at": str(entry.get("created_at")),
                    }
    return result


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

    Phase 2A.3 Stage 6: each conflicting pair is enriched with
    authorized_override (True if an admin override-assigned one side while
    knowingly acknowledging the other) and review (the latest keep-both/
    defer decision recorded via record_conflict_pair_review(), if any) --
    both derived read-only from escort_roster_audit_logs, per Decision 5's
    requirement that overridden/reviewed conflicts stay visible here, never
    hidden from ongoing review.
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

    pair_markers = await _fetch_pair_review_markers([r["program_id"] for r in rows])

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
                    review_info = _enrich_pair_with_review(pair_markers, p1["program_id"], p2["program_id"])
                    pairs.append({
                        "program_a": _program_summary(p1), "program_b": _program_summary(p2),
                        "classification": cls,
                        "authorized_override": review_info["authorized_override"],
                        "review": review_info["review"],
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 3 — Different-Date Segments and Escort Replacement
#
# Owner-approved (2026-07-19). A replacement candidate requires: same
# normalized non-empty mother_vessel AND lighter_vessel, a different
# escort, and a LATER operational start boundary (start_date/start_shift,
# never created_at). Decision 6 (seven-day boundary):
#   date_delta < 7 days  -> earlier segment = escort_changed
#   date_delta >= 7 days -> earlier segment = completed
# Explicit completion evidence always outranks this inference (checked
# fresh inside the transaction -- a program already completed/cancelled
# is never silently reclassified).
# ─────────────────────────────────────────────────────────────────────────────

_REPLACEMENT_UNDER_DAYS_THRESHOLD = 7


def _normalize_vessel_for_matching(raw: Optional[str]) -> str:
    """Phase 2A.3 sec.3: normalize a vessel name for same-vessel-pair
    matching -- case, punctuation, extra spaces, and MV/M.V./M/V-style
    prefixes. Deliberately a NEW, separate normalizer from the three
    pre-existing, not-yet-consolidated ones (modules.escort._clean_vessel_name,
    modules.ocr_processor._normalize_vessel_name,
    modules.escort_slip_extractor._normalize_vessel -- flagged as an
    unresolved hygiene item in CANONICAL_BUSINESS_RULES.md) -- unifying
    them is out of scope here (unrelated roster refactoring)."""
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"^(mv|m\.v\.|m/v)\s*", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def classify_vessel_match(mv_a: Optional[str], lv_a: Optional[str],
                           mv_b: Optional[str], lv_b: Optional[str]) -> str:
    """Phase 2A.3 Decision 3: 'match' only when BOTH mother_vessel and
    lighter_vessel are non-empty (after normalization) on BOTH sides AND
    both match. 'insufficient_data' whenever either lighter_vessel is
    empty -- never silently falls back to mother-vessel-only confidence,
    per the Owner's explicit instruction (this exact gap was found to
    produce false matches from a real bulk-import artifact during the
    Decision Report's historical audit). 'no_match' otherwise."""
    n_mv_a, n_lv_a = _normalize_vessel_for_matching(mv_a), _normalize_vessel_for_matching(lv_a)
    n_mv_b, n_lv_b = _normalize_vessel_for_matching(mv_b), _normalize_vessel_for_matching(lv_b)
    if not n_mv_a or not n_mv_b or not n_lv_a or not n_lv_b:
        return "insufficient_data"
    if n_mv_a == n_mv_b and n_lv_a == n_lv_b:
        return "match"
    return "no_match"


async def find_replacement_predecessor(
    mother_vessel: str, lighter_vessel: str, escort_name: str, escort_mobile: str,
    start_date: date, conn=None,
) -> Optional[dict]:
    """Phase 2A.3 sec.11: read-only lookup for a same-vessel-pair,
    non-terminal, DIFFERENT-escort program with a strictly EARLIER
    operational start boundary than the given one -- the precondition for
    treating a new admin confirmation as a replacement segment rather than
    an unrelated new program. Matches only on the stored operational
    start_date/program_date, never on created_at/message-arrival time.
    Returns None (not "insufficient_data") for any non-confident case --
    callers must never guess a replacement into existence."""
    candidates = await fetch_all(
        """SELECT program_id, status, mother_vessel, lighter_vessel, escort_name, escort_mobile,
                  escort_employee_id, COALESCE(start_date, program_date) AS eff_start, shift
           FROM wbom_escort_programs
           WHERE status NOT IN ('completed', 'cancelled', 'expired', 'escort_changed')
           ORDER BY COALESCE(start_date, program_date) DESC
           LIMIT 500""",
        conn=conn,
    )
    for c in candidates:
        if classify_vessel_match(mother_vessel, lighter_vessel, c["mother_vessel"], c["lighter_vessel"]) != "match":
            continue
        existing_name = (c["escort_name"] or "").strip().lower()
        existing_mobile = (c["escort_mobile"] or "").strip()
        if (escort_name or "").strip().lower() == existing_name and (escort_mobile or "").strip() == existing_mobile:
            continue  # same escort -- not a replacement candidate
        if c["eff_start"] is None or c["eff_start"] >= start_date:
            continue  # not a clear earlier boundary (equal-date is Stage 2's job)
        return dict(c)
    return None


async def create_replacement_segment(
    earlier_program_id: int,
    mother_vessel: str,
    lighter_vessel: str,
    escort_employee_id: Optional[int],
    escort_name: str,
    escort_mobile: str,
    start_date: date,
    start_shift: str,
    master_mobile: Optional[str] = None,
    destination: Optional[str] = None,
    performed_by: str = "system",
) -> dict:
    """
    Phase 2A.3 Stage 3. Creates a new 'running' escort-duty segment that
    replaces an earlier segment for the same vessel operation, applying
    Decision 6's seven-day boundary rule. Transactional and concurrency-
    safe, following the exact same pattern as assign_program() (Phase
    2A.2): pg_advisory_xact_lock in a fixed order (the NEW segment's
    escort_employee_id first if known, then the earlier program_id --
    matching assign_program()'s "employee, then program" global lock
    order so the two functions can never deadlock against each other),
    fresh re-reads inside the transaction, and never trusting a caller-
    supplied classification.

    Never transfers attendance or payment records -- each segment keeps
    its own history untouched; only the earlier program's status/end
    boundary and the new program's row are written here.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if escort_employee_id is not None:
                await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", escort_employee_id)
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", earlier_program_id)

            earlier = await fetch_one(
                """SELECT program_id, status, mother_vessel, lighter_vessel, escort_name, escort_mobile,
                          escort_employee_id, COALESCE(start_date, program_date) AS eff_start, shift,
                          end_date, end_shift
                   FROM wbom_escort_programs WHERE program_id = $1""",
                earlier_program_id, conn=conn,
            )
            if earlier is None:
                raise DispatchAssignmentError("program_not_found", 404, f"program {earlier_program_id} not found")

            # Explicit completion/terminal evidence always outranks
            # inference (sec.13/23) -- re-checked fresh, not trusted from
            # whatever the caller observed before acquiring the lock.
            if earlier["status"] in ("completed", "cancelled", "escort_changed"):
                raise DispatchAssignmentError(
                    "already_terminal", 409,
                    f"program {earlier_program_id} is already {earlier['status']!r} -- "
                    f"explicit prior evidence outranks replacement inference",
                )

            match = classify_vessel_match(
                mother_vessel, lighter_vessel, earlier["mother_vessel"], earlier["lighter_vessel"])
            if match != "match":
                raise DispatchAssignmentError(
                    "insufficient_vessel_match", 422,
                    f"vessel-pair match classification={match!r} -- cannot create an "
                    f"automatic replacement segment (Decision 3)",
                )

            existing_name = (earlier["escort_name"] or "").strip().lower()
            existing_mobile = (earlier["escort_mobile"] or "").strip()
            if (escort_name or "").strip().lower() == existing_name and (escort_mobile or "").strip() == existing_mobile:
                raise DispatchAssignmentError(
                    "same_escort", 422, "incoming escort matches the earlier segment's escort -- not a replacement")

            new_shift = normalize_shift(start_shift)
            earlier_shift = normalize_shift(earlier["shift"]) or "D"
            if new_shift is None or earlier["eff_start"] is None:
                raise DispatchAssignmentError(
                    "invalid_window", 422, "cannot compute a valid start boundary for replacement")
            earlier_ord = shift_ordinal(earlier["eff_start"], earlier_shift)
            new_ord = shift_ordinal(start_date, new_shift)
            if new_ord <= earlier_ord:
                raise DispatchAssignmentError(
                    "not_later", 422,
                    f"new start ({start_date} {new_shift}) is not later than the earlier segment's "
                    f"start ({earlier['eff_start']} {earlier_shift}) -- replacement requires a later boundary",
                )

            # Prevent duplicate active replacement (sec.21). The partial
            # unique index (migration 050) enforces this at the DB level
            # too -- this pre-check just produces a clean structured error
            # instead of a raw UniqueViolationError bubbling up.
            existing_active_replacement = await fetch_one(
                """SELECT program_id FROM wbom_escort_programs
                   WHERE replaces_program_id = $1
                     AND status NOT IN ('completed', 'cancelled', 'expired', 'escort_changed')""",
                earlier_program_id, conn=conn,
            )
            if existing_active_replacement:
                raise DispatchAssignmentError(
                    "duplicate_active_replacement", 409,
                    f"program {earlier_program_id} already has an active replacement "
                    f"(program_id={existing_active_replacement['program_id']})",
                )

            date_delta = (start_date - earlier["eff_start"]).days
            if date_delta < _REPLACEMENT_UNDER_DAYS_THRESHOLD:
                earlier_new_status = "escort_changed"
                classification_rule = "under_7_days_escort_changed"
            else:
                earlier_new_status = "completed"
                classification_rule = "7_days_or_more_completed"

            # Earlier segment's end boundary: the shift immediately before
            # the later segment's start (sec.12) -- pure ordinal
            # arithmetic, matches AssignmentWindow's own boundary
            # semantics exactly, no day/night special-casing.
            end_date_v, end_shift_v = ordinal_to_shift(new_ord - 1)

            before_earlier_status = earlier["status"]
            before_earlier_end_date = earlier["end_date"]
            await execute(
                """UPDATE wbom_escort_programs
                   SET status = $1, end_date = $2, end_shift = $3
                   WHERE program_id = $4""",
                earlier_new_status, end_date_v, end_shift_v, earlier_program_id, conn=conn,
            )

            remarks = json.dumps({
                "source_of_truth": "dispatch_replacement_segment",
                "auto_created": True,
                "replaces_program_id": earlier_program_id,
                "classification_rule": classification_rule,
                "date_delta_days": date_delta,
            })
            new_program_id = await fetch_val(
                """INSERT INTO wbom_escort_programs
                       (mother_vessel, lighter_vessel, master_mobile, destination, escort_name, escort_mobile,
                        escort_employee_id, program_date, start_date, shift, status, replaces_program_id,
                        assignment_time, remarks, is_historical)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$8,$9,'running',$10,NOW(),$11,FALSE)
                   RETURNING program_id""",
                mother_vessel, lighter_vessel, master_mobile or "", destination, escort_name, escort_mobile,
                escort_employee_id, start_date, new_shift, earlier_program_id, remarks, conn=conn,
            )

            from modules.escort_roster.db import sync_program_to_roster, log_audit
            await sync_program_to_roster(earlier_program_id, actor=performed_by, conn=conn)
            await sync_program_to_roster(new_program_id, actor=performed_by, conn=conn)

            await log_audit(
                program_id=earlier_program_id,
                action="escort_replacement_earlier",
                old_data={"status": before_earlier_status,
                          "end_date": str(before_earlier_end_date) if before_earlier_end_date else None},
                new_data={"status": earlier_new_status, "end_date": str(end_date_v), "end_shift": end_shift_v,
                          "replaced_by_program_id": new_program_id, "date_delta_days": date_delta,
                          "classification_rule": classification_rule},
                performed_by=performed_by, source="dispatch_replacement",
            )
            await log_audit(
                program_id=new_program_id,
                action="escort_replacement_new_segment",
                old_data=None,
                new_data={"replaces_program_id": earlier_program_id, "escort_name": escort_name,
                          "escort_mobile": escort_mobile, "start_date": str(start_date), "start_shift": new_shift,
                          "date_delta_days": date_delta},
                performed_by=performed_by, source="dispatch_replacement",
            )

            return {
                "earlier_program_id": earlier_program_id,
                "earlier_status": earlier_new_status,
                "earlier_end_date": str(end_date_v),
                "earlier_end_shift": end_shift_v,
                "new_program_id": new_program_id,
                "new_escort_employee_id": escort_employee_id,
                "date_delta_days": date_delta,
                "classification_rule": classification_rule,
            }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 4 — Safe Unassignment
#
# Owner-approved (2026-07-19). Supported only for draft/confirmed programs
# -- never running/completed/cancelled/escort_changed. Never hard-deletes
# the program; only clears escort_employee_id and restores the
# pre-assignment state.
# ─────────────────────────────────────────────────────────────────────────────

_UNASSIGNABLE_FROM_STATUSES = ("draft", "confirmed")


async def unassign_program(
    program_id: int,
    expected_current_employee_id: int,
    reason: str,
    performed_by: str,
) -> dict:
    """
    Phase 2A.3 sec.12/18. Transactional, concurrency-safe (same
    pg_advisory_xact_lock pattern and lock order as assign_program()/
    create_replacement_segment(): employee, then program). Sets
    escort_employee_id = NULL. Blocks whenever accepted attendance or a
    payment draft already exists for this program -- checked explicitly
    here, in addition to (not merely inferred from) the draft/confirmed
    status guard, since attendance is keyed on (employee_id, date) with
    no direct program_id FK and a self-reported ('whatsapp_escort'
    source) attendance row could in principle exist before a program
    formally transitions to 'running'.
    """
    if not reason or not reason.strip():
        raise DispatchAssignmentError("reason_required", 422, "a mandatory reason is required for unassignment")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", expected_current_employee_id)
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", program_id)

            program = await fetch_one(
                """SELECT program_id, escort_employee_id, status,
                          COALESCE(start_date, program_date) AS eff_start, end_date
                   FROM wbom_escort_programs WHERE program_id = $1""",
                program_id, conn=conn,
            )
            if program is None:
                raise DispatchAssignmentError("program_not_found", 404, f"program {program_id} not found")

            if program["status"] not in _UNASSIGNABLE_FROM_STATUSES:
                raise DispatchAssignmentError(
                    "program_not_unassignable", 409,
                    f"program status {program['status']!r} is not eligible for unassignment "
                    f"(allowed: {list(_UNASSIGNABLE_FROM_STATUSES)})",
                )

            current_employee_id = program["escort_employee_id"]
            if current_employee_id is None:
                raise DispatchAssignmentError("not_assigned", 422, "program has no current assignment to remove")
            if current_employee_id != expected_current_employee_id:
                raise DispatchAssignmentError(
                    "stale_assignment", 409,
                    f"expected_current_employee_id={expected_current_employee_id!r} but the program's "
                    f"current assignment is {current_employee_id!r}; refresh and retry",
                )

            eff_start = program["eff_start"]
            eff_end = program["end_date"] or eff_start
            if eff_start is not None:
                attendance_row = await fetch_one(
                    """SELECT 1 FROM wbom_attendance
                       WHERE employee_id = $1 AND attendance_date BETWEEN $2 AND $3 LIMIT 1""",
                    current_employee_id, eff_start, eff_end, conn=conn,
                )
                if attendance_row:
                    raise DispatchAssignmentError(
                        "attendance_exists", 409,
                        "cannot unassign -- accepted attendance already exists for this program's window")

            payment_draft_row = await fetch_one(
                "SELECT 1 FROM fazle_payment_drafts WHERE escort_program_id = $1 LIMIT 1",
                program_id, conn=conn,
            )
            if payment_draft_row:
                raise DispatchAssignmentError(
                    "payment_draft_exists", 409,
                    "cannot unassign -- a payment draft already exists for this program")

            await execute(
                "UPDATE wbom_escort_programs SET escort_employee_id = NULL WHERE program_id = $1",
                program_id, conn=conn,
            )
            from modules.escort_roster.db import sync_program_to_roster, log_audit
            await sync_program_to_roster(program_id, actor=performed_by, conn=conn)
            await log_audit(
                program_id=program_id,
                action="dispatch_unassign",
                old_data={"escort_employee_id": current_employee_id, "status": program["status"]},
                new_data={"escort_employee_id": None, "reason": reason},
                performed_by=performed_by,
                source="dispatch_ui",
            )
            return {
                "program_id": program_id,
                "previous_employee_id": current_employee_id,
                "status": program["status"],
                "reason": reason,
            }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 5 — Manual Completion and Release-Slip Review
#
# Owner-approved (2026-07-19). Manual completion reuses
# modules.escort_lifecycle.close_program() directly -- the same, already
# certified, idempotent completion path [RELEASE CONFIRMED] messages use --
# never a second implementation. Release-slip review is built over the
# EXISTING escort_slip_extractions/escort_release_matches tables and the
# EXISTING modules.escort_roster.extractor.find_slip_matches() weighted
# scorer -- confirmed dormant (zero rows, no INSERT site) in the Decision
# Report; this stage gives escort_release_matches its first real write,
# without reviving its unused 'keep'/'reject' admin_action semantics --
# approval calls close_program(), never a parallel completion path.
# ─────────────────────────────────────────────────────────────────────────────

_TERMINAL_PROGRAM_STATUSES = ("completed", "cancelled", "escort_changed", "expired")


async def manually_complete_program(
    program_id: int,
    end_date: date,
    end_shift: str,
    release_point: Optional[str],
    reason: str,
    expected_status: Optional[str],
    performed_by: str,
) -> dict:
    """
    Phase 2A.3 sec.16/22. Admin/superadmin-only (enforced by the route
    layer, not here). Requires end_date, end_shift, release_point,
    mandatory reason. Rejects: status already terminal, end boundary
    before start boundary, duplicate completion. Never fabricates a
    missing end_shift -- normalize_shift() returning None is a hard
    rejection, not a silent default.
    """
    if not reason or not reason.strip():
        raise DispatchAssignmentError("reason_required", 422, "a mandatory reason is required for manual completion")

    norm_end_shift = normalize_shift(end_shift)
    if norm_end_shift is None:
        raise DispatchAssignmentError(
            "end_shift_required", 422, "a valid end_shift (D or N) is required -- it is never fabricated")

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", program_id)

            program = await fetch_one(
                """SELECT program_id, status, COALESCE(start_date, program_date) AS eff_start, shift
                   FROM wbom_escort_programs WHERE program_id = $1""",
                program_id, conn=conn,
            )
            if program is None:
                raise DispatchAssignmentError("program_not_found", 404, f"program {program_id} not found")

            if program["status"] in _TERMINAL_PROGRAM_STATUSES:
                raise DispatchAssignmentError(
                    "already_terminal", 409,
                    f"program {program_id} is already {program['status']!r} -- no duplicate completion")

            if expected_status is not None and program["status"] != expected_status:
                raise DispatchAssignmentError(
                    "stale_status", 409,
                    f"expected_status={expected_status!r} but program's current status is "
                    f"{program['status']!r}; refresh and retry",
                )

            eff_start = program["eff_start"]
            start_shift_v = normalize_shift(program["shift"]) or "D"
            if eff_start is not None:
                start_ord = shift_ordinal(eff_start, start_shift_v)
                end_ord = shift_ordinal(end_date, norm_end_shift)
                if end_ord < start_ord:
                    raise DispatchAssignmentError(
                        "invalid_window", 422,
                        f"end boundary ({end_date} {norm_end_shift}) is before the start boundary "
                        f"({eff_start} {start_shift_v})",
                    )

            from modules.escort_lifecycle import close_program
            from modules.escort_roster.db import sync_program_to_roster, log_audit

            result = await close_program(
                program_id=program_id, end_date_v=end_date, end_shift=norm_end_shift,
                release_point=release_point, day_count=None, completed_by=performed_by, conn=conn,
            )
            if not result.get("ok"):
                raise DispatchAssignmentError("completion_failed", 500, result.get("error", "close_program failed"))

            await sync_program_to_roster(program_id, actor=performed_by, conn=conn)
            await log_audit(
                program_id=program_id,
                action="dispatch_manual_completion",
                old_data={"status": program["status"]},
                new_data={"status": "completed", "end_date": str(end_date), "end_shift": norm_end_shift,
                          "release_point": release_point, "reason": reason, "day_count": result.get("day_count")},
                performed_by=performed_by, source="dispatch_ui",
            )
            return {
                "program_id": program_id, "status": "completed",
                "end_date": str(end_date), "end_shift": norm_end_shift,
                "release_point": release_point, "day_count": result.get("day_count"),
                "already_closed": result.get("already_closed", False),
            }


async def list_incomplete_drafts(page: int = 1, page_size: int = 50) -> dict:
    """Phase 2A.3 sec.19/24 -- read-only listing of draft programs missing
    escort identity (the exact TTL-eligible predicate expire_stale_drafts()
    uses, Stage 4), so the UI can show what's approaching/eligible for
    cleanup before it happens. Never mutates anything."""
    total = await fetch_val(
        """SELECT COUNT(*) FROM escort_roster_entries
           WHERE roster_status = 'draft'
             AND (escort_name IS NULL OR TRIM(escort_name) = '')
             AND (escort_mobile IS NULL OR TRIM(escort_mobile) = '')"""
    ) or 0
    offset = (page - 1) * page_size
    rows = await fetch_all(
        """SELECT program_id, mother_vessel, lighter_vessel, master_mobile, created_at,
                  EXTRACT(EPOCH FROM (NOW() - created_at)) / 3600.0 AS age_hours
           FROM escort_roster_entries
           WHERE roster_status = 'draft'
             AND (escort_name IS NULL OR TRIM(escort_name) = '')
             AND (escort_mobile IS NULL OR TRIM(escort_mobile) = '')
           ORDER BY created_at ASC
           LIMIT $1 OFFSET $2""",
        page_size, offset,
    )
    items = []
    for r in rows:
        d = dict(r)
        d["age_hours"] = round(float(d["age_hours"]), 1) if d["age_hours"] is not None else None
        d["created_at"] = str(d["created_at"])
        items.append(d)
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if page_size else 0}


async def list_pending_release_slips(page: int = 1, page_size: int = 20) -> dict:
    """Phase 2A.3 sec.19.2/21 -- read-only queue of OCR extractions that
    have not yet been approved or rejected (no escort_release_matches row
    with a terminal admin_action). Never mutates anything."""
    total = await fetch_val(
        """SELECT COUNT(*) FROM escort_slip_extractions e
           WHERE NOT EXISTS (
               SELECT 1 FROM escort_release_matches m
               WHERE m.extraction_id = e.id AND m.admin_action IN ('keep', 'reject')
           )"""
    ) or 0
    offset = (page - 1) * page_size
    rows = await fetch_all(
        """SELECT id, source_file, created_at, document_type, mother_vessel, lighter_vessel,
                  master_mobile, escort_name, escort_mobile, start_date, completion_date,
                  release_place, confidence
           FROM escort_slip_extractions e
           WHERE NOT EXISTS (
               SELECT 1 FROM escort_release_matches m
               WHERE m.extraction_id = e.id AND m.admin_action IN ('keep', 'reject')
           )
           ORDER BY created_at DESC
           LIMIT $1 OFFSET $2""",
        page_size, offset,
    )
    items = [dict(r) for r in rows]
    for it in items:
        it["created_at"] = str(it["created_at"])
        it["confidence"] = float(it["confidence"]) if it["confidence"] is not None else None
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "pages": (total + page_size - 1) // page_size if page_size else 0}


async def get_release_slip_candidates(extraction_id: int, top_n: int = 5) -> dict:
    """Phase 2A.3 sec.13.2/21 -- weighted deterministic candidate matching,
    reusing the EXISTING modules.escort_roster.extractor.find_slip_matches()
    scorer verbatim (never a second matching algorithm). Never mutates."""
    extraction = await fetch_one("SELECT * FROM escort_slip_extractions WHERE id = $1", extraction_id)
    if extraction is None:
        raise DispatchAssignmentError("extraction_not_found", 404, f"extraction {extraction_id} not found")

    from modules.escort_roster.extractor import find_slip_matches
    extracted = dict(extraction)
    candidates = await find_slip_matches(extracted, top_n=top_n)
    return {
        "extraction": {
            "id": extraction["id"], "source_file": extraction["source_file"],
            "document_type": extraction["document_type"], "mother_vessel": extraction["mother_vessel"],
            "lighter_vessel": extraction["lighter_vessel"], "master_mobile": extraction["master_mobile"],
            "escort_name": extraction["escort_name"], "escort_mobile": extraction["escort_mobile"],
            "start_date": extraction["start_date"], "completion_date": extraction["completion_date"],
            "release_place": extraction["release_place"],
            "confidence": float(extraction["confidence"]) if extraction["confidence"] is not None else None,
            "raw_text": extraction["raw_text"],
        },
        "candidates": candidates,
    }


async def approve_release_slip(
    extraction_id: int,
    program_id: int,
    end_shift: str,
    performed_by: str,
    release_point: Optional[str] = None,
) -> dict:
    """
    Phase 2A.3 sec.13.1/21. Approval calls the SAME certified
    close_program() path [RELEASE CONFIRMED] messages use -- never a
    parallel completion implementation. Requires the admin to explicitly
    confirm end_shift (never parsed/fabricated from OCR text -- the
    escort_slip_extractions schema itself has no shift column). If
    multiple programs are plausible, the caller must have already chosen
    one via get_release_slip_candidates() -- this function does not
    itself disambiguate.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", program_id)

            extraction = await fetch_one(
                "SELECT * FROM escort_slip_extractions WHERE id = $1", extraction_id, conn=conn)
            if extraction is None:
                raise DispatchAssignmentError("extraction_not_found", 404, f"extraction {extraction_id} not found")

            already_approved = await fetch_one(
                "SELECT id FROM escort_release_matches WHERE extraction_id = $1 AND admin_action IN ('keep', 'reject')",
                extraction_id, conn=conn,
            )
            if already_approved:
                raise DispatchAssignmentError(
                    "already_reviewed", 409, f"extraction {extraction_id} has already been reviewed")

            program = await fetch_one(
                """SELECT program_id, status, COALESCE(start_date, program_date) AS eff_start, shift
                   FROM wbom_escort_programs WHERE program_id = $1""",
                program_id, conn=conn,
            )
            if program is None:
                raise DispatchAssignmentError("program_not_found", 404, f"program {program_id} not found")
            if program["status"] in _TERMINAL_PROGRAM_STATUSES:
                raise DispatchAssignmentError(
                    "already_terminal", 409, f"program {program_id} is already {program['status']!r}")

            norm_end_shift = normalize_shift(end_shift)
            if norm_end_shift is None:
                raise DispatchAssignmentError(
                    "end_shift_required", 422, "a valid end_shift (D or N) is required -- it is never fabricated")

            completion_date_v = None
            raw_completion_date = extraction.get("completion_date")
            if raw_completion_date:
                # modules.escort_slip_extractor._normalize_date() stores DD-MM-YYYY
                # text, not ISO -- date.fromisoformat() would reject every real
                # OCR extraction, so this parses the extractor's own format.
                m = re.match(r"^(\d{1,2})-(\d{1,2})-(\d{4})$", str(raw_completion_date).strip())
                if m:
                    try:
                        completion_date_v = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    except ValueError:
                        pass
            if completion_date_v is None:
                raise DispatchAssignmentError(
                    "invalid_completion_date", 422,
                    "extraction has no usable completion_date -- cannot approve without a real end date")

            from modules.escort_lifecycle import close_program
            from modules.escort_roster.db import sync_program_to_roster, log_audit

            result = await close_program(
                program_id=program_id, end_date_v=completion_date_v, end_shift=norm_end_shift,
                release_point=release_point or extraction.get("release_place"),
                day_count=None, completed_by=performed_by, conn=conn,
            )
            if not result.get("ok"):
                raise DispatchAssignmentError("completion_failed", 500, result.get("error", "close_program failed"))

            await conn.execute(
                """INSERT INTO escort_release_matches
                       (extraction_id, program_id, match_confidence, match_reason, matched_fields,
                        admin_action, admin_phone, acted_at)
                   VALUES ($1, $2, $3, 'dispatch_release_slip_approval', $4, 'keep', $5, NOW())""",
                extraction_id, program_id,
                float(extraction["confidence"]) if extraction["confidence"] is not None else None,
                json.dumps({"approved_via": "dispatch_ui"}), performed_by,
            )

            await sync_program_to_roster(program_id, actor=performed_by, conn=conn)
            await log_audit(
                program_id=program_id,
                action="dispatch_release_slip_approved",
                old_data={"status": program["status"]},
                new_data={"status": "completed", "extraction_id": extraction_id,
                          "end_date": str(completion_date_v), "end_shift": norm_end_shift,
                          "day_count": result.get("day_count")},
                performed_by=performed_by, source="dispatch_ui",
            )
            return {
                "program_id": program_id, "extraction_id": extraction_id, "status": "completed",
                "end_date": str(completion_date_v), "end_shift": norm_end_shift,
                "day_count": result.get("day_count"),
            }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 6 — Controlled Override Review and Same-Vessel Review Queue
#
# Owner-approved (2026-07-19), Decision 5: the existing 48 different-vessel
# conflict pairs (and any future ones) are manual-review-only. Of the five
# reviewed actions Decision 5 names, three already have a dedicated guarded
# workflow (correct one -> assign_program()/create_replacement_segment();
# unassign one safe draft/confirmed assignment -> unassign_program(); cancel
# invalid duplicate -> explicitly NOT built, no separately-supported cancel
# workflow exists). Only the remaining two -- keep both as an authorized
# overlap, or defer for investigation -- have no mutation to perform, so
# record_conflict_pair_review() below records them as audit-only markers,
# never touching wbom_escort_programs.
# ─────────────────────────────────────────────────────────────────────────────

_CONFLICT_REVIEW_DECISIONS = ("keep_both_authorized", "defer_for_investigation")


async def record_conflict_pair_review(
    program_id_a: int,
    program_id_b: int,
    decision: str,
    note: str,
    performed_by: str,
) -> dict:
    """Pure review-decision recording (Phase 2A.3 sec.9/30, Decision 5).
    Never mutates wbom_escort_programs -- writes one audit entry to EACH
    program in the pair so either side's history shows the decision, and so
    list_overlaps() can surface it back via _enrich_pair_with_review()."""
    if decision not in _CONFLICT_REVIEW_DECISIONS:
        raise DispatchAssignmentError(
            "invalid_decision", 422, f"decision must be one of {_CONFLICT_REVIEW_DECISIONS}")
    if not note or not note.strip():
        raise DispatchAssignmentError(
            "note_required", 422, "a mandatory note is required to record a conflict review")
    if program_id_a == program_id_b:
        raise DispatchAssignmentError("invalid_pair", 422, "a program cannot be reviewed against itself")

    pool = get_pool()
    lo, hi = sorted((program_id_a, program_id_b))
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", lo)
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", hi)

            progs = await fetch_all(
                "SELECT program_id FROM wbom_escort_programs WHERE program_id = ANY($1::int[])",
                [program_id_a, program_id_b], conn=conn,
            )
            found_ids = {p["program_id"] for p in progs}
            if program_id_a not in found_ids or program_id_b not in found_ids:
                raise DispatchAssignmentError(
                    "program_not_found", 404, "one or both programs in the pair were not found")

            from modules.escort_roster.db import log_audit
            for pid, other in ((program_id_a, program_id_b), (program_id_b, program_id_a)):
                await log_audit(
                    program_id=pid,
                    action="dispatch_conflict_review",
                    old_data=None,
                    new_data={"decision": decision, "note": note, "pair_program_id": other},
                    performed_by=performed_by,
                    source="dispatch_ui",
                    conn=conn,
                )
            return {
                "program_id_a": program_id_a, "program_id_b": program_id_b,
                "decision": decision, "note": note, "performed_by": performed_by,
            }


# ── Same-vessel review queue (Phase 2A.3 sec.9.2/30) ───────────────────────────
#
# Read-only. Reuses the EXACT two-part method from the Decision Report's own
# historical audit: group all non-terminal programs by normalized
# (mother_vessel, lighter_vessel) pair (not by employee -- one side of a
# same-vessel change often has escort_employee_id IS NULL, invisible to
# list_overlaps()'s per-employee grouping), then classify each adjacent,
# different-escort transition inside a group using classify_vessel_match()
# and Decision 6's seven-day boundary. Never mutates anything -- this feeds
# the UI review queue AND the (separate, still-pending) Historical Overlap
# Recommendation Report.

_BULK_IMPORT_WINDOW_SECONDS = 60


def _escort_identity(p: dict) -> tuple:
    return (
        (p.get("escort_mobile") or "").strip(),
        (p.get("escort_name") or "").strip().lower(),
        p.get("escort_employee_id"),
    )


async def find_same_vessel_review_candidates(include_terminal: bool = False) -> dict:
    """Returns candidates grouped by category: same_date_historical,
    under_seven_day, seven_plus_day, insufficient_data, bulk_import_artifact.
    Each item names program IDs, current values, the rule/evidence behind
    the classification, and NO proposed mutation is applied -- classification
    only, exactly like the Decision Report's own read-only analysis."""
    status_filter = "" if include_terminal else (
        "WHERE status NOT IN ('completed', 'cancelled', 'expired', 'escort_changed')")
    rows = await fetch_all(
        f"""SELECT program_id, mother_vessel, lighter_vessel, escort_name, escort_mobile,
                   escort_employee_id, COALESCE(start_date, program_date) AS eff_start, shift,
                   status, remarks, assignment_time
            FROM wbom_escort_programs
            {status_filter}
            ORDER BY mother_vessel, lighter_vessel, COALESCE(start_date, program_date)"""
    )

    groups: dict = {}
    for r in rows:
        d = dict(r)
        n_mv = _normalize_vessel_for_matching(d.get("mother_vessel"))
        n_lv = _normalize_vessel_for_matching(d.get("lighter_vessel"))
        if not n_mv:
            continue  # no vessel identity at all -- nothing to group on
        groups.setdefault((n_mv, n_lv), []).append(d)

    same_date_historical, under_seven_day, seven_plus_day = [], [], []
    insufficient_data, bulk_import_artifact = [], []
    seen_insufficient_pairs = set()

    for (n_mv, n_lv), progs in groups.items():
        if len(progs) < 2:
            continue
        progs.sort(key=lambda p: (p["eff_start"] or date.min, normalize_shift(p["shift"]) or "D"))
        for i in range(len(progs) - 1):
            a, b = progs[i], progs[i + 1]
            if _escort_identity(a) == _escort_identity(b):
                continue  # same escort re-appearing -- not a change candidate

            a_remarks = (a.get("remarks") or "")
            b_remarks = (b.get("remarks") or "")
            bulk_marker = ("source_bridge" in a_remarks or "auto_created" in a_remarks) and \
                          ("source_bridge" in b_remarks or "auto_created" in b_remarks)
            # wbom_escort_programs has no created_at column in production
            # (confirmed via information_schema -- unlike escort_roster_entries/
            # escort_slip_extractions/escort_roster_audit_logs, which do) --
            # assignment_time is the closest equivalent (DEFAULT NOW() on insert).
            created_delta = abs((b["assignment_time"] - a["assignment_time"]).total_seconds()) \
                if a.get("assignment_time") and b.get("assignment_time") else None
            is_bulk_artifact = bulk_marker and created_delta is not None \
                and created_delta <= _BULK_IMPORT_WINDOW_SECONDS

            # Bulk-import artifacts are checked BEFORE the generic
            # insufficient_data fallback below -- they are definitionally a
            # more specific case of it (Decision 3: the exact false-match
            # pattern found in a real bulk-import batch during the Decision
            # Report's historical audit had an empty lighter_vessel).
            if is_bulk_artifact:
                bulk_import_artifact.append({
                    "program_id_earlier": a["program_id"], "program_id_later": b["program_id"],
                    "mother_vessel": a.get("mother_vessel"), "lighter_vessel": a.get("lighter_vessel"),
                    "escort_earlier": {"name": a.get("escort_name"), "mobile": a.get("escort_mobile"),
                                        "employee_id": a.get("escort_employee_id")},
                    "escort_later": {"name": b.get("escort_name"), "mobile": b.get("escort_mobile"),
                                      "employee_id": b.get("escort_employee_id")},
                    "reason": (
                        f"same/insufficient lighter_vessel, created within {created_delta:.0f}s of each other "
                        f"-- likely a bulk-import duplicate, not a real change"),
                })
                continue

            if not n_lv:
                key = (n_mv, a["program_id"], b["program_id"])
                if key not in seen_insufficient_pairs:
                    seen_insufficient_pairs.add(key)
                    insufficient_data.append({
                        "program_id_earlier": a["program_id"], "program_id_later": b["program_id"],
                        "mother_vessel": a.get("mother_vessel"), "lighter_vessel": a.get("lighter_vessel"),
                        "reason": "lighter_vessel empty on at least one side -- Decision 3 requires manual review",
                    })
                continue

            if a["eff_start"] is None or b["eff_start"] is None:
                continue  # cannot classify without both dates -- leave to manual review elsewhere

            same_date = a["eff_start"] == b["eff_start"]
            delta_days = (b["eff_start"] - a["eff_start"]).days if not same_date else 0

            item = {
                "program_id_earlier": a["program_id"], "program_id_later": b["program_id"],
                "mother_vessel": a.get("mother_vessel"), "lighter_vessel": a.get("lighter_vessel"),
                "escort_earlier": {"name": a.get("escort_name"), "mobile": a.get("escort_mobile"),
                                    "employee_id": a.get("escort_employee_id")},
                "escort_later": {"name": b.get("escort_name"), "mobile": b.get("escort_mobile"),
                                  "employee_id": b.get("escort_employee_id")},
                "status_earlier": a.get("status"), "status_later": b.get("status"),
                "start_date_earlier": str(a["eff_start"]), "start_date_later": str(b["eff_start"]),
                "date_delta_days": delta_days,
            }

            if same_date:
                item["reason"] = "identical start_date -- predates or was missed by the Stage 2 correction guard"
                same_date_historical.append(item)
            elif delta_days < _REPLACEMENT_UNDER_DAYS_THRESHOLD:
                item["reason"] = f"date_delta={delta_days}d < 7 -- Decision 6 candidate for escort_changed"
                under_seven_day.append(item)
            else:
                item["reason"] = f"date_delta={delta_days}d >= 7 -- Decision 6 candidate for completed"
                seven_plus_day.append(item)

    return {
        "same_date_historical": same_date_historical,
        "under_seven_day": under_seven_day,
        "seven_plus_day": seven_plus_day,
        "insufficient_data": insufficient_data,
        "bulk_import_artifact": bulk_import_artifact,
        "note": "Classification only -- no mutation is applied by this function. Every listed candidate "
                "requires the applicable guarded workflow (replace-escort / manual review) before any change.",
    }
