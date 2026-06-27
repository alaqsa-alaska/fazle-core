"""
Draft Dashboard — API routes
GET  /api/drafts          → pending drafts list
GET  /api/drafts/stats    → status counts
POST /api/drafts/{id}/approve → approve draft
POST /api/drafts/{id}/edit    → edit draft body
POST /api/drafts/{id}/delete  → soft delete
POST /api/drafts/{id}/block   → block number + delete all drafts
"""
from __future__ import annotations
import json as _json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from app.database import fetch_all, fetch_one, execute
from app.config import get_settings as _get_settings

log = logging.getLogger("fazle.drafts")
router = APIRouter(tags=["drafts"])

# Local copy — avoids circular import (app.main → drafts → app.main)
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(key: str = Depends(_API_KEY_HEADER)) -> None:
    settings = _get_settings()
    if key and key == settings.internal_api_key:
        return
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin is not None and admin.get("status") == "active":
                return
        except Exception as e:
            import logging
            logging.getLogger("fazle.drafts.routes").warning(
                f"Auth error for key {key[:10]}...: {e}"
            )
    raise HTTPException(status_code=403, detail="Forbidden")


# ── Request models ──────────────────────────────────────────────────────────
class EditRequest(BaseModel):
    new_body: str
    admin_note: Optional[str] = None

class BlockRequest(BaseModel):
    reason: Optional[str] = "admin_block"


# ── GET /api/drafts/stats ───────────────────────────────────────────────────
@router.get("/api/drafts/stats")
async def draft_stats(_: None = Depends(_require_api_key)):
    """Status count summary."""
    rows = await fetch_all(
        """
        SELECT status, COUNT(*) as n,
               MAX(created_at) as newest
        FROM fazle_draft_replies
        GROUP BY status
        ORDER BY n DESC
        """
    )
    return {"stats": [dict(r) for r in rows]}


# ── GET /api/drafts ─────────────────────────────────────────────────────────
@router.get("/api/drafts")
async def list_drafts(
    _: None = Depends(_require_api_key),
    status: str = Query(
        "pending,pending_selfie,edited",
        description="comma-separated statuses",
    ),
    limit: int = Query(100, le=500),
    offset: int = Query(0, ge=0),
):
    """Pending drafts list — for admin review table."""
    statuses = [s.strip() for s in status.split(",")]
    n = len(statuses)
    placeholders = ", ".join(f"${i + 1}" for i in range(n))
    rows = await fetch_all(
        f"""
        SELECT
          d.id,
          d.recipient,
          d.reply_text,
          d.status,
          d.intent,
          d.draft_type,
          d.source,
          d.meta,
          d.created_at,
          d.reviewed,
          d.admin_phone,
          c.display_name AS contact_name
        FROM fazle_draft_replies d
        LEFT JOIN wbom_contacts c
          ON c.whatsapp_number = d.recipient
         AND c.is_active = true
        WHERE d.status = ANY(ARRAY[{placeholders}])
        ORDER BY d.created_at DESC
        LIMIT ${n + 1} OFFSET ${n + 2}
        """,
        *statuses, limit, offset,
    )
    result = []
    for r in rows:
        row = dict(r)
        meta = row.get("meta") or {}
        if isinstance(meta, str):
            try:
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        row["role_detected"] = (
            meta.get("role") or meta.get("identity_role") or "unknown"
        )
        result.append(row)
    return {"drafts": result, "count": len(result)}


# ── POST /api/drafts/{id}/approve ──────────────────────────────────────────
@router.post("/api/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, _: None = Depends(_require_api_key)):
    """Approve draft — mark approved, then enqueue for sending."""
    draft = await fetch_one(
        "SELECT id, status, recipient, reply_text, source FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft["status"] not in ("pending", "pending_selfie", "edited"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve draft with status: {draft['status']}",
        )
    await execute(
        """
        UPDATE fazle_draft_replies
        SET status      = 'approved',
            approved_at = NOW(),
            reviewed    = true
        WHERE id = $1
        """,
        draft_id,
    )
    log.info("Draft %d approved for %s", draft_id, draft["recipient"])

    # Enqueue for sending — admin approval overrides AUTO_REPLY_ENABLED
    source = draft.get("source") or "bridge1"
    source_bridge = "bridge1" if source in ("bridge1", "meta") else "bridge2"
    queued = False
    try:
        from modules.outbound import enqueue as _enqueue
        qid = await _enqueue(
            recipient=draft["recipient"],
            body=draft["reply_text"],
            source_bridge=source_bridge,
            purpose="admin_approve",
            idempotency_key=f"draft-approve-{draft_id}",
        )
        if qid:
            await execute(
                "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
                draft_id,
            )
            queued = True
            log.info("Draft %d queued (outbound id=%s) for %s", draft_id, qid, draft["recipient"])
        else:
            log.warning("Draft %d enqueue dedup'd — already queued", draft_id)
            queued = True
    except Exception as _err:
        log.error("Draft %d enqueue failed: %s", draft_id, _err)

    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "sent" if queued else "approved",
        "queued": queued,
        "recipient": draft["recipient"],
    }


# ── POST /api/drafts/{id}/edit ─────────────────────────────────────────────
@router.post("/api/drafts/{draft_id}/edit")
async def edit_draft(draft_id: int, req: EditRequest, _: None = Depends(_require_api_key)):
    """Edit draft reply_text — status becomes edited."""
    if not req.new_body or len(req.new_body.strip()) < 2:
        raise HTTPException(status_code=400, detail="new_body too short")
    draft = await fetch_one(
        "SELECT id, status, recipient FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    note_suffix = f" | admin_edit: {req.admin_note}" if req.admin_note else ""
    await execute(
        """
        UPDATE fazle_draft_replies
        SET reply_text = $1,
            status     = 'edited',
            edited_at  = NOW(),
            reviewed   = true,
            error_text = COALESCE(error_text, '') || $2
        WHERE id = $3
        """,
        req.new_body.strip(),
        note_suffix,
        draft_id,
    )
    log.info("Draft %d edited for %s", draft_id, draft["recipient"])
    return {"ok": True, "draft_id": draft_id, "status": "edited"}


# ── POST /api/drafts/{id}/delete ───────────────────────────────────────────
@router.post("/api/drafts/{draft_id}/delete")
async def delete_draft(draft_id: int, _: None = Depends(_require_api_key)):
    """Soft-delete draft."""
    draft = await fetch_one(
        "SELECT id, recipient FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await execute(
        """
        UPDATE fazle_draft_replies
        SET status   = 'deleted',
            reviewed = true
        WHERE id = $1
        """,
        draft_id,
    )
    log.info("Draft %d deleted for %s", draft_id, draft["recipient"])
    return {"ok": True, "draft_id": draft_id, "status": "deleted"}


# ── POST /api/drafts/{id}/block ────────────────────────────────────────────
@router.post("/api/drafts/{draft_id}/block")
async def block_number(draft_id: int, req: BlockRequest, _: None = Depends(_require_api_key)):
    """Block sender number + delete all pending drafts from that number."""
    draft = await fetch_one(
        "SELECT id, recipient FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    phone = draft["recipient"]
    note = f"Blocked via draft dashboard: {req.reason}"

    # ON CONFLICT (phone, platform) — correct unique constraint
    await execute(
        """
        INSERT INTO fazle_contact_roles
          (phone, platform, name, role, sub_role,
           priority, source, is_active, confidence, notes)
        VALUES ($1, 'whatsapp', 'Blocked', 'blocked', $2,
                999, 'admin_block', true, 100, $3)
        ON CONFLICT (phone, platform) DO UPDATE
          SET role       = 'blocked',
              sub_role   = $2,
              is_active  = true,
              source     = 'admin_block',
              notes      = $3,
              updated_at = NOW()
        """,
        phone,
        req.reason,
        note,
    )

    await execute(
        """
        UPDATE fazle_draft_replies
        SET status   = 'deleted',
            reviewed = true
        WHERE recipient = $1
          AND status IN ('pending', 'pending_selfie', 'edited', 'approved')
        """,
        phone,
    )

    log.info("Number %s blocked, drafts deleted (draft_id=%d)", phone, draft_id)
    return {
        "ok": True,
        "phone": phone,
        "status": "blocked",
        "reason": req.reason,
    }
