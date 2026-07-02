"""
shared.runtime_gateway — Distributed Runtime Gateway (Phase 13A)
================================================================

Registers app instances (fazle-core, payroll-engine, escort-roster) in
the fazle_runtime_nodes table, maintains 30-second heartbeats, marks
stale nodes offline, routes cross-app events, and exposes a node-health
summary for the /api/runtime/nodes diagnostic endpoint.

Design constraints
------------------
* Additive only — no existing tables or APIs modified.
* Standalone-safe — no hard import from app.database at module level;
  accepts an explicit pool or resolves lazily when running inside the app.
* Non-fatal — all DB operations are guarded; a failure never crashes
  the caller (log + continue).
* Race-safe — UPSERT-based registration; stale-sweep uses a single atomic
  UPDATE ... RETURNING so there are no lost-update windows.
* Reconnect-safe — heartbeat loop retries indefinitely with failure
  counting; emits RECONNECTING event after 3 consecutive misses.

Public API
----------
  # App startup (lifespan)
  node_id = await start_gateway("fazle-core", role="orchestrator", version="1.1.0")

  # App shutdown (lifespan)
  await stop_gateway()

  # Current process node id
  node_id = get_current_node_id()

  # Diagnostic snapshot
  nodes = await get_active_nodes()

  # Cross-app event bus (re-exports shared.events)
  import shared.runtime_gateway as gw
  await gw.shared_runtime_event_bus.emit(gw.QUEUE_PRESSURE, {...})

Runtime event types (also exported from shared.events)
-------------------------------------------------------
  node_online, node_offline, queue_pressure, stale_frontend,
  lock_contention (already in events), reconnecting, ws_recovered
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("fazle.runtime_gateway")

# ── Heartbeat / stale thresholds ──────────────────────────────────────────────
HEARTBEAT_INTERVAL_S: int = 30    # how often a node sends its heartbeat
STALE_THRESHOLD_S:    int = 90    # last_seen > 90 s → mark offline
_STALE_SWEEP_EVERY:   int = 3     # run stale sweep every N heartbeat cycles

# ── Node status constants ─────────────────────────────────────────────────────
STATUS_ONLINE   = "online"
STATUS_OFFLINE  = "offline"
STATUS_DEGRADED = "degraded"

# ── Runtime event type constants (re-exported from shared.events) ─────────────
from shared.events import (          # noqa: E402
    NODE_ONLINE,
    NODE_OFFLINE,
    QUEUE_PRESSURE,
    STALE_FRONTEND,
    LOCK_CONTENTION,
    RECONNECTING,
    WS_RECOVERED,
    emit as _emit,
)

# ── shared_runtime_event_bus — public name for the shared event module ─────────
import shared.events as shared_runtime_event_bus   # noqa: E402  (re-exported alias)

__all__ = [
    # Gateway lifecycle
    "start_gateway",
    "stop_gateway",
    "get_current_node_id",
    # Low-level ops
    "register_node",
    "heartbeat",
    "deregister_node",
    "mark_stale_nodes",
    "get_active_nodes",
    "get_current_runtime_nodes",
    "get_historical_runtime_nodes",
    # Background helpers
    "heartbeat_loop",
    "make_node_id",
    # Event-bus re-export
    "shared_runtime_event_bus",
    # Event type constants
    "NODE_ONLINE", "NODE_OFFLINE", "QUEUE_PRESSURE",
    "STALE_FRONTEND", "LOCK_CONTENTION", "RECONNECTING", "WS_RECOVERED",
    # Thresholds (so tests/callers can override)
    "HEARTBEAT_INTERVAL_S", "STALE_THRESHOLD_S",
]


# ── RuntimeNode dataclass ─────────────────────────────────────────────────────

@dataclass
class RuntimeNode:
    """In-memory snapshot of a node record (not persisted directly)."""
    node_id:         str
    app_name:        str
    role:            str
    status:          str                    = STATUS_ONLINE
    last_seen:       float                  = field(default_factory=time.time)
    version:         str                    = "unknown"
    active_requests: int                    = 0
    queue_depth:     int                    = 0
    metadata:        Dict[str, Any]         = field(default_factory=dict)


# ── Node identity ─────────────────────────────────────────────────────────────

def make_node_id(app_name: str) -> str:
    """
    Build a stable-per-process node_id.

    Format: ``{app_name}-{hostname}-{pid}``
    Example: ``fazle-core-vmi3117764-12345``

    The pid makes the id unique across restarts on the same host while
    still encoding enough info for human diagnosis.
    """
    hostname = socket.gethostname().split(".")[0][:20]  # truncate for readability
    pid      = os.getpid()
    return f"{app_name}-{hostname}-{pid}"


# ── Internal pool resolution ──────────────────────────────────────────────────

def _resolve_pool(pool):
    """
    Return the provided pool, or fall back to app.database.get_pool().

    Returns None if neither is available (standalone script without DB config).
    This is the ONLY place where app.database is imported, and only lazily,
    so standalone scripts that import runtime_gateway do not need the app
    to be running.
    """
    if pool is not None:
        return pool
    try:
        from app.database import get_pool
        return get_pool()
    except Exception:
        return None


# ── Core DB operations ────────────────────────────────────────────────────────

async def register_node(
    app_name:  str,
    role:      str                       = "worker",
    version:   str                       = "unknown",
    metadata:  Optional[Dict[str, Any]]  = None,
    *,
    pool=None,
) -> str:
    """
    Register (or re-register) this process in fazle_runtime_nodes.

    Uses UPSERT so a crashed-and-restarted process safely overwrites its old
    row instead of creating a duplicate.  A stale row from the old PID is
    replaced; the new PID gets a fresh registered_at via the INSERT branch.

    Returns the node_id so callers can reference it in subsequent calls.
    Non-fatal: logs a warning and returns the node_id even on DB failure.
    """
    node_id       = make_node_id(app_name)
    resolved_pool = _resolve_pool(pool)

    if resolved_pool is None:
        log.warning("[gateway] no pool available — node %s registration deferred", node_id)
        # Return id so the caller can still use heartbeat_loop (which will retry)
        return node_id

    try:
        async with resolved_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO fazle_runtime_nodes
                    (node_id, app_name, role, status, last_seen, registered_at,
                     version, active_requests, queue_depth, metadata_json)
                VALUES ($1, $2, $3, 'online', NOW(), NOW(), $4, 0, 0, $5::jsonb)
                ON CONFLICT (node_id) DO UPDATE SET
                    status        = 'online',
                    last_seen     = NOW(),
                    version       = EXCLUDED.version,
                    role          = EXCLUDED.role,
                    metadata_json = EXCLUDED.metadata_json
                """,
                node_id,
                app_name,
                role,
                version,
                json.dumps(metadata or {}, ensure_ascii=False),
            )
        log.info("[gateway] registered node=%s app=%s role=%s version=%s",
                 node_id, app_name, role, version)
        await _emit(
            NODE_ONLINE,
            {"node_id": node_id, "app_name": app_name, "role": role, "version": version},
            emitted_by="runtime_gateway",
        )
    except Exception as exc:
        log.warning("[gateway] register_node failed for %s: %s", node_id, exc)

    return node_id


async def heartbeat(
    node_id:         str,
    active_requests: int = 0,
    queue_depth:     int = 0,
    *,
    pool=None,
) -> bool:
    """
    Touch last_seen and update metrics for this node.

    Called every HEARTBEAT_INTERVAL_S seconds from heartbeat_loop.
    Returns True on success, False on any DB error (caller decides
    whether to escalate).
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return False

    try:
        async with resolved_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE fazle_runtime_nodes
                SET last_seen       = NOW(),
                    status          = 'online',
                    active_requests = $2,
                    queue_depth     = $3
                WHERE node_id = $1
                """,
                node_id,
                active_requests,
                queue_depth,
            )
        # asyncpg returns "UPDATE N" — 0 rows means the node was somehow deleted
        if result == "UPDATE 0":
            log.warning("[gateway] heartbeat found no row for %s — re-registering", node_id)
            return False
        return True
    except Exception as exc:
        log.debug("[gateway] heartbeat failed for %s: %s", node_id, exc)
        return False


async def deregister_node(node_id: str, *, pool=None) -> None:
    """
    Mark a node offline (clean shutdown).

    Called in lifespan teardown so the row is immediately available for
    dashboard inspection rather than waiting for the stale sweep.
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return

    try:
        async with resolved_pool.acquire() as conn:
            await conn.execute(
                "UPDATE fazle_runtime_nodes SET status = 'offline' WHERE node_id = $1",
                node_id,
            )
        await _emit(
            NODE_OFFLINE,
            {"node_id": node_id, "reason": "clean_shutdown"},
            emitted_by="runtime_gateway",
        )
        log.info("[gateway] deregistered node=%s (clean shutdown)", node_id)
    except Exception as exc:
        log.debug("[gateway] deregister_node failed for %s: %s", node_id, exc)


async def mark_stale_nodes(
    *,
    stale_after_seconds: int = STALE_THRESHOLD_S,
    pool=None,
) -> List[str]:
    """
    Atomically mark online nodes whose last_seen is older than
    ``stale_after_seconds`` as 'offline'.

    Uses ``UPDATE ... RETURNING`` so there is no lost-update race between
    two workers running the sweep simultaneously.

    For each stale node:
    - Emits NODE_OFFLINE event with reason='stale_heartbeat'
    - Attempts to release any queue items claimed by that node back to 'pending'
      (best-effort — silently skips if processor_id column does not exist)

    Returns list of node_ids that were marked stale.
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return []

    try:
        async with resolved_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                UPDATE fazle_runtime_nodes
                SET status = 'offline'
                WHERE status = 'online'
                  AND last_seen < NOW() - ($1 || ' seconds')::INTERVAL
                RETURNING node_id, app_name, queue_depth
                """,
                str(stale_after_seconds),
            )

        stale_ids = [r["node_id"] for r in rows]
        for r in rows:
            log.warning(
                "[gateway] stale node marked offline: node=%s app=%s queue_depth_at_offline=%d",
                r["node_id"], r["app_name"], r["queue_depth"],
            )
            await _emit(
                NODE_OFFLINE,
                {
                    "node_id":                r["node_id"],
                    "app_name":               r["app_name"],
                    "reason":                 "stale_heartbeat",
                    "queue_depth_at_offline": r["queue_depth"],
                },
                emitted_by="runtime_gateway",
            )
            # Release any queue items claimed by this node
            await _release_claimed_queue_items(r["node_id"], pool=resolved_pool)

        return stale_ids

    except Exception as exc:
        log.warning("[gateway] mark_stale_nodes failed: %s", exc)
        return []


async def _release_claimed_queue_items(node_id: str, *, pool) -> None:
    """
    Best-effort release of fazle_message_queue items that were being
    processed by the now-offline node.

    Sets their status back to 'pending' (capped at 2 attempts so they
    don't immediately dead-letter) so the next available worker picks
    them up.  Silently skips if processor_id column does not exist.
    """
    try:
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE fazle_message_queue
                SET status       = 'pending',
                    processor_id = NULL,
                    attempts     = LEAST(attempts, 2)
                WHERE processor_id = $1
                  AND status IN ('processing', 'pending')
                """,
                node_id,
            )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            log.info("[gateway] released %d queue items from offline node %s",
                     count, node_id)
            await _emit(
                QUEUE_PRESSURE,
                {"node_id": node_id, "released_items": count, "reason": "node_offline"},
                emitted_by="runtime_gateway",
            )
    except Exception as exc:
        log.debug("[gateway] _release_claimed_queue_items skipped: %s", exc)


def _rows_to_nodes(rows: list) -> list[dict]:
    """Normalize asyncpg rows into JSON-serializable runtime node dicts."""
    now = time.time()
    result = []
    for r in rows:
        last_seen_dt = r["last_seen"]
        registered_dt = r["registered_at"]
        age_s = round(now - last_seen_dt.timestamp(), 1) if last_seen_dt else None
        result.append({
            "node_id":         r["node_id"],
            "app_name":        r["app_name"],
            "role":            r["role"],
            "status":          r["status"],
            "last_seen":       last_seen_dt.isoformat() if last_seen_dt else None,
            "age_s":           age_s,
            "registered_at":   registered_dt.isoformat() if registered_dt else None,
            "version":         r["version"],
            "active_requests": r["active_requests"],
            "queue_depth":     r["queue_depth"],
            "metadata":        (
                json.loads(r["metadata_json"])
                if r["metadata_json"] else {}
            ),
        })
    return result


async def get_active_nodes(*, pool=None) -> List[dict]:
    """
    Return all rows from fazle_runtime_nodes with a derived ``age_s`` field
    (seconds since last_seen).

    Legacy compatibility reader used by GET /api/runtime/nodes.
    Returns all rows, including offline history. Returns [] on any error.
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return []

    try:
        async with resolved_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT node_id, app_name, role, status,
                       last_seen, registered_at, version,
                       active_requests, queue_depth, metadata_json
                FROM fazle_runtime_nodes
                ORDER BY app_name, node_id
                """,
            )
        return _rows_to_nodes(rows)

    except Exception as exc:
        log.warning("[gateway] get_active_nodes failed: %s", exc)
        return []


async def get_current_runtime_nodes(*, pool=None) -> List[dict]:
    """
    Return only current active runtime truth for AI and operational use.

    Current means:
    - status='online'
    - last_seen within the stale threshold window
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return []

    try:
        async with resolved_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT node_id, app_name, role, status,
                       last_seen, registered_at, version,
                       active_requests, queue_depth, metadata_json
                FROM fazle_runtime_nodes
                WHERE status = 'online'
                  AND last_seen >= NOW() - ($1 || ' seconds')::INTERVAL
                ORDER BY app_name, node_id
                """,
                str(STALE_THRESHOLD_S),
            )
        return _rows_to_nodes(rows)
    except Exception as exc:
        log.warning("[gateway] get_current_runtime_nodes failed: %s", exc)
        return []


async def get_historical_runtime_nodes(*, pool=None) -> List[dict]:
    """
    Return historical runtime rows that should not be treated as current truth.

    History means:
    - status is not online, OR
    - status is online but last_seen is stale
    """
    resolved_pool = _resolve_pool(pool)
    if resolved_pool is None:
        return []

    try:
        async with resolved_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT node_id, app_name, role, status,
                       last_seen, registered_at, version,
                       active_requests, queue_depth, metadata_json
                FROM fazle_runtime_nodes
                WHERE status <> 'online'
                   OR last_seen < NOW() - ($1 || ' seconds')::INTERVAL
                ORDER BY last_seen DESC, app_name, node_id
                """,
                str(STALE_THRESHOLD_S),
            )
        return _rows_to_nodes(rows)

    except Exception as exc:
        log.warning("[gateway] get_historical_runtime_nodes failed: %s", exc)
        return []


# ── Background heartbeat loop ─────────────────────────────────────────────────

async def heartbeat_loop(
    node_id: str,
    *,
    interval:            int                              = HEARTBEAT_INTERVAL_S,
    pool=None,
    get_active_requests: Optional[Callable[[], Any]]     = None,
    get_queue_depth:     Optional[Callable[[], Any]]     = None,
) -> None:
    """
    Background asyncio task: send heartbeat every ``interval`` seconds.

    ``get_active_requests`` / ``get_queue_depth``:
        Optional async (or sync) callables that return the current counts
        so the heartbeat row in the DB stays accurate for the dashboard.

    Stale sweep:
        Runs ``mark_stale_nodes()`` every ``_STALE_SWEEP_EVERY`` cycles
        (default every 90 s) to catch peers that crashed without a clean
        deregister.

    Reconnect alerting:
        Emits RECONNECTING after 3 consecutive heartbeat failures; resets
        counter on success.
    """
    log.info("[gateway] heartbeat loop started node=%s interval=%ds", node_id, interval)
    cycle                 = 0
    consecutive_failures  = 0

    while True:
        try:
            await asyncio.sleep(interval)
            cycle += 1

            # Collect live metrics if callbacks are provided
            ar = 0
            qd = 0
            if get_active_requests:
                try:
                    val = get_active_requests()
                    ar  = int(await val if asyncio.iscoroutine(val) else val)
                except Exception:
                    pass
            if get_queue_depth:
                try:
                    val = get_queue_depth()
                    qd  = int(await val if asyncio.iscoroutine(val) else val)
                except Exception:
                    pass

            ok = await heartbeat(node_id, ar, qd, pool=pool)

            if ok:
                if consecutive_failures > 0:
                    log.info("[gateway] heartbeat recovered after %d failures node=%s",
                             consecutive_failures, node_id)
                    await _emit(
                        WS_RECOVERED,
                        {"node_id": node_id, "after_failures": consecutive_failures},
                        emitted_by="runtime_gateway",
                    )
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    log.warning(
                        "[gateway] %d consecutive heartbeat failures node=%s",
                        consecutive_failures, node_id,
                    )
                    await _emit(
                        RECONNECTING,
                        {"node_id": node_id, "consecutive_failures": consecutive_failures},
                        emitted_by="runtime_gateway",
                    )

            # Periodic stale sweep — only one node needs to do this but it's
            # idempotent so running it on multiple nodes is safe (atomic UPDATE).
            if cycle % _STALE_SWEEP_EVERY == 0:
                stale = await mark_stale_nodes(pool=pool)
                if stale:
                    log.info("[gateway] stale sweep found %d offline nodes: %s",
                             len(stale), stale)

        except asyncio.CancelledError:
            log.info("[gateway] heartbeat loop cancelled node=%s", node_id)
            break
        except Exception as exc:
            log.warning("[gateway] heartbeat loop unexpected error node=%s: %s", node_id, exc)


# ── Module-level gateway state (one per process) ──────────────────────────────

_active_node_id:   Optional[str]              = None
_heartbeat_task:   Optional[asyncio.Task]     = None


async def start_gateway(
    app_name:  str,
    role:      str                       = "worker",
    version:   str                       = "unknown",
    metadata:  Optional[Dict[str, Any]]  = None,
    *,
    pool=None,
    get_active_requests: Optional[Callable[[], Any]] = None,
    get_queue_depth:     Optional[Callable[[], Any]] = None,
) -> str:
    """
    Register this node and start the background heartbeat task.

    Call once from app lifespan startup (non-fatal — all errors logged).

    Parameters
    ----------
    app_name   : Human-readable name stored in fazle_runtime_nodes.app_name.
                 E.g. "fazle-core", "payroll-engine", "escort-roster".
    role       : Functional role, e.g. "orchestrator", "worker", "standalone".
    version    : App version string for the dashboard.
    metadata   : Arbitrary JSON stored in metadata_json for diagnostics.
    pool       : Explicit asyncpg pool. Defaults to app.database.get_pool().
    get_active_requests / get_queue_depth :
                 Optional callables for live metric collection per heartbeat.

    Returns the node_id (``{app_name}-{hostname}-{pid}``).
    """
    global _active_node_id, _heartbeat_task

    node_id         = await register_node(app_name, role, version, metadata, pool=pool)
    _active_node_id = node_id

    _heartbeat_task = asyncio.get_event_loop().create_task(
        heartbeat_loop(
            node_id,
            pool=pool,
            get_active_requests=get_active_requests,
            get_queue_depth=get_queue_depth,
        ),
        name=f"gateway-heartbeat-{node_id}",
    )
    log.info("[gateway] gateway started node=%s", node_id)
    return node_id


async def stop_gateway(*, pool=None) -> None:
    """
    Stop the heartbeat task and deregister this node (clean shutdown).

    Call from app lifespan teardown so the row is immediately marked offline
    rather than waiting for the stale sweep (which takes up to STALE_THRESHOLD_S).
    """
    global _active_node_id, _heartbeat_task

    if _heartbeat_task and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        _heartbeat_task = None

    if _active_node_id:
        await deregister_node(_active_node_id, pool=pool)
        _active_node_id = None

    log.info("[gateway] gateway stopped")


def get_current_node_id() -> Optional[str]:
    """
    Return this process's current node_id, or None if start_gateway has not
    been called yet (e.g. in standalone scripts that import this module but
    don't use the full gateway lifecycle).
    """
    return _active_node_id
