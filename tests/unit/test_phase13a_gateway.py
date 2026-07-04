"""
Phase 13A — Distributed Runtime Gateway tests
=============================================

Tests:
  1. test_node_registers_correctly          — register() inserts a row, emits NODE_ONLINE
  2. test_duplicate_node_id_upserts         — re-registering same node_id is idempotent
  3. test_heartbeat_updates_last_seen       — heartbeat() touches last_seen and metrics
  4. test_stale_node_marked_offline         — mark_stale_nodes() flips status + emits event
  5. test_stale_heartbeat_releases_queue    — offline node's queue items returned to pending
  6. test_node_recovery_after_stale         — re-registering after stale → back online
  7. test_deregister_marks_offline          — deregister_node() → status=offline immediately
  8. test_heartbeat_reconnect_event         — 3 consecutive failures → RECONNECTING emitted
  9. test_get_active_nodes_shape            — legacy all-rows reader returns expected dict keys
  10. test_get_current_runtime_nodes_query  — current reader filters to fresh online rows
  11. test_get_historical_runtime_nodes_query — history reader excludes current runtime truth
  12. test_node_offline_emits_event         — deregister emits NODE_OFFLINE with reason

All tests use AsyncMock + in-memory fakes — no live DB required.
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch

# ── Path setup ────────────────────────────────────────────────────────────────
_CORE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _CORE_ROOT not in sys.path:
    sys.path.insert(0, _CORE_ROOT)

# ── Helpers ───────────────────────────────────────────────────────────────────

class _AcquireCtx:
    """Sync callable that returns an async context manager wrapping `conn`."""
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


def _make_pool(*, execute_result="UPDATE 1", fetch_rows=None):
    """
    Build a minimal asyncpg pool mock.

    pool.acquire() is a *sync* call that returns an async context manager,
    so we use MagicMock (not AsyncMock) for acquire itself.
    """
    conn = AsyncMock()
    conn.execute  = AsyncMock(return_value=execute_result)
    conn.fetch    = AsyncMock(return_value=fetch_rows or [])
    conn.fetchval = AsyncMock(return_value=1)

    ctx = _AcquireCtx(conn)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _make_node_row(
    node_id="test-node-1",
    app_name="fazle-core",
    role="orchestrator",
    status="online",
    queue_depth=0,
    version="1.0.0",
):
    """Minimal fake asyncpg row dict for fazle_runtime_nodes."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "node_id":         node_id,
        "app_name":        app_name,
        "role":            role,
        "status":          status,
        "last_seen":       now,
        "registered_at":   now,
        "version":         version,
        "active_requests": 0,
        "queue_depth":     queue_depth,
        "metadata_json":   json.dumps({}),
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_node_registers_correctly():
    """register_node executes an UPSERT and emits NODE_ONLINE."""
    from shared import runtime_gateway as gw, events

    received: list = []

    async def on_node_online(evt):
        received.append(evt.event_type)

    events.subscribe(events.NODE_ONLINE, on_node_online)
    try:
        pool, conn = _make_pool()
        node_id = await gw.register_node("fazle-core", role="orchestrator",
                                         version="1.0.0", pool=pool)

        # Should have called execute (UPSERT)
        assert conn.execute.called
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO fazle_runtime_nodes" in sql
        assert "ON CONFLICT" in sql

        # node_id should encode app_name
        assert "fazle-core" in node_id

        await events.flush_pending(timeout=2.0)
        assert events.NODE_ONLINE in received
    finally:
        events.unsubscribe(events.NODE_ONLINE, on_node_online)


@pytest.mark.asyncio
async def test_duplicate_node_id_upserts():
    """Registering the same node_id twice must not raise — UPSERT handles it."""
    from shared import runtime_gateway as gw

    pool, conn = _make_pool()

    id1 = await gw.register_node("payroll-engine", pool=pool)
    id2 = await gw.register_node("payroll-engine", pool=pool)

    # Both calls execute successfully
    assert conn.execute.call_count == 2
    # Both return a valid node_id (will differ if pid changed between calls,
    # but since we're in the same process they must be identical)
    assert id1 == id2


@pytest.mark.asyncio
async def test_heartbeat_updates_last_seen():
    """heartbeat() issues an UPDATE touching last_seen, returns True on success."""
    from shared import runtime_gateway as gw

    pool, conn = _make_pool(execute_result="UPDATE 1")
    ok = await gw.heartbeat("my-node-123", active_requests=3, queue_depth=7, pool=pool)

    assert ok is True
    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "UPDATE fazle_runtime_nodes" in sql
    assert "last_seen" in sql
    # Positional args: node_id, active_requests, queue_depth
    args = conn.execute.call_args[0][1:]
    assert args[0] == "my-node-123"
    assert args[1] == 3
    assert args[2] == 7


@pytest.mark.asyncio
async def test_heartbeat_returns_false_on_db_error():
    """heartbeat() returns False (never raises) when DB throws."""
    from shared import runtime_gateway as gw

    pool, conn = _make_pool()
    conn.execute.side_effect = RuntimeError("connection refused")

    ok = await gw.heartbeat("node-down", pool=pool)
    assert ok is False


@pytest.mark.asyncio
async def test_stale_node_marked_offline():
    """mark_stale_nodes executes UPDATE ... RETURNING and emits NODE_OFFLINE."""
    from shared import runtime_gateway as gw, events

    received_offline: list = []

    async def on_offline(evt):
        received_offline.append(evt.payload.get("reason"))

    events.subscribe(events.NODE_OFFLINE, on_offline)
    try:
        stale_row = {
            "node_id":    "old-node-999",
            "app_name":   "payroll-engine",
            "queue_depth": 0,
        }
        pool, conn = _make_pool(fetch_rows=[stale_row])
        conn.fetch = AsyncMock(return_value=[stale_row])

        stale_ids = await gw.mark_stale_nodes(stale_after_seconds=90, pool=pool)

        assert "old-node-999" in stale_ids
        await events.flush_pending(timeout=2.0)
        assert "stale_heartbeat" in received_offline
    finally:
        events.unsubscribe(events.NODE_OFFLINE, on_offline)


@pytest.mark.asyncio
async def test_stale_heartbeat_releases_queue_items():
    """
    When a node goes stale, _release_claimed_queue_items should be called.
    We verify the UPDATE on fazle_message_queue is attempted.
    """
    from shared import runtime_gateway as gw

    execute_calls: list = []

    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[
        {"node_id": "dead-node", "app_name": "escort-roster", "queue_depth": 3}
    ])

    async def fake_execute(sql, *args):
        execute_calls.append(sql)
        return "UPDATE 3"

    conn.execute = fake_execute

    ctx  = _AcquireCtx(conn)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)

    await gw.mark_stale_nodes(pool=pool)

    # One of the execute calls should reference fazle_message_queue
    queue_release = [s for s in execute_calls if "fazle_message_queue" in s]
    assert len(queue_release) >= 1, (
        f"Expected a queue-release UPDATE; got execute calls: {execute_calls}"
    )


@pytest.mark.asyncio
async def test_node_recovery_after_stale():
    """
    Simulate: node goes stale → register_node called again → status = online.
    The UPSERT sets status='online' unconditionally.
    """
    from shared import runtime_gateway as gw, events

    received: list = []

    async def on_online(evt):
        received.append(evt.event_type)

    events.subscribe(events.NODE_ONLINE, on_online)
    try:
        pool, conn = _make_pool()
        await gw.register_node("escort-roster", role="worker", pool=pool)
        await events.flush_pending(timeout=2.0)

        # Verify the UPSERT includes status='online' (recovery path)
        sql = conn.execute.call_args[0][0]
        assert "status        = 'online'" in sql or "status = 'online'" in sql.replace("'online'", "'online'")
        assert events.NODE_ONLINE in received
    finally:
        events.unsubscribe(events.NODE_ONLINE, on_online)


@pytest.mark.asyncio
async def test_deregister_marks_offline():
    """deregister_node marks status=offline and emits NODE_OFFLINE."""
    from shared import runtime_gateway as gw, events

    received: list = []

    async def on_offline(evt):
        received.append(evt.payload.get("reason"))

    events.subscribe(events.NODE_OFFLINE, on_offline)
    try:
        pool, conn = _make_pool()
        await gw.deregister_node("fazle-core-host-1234", pool=pool)

        conn.execute.assert_called_once()
        sql  = conn.execute.call_args[0][0]
        args = conn.execute.call_args[0][1:]
        assert "status = 'offline'" in sql
        assert args[0] == "fazle-core-host-1234"

        await events.flush_pending(timeout=2.0)
        assert "clean_shutdown" in received
    finally:
        events.unsubscribe(events.NODE_OFFLINE, on_offline)


@pytest.mark.asyncio
async def test_heartbeat_reconnect_event_after_3_failures():
    """
    heartbeat_loop emits RECONNECTING after 3 consecutive heartbeat failures.
    """
    from shared import runtime_gateway as gw, events

    reconnect_received: list = []

    async def on_reconnecting(evt):
        reconnect_received.append(evt.payload.get("consecutive_failures"))

    events.subscribe(events.RECONNECTING, on_reconnecting)
    try:
        # Pool that always fails
        pool, conn = _make_pool()
        conn.execute.side_effect = RuntimeError("DB down")

        # Run the heartbeat loop manually for 4 cycles (using very short interval)
        # We'll patch asyncio.sleep to skip waits
        call_count = 0

        async def fake_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count > 4:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", fake_sleep):
            try:
                await gw.heartbeat_loop(
                    "flaky-node",
                    interval=1,
                    pool=pool,
                )
            except asyncio.CancelledError:
                pass

        await events.flush_pending(timeout=2.0)
        # After 3+ failures RECONNECTING should have been emitted
        assert len(reconnect_received) >= 1, "Expected RECONNECTING event"
        assert reconnect_received[0] >= 3
    finally:
        events.unsubscribe(events.RECONNECTING, on_reconnecting)


@pytest.mark.asyncio
async def test_get_active_nodes_shape():
    """Legacy all-rows reader returns dicts with all expected keys."""
    from shared import runtime_gateway as gw
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)

    fake_rows = [
        {
            "node_id":         "fazle-core-host-11",
            "app_name":        "fazle-core",
            "role":            "orchestrator",
            "status":          "online",
            "last_seen":       now,
            "registered_at":   now,
            "version":         "1.1.0",
            "active_requests": 2,
            "queue_depth":     5,
            "metadata_json":   json.dumps({"port": 8200}),
        },
    ]

    pool, conn = _make_pool(fetch_rows=fake_rows)
    conn.fetch = AsyncMock(return_value=fake_rows)
    nodes = await gw.get_active_nodes(pool=pool)

    assert len(nodes) == 1
    n = nodes[0]
    for key in ("node_id", "app_name", "role", "status", "last_seen",
                "age_s", "registered_at", "version", "active_requests",
                "queue_depth", "metadata"):
        assert key in n, f"Missing key: {key}"

    assert n["app_name"] == "fazle-core"
    assert n["active_requests"] == 2
    assert isinstance(n["metadata"], dict)
    assert n["metadata"]["port"] == 8200


@pytest.mark.asyncio
async def test_get_current_runtime_nodes_query():
    """Current reader must query only fresh online rows."""
    from shared import runtime_gateway as gw

    fake_rows = [_make_node_row(status="online")]
    pool, conn = _make_pool(fetch_rows=fake_rows)
    conn.fetch = AsyncMock(return_value=fake_rows)

    nodes = await gw.get_current_runtime_nodes(pool=pool)

    assert len(nodes) == 1
    sql = conn.fetch.call_args[0][0]
    args = conn.fetch.call_args[0][1:]
    assert "WHERE status = 'online'" in sql
    assert "last_seen >= NOW()" in sql
    assert args[0] == str(gw.STALE_THRESHOLD_S)


@pytest.mark.asyncio
async def test_get_historical_runtime_nodes_query():
    """History reader must exclude fresh current runtime truth."""
    from shared import runtime_gateway as gw

    fake_rows = [_make_node_row(status="offline")]
    pool, conn = _make_pool(fetch_rows=fake_rows)
    conn.fetch = AsyncMock(return_value=fake_rows)

    nodes = await gw.get_historical_runtime_nodes(pool=pool)

    assert len(nodes) == 1
    sql = conn.fetch.call_args[0][0]
    args = conn.fetch.call_args[0][1:]
    assert "WHERE status <> 'online'" in sql
    assert "OR last_seen < NOW()" in sql
    assert args[0] == str(gw.STALE_THRESHOLD_S)


@pytest.mark.asyncio
async def test_node_offline_event_payload():
    """NODE_OFFLINE event payload includes node_id and reason."""
    from shared import runtime_gateway as gw, events

    payloads: list = []

    async def capture(evt):
        payloads.append(evt.payload)

    events.subscribe(events.NODE_OFFLINE, capture)
    try:
        pool, _ = _make_pool()
        await gw.deregister_node("escort-roster-host-99", pool=pool)
        await events.flush_pending(timeout=2.0)

        assert len(payloads) == 1
        assert payloads[0]["node_id"]  == "escort-roster-host-99"
        assert payloads[0]["reason"]   == "clean_shutdown"
    finally:
        events.unsubscribe(events.NODE_OFFLINE, capture)
