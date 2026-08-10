"""
Draft Dashboard — API routes
GET  /api/drafts          → pending drafts list
GET  /api/drafts/stats    → status counts
POST /api/drafts/{id}/approve → approve draft
POST /api/drafts/{id}/edit    → edit draft body
POST /api/drafts/{id}/delete  → soft delete

Note: the "blocked number" concept (POST /api/drafts/{id}/block) was
permanently removed by Owner decision, 2026-07-11 — see migration 042.
There is no scenario in which a phone number should be blocked at the
pipeline level; unknown senders simply trigger no business action.
"""
from __future__ import annotations
import json as _json
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from app.database import fetch_all, fetch_one, execute
from app.config import get_settings as _get_settings
from shared.auth_deps import API_KEY_HEADER as _API_KEY_HEADER, require_api_key as _require_api_key

log = logging.getLogger("fazle.drafts")
router = APIRouter(tags=["drafts"])


async def _resolve_reviewer_identity(key: str) -> str:
    """Best-effort reviewer identity for audit fields (approved_by, etc.) --
    matches _require_api_key's own auth check so it never resolves an
    identity for a key that wouldn't already have passed authorization."""
    settings = _get_settings()
    if key and key == settings.internal_api_key:
        return "owner-internal-key"
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin:
                return admin.get("phone") or admin.get("name") or f"admin-{admin.get('id')}"
        except Exception:
            pass
    return "unknown-frontend-admin"


# ── Request models ──────────────────────────────────────────────────────────
class EditRequest(BaseModel):
    new_body: str
    admin_note: Optional[str] = None


class CreateDraftRequest(BaseModel):
    recipient: str
    bridge: str
    draft_text: str
    role: str = "unknown"
    intent: str = "hermes_suggested"
    context: str = ""


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


# ── POST /api/drafts (create) ────────────────────────────────────────────────
@router.post("/api/drafts")
async def create_draft(
    body: CreateDraftRequest,
    _: None = Depends(_require_api_key),
):
    """Create a new pending draft reply — external-caller entry point
    (2026-08-10, Hermes Capability Expansion Level 1: fazle-mcp's
    draft_whatsapp_reply tool is the first caller). Thin wrapper around
    shared.draft_reply.create_draft_reply(), the same race-safe/deduped/
    audited insert path every internal module already uses — no new
    draft-storage logic here. This never sends anything; a draft only
    leaves 'pending' via the approve/edit endpoints above, exactly like
    every other draft in the system, so this endpoint cannot itself cause
    an outbound message."""
    from shared.draft_reply import create_draft_reply

    if not body.recipient.strip() or not body.draft_text.strip():
        raise HTTPException(status_code=400, detail="recipient and draft_text are required")

    draft_id = await create_draft_reply(
        sender=body.recipient,
        bridge=body.bridge,
        draft_text=body.draft_text,
        role=body.role,
        intent=body.intent,
        context=body.context,
        source_module="hermes_admin",
    )
    if draft_id is None:
        return {
            "ok": False,
            "reason": "suppressed — either a pending draft already exists for "
            "this recipient+bridge within the last 5 minutes, or a transient "
            "lock/DB issue. Not an error to retry blindly; check GET /api/drafts first.",
        }
    return {"ok": True, "draft_id": draft_id, "status": "pending"}


# ── POST /api/drafts/{id}/approve ──────────────────────────────────────────
@router.post("/api/drafts/{draft_id}/approve")
async def approve_draft(
    draft_id: int,
    _: None = Depends(_require_api_key),
    key: str = Depends(_API_KEY_HEADER),
):
    """Approve draft — mark approved, then enqueue for sending.

    Attendance-intent drafts (Owner decision, 2026-07-11) are handled by the
    shared modules.attendance.finalize_attendance_draft() service instead --
    the same function modules.admin_commands._cmd_approve uses for the
    WhatsApp APPROVE <id> channel, so the two approval paths cannot drift.
    Every other intent keeps this endpoint's original behavior unchanged.
    """
    draft = await fetch_one(
        "SELECT id, status, recipient, reply_text, source, intent FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    if draft["intent"] == "attendance":
        from modules.attendance import finalize_attendance_draft
        actor = await _resolve_reviewer_identity(key)
        result = await finalize_attendance_draft(draft_id, channel="frontend_review_queue", actor=actor)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "attendance finalize failed")
        return {
            "ok": True,
            "draft_id": draft_id,
            "status": "approved",
            "already_approved": result.get("already_approved", False),
            "employee_id": result.get("employee_id"),
        }

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
    """Edit draft reply_text — status becomes edited, then send to recipient.

    2026-07-27: Now completes the full edit flow — updates the draft text AND
    enqueues the edited text for sending to the original recipient via the
    SAME inbound bridge (stored in fazle_draft_replies.source), mirroring the
    approve endpoint's delivery mechanism.
    """
    if not req.new_body or len(req.new_body.strip()) < 2:
        raise HTTPException(status_code=400, detail="new_body too short")
    draft = await fetch_one(
        "SELECT id, status, recipient, source FROM fazle_draft_replies WHERE id = $1",
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

    # Enqueue the edited text for sending via the SAME bridge
    source = draft.get("source") or "bridge1"
    source_bridge = "bridge1" if source in ("bridge1", "meta") else "bridge2"
    queued = False
    try:
        from modules.outbound import enqueue as _enqueue
        qid = await _enqueue(
            recipient=draft["recipient"],
            body=req.new_body.strip(),
            source_bridge=source_bridge,
            purpose="admin_edit",
            idempotency_key=f"draft-edit-{draft_id}",
        )
        if qid:
            await execute(
                "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
                draft_id,
            )
            queued = True
            log.info("Edited draft %d queued (outbound id=%s) for %s", draft_id, qid, draft["recipient"])
        else:
            log.warning("Edited draft %d enqueue dedup'd — already queued", draft_id)
            queued = True
    except Exception as _err:
        log.error("Edited draft %d enqueue failed: %s", draft_id, _err)

    return {
        "ok": True,
        "draft_id": draft_id,
        "status": "sent" if queued else "edited",
        "queued": queued,
        "recipient": draft["recipient"],
    }


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
