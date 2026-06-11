"""
Contact Roles — CRUD API
GET    /admin/contact-roles          → list all (or filter by phone)
POST   /admin/contact-roles          → upsert role for a phone+platform
DELETE /admin/contact-roles/{phone}  → remove role by phone (+ optional platform query param)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import get_settings
from app.database import execute, fetch_all, fetch_one

log = logging.getLogger("fazle.contact_roles")
router = APIRouter(prefix="/admin/contact-roles", tags=["contact-roles"])

_API_KEY_HEADER = APIKeyHeader(name="X-Internal-Key", auto_error=False)


async def _require_api_key(key: str = Depends(_API_KEY_HEADER)) -> None:
    settings = get_settings()
    if key and key == settings.internal_api_key:
        return
    if key:
        try:
            from modules import rbac
            admin = await rbac.get_admin_by_api_key(key)
            if admin and admin.get("status") == "active":
                return
        except Exception:
            pass
    raise HTTPException(status_code=403, detail="Forbidden")


class ContactRoleBody(BaseModel):
    phone: str
    platform: str = "whatsapp"
    name: Optional[str] = None
    role: str
    sub_role: Optional[str] = None
    priority: int = 10
    source: str = "admin_api"
    notes: Optional[str] = None
    confidence: int = 90


class ContactRoleUpdate(BaseModel):
    platform: str = "whatsapp"
    role: Optional[str] = None
    sub_role: Optional[str] = None
    name: Optional[str] = None
    priority: Optional[int] = None
    notes: Optional[str] = None
    confidence: Optional[int] = None
    is_active: Optional[bool] = None


@router.get("/", dependencies=[Depends(_require_api_key)])
async def list_contact_roles(
    phone: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
):
    """List contact role entries with pagination, optionally filtered by phone."""
    if phone:
        rows = await fetch_all(
            "SELECT * FROM fazle_contact_roles WHERE phone = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            phone, limit, offset,
        )
    else:
        rows = await fetch_all(
            "SELECT * FROM fazle_contact_roles ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return {"items": [dict(r) for r in rows], "count": len(rows), "limit": limit, "offset": offset}


@router.post("/", dependencies=[Depends(_require_api_key)])
async def upsert_contact_role(body: ContactRoleBody):
    """Insert or update a contact role (unique on phone+platform)."""
    if not body.phone or not body.role:
        raise HTTPException(status_code=400, detail="phone and role are required")
    await execute(
        """
        INSERT INTO fazle_contact_roles
          (phone, platform, name, role, sub_role,
           priority, source, is_active, confidence, notes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, true, $8, $9)
        ON CONFLICT (phone, platform) DO UPDATE SET
          role       = EXCLUDED.role,
          sub_role   = EXCLUDED.sub_role,
          name       = COALESCE(EXCLUDED.name, fazle_contact_roles.name),
          priority   = EXCLUDED.priority,
          source     = EXCLUDED.source,
          confidence = EXCLUDED.confidence,
          notes      = COALESCE(EXCLUDED.notes, fazle_contact_roles.notes),
          is_active  = true,
          updated_at = NOW()
        """,
        body.phone,
        body.platform,
        body.name,
        body.role,
        body.sub_role,
        body.priority,
        body.source,
        body.confidence,
        body.notes,
    )
    log.info("contact_role upserted phone=%s platform=%s role=%s", body.phone, body.platform, body.role)
    return {"ok": True, "phone": body.phone, "platform": body.platform, "role": body.role}


@router.put("/{phone}", dependencies=[Depends(_require_api_key)])
async def update_contact_role(
    phone: str,
    body: ContactRoleUpdate,
):
    """Update an existing contact role by phone + platform. Returns 404 if not found."""
    row = await fetch_one(
        """
        UPDATE fazle_contact_roles
        SET role        = COALESCE($3, role),
            sub_role    = COALESCE($4, sub_role),
            name        = COALESCE($5, name),
            priority    = COALESCE($6, priority),
            notes       = COALESCE($7, notes),
            confidence  = COALESCE($8, confidence),
            is_active   = COALESCE($9, is_active),
            updated_at  = NOW()
        WHERE phone = $1 AND platform = $2
        RETURNING id, phone, platform, role, sub_role, is_active, updated_at
        """,
        phone,
        body.platform,
        body.role,
        body.sub_role,
        body.name,
        body.priority,
        body.notes,
        body.confidence,
        body.is_active,
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No role found for phone={phone} platform={body.platform}")
    log.info("contact_role updated phone=%s platform=%s", phone, body.platform)
    return {"ok": True, **dict(row)}


@router.delete("/{phone}", dependencies=[Depends(_require_api_key)])
async def delete_contact_role(
    phone: str,
    platform: Optional[str] = Query(None),
):
    """Remove a contact role. If platform is given, removes only that platform row;
    otherwise removes all rows for the phone."""
    if platform:
        await execute(
            "DELETE FROM fazle_contact_roles WHERE phone = $1 AND platform = $2",
            phone, platform,
        )
    else:
        await execute(
            "DELETE FROM fazle_contact_roles WHERE phone = $1",
            phone,
        )
    log.info("contact_role deleted phone=%s platform=%s", phone, platform or "all")
    return {"ok": True, "phone": phone, "platform": platform or "all"}
