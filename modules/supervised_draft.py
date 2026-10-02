"""Stage G5/G6/G7 (2026-08-28) -- Client / Office-Internal supervised
draft lane: actively notify the Super Admin about a pending draft.

Discovery, not invention: draft creation, storage, and the entire
APPROVE/EDIT/REJECT command handling already exist and already satisfy
Stage G8-G10 as-is (modules.admin_commands._cmd_approve/_cmd_edit/
_cmd_reject, traced this stage -- idempotent, delivery-recording,
audit-preserving). modules.bridge_poller._build_admin_review_draft()
already produces the 5-part card format G7 asks for (extended this
stage with an optional category line). shared.draft_reply.
create_draft_reply() / the existing POST /api/drafts route / fazle-mcp's
existing draft_whatsapp_reply() tool already create the draft row itself.

What was genuinely missing, confirmed by reading both code paths:
neither create_draft_reply() nor the /api/drafts route ever actively
PUSHES the review card to the admin as an outbound WhatsApp message --
_save_draft()'s own enriched-card path (bridge_poller's internal, legacy
auto-reply-suppression flow) doesn't either. This module is the one new
piece: given a draft_id, build the (now category-aware) admin review
card and enqueue it as a real outbound message -- reusing outbound.enqueue()
(the same idempotent send path every other alert/digest job already
uses), not a new delivery mechanism.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("fazle.supervised_draft")


async def notify_admin_of_pending_draft(draft_id: int, category: str = "") -> dict:
    """Push the (category-aware) admin review card for an existing pending
    draft. Does NOT create the draft (call create_draft_reply()/
    draft_whatsapp_reply() first) and does NOT approve/send anything --
    this only notifies; the existing APPROVE/EDIT/REJECT command path
    (modules.admin_commands) remains the sole way the draft actually gets
    sent. Idempotent per draft (idempotency_key derived from draft_id, so
    a retry/duplicate call for the same draft never double-notifies)."""
    from app.database import fetch_one
    from modules.bridge_poller import _build_admin_review_draft
    from modules.message_router import get_primary_admin
    from modules import outbound

    row = await fetch_one(
        "SELECT id, recipient, reply_text, source, status, meta FROM fazle_draft_replies WHERE id = $1",
        draft_id,
    )
    if not row:
        return {"ok": False, "error": f"draft #{draft_id} not found"}
    if row["status"] not in ("pending", "pending_selfie", "edited"):
        return {"ok": False, "error": f"draft #{draft_id} is not pending (status={row['status']})"}

    admin = get_primary_admin()
    if not admin:
        return {"ok": False, "error": "no primary admin configured"}

    meta = row.get("meta") or {}
    if isinstance(meta, str):
        import json
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    sender_name = (meta.get("sender_name") or "") if isinstance(meta, dict) else ""
    sender_message = (meta.get("sender_message") or "") if isinstance(meta, dict) else ""
    ai_reply = (meta.get("ai_reply_raw") or row.get("reply_text") or "") if isinstance(meta, dict) else row.get("reply_text", "")

    card = _build_admin_review_draft(
        sender_name=sender_name,
        sender_mobile=row.get("recipient", ""),
        sender_message=sender_message or "(see draft #%d for full context)" % draft_id,
        ai_reply=ai_reply,
        draft_no=str(draft_id),
        category=category,
    )

    result = await outbound.enqueue(
        admin, card, source_bridge="bridge1", fallback_channel="bridge2",
        purpose="supervised-draft-review", idempotency_key=f"draft-review-notify-{draft_id}",
    )
    return {"ok": True, "draft_id": draft_id, "admin": admin, "enqueue_result": result}
