"""Thread history helpers for social auto-reply planning."""
from __future__ import annotations

from app.database import fetch_all, fetch_one

from . import state_tracker


def thread_target(platform: str, sender_id: str | None, comment_id: str | None = None) -> str:
    if platform == "facebook_comment" and comment_id:
        return comment_id
    return sender_id or ""


async def pending_threads(compression_seconds: int, limit: int = 20) -> list[dict]:
    # Backlog-flood guard (2026-07-15) -- never sweep events older than the
    # daemon's first-activation watermark. See state_tracker.get_activation_watermark().
    watermark = await state_tracker.get_activation_watermark()
    return await fetch_all(
        """
        SELECT platform,
               CASE WHEN platform='facebook_comment' THEN COALESCE(comment_id, sender_id, '')
                    ELSE COALESCE(sender_id, '') END AS target_id,
               MIN(received_at) AS first_received_at,
               COUNT(*) AS pending_count
        FROM social_inbox_events
        WHERE reply_status='pending'
          AND received_at <= NOW() - ($1::int * INTERVAL '1 second')
          AND received_at >= $3
        GROUP BY platform, target_id
        ORDER BY first_received_at
        LIMIT $2
        """,
        compression_seconds,
        limit,
        watermark,
    )


async def pending_events(platform: str, target_id: str) -> list[dict]:
    # Same backlog-flood guard applied here too, so a thread with a mix of
    # pre- and post-watermark pending events only ever claims the new ones.
    watermark = await state_tracker.get_activation_watermark()
    return await fetch_all(
        """
                WITH claimed AS (
                        SELECT id
                        FROM social_inbox_events
                        WHERE reply_status='pending'
                            AND platform=$1
                            AND CASE WHEN platform='facebook_comment' THEN COALESCE(comment_id, sender_id, '') ELSE COALESCE(sender_id, '') END = $2
                            AND received_at >= $3
                        ORDER BY received_at, id
                        FOR UPDATE SKIP LOCKED
                )
                UPDATE social_inbox_events e
                SET reply_status='planning'
                FROM claimed
                WHERE e.id=claimed.id
                RETURNING e.*
        """,
        platform,
        target_id,
        watermark,
    )


async def recent_thread_history(platform: str, target_id: str, limit: int = 30) -> list[dict]:
    return await fetch_all(
        """
        SELECT 'inbound' AS direction, message_text AS text, classification AS intent, received_at AS at, reply_status AS status
        FROM social_inbox_events
        WHERE platform=$1
          AND CASE WHEN platform='facebook_comment' THEN COALESCE(comment_id, sender_id, '') ELSE COALESCE(sender_id, '') END = $2
        UNION ALL
        SELECT 'outbound' AS direction, reply_text AS text, NULL AS intent, sent_at AS at, 'sent' AS status
        FROM social_sent_log
        WHERE platform=$1 AND target_id=$2
        ORDER BY at DESC
        LIMIT $3
        """,
        platform,
        target_id,
        limit,
    )


async def thread_state(platform: str, target_id: str) -> dict | None:
    return await fetch_one(
        "SELECT * FROM social_thread_state WHERE platform=$1 AND target_id=$2",
        platform,
        target_id,
    )
