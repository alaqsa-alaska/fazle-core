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


async def find_conflicting_directive(
    subject_type: Optional[str], subject_key: Optional[str], directive_text: str,
) -> Optional[dict]:
    """Phase 3, 2026-08-25 recovery pass ("general Owner business-rule
    mutation architecture"): conflict-detection helper for the write path.

    Returns the existing ACTIVE directive row that shares the same
    (subject_type, subject_key) as a proposed new one, if its text
    materially differs (case/whitespace-insensitive compare) -- i.e. a
    genuine content conflict, not just a re-statement of the same rule.
    Returns None if there's no active directive on this exact subject, or
    if one exists but says essentially the same thing (safe to no-op).

    Deliberately a separate function, not a parameter on save_directive():
    save_directive() has 4 existing call sites (router.py x2, app/main.py,
    ai_console/live_adapters.py) and changing its signature/return shape
    would need auditing every one (see this repo's own standing rule
    against converting a function's contract without a repo-wide grep).
    Callers that want conflict-checking call this BEFORE save_directive();
    callers that don't (e.g. the existing REMEMBER/FORGET WhatsApp path)
    are completely unaffected.

    Deliberately exact-key matching only (no semantic/fuzzy similarity) --
    matching this session's "minimum required change" principle. A
    directive on a materially related but differently-keyed subject
    (e.g. "recruitment_minimum_age" vs "escort_minimum_age") is not
    flagged; that would need embedding-based similarity, out of scope
    here."""
    if not subject_type or not subject_key:
        return None
    rows = await fetch_all(
        """SELECT * FROM fazle_admin_directives
           WHERE status='active' AND subject_type=$1 AND subject_key=$2
           ORDER BY created_at DESC""",
        subject_type, subject_key,
    )
    new_norm = " ".join((directive_text or "").strip().lower().split())
    for row in rows:
        existing_norm = " ".join((row["directive_text"] or "").strip().lower().split())
        if existing_norm != new_norm:
            return dict(row)
    return None


async def get_active_directives_context(*, limit: int = 50) -> str:
    """Phase 3, 2026-08-25 recovery pass: format every currently-active
    Owner directive as a single context block, for UNCONDITIONAL inclusion
    (not RAG-dependent retrieval) in customer/employee answer generation.

    This is the fix for a real, verified gap: before this, an Owner
    directive was only ever visible to the tiny number of hardcoded call
    sites that explicitly `list_directives(subject_key=<one exact known
    key>)` (recruitment_minimum_age, office_numbers) -- a directive
    written on any other subject_key was silently invisible to every
    live customer/employee answer path, and fazle_admin_directives was
    never indexed into the generic RAG layer either. Rather than relying
    on RAG recall (this codebase's own comments elsewhere document that
    mechanism as unreliable for surfacing a single source's content) or
    building a bespoke per-topic read site for every possible subject,
    this simply includes ALL active directives every time -- Owner
    directives are expected to stay a small, curated set (not a large
    corpus), so unconditional inclusion is cheap and far more reliable
    than retrieval. Callers should label this block as taking precedence
    over older static source-of-truth content when they conflict, since
    a directive is by definition the most recent explicit Owner
    instruction. Fails closed to "" on any error; never raises."""
    try:
        rows = await list_directives(status="active", limit=limit)
    except Exception as exc:
        log.warning("[admin_directives] get_active_directives_context failed: %s", exc)
        return ""
    if not rows:
        return ""
    lines = []
    for row in rows:
        subject = f"{row.get('subject_type') or '?'}/{row.get('subject_key') or '?'}"
        text = (row.get("directive_text") or "").strip()
        if not text:
            continue
        set_date = row.get("created_at")
        set_date_str = set_date.strftime("%Y-%m-%d") if set_date else "?"
        lines.append(f"- [{subject}, set {set_date_str}] {text}")
    if not lines:
        return ""
    return "সাম্প্রতিক Owner নির্দেশনা (এগুলো পুরনো তথ্যের চেয়ে অগ্রাধিকার পাবে):\n" + "\n".join(lines)


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
