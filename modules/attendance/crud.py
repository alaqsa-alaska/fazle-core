"""
Fazle Core — Manual Office Attendance CRUD (Owner decision, 2026-07-11)

Direct frontend-authored attendance entry: entry_source='manual_office',
approval_channel='manual_direct', approved immediately at creation (no
draft/review stage) — only reachable by an already-authorized (admin+)
actor, per this module's routes.py RBAC gate.

Employee DB (wbom_employees) remains authoritative. This module never
duplicates mutable employee identity fields into wbom_attendance beyond
employee_id and the audit/source columns added by migration 040 — every
read joins back to wbom_employees for display fields.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import asyncpg
from fastapi import HTTPException

from app.database import execute, fetch_all, fetch_one, fetch_val

log = logging.getLogger("fazle.attendance.crud")

DUTY_STATUS_VALUES = ("Present", "Half Day", "Absent")
_DUTY_DAY_BY_STATUS = {"Present": 1.0, "Half Day": 0.5, "Absent": 0.0}


def derive_duty_days(duty_status: str) -> float:
    """Backend-derived duty-day value. Never trust a client-submitted
    numeric duty-day amount — the frontend may only submit a constrained
    Duty Status string, and this is the sole place that value is converted."""
    if duty_status not in DUTY_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"duty_status must be one of {list(DUTY_STATUS_VALUES)}",
        )
    return _DUTY_DAY_BY_STATUS[duty_status]


async def _validate_employee(employee_id: int) -> dict:
    row = await fetch_one(
        "SELECT employee_id, employee_name, status FROM wbom_employees WHERE employee_id = $1",
        employee_id,
    )
    if not row:
        raise HTTPException(status_code=400, detail=f"employee_id {employee_id} does not exist")
    return dict(row)


async def create_manual_attendance(
    employee_id: int,
    attendance_date: date,
    duty_status: str,
    remarks: Optional[str],
    actor: str,
) -> dict:
    """Explicit Create. Rejects with 409 if a row already exists for this
    employee+date — unlike the parser-approval path's idempotent upsert,
    manual Create must never silently overwrite an existing result. Use
    update_manual_attendance (Edit) to change an existing row instead."""
    await _validate_employee(employee_id)
    derive_duty_days(duty_status)
    approved_at = datetime.now(timezone.utc)
    try:
        row = await fetch_one(
            """INSERT INTO wbom_attendance
                   (employee_id, attendance_date, status, remarks, recorded_by,
                    entry_source, approval_channel, approved_by, approved_at)
               VALUES ($1, $2, $3, $4, $5, 'manual_office', 'manual_direct', $6, $7)
               RETURNING *""",
            employee_id, attendance_date, duty_status, remarks or "", actor, actor, approved_at,
        )
    except asyncpg.UniqueViolationError:
        log.warning(f"[attendance-crud] duplicate create rejected emp={employee_id} date={attendance_date} actor={actor}")
        raise HTTPException(
            status_code=409,
            detail=f"Attendance already exists for employee_id {employee_id} on {attendance_date}. Use Edit instead.",
        )
    log.info(
        f"[attendance-crud] created attendance_id={row['attendance_id']} emp={employee_id} "
        f"date={attendance_date} status={duty_status} actor={actor} channel=manual_direct"
    )
    return dict(row)


async def update_manual_attendance(
    attendance_id: int,
    duty_status: Optional[str],
    remarks: Optional[str],
    actor: str,
) -> dict:
    existing = await fetch_one("SELECT * FROM wbom_attendance WHERE attendance_id = $1", attendance_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"attendance_id {attendance_id} not found")

    set_clauses: list[str] = []
    params: list = []
    idx = 1
    if duty_status is not None:
        derive_duty_days(duty_status)
        set_clauses.append(f"status = ${idx}")
        params.append(duty_status)
        idx += 1
    if remarks is not None:
        set_clauses.append(f"remarks = ${idx}")
        params.append(remarks)
        idx += 1
    set_clauses.append(f"approval_channel = ${idx}"); params.append("manual_direct"); idx += 1
    set_clauses.append(f"approved_by = ${idx}"); params.append(actor); idx += 1
    set_clauses.append(f"approved_at = ${idx}"); params.append(datetime.now(timezone.utc)); idx += 1
    params.append(attendance_id)

    row = await fetch_one(
        f"UPDATE wbom_attendance SET {', '.join(set_clauses)} WHERE attendance_id = ${idx} RETURNING *",
        *params,
    )
    log.info(f"[attendance-crud] edited attendance_id={attendance_id} actor={actor} before={dict(existing)} after={dict(row)}")
    return dict(row)


async def delete_manual_attendance(attendance_id: int, actor: str) -> dict:
    existing = await fetch_one("SELECT * FROM wbom_attendance WHERE attendance_id = $1", attendance_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"attendance_id {attendance_id} not found")
    await execute("DELETE FROM wbom_attendance WHERE attendance_id = $1", attendance_id)
    log.info(f"[attendance-crud] deleted attendance_id={attendance_id} actor={actor} record={dict(existing)}")
    return dict(existing)


async def create_attendance_draft_entry(
    employee_id: int,
    attendance_date: date,
    duty_status: str,
    remarks: Optional[str],
    actor: str,
) -> dict:
    """
    office_assistant's manual entry — creates a PENDING draft, never a final
    wbom_attendance row directly (Owner decision: office_assistant has no
    approve/edit power). An admin/superadmin must approve it via the
    existing shared finalize_attendance_draft() service (same path already
    used for WhatsApp APPROVE and the frontend Review Queue), which is what
    actually writes wbom_attendance with entry_source='message_parser'
    (approval-gated office payroll — see modules.attendance's
    finalize_attendance_draft docstring for why office_assistant_manual maps
    to that entry_source, not a new one).
    """
    import json as _json

    emp = await _validate_employee(employee_id)
    derive_duty_days(duty_status)

    context = _json.dumps({
        "employee_id": employee_id,
        "att_date": attendance_date.isoformat(),
        "duty_status": duty_status,
    })
    meta = _json.dumps({
        "role": "office_assistant",
        "context": context,
        "source_module": "office_assistant_manual",
    })
    draft_text = (
        f"Attendance draft: {emp.get('employee_name')} (#{employee_id}) "
        f"{attendance_date.isoformat()} — {duty_status}"
    )
    row = await fetch_one(
        """
        INSERT INTO fazle_draft_replies
            (source, recipient, reply_text, intent, draft_only, status, draft_type, meta)
        VALUES ('office_assistant', $1, $2, 'attendance', true, 'pending', 'attendance', $3::jsonb)
        RETURNING id, status, created_at
        """,
        actor, draft_text, meta,
    )
    log.info(
        f"[attendance-crud] draft created id={row['id']} emp={employee_id} "
        f"date={attendance_date} status={duty_status} actor={actor} — pending admin approval"
    )
    return {
        "draft_id": row["id"],
        "status": row["status"],
        "employee_id": employee_id,
        "employee_name": emp.get("employee_name"),
        "attendance_date": attendance_date,
        "duty_status": duty_status,
        "created_at": row["created_at"],
    }


async def get_attendance(attendance_id: int) -> Optional[dict]:
    row = await fetch_one(
        """SELECT a.*, e.employee_name, e.designation
           FROM wbom_attendance a JOIN wbom_employees e ON e.employee_id = a.employee_id
           WHERE a.attendance_id = $1""",
        attendance_id,
    )
    return dict(row) if row else None


async def list_attendance(
    *,
    employee_id: Optional[int] = None,
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    entry_source: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    conditions: list[str] = []
    params: list = []
    idx = 1
    if employee_id:
        conditions.append(f"a.employee_id = ${idx}"); params.append(employee_id); idx += 1
    if search:
        conditions.append(f"(e.employee_name ILIKE ${idx} OR e.employee_mobile ILIKE ${idx})")
        params.append(f"%{search}%"); idx += 1
    if date_from:
        conditions.append(f"a.attendance_date >= ${idx}"); params.append(date_from); idx += 1
    if date_to:
        conditions.append(f"a.attendance_date <= ${idx}"); params.append(date_to); idx += 1
    if entry_source:
        conditions.append(f"a.entry_source = ${idx}"); params.append(entry_source); idx += 1

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    page = max(page, 1)
    offset = (page - 1) * page_size

    total = await fetch_val(
        f"SELECT COUNT(*) FROM wbom_attendance a JOIN wbom_employees e ON e.employee_id = a.employee_id {where_clause}",
        *params,
    )
    rows = await fetch_all(
        f"""SELECT a.*, e.employee_name, e.designation
            FROM wbom_attendance a JOIN wbom_employees e ON e.employee_id = a.employee_id
            {where_clause}
            ORDER BY a.attendance_date DESC, a.attendance_id DESC
            LIMIT ${idx} OFFSET ${idx + 1}""",
        *params, page_size, offset,
    )
    total = total or 0
    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
    }


# ── Structured employee lookup (Owner decision: dedicated, non-fuzzy) ────────
# Deliberately not modules.fazle_payroll_engine.routes.suggest_employees,
# which is tuned for messy free-form WhatsApp text against fpe_employees.
# This is a plain, structured lookup against wbom_employees (the canonical
# Employee DB) for the admin-facing form.

_LOOKUP_FIELDS = "w.employee_id, w.employee_name, w.employee_mobile, w.address, w.designation, w.status, w.basic_salary"

# 2026-07-13 audit Fix #3: attach fpe_employees.employee_id_phone (the
# identifier every OTHER payroll.html form already shows) via a scalar
# subquery, not a JOIN -- a JOIN with these OR conditions could multiply
# rows if more than one fpe_employees row matched. Prefers the clean
# wbom_employee_id FK link (set as a "runtime repair" by
# fazle_payroll_engine.employee.match_or_create_employee's step 3b)
# over the phone-string fallback when both exist. Read-only: unlike that
# step 3b logic, this never creates or links anything, just displays
# whatever link already exists.
_FPE_PHONE_SUBQUERY = """(
    SELECT fpe.employee_id_phone FROM fpe_employees fpe
    WHERE fpe.wbom_employee_id = w.employee_id
       OR fpe.primary_phone IN (w.employee_mobile, w.bkash_number, w.nagad_number)
       OR fpe.employee_id_phone IN (w.employee_mobile, w.bkash_number, w.nagad_number)
    ORDER BY (fpe.wbom_employee_id = w.employee_id) DESC
    LIMIT 1
) AS employee_id_phone"""


async def lookup_employee_by_id(employee_id: int) -> Optional[dict]:
    row = await fetch_one(
        f"SELECT {_LOOKUP_FIELDS}, {_FPE_PHONE_SUBQUERY} FROM wbom_employees w WHERE w.employee_id = $1",
        employee_id,
    )
    return dict(row) if row else None


async def suggest_employees_by_name(query: str, limit: int = 8) -> list[dict]:
    """Returns every match (not just the first) so the frontend can present
    duplicate-name disambiguation instead of silently guessing."""
    if not query or not query.strip():
        return []
    rows = await fetch_all(
        f"""SELECT {_LOOKUP_FIELDS}, {_FPE_PHONE_SUBQUERY} FROM wbom_employees w
            WHERE w.employee_name ILIKE $1
            ORDER BY (w.status = 'Active') DESC, w.employee_name
            LIMIT $2""",
        f"%{query.strip()}%", limit,
    )
    return [dict(r) for r in rows]
