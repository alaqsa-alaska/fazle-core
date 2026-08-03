"""
Contact Roles — CRUD API
GET    /admin/contact-roles          → list all (or filter by phone)
POST   /admin/contact-roles          → upsert role for a phone+platform
DELETE /admin/contact-roles/{phone}  → remove role by phone (+ optional platform query param)
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator

from app.database import execute, fetch_all, fetch_one
from shared.auth_deps import require_api_key as _require_api_key

log = logging.getLogger("fazle.contact_roles")
router = APIRouter(prefix="/admin/contact-roles", tags=["contact-roles"])


class AssignableContactRole(str, Enum):
    """Roles this API may assign. Deliberately excludes admin/accountant/
    employee/family — those must come from .env or their own canonical
    domain (CANONICAL_BUSINESS_RULES.md's .env-authority Structural Rule),
    never from this free-text seed table. 'blocked' is not a role here
    either; disabling a contact is `is_active=false`, not a role string
    (that value was removed for good, migration 042)."""

    VIP_CLIENT = "vip_client"
    CLIENT_ESCORT_BUYER = "client_escort_buyer"
    REPEAT_CLIENT = "repeat_client"
    VENDOR = "vendor"
    SUPERVISOR = "supervisor"
    CANDIDATE = "candidate"


def _strip_role_ws(v):
    return v.strip() if isinstance(v, str) else v


class ContactRoleBody(BaseModel):
    phone: str
    platform: str = "whatsapp"
    name: Optional[str] = None
    role: AssignableContactRole
    sub_role: Optional[str] = None
    priority: int = 10
    source: str = "admin_api"
    notes: Optional[str] = None
    confidence: int = 90

    _strip_role = field_validator("role", mode="before")(_strip_role_ws)


class ContactRoleUpdate(BaseModel):
    platform: str = "whatsapp"
    role: Optional[AssignableContactRole] = None
    sub_role: Optional[str] = None
    name: Optional[str] = None
    priority: Optional[int] = None
    notes: Optional[str] = None
    confidence: Optional[int] = None
    is_active: Optional[bool] = None

    _strip_role = field_validator("role", mode="before")(_strip_role_ws)


@router.get("/", dependencies=[Depends(_require_api_key)])
async def list_contact_roles(
    phone: Optional[str] = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0, ge=0),
):
    """List contact role entries with pagination, optionally filtered by phone."""
    if phone:
        # 2026-08-03: matches all phone variants (01XXXXXXXXX, 8801XXXXXXXXX,
        # +8801XXXXXXXXX) instead of an exact match against one normalized
        # form — fazle_contact_roles.phone is not guaranteed to be stored
        # canonically (confirmed live: real vip_client rows are stored raw
        # 01XXXXXXXXX). Same class of bug as identity_brain._lookup_seed(),
        # same table, fixed the same way — see identity_brain.md's standing
        # rule on phone normalization.
        from modules.number_identity import normalize_phone as _phone_variants
        variants = _phone_variants(phone) or [phone]
        rows = await fetch_all(
            "SELECT * FROM fazle_contact_roles WHERE phone = ANY($1) ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            variants, limit, offset,
        )
    else:
        rows = await fetch_all(
            "SELECT * FROM fazle_contact_roles ORDER BY created_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return {"items": [dict(r) for r in rows], "count": len(rows), "limit": limit, "offset": offset}


@router.post("/", dependencies=[Depends(_require_api_key)])
async def upsert_contact_role(body: ContactRoleBody):
    """Insert or update a contact role (unique on phone+platform).

    2026-07-22: phone is now normalized to canonical 8801XXXXXXXXX before
    the write. The (phone, platform) unique constraint below only catches a
    conflict when the *string* matches -- "01844836824" and
    "8801844836824" are different strings even though they're the same
    number, which is exactly how this table accumulated real duplicate
    rows historically (cleaned up separately, see management_decisions.md
    2026-07-22). Normalizing here is what makes the existing constraint
    actually effective going forward.
    """
    if not body.phone or not body.role:
        raise HTTPException(status_code=400, detail="phone and role are required")
    from modules.phone_normalizer import normalize_phone as _normalize_phone
    norm_phone = _normalize_phone(body.phone)
    if not norm_phone:
        raise HTTPException(status_code=400, detail=f"invalid phone: {body.phone!r}")
    await execute(
        """
        INSERT INTO fazle_contact_roles
          (phone, platform, name, role, sub_role,
           priority, source, is_active, confidence, notes)
        VALUES ($1, $2, COALESCE($3, ''), $4, COALESCE($5, ''), $6, $7, true, $8, COALESCE($9, ''))
        ON CONFLICT (phone, platform) DO UPDATE SET
          role       = EXCLUDED.role,
          sub_role   = COALESCE($5, fazle_contact_roles.sub_role),
          name       = COALESCE($3, fazle_contact_roles.name),
          priority   = EXCLUDED.priority,
          source     = EXCLUDED.source,
          confidence = EXCLUDED.confidence,
          notes      = COALESCE($9, fazle_contact_roles.notes),
          is_active  = true,
          updated_at = NOW()
        """,
        norm_phone,
        body.platform,
        body.name,
        body.role.value,
        body.sub_role,
        body.priority,
        body.source,
        body.confidence,
        body.notes,
    )
    log.info("contact_role upserted phone=%s platform=%s role=%s", body.phone, body.platform, body.role.value)
    return {"ok": True, "phone": body.phone, "platform": body.platform, "role": body.role.value}


@router.put("/{phone}", dependencies=[Depends(_require_api_key)])
async def update_contact_role(
    phone: str,
    body: ContactRoleUpdate,
):
    """Update an existing contact role by phone + platform. Returns 404 if not found."""
    from modules.phone_normalizer import normalize_phone as _normalize_phone
    phone = _normalize_phone(phone) or phone
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
        body.role.value if body.role is not None else None,
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
    # 2026-08-03: same fix as list_contact_roles above — match all phone
    # variants, not one exact normalized form, since the stored column
    # isn't guaranteed canonical.
    from modules.number_identity import normalize_phone as _phone_variants
    variants = _phone_variants(phone) or [phone]
    if platform:
        await execute(
            "DELETE FROM fazle_contact_roles WHERE phone = ANY($1) AND platform = $2",
            variants, platform,
        )
    else:
        await execute(
            "DELETE FROM fazle_contact_roles WHERE phone = ANY($1)",
            variants,
        )
    log.info("contact_role deleted phone=%s platform=%s", phone, platform or "all")
    return {"ok": True, "phone": phone, "platform": platform or "all"}
