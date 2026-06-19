"""
Fazle Core — WhatsApp-Like Chat Frontend (wa_chat_frontend)

API endpoints for the 3-panel WhatsApp Web-style UI at /wa-chat.

Routes:
  GET    /api/wa/contacts               — paginated contact list with unread counts
  GET    /api/wa/contacts/sync-status   — contact sync health
  POST   /api/wa/contacts/sync          — trigger contact book refresh
  GET    /api/wa/contacts/{phone}       — single contact detail
  PATCH  /api/wa/contacts/{phone}       — edit display_name
  DELETE /api/wa/contacts/{phone}       — delete contact
  POST   /api/wa/contacts/{phone}/block — disable auto-reply for number

  GET    /api/wa/messages/{phone}       — conversation history (cursor-paginated)
  POST   /api/wa/send                   — send a message via bridge
  POST   /api/wa/broadcast              — send one message to multiple contacts

  GET    /api/wa/drafts                 — pending drafts enriched with contact name + original msg
  POST   /api/wa/drafts/{id}/approve    — approve + enqueue draft
  POST   /api/wa/drafts/{id}/edit       — edit draft body
  POST   /api/wa/drafts/{id}/reject     — reject draft

  POST   /api/wa/groups                 — create group
  GET    /api/wa/groups                 — list groups
  PATCH  /api/wa/groups/{id}            — edit group members
  DELETE /api/wa/groups/{id}            — delete group
  POST   /api/wa/groups/{id}/send       — broadcast to group members

  GET    /api/wa/settings               — role-based auto-reply toggles
  PATCH  /api/wa/settings               — update toggles (persisted to DB)

  GET    /api/wa/stream                 — SSE real-time stream (new messages + new drafts)
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.database import execute, fetch_all, fetch_one, fetch_val
from app.config import get_settings as _get_settings

log = logging.getLogger("fazle.wa_chat")
router = APIRouter(tags=["wa-chat-frontend"])

# ── Auth ─────────────────────────────────────────────────────────────────────
_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(
    header_key: Optional[str] = Depends(_API_KEY_HEADER),
    query_key: Optional[str] = Query(None, alias="key"),
) -> None:
    """Accept API key via header (all endpoints) or ?key= query param (SSE only)."""
    token = header_key or query_key
    settings = _get_settings()
    if token and token == settings.internal_api_key:
        return
    if token:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(token)
            if admin and admin.get("status") == "active":
                return
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Forbidden")


# ── Role-based auto-reply settings ──────────────────────────────────────────

ROLE_SETTINGS_KEYS = {
    "auto_reply.all",
    "auto_reply.family",
    "auto_reply.admin_group",
    "auto_reply.employee",
    "auto_reply.escort_client",
    "auto_reply.client",
    "auto_reply.recruitment",
}

ROLE_LABELS = {
    "auto_reply.all":          "All (Master)",
    "auto_reply.family":       "Family",
    "auto_reply.admin_group":  "Admin Group",
    "auto_reply.employee":     "Employee",
    "auto_reply.escort_client": "Escort Client",
    "auto_reply.client":       "Client",
    "auto_reply.recruitment":  "Recruitment",
}


def _id_role_to_setting_key(id_role: str) -> str:
    """Map an identity_role to the matching auto_reply.* settings key."""
    r = (id_role or "").lower()
    if "family" in r:
        return "family"
    if "admin" in r:
        return "admin_group"
    if "escort" in r and "client" in r:
        return "escort_client"
    if r in ("vip_client", "repeat_client", "client_escort_buyer", "client"):
        return "client"
    if r in ("employee", "worker"):
        return "employee"
    if r in ("candidate", "recruit"):
        return "recruitment"
    return "all"


# ── Runtime Settings Helpers ─────────────────────────────────────────────────

async def get_runtime_setting(key: str, default: str = "false") -> str:
    try:
        row = await fetch_one(
            "SELECT value FROM fazle_runtime_settings WHERE key=$1", key
        )
        return row["value"] if row else default
    except Exception:
        return default


async def set_runtime_setting(key: str, value: str) -> None:
    await execute(
        """INSERT INTO fazle_runtime_settings (key, value, updated_at)
           VALUES ($1, $2, NOW())
           ON CONFLICT (key) DO UPDATE SET value=$2, updated_at=NOW()""",
        key, value,
    )


async def get_effective_auto_reply(role: str = "all") -> bool:
    """
    Returns True if auto-reply should fire for the given role.
    Master toggle (auto_reply.all) must be true first.
    """
    master = await get_runtime_setting("auto_reply.all", "false")
    if master.lower() not in ("true", "1", "yes"):
        return False
    if role == "all":
        return True
    val = await get_runtime_setting(f"auto_reply.{role}", "false")
    return val.lower() in ("true", "1", "yes")


# ── DB Table Setup ───────────────────────────────────────────────────────────

async def ensure_wa_chat_tables() -> None:
    """Create runtime settings and groups tables; seed auto-reply defaults."""
    settings = _get_settings()

    await execute("""
        CREATE TABLE IF NOT EXISTS fazle_runtime_settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    await execute("""
        CREATE TABLE IF NOT EXISTS wa_chat_groups (
            id          SERIAL PRIMARY KEY,
            name        TEXT NOT NULL,
            admin_phone TEXT,
            members     TEXT[] NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # Seed auto-reply defaults — only insert if row does not yet exist
    global_default = "true" if settings.auto_reply_enabled else "false"
    recruit_default = "true" if settings.recruitment_autoreply_enabled else "false"
    seeds = {
        "auto_reply.all":           global_default,
        "auto_reply.family":        "false",
        "auto_reply.admin_group":   "false",
        "auto_reply.employee":      "false",
        "auto_reply.escort_client": "false",
        "auto_reply.client":        "false",
        "auto_reply.recruitment":   recruit_default,
    }
    for key, val in seeds.items():
        await execute(
            "INSERT INTO fazle_runtime_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING",
            key, val,
        )


# ── Settings endpoints ────────────────────────────────────────────────────────

@router.get("/api/wa/settings")
async def get_settings_api(_: None = Depends(_require_api_key)):
    rows = await fetch_all(
        "SELECT key, value, updated_at FROM fazle_runtime_settings WHERE key LIKE 'auto_reply.%' ORDER BY key"
    )
    toggles = {}
    updated_at = {}
    for r in rows:
        toggles[r["key"]] = r["value"].lower() in ("true", "1", "yes")
        updated_at[r["key"]] = r["updated_at"].isoformat() if r["updated_at"] else None
    return {"toggles": toggles, "labels": ROLE_LABELS, "updated_at": updated_at}


class SettingsPatch(BaseModel):
    settings: dict[str, bool]


@router.patch("/api/wa/settings")
async def patch_settings(body: SettingsPatch, _: None = Depends(_require_api_key)):
    updated = {}
    for key, val in body.settings.items():
        if key not in ROLE_SETTINGS_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown setting key: {key!r}")
        await set_runtime_setting(key, "true" if val else "false")
        updated[key] = val
    return {"updated": updated}


# ── Contacts endpoints ────────────────────────────────────────────────────────

@router.get("/api/wa/contacts")
async def list_contacts(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    search: str = Query(""),
    _: None = Depends(_require_api_key),
):
    rows = await fetch_all(
        """
        SELECT
            m.sender_number                                        AS phone,
            m.platform,
            COALESCE(c.display_name, m.sender_number)             AS display_name,
            m.message_body                                         AS last_message,
            m.received_at                                          AS last_message_at,
            m.direction                                            AS last_direction,
            m.identity_role
        FROM (
            SELECT DISTINCT ON (sender_number)
                sender_number, platform, message_body, received_at, direction, identity_role
            FROM wbom_whatsapp_messages
            ORDER BY sender_number, received_at DESC
        ) m
        LEFT JOIN wbom_contacts c ON c.whatsapp_number = m.sender_number
        WHERE (
            $3 = ''
            OR m.sender_number ILIKE '%' || $3 || '%'
            OR c.display_name  ILIKE '%' || $3 || '%'
        )
        ORDER BY m.received_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit, offset, search,
    )

    phones = [r["phone"] for r in rows]
    unread_map: dict[str, int] = {}
    if phones:
        unread_rows = await fetch_all(
            """
            SELECT m.sender_number AS phone, COUNT(*) AS cnt
            FROM wbom_whatsapp_messages m
            WHERE m.sender_number = ANY($1::text[])
              AND m.direction = 'inbound'
              AND m.received_at > COALESCE((
                  SELECT MAX(m2.received_at)
                  FROM wbom_whatsapp_messages m2
                  WHERE m2.direction = 'outbound'
                    AND m2.receiver_number = m.sender_number
              ), '1970-01-01'::timestamptz)
            GROUP BY m.sender_number
            """,
            phones,
        )
        unread_map = {r["phone"]: int(r["cnt"]) for r in unread_rows}

    total = await fetch_val(
        """SELECT COUNT(DISTINCT m.sender_number)
           FROM wbom_whatsapp_messages m
           LEFT JOIN wbom_contacts c ON c.whatsapp_number = m.sender_number
           WHERE ($1 = '' OR m.sender_number ILIKE '%'||$1||'%' OR c.display_name ILIKE '%'||$1||'%')""",
        search,
    ) or 0

    contacts = []
    for r in rows:
        d = dict(r)
        d["unread_count"] = unread_map.get(r["phone"], 0)
        if d.get("last_message_at"):
            d["last_message_at"] = d["last_message_at"].isoformat()
        contacts.append(d)

    # Return the global max message_id so the frontend can seed its SSE cursor
    # and avoid replaying all historical messages on every page load.
    max_message_id = int(await fetch_val("SELECT COALESCE(MAX(message_id), 0) FROM wbom_whatsapp_messages") or 0)
    max_draft_id = int(await fetch_val("SELECT COALESCE(MAX(id), 0) FROM fazle_draft_replies") or 0)

    return {
        "contacts": contacts,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "max_message_id": max_message_id,
        "max_draft_id": max_draft_id,
    }


@router.get("/api/wa/contacts/sync-status")
async def contact_sync_status(_: None = Depends(_require_api_key)):
    total = int(await fetch_val("SELECT COUNT(*) FROM wbom_contacts") or 0)
    with_name = int(await fetch_val(
        "SELECT COUNT(*) FROM wbom_contacts WHERE display_name IS NOT NULL AND display_name != whatsapp_number"
    ) or 0)
    last_sync = await get_runtime_setting("contact_sync.last_run", "never")
    return {
        "total_contacts": total,
        "with_display_name": with_name,
        "unnamed": total - with_name,
        "last_sync": last_sync,
    }


@router.post("/api/wa/contacts/sync")
async def trigger_contact_sync(_: None = Depends(_require_api_key)):
    try:
        from modules.contact_sync import sync_all_contacts
        result = await sync_all_contacts()
        await set_runtime_setting(
            "contact_sync.last_run",
            datetime.now(timezone.utc).isoformat(),
        )
        return {"synced": True, "result": result}
    except Exception as e:
        log.warning("[wa_chat] contact sync failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/wa/contacts/{phone}")
async def get_contact(phone: str, _: None = Depends(_require_api_key)):
    row = await fetch_one(
        "SELECT whatsapp_number AS phone, display_name, platform, last_seen, updated_at FROM wbom_contacts WHERE whatsapp_number=$1",
        phone,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Contact not found")
    d = dict(row)
    d["auto_reply_blocked"] = (await get_runtime_setting(f"phone_block.{phone}", "false")) == "true"
    if d.get("last_seen"):
        d["last_seen"] = d["last_seen"].isoformat()
    if d.get("updated_at"):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


class ContactPatch(BaseModel):
    display_name: Optional[str] = None


@router.patch("/api/wa/contacts/{phone}")
async def update_contact(phone: str, body: ContactPatch, _: None = Depends(_require_api_key)):
    if body.display_name is not None:
        await execute(
            "UPDATE wbom_contacts SET display_name=$1, updated_at=NOW() WHERE whatsapp_number=$2",
            body.display_name, phone,
        )
    return {"updated": True}


@router.delete("/api/wa/contacts/{phone}")
async def delete_contact(phone: str, _: None = Depends(_require_api_key)):
    await execute("DELETE FROM wbom_contacts WHERE whatsapp_number=$1", phone)
    return {"deleted": True}


@router.post("/api/wa/contacts/{phone}/block")
async def block_contact_autoreply(phone: str, _: None = Depends(_require_api_key)):
    """Persist a per-phone auto-reply block (treated as draft-always for this number)."""
    await set_runtime_setting(f"phone_block.{phone}", "true")
    return {"blocked": True, "phone": phone}


@router.post("/api/wa/contacts/{phone}/unblock")
async def unblock_contact_autoreply(phone: str, _: None = Depends(_require_api_key)):
    await set_runtime_setting(f"phone_block.{phone}", "false")
    return {"unblocked": True, "phone": phone}


# ── Messages endpoints ────────────────────────────────────────────────────────

@router.get("/api/wa/messages/{phone}")
async def get_messages(
    phone: str,
    limit: int = Query(50, le=200),
    before_id: Optional[int] = Query(None),
    _: None = Depends(_require_api_key),
):
    from modules.phone_normalizer import normalize_phone
    canonical = normalize_phone(phone) or phone
    variants = list({canonical, phone})

    rows = await fetch_all(
        """
        SELECT message_id AS id, sender_number, message_body AS body,
               direction, platform, received_at, identity_role, intent_detected,
               receiver_number
        FROM wbom_whatsapp_messages
        WHERE (
            sender_number = ANY($2::text[])
            OR (direction='outbound' AND receiver_number = ANY($2::text[]))
        )
          AND ($3::bigint IS NULL OR message_id < $3)
        ORDER BY message_id DESC
        LIMIT $1
        """,
        limit, variants, before_id,
    )

    messages = []
    oldest_id = None
    for r in rows:
        d = dict(r)
        if d.get("received_at"):
            d["received_at"] = d["received_at"].isoformat()
        messages.append(d)
        if oldest_id is None or d["id"] < oldest_id:
            oldest_id = d["id"]

    return {"messages": messages, "has_more": len(messages) == limit, "oldest_id": oldest_id}


class SendRequest(BaseModel):
    phone: str
    text: str
    platform: Optional[str] = None


@router.post("/api/wa/send")
async def send_message(body: SendRequest, _: None = Depends(_require_api_key)):
    platform = body.platform
    if not platform:
        row = await fetch_one(
            "SELECT platform FROM wbom_whatsapp_messages WHERE sender_number=$1 ORDER BY received_at DESC LIMIT 1",
            body.phone,
        )
        platform = row["platform"] if row else "bridge2"

    source_bridge = platform if platform in ("bridge1", "bridge2", "meta") else "bridge2"
    from modules.outbound import enqueue as _enqueue
    qid = await _enqueue(
        recipient=body.phone,
        body=body.text,
        source_bridge=source_bridge,
        purpose="wa_chat_ui_send",
    )
    return {"sent": True, "queue_id": qid, "platform": source_bridge}


class BroadcastRequest(BaseModel):
    phones: list[str]
    text: str
    platform: Optional[str] = "bridge2"


@router.post("/api/wa/broadcast")
async def broadcast_message(body: BroadcastRequest, _: None = Depends(_require_api_key)):
    if not body.phones:
        raise HTTPException(status_code=400, detail="phones list is empty")
    if len(body.phones) > 500:
        raise HTTPException(status_code=400, detail="Cannot broadcast to more than 500 numbers at once")

    from modules.outbound import enqueue as _enqueue
    results = []
    for phone in body.phones:
        try:
            qid = await _enqueue(
                recipient=phone,
                body=body.text,
                source_bridge=body.platform or "bridge2",
                purpose="wa_chat_broadcast",
            )
            results.append({"phone": phone, "queue_id": qid, "ok": True})
        except Exception as e:
            results.append({"phone": phone, "ok": False, "error": str(e)})

    return {
        "sent": len([r for r in results if r["ok"]]),
        "total": len(body.phones),
        "results": results,
    }


# ── Drafts endpoints ──────────────────────────────────────────────────────────

@router.get("/api/wa/drafts")
async def list_drafts(
    limit: int = Query(50, le=200),
    offset: int = Query(0),
    _: None = Depends(_require_api_key),
):
    rows = await fetch_all(
        """
        SELECT
            d.id,
            d.recipient                                            AS phone,
            COALESCE(c.display_name, d.recipient)                 AS contact_name,
            d.source                                               AS source_bridge,
            d.reply_text                                           AS draft_body,
            d.intent,
            d.status,
            d.created_at,
            orig.message_body                                      AS original_message,
            orig.received_at                                       AS original_received_at
        FROM fazle_draft_replies d
        LEFT JOIN wbom_contacts c ON c.whatsapp_number = d.recipient
        LEFT JOIN LATERAL (
            SELECT message_body, received_at
            FROM wbom_whatsapp_messages
            WHERE sender_number = d.recipient
            ORDER BY received_at DESC
            LIMIT 1
        ) orig ON true
        WHERE d.reviewed = false
          AND d.status NOT IN ('rejected', 'sent', 'approved')
        ORDER BY d.created_at DESC
        LIMIT $1 OFFSET $2
        """,
        limit, offset,
    )
    total = int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_draft_replies WHERE reviewed=false AND status NOT IN ('rejected','sent','approved')"
    ) or 0)

    drafts = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        if d.get("original_received_at"):
            d["original_received_at"] = d["original_received_at"].isoformat()
        drafts.append(d)
    return {"drafts": drafts, "total": total}


class DraftEdit(BaseModel):
    new_body: str


@router.post("/api/wa/drafts/{draft_id}/edit")
async def edit_draft(draft_id: int, body: DraftEdit, _: None = Depends(_require_api_key)):
    draft = await fetch_one("SELECT id FROM fazle_draft_replies WHERE id=$1", draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await execute(
        "UPDATE fazle_draft_replies SET reply_text=$1, status='edited' WHERE id=$2",
        body.new_body, draft_id,
    )
    return {"edited": True}


@router.post("/api/wa/drafts/{draft_id}/approve")
async def approve_draft(draft_id: int, _: None = Depends(_require_api_key)):
    draft = await fetch_one(
        "SELECT id, status, recipient, reply_text, source FROM fazle_draft_replies WHERE id=$1",
        draft_id,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft["status"] in ("sent", "approved"):
        raise HTTPException(status_code=400, detail=f"Draft already {draft['status']}")

    await execute(
        "UPDATE fazle_draft_replies SET status='approved', approved_at=NOW(), reviewed=true WHERE id=$1",
        draft_id,
    )
    source = draft.get("source") or "bridge1"
    source_bridge = "bridge1" if source in ("bridge1", "meta") else "bridge2"
    from modules.outbound import enqueue as _enqueue
    qid = await _enqueue(
        recipient=draft["recipient"],
        body=draft["reply_text"],
        source_bridge=source_bridge,
        purpose="draft_approved_wa_chat",
    )
    await execute(
        "UPDATE fazle_draft_replies SET status='sent', sent_at=NOW() WHERE id=$1",
        draft_id,
    )
    return {"approved": True, "queue_id": qid}


@router.post("/api/wa/drafts/{draft_id}/reject")
async def reject_draft(draft_id: int, _: None = Depends(_require_api_key)):
    draft = await fetch_one("SELECT id FROM fazle_draft_replies WHERE id=$1", draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    await execute(
        "UPDATE fazle_draft_replies SET status='rejected', reviewed=true WHERE id=$1",
        draft_id,
    )
    return {"rejected": True}


# ── Groups endpoints ──────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name: str
    admin_phone: Optional[str] = None
    members: list[str] = []


@router.post("/api/wa/groups")
async def create_group(body: GroupCreate, _: None = Depends(_require_api_key)):
    row = await fetch_one(
        "INSERT INTO wa_chat_groups (name, admin_phone, members) VALUES ($1, $2, $3) RETURNING id",
        body.name, body.admin_phone, body.members,
    )
    return {"id": row["id"], "name": body.name}


@router.get("/api/wa/groups")
async def list_groups(_: None = Depends(_require_api_key)):
    rows = await fetch_all(
        "SELECT id, name, admin_phone, members, created_at FROM wa_chat_groups ORDER BY name"
    )
    groups = []
    for r in rows:
        d = dict(r)
        if d.get("created_at"):
            d["created_at"] = d["created_at"].isoformat()
        groups.append(d)
    return {"groups": groups}


class GroupPatch(BaseModel):
    name: Optional[str] = None
    admin_phone: Optional[str] = None
    add_members: list[str] = []
    remove_members: list[str] = []


@router.patch("/api/wa/groups/{group_id}")
async def update_group(group_id: int, body: GroupPatch, _: None = Depends(_require_api_key)):
    grp = await fetch_one("SELECT id, members FROM wa_chat_groups WHERE id=$1", group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")

    members: list[str] = list(grp["members"] or [])
    for m in body.add_members:
        if m not in members:
            members.append(m)
    for m in body.remove_members:
        if m in members:
            members.remove(m)

    args: list[Any] = []
    updates: list[str] = []

    if body.name is not None:
        args.append(body.name)
        updates.append(f"name=${len(args)}")
    if body.admin_phone is not None:
        args.append(body.admin_phone)
        updates.append(f"admin_phone=${len(args)}")

    args.append(members)
    updates.append(f"members=${len(args)}")
    updates.append("updated_at=NOW()")

    args.append(group_id)
    await execute(
        f"UPDATE wa_chat_groups SET {', '.join(updates)} WHERE id=${len(args)}",
        *args,
    )
    return {"updated": True, "member_count": len(members)}


@router.delete("/api/wa/groups/{group_id}")
async def delete_group(group_id: int, _: None = Depends(_require_api_key)):
    await execute("DELETE FROM wa_chat_groups WHERE id=$1", group_id)
    return {"deleted": True}


@router.post("/api/wa/groups/{group_id}/send")
async def send_to_group(group_id: int, body: SendRequest, _: None = Depends(_require_api_key)):
    grp = await fetch_one("SELECT members FROM wa_chat_groups WHERE id=$1", group_id)
    if not grp:
        raise HTTPException(status_code=404, detail="Group not found")
    members = list(grp["members"] or [])
    if not members:
        return {"sent": 0, "total": 0, "message": "No members in group"}

    from modules.outbound import enqueue as _enqueue
    results = []
    for phone in members:
        try:
            qid = await _enqueue(
                recipient=phone,
                body=body.text,
                source_bridge=body.platform or "bridge2",
                purpose="wa_chat_group_send",
            )
            results.append({"phone": phone, "queue_id": qid, "ok": True})
        except Exception as e:
            results.append({"phone": phone, "ok": False, "error": str(e)})

    return {"sent": len([r for r in results if r["ok"]]), "total": len(members), "results": results}


# ── SSE Stream ────────────────────────────────────────────────────────────────

@router.get("/api/wa/stream")
async def sse_stream(
    last_id: int = Query(0),
    last_draft_id: int = Query(0),
    _: None = Depends(_require_api_key),
):
    """Server-Sent Events stream: emits new_message and new_draft events."""

    async def event_generator():
        current_msg_id = last_id
        current_draft_id = last_draft_id

        while True:
            # Poll for new messages
            try:
                msg_rows = await fetch_all(
                    """
                    SELECT message_id AS id, sender_number AS phone,
                           message_body AS body, direction, platform,
                           received_at, identity_role, intent_detected
                    FROM wbom_whatsapp_messages
                    WHERE message_id > $1
                    ORDER BY message_id ASC
                    LIMIT 50
                    """,
                    current_msg_id,
                )
                for row in msg_rows:
                    current_msg_id = row["id"]
                    payload = json.dumps({
                        "type": "new_message",
                        "payload": {
                            "id": row["id"],
                            "phone": row["phone"],
                            "body": row["body"],
                            "direction": row["direction"],
                            "platform": row["platform"],
                            "received_at": row["received_at"].isoformat() if row["received_at"] else None,
                            "identity_role": row["identity_role"],
                            "intent_detected": row["intent_detected"],
                        },
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
            except Exception as e:
                log.debug("[wa_stream] msg poll error: %s", e)

            # Poll for new drafts
            try:
                draft_rows = await fetch_all(
                    """
                    SELECT d.id,
                           d.recipient                              AS phone,
                           COALESCE(c.display_name, d.recipient)   AS contact_name,
                           d.reply_text                            AS draft_body,
                           d.intent,
                           d.created_at,
                           orig.message_body                       AS original_message
                    FROM fazle_draft_replies d
                    LEFT JOIN wbom_contacts c ON c.whatsapp_number = d.recipient
                    LEFT JOIN LATERAL (
                        SELECT message_body FROM wbom_whatsapp_messages
                        WHERE sender_number = d.recipient
                        ORDER BY received_at DESC LIMIT 1
                    ) orig ON true
                    WHERE d.id > $1
                      AND d.status NOT IN ('rejected', 'sent', 'approved')
                    ORDER BY d.id ASC
                    LIMIT 20
                    """,
                    current_draft_id,
                )
                for row in draft_rows:
                    current_draft_id = row["id"]
                    payload = json.dumps({
                        "type": "new_draft",
                        "payload": {
                            "id": row["id"],
                            "phone": row["phone"],
                            "contact_name": row["contact_name"],
                            "draft_body": row["draft_body"],
                            "intent": row["intent"],
                            "original_message": row["original_message"],
                            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                        },
                    }, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
            except Exception as e:
                log.debug("[wa_stream] draft poll error: %s", e)

            yield ": keepalive\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
