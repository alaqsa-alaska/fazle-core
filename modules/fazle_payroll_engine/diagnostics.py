"""
Fazle Payroll Engine — Processing diagnostics and bridge health monitoring.

Two responsibilities:
  1. record_outcome()   — write one row per processed message to
                          fpe_processing_diagnostics for latency + failure tracking.
  2. bridge_health_loop() — background worker that periodically checks bridge
                             ingestion gaps, skip ratios, DLQ depth, and retry
                             storms, publishing results to observability gauges
                             and emitting structured WARNING logs on alert.

Both are designed to be non-blocking: if the diagnostics table doesn't exist yet
(before migration runs) or the DB is temporarily unavailable, the error is logged
at DEBUG level and the main pipeline continues uninterrupted.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("fazle.fpe.diagnostics")

# ── Bridge health thresholds ──────────────────────────────────────────────────
_HEALTH_POLL_SECS    = 300      # run health checks every 5 minutes
_GAP_ALERT_MINS      = 30       # no new messages for this long → ALERT
_SKIP_RATIO_WARN     = 0.20     # >20% skip rate in last 1 h → WARN
_RETRY_STORM_THRESH  = 10       # >10 messages with attempts>2 → WARN
_DLQ_WARN_THRESH     = 20       # >20 messages in DLQ → WARN


# ── Timer context manager ─────────────────────────────────────────────────────

class Timer:
    """Measure elapsed wall-clock time in milliseconds.

    Usage — manual stop (preferred inside loop bodies with continue/except):
        t = Timer()
        ...work...
        elapsed = t.stop()

    Usage — context manager (works but elapsed is only set after __exit__):
        with Timer() as t:
            ...work...
        # t.elapsed_ms is now set
    """

    def __init__(self) -> None:
        self._start: float = time.monotonic()
        self.elapsed_ms: float = 0.0

    def stop(self) -> float:
        """Stop the timer and return elapsed ms (also stored in elapsed_ms)."""
        self.elapsed_ms = (time.monotonic() - self._start) * 1000.0
        return self.elapsed_ms

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ── Outcome recording ─────────────────────────────────────────────────────────

async def record_outcome(
    *,
    msg_id: int,
    msg_type: str,
    worker_name: str,
    status: str,                    # 'done' | 'skipped' | 'failed'
    failure_reason: Optional[str] = None,
    processing_ms: Optional[float] = None,
) -> None:
    """
    Insert one row into fpe_processing_diagnostics.

    Non-fatal: swallows all errors so the main pipeline is never blocked
    by a diagnostics write failure (missing table, DB hiccup, etc.).
    """
    try:
        from app.database import execute
        await execute(
            """
            INSERT INTO fpe_processing_diagnostics
                (fpe_wa_message_id, message_type, worker_name,
                 processing_status, failure_reason, processing_ms, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            """,
            msg_id,
            msg_type,
            worker_name,
            status,
            failure_reason,
            round(processing_ms, 2) if processing_ms is not None else None,
        )
    except Exception as exc:
        log.debug("[fpe.diagnostics] record_outcome skipped (table missing?): %s", exc)


# ── Bridge health worker ──────────────────────────────────────────────────────

async def bridge_health_loop() -> None:
    """
    Background worker: periodically runs ingestion-health checks and publishes
    to observability gauges.  Emits structured WARNING logs on alert conditions.

    Phase 10 additions:
    - Writes a heartbeat row to fazle_bridge_heartbeats after each cycle
      (shared.queue.record_heartbeat) so the scheduler can detect stale bridges.
    - Checks get_stale_bridges() to trigger reconnect-alert when a bridge has
      not delivered a message for > GAP_ALERT_MINS minutes.
    """
    log.info("[fpe.health] bridge health monitor started (poll=%ds)", _HEALTH_POLL_SECS)
    while True:
        try:
            await asyncio.sleep(_HEALTH_POLL_SECS)
            await _run_health_checks()
            await _check_and_alert_stale_bridges()
            await check_phase12_health()   # Phase 12I
        except asyncio.CancelledError:
            log.info("[fpe.health] bridge health monitor stopped")
            break
        except Exception as exc:
            log.error("[fpe.health] health check error: %s", exc, exc_info=True)


async def _check_and_alert_stale_bridges() -> None:
    """
    Phase 10: query fazle_bridge_heartbeats for bridges that have not emitted
    a heartbeat recently, and emit an alert log + optional admin notification.
    """
    try:
        from shared.queue import get_stale_bridges, record_heartbeat

        # Record that the FPE health worker itself is alive
        await record_heartbeat(
            bridge_id="fpe_health_worker",
        )

        stale = await get_stale_bridges(stale_minutes=_GAP_ALERT_MINS)
        for s in stale:
            bridge_id = s.get("bridge_id") or s.get("bridge_number") or str(s)
            last_seen = s.get("last_seen_at") or s.get("last_heartbeat_at") or "unknown"
            log.warning(
                "[fpe.health] STALE BRIDGE DETECTED bridge=%s last_seen=%s — "
                "check docker / bridge process; manual restart may be required",
                bridge_id, last_seen,
            )
            # Best-effort admin notification via outbound queue
            try:
                from shared.queue import enqueue_message
                await enqueue_message(
                    bridge_number="8801880446111",   # bridge2 = admin authority
                    recipient_jid="8801880446111@s.whatsapp.net",
                    text=(
                        f"⚠️ BRIDGE ALERT: {bridge_id} last seen {last_seen}.\n"
                        f"Ingestion gap > {_GAP_ALERT_MINS} min. "
                        f"Please verify and restart if needed."
                    ),
                    priority=5,
                    source="fpe_health_watchdog",
                )
            except Exception as notify_exc:
                log.debug("[fpe.health] admin notification failed: %s", notify_exc)
    except Exception as exc:
        log.warning("[fpe.health] _check_and_alert_stale_bridges error: %s", exc)


async def _run_health_checks() -> None:
    from app.database import fetch_all, fetch_val
    from modules import observability as obs
    from modules.fazle_payroll_engine.workers import MAX_ATTEMPTS

    # ── 1. Ingestion gap per bridge ───────────────────────────────────────────
    bridge_rows = await fetch_all(
        """
        SELECT source,
               EXTRACT(EPOCH FROM (now() - MAX(ingested_at))) / 60  AS gap_minutes,
               COUNT(*) FILTER (WHERE ingested_at > now() - INTERVAL '1 hour')  AS last_hour_count
        FROM fpe_wa_messages
        GROUP BY source
        """,
    )
    for r in bridge_rows:
        source    = r["source"] or "unknown"
        gap_min   = float(r["gap_minutes"] or 0)
        last_hour = int(r["last_hour_count"] or 0)
        obs.gauge("fpe_bridge_gap_minutes",      gap_min,          labels={"source": source})
        obs.gauge("fpe_bridge_last_hour_count",  float(last_hour), labels={"source": source})
        # Alert only when a bridge is truly idle for the full recent window.
        if gap_min > _GAP_ALERT_MINS and last_hour == 0:
            log.warning(
                "[fpe.health] ALERT ingestion_gap source=%s gap_minutes=%.1f last_hour=%d",
                source, gap_min, last_hour,
            )

    # ── 2. Skip ratio over last 1 hour ────────────────────────────────────────
    total_1h = int(await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE status IN ('done','skipped','failed') "
        "AND queued_at > now() - INTERVAL '1 hour'"
    ) or 0)
    skipped_1h = int(await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE status = 'skipped' "
        "AND queued_at > now() - INTERVAL '1 hour'"
    ) or 0)
    skip_ratio = skipped_1h / total_1h if total_1h > 0 else 0.0
    obs.gauge("fpe_skip_ratio_1h",   skip_ratio)
    obs.gauge("fpe_processed_1h",    float(total_1h))
    if total_1h >= 5 and skip_ratio > _SKIP_RATIO_WARN:
        log.warning(
            "[fpe.health] ALERT high_skip_ratio ratio=%.2f skipped=%d total=%d window=1h",
            skip_ratio, skipped_1h, total_1h,
        )

    # ── 3. Retry storm (messages stuck with attempts > 2) ────────────────────
    retry_storm = int(await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE attempts > 2 AND status NOT IN ('done','skipped')"
    ) or 0)
    obs.gauge("fpe_retry_storm_count", float(retry_storm))
    if retry_storm > _RETRY_STORM_THRESH:
        log.warning(
            "[fpe.health] ALERT retry_storm count=%d messages with attempts>2", retry_storm
        )

    # ── 4. DLQ depth (exhausted retries) ─────────────────────────────────────
    dlq = int(await fetch_val(
        "SELECT COUNT(*) FROM fpe_message_processing_state "
        "WHERE status='failed' AND attempts >= $1",
        MAX_ATTEMPTS,
    ) or 0)
    obs.gauge("fpe_dlq_backlog", float(dlq))
    if dlq > _DLQ_WARN_THRESH:
        log.warning("[fpe.health] ALERT dlq_backlog count=%d exhausted retries", dlq)

    # ── 5. Per-type skip stats (last 1 h) ─────────────────────────────────────
    try:
        type_stats = await fetch_all(
            """
            SELECT pr.message_type, mps.status, COUNT(*) AS n
            FROM fpe_message_processing_state mps
            JOIN fpe_parser_results pr ON pr.fpe_wa_message_id = mps.fpe_wa_message_id
            WHERE mps.queued_at > now() - INTERVAL '1 hour'
            GROUP BY pr.message_type, mps.status
            """,
        )
        for r in type_stats:
            obs.gauge(
                "fpe_type_status_count",
                float(r["n"]),
                labels={"message_type": r["message_type"] or "unknown", "status": r["status"]},
            )
    except Exception as exc:
        log.debug("[fpe.health] per-type stats query failed: %s", exc)

    log.debug(
        "[fpe.health] check done bridges=%d skip_ratio=%.2f dlq=%d retry_storm=%d",
        len(bridge_rows), skip_ratio, dlq, retry_storm,
    )


# ── Stats API for dashboard / metrics endpoints ───────────────────────────────

async def get_processing_stats(window_hours: int = 24) -> list[dict]:
    """
    Return per-message-type processing stats from fpe_processing_diagnostics.
    Falls back to an empty list if the table doesn't exist yet.
    """
    try:
        from app.database import fetch_all
        return await fetch_all(
            """
            SELECT
                message_type,
                processing_status,
                COUNT(*)                                                      AS count,
                ROUND(AVG(processing_ms)::numeric, 1)                        AS avg_ms,
                ROUND(
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY processing_ms)::numeric,
                    1
                )                                                             AS p95_ms
            FROM fpe_processing_diagnostics
            WHERE created_at > now() - ($1 || ' hours')::INTERVAL
            GROUP BY message_type, processing_status
            ORDER BY message_type, processing_status
            """,
            str(window_hours),
        )
    except Exception as exc:
        log.debug("[fpe.diagnostics] get_processing_stats failed: %s", exc)
        return []


# ── Phase 12I — Coordination layer metrics ───────────────────────────────────

async def get_phase12_metrics() -> dict:
    """
    Return a snapshot of Phase 12 Unified Request Coordination Layer metrics.

    Designed to be non-fatal — always returns a dict even on partial failures.
    Callers (dashboard /api/metrics, admin health check) can include this in
    their response without risking exceptions.

    Returns:
    {
        "event_bus":     {"emitted": N, "handled": N, "failed": N, "subscribers": {...}},
        "realtime_ws":   {"connected_clients": N, "client_ages_s": [...]},
        "write_router":  {"locks_held": N, "resources": [...]},
        "state_version": N,
    }
    """
    out: dict = {}

    # Event bus metrics
    try:
        from shared.events import get_metrics as _evt_metrics, get_subscriber_counts
        out["event_bus"] = {**_evt_metrics(), "subscribers": get_subscriber_counts()}
    except Exception as exc:
        log.debug("[phase12] event_bus metrics failed: %s", exc)
        out["event_bus"] = {}

    # Realtime WebSocket stats
    try:
        from shared.realtime import get_realtime_stats
        out["realtime_ws"] = get_realtime_stats()
    except Exception as exc:
        log.debug("[phase12] realtime_ws metrics failed: %s", exc)
        out["realtime_ws"] = {}

    # Write-router local lock stats
    try:
        from shared.write_router import get_local_lock_stats
        out["write_router"] = get_local_lock_stats()
    except Exception as exc:
        log.debug("[phase12] write_router metrics failed: %s", exc)
        out["write_router"] = {}

    # Current state version
    try:
        from shared.state_version import get_state_version
        out["state_version"] = await get_state_version()
    except Exception as exc:
        log.debug("[phase12] state_version metrics failed: %s", exc)
        out["state_version"] = 0

    return out


async def check_phase12_health() -> None:
    """
    Called from bridge_health_loop every cycle.

    Logs warnings when the coordination layer shows signs of stress:
    - Too many failed event handler calls
    - No realtime clients connected (may be normal off-hours, DEBUG only)
    - Write-router lock resource count unusually high (potential leak)
    """
    try:
        from modules import observability as obs
        m = await get_phase12_metrics()

        # Event bus
        eb = m.get("event_bus", {})
        failed_events = eb.get("failed", 0)
        emitted_events = eb.get("emitted", 0)
        obs.gauge("phase12_event_emitted",  float(emitted_events))
        obs.gauge("phase12_event_failed",   float(failed_events))
        if emitted_events > 0 and failed_events / max(emitted_events, 1) > 0.1:
            log.warning(
                "[phase12] high event handler failure rate failed=%d emitted=%d",
                failed_events, emitted_events,
            )

        # Realtime clients
        ws = m.get("realtime_ws", {})
        connected = ws.get("connected_clients", 0)
        obs.gauge("phase12_ws_clients", float(connected))
        log.debug("[phase12] realtime_ws connected_clients=%d", connected)

        # Write-router lock footprint
        wr = m.get("write_router", {})
        lock_count = wr.get("total_locks", 0)
        obs.gauge("phase12_wr_lock_count", float(lock_count))
        if lock_count > 500:
            log.warning(
                "[phase12] write_router lock registry unusually large count=%d "
                "(possible lock leak — check for abandoned resource locks)",
                lock_count,
            )

        # State version
        sv = m.get("state_version", 0)
        obs.gauge("phase12_state_version", float(sv))

    except Exception as exc:
        log.debug("[phase12] check_phase12_health error: %s", exc)
