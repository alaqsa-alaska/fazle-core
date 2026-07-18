"""
Fazle Core — Dispatch Phase 2A.1 (read-only) + Phase 2A.2 (controlled
manual assignment) API routes.

Phase 2A.1 endpoints (availability/check-conflict/suggestions/overlaps) are
read-only. RBAC: viewer role or above (modules.rbac's existing fazle_roles
hierarchy) -- the closest canonical precedent for read-only listing/detail
endpoints that reveal operational (not payroll/salary) data, matching
modules.fazle_payroll_engine.routes's _require_viewer_or_above pattern
exactly (local copy here to avoid a cross-module import cycle, same
justification as that module's own copy of _require_api_key).

Phase 2A.2's assignment endpoint is the only write in this router. RBAC:
operator role or above, matching modules.fazle_payroll_engine.routes's own
_require_operator_or_above pattern verbatim (local copy, same cross-module-
import-cycle justification as above) -- confirmed via a full trace of every
existing escort_employee_id writer that no assignment-specific write-gate
already exists to reuse.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings
from modules import dispatch as dispatch_svc

router = APIRouter(prefix="/api/dispatch", tags=["dispatch"])

_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_viewer_or_above(key: str = Depends(_API_KEY_HEADER)) -> str:
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return key
    from modules import rbac
    check = await rbac.check_permission(api_key=key or "", command="status")
    if check.get("allowed"):
        return key
    raise HTTPException(status_code=403, detail="viewer role or above required")


async def _require_operator_or_above(key: str = Depends(_API_KEY_HEADER)) -> str:
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return key
    from modules import rbac
    check = await rbac.check_permission(api_key=key or "", command="approve")
    if check.get("allowed"):
        return key
    raise HTTPException(status_code=403, detail="operator role or above required")


def _parse_shift_query(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    norm = dispatch_svc.normalize_shift(value)
    if norm is None:
        raise HTTPException(status_code=400, detail=f"invalid shift value: {value!r} (expected D or N)")
    return norm


@router.get("/availability", dependencies=[Depends(_require_viewer_or_above)])
async def api_availability(
    employee_id: Optional[int] = Query(None),
    available_only: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Read-only listing of active escort workers with current
    availability, conflict (if any), last assignment date, and active
    workload. Never returns all employees -- filtered to active escort
    workers only (designation='Escort', status='Active')."""
    result = await dispatch_svc.list_escort_workers(
        active_only=True, employee_id=employee_id, page=page, page_size=page_size,
    )
    if available_only is not None:
        result["items"] = [i for i in result["items"] if i["available"] == available_only]
    return result


class ConflictCheckRequest(BaseModel):
    escort_employee_id: int
    start_date: date
    start_shift: str
    end_date: date
    end_shift: str
    exclude_program_id: Optional[int] = None


@router.post("/check-conflict", dependencies=[Depends(_require_viewer_or_above)])
async def api_check_conflict(body: ConflictCheckRequest):
    """Structured read-only conflict query -- POST because the request has
    several fields, but this never mutates any table (matches the pattern
    already used elsewhere in this repo for structured read-only queries,
    e.g. modules/escort_roster/routes.py's /match-slip)."""
    start_shift = _parse_shift_query(body.start_shift)
    end_shift = _parse_shift_query(body.end_shift)
    return await dispatch_svc.check_conflict(
        escort_employee_id=body.escort_employee_id,
        start_date=body.start_date, start_shift=start_shift,
        end_date=body.end_date, end_shift=end_shift,
        exclude_program_id=body.exclude_program_id,
    )


@router.get("/suggestions", dependencies=[Depends(_require_viewer_or_above)])
async def api_suggestions(
    start_date: date = Query(...),
    start_shift: str = Query(...),
    end_date: date = Query(...),
    end_shift: str = Query(...),
    destination: Optional[str] = Query(None),
    limit: int = Query(10, ge=1, le=50),
):
    """Deterministic ranked suggestions for the proposed window. No LLM."""
    norm_start_shift = _parse_shift_query(start_shift)
    norm_end_shift = _parse_shift_query(end_shift)
    return await dispatch_svc.get_suggestions(
        start_date=start_date, start_shift=norm_start_shift,
        end_date=end_date, end_shift=norm_end_shift,
        destination=destination, limit=limit,
    )


@router.get("/overlaps", dependencies=[Depends(_require_viewer_or_above)])
async def api_overlaps(
    employee_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Read-only overlap-review facility (Phase 2A.2 sec.4/sec.9). Never
    mutates anything -- existing overlaps require manual business review,
    this endpoint only surfaces them using the same certified interval
    logic as availability/check-conflict/suggestions."""
    return await dispatch_svc.list_overlaps(
        employee_id=employee_id, status=status, page=page, page_size=page_size,
    )


async def _resolve_performed_by(key: str) -> str:
    """Human-readable actor label for the audit trail -- prefers the admin's
    username/name, falls back to phone, falls back to a generic label for
    the internal API key (which has no fazle_admins row)."""
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return "internal_api_key"
    from modules import rbac
    admin = await rbac.get_admin_by_api_key(key or "")
    if not admin:
        return "unknown"
    return admin.get("username") or admin.get("name") or admin.get("phone") or f"admin#{admin.get('id')}"


class AssignRequest(BaseModel):
    escort_employee_id: int
    expected_current_employee_id: Optional[int] = None


@router.post("/programs/{program_id}/assign", dependencies=[Depends(_require_operator_or_above)])
async def api_assign_program(
    program_id: int,
    body: AssignRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Controlled manual assignment/reassignment (Phase 2A.2). The ONLY
    write endpoint in this router. Transactional, conflict-checked inside
    the same transaction (never trusts a browser-side check), concurrency-
    safe via advisory locks. No auto-assignment, no override, no
    reservation -- the caller must supply escort_employee_id explicitly."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await dispatch_svc.assign_program(
            program_id=program_id,
            escort_employee_id=body.escort_employee_id,
            expected_current_employee_id=body.expected_current_employee_id,
            performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        detail = {"detail": e.message, "code": e.code}
        if e.conflicts is not None:
            detail["escort_employee_id"] = body.escort_employee_id
            detail["conflicts"] = e.conflicts
        raise HTTPException(status_code=e.http_status, detail=detail) from None
