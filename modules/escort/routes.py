"""
Fazle Core — Draft-Confirmation Invariant (Workstream 2) review-queue routes.

Stage 1 only: read-only visibility into wbom_unmatched_confirmations
(migration 052). The no-match admission behavior change in
handle_admin_escort_completion() (modules/escort/__init__.py) is Stage 2,
deliberately not wired yet -- this router exists first so the queue is
visible and verifiable before anything starts writing to it.

RBAC: admin/superadmin only for every route here, including the list --
this queue holds raw confirmation text and phone numbers, the same
sensitivity tier as modules.dispatch.routes's Stage 5/6 admin-only actions,
whose _require_admin_or_above this file copies verbatim (local copy to
avoid a cross-module import cycle, same justification as that module's own
copy).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader

from app.config import get_settings
from app.database import fetch_all, fetch_one

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
