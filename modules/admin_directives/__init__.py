"""
modules.admin_directives -- narrow admin directive/action memory.

This is NOT modules.ollama_memory. ollama_memory is the AI's own isolated
self-memory (facts it infers, questions it answered) -- DEVELOPER_GUIDE.md
Section H documents it as the only module allowed to write there. This
module instead stores explicit, admin-authored instructions ("remember
this", "note about this number") with a full audit trail of who wrote it,
from which channel, and when it was revoked.

Single write path, two entry points (mirrors modules.attendance's
finalize_attendance_draft() shared-service pattern):
  - WhatsApp: modules.admin_directives.router's REMEMBER / FORGET commands
  - Web:      the /admin/directives endpoints in app/main.py

Table: fazle_admin_directives (db/migrations/059_admin_directives.sql).
category/status/created_by_channel are enforced by DB-level CHECK
constraints; the validation here fails fast with a clear error instead of
surfacing a raw asyncpg constraint-violation to the caller.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.database import fetch_all, fetch_one, fetch_val

log = logging.getLogger("fazle.admin_directives")

VALID_CATEGORIES = {"general", "autoreply_policy", "contact_note", "escalation"}
VALID_CHANNELS = {"whatsapp", "web"}


async def save_directive(
    directive_text: str,
    *,
    created_by_phone: str,
    created_by_channel: str,
    category: str = "general",
    subject_type: Optional[str] = None,
    subject_key: Optional[str] = None,
    source_ref: Optional[str] = None,
) -> int:
    """Insert a new active directive. Returns the new directive's id."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}, got {category!r}")
    if created_by_channel not in VALID_CHANNELS:
        raise ValueError(f"created_by_channel must be one of {sorted(VALID_CHANNELS)}, got {created_by_channel!r}")
    if not directive_text or not directive_text.strip():
        raise ValueError("directive_text must not be empty")

    directive_id = await fetch_val(
        """INSERT INTO fazle_admin_directives
               (directive_text, category, subject_type, subject_key,
                created_by_phone, created_by_channel, source_ref)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           RETURNING id""",
        directive_text.strip()[:4000],
        category,
        subject_type,
        subject_key,
        created_by_phone,
        created_by_channel,
        source_ref,
    )
    log.info(
        "[ADMIN_DIRECTIVE_SAVED] id=%s category=%s subject=%s/%s by=%s channel=%s",
        directive_id, category, subject_type, subject_key, created_by_phone, created_by_channel,
    )
    return int(directive_id)


async def get_directive(directive_id: int) -> Optional[dict]:
    row = await fetch_one(
        "SELECT * FROM fazle_admin_directives WHERE id=$1", directive_id
    )
    return dict(row) if row else None


async def list_directives(
    *,
    subject_key: Optional[str] = None,
    status: str = "active",
    limit: int = 50,
) -> list[dict]:
    """List directives, most recent first. status='all' returns every status."""
    limit = max(1, min(limit, 200))
    if status == "all":
        if subject_key:
            rows = await fetch_all(
                """SELECT * FROM fazle_admin_directives
                   WHERE subject_key=$1
                   ORDER BY created_at DESC LIMIT $2""",
                subject_key, limit,
            )
        else:
            rows = await fetch_all(
                "SELECT * FROM fazle_admin_directives ORDER BY created_at DESC LIMIT $1",
                limit,
            )
    else:
        if subject_key:
            rows = await fetch_all(
                """SELECT * FROM fazle_admin_directives
                   WHERE status=$1 AND subject_key=$2
                   ORDER BY created_at DESC LIMIT $3""",
                status, subject_key, limit,
            )
        else:
            rows = await fetch_all(
                """SELECT * FROM fazle_admin_directives
                   WHERE status=$1
                   ORDER BY created_at DESC LIMIT $2""",
                status, limit,
            )
    return [dict(r) for r in rows]


async def revoke_directive(directive_id: int, *, revoked_by_phone: str) -> bool:
    """Soft-revoke a directive. Returns False if the id doesn't exist or is
    already revoked (idempotent no-op), True if this call revoked it."""
    revoked_id = await fetch_val(
        """UPDATE fazle_admin_directives
           SET status='revoked', revoked_at=NOW(), revoked_by_phone=$1, updated_at=NOW()
           WHERE id=$2 AND status='active'
           RETURNING id""",
        revoked_by_phone, directive_id,
    )
    if revoked_id is not None:
        log.info("[ADMIN_DIRECTIVE_REVOKED] id=%s by=%s", directive_id, revoked_by_phone)
        return True
    return False
