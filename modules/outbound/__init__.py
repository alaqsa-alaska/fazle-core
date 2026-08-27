"""
Fazle Core — Persistent Outbound Queue (Batch 15.1 + 15.2)

Tables: fazle_outbound_queue
- enqueue() with idempotency_key dedup
- sweep_once() polls due rows, sends via bridge, retries with exp backoff, → DLQ at max_attempts
- start_background_worker() lifespan task
- Circuit breaker integration: opening enqueues a one-per-minute alert to admin
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

from app.database import execute, fetch_one, fetch_all, fetch_val, get_pool
from app.error_log import record_error

log = logging.getLogger("fazle.outbound")

_worker_task: Optional[asyncio.Task] = None


def _outbound_enabled() -> bool:
    return os.getenv("OUTBOUND_ENABLED", "false").lower() in ("1", "true", "yes")


def _admin_number() -> Optional[str]:
    raw = os.getenv("ADMIN_NUMBERS", "").strip()
    if not raw:
        return None
    return raw.split(",")[0].strip() or None


_META_CHANNELS = {"meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}


def _meta_outbound_log(event: str, *, source_bridge: str, **fields: Any) -> None:
    """Meta-channel-scoped observability event (Owner directive,
    2026-08-27, Section 1) -- channel-gated so bridge1/bridge2/bridge3
    sends (the vast majority of this generic worker's traffic) stay
    completely unaffected; only messenger/instagram/meta* rows emit
    anything here. Matches app.main._meta_log_event's format
    ("[META_EVENT] event=... channel=...") so both are grep-able the same
    way. Never logs recipient/body/tokens -- queue id, status, and error
    class only."""
    if source_bridge not in _META_CHANNELS:
        return
    parts = [f"event={event}", f"channel={source_bridge}"]
    parts += [f"{k}={v}" for k, v in fields.items() if v not in (None, "")]
    log.info("[META_EVENT] " + " ".join(parts))


async def enqueue(
    recipient: str,
    body: str,
    *,
    source_bridge: str = "bridge2",
    fallback_channel: Optional[str] = None,
    purpose: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Insert into queue. Returns id, or None if dedup'd by idempotency_key."""
    import json
    # Phase 7 (2026-08-26): Generic no-duplicate-answer invariant.
    # Read-only check for a substantially similar recent reply to the same
    # recipient + same purpose within the dedup window. Emits a structured
    # warning log so the caller (Phase 5/6 informational handlers, Hermes
    # dispatch) can include "you already said this" in the LLM prompt on
    # the next iteration. Does NOT block the current send — the existing
    # idempotency_key dedup is the authoritative gate. Falls closed to
    # "no recent reply" on any DB error.
    try:
        if purpose and _was_recently_replied(recipient, purpose, body):
            log.warning(
                "[OUTBOUND_PHASE7] similar recent reply recipient=%s purpose=%s "
                "dedup_window_minutes=%s (informational, NOT blocking send)",
                recipient, purpose, _PHASE7_DEDUP_MINUTES,
            )
    except Exception as _phase7_exc:  # noqa: BLE001
        log.debug("[OUTBOUND_PHASE7] similar-reply check skipped (non-fatal): %s", _phase7_exc)

    meta_json = json.dumps(meta or {})
    row = await fetch_one(
        """INSERT INTO fazle_outbound_queue
              (recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json)
           VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
           ON CONFLICT (idempotency_key) DO NOTHING
           RETURNING id""",
        recipient, body, source_bridge, fallback_channel, purpose, idempotency_key, meta_json,
    )
    qid = row["id"] if row else None
    if qid:
        log.info(f"[OUTBOUND_ENQUEUE] id={qid} recipient={recipient} bridge={source_bridge} purpose={purpose} key={idempotency_key} body={body[:60]!r}")
    else:
        log.info(f"[OUTBOUND_ENQUEUE] dedup_skip recipient={recipient} key={idempotency_key}")
    return qid


# Phase 7 (2026-08-26): reply-invariant helper. Window is intentionally
# generous so genuinely different replies aren't flagged; tighten only if
# the structured-log noise becomes operationally annoying.
_PHASE7_DEDUP_MINUTES = 10


def _normalize_reply_for_compare(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation. Used only for
    similarity comparison — never reaches candidate-facing text."""
    import re as _re
    s = (text or "").strip().lower()
    s = _re.sub(r"\s+", " ", s)
    s = _re.sub(r"[।.,?!:;]", "", s)
    return s.strip()


async def _was_recently_replied(recipient: str, purpose: str, candidate_body: str) -> bool:
    """Return True iff a substantially similar outbound reply was queued
    to the same recipient + same purpose within the dedup window.

    Read-only: queries fazle_outbound_queue + fazle_draft_replies
    history. Never blocks; caller decides whether to act on the warning.
    Returns False (no recent reply) on any error.
    """
    try:
        # Look at the last few outbound replies to this recipient + purpose
        rows = await fetch_all(
            """SELECT body, created_at FROM fazle_outbound_queue
               WHERE recipient = $1 AND purpose = $2
                 AND created_at > NOW() - make_interval(mins => $3)
               ORDER BY created_at DESC LIMIT 5""",
            recipient, purpose, _PHASE7_DEDUP_MINUTES,
        )
        norm_candidate = _normalize_reply_for_compare(candidate_body)
        if not norm_candidate:
            return False
        for r in rows:
            if _normalize_reply_for_compare(r.get("body") or "") == norm_candidate:
                return True
        return False
    except Exception:  # noqa: BLE001 — fail closed to False
        return False


async def pending_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status IN ('pending','failed')"
    ) or 0)


async def dlq_count() -> int:
    return int(await fetch_val(
        "SELECT COUNT(*) FROM fazle_outbound_queue WHERE status = 'dlq'"
    ) or 0)


async def actionable_dlq_count() -> int:
    """Recent business DLQ only; terminal alert/history rows are informational."""
    return int(await fetch_val(
        """SELECT COUNT(*) FROM fazle_outbound_queue
           WHERE status='dlq'
             AND created_at >= NOW() - INTERVAL '24 hours'
             AND COALESCE(purpose, '') NOT IN ('dlq-alert', 'health-summary', 'circuit-alert')"""
    ) or 0)


_ALERT_PIPELINE_PURPOSES = ("dlq-alert", "health-summary", "circuit-alert")


async def list_dlq_entries(limit: int = 20) -> list[dict]:
    """Real dead-lettered outbound WhatsApp sends -- the same table
    dlq_count()/actionable_dlq_count() above already summarize, now with
    actual rows for diagnosis (2026-08-20 fix: fazle-mcp's get_dlq_status
    has called GET /api/outbound/dead-letters since 2026-08-15 expecting
    exactly this, but no fazle-core route backed it -- 404 every time).
    `actionable` flags the same "recent, not an alert-pipeline artifact"
    distinction actionable_dlq_count() already uses, per-row, so a caller
    can tell a real stuck business message apart from the alert pipeline's
    own historical noise without re-deriving the filter itself."""
    rows = await fetch_all(
        """SELECT id, recipient, purpose, source_bridge, attempts, max_attempts,
                  last_error, created_at, updated_at,
                  (created_at >= NOW() - INTERVAL '24 hours'
                   AND COALESCE(purpose, '') NOT IN ($1, $2, $3)) AS actionable
           FROM fazle_outbound_queue
           WHERE status = 'dlq'
           ORDER BY updated_at DESC
           LIMIT $4""",
        *_ALERT_PIPELINE_PURPOSES, limit,
    )
    return [dict(r) for r in rows]


async def _send_with_channel(source_bridge: str, recipient: str, body: str) -> str:
    """Send through a supported channel and return an external delivery id."""
    if source_bridge in {"meta", "meta_whatsapp", "messenger", "instagram", "facebook_comment"}:
        from modules.social_auto_reply.send_queue import (
            _send_comment, _send_instagram, _send_messenger, _send_meta_whatsapp,
        )
        if source_bridge in {"meta", "meta_whatsapp"}:
            return await _send_meta_whatsapp(recipient, body)
        if source_bridge == "messenger":
            return await _send_messenger(recipient, body)
        if source_bridge == "instagram":
            return await _send_instagram(recipient, body)
        return await _send_comment(recipient, body)

    from app.bridge import get_bridge1, get_bridge2, get_bridge3, BridgeSendError
    if source_bridge == "bridge2":
        client = get_bridge2()
    elif source_bridge == "bridge3":
        client = get_bridge3()
    else:
        client = get_bridge1()

    if not client.breaker.allow():
        log.warning(f"[OUTBOUND_SEND_FAIL] circuit_open bridge={source_bridge} recipient={recipient}")
        raise BridgeSendError(f"circuit_open:{source_bridge}")

    log.info(f"[OUTBOUND_SEND_START] bridge={source_bridge} recipient={recipient} body={body[:60]!r}")
    try:
        await client.send_strict(recipient, body)
        client.breaker.record_success()
        log.info(f"[OUTBOUND_SEND_SUCCESS] bridge={source_bridge} recipient={recipient}")
        return f"{source_bridge}:{recipient}"
    except BridgeSendError as exc:
        just_opened = client.breaker.record_failure()
        log.error(f"[OUTBOUND_SEND_FAIL] bridge={source_bridge} recipient={recipient} error={exc}")
        if just_opened:
            await _alert_circuit_open(source_bridge)
        raise


async def _alert_circuit_open(source_bridge: str) -> None:
    """Enqueue a one-per-minute alert to admin when a breaker opens."""
    admin = _admin_number()
    if not admin:
        return
    from datetime import datetime
    minute = datetime.utcnow().strftime("%Y%m%d%H%M")
    key = f"circuit-open-{source_bridge}-{minute}"
    other = "bridge1" if source_bridge == "bridge2" else "bridge2"
    body = f"⚠️ ALERT: {source_bridge} circuit OPEN. Outbound paused 60s. Investigate."
    try:
        await enqueue(admin, body,
                      source_bridge=other,  # send via the other bridge
                      purpose="circuit-alert",
                      idempotency_key=key)
    except Exception as e:
        log.warning(f"[outbound] failed to enqueue circuit alert: {e}")


async def sweep_once(limit: int = 20) -> dict:
    """Pick up to N due rows, send them, update status. Returns counts."""
    if not _outbound_enabled():
        return {"picked": 0, "sent": 0, "failed": 0, "dlq": 0, "paused": True}
    pool = get_pool()
    sent = failed = dlq = 0
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """SELECT id, recipient, body, source_bridge, fallback_channel, attempts, max_attempts
                     FROM fazle_outbound_queue
                    WHERE status IN ('pending','failed')
                      AND next_retry_at <= NOW()
                    ORDER BY next_retry_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT $1""",
                limit,
            )
            ids = [r["id"] for r in rows]
            if ids:
                await conn.execute(
                    "UPDATE fazle_outbound_queue SET status='sending', locked_at=NOW(), updated_at=NOW() "
                    "WHERE id = ANY($1::bigint[])",
                    ids,
                )

        # Process outside the lock transaction; each result is its own update
        for r in rows:
            rid = r["id"]
            attempts = r["attempts"] + 1
            try:
                try:
                    external_id = await _send_with_channel(
                        r["source_bridge"], r["recipient"], r["body"]
                    )
                except Exception:
                    fallback = r.get("fallback_channel")
                    if not fallback:
                        raise
                    log.warning(
                        "[OUTBOUND_FAILOVER] id=%s from=%s to=%s",
                        rid, r["source_bridge"], fallback,
                    )
                    external_id = await _send_with_channel(
                        fallback, r["recipient"], r["body"]
                    )
                await conn.execute(
                    """UPDATE fazle_outbound_queue
                          SET status='sent', attempts=$2, sent_at=NOW(), last_error=NULL,
                              external_id=$3, locked_at=NULL, updated_at=NOW()
                        WHERE id=$1""",
                    rid, attempts, external_id,
                )
                sent += 1
                _meta_outbound_log("meta_outbound_sent", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts)
            except Exception as e:
                err = str(e)[:500]
                if attempts >= r["max_attempts"]:
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='dlq', attempts=$2, last_error=$3,
                                  locked_at=NULL, updated_at=NOW()
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    dlq += 1
                    await record_error("outbound.dlq", e)
                    _meta_outbound_log("meta_outbound_dlq", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts, error_class=type(e).__name__)
                else:
                    # Exponential backoff: 1m, 2m, 4m, ...
                    await conn.execute(
                        """UPDATE fazle_outbound_queue
                              SET status='failed', attempts=$2::integer, last_error=$3,
                                  next_retry_at = NOW() + (
                                      INTERVAL '1 minute' * power(2::numeric, $2::integer)
                                  )
                                  , locked_at=NULL, updated_at=NOW()
                            WHERE id=$1""",
                        rid, attempts, err,
                    )
                    failed += 1
                    _meta_outbound_log("meta_outbound_failed", source_bridge=r["source_bridge"], queue_id=rid, attempts=attempts, error_class=type(e).__name__)
    return {"picked": len(rows), "sent": sent, "failed": failed, "dlq": dlq}


async def _worker_loop(interval_seconds: int) -> None:
    log.info(f"[outbound] worker started (interval={interval_seconds}s, enabled={_outbound_enabled()})")
    while True:
        try:
            # Never replay an old `sending` row automatically. A prior process
            # may have delivered it before losing its acknowledgement.
            await sweep_once(limit=20)
        except asyncio.CancelledError:
            log.info("[outbound] worker cancelled")
            raise
        except Exception as e:
            log.exception(f"[outbound] sweep error: {e}")
            await record_error("outbound.sweep", e)
        await asyncio.sleep(interval_seconds)


def start_background_worker(interval_seconds: int = 10) -> asyncio.Task:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    interval = int(os.getenv("OUTBOUND_SWEEP_INTERVAL_S", str(interval_seconds)))
    _worker_task = asyncio.create_task(_worker_loop(interval), name="outbound-worker")
    return _worker_task


async def stop_background_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except (asyncio.CancelledError, Exception):
        pass
    _worker_task = None
