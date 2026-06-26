"""
shared.draft_reply — Single Source of Truth for fazle_draft_replies INSERT.

All modules that need to create a draft reply MUST call create_draft_reply()
instead of issuing raw INSERTs. This guarantees:

  • Race-safe collision guard: the duplicate-check SELECT and the INSERT run
    inside a distributed lock (shared.locks.locked). Two concurrent callers
    for the same sender+bridge cannot both pass the check and both insert.
  • Consistent logging: every attempt (created, suppressed, or lock-missed) is
    logged with the originating module name for traceability.
  • Schema mapping: the caller uses logical names (sender, bridge, draft_text,
    role, context); this module maps them to the actual DB column names
    (recipient, source, reply_text, meta).

Lock key pattern:  draft_reply:{bridge}:{sender}
Lock TTL:          15 seconds (covers SELECT + INSERT, never gets stuck)
Collision window:  5 minutes (matching pending draft for same sender+bridge)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.database import fetch_val, execute
from shared.locks import locked

log = logging.getLogger("fazle.shared.draft_reply")


async def create_draft_reply(
    *,
    sender: str,
    bridge: str,
    draft_text: str,
    role: str,
    intent: str,
    context: str = "",
    source_module: str = "unknown",
    db=None,  # reserved for future explicit connection passing; currently unused
) -> Optional[int]:
    """
    Insert a new draft reply into fazle_draft_replies, race-safe.

    Execution order:
    1. Normalise sender (strip whitespace) and bridge (strip).
    2. Acquire a distributed lock scoped to sender + bridge.
       If the lock is already held by a concurrent caller, return None.
    3. Inside the lock, check for an existing pending draft for the same
       recipient AND source within the last 5 minutes.
    4. If a duplicate exists → log warning and return None.
    5. Otherwise INSERT the new row and return its id.

    The lock makes steps 3-5 atomic across concurrent workers, preventing
    the SELECT-then-INSERT race that produced duplicate pending drafts.

    Column mapping (logical → actual):
        sender        → recipient
        bridge        → source
        draft_text    → reply_text
        role          → meta->>'role'
        context       → meta->>'context'
        source_module → meta->>'source_module'

    Returns:
        int   — new draft id when successfully created
        None  — when suppressed (duplicate found, lock missed, or any error)
    """
    # ── 1. Normalise inputs ───────────────────────────────────────────────────
    sender = (sender or "").strip()
    bridge = (bridge or "").strip()

    if not sender:
        log.warning(
            "[draft_reply] create_draft_reply called with empty sender module=%s",
            source_module,
        )
        return None

    # ── 2. Acquire distributed lock ───────────────────────────────────────────
    lock_key = f"draft_reply:{bridge}:{sender}"
    worker_id = source_module or "draft_reply"

    try:
        async with locked(lock_key, worker_id=worker_id, ttl_s=15) as got_lock:
            if not got_lock:
                log.warning(
                    "[draft_reply] lock not acquired sender=%s bridge=%s module=%s",
                    sender, bridge, source_module,
                )
                return None

            # ── 3. Collision check (inside lock — now truly atomic) ───────────
            try:
                existing_id = await fetch_val(
                    """
                    SELECT id FROM fazle_draft_replies
                    WHERE  recipient  = $1
                      AND  source     = $2
                      AND  status     = 'pending'
                      AND  created_at > NOW() - INTERVAL '5 minutes'
                    LIMIT 1
                    """,
                    sender,
                    bridge,
                )
            except Exception as exc:
                # Fail closed under concurrency — safer to drop than to duplicate.
                log.error(
                    "[draft_reply] collision check failed sender=%s bridge=%s module=%s: %s",
                    sender, bridge, source_module, exc,
                )
                return None

            if existing_id is not None:
                log.warning(
                    "[draft_reply] DUPLICATE suppressed sender=%s bridge=%s existing_id=%s module=%s",
                    sender, bridge, existing_id, source_module,
                )
                return None

            # ── 4. INSERT ─────────────────────────────────────────────────────
            expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
            meta = json.dumps({
                "role": role,
                "context": context,
                "source_module": source_module,
                "draft_policy": "draft_always_no_autoreply_when_matched",
                "auto_delete_after_hours": 24,
                "expires_at": expires_at.isoformat(),
            })

            try:
                new_id = await fetch_val(
                    """
                    INSERT INTO fazle_draft_replies
                        (source, recipient, reply_text, intent, status, created_at, meta)
                    VALUES ($1, $2, $3, $4, 'pending', NOW(), $5::jsonb)
                    RETURNING id
                    """,
                    bridge, sender, draft_text, intent, meta,
                )
            except Exception as exc:
                log.error(
                    "[draft_reply] INSERT failed sender=%s bridge=%s module=%s: %s",
                    sender, bridge, source_module, exc,
                )
                return None

            log.info(
                "[draft_reply] CREATED id=%s sender=%s bridge=%s intent=%s module=%s",
                new_id, sender, bridge, intent, source_module,
            )
            return int(new_id) if new_id is not None else None

    except Exception as exc:
        # locked() itself raised (e.g. DB unavailable before acquiring)
        log.error(
            "[draft_reply] lock context failed sender=%s bridge=%s module=%s: %s",
            sender, bridge, source_module, exc,
        )
        return None


# ── Usage example (for testing) ───────────────────────────────────────────────
#
# from shared.draft_reply import create_draft_reply
#
# draft_id = await create_draft_reply(
#     sender="8801XXXXXXXXX",
#     bridge="bridge1",
#     draft_text="Salary is processing...",
#     role="employee",
#     intent="salary_query",
#     source_module="message_router"
# )
# if draft_id is None:
#     print("Duplicate suppressed or lock missed")
# else:
#     print(f"Created draft {draft_id}")
