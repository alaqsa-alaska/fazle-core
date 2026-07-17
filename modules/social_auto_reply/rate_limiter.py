"""Persistent send rate limiter for social auto-reply."""
from __future__ import annotations

import os
import random
from datetime import datetime, timezone

from app.database import execute, fetch_one


def _delay_seconds() -> int:
    low = int(os.getenv("SOCIAL_REPLY_DELAY_MIN_S", "30"))
    high = int(os.getenv("SOCIAL_REPLY_DELAY_MAX_S", "45"))
    high = max(high, low)
    return random.randint(low, high)


async def can_send(channel: str = "global") -> bool:
    row = await fetch_one(
        "SELECT next_allowed_at FROM social_rate_limit_state WHERE channel=$1",
        channel,
    )
    if not row or not row.get("next_allowed_at"):
        return True
    return row["next_allowed_at"] <= datetime.now(timezone.utc)


async def mark_sent(channel: str = "global") -> int:
    delay = _delay_seconds()
    await execute(
        """
        INSERT INTO social_rate_limit_state
            (channel, next_allowed_at, last_sent_at, sent_count_window, window_started_at)
        VALUES ($1, NOW() + ($2::text || ' seconds')::interval, NOW(), 1, NOW())
        ON CONFLICT (channel) DO UPDATE SET
            next_allowed_at = EXCLUDED.next_allowed_at,
            last_sent_at = NOW(),
            sent_count_window = CASE
                WHEN social_rate_limit_state.window_started_at < NOW() - INTERVAL '1 hour' THEN 1
                ELSE social_rate_limit_state.sent_count_window + 1
            END,
            window_started_at = CASE
                WHEN social_rate_limit_state.window_started_at < NOW() - INTERVAL '1 hour' THEN NOW()
                ELSE social_rate_limit_state.window_started_at
            END
        """,
        channel,
        str(delay),
    )
    return delay
