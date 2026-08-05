"""
shared.queue — Central message ingestion queue helpers.

Wraps the fazle_message_queue table introduced in migration 009.

Usage
-----
  # Enqueue an inbound WhatsApp message:
  msg_id = await enqueue_message("bridge1", "+8801XXXXXXXXX", "text", "Hello")

  # Pull a batch for processing (uses SELECT FOR UPDATE SKIP LOCKED):
  batch = await dequeue_batch(limit=10, processor_id="worker-1")

  # After processing:
  await ack_message(msg_id)              # success
  await fail_message(msg_id, "timeout")  # retry later
  await dead_letter(msg_id, "parse fail") # permanent failure

All functions are safe: they never raise, always return a sane default.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional

from app.database import fetch_one, fetch_val, fetch_all, execute

log = logging.getLogger("fazle.shared.queue")

MAX_ATTEMPTS = 3   # after this many failures the message is dead-lettered


# ── Enqueue ───────────────────────────────────────────────────────────────────

async def enqueue_message(
    source:       str,
    sender_phone: str,
    message_type: str = "text",
    content_text: Optional[str] = None,
    media_url:    Optional[str] = None,
    media_id:     Optional[str] = None,
    direction:    str = "inbound",
    priority:     int = 5,
    idempotency_key: Optional[str] = None,
    extra:        Optional[dict] = None,
) -> Optional[int]:
    """
    Add a message to the queue.

    Automatically derives an idempotency_key from (source, sender, text) when
    none is supplied — preventing double-enqueue on bridge retries.

    Returns the new row id, or None on error / duplicate.
    """
    if not idempotency_key:
        raw = f"{source}|{sender_phone}|{content_text or ''}|{media_id or ''}"
        idempotency_key = hashlib.sha256(raw.encode()).hexdigest()[:32]

    try:
        row_id = await fetch_val(
            """
            INSERT INTO fazle_message_queue
                (source, sender_phone, direction, message_type,
                 content_text, media_url, media_id,
                 idempotency_key, status, extra)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending', $9)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            source, sender_phone, direction, message_type,
            content_text, media_url, media_id,
            idempotency_key,
            json.dumps(extra, ensure_ascii=False) if extra else None,
        )
        if row_id:
            log.debug(f"[queue] enqueued id={row_id} src={source} sender={sender_phone}")
        return row_id
    except Exception as e:
        log.error(f"[queue] enqueue_message error: {e}")
        return None


# ── Dequeue ───────────────────────────────────────────────────────────────────

async def dequeue_batch(
    limit:        int = 10,
    processor_id: str = "default",
    max_attempts: int = MAX_ATTEMPTS,
) -> list[dict]:
    """
    Atomically claim up to `limit` pending messages for processing.

    Uses SELECT FOR UPDATE SKIP LOCKED for safe concurrent workers.
    Returns list of row dicts; returns [] on error.
    """
    try:
        rows = await fetch_all(
            """
            WITH claimed AS (
                SELECT id FROM fazle_message_queue
                WHERE  status   = 'pending'
                  AND  attempts < $1
                ORDER BY enqueued_at
                LIMIT  $2
                FOR UPDATE SKIP LOCKED
            )
            UPDATE fazle_message_queue m
            SET    status       = 'processing',
                   attempts     = attempts + 1,
                   processor_id = $3
            FROM   claimed
            WHERE  m.id = claimed.id
            RETURNING m.*
            """,
            max_attempts, limit, processor_id,
        )
        return [dict(r) for r in rows]
    except Exception as e:
        log.error(f"[queue] dequeue_batch error: {e}")
        return []


# ── Ack / fail / dead-letter ──────────────────────────────────────────────────

async def ack_message(msg_id: int) -> bool:
    """Mark a message as successfully processed."""
    try:
        await execute(
            """UPDATE fazle_message_queue
               SET    status = 'done', processed_at = NOW()
               WHERE  id = $1""",
            msg_id,
        )
        return True
    except Exception as e:
        log.error(f"[queue] ack_message({msg_id}) error: {e}")
        return False


async def fail_message(msg_id: int, error: str = "") -> bool:
    """
    Return a message to 'pending' for retry (if attempts < MAX_ATTEMPTS).
    After MAX_ATTEMPTS the message is dead-lettered automatically on next dequeue.
    """
    try:
        await execute(
            """UPDATE fazle_message_queue
               SET    status     = 'pending',
                      last_error = $2
               WHERE  id = $1""",
            msg_id, error[:500],
        )
        return True
    except Exception as e:
        log.error(f"[queue] fail_message({msg_id}) error: {e}")
        return False


async def dead_letter(msg_id: int, reason: str = "") -> bool:
    """Permanently park a message as unprocessable."""
    try:
        await execute(
            """UPDATE fazle_message_queue
               SET    status     = 'failed',
                      last_error = $2,
                      processed_at = NOW()
               WHERE  id = $1""",
            msg_id, reason[:500],
        )
        return True
    except Exception as e:
        log.error(f"[queue] dead_letter({msg_id}) error: {e}")
        return False


# ── Bridge heartbeat ──────────────────────────────────────────────────────────

async def record_heartbeat(
    bridge_id:    str,
    bridge_label: str = "",
    last_msg_id:  Optional[str] = None,
    extra:        Optional[dict] = None,
) -> bool:
    """
    Upsert a heartbeat row for the given bridge.
    Call this each time a bridge successfully delivers a message.
    """
    try:
        await execute(
            """
            INSERT INTO fazle_bridge_heartbeats
                (bridge_id, bridge_label, last_seen_at, last_msg_id, status, extra)
            VALUES ($1, $2, NOW(), $3, 'ok', $4)
            ON CONFLICT (bridge_id) DO UPDATE
               SET last_seen_at = EXCLUDED.last_seen_at,
                   last_msg_id  = COALESCE(EXCLUDED.last_msg_id, fazle_bridge_heartbeats.last_msg_id),
                   status       = 'ok',
                   extra        = COALESCE(EXCLUDED.extra, fazle_bridge_heartbeats.extra)
            """,
            bridge_id,
            bridge_label or bridge_id,
            last_msg_id,
            json.dumps(extra, ensure_ascii=False) if extra else None,
        )
        return True
    except Exception as e:
        log.error(f"[queue] record_heartbeat({bridge_id}) error: {e}")
        return False


# Per-bridge stale-threshold overrides, in minutes (2026-08-05, Owner
# decision). Design mismatch this fixes: bridge1/bridge2/bridge3 heartbeat
# on every successful POLL CYCLE (modules.bridge_poller -- a transport/
# liveness signal, updated whether or not new messages exist), but "meta"
# only heartbeats when a real inbound WhatsApp message actually arrives via
# the Meta webhook (app.main._handle_meta_message -- a traffic signal, not
# a liveness signal; there is no periodic "webhook endpoint is reachable"
# check for it). meta is confirmed legitimately low/idle-traffic, so
# sharing bridge1/2/3's short threshold produced a false-positive "stale"
# alert every 5 minutes once meta crossed 10 minutes of no real messages --
# which, given its normal traffic pattern, is effectively always. 30 days
# chosen because meta's last real message was already >8 days old at
# decision time (a 24h/7d threshold would not have stopped the existing
# noise immediately). Any bridge_id not listed here keeps using the
# `stale_minutes` argument passed in by its caller, unchanged.
_STALE_THRESHOLD_OVERRIDES_MINUTES: dict[str, int] = {
    "meta": 30 * 24 * 60,  # 43200 minutes = 30 days
}


async def get_stale_bridges(stale_minutes: int = 10) -> list[dict]:
    """
    Return bridges whose last heartbeat is older than `stale_minutes` --
    except any bridge_id in _STALE_THRESHOLD_OVERRIDES_MINUTES, which uses
    its own threshold instead (see that dict's comment). Every caller
    (modules.scheduler.job_bridge_watchdog -- both its native alert and its
    Phase 5B Hermes-investigation trigger read the same returned list, so
    both are corrected by this one change -- and
    modules.fazle_payroll_engine.diagnostics._check_and_alert_stale_bridges)
    is covered automatically, with no per-caller change needed.
    """
    try:
        rows = await fetch_all(
            """
            SELECT bridge_id, bridge_label, last_seen_at, status,
                   EXTRACT(EPOCH FROM (NOW() - last_seen_at))::INT AS seconds_ago
            FROM   fazle_bridge_heartbeats
            ORDER BY last_seen_at
            """
        )
        stale = []
        for r in rows:
            row = dict(r)
            threshold_minutes = _STALE_THRESHOLD_OVERRIDES_MINUTES.get(row["bridge_id"], stale_minutes)
            if row["seconds_ago"] >= threshold_minutes * 60:
                stale.append(row)
        return stale
    except Exception as e:
        log.error(f"[queue] get_stale_bridges error: {e}")
        return []
