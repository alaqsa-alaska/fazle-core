"""
Escort Roster — FastAPI Router

All routes under /api/escort-roster/
Auth: X-Internal-Key header (same as rest of Fazle Core)

Endpoints:
  GET    /api/escort-roster/summary          — aggregate stats
  GET    /api/escort-roster                  — paginated list
  GET    /api/escort-roster/active           — active programs only
  GET    /api/escort-roster/export           — CSV download
  GET    /api/escort-roster/{id}             — detail with timeline
  PATCH  /api/escort-roster/{id}             — inline edit
  POST   /api/escort-roster/{id}/recalculate — recompute pay
  POST   /api/escort-roster/{id}/sync        — sync from source program
  POST   /api/escort-roster/sync-all         — bulk sync all programs
  POST   /api/escort-roster/match-slip       — OCR → program match
  GET    /api/escort-roster/config           — conveyance config
  POST   /api/escort-roster/config           — upsert conveyance config

Registered in app/main.py:
  from modules.escort_roster.routes import router as escort_roster_router
  app.include_router(escort_roster_router)
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from app.config import get_settings, update_repo_env_value
from app.database import fetch_all
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone, to_880

from .db import (
    audit_exact_confirmed_duplicates,
    cleanup_exact_confirmed_duplicates,
    confirm_slip_match,
    create_roster_change_request,
    create_roster_entry,
    decide_roster_change_request,
    delete_program_any_status,
    get_conveyance_config,
    get_roster_change_request,
    get_roster_change_requests,
    get_roster_detail,
    get_roster_list,
    get_roster_summary,
    get_slip_queue,
    recalculate_entry,
    reject_slip_match,
    sync_all_programs,
    sync_program_to_roster,
    update_roster_entry,
    upsert_conveyance_config,
)
from .extractor import find_slip_matches

log = logging.getLogger("fazle.escort_roster.routes")
settings = get_settings()

router = APIRouter(prefix="/api/escort-roster", tags=["escort-roster"])

# ── Auth ──────────────────────────────────────────────────────────────────────
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_key(key: str = Depends(_API_KEY_HEADER)):
    if key and key == settings.internal_api_key:
        return key
    try:
        from modules import rbac
        admin = await rbac.get_admin_by_api_key(key or "")
        if admin is not None and admin.get("status") == "active":
            return key
    except Exception as e:
        log.warning(f"Auth error for key {(key or '')[:10]}...: {e}")
    raise HTTPException(status_code=403, detail="Unauthorized")


async def _resolve_actor(key: str = Depends(_API_KEY_HEADER)) -> dict:
    """Resolve authenticated actor context for role-aware escort-roster mutations."""
    await _require_key(key)
    if key == settings.internal_api_key:
        return {
            "api_key": key,
            "admin_id": None,
            "name": "owner",
            "phone": "",
            "roles": ["owner", "superadmin"],
            "is_internal": True,
            "can_operator_mutate": True,
            "can_admin_direct": True,
        }

    from modules import rbac

    admin = await rbac.get_admin_by_api_key(key or "")
    if not admin or admin.get("status") != "active":
        raise HTTPException(status_code=403, detail="Unauthorized")

    roles = await rbac.get_roles(int(admin["id"]))
    normalized_roles = {str(role or "").strip().lower() for role in roles}
    can_edit = await rbac.check_permission(api_key=key, command="edit")
    can_admin_direct = bool(normalized_roles.intersection({"admin", "superadmin", "owner"}))

    return {
        "api_key": key,
        "admin_id": int(admin["id"]),
        "name": admin.get("name") or admin.get("username") or "admin",
        "phone": admin.get("phone") or "",
        "roles": list(roles),
        "is_internal": False,
        "can_operator_mutate": bool(can_edit.get("allowed")),
        "can_admin_direct": can_admin_direct,
    }


# ── Pydantic models ───────────────────────────────────────────────────────────

class PatchRosterEntry(BaseModel):
    notes: Optional[str] = None
    release_point: Optional[str] = None
    roster_status: Optional[str] = None
    conveyance: Optional[float] = None
    end_date: Optional[date] = None
    end_shift: Optional[str] = Field(None, pattern="^[DNdn]$")
    start_date: Optional[date] = None
    start_shift: Optional[str] = Field(None, pattern="^[DNdn]$")
    mother_vessel: Optional[str] = None
    lighter_vessel: Optional[str] = None
    master_mobile: Optional[str] = None
    escort_name: Optional[str] = None
    escort_mobile: Optional[str] = None


class CreateRosterEntry(BaseModel):
    mother_vessel: str
    lighter_vessel: Optional[str] = None
    master_mobile: Optional[str] = None
    escort_name: Optional[str] = None
    escort_mobile: Optional[str] = None
    destination: Optional[str] = None
    start_date: Optional[date] = None
    start_shift: Optional[str] = Field(None, pattern="^[DNdn]$")
    end_date: Optional[date] = None
    end_shift: Optional[str] = Field(None, pattern="^[DNdn]$")
    release_point: Optional[str] = None
    conveyance: Optional[float] = None
    notes: Optional[str] = None
    roster_status: Optional[str] = Field(default="draft")


class SlipMatchRequest(BaseModel):
    extracted_data: dict
    top_n: int = Field(default=5, ge=1, le=20)


class ConveyanceConfigRequest(BaseModel):
    destination: str
    conveyance_amount: float
    shift_rate: float = 200.0


class EscortClientRequest(BaseModel):
    phone: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/summary", dependencies=[Depends(_require_key)])
async def api_roster_summary():
    """Aggregate stats: counts by status, total amounts, pending reviews."""
    return await get_roster_summary()


@router.get("/active", dependencies=[Depends(_require_key)])
async def api_roster_active(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
):
    """Active programs only (status=active)."""
    return await get_roster_list(
        page=page,
        page_size=page_size,
        search=search,
        status="active",
        sort_by="start_date",
        sort_dir="desc",
    )


@router.get("/export", dependencies=[Depends(_require_key)])
async def api_roster_export(
    search: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_from: Optional[str] = Query(None),
    startFrom: Optional[str] = Query(None),
    start_to: Optional[str] = Query(None),
    startTo: Optional[str] = Query(None),
):
    """Export roster as CSV download."""
    effective_search = search if search is not None else q
    effective_start_from = start_from if start_from is not None else startFrom
    effective_start_to = start_to if start_to is not None else startTo

    result = await get_roster_list(
        page=1,
        page_size=10000,
        search=effective_search,
        status=status,
        start_from=effective_start_from,
        start_to=effective_start_to,
        sort_by="start_date",
        sort_dir="desc",
    )
    items = result["items"]

    columns = [
        "program_id", "mother_vessel", "lighter_vessel", "master_mobile",
        "escort_name", "escort_mobile", "destination", "start_date", "start_shift",
        "end_date", "end_shift", "total_shifts", "total_days", "salary",
        "conveyance", "total", "release_point", "roster_status",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow(item)

    output.seek(0)
    filename = "escort_roster.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/config", dependencies=[Depends(_require_key)])
async def api_get_config():
    """Get all conveyance/rate configuration."""
    return await get_conveyance_config()


@router.post("/config", dependencies=[Depends(_require_key)])
async def api_upsert_config(body: ConveyanceConfigRequest):
    """Create or update a conveyance/rate config entry."""
    return await upsert_conveyance_config(
        destination=body.destination,
        conveyance_amount=body.conveyance_amount,
        shift_rate=body.shift_rate,
    )


def _escort_client_phone_list() -> list[str]:
    current = get_settings().escort_client_phones or ""
    phones: list[str] = []
    for item in current.split(","):
        item = item.strip()
        if not item:
            continue
        norm = to_880(normalize_bd_phone(item))
        if not norm:
            continue
        if norm not in phones:
            phones.append(norm)
    return phones


@router.get("/escort-clients", dependencies=[Depends(_require_key)])
async def api_get_escort_clients():
    return {"phones": _escort_client_phone_list(), "count": len(_escort_client_phone_list())}


@router.post("/escort-clients", dependencies=[Depends(_require_key)])
async def api_add_escort_client(body: EscortClientRequest):
    phone = to_880(normalize_bd_phone(body.phone))
    if not phone:
        raise HTTPException(status_code=400, detail="Invalid Bangladesh mobile number")
    phones = _escort_client_phone_list()
    if phone not in phones:
        phones.append(phone)
        update_repo_env_value("ESCORT_CLIENT_PHONES", ",".join(phones))
    return {"ok": True, "phones": phones}


@router.delete("/escort-clients/{phone}", dependencies=[Depends(_require_key)])
async def api_remove_escort_client(phone: str):
    norm = to_880(normalize_bd_phone(phone))
    if not norm:
        raise HTTPException(status_code=400, detail="Invalid Bangladesh mobile number")
    phones = [p for p in _escort_client_phone_list() if p != norm]
    update_repo_env_value("ESCORT_CLIENT_PHONES", ",".join(phones))
    return {"ok": True, "phones": phones}


@router.get("/sync-all", dependencies=[Depends(_require_key)])
async def api_sync_all_get():
    """Status check for sync endpoint (returns counts without running sync)."""
    from app.database import fetch_val
    total_programs = await fetch_val("SELECT COUNT(*) FROM wbom_escort_programs") or 0
    total_entries = await fetch_val("SELECT COUNT(*) FROM escort_roster_entries") or 0
    return {
        "programs": int(total_programs),
        "roster_entries": int(total_entries),
        "unsynced": int(total_programs) - int(total_entries),
    }


@router.post("/sync-all", dependencies=[Depends(_require_key)])
async def api_sync_all():
    """Bulk sync ALL wbom_escort_programs into roster entries. Idempotent."""
    result = await sync_all_programs(actor="api")
    return result


@router.post("/match-slip", dependencies=[Depends(_require_key)])
async def api_match_slip(body: SlipMatchRequest):
    """
    Given OCR extraction data, find the best-matching active escort programs.
    Returns top N matches sorted by confidence.
    """
    matches = await find_slip_matches(body.extracted_data, top_n=body.top_n)
    return {"matches": matches, "count": len(matches)}


@router.get("", dependencies=[Depends(_require_key)])
async def api_roster_list(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    pageSize: Optional[int] = Query(None, ge=1, le=200),
    search: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    start_from: Optional[str] = Query(None),
    startFrom: Optional[str] = Query(None),
    start_to: Optional[str] = Query(None),
    startTo: Optional[str] = Query(None),
    sort_by: str = Query("start_date"),
    sortBy: Optional[str] = Query(None),
    sort_dir: str = Query("desc"),
    sortDir: Optional[str] = Query(None),
):
    """Paginated, searchable, filterable roster list."""
    effective_page_size = pageSize if pageSize is not None else page_size
    effective_search = search if search is not None else q
    effective_start_from = start_from if start_from is not None else startFrom
    effective_start_to = start_to if start_to is not None else startTo
    effective_sort_by = sortBy if sortBy is not None else sort_by
    effective_sort_dir = sortDir if sortDir is not None else sort_dir

    return await get_roster_list(
        page=page,
        page_size=effective_page_size,
        search=effective_search,
        status=status,
        start_from=effective_start_from,
        start_to=effective_start_to,
        sort_by=effective_sort_by,
        sort_dir=effective_sort_dir,
    )


# ─────────────────────────────────────────────────────────────────────────────
# History sync endpoints  (must be BEFORE /{program_id} to avoid int parse)
# ─────────────────────────────────────────────────────────────────────────────

class SyncHistoryRequest(BaseModel):
    filepath: Optional[str] = None
    sender_phone: str = "01670535255"
    dry_run: bool = False


@router.post("/sync-history", dependencies=[Depends(_require_key)])
async def api_sync_history(body: SyncHistoryRequest):
    from modules.escort_roster.history_sync import sync_conversation_history
    filepath = body.filepath or "/home/azim/wa_conversation_01880446111_01670535255.txt"
    try:
        result = await sync_conversation_history(
            filepath=filepath,
            sender_phone=body.sender_phone,
            actor="api_history_sync",
            dry_run=body.dry_run,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {e}")
    return result


@router.post("/rebuild-history", dependencies=[Depends(_require_key)])
async def api_rebuild_history():
    from modules.escort_roster.history_sync import rebuild_roster_from_history
    try:
        result = await rebuild_roster_from_history(actor="api_rebuild")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rebuild failed: {e}")
    return result


@router.get("/drafts", dependencies=[Depends(_require_key)])
async def api_get_drafts(page: int = 1, page_size: int = 50):
    """List current draft entries (paginated)."""
    from modules.escort_roster.db import get_draft_entries
    return await get_draft_entries(page=page, page_size=page_size)


@router.post("/cleanup-drafts", dependencies=[Depends(_require_key)])
async def api_cleanup_drafts():
    """Delete ALL draft entries and log each to audit trail."""
    from modules.escort_roster.db import cleanup_draft_entries
    return await cleanup_draft_entries(actor="api")


@router.post("/cleanup-empty-drafts", dependencies=[Depends(_require_key)])
async def api_cleanup_empty_drafts(min_age_hours: int = Query(default=1, ge=0)):
    """
    Delete draft programs with no meaningful data (empty vessel names + escort).
    These are junk rows from failed OCR / partial extractions.
    min_age_hours: only delete rows older than this (default 1 hour).
    """
    from modules.escort_roster.db import cleanup_empty_drafts
    return await cleanup_empty_drafts(min_age_hours=min_age_hours, actor="api")


@router.post("/cleanup-junk-drafts", dependencies=[Depends(_require_key)])
async def api_cleanup_junk_drafts():
    """
    Delete draft programs whose lighter_vessel contains junk extracted content:
    numbered list lines, raw CAPACITY/PHONE field values, contact list headers, etc.
    """
    from modules.escort_roster.db import cleanup_junk_drafts
    return await cleanup_junk_drafts(actor="api")


@router.get("/audit-exact-duplicates", dependencies=[Depends(_require_key)])
async def api_audit_exact_duplicates(days: int = Query(default=30, ge=1, le=3650)):
    """
    Audit exact duplicate confirmed rows by key:
    duty_date + mother_vessel + lighter_vessel + escort_name.
    """
    groups = await audit_exact_confirmed_duplicates(days=days)
    return {
        "days": days,
        "groups": len(groups),
        "items": groups,
    }


@router.post("/cleanup-exact-duplicates", dependencies=[Depends(_require_key)])
async def api_cleanup_exact_duplicates(days: int = Query(default=30, ge=1, le=3650)):
    """
    Cleanup exact duplicate confirmed rows (keep earliest program_id per key).
    """
    return await cleanup_exact_confirmed_duplicates(days=days, actor="api")


@router.post("/reconcile", dependencies=[Depends(_require_key)])
async def api_reconcile_drafts():
    """
    Bulk reconciliation: for every confirmed/assigned/running program that has
    a lighter_vessel set, delete any orphaned draft rows with the same
    lighter vessel (fuzzy match). Run after a backfill or manual data import.
    """
    from modules.escort_roster.db import reconcile_drafts_for_confirmation
    from app.database import fetch_all as _fa

    confirmed_rows = await _fa(
        """
        SELECT lighter_vessel, mother_vessel
        FROM wbom_escort_programs
        WHERE status IN ('confirmed', 'running', 'completed')
          AND lighter_vessel IS NOT NULL AND TRIM(lighter_vessel) != ''
        ORDER BY program_id DESC
        """
    )

    total_reconciled = 0
    for row in confirmed_rows:
        result = await reconcile_drafts_for_confirmation(
            lighter_vessel=row["lighter_vessel"],
            mother_vessel=row["mother_vessel"],
            actor="bulk_reconcile",
        )
        total_reconciled += result.get("reconciled", 0)

    return {
        "confirmed_programs_scanned": len(confirmed_rows),
        "drafts_reconciled": total_reconciled,
    }


@router.post("/backfill-sqlite", dependencies=[Depends(_require_key)])
async def api_backfill_sqlite(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    dry_run: bool = Query(default=False),
):
    """
    Backfill historical escort assignments from bridge2 SQLite database.
    Reads outbound admin messages sent to ESCORT_CLIENT_PHONES.
    date_from / date_to: optional inclusive filter (YYYY-MM-DD).
    dry_run=true: parse and count only, no DB writes.
    """
    from modules.escort_roster.history_sync import sync_from_bridge_sqlite
    from app.config import get_settings as _gs
    import os as _os

    _settings = _gs()
    client_phones = [p.strip() for p in _settings.escort_client_phones.split(",") if p.strip()]
    if not client_phones:
        raise HTTPException(status_code=400, detail="ESCORT_CLIENT_PHONES not configured")

    bridge2_db = "/home/azim/whatsapp2/store/messages.db"
    if not _os.path.exists(bridge2_db):
        raise HTTPException(status_code=404, detail=f"Bridge2 SQLite not found: {bridge2_db}")

    result = await sync_from_bridge_sqlite(
        db_path=bridge2_db,
        bridge_number=_settings.bridge2_number,
        client_phones=client_phones,
        date_from=date_from,
        date_to=date_to,
        actor="api_backfill",
        dry_run=dry_run,
    )
    return result


@router.post("/backfill-files", dependencies=[Depends(_require_key)])
async def api_backfill_files(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    dry_run: bool = Query(default=False),
):
    """
    Backfill from the WA conversation export text files in /home/azim/.
    Processes all wa_conversation_01880446111_*.txt files found.
    """
    from modules.escort_roster.history_sync import sync_conversation_history
    import glob as _glob

    files = _glob.glob("/home/azim/wa_conversation_01880446111_*.txt")
    if not files:
        raise HTTPException(status_code=404, detail="No conversation export files found")

    combined: dict = {
        "files_processed": 0,
        "messages_parsed": 0,
        "orders_found": 0,
        "groups_created": 0,
        "lighters_created": 0,
        "matched": 0,
        "unmatched": 0,
        "errors": [],
    }

    for filepath in sorted(files):
        # Extract sender_phone from filename: wa_conversation_01880446111_01670535255.txt
        import re as _re
        m = _re.search(r"wa_conversation_\d+_(\d+)\.txt$", filepath)
        sender_phone = m.group(1) if m else "unknown"
        try:
            result = await sync_conversation_history(
                filepath=filepath,
                sender_phone=sender_phone,
                actor="api_backfill_files",
                dry_run=dry_run,
                date_from=date_from,
                date_to=date_to,
            )
            combined["files_processed"] += 1
            for k in ("messages_parsed", "orders_found", "groups_created",
                      "lighters_created", "matched", "unmatched"):
                combined[k] = combined.get(k, 0) + result.get(k, 0)
            combined["errors"].extend(result.get("errors", []))
        except Exception as e:
            combined["errors"].append(f"{filepath}: {e}")
            log.error(f"[backfill_files] error for {filepath}: {e}")

    return combined


@router.post("", dependencies=[Depends(_require_key)])
async def api_roster_create(body: CreateRosterEntry):
    """Manually create a new roster entry."""
    # Task 7: validate and normalise phone numbers
    data = body.model_dump(exclude_none=True)
    for field in ("master_mobile", "escort_mobile"):
        raw = data.get(field)
        if raw:
            norm = normalize_bd_phone(raw)
            if not norm:
                raise HTTPException(400, detail=f"Invalid Bangladesh mobile number for '{field}': {raw!r}")
            data[field] = norm
    try:
        row = await create_roster_entry(data, actor="api")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Ensure escort's FPE employee record exists (non-fatal)
    escort_mobile = data.get("escort_mobile")
    escort_name = data.get("escort_name")
    if escort_mobile:
        try:
            from modules.escort import _resolve_escort_employee_id
            await _resolve_escort_employee_id(escort_mobile, escort_name, auto_create=True)
        except Exception as _fpe_err:
            log.debug("[roster_create] FPE seed non-fatal: %s", _fpe_err)
    return row


# ── Slip Queue (Task 5) — must be BEFORE /{program_id} to avoid int parse ──

@router.get("/slip-queue", dependencies=[Depends(_require_key)])
async def api_slip_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
):
    """Return pending OCR slip extractions with their best program match candidate."""
    return await get_slip_queue(page=page, page_size=page_size)


@router.post("/slip-queue/{match_id}/confirm", dependencies=[Depends(_require_key)])
async def api_confirm_slip_match(match_id: int):
    """Admin confirms an OCR slip match (sets admin_action='keep')."""
    ok = await confirm_slip_match(match_id, admin_phone="api")
    if not ok:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found or already actioned")
    return {"ok": True, "match_id": match_id, "action": "keep"}


@router.post("/slip-queue/{match_id}/reject", dependencies=[Depends(_require_key)])
async def api_reject_slip_match(match_id: int):
    """Admin rejects an OCR slip match (sets admin_action='reject')."""
    ok = await reject_slip_match(match_id, admin_phone="api")
    if not ok:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found or already actioned")
    return {"ok": True, "match_id": match_id, "action": "reject"}


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/{program_id}", dependencies=[Depends(_require_key)])
async def api_roster_detail(program_id: int):
    """Full detail: roster entry + slip matches + audit log + shift logs."""
    detail = await get_roster_detail(program_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not in roster")
    return detail


@router.patch("/{program_id}", dependencies=[Depends(_require_key)])
async def api_roster_patch(program_id: int, body: PatchRosterEntry, actor: dict = Depends(_resolve_actor)):
    """Inline edit of a roster entry. Only provided fields are updated."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Task 7: validate phone numbers in patch
    for field in ("master_mobile", "escort_mobile"):
        raw = updates.get(field)
        if raw:
            norm = normalize_bd_phone(raw)
            if not norm:
                raise HTTPException(400, detail=f"Invalid Bangladesh mobile number for '{field}': {raw!r}")
            updates[field] = norm
    actor_name = actor.get("name") or actor.get("phone") or "api"

    if actor.get("can_operator_mutate") and not actor.get("can_admin_direct"):
        req = await create_roster_change_request(
            program_id=program_id,
            action="edit",
            payload={"updates": updates},
            requested_by=actor_name,
            requested_by_phone=actor.get("phone") or None,
        )
        return {
            "status": "pending_approval",
            "message": "Edit submitted for admin approval.",
            "request": req,
        }

    if not actor.get("can_admin_direct"):
        raise HTTPException(status_code=403, detail="Not allowed to edit roster directly")

    try:
        row = await update_roster_entry(program_id, updates, actor=actor_name)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=409, detail=msg)
    if not row:
        raise HTTPException(status_code=404, detail="Program not found")
    return dict(row)


@router.post("/{program_id}/recalculate", dependencies=[Depends(_require_key)])
async def api_roster_recalculate(program_id: int):
    """Recompute pay fields (shifts, salary, total) from current dates."""
    try:
        row = await recalculate_entry(program_id, actor="api")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return row


@router.post("/{program_id}/sync", dependencies=[Depends(_require_key)])
async def api_roster_sync_one(program_id: int):
    """Re-sync a single program from wbom_escort_programs."""
    try:
        row = await sync_program_to_roster(program_id, actor="api")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return row


@router.delete("/{program_id}", dependencies=[Depends(_require_key)])
async def api_delete_program(program_id: int, actor: dict = Depends(_resolve_actor)):
    """Delete behavior is role-based: operator submits approval request, admin/superadmin can delete directly."""
    actor_name = actor.get("name") or actor.get("phone") or "api"

    if actor.get("can_operator_mutate") and not actor.get("can_admin_direct"):
        req = await create_roster_change_request(
            program_id=program_id,
            action="delete",
            payload={},
            requested_by=actor_name,
            requested_by_phone=actor.get("phone") or None,
        )
        return {
            "status": "pending_approval",
            "message": "Delete submitted for admin approval.",
            "request": req,
        }

    if not actor.get("can_admin_direct"):
        raise HTTPException(status_code=403, detail="Not allowed to delete roster entries")

    result = await delete_program_any_status(program_id, actor=actor_name)
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return result


@router.get("/change-requests", dependencies=[Depends(_require_key)])
async def api_roster_change_requests(
    status: Optional[str] = Query(default="pending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """List roster change requests for review workflows."""
    return await get_roster_change_requests(status=status, page=page, page_size=page_size)


@router.post("/change-requests/{request_id}/approve", dependencies=[Depends(_require_key)])
async def api_approve_roster_change_request(
    request_id: int,
    actor: dict = Depends(_resolve_actor),
):
    if not actor.get("can_admin_direct"):
        raise HTTPException(status_code=403, detail="Only admin/superadmin can approve roster requests")

    req = await get_roster_change_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found")

    decision = await decide_roster_change_request(
        request_id=request_id,
        decision="approved",
        reviewed_by=actor.get("name") or actor.get("phone") or "admin",
        review_note=None,
    )
    return decision


@router.post("/change-requests/{request_id}/reject", dependencies=[Depends(_require_key)])
async def api_reject_roster_change_request(
    request_id: int,
    note: Optional[str] = Query(default=None),
    actor: dict = Depends(_resolve_actor),
):
    if not actor.get("can_admin_direct"):
        raise HTTPException(status_code=403, detail="Only admin/superadmin can reject roster requests")

    req = await get_roster_change_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found")

    decision = await decide_roster_change_request(
        request_id=request_id,
        decision="rejected",
        reviewed_by=actor.get("name") or actor.get("phone") or "admin",
        review_note=note,
    )
    return decision
