"""
Fazle Core — Office Payroll: eligibility preview, dry run, reconciliation
(Owner decision, 2026-07-10/11).

Pure read/compute. Never mutates wbom_payroll_runs, wbom_payroll_run_items,
fpe_cash_transactions, fazle_payment_drafts, or wbom_attendance.

Canonical formula: basic_salary / calendar_days_in_month * approved_duty_days.
Duty days come only from wbom_attendance rows whose entry_source is
'manual_office' or 'message_parser' -- every row in wbom_attendance is
already "approved" by construction (a parser draft never reaches this table
until modules.attendance.finalize_attendance_draft() commits it), so no
separate approval-status filter is needed on the attendance table itself.
'whatsapp_escort' and 'escort_release' rows are excluded regardless of
approval, per Owner decision.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from app.database import fetch_all
from modules.payroll.eligibility import classify_office_staff

OFFICE_PAYROLL_SOURCES = ("manual_office", "message_parser")

_DUTY_DAY_VALUES = {
    "present": Decimal("1.0"),
    "half day": Decimal("0.5"),
    "absent": Decimal("0.0"),
}


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _parse_draft_attendance_fields(meta) -> dict:
    """Same defensive parse as modules.attendance._parse_attendance_draft_fields
    (duplicated narrowly here to avoid a payroll->attendance module import
    cycle risk) -- handles both the context-wrapped and legacy flat shapes."""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    meta = meta or {}
    context = meta.get("context")
    if context:
        try:
            inner = json.loads(context) if isinstance(context, str) else context
            if isinstance(inner, dict) and inner:
                return inner
        except Exception:
            pass
    if any(k in meta for k in ("employee_id", "employee_mobile", "employee_name")):
        return meta
    return {}


async def _attendance_summary(employee_id: int, period_start: date, period_end: date) -> dict:
    rows = await fetch_all(
        """SELECT status, entry_source FROM wbom_attendance
           WHERE employee_id = $1 AND attendance_date BETWEEN $2 AND $3
             AND entry_source = ANY($4::text[])""",
        employee_id, period_start, period_end, list(OFFICE_PAYROLL_SOURCES),
    )
    manual_days = sum(1 for r in rows if r["entry_source"] == "manual_office")
    parser_days = sum(1 for r in rows if r["entry_source"] == "message_parser")
    total = sum(_DUTY_DAY_VALUES.get((r["status"] or "").strip().lower(), Decimal("0")) for r in rows)
    return {
        "approved_manual_days": manual_days,
        "approved_parser_days": parser_days,
        "total_duty_days": float(total),
    }


async def _pending_rejected_counts(employee_id: int, period_start: date, period_end: date) -> dict:
    """Best-effort: fazle_draft_replies has no native date column for
    attendance drafts, so this correlates via the parsed att_date inside
    meta. Documented limitation: drafts whose att_date could not be parsed
    at all are not counted here (they also can't be finalized, so they stay
    correctly excluded from payroll either way)."""
    rows = await fetch_all(
        "SELECT status, meta FROM fazle_draft_replies WHERE intent = 'attendance' AND status IN ('pending','edited','rejected')"
    )
    pending = 0
    rejected = 0
    for r in rows:
        fields = _parse_draft_attendance_fields(r["meta"])
        if fields.get("employee_id") != employee_id:
            continue
        att_date_str = fields.get("att_date")
        try:
            att_date = date.fromisoformat(att_date_str) if att_date_str else None
        except Exception:
            att_date = None
        if att_date and not (period_start <= att_date <= period_end):
            continue
        if r["status"] in ("pending", "edited"):
            pending += 1
        elif r["status"] == "rejected":
            rejected += 1
    return {"pending_parser_drafts": pending, "rejected_parser_drafts": rejected}


async def eligibility_preview(year: int, month: int) -> list[dict]:
    """One row per employee: structural classification, plus (for
    structurally-eligible employees) the attendance-based inclusion result
    for the given payroll month."""
    last_day = monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, last_day)

    employees = await fetch_all(
        "SELECT employee_id, employee_name, designation, status, basic_salary "
        "FROM wbom_employees ORDER BY employee_id"
    )
    results = []
    for emp_row in employees:
        emp = dict(emp_row)
        classification = classify_office_staff(emp)
        row = {
            "employee_id": emp["employee_id"],
            "employee_name": emp["employee_name"],
            "designation": emp["designation"],
            "status": emp["status"],
            "basic_salary": float(emp["basic_salary"]) if emp["basic_salary"] is not None else None,
            "is_office_staff": classification["is_office_staff"],
            "approved_manual_days": 0,
            "approved_parser_days": 0,
            "total_duty_days": 0.0,
            "pending_parser_drafts": 0,
            "rejected_parser_drafts": 0,
        }
        if not classification["eligible"]:
            row["included"] = False
            row["reason"] = classification["reason"]
            results.append(row)
            continue

        att = await _attendance_summary(emp["employee_id"], period_start, period_end)
        pr = await _pending_rejected_counts(emp["employee_id"], period_start, period_end)
        row.update(att)
        row.update(pr)

        if att["total_duty_days"] <= 0:
            row["included"] = False
            row["reason"] = "Excluded: no approved duty attendance for selected month"
        else:
            if att["approved_manual_days"] and att["approved_parser_days"]:
                row["reason"] = "Included: active office employee with approved manual and admin-approved parser attendance"
            elif att["approved_parser_days"]:
                row["reason"] = "Included: active office employee with admin-approved parser attendance"
            else:
                row["reason"] = "Included: active office employee with approved manual attendance"
            row["included"] = True
        results.append(row)
    return results


async def payroll_dry_run(year: int, month: int) -> list[dict]:
    """Non-destructive: creates no payroll runs, items, cash transactions,
    payment drafts, or ledger entries. Decimal-safe; rounds to 2 decimal
    places using ROUND_HALF_UP."""
    last_day = monthrange(year, month)[1]
    rows = await eligibility_preview(year, month)
    out = []
    for row in rows:
        result = {**row, "calendar_days": last_day, "daily_rate": None, "calculated_salary": None}
        if row["included"]:
            salary = Decimal(str(row["basic_salary"]))
            calendar_days = Decimal(str(last_day))
            duty_days = Decimal(str(row["total_duty_days"]))
            daily_rate = salary / calendar_days
            result["daily_rate"] = _money(daily_rate)
            result["calculated_salary"] = _money(daily_rate * duty_days)
        out.append(result)
    return out


async def reconciliation_report(year: int, month: int) -> dict:
    """Read-only comparison + data-quality warnings. Never mutates anything."""
    last_day = monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, last_day)
    rows = await payroll_dry_run(year, month)

    warnings: list[str] = []
    for row in rows:
        if (row.get("basic_salary") or 0) > 0 and not row["included"] and \
                row.get("reason") == "Excluded: no approved duty attendance for selected month":
            warnings.append(
                f"employee {row['employee_id']} ({row['employee_name']}): has salary but no duty data this month"
            )
        if row["included"] and row.get("total_duty_days", 0) > 0 and not (row.get("basic_salary") or 0) > 0:
            warnings.append(f"employee {row['employee_id']}: has duty data but no valid salary")
        if row.get("pending_parser_drafts"):
            warnings.append(
                f"employee {row['employee_id']}: {row['pending_parser_drafts']} pending parser draft(s) "
                "correctly excluded this period"
            )
        if row.get("rejected_parser_drafts"):
            warnings.append(
                f"employee {row['employee_id']}: {row['rejected_parser_drafts']} rejected parser draft(s) "
                "correctly excluded this period"
            )

    dupes = await fetch_all(
        """SELECT employee_id, attendance_date, COUNT(*) AS c FROM wbom_attendance
           WHERE attendance_date BETWEEN $1 AND $2
           GROUP BY employee_id, attendance_date HAVING COUNT(*) > 1""",
        period_start, period_end,
    )
    for d in dupes:
        warnings.append(f"DATA INTEGRITY: duplicate attendance employee={d['employee_id']} date={d['attendance_date']}")

    orphans = await fetch_all(
        """SELECT wa.employee_id, COUNT(*) AS c FROM wbom_attendance wa
           LEFT JOIN wbom_employees we ON we.employee_id = wa.employee_id
           WHERE we.employee_id IS NULL AND wa.attendance_date BETWEEN $1 AND $2
           GROUP BY wa.employee_id""",
        period_start, period_end,
    )
    for o in orphans:
        warnings.append(f"DATA INTEGRITY: unresolved employee_id={o['employee_id']} referenced in attendance")

    return {
        "year": year,
        "month": month,
        "calendar_days": last_day,
        "rows": rows,
        "warnings": warnings,
        "status": "clean" if not warnings else "warnings_found",
    }
