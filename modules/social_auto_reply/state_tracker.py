"""Persistence helpers for social auto-reply state and admin status."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.database import execute, fetch_all, fetch_one, fetch_val


def daemon_enabled() -> bool:
    return os.getenv("SOCIAL_AUTO_REPLY_ENABLED", "false").lower() in {"1", "true", "yes"}


async def get_activation_watermark() -> datetime:
    """Backlog-flood guard (2026-07-15): the planner must never process
    historical pending events accumulated while the daemon was disabled --
    only see docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md Phase 4.

    Set once, the first time this is called after the daemon is actually
    un-paused (not on every restart -- ON CONFLICT DO NOTHING keeps the
    original value), and stable thereafter. Events received before this
    timestamp are treated as pre-activation backlog and are permanently
    excluded from planner sweeps, regardless of how many future
    restarts/re-activations happen.
    """
    row = await fetch_one(
        "SELECT last_checked_at FROM social_backlog_state WHERE state_key='activation_watermark'"
    )
    if row and row.get("last_checked_at"):
        return row["last_checked_at"]
    now = datetime.now(timezone.utc)
    await execute(
        """
        INSERT INTO social_backlog_state (state_key, platform, last_checked_at, metadata)
        VALUES ('activation_watermark', 'global', $1,
                '{"reason": "first_daemon_activation_backlog_cutoff"}'::jsonb)
        ON CONFLICT (state_key) DO NOTHING
        """,
        now,
    )
    # Re-read in case a concurrent caller inserted first -- their value wins.
    row = await fetch_one(
        "SELECT last_checked_at FROM social_backlog_state WHERE state_key='activation_watermark'"
    )
    return row["last_checked_at"] if row and row.get("last_checked_at") else now


async def set_paused(paused: bool, reason: str = "manual") -> None:
    await execute(
        """
        INSERT INTO social_backlog_state (state_key, platform, metadata, last_checked_at)
        VALUES ('daemon_paused', 'global', $1::jsonb, NOW())
        ON CONFLICT (state_key) DO UPDATE SET metadata=EXCLUDED.metadata, last_checked_at=NOW()
        """,
        json.dumps({"paused": paused, "reason": reason}),
    )


async def is_paused() -> bool:
    if not daemon_enabled():
        return True
    row = await fetch_one("SELECT metadata FROM social_backlog_state WHERE state_key='daemon_paused'")
    if not row:
        return False
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    return bool(meta.get("paused"))


async def status() -> dict[str, Any]:
    pending = await fetch_val("SELECT COUNT(*) FROM social_reply_queue WHERE status='pending'")
    failed = await fetch_val("SELECT COUNT(*) FROM social_reply_queue WHERE status='failed'")
    flagged = await fetch_val("SELECT COUNT(*) FROM social_flagged_items WHERE status='open'")
    sent = await fetch_val("SELECT COUNT(*) FROM social_sent_log WHERE sent_at >= NOW() - INTERVAL '24 hours'")
    rate = await fetch_one("SELECT * FROM social_rate_limit_state WHERE channel='global'")
    return {
        "enabled": daemon_enabled(),
        "paused": await is_paused(),
        "pending": int(pending or 0),
        "failed": int(failed or 0),
        "flagged_open": int(flagged or 0),
        "sent_24h": int(sent or 0),
        "rate_limit": dict(rate) if rate else None,
    }


async def queue_rows(limit: int = 50) -> list[dict]:
    return await fetch_all(
        """
        SELECT id, platform, target_id, intent, status, attempts, next_retry_at, last_error, created_at
        FROM social_reply_queue
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )


async def flagged_rows(limit: int = 50) -> list[dict]:
    return await fetch_all(
        """
        SELECT id, platform, target_id, reason, severity, message_text, status, created_at
        FROM social_flagged_items
        ORDER BY created_at DESC
        LIMIT $1
        """,
        limit,
    )
