"""
Fazle Core — Draft-Confirmation Invariant (Workstream 2) review-queue routes.

Stage 1: read-only visibility into wbom_unmatched_confirmations
(migration 052). Stage 2 wired the no-match admission behavior change in
handle_admin_escort_completion() (modules/escort/__init__.py). Stage 3
(this addition) adds the actual review actions -- link-existing,
confirm-new, reject, defer -- so a queued item can finally be resolved,
not just viewed.

RBAC: admin/superadmin only for every route here, including the list --
this queue holds raw confirmation text and phone numbers, the same
sensitivity tier as modules.dispatch.routes's Stage 5/6 admin-only actions,
whose _require_admin_or_above and _resolve_performed_by this file copies
verbatim (local copy to avoid a cross-module import cycle, same
justification as that module's own copy).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings
from app.database import fetch_all, fetch_one
from modules import escort as escort_svc

router = APIRouter(prefix="/api/escort", tags=["escort-unmatched-confirmations"])

_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)

_VALID_STATUSES = {"pending", "linked_existing", "confirmed_new", "rejected", "deferred"}


async def _require_admin_or_above(key: str = Depends(_API_KEY_HEADER)) -> str:
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


async def _resolve_performed_by(key: str) -> str:
    """Human-readable actor label for the audit trail -- prefers the admin's
    username/name, falls back to phone, falls back to a generic label for
    the internal API key. Verbatim copy of modules.dispatch.routes's own
    helper (same cross-module-import-cycle justification)."""
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return "internal_api_key"
    from modules import rbac
    admin = await rbac.get_admin_by_api_key(key or "")
    if not admin:
        return "unknown"
    return admin.get("username") or admin.get("name") or admin.get("phone") or f"admin#{admin.get('id')}"


def _mask_phone(phone: Optional[str]) -> Optional[str]:
    """Last 4 digits visible only -- same masking intent as the AI console's
    evidence-card redaction, applied here since this queue is admin-facing
    but still logged/rendered outside a direct DB session."""
    if not phone:
        return phone
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("parsed_confirmation", "candidate_matches"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    d["admin_phone_masked"] = _mask_phone(d.pop("admin_phone", None))
    d["client_phone_masked"] = _mask_phone(d.pop("client_phone", None))
    return d


@router.get("/unmatched-confirmations", dependencies=[Depends(_require_admin_or_above)])
async def list_unmatched_confirmations(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Paginated list, newest first. status=None returns every status;
    pass status=pending (etc.) to filter. Does not resolve or mutate
    anything -- Stage 1 is visibility only."""
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status!r}")

    offset = (page - 1) * page_size
    if status:
        rows = await fetch_all(
            """SELECT * FROM wbom_unmatched_confirmations
               WHERE status = $1
               ORDER BY created_at DESC
               LIMIT $2 OFFSET $3""",
            status, page_size, offset,
        )
        total = await fetch_one(
            "SELECT COUNT(*) AS n FROM wbom_unmatched_confirmations WHERE status = $1",
            status,
        )
    else:
        rows = await fetch_all(
            """SELECT * FROM wbom_unmatched_confirmations
               ORDER BY created_at DESC
               LIMIT $1 OFFSET $2""",
            page_size, offset,
        )
        total = await fetch_one("SELECT COUNT(*) AS n FROM wbom_unmatched_confirmations")

    return {
        "items": [_row_to_dict(r) for r in rows],
        "count": len(rows),
        "total": total["n"] if total else 0,
        "page": page,
        "page_size": page_size,
    }


@router.get("/unmatched-confirmations/{item_id}", dependencies=[Depends(_require_admin_or_above)])
async def get_unmatched_confirmation(item_id: int):
    row = await fetch_one(
        "SELECT * FROM wbom_unmatched_confirmations WHERE id = $1", item_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Unmatched confirmation not found")

    result = _row_to_dict(row)

    candidate_ids = [
        c.get("program_id") for c in (result.get("candidate_matches") or [])
        if isinstance(c, dict) and c.get("program_id")
    ]
    if candidate_ids:
        live_rows = await fetch_all(
            """SELECT program_id, status, mother_vessel, lighter_vessel,
                      master_mobile, program_date, escort_name, escort_mobile
               FROM wbom_escort_programs WHERE program_id = ANY($1)""",
            candidate_ids,
        )
        live_by_id = {r["program_id"]: dict(r) for r in live_rows}
        for c in result["candidate_matches"]:
            if isinstance(c, dict) and c.get("program_id") in live_by_id:
                c["current_live_state"] = live_by_id[c["program_id"]]
            elif isinstance(c, dict):
                c["current_live_state"] = None  # candidate program no longer exists

    return result


def _map_review_error(e: Exception) -> HTTPException:
    if isinstance(e, escort_svc.UnmatchedConfirmationNotFound):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, escort_svc.UnmatchedConfirmationConflict):
        return HTTPException(status_code=409, detail=str(e))
    if isinstance(e, ValueError):
        return HTTPException(status_code=400, detail=str(e))
    return HTTPException(status_code=500, detail="review action failed, check logs")


class LinkExistingRequest(BaseModel):
    target_program_id: int
    reason: str
    expected_queue_status: Optional[str] = None
    expected_target_status: Optional[str] = None


class ConfirmNewRequest(BaseModel):
    reason: str
    expected_queue_status: Optional[str] = None


class RejectRequest(BaseModel):
    reason: str


class DeferRequest(BaseModel):
    reason: Optional[str] = None


@router.post(
    "/unmatched-confirmations/{item_id}/link-existing",
    dependencies=[Depends(_require_admin_or_above)],
)
async def link_existing(item_id: int, body: LinkExistingRequest, key: str = Depends(_API_KEY_HEADER)):
    """Resolve a queued confirmation by linking it to an existing draft.
    Updates the SAME program row (never a second one) via the certified
    _update_program_confirmed()/_resolve_escort_employee_id() functions."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await escort_svc.link_unmatched_confirmation_to_existing_draft(
            item_id,
            body.target_program_id,
            performed_by=performed_by,
            reason=body.reason,
            expected_queue_status=body.expected_queue_status,
            expected_target_status=body.expected_target_status,
        )
    except Exception as e:
        raise _map_review_error(e) from None


@router.post(
    "/unmatched-confirmations/{item_id}/confirm-new",
    dependencies=[Depends(_require_admin_or_above)],
)
async def confirm_new(item_id: int, body: ConfirmNewRequest, key: str = Depends(_API_KEY_HEADER)):
    """Explicit admin confirmation that a queued item is genuinely a
    separate duty segment. Re-validates no confident match has since
    appeared before creating exactly one new program."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await escort_svc.confirm_unmatched_confirmation_as_new(
            item_id,
            performed_by=performed_by,
            reason=body.reason,
            expected_queue_status=body.expected_queue_status,
        )
    except Exception as e:
        raise _map_review_error(e) from None


@router.post(
    "/unmatched-confirmations/{item_id}/reject",
    dependencies=[Depends(_require_admin_or_above)],
)
async def reject(item_id: int, body: RejectRequest, key: str = Depends(_API_KEY_HEADER)):
    """Reject a queued confirmation. Never creates or updates a program."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await escort_svc.reject_unmatched_confirmation(
            item_id, performed_by=performed_by, reason=body.reason,
        )
    except Exception as e:
        raise _map_review_error(e) from None


@router.post(
    "/unmatched-confirmations/{item_id}/defer",
    dependencies=[Depends(_require_admin_or_above)],
)
async def defer(item_id: int, body: DeferRequest, key: str = Depends(_API_KEY_HEADER)):
    """Defer a queued confirmation for later review. Item stays reviewable
    (status='deferred' is treated identically to 'pending' by every other
    action's status check)."""
    performed_by = await _resolve_performed_by(key)
    try:
        return await escort_svc.defer_unmatched_confirmation(
            item_id, performed_by=performed_by, reason=body.reason,
        )
    except Exception as e:
        raise _map_review_error(e) from None
