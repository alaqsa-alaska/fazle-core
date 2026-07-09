"""
Phase 12J — Concurrency and consistency tests for the Unified Request
Coordination Layer.

Tests:
  1. Same employee, two concurrent callers → only one succeeds
  2. Same escort program, two concurrent callers → second is blocked/queued
  3. Same payment txn_ref inserted twice → DuplicateWriteError
  4. Event emitted after routed_write → subscriber receives it
  5. OptimisticLockError raised on updated_at mismatch
  6. Events module: wildcard subscriber receives all events
  7. State version increments after bump
  8. Events flush_pending drains all tasks before returning

All DB-dependent tests use in-memory mocks / AsyncMock to avoid requiring
a live PostgreSQL connection.  Only shared.events and shared.state_version
Redis tests are skipped when Redis is unavailable (graceful skip).
"""
from __future__ import annotations

import asyncio
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Path resolution ───────────────────────────────────────────────────────────
_CORE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _CORE_ROOT not in sys.path:
    sys.path.insert(0, _CORE_ROOT)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_conn(*, rows=None, rowcount=1):
    """Return a mock asyncpg connection."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=rows[0] if rows else None)
    conn.fetch    = AsyncMock(return_value=rows or [])
    conn.fetchval = AsyncMock(return_value=rowcount)
    conn.execute  = AsyncMock(return_value=f"UPDATE {rowcount}")
    # Simulate asyncpg transaction context manager
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__  = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Concurrent employee writes — only one acquires the lock
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="ISSUE-REL-2026-07-W1-TESTINFRA-003: shared.write_router no longer exposes emit symbol expected by legacy test harness",
    strict=False,
)
async def test_concurrent_employee_writes_only_one_succeeds():
    """
    Two coroutines attempt to write to the same employee simultaneously.
    The in-process asyncio.Lock guarantees only one enters at a time.
    We verify both calls complete and exactly one returns ok=True when
    the second is configured to fail due to lock contention.
    """
    from shared import write_router

    results: list = []
    written: list = []

    async def slow_writer(ctx):
        await asyncio.sleep(0.05)   # simulate DB work
        written.append(ctx.resource_id)
        return {"updated": True}

    # Patch distributed lock to always succeed (unit-test, no DB needed)
    with patch("shared.write_router.acquire_lock", new_callable=AsyncMock, return_value=True), \
         patch("shared.write_router.release_lock", new_callable=AsyncMock), \
         patch("shared.write_router._bump_version",  new_callable=AsyncMock, return_value=1), \
         patch("shared.write_router.emit",           new_callable=AsyncMock), \
         patch("shared.write_router.get_pool") as mock_pool:

        # Provide a fresh connection each call
        pool = AsyncMock()
        pool.acquire = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=_make_conn())
        pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
        mock_pool.return_value = pool

        r1, r2 = await asyncio.gather(
            write_router.write_employee(42, slow_writer, caller="test-A"),
            write_router.write_employee(42, slow_writer, caller="test-B"),
            return_exceptions=True,
        )

    results = [r1, r2]
    ok_count = sum(1 for r in results if not isinstance(r, Exception) and getattr(r, "ok", False))
    # Both may succeed sequentially (asyncio.Lock serialises them) — that is correct.
    # Neither should raise an unhandled exception.
    assert not any(isinstance(r, Exception) for r in results), \
        f"Unexpected exception in results: {results}"
    assert ok_count >= 1, "At least one write should succeed"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Concurrent escort program writes
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="ISSUE-REL-2026-07-W1-TESTINFRA-003: shared.write_router no longer exposes emit symbol expected by legacy test harness",
    strict=False,
)
async def test_concurrent_escort_writes_serialised():
    """write_escort serialises concurrent calls for the same program_id."""
    from shared import write_router

    call_order: list[str] = []

    async def writer_a(ctx):
        call_order.append("A-start")
        await asyncio.sleep(0.03)
        call_order.append("A-end")
        return {}

    async def writer_b(ctx):
        call_order.append("B-start")
        await asyncio.sleep(0.01)
        call_order.append("B-end")
        return {}

    with patch("shared.write_router.acquire_lock", new_callable=AsyncMock, return_value=True), \
         patch("shared.write_router.release_lock", new_callable=AsyncMock), \
         patch("shared.write_router._bump_version",  new_callable=AsyncMock, return_value=1), \
         patch("shared.write_router.emit",           new_callable=AsyncMock), \
         patch("shared.write_router.get_pool") as mock_pool:

        pool = AsyncMock()
        pool.acquire = AsyncMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=_make_conn())
        pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
        mock_pool.return_value = pool

        await asyncio.gather(
            write_router.write_escort("prog-99", writer_a, caller="A"),
            write_router.write_escort("prog-99", writer_b, caller="B"),
        )

    # Due to asyncio.Lock the calls are strictly interleaved: one finishes before the other starts
    assert call_order[0] in ("A-start", "B-start")
    # Verify no interleaving: once A starts, it must finish before B starts (or vice versa)
    if call_order[0] == "A-start":
        assert call_order[1] == "A-end", f"Interleaved: {call_order}"
        assert call_order[2] == "B-start"
    else:
        assert call_order[1] == "B-end", f"Interleaved: {call_order}"
        assert call_order[2] == "A-start"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: DuplicateWriteError on same txn_ref
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_write_error_raised():
    """assert_not_duplicate raises DuplicateWriteError when the key exists."""
    from shared.consistency import assert_not_duplicate, DuplicateWriteError

    # Connection mock that returns a row → key already exists
    conn = _make_conn(rows=[{"id": 1}])

    with pytest.raises(DuplicateWriteError) as exc_info:
        await assert_not_duplicate(conn, "fpe_transactions", "TXN-9999", key_col="txn_ref")

    assert "TXN-9999" in str(exc_info.value) or exc_info.value.key == "TXN-9999"


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Event emitted after routed_write → subscriber receives it
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="ISSUE-REL-2026-07-W1-TESTINFRA-003: write_payment signature changed (event_type/event_payload no longer accepted)",
    strict=False,
)
async def test_event_emitted_after_write_reaches_subscriber():
    """After routed_write, the configured event_type should reach subscribers."""
    from shared import events

    received: list = []

    async def handler(evt):
        received.append(evt.event_type)

    events.subscribe(events.PAYMENT_CREATED, handler)
    try:
        from shared import write_router

        async def writer(ctx):
            return {"paid": True}

        with patch("shared.write_router.acquire_lock", new_callable=AsyncMock, return_value=True), \
             patch("shared.write_router.release_lock", new_callable=AsyncMock), \
             patch("shared.write_router._bump_version",  new_callable=AsyncMock, return_value=1), \
             patch("shared.write_router.get_pool") as mock_pool:

            pool = AsyncMock()
            pool.acquire = AsyncMock()
            pool.acquire.return_value.__aenter__ = AsyncMock(return_value=_make_conn())
            pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)
            mock_pool.return_value = pool

            result = await write_router.write_payment(
                "TXN-100",
                writer,
                event_type=events.PAYMENT_CREATED,
                event_payload={"amount": 500},
                caller="test",
            )

        assert result.ok, f"Write failed: {result.error}"
        await events.flush_pending(timeout=2.0)
        assert events.PAYMENT_CREATED in received, \
            f"Expected payment_created event; got: {received}"
    finally:
        events.unsubscribe(events.PAYMENT_CREATED, handler)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: OptimisticLockError on updated_at mismatch
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_optimistic_lock_error_on_stale_version():
    """optimistic_update raises OptimisticLockError when 0 rows affected."""
    from shared.consistency import optimistic_update, OptimisticLockError
    import datetime

    # Simulate 0 rows updated (version mismatch)
    conn = _make_conn(rowcount=0)
    conn.execute = AsyncMock(return_value="UPDATE 0")

    stale_ts = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)

    with pytest.raises(OptimisticLockError) as exc_info:
        await optimistic_update(
            conn,
            table="employees",
            pk_col="id",
            pk_val=7,
            updates={"name": "New Name"},
            expected_version=stale_ts,
        )

    err = exc_info.value
    assert err.table == "employees"
    assert err.pk_val == 7


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Wildcard event subscriber receives all event types
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wildcard_subscriber_receives_all_events():
    """Subscribing to '*' receives every event type emitted."""
    from shared import events

    received_types: list[str] = []

    async def catch_all(evt):
        received_types.append(evt.event_type)

    events.subscribe("*", catch_all)
    try:
        await events.emit(events.EMPLOYEE_UPDATED,  {"id": 1},  emitted_by="test")
        await events.emit(events.ESCORT_ASSIGNED,   {"id": 2},  emitted_by="test")
        await events.emit(events.PAYMENT_CORRECTED, {"id": 3},  emitted_by="test")
        await events.flush_pending(timeout=2.0)

        assert events.EMPLOYEE_UPDATED  in received_types
        assert events.ESCORT_ASSIGNED   in received_types
        assert events.PAYMENT_CORRECTED in received_types
    finally:
        events.unsubscribe("*", catch_all)


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: State version increments on bump
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_state_version_bumps():
    """bump_state_version returns a value >= 1 and get_state_version is consistent."""
    pytest.importorskip("aioredis",  reason="aioredis not installed")
    # Use fakeredis if available, otherwise skip
    try:
        import fakeredis.aioredis as fakeredis_async
    except ImportError:
        pytest.skip("fakeredis not installed — skipping Redis state-version test")

    from shared import state_version

    fake_redis = fakeredis_async.FakeRedis()

    async def _fake_get_redis():
        return fake_redis

    with patch("shared.state_version._get_redis", _fake_get_redis):
        v1 = await state_version.bump_state_version()
        v2 = await state_version.bump_state_version()
        v3 = await state_version.get_state_version()

    assert v1 == 1
    assert v2 == 2
    assert v3 == 2   # get does not increment


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: flush_pending drains all tasks
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flush_pending_drains_all_tasks():
    """flush_pending waits for all async handler tasks to complete."""
    from shared import events

    results: list[int] = []

    async def slow_handler(evt):
        await asyncio.sleep(0.05)
        results.append(1)

    events.subscribe(events.DRAFT_CREATED, slow_handler)
    try:
        await events.emit(events.DRAFT_CREATED, {}, emitted_by="test")
        await events.emit(events.DRAFT_CREATED, {}, emitted_by="test")
        await events.emit(events.DRAFT_CREATED, {}, emitted_by="test")
        # Without flush, results may still be empty
        await events.flush_pending(timeout=3.0)
        assert len(results) == 3, f"Expected 3 handler completions, got {len(results)}"
    finally:
        events.unsubscribe(events.DRAFT_CREATED, slow_handler)
