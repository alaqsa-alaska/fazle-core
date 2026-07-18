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

import os
import pathlib
import time
import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
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


async def _require_admin_or_above(key: str = Depends(_API_KEY_HEADER)) -> str:
    """Phase 2A.3 Stage 5/6. Manual completion, release-slip approval, and
    controlled override are admin/superadmin-only -- mirrors
    modules.escort_roster.routes._resolve_actor's can_admin_direct role-set
    intersection (roles are DB data, {"admin","superadmin","owner"}), NOT
    the COMMAND_ROLE string-command mapping _require_operator_or_above
    reuses, since no existing command maps to this tier."""
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return key
    from modules import rbac
    admin = await rbac.get_admin_by_api_key(key or "")
    if not admin or admin.get("status") != "active":
        raise HTTPException(status_code=403, detail="admin role or above required")
    roles = await rbac.get_roles(int(admin["id"]))
    normalized_roles = {str(role or "").strip().lower() for role in roles}
    if normalized_roles.intersection({"admin", "superadmin", "owner"}):
        return key
    raise HTTPException(status_code=403, detail="admin role or above required")


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


class OverrideAssignRequest(BaseModel):
    escort_employee_id: int
    expected_current_employee_id: Optional[int] = None
    override_reason: str
    acknowledged_conflict_program_ids: list[int] = []


@router.post("/programs/{program_id}/assign-override", dependencies=[Depends(_require_admin_or_above)])
async def api_assign_program_override(
    program_id: int,
    body: OverrideAssignRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Controlled conflict override (Phase 2A.3 sec.11/28/30), admin/
    superadmin only -- a SEPARATE, more privileged route from /assign, never
    the same operator-reachable endpoint with an optional flag. Requires a
    mandatory override_reason and the caller to explicitly acknowledge the
    exact conflicting program_ids being overridden; if the conflict set
    re-checked under lock differs, the override is rejected as stale."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await dispatch_svc.assign_program(
            program_id=program_id,
            escort_employee_id=body.escort_employee_id,
            expected_current_employee_id=body.expected_current_employee_id,
            performed_by=performed_by,
            override=True,
            override_reason=body.override_reason,
            acknowledged_conflict_program_ids=body.acknowledged_conflict_program_ids,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        detail = {"detail": e.message, "code": e.code}
        if e.conflicts is not None:
            detail["escort_employee_id"] = body.escort_employee_id
            detail["conflicts"] = e.conflicts
        raise HTTPException(status_code=e.http_status, detail=detail) from None


class ReplaceEscortRequest(BaseModel):
    mother_vessel: str
    lighter_vessel: str
    escort_employee_id: Optional[int] = None
    escort_name: str
    escort_mobile: str
    start_date: date
    start_shift: str
    master_mobile: Optional[str] = None
    destination: Optional[str] = None


@router.post("/programs/{program_id}/replace-escort", dependencies=[Depends(_require_operator_or_above)])
async def api_replace_escort(
    program_id: int,
    body: ReplaceEscortRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Manual trigger for the same escort-replacement-segment workflow
    Stage 3's automatic admin-message pipeline uses (Phase 2A.3 sec.20) --
    for correcting/backfilling historical data through the UI rather than
    a new WhatsApp message. Same validation, same transaction, same
    seven-day boundary rule (Decision 6), same audit trail -- this is a
    thin route wrapper, not a second implementation."""
    performed_by = await _resolve_performed_by(key)
    start_shift = _parse_shift_query(body.start_shift)
    try:
        return await dispatch_svc.create_replacement_segment(
            earlier_program_id=program_id,
            mother_vessel=body.mother_vessel, lighter_vessel=body.lighter_vessel,
            escort_employee_id=body.escort_employee_id, escort_name=body.escort_name,
            escort_mobile=body.escort_mobile, start_date=body.start_date, start_shift=start_shift,
            master_mobile=body.master_mobile, destination=body.destination,
            performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


class UnassignRequest(BaseModel):
    expected_current_employee_id: int
    reason: str


@router.post("/programs/{program_id}/unassign", dependencies=[Depends(_require_operator_or_above)])
async def api_unassign_program(
    program_id: int,
    body: UnassignRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Safe unassignment (Phase 2A.3 sec.12/18) -- draft/confirmed
    programs only, blocked by any accepted attendance or existing payment
    draft. Never hard-deletes the program. Mandatory reason, required
    expected_current_employee_id (optimistic concurrency, same pattern as
    /assign)."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await dispatch_svc.unassign_program(
            program_id=program_id,
            expected_current_employee_id=body.expected_current_employee_id,
            reason=body.reason,
            performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 5 — Manual completion and release-slip review (admin+ only)
# ─────────────────────────────────────────────────────────────────────────────

class CompleteProgramRequest(BaseModel):
    end_date: date
    end_shift: str
    release_point: Optional[str] = None
    reason: str
    expected_status: Optional[str] = None


@router.post("/programs/{program_id}/complete", dependencies=[Depends(_require_admin_or_above)])
async def api_complete_program(
    program_id: int,
    body: CompleteProgramRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Manual completion (Phase 2A.3 sec.16/22), admin/superadmin only.
    Reuses modules.escort_lifecycle.close_program() directly -- the same
    idempotent path [RELEASE CONFIRMED] messages use. Never a second
    completion implementation."""
    performed_by = await _resolve_performed_by(key)
    end_shift = _parse_shift_query(body.end_shift)
    try:
        return await dispatch_svc.manually_complete_program(
            program_id=program_id,
            end_date=body.end_date, end_shift=end_shift,
            release_point=body.release_point, reason=body.reason,
            expected_status=body.expected_status, performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


@router.get("/incomplete-drafts", dependencies=[Depends(_require_viewer_or_above)])
async def api_incomplete_drafts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Read-only listing of TTL-eligible incomplete drafts (Phase 2A.3
    sec.19/24) -- the exact predicate expire_stale_drafts() (Stage 4) uses,
    surfaced here before cleanup happens. Never mutates anything."""
    return await dispatch_svc.list_incomplete_drafts(page=page, page_size=page_size)


@router.get("/release-slips/pending", dependencies=[Depends(_require_viewer_or_above)])
async def api_pending_release_slips(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Read-only queue of unreviewed OCR release-slip extractions
    (Phase 2A.3 sec.19.2/21). Never mutates anything."""
    return await dispatch_svc.list_pending_release_slips(page=page, page_size=page_size)


@router.get("/release-slips/{extraction_id}/candidates", dependencies=[Depends(_require_viewer_or_above)])
async def api_release_slip_candidates(
    extraction_id: int,
    top_n: int = Query(5, ge=1, le=20),
):
    """Weighted candidate-program matches for a given OCR extraction
    (Phase 2A.3 sec.13.2/21), reusing
    modules.escort_roster.extractor.find_slip_matches() verbatim. Never
    mutates anything -- the admin picks a candidate, then calls
    /release-slips/{id}/approve explicitly."""
    try:
        return await dispatch_svc.get_release_slip_candidates(extraction_id, top_n=top_n)
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


class ApproveReleaseSlipRequest(BaseModel):
    program_id: int
    end_shift: str
    release_point: Optional[str] = None


@router.post("/release-slips/{extraction_id}/approve", dependencies=[Depends(_require_admin_or_above)])
async def api_approve_release_slip(
    extraction_id: int,
    body: ApproveReleaseSlipRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Admin approval of a release-slip match (Phase 2A.3 sec.13.1/21),
    admin/superadmin only. Calls the same close_program() path manual
    completion uses -- never a parallel completion implementation. The
    admin must explicitly supply program_id (chosen from
    /candidates) and end_shift (never parsed/fabricated from OCR text)."""
    performed_by = await _resolve_performed_by(key)
    end_shift = _parse_shift_query(body.end_shift)
    try:
        return await dispatch_svc.approve_release_slip(
            extraction_id=extraction_id, program_id=body.program_id,
            end_shift=end_shift, release_point=body.release_point,
            performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


@router.post("/release-slips/upload", dependencies=[Depends(_require_operator_or_above)])
async def api_upload_release_slip(
    file: UploadFile = File(...),
    key: str = Depends(_API_KEY_HEADER),
):
    """Multipart upload entry point into the SAME OCR extraction pipeline
    the existing WhatsApp-image path uses (modules.escort_slip_extractor.
    extract_escort_slip -- a plain-file-path function, reused verbatim, not
    duplicated). This is a second entry point into one pipeline, not a
    second pipeline: it does not replace the existing POST /escort-slip/
    extract endpoint (app/main.py), it only gives the Dispatch UI its own
    upload affordance. OCR extraction never finalizes a release -- only
    /release-slips/{id}/approve or [RELEASE CONFIRMED] does."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename")

    safe_name = pathlib.Path(file.filename).name
    ext = os.path.splitext(safe_name)[1].lower()
    allowed = {".jpg", ".jpeg", ".png", ".pdf", ".webp"}
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext}. Allowed: {', '.join(allowed)}")

    content_bytes = await file.read()
    max_size = 10 * 1024 * 1024
    if len(content_bytes) > max_size:
        raise HTTPException(status_code=400, detail=f"File too large: {len(content_bytes)//1024}KB. Max: {max_size//1024}KB")
    if len(content_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")

    upload_dir = "/home/azim/core/resources/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    upload_id = f"dispatch_slip_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    temp_path = os.path.join(upload_dir, f"{upload_id}{ext}")
    with open(temp_path, "wb") as fh:
        fh.write(content_bytes)

    performed_by = await _resolve_performed_by(key)
    from modules.escort_slip_extractor import extract_escort_slip
    result = await extract_escort_slip(temp_path, source_label=f"dispatch_ui:{performed_by}", save_to_db=True)
    return {
        "extraction_id": result.get("extraction_id"),
        "document_type": result.get("document_type"),
        "confidence": result.get("confidence"),
        "mother_vessel": result.get("mother_vessel"),
        "lighter_vessel": result.get("lighter_vessel"),
        "escort_name": result.get("escort_name"),
        "escort_mobile": result.get("escort_mobile"),
        "missing_fields": result.get("missing_fields"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2A.3 Stage 6 — Conflict-pair review and same-vessel review queue
# ─────────────────────────────────────────────────────────────────────────────

class ConflictReviewRequest(BaseModel):
    program_id_a: int
    program_id_b: int
    decision: str
    note: str


@router.post("/overlaps/review", dependencies=[Depends(_require_admin_or_above)])
async def api_review_conflict_pair(
    body: ConflictReviewRequest,
    key: str = Depends(_API_KEY_HEADER),
):
    """Records a keep-both/defer decision on an existing conflicting pair
    (Phase 2A.3 sec.9/30, Decision 5), admin/superadmin only. Audit-only --
    never mutates wbom_escort_programs. 'Correct one' and 'unassign one'
    reuse /assign, /assign-override, and /unassign instead of this route."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await dispatch_svc.record_conflict_pair_review(
            program_id_a=body.program_id_a, program_id_b=body.program_id_b,
            decision=body.decision, note=body.note, performed_by=performed_by,
        )
    except dispatch_svc.DispatchAssignmentError as e:
        raise HTTPException(status_code=e.http_status, detail={"detail": e.message, "code": e.code}) from None


@router.get("/same-vessel-review", dependencies=[Depends(_require_viewer_or_above)])
async def api_same_vessel_review(include_terminal: bool = Query(False)):
    """Read-only same-vessel review queue (Phase 2A.3 sec.9.2/30):
    same_date_historical, under_seven_day, seven_plus_day, insufficient_data,
    and bulk_import_artifact candidates. Classification only -- no mutation
    is applied. Every listed candidate requires the applicable guarded
    workflow (replace-escort / manual review) before any real change."""
    return await dispatch_svc.find_same_vessel_review_candidates(include_terminal=include_terminal)
