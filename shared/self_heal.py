"""
shared.self_heal — Self-Healing Runtime Layer (Phase 13E)
=========================================================

Monitors operational health and executes automatic recovery actions
to prevent total orchestration collapse from any single failing module.

Monitored conditions
--------------------
  queue_stall      — outbound pending count above threshold
  stale_locks      — fazle_processing_locks TTL exceeded (cleaned automatically)
  ws_failure       — realtime heartbeat task dead
  bridge_outage    — bridge in outage state (from bridge_orchestrator)
  dead_worker      — outbound background worker task dead
  retry_storm      — outbound DLQ count above storm threshold
  stale_heartbeats — arbiter recovery task dead

Recovery actions
----------------
  restart_outbound_worker   — call modules.outbound.start_background_worker()
  restart_ws_broadcaster    — recreate realtime._heartbeat_task
  restart_arbiter_recovery  — call shared.queue_arbiter.start_arbiter_recovery()
  clean_expired_locks       — call shared.locks.cleanup_expired_locks()
  throttle_non_critical     — set _throttled flag; callers check is_throttled()
  emit_bridge_probe         — trigger immediate health probe on bridge_orchestrator

System pressure score
---------------------
Weighted sum of normalised pressure signals clamped to [0.0, 1.0]:

  bridge_outage   weight=0.30  (per bridge, max 1 bridge counted)
  dead_worker     weight=0.25
  queue_stall     weight=0.20  (linear: 0 at STALL_THRESHOLD, 1 at STORM_THRESHOLD)
  ws_failure      weight=0.10
  retry_storm     weight=0.10
  stale_locks     weight=0.05

Panic-safe mode
---------------
When pressure >= PANIC_THRESHOLD (0.85):
  * _panic_mode = True, _throttled = True
  * Emit SELF_HEAL_PANIC event
  * Callers must check is_panic_mode() before launching bulk / non-critical work

When pressure <= PANIC_CLEAR (0.60):
  * _panic_mode = False, _throttled = False
  * Emit SELF_HEAL_RECOVERED event

Graceful degradation
--------------------
  * Every check is wrapped in try/except — DB failure never aborts the loop
  * Max one recovery action per check cycle per component
  * Self-healer startup failure is non-fatal to the main app
  * Feature flag: SELF_HEAL_ENABLED=false (default true) disables all checks

Runtime recovery audit log
--------------------------
  In-process deque of last AUDIT_LOG_MAX entries.
  Each entry: {ts, action, detail, severity, pressure_at}
  Exposed via get_self_heal_diagnostics() and the /api/self-heal/diagnostics endpoint.

Public API
----------
  await start_self_healer()              # call in lifespan startup
  await stop_self_healer()               # call in lifespan teardown
  await get_self_heal_diagnostics()      # returns full diagnostics dict
  bool   is_panic_mode()                 # True when system under severe pressure
  float  get_pressure_score()            # current 0.0–1.0 score
  await  trigger_check_cycle()           # immediate check (for /api endpoint)

Do NOT import this at module level from places that run outside the FastAPI
app context — the recovery functions lazy-import their dependencies.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from typing import Any, Optional

log = logging.getLogger("fazle.self_heal")

# ── Constants ─────────────────────────────────────────────────────────────────

CHECK_INTERVAL_S: int = 30           # background check loop period
PANIC_THRESHOLD: float = 0.85        # enter panic-safe mode above this
PANIC_CLEAR: float = 0.60            # exit panic-safe mode below this
QUEUE_STALL_THRESHOLD: int = 50      # pending items → stall signal starts
QUEUE_STORM_THRESHOLD: int = 200     # pending items → full pressure (1.0)
RETRY_STORM_THRESHOLD: int = 20      # DLQ items → retry storm signal
LOCK_STALE_WARN: int = 10            # stale locks cleaned → pressure contribution
AUDIT_LOG_MAX: int = 200             # max entries in runtime audit log

# Pressure signal weights (must sum to ≤ 1.0)
_WEIGHTS: dict[str, float] = {
    "bridge_outage": 0.30,
    "dead_worker":   0.25,
    "queue_stall":   0.20,
    "ws_failure":    0.10,
    "retry_storm":   0.10,
    "stale_locks":   0.05,
}


def _self_heal_enabled() -> bool:
    return os.getenv("SELF_HEAL_ENABLED", "true").lower() not in ("0", "false", "no")


# ── SelfHealer ────────────────────────────────────────────────────────────────

class SelfHealer:
    """
    Single-instance orchestrator for runtime self-healing.
    Instantiate via the module-level singleton get_self_healer().
    """

    def __init__(self) -> None:
        self._started: bool = False
        self._panic_mode: bool = False
        self._throttled: bool = False
        self._pressure: float = 0.0
        self._last_signals: dict[str, float] = {}
        self._recovery_counts: dict[str, int] = {}
        self._audit_log: deque[dict[str, Any]] = deque(maxlen=AUDIT_LOG_MAX)
        self._check_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._check_task = asyncio.create_task(
            self._check_loop(), name="self_heal:check_loop"
        )
        log.info("[self_heal] started (interval=%ds)", CHECK_INTERVAL_S)

    async def stop(self) -> None:
        self._started = False
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        self._check_task = None
        log.info("[self_heal] stopped")

    # ── Background loop ───────────────────────────────────────────────────────

    async def _check_loop(self) -> None:
        """Run all health checks every CHECK_INTERVAL_S seconds."""
        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL_S)
                await self._run_all_checks()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("[self_heal] check loop error: %s", exc)

    async def _run_all_checks(self) -> None:
        """Collect pressure signals, compute score, trigger panic if needed."""
        if not _self_heal_enabled():
            return

        signals: dict[str, float] = {}

        # Each check returns a [0.0, 1.0] pressure contribution
        signals["dead_worker"]   = await self._check_worker_health()
        signals["ws_failure"]    = await self._check_websocket()
        signals["bridge_outage"] = await self._check_bridge_health()
        signals["queue_stall"]   = await self._check_queue_stall()
        signals["stale_locks"]   = await self._check_stale_locks()
        signals["retry_storm"]   = await self._check_retry_storm()

        self._last_signals = signals
        self._pressure = _compute_pressure(signals)

        log.debug(
            "[self_heal] pressure=%.3f signals=%s",
            self._pressure,
            {k: round(v, 2) for k, v in signals.items()},
        )

        await self._maybe_enter_panic()
        await self._maybe_exit_panic()

    # ── Individual checks ─────────────────────────────────────────────────────

    async def _check_worker_health(self) -> float:
        """Return 1.0 if outbound worker task is dead; 0.0 otherwise."""
        try:
            from modules import outbound as _q
            task = _q._worker_task
            if task is None or task.done():
                self._audit(
                    "dead_worker",
                    "outbound worker task dead — restarting",
                    "critical",
                )
                await self._recover_outbound_worker()
                return 1.0
            return 0.0
        except Exception as exc:
            log.debug("[self_heal] worker health check error: %s", exc)
            return 0.0

    async def _check_websocket(self) -> float:
        """Return 1.0 if realtime heartbeat task is dead; 0.0 otherwise."""
        try:
            from shared import realtime as _rt
            task = _rt._heartbeat_task
            if task is None or task.done():
                self._audit(
                    "ws_failure",
                    "realtime heartbeat task dead — restarting",
                    "warning",
                )
                await self._recover_ws_broadcaster()
                return 1.0
            return 0.0
        except Exception as exc:
            log.debug("[self_heal] websocket check error: %s", exc)
            return 0.0

    async def _check_bridge_health(self) -> float:
        """Return pressure based on bridge outage count from orchestrator."""
        try:
            from shared.bridge_orchestrator import get_orchestrator
            diag = get_orchestrator().get_diagnostics()
            bridges = diag.get("bridges", {})
            outage_count = sum(
                1 for b in bridges.values() if b.get("state") == "outage"
            )
            degraded_count = sum(
                1 for b in bridges.values() if b.get("state") == "degraded"
            )
            if outage_count > 0:
                self._audit(
                    "bridge_outage",
                    f"{outage_count} bridge(s) in outage state",
                    "warning",
                )
                # Trigger an immediate probe to accelerate recovery detection
                await self._probe_bridges()
                return min(float(outage_count), 1.0)  # 1 bridge → full weight

            if degraded_count > 0:
                return degraded_count * 0.15  # soft degradation signal

            return 0.0
        except Exception as exc:
            log.debug("[self_heal] bridge health check error: %s", exc)
            return 0.0

    async def _check_queue_stall(self) -> float:
        """Return linear pressure [0,1] based on outbound pending count."""
        try:
            from modules import outbound as _q
            pending = await _q.pending_count()

            if pending >= QUEUE_STORM_THRESHOLD:
                self._audit(
                    "queue_storm",
                    f"outbound pending={pending} >= storm threshold {QUEUE_STORM_THRESHOLD}",
                    "critical",
                )
                from shared.events import emit
                from shared.events import QUEUE_PRESSURE
                await emit(QUEUE_PRESSURE, {"pending": pending, "level": "storm"})
                return 1.0

            if pending >= QUEUE_STALL_THRESHOLD:
                self._audit(
                    "queue_stall",
                    f"outbound pending={pending} >= stall threshold {QUEUE_STALL_THRESHOLD}",
                    "warning",
                )
                from shared.events import emit
                from shared.events import QUEUE_PRESSURE
                await emit(QUEUE_PRESSURE, {"pending": pending, "level": "stall"})
                span = QUEUE_STORM_THRESHOLD - QUEUE_STALL_THRESHOLD
                return max(0.0, (pending - QUEUE_STALL_THRESHOLD) / span)

            return 0.0
        except Exception as exc:
            log.debug("[self_heal] queue stall check error: %s", exc)
            return 0.0  # fail-safe: no pressure on DB errors

    async def _check_stale_locks(self) -> float:
        """Clean expired locks; return small pressure if many were stale."""
        try:
            from shared.locks import cleanup_expired_locks
            n = await cleanup_expired_locks()
            if n >= LOCK_STALE_WARN:
                self._audit(
                    "stale_locks",
                    f"cleaned {n} expired processing locks",
                    "info",
                )
                return min(n / (LOCK_STALE_WARN * 4), 1.0)
            return 0.0
        except Exception as exc:
            log.debug("[self_heal] stale lock check error: %s", exc)
            return 0.0

    async def _check_retry_storm(self) -> float:
        """Return pressure based on outbound DLQ count."""
        try:
            from modules import outbound as _q
            dlq = await _q.actionable_dlq_count()
            if dlq >= RETRY_STORM_THRESHOLD:
                self._audit(
                    "retry_storm",
                    f"outbound DLQ count={dlq} >= threshold {RETRY_STORM_THRESHOLD}",
                    "warning",
                )
                return min(dlq / (RETRY_STORM_THRESHOLD * 2), 1.0)
            return 0.0
        except Exception as exc:
            log.debug("[self_heal] retry storm check error: %s", exc)
            return 0.0

    # ── Recovery actions ──────────────────────────────────────────────────────

    async def _recover_outbound_worker(self) -> None:
        """Restart the outbound background worker task."""
        try:
            from modules import outbound as _q
            _q.start_background_worker()
            self._recovery_counts["outbound_worker"] = (
                self._recovery_counts.get("outbound_worker", 0) + 1
            )
            self._audit(
                "recover_outbound_worker",
                f"worker restarted (count={self._recovery_counts['outbound_worker']})",
                "info",
            )
            from shared.events import emit, SELF_HEAL_WORKER_RESTARTED
            await emit(
                SELF_HEAL_WORKER_RESTARTED,
                {
                    "component": "outbound_worker",
                    "count": self._recovery_counts["outbound_worker"],
                },
            )
        except Exception as exc:
            log.error("[self_heal] outbound worker restart failed: %s", exc)

    async def _recover_ws_broadcaster(self) -> None:
        """Recreate the realtime heartbeat task; re-attach event bridge."""
        try:
            from shared import realtime as _rt
            # Recreate heartbeat task (idempotent — done check is the guard)
            _rt._heartbeat_task = asyncio.create_task(
                _rt._heartbeat_loop(), name="realtime:heartbeat"
            )
            self._recovery_counts["ws_broadcaster"] = (
                self._recovery_counts.get("ws_broadcaster", 0) + 1
            )
            self._audit(
                "recover_ws_broadcaster",
                f"heartbeat task restarted (count={self._recovery_counts['ws_broadcaster']})",
                "info",
            )
            from shared.events import emit, SELF_HEAL_WS_RESTARTED
            await emit(
                SELF_HEAL_WS_RESTARTED,
                {
                    "component": "ws_broadcaster",
                    "count": self._recovery_counts["ws_broadcaster"],
                },
            )
        except Exception as exc:
            log.error("[self_heal] ws broadcaster restart failed: %s", exc)

    async def _probe_bridges(self) -> None:
        """Trigger immediate bridge health probe via orchestrator."""
        try:
            from shared.bridge_orchestrator import probe_all_bridges
            await probe_all_bridges()
        except Exception as exc:
            log.debug("[self_heal] bridge probe failed: %s", exc)

    # ── Pressure + panic ──────────────────────────────────────────────────────

    async def _maybe_enter_panic(self) -> None:
        if self._pressure >= PANIC_THRESHOLD and not self._panic_mode:
            self._panic_mode = True
            self._throttled = True
            self._audit(
                "panic_mode_entered",
                f"pressure={self._pressure:.3f} >= threshold {PANIC_THRESHOLD}",
                "critical",
            )
            log.warning(
                "[self_heal] PANIC MODE — pressure=%.3f; non-critical work throttled",
                self._pressure,
            )
            from shared.events import emit, SELF_HEAL_PANIC
            await emit(SELF_HEAL_PANIC, {"pressure": self._pressure})

    async def _maybe_exit_panic(self) -> None:
        if self._pressure <= PANIC_CLEAR and self._panic_mode:
            self._panic_mode = False
            self._throttled = False
            self._audit(
                "panic_mode_cleared",
                f"pressure={self._pressure:.3f} <= clear threshold {PANIC_CLEAR}",
                "info",
            )
            log.info("[self_heal] pressure recovered — panic mode cleared")
            from shared.events import emit, SELF_HEAL_RECOVERED
            await emit(SELF_HEAL_RECOVERED, {"pressure": self._pressure})

    # ── Audit log ─────────────────────────────────────────────────────────────

    def _audit(self, action: str, detail: str, severity: str) -> None:
        """Append a record to the in-process recovery audit log."""
        entry: dict[str, Any] = {
            "ts": time.time(),
            "action": action,
            "detail": detail,
            "severity": severity,
            "pressure_at": round(self._pressure, 3),
        }
        self._audit_log.append(entry)
        lvl = logging.WARNING if severity in ("warning", "critical") else logging.INFO
        log.log(lvl, "[self_heal] audit action=%s detail=%s", action, detail)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict:
        """
        Return a full snapshot for the /api/self-heal/diagnostics endpoint.
        No secrets, no credentials — safe to expose without auth.
        """
        return {
            "pressure": round(self._pressure, 3),
            "panic_mode": self._panic_mode,
            "throttled": self._throttled,
            "enabled": _self_heal_enabled(),
            "last_signals": {k: round(v, 3) for k, v in self._last_signals.items()},
            "signal_weights": _WEIGHTS,
            "recovery_counts": dict(self._recovery_counts),
            "audit_log": list(self._audit_log)[-20:],   # last 20 entries only
            "check_interval_s": CHECK_INTERVAL_S,
            "panic_threshold": PANIC_THRESHOLD,
            "ts": time.time(),
        }


# ── Pressure computation ──────────────────────────────────────────────────────

def _compute_pressure(signals: dict[str, float]) -> float:
    """
    Weighted sum of pressure signals, clamped to [0.0, 1.0].
    Each signal must already be normalised to [0.0, 1.0].
    """
    total = sum(_WEIGHTS.get(k, 0.0) * v for k, v in signals.items())
    return max(0.0, min(1.0, total))


# ── Module-level singleton ────────────────────────────────────────────────────

_healer: Optional[SelfHealer] = None


def get_self_healer() -> SelfHealer:
    """Return the module singleton, creating it if needed."""
    global _healer
    if _healer is None:
        _healer = SelfHealer()
    return _healer


async def start_self_healer() -> None:
    """Start the self-healing background loop. Call once in lifespan startup."""
    await get_self_healer().start()


async def stop_self_healer() -> None:
    """Stop the background loop. Call in lifespan teardown."""
    await get_self_healer().stop()


def is_panic_mode() -> bool:
    """
    True when system pressure exceeds PANIC_THRESHOLD.
    Callers launching bulk/non-critical work SHOULD check this:

        if is_panic_mode():
            log.warning("skipping bulk compute — system under pressure")
            return
    """
    return get_self_healer()._panic_mode


def is_throttled() -> bool:
    """True when non-critical workloads should be deferred."""
    return get_self_healer()._throttled


def get_pressure_score() -> float:
    """Current system pressure score in [0.0, 1.0]."""
    return get_self_healer()._pressure


async def get_self_heal_diagnostics() -> dict:
    """Return full diagnostics dict (awaitable wrapper for endpoint handlers)."""
    return get_self_healer().get_diagnostics()


async def trigger_check_cycle() -> dict:
    """
    Run a single check cycle immediately (used by POST /api/self-heal/check).
    Returns diagnostics snapshot after checks complete.
    """
    healer = get_self_healer()
    await healer._run_all_checks()
    return healer.get_diagnostics()
