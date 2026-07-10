"""
Office Attendance — FastAPI Router (Owner decision, 2026-07-11)

All routes under /admin/attendance and /admin/payroll/*. Auth: X-Internal-Key
header or an active admin's API key, gated at admin+/superadmin only —
matching modules.client_billing.routes's precedent (this data includes
basic_salary and payroll calculations, equally sensitive).

  GET    /admin/attendance                      — list/search/filter/paginate
  GET    /admin/attendance/{attendance_id}       — view one
  POST   /admin/attendance                       — manual create (entry_source=manual_office)
  PUT    /admin/attendance/{attendance_id}       — edit
  DELETE /admin/attendance/{attendance_id}       — delete
  GET    /admin/attendance/lookup/by-id/{employee_id}  — exact employee lookup
  GET    /admin/attendance/lookup/suggest        — name-based suggestions

  GET    /admin/payroll/eligibility-preview      — read-only, no mutation
  GET    /admin/payroll/dry-run                  — read-only, no mutation
  GET    /admin/payroll/reconciliation           — read-only, no mutation

Registered in app/main.py:
  from modules.attendance.routes import router as attendance_router
  app.include_router(attendance_router)
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings as _get_settings
from modules.escort_roster.routes import _resolve_actor

from .crud import (
    create_manual_attendance,
    delete_manual_attendance,
    get_attendance,
    list_attendance,
    lookup_employee_by_id,
    suggest_employees_by_name,
    update_manual_attendance,
)

router = APIRouter(tags=["attendance"])

# Local copy — avoids circular import, matches modules.drafts.routes's own
# existing pattern for the same reason.
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_admin_or_above(key: str = Depends(_API_KEY_HEADER)) -> dict:
    """Attendance + payroll data includes basic_salary and duty calculations
    — admin or superadmin only, matching modules.client_billing.routes's
    precedent for equally sensitive data."""
    actor = await _resolve_actor(key)
    roles = {str(r or "").strip().lower() for r in actor.get("roles", [])}
    if actor.get("is_internal") or roles.intersection({"admin", "superadmin", "owner"}):
        return actor
    raise HTTPException(status_code=403, detail="admin or superadmin required")


def _actor_identity(actor: dict) -> str:
    return actor.get("phone") or actor.get("name") or "unknown-admin"


class AttendanceCreateIn(BaseModel):
    employee_id: int
    attendance_date: date
    duty_status: str
    remarks: Optional[str] = None


class AttendanceUpdateIn(BaseModel):
    duty_status: Optional[str] = None
    remarks: Optional[str] = None


@router.get("/admin/attendance", dependencies=[Depends(_require_admin_or_above)])
async def api_list_attendance(
    employee_id: Optional[int] = None,
    search: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    entry_source: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    return await list_attendance(
        employee_id=employee_id, search=search, date_from=date_from, date_to=date_to,
        entry_source=entry_source, page=page, page_size=page_size,
    )


@router.get("/admin/attendance/lookup/by-id/{employee_id}", dependencies=[Depends(_require_admin_or_above)])
async def api_lookup_by_id(employee_id: int):
    emp = await lookup_employee_by_id(employee_id)
    if not emp:
        raise HTTPException(status_code=404, detail=f"employee_id {employee_id} not found")
    return emp


@router.get("/admin/attendance/lookup/suggest", dependencies=[Depends(_require_admin_or_above)])
async def api_suggest_by_name(q: str = Query(..., min_length=1), limit: int = Query(8, ge=1, le=20)):
    return {"results": await suggest_employees_by_name(q, limit=limit)}


@router.get("/admin/attendance/{attendance_id}", dependencies=[Depends(_require_admin_or_above)])
async def api_get_attendance(attendance_id: int):
    row = await get_attendance(attendance_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"attendance_id {attendance_id} not found")
    return row


@router.post("/admin/attendance")
async def api_create_attendance(body: AttendanceCreateIn, actor: dict = Depends(_require_admin_or_above)):
    return await create_manual_attendance(
        employee_id=body.employee_id,
        attendance_date=body.attendance_date,
        duty_status=body.duty_status,
        remarks=body.remarks,
        actor=_actor_identity(actor),
    )


@router.put("/admin/attendance/{attendance_id}")
async def api_update_attendance(attendance_id: int, body: AttendanceUpdateIn, actor: dict = Depends(_require_admin_or_above)):
    return await update_manual_attendance(
        attendance_id, duty_status=body.duty_status, remarks=body.remarks, actor=_actor_identity(actor),
    )


@router.delete("/admin/attendance/{attendance_id}")
async def api_delete_attendance(attendance_id: int, actor: dict = Depends(_require_admin_or_above)):
    return await delete_manual_attendance(attendance_id, actor=_actor_identity(actor))


# ── Payroll analysis — read-only, no mutation ────────────────────────────────

@router.get("/admin/payroll/eligibility-preview", dependencies=[Depends(_require_admin_or_above)])
async def api_eligibility_preview(year: int = Query(...), month: int = Query(..., ge=1, le=12)):
    from modules.payroll.dry_run import eligibility_preview
    rows = await eligibility_preview(year, month)
    return {"year": year, "month": month, "rows": rows, "payable_count": sum(1 for r in rows if r["included"])}


@router.get("/admin/payroll/dry-run", dependencies=[Depends(_require_admin_or_above)])
async def api_payroll_dry_run(year: int = Query(...), month: int = Query(..., ge=1, le=12)):
    from modules.payroll.dry_run import payroll_dry_run
    rows = await payroll_dry_run(year, month)
    return {"year": year, "month": month, "rows": rows, "payable_count": sum(1 for r in rows if r["included"])}


@router.get("/admin/payroll/reconciliation", dependencies=[Depends(_require_admin_or_above)])
async def api_reconciliation_report(year: int = Query(...), month: int = Query(..., ge=1, le=12)):
    from modules.payroll.dry_run import reconciliation_report
    return await reconciliation_report(year, month)
