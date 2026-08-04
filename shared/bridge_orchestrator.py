"""
shared.bridge_orchestrator — Multi-Bridge Orchestration Layer (Phase 13D)
=========================================================================

Responsibilities
----------------
* Bridge health tracking   — periodic probe of bridge1 and bridge2
* Bridge failover          — route outbound via next healthy bridge on failure
* Cross-bridge deduplication — SHA-256 content hash prevents same WA message
                               being processed twice across bridges
* Source prioritization    — bridge2 (OPS/admin) always has highest authority
* Realtime bridge sync     — emits structured events for every state change
* Admin self-chat routing  — bridge2 sends drafts to admin own number
* Historical message guard — messages older than HISTORICAL_CUTOFF_S are
                             never eligible for draft generation (re-exposed
                             here as a fast in-process check)
* Outage buffer + replay   — per-bridge send queue with exponential backoff;
                             queued messages are drained on reconnect
* Lag diagnostics          — RTT samples per bridge + propagation latency

Architecture
------------
* Single-instance singleton — use module-level start_orchestrator / stop_orchestrator
* No broker required — in-process asyncio tasks
* Does NOT replace bridge clients — wraps get_bridge1 / get_bridge2
* Additive — zero breaking changes to existing bridge_poller or message_router

Admin authority
---------------
* bridge2 (OPS, 8801880446111) = MAIN ADMIN AUTHORITY
* Admin communicates with system via self-number (sends to own JID)
* System sends draft approvals to 8801880446111@s.whatsapp.net via bridge2
* Failover order for admin messages: bridge2 → bridge1

bridge3 (REC2, Phase E, wired 2026-08-04)
------------------------------------------
* bridge3 is health-tracked/probed/diagnosable (self._health, RT-013) exactly
  like bridge1/bridge2, so Admin can inspect it via the existing AI console.
* bridge3 is deliberately NOT added to BRIDGE_PRIORITY / ADMIN_BRIDGE_NAME /
  send_with_failover / send_to_admin. Those exist to deliver a notification
  TO the admin's bridge2 self-chat number via alternate bridges — bridge3 is
  a different device (REC2) and must never silently receive a message meant
  for bridge2's own number. bridge3 replies are sent back on bridge3 itself,
  via modules.bridge_poller's same-bridge reply path + modules.outbound.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.bridge import get_bridge1, get_bridge2, get_bridge3, BridgeSendError
from app.config import get_settings
from shared.events import emit

log = logging.getLogger("fazle.bridge_orchestrator")
_settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────

ADMIN_BRIDGE_NAME: str = "bridge2"
ADMIN_SELF_NUMBER: str = _settings.admin_bridge2_number
ADMIN_SELF_JID: str = f"{_settings.admin_bridge2_number}@s.whatsapp.net"

# Lower integer = higher priority (bridge2 is always preferred).
# bridge3 is intentionally absent — see "bridge3" docstring note above.
BRIDGE_PRIORITY: dict[str, int] = {
    "bridge2": 0,
    "bridge1": 1,
}

# Health-tracked/probed bridges (superset of BRIDGE_PRIORITY — includes
# bridge3 for diagnostics/RT-013 even though it's excluded from failover).
_TRACKED_BRIDGES: tuple[str, ...] = ("bridge1", "bridge2", "bridge3")


def _client_for(bridge_name: str):
    """Resolve the BridgeClient singleton for a tracked bridge name.

    Mirrors the pre-existing `get_bridge2() if x == "bridge2" else
    get_bridge1()` fallback semantics (unknown/other names → bridge1).
    """
    if bridge_name == "bridge2":
        return get_bridge2()
    if bridge_name == "bridge3":
        return get_bridge3()
    return get_bridge1()

DEDUP_TTL_S: int = 120          # 2-minute window for cross-bridge dedup
HEALTH_PROBE_INTERVAL_S: int = 30  # how often we probe each bridge
LAG_DEGRADED_MS: float = 5_000.0  # RTT above this → DEGRADED state
OUTAGE_THRESHOLD_S: float = 120.0  # no healthy probe in N seconds → OUTAGE
RETRY_MAX_ATTEMPTS: int = 5
RETRY_BACKOFF_S: list[int] = [5, 10, 30, 60, 120]
LAG_SAMPLE_WINDOW: int = 20     # rolling avg over last N samples

# Messages older than this are NEVER eligible for draft generation
HISTORICAL_CUTOFF_S: float = 300.0  # 5 minutes


# ── Domain types ─────────────────────────────────────────────────────────────

@dataclass
class BridgeHealth:
    name: str            # "bridge1" | "bridge2"
    label: str           # "BR1" | "BR2"
    number: str          # assigned WhatsApp number
    state: str = "healthy"         # "healthy" | "degraded" | "outage"
    last_healthy: float = field(default_factory=time.time)
    consecutive_failures: int = 0
    last_lag_ms: float = 0.0
    avg_lag_ms: float = 0.0
    message_count: int = 0         # inbound messages registered this session
    dedup_rejected: int = 0        # cross-bridge duplicates blocked
    retry_queue_depth: int = 0     # pending retry items for this bridge
    reconnect_count: int = 0       # number of outage→healthy transitions


@dataclass
class _RetryItem:
    bridge_name: str    # target bridge for retry
    jid: str
    text: str
    attempt: int        # zero-based attempt number
    next_retry_at: float
    created_at: float = field(default_factory=time.time)


# ── Orchestrator ──────────────────────────────────────────────────────────────

class BridgeOrchestrator:
    """
    Coordinates health, failover, deduplication, and admin routing across
    bridge1 and bridge2.  Instantiated once and accessed via module singletons.
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._health: dict[str, BridgeHealth] = {
            "bridge1": BridgeHealth(
                name="bridge1",
                label="BR1",
                number=settings.bridge1_number,
            ),
            "bridge2": BridgeHealth(
                name="bridge2",
                label="BR2",
                number=settings.bridge2_number,
            ),
            "bridge3": BridgeHealth(
                name="bridge3",
                label="BR3",
                number=settings.bridge3_number,
            ),
        }
        self._lag_samples: dict[str, list[float]] = {name: [] for name in _TRACKED_BRIDGES}
        # In-process dedup store: hash → expiry_epoch
        self._dedup_local: dict[str, float] = {}
        self._total_dedup_hits: int = 0
        # Retry queue
        self._retry_queue: list[_RetryItem] = []
        # Background tasks
        self._health_task: asyncio.Task | None = None
        self._retry_task: asyncio.Task | None = None
        self._started: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._health_task = asyncio.create_task(
            self._health_loop(), name="bridge_orchestrator:health"
        )
        self._retry_task = asyncio.create_task(
            self._retry_loop(), name="bridge_orchestrator:retry"
        )
        log.info("[orchestrator] started (bridge2=admin-authority)")

    async def stop(self) -> None:
        self._started = False
        for task in (self._health_task, self._retry_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._health_task = None
        self._retry_task = None
        log.info("[orchestrator] stopped")

    # ── Cross-bridge deduplication ────────────────────────────────────────────

    def _dedup_key(self, sender_jid: str, content: str, msg_ts: float) -> str:
        """
        Content-based dedup key.  Uses a 5-minute timestamp bucket so the same
        WA message arriving on bridge1 and bridge2 (which have different message
        IDs) still resolves to the same hash.
        """
        bucket = int(msg_ts // 300) * 300   # 5-min bucket
        raw = f"{sender_jid}|{content[:200]}|{bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _dedup_sweep(self) -> None:
        """Remove expired entries from the in-process dedup cache."""
        now = time.time()
        expired = [k for k, exp in self._dedup_local.items() if exp <= now]
        for k in expired:
            del self._dedup_local[k]

    def _is_duplicate_local(self, key: str) -> bool:
        now = time.time()
        exp = self._dedup_local.get(key)
        if exp is None or exp <= now:
            return False
        return True

    def _mark_seen_local(self, key: str) -> None:
        self._dedup_local[key] = time.time() + DEDUP_TTL_S

    async def register_message(
        self,
        *,
        bridge_name: str,
        sender_jid: str,
        content: str,
        msg_ts: float,
    ) -> bool:
        """
        Register an inbound message for cross-bridge deduplication.

        Returns True if this message has already been seen from another bridge
        and SHOULD BE SKIPPED.  Returns False (allow processing) if new.

        Side-effects:
        - Marks the message as seen so subsequent bridges skip it.
        - Increments health.message_count for the calling bridge.
        - Emits MESSAGE_DEDUPLICATED event on duplicate.
        """
        if bridge_name in self._health:
            self._health[bridge_name].message_count += 1

        # Occasional sweep to prevent unbounded growth
        if len(self._dedup_local) > 5_000:
            self._dedup_sweep()

        key = self._dedup_key(sender_jid, content, msg_ts)

        if self._is_duplicate_local(key):
            self._total_dedup_hits += 1
            if bridge_name in self._health:
                self._health[bridge_name].dedup_rejected += 1
            log.info(
                "[orchestrator] dedup hit bridge=%s sender=%s",
                bridge_name, sender_jid,
            )
            await emit(
                "cross_bridge_duplicate",
                {
                    "bridge": bridge_name,
                    "sender_jid": sender_jid,
                    "dedup_key": key,
                },
                emitted_by="bridge_orchestrator",
            )
            return True  # duplicate — skip

        self._mark_seen_local(key)
        return False  # new message — process it

    # ── Historical message guard ──────────────────────────────────────────────

    def is_historical(self, msg_ts: float) -> bool:
        """
        Return True if the message timestamp is older than HISTORICAL_CUTOFF_S.
        Historical messages MUST NEVER trigger draft generation.
        """
        return (time.time() - msg_ts) > HISTORICAL_CUTOFF_S

    # ── Bridge health recording ───────────────────────────────────────────────

    def _record_probe_result(
        self, bridge_name: str, *, lag_ms: float | None, error: str | None
    ) -> None:
        h = self._health.get(bridge_name)
        if h is None:
            return

        prev_state = h.state
        now = time.time()

        if error is None and lag_ms is not None:
            # Successful probe
            h.consecutive_failures = 0
            h.last_healthy = now
            h.last_lag_ms = lag_ms
            # Rolling avg
            samples = self._lag_samples[bridge_name]
            samples.append(lag_ms)
            if len(samples) > LAG_SAMPLE_WINDOW:
                samples.pop(0)
            h.avg_lag_ms = sum(samples) / len(samples)
            # State transition
            if lag_ms > LAG_DEGRADED_MS:
                h.state = "degraded"
            else:
                if prev_state == "outage":
                    h.reconnect_count += 1
                h.state = "healthy"
        else:
            # Failed probe
            h.consecutive_failures += 1
            age = now - h.last_healthy
            if age > OUTAGE_THRESHOLD_S:
                h.state = "outage"
            else:
                h.state = "degraded"

        if h.state != prev_state:
            log.warning(
                "[orchestrator] bridge=%s %s → %s (fail_streak=%d)",
                bridge_name, prev_state, h.state, h.consecutive_failures,
            )
            event_map = {
                "healthy": "bridge_health_changed",
                "degraded": "bridge_health_changed",
                "outage": "bridge_health_changed",
            }
            asyncio.create_task(
                emit(
                    event_map[h.state],
                    {
                        "bridge": bridge_name,
                        "prev_state": prev_state,
                        "state": h.state,
                        "consecutive_failures": h.consecutive_failures,
                        "lag_ms": lag_ms,
                    },
                    emitted_by="bridge_orchestrator",
                )
            )

    # ── Send with failover ────────────────────────────────────────────────────

    async def send_with_failover(
        self,
        jid: str,
        text: str,
        *,
        preferred_bridge: str = ADMIN_BRIDGE_NAME,
        exclude_bridges: set[str] | None = None,
    ) -> tuple[bool, str]:
        """
        Send a message trying bridges in priority order.

        Returns (success: bool, bridge_used: str).
        On total failure, queues for retry and returns (False, "queued").
        """
        exclude = exclude_bridges or set()
        ordered = sorted(
            (name for name in BRIDGE_PRIORITY if name not in exclude),
            key=lambda n: (0 if n == preferred_bridge else 1, BRIDGE_PRIORITY[n]),
        )

        for bridge_name in ordered:
            h = self._health.get(bridge_name)
            if h and h.state == "outage":
                log.debug("[orchestrator] skip outage bridge=%s", bridge_name)
                continue
            try:
                client = _client_for(bridge_name)
                await client.send_strict(jid, text)
                if bridge_name != preferred_bridge:
                    await emit(
                        "bridge_failover",
                        {
                            "preferred": preferred_bridge,
                            "used": bridge_name,
                            "jid": jid,
                        },
                        emitted_by="bridge_orchestrator",
                    )
                return True, bridge_name
            except BridgeSendError as exc:
                log.warning(
                    "[orchestrator] send failed bridge=%s: %s",
                    bridge_name, exc,
                )

        # All bridges failed — queue for retry
        self._enqueue_retry(preferred_bridge, jid, text)
        log.error(
            "[orchestrator] all bridges failed for jid=%s — queued for retry",
            jid,
        )
        return False, "queued"

    async def send_to_admin(
        self,
        text: str,
        *,
        context: dict | None = None,
    ) -> bool:
        """
        Send a message to the admin self-chat (bridge2 → own number).
        Falls back to bridge1 if bridge2 is unavailable.

        context: optional metadata emitted with DRAFT_APPROVAL_SENT event.
        """
        success, bridge_used = await self.send_with_failover(
            ADMIN_SELF_JID,
            text,
            preferred_bridge=ADMIN_BRIDGE_NAME,
        )
        if success:
            await emit(
                "draft_approval_sent",
                {
                    "jid": ADMIN_SELF_JID,
                    "bridge_used": bridge_used,
                    **(context or {}),
                },
                emitted_by="bridge_orchestrator",
            )
        return success

    # ── Retry queue ───────────────────────────────────────────────────────────

    def _enqueue_retry(self, bridge_name: str, jid: str, text: str) -> None:
        item = _RetryItem(
            bridge_name=bridge_name,
            jid=jid,
            text=text,
            attempt=0,
            next_retry_at=time.time() + RETRY_BACKOFF_S[0],
        )
        self._retry_queue.append(item)
        if bridge_name in self._health:
            self._health[bridge_name].retry_queue_depth = sum(
                1 for r in self._retry_queue if r.bridge_name == bridge_name
            )
        log.info("[orchestrator] queued retry for bridge=%s jid=%s", bridge_name, jid)

    async def _drain_retry_for_bridge(self, bridge_name: str) -> None:
        """Called when a bridge transitions from outage → healthy."""
        due = [r for r in self._retry_queue if r.bridge_name == bridge_name]
        if not due:
            return
        log.info(
            "[orchestrator] reconnect replay: %d queued messages for bridge=%s",
            len(due), bridge_name,
        )
        replayed = 0
        for item in due[:]:
            try:
                client = _client_for(bridge_name)
                await client.send_strict(item.jid, item.text)
                self._retry_queue.remove(item)
                replayed += 1
            except BridgeSendError:
                item.attempt += 1
                if item.attempt >= RETRY_MAX_ATTEMPTS:
                    log.error(
                        "[orchestrator] retry exhausted bridge=%s jid=%s (dropped)",
                        bridge_name, item.jid,
                    )
                    self._retry_queue.remove(item)
        if replayed:
            await emit(
                "bridge_reconnected",
                {"bridge": bridge_name, "replayed": replayed},
                emitted_by="bridge_orchestrator",
            )

    # ── Probe helpers ─────────────────────────────────────────────────────────

    async def _probe_bridge(self, bridge_name: str) -> None:
        """Probe a single bridge and update health state."""
        client = _client_for(bridge_name)
        prev_state = self._health[bridge_name].state if bridge_name in self._health else "healthy"
        t0 = time.monotonic()
        try:
            await client.status()
            lag_ms = (time.monotonic() - t0) * 1_000
            self._record_probe_result(bridge_name, lag_ms=lag_ms, error=None)
        except Exception as exc:
            lag_ms = (time.monotonic() - t0) * 1_000
            self._record_probe_result(bridge_name, lag_ms=None, error=str(exc))
            log.debug("[orchestrator] probe failed bridge=%s: %s", bridge_name, exc)

        # Drain retry queue on recovery
        new_state = self._health[bridge_name].state if bridge_name in self._health else "healthy"
        if prev_state == "outage" and new_state == "healthy":
            await self._drain_retry_for_bridge(bridge_name)

    async def probe_all(self) -> None:
        """Probe all tracked bridges (available as manual trigger)."""
        await asyncio.gather(
            *(self._probe_bridge(name) for name in _TRACKED_BRIDGES)
        )

    # ── Background loops ──────────────────────────────────────────────────────

    async def _health_loop(self) -> None:
        """Periodically probe all bridges and emit health events."""
        while self._started:
            try:
                await self.probe_all()
            except Exception as exc:
                log.error("[orchestrator] health probe error: %s", exc)
            await asyncio.sleep(HEALTH_PROBE_INTERVAL_S)

    async def _retry_loop(self) -> None:
        """Process the retry queue — re-attempt failed sends."""
        while self._started:
            now = time.time()
            due = [r for r in self._retry_queue if r.next_retry_at <= now]
            for item in due:
                if item.attempt >= RETRY_MAX_ATTEMPTS:
                    log.warning(
                        "[orchestrator] retry dropped (max attempts) bridge=%s jid=%s",
                        item.bridge_name, item.jid,
                    )
                    self._retry_queue.remove(item)
                    continue
                try:
                    client = _client_for(item.bridge_name)
                    await client.send_strict(item.jid, item.text)
                    log.info(
                        "[orchestrator] retry succeeded bridge=%s attempt=%d jid=%s",
                        item.bridge_name, item.attempt, item.jid,
                    )
                    self._retry_queue.remove(item)
                except BridgeSendError:
                    item.attempt += 1
                    delay = (
                        RETRY_BACKOFF_S[item.attempt - 1]
                        if item.attempt - 1 < len(RETRY_BACKOFF_S)
                        else RETRY_BACKOFF_S[-1]
                    )
                    item.next_retry_at = time.time() + delay
                    log.debug(
                        "[orchestrator] retry %d failed bridge=%s — next in %ds",
                        item.attempt, item.bridge_name, delay,
                    )
            # Update queue depth metrics
            for bridge_name, h in self._health.items():
                h.retry_queue_depth = sum(
                    1 for r in self._retry_queue if r.bridge_name == bridge_name
                )
            await asyncio.sleep(5)

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def get_diagnostics(self) -> dict:
        """
        Return a full diagnostics snapshot for /api/bridges/diagnostics.
        No secrets, no credentials — safe to expose via non-auth endpoint.
        """
        bridges_info = {}
        for name, h in self._health.items():
            bridges_info[name] = {
                "name": h.name,
                "label": h.label,
                "number": h.number,
                "state": h.state,
                "last_healthy_ago_s": round(time.time() - h.last_healthy, 1),
                "consecutive_failures": h.consecutive_failures,
                "last_lag_ms": round(h.last_lag_ms, 1),
                "avg_lag_ms": round(h.avg_lag_ms, 1),
                "message_count": h.message_count,
                "dedup_rejected": h.dedup_rejected,
                "retry_queue_depth": h.retry_queue_depth,
                "reconnect_count": h.reconnect_count,
                "is_admin_authority": (name == ADMIN_BRIDGE_NAME),
            }
        return {
            "bridges": bridges_info,
            "orchestrator": {
                "admin_bridge": ADMIN_BRIDGE_NAME,
                "admin_self_number": ADMIN_SELF_NUMBER,
                "dedup_window_s": DEDUP_TTL_S,
                "dedup_cache_size": len(self._dedup_local),
                "total_dedup_hits": self._total_dedup_hits,
                "retry_queue_total": len(self._retry_queue),
                "health_probe_interval_s": HEALTH_PROBE_INTERVAL_S,
                "lag_degraded_threshold_ms": LAG_DEGRADED_MS,
                "outage_threshold_s": OUTAGE_THRESHOLD_S,
                "historical_cutoff_s": HISTORICAL_CUTOFF_S,
                "started": self._started,
            },
        }


# ── Module-level singleton ────────────────────────────────────────────────────

_orchestrator: BridgeOrchestrator | None = None


def get_orchestrator() -> BridgeOrchestrator:
    """Return the module-level orchestrator instance (created on first call)."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BridgeOrchestrator()
    return _orchestrator


# ── Public convenience API ────────────────────────────────────────────────────

async def start_orchestrator() -> None:
    """Start the bridge orchestrator — call once in lifespan startup."""
    await get_orchestrator().start()


async def stop_orchestrator() -> None:
    """Stop the bridge orchestrator — call in lifespan teardown."""
    await get_orchestrator().stop()


async def register_message(
    *,
    bridge_name: str,
    sender_jid: str,
    content: str,
    msg_ts: float,
) -> bool:
    """
    Register an inbound message for cross-bridge deduplication.
    Returns True if duplicate (should be skipped), False if new (process it).
    """
    return await get_orchestrator().register_message(
        bridge_name=bridge_name,
        sender_jid=sender_jid,
        content=content,
        msg_ts=msg_ts,
    )


def is_historical(msg_ts: float) -> bool:
    """Return True if the message is older than HISTORICAL_CUTOFF_S (5 min)."""
    return get_orchestrator().is_historical(msg_ts)


async def send_with_failover(
    jid: str,
    text: str,
    *,
    preferred_bridge: str = ADMIN_BRIDGE_NAME,
    exclude_bridges: set[str] | None = None,
) -> tuple[bool, str]:
    """Send with automatic bridge failover. Returns (success, bridge_used)."""
    return await get_orchestrator().send_with_failover(
        jid, text,
        preferred_bridge=preferred_bridge,
        exclude_bridges=exclude_bridges,
    )


async def send_to_admin(text: str, *, context: dict | None = None) -> bool:
    """Send text to admin self-chat (bridge2 → 8801880446111)."""
    return await get_orchestrator().send_to_admin(text, context=context)


async def get_bridge_diagnostics() -> dict:
    """Return diagnostics for all bridges."""
    return get_orchestrator().get_diagnostics()


async def probe_all_bridges() -> None:
    """Trigger an immediate health probe on all bridges."""
    await get_orchestrator().probe_all()
