"""Social auto-reply backend extension for fazle-core."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.database import execute, fetch_one

from .classifier import classify_message
from .message_deduplicator import event_key
from .risk_flagger import is_recruiting_intent, is_escalation_intent

log = logging.getLogger("fazle.social")

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS social_inbox_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    platform TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sender_id TEXT,
    sender_name TEXT,
    conversation_id TEXT,
    message_id TEXT,
    comment_id TEXT,
    parent_id TEXT,
    message_text TEXT,
    media_flag BOOLEAN NOT NULL DEFAULT FALSE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    reply_status TEXT NOT NULL DEFAULT 'pending',
    classification TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_social_inbox_platform_received ON social_inbox_events(platform, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_inbox_reply_status ON social_inbox_events(reply_status);

CREATE TABLE IF NOT EXISTS social_reply_queue (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES social_inbox_events(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    target_id TEXT NOT NULL,
    conversation_id TEXT,
    reply_to_comment_id TEXT,
    source_bridge TEXT,
    reply_text TEXT NOT NULL,
    intent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_social_reply_queue_status_due ON social_reply_queue(status, next_retry_at);

CREATE TABLE IF NOT EXISTS social_sent_log (
    id BIGSERIAL PRIMARY KEY,
    queue_id BIGINT,
    event_id BIGINT,
    platform TEXT NOT NULL,
    target_id TEXT NOT NULL,
    external_id TEXT,
    reply_text TEXT NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS social_retry_queue (
    id BIGSERIAL PRIMARY KEY,
    queue_id BIGINT NOT NULL UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS social_flagged_items (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT REFERENCES social_inbox_events(id) ON DELETE SET NULL,
    platform TEXT NOT NULL,
    target_id TEXT,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'manual_review',
    message_text TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_social_flagged_status ON social_flagged_items(status, created_at DESC);

CREATE TABLE IF NOT EXISTS social_backlog_state (
    state_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    last_cursor TEXT,
    last_checked_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS social_rate_limit_state (
    channel TEXT PRIMARY KEY,
    next_allowed_at TIMESTAMPTZ,
    last_sent_at TIMESTAMPTZ,
    sent_count_window INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS social_thread_state (
    id BIGSERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    target_id TEXT NOT NULL,
    answered_intents TEXT[] NOT NULL DEFAULT ARRAY[]::text[],
    last_reply_text TEXT,
    context_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(platform, target_id)
);
"""


async def init_tables() -> None:
    for stmt in _split_sql(_INIT_SQL):
        await execute(stmt)
    log.info("[social] schema ready")


async def start_social_auto_reply() -> None:
    await init_tables()
    from .daemon_worker import start_background_worker
    start_background_worker()
    log.info("[social] startup complete")


async def stop_social_auto_reply() -> None:
    from .daemon_worker import stop_background_worker
    await stop_background_worker()
    log.info("[social] shutdown complete")


async def ingest_social_event(
    *,
    platform: str,
    event_type: str,
    sender_id: str,
    sender_name: str = "",
    text: str = "",
    conversation_id: str | None = None,
    message_id: str | None = None,
    comment_id: str | None = None,
    parent_id: str | None = None,
    media_flag: bool = False,
    raw_payload: dict[str, Any] | None = None,
) -> int | None:
    external_id = message_id or comment_id
    key = event_key(platform=platform, event_type=event_type, external_id=external_id, sender_id=sender_id, text=text)
    try:
        row = await fetch_one(
            """
            INSERT INTO social_inbox_events
                (event_key, platform, event_type, sender_id, sender_name, conversation_id,
                 message_id, comment_id, parent_id, message_text, media_flag, raw_payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb)
            ON CONFLICT (event_key) DO NOTHING
            RETURNING id
            """,
            key,
            platform,
            event_type,
            sender_id,
            sender_name,
            conversation_id,
            message_id,
            comment_id,
            parent_id,
            text,
            media_flag,
            json.dumps(raw_payload or {}, ensure_ascii=False, default=str),
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UndefinedTableError":
            log.warning("[social] social_inbox_events missing; ingest skipped")
            return None
        raise
    if not row:
        return None
    event_id = int(row["id"])
    classification = classify_message(text, media_flag=media_flag, platform=platform)
    if is_escalation_intent(classification.intent):
        await execute("UPDATE social_inbox_events SET classification=$2, reply_status='needs_admin' WHERE id=$1", event_id, classification.intent)
        log.warning("ESCALATION | intent=%s sender=%s text=%.100s", classification.intent, sender_id, text)
        return event_id
    if not is_recruiting_intent(classification.intent):
        await execute("UPDATE social_inbox_events SET classification=$2, reply_status='ignored' WHERE id=$1", event_id, classification.intent)
        return event_id
    await execute("UPDATE social_inbox_events SET classification=$2 WHERE id=$1", event_id, classification.intent)
    return event_id


async def flag_event(event_id: int, *, platform: str, target_id: str | None, reason: str, text: str) -> None:
    await execute(
        """
        INSERT INTO social_flagged_items (event_id, platform, target_id, reason, message_text)
        VALUES ($1,$2,$3,$4,$5)
        """,
        event_id,
        platform,
        target_id,
        reason,
        text,
    )


def _split_sql(sql: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(current).strip().rstrip(";"))
            current = []
    if current:
        statements.append("\n".join(current).strip().rstrip(";"))
    return statements
