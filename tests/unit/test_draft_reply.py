"""
Unit tests for shared.draft_reply.create_draft_reply().

All DB interactions are mocked — no live PostgreSQL connection required.
Tests verify:
  - blank sender returns None without touching the DB
  - lock key pattern includes sender + bridge
  - lock not acquired → None returned, no INSERT attempted
  - collision found → None returned, no INSERT attempted
  - happy path → int draft id returned
  - DB errors inside lock → None returned (fail closed, not fail open)
"""
from __future__ import annotations

import sys
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

_CORE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _CORE_ROOT not in sys.path:
    sys.path.insert(0, _CORE_ROOT)

from shared.draft_reply import create_draft_reply


# ── Helpers ────────────────────────────────────────────────────────────────────

def _locked_yields(got_lock: bool):
    """Return a mock async context manager for shared.locks.locked that yields got_lock."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _cm(*args, **kwargs):
        yield got_lock

    return _cm


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_blank_sender_returns_none():
    """Empty sender must short-circuit before any lock or DB call."""
    result = await create_draft_reply(
        sender="   ",
        bridge="bridge1",
        draft_text="hello",
        role="employee",
        intent="salary_query",
        source_module="test",
    )
    assert result is None


@pytest.mark.asyncio
async def test_blank_sender_no_lock_acquired():
    """Blank sender returns None without ever touching the lock layer."""
    with patch("shared.draft_reply.locked") as mock_locked:
        result = await create_draft_reply(
            sender="",
            bridge="bridge1",
            draft_text="hello",
            role="employee",
            intent="salary_query",
        )
    assert result is None
    mock_locked.assert_not_called()


@pytest.mark.asyncio
async def test_lock_key_includes_sender_and_bridge():
    """Lock key must be draft_reply:{bridge}:{sender}."""
    captured_key = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def capturing_locked(key, *, worker_id, ttl_s):
        captured_key.append(key)
        yield False  # don't need to proceed further

    with patch("shared.draft_reply.locked", capturing_locked):
        await create_draft_reply(
            sender="8801999000111",
            bridge="bridge2",
            draft_text="test",
            role="employee",
            intent="greeting",
            source_module="router",
        )

    assert len(captured_key) == 1
    assert captured_key[0] == "draft_reply:bridge2:8801999000111"


@pytest.mark.asyncio
async def test_lock_not_acquired_returns_none_no_insert():
    """When the lock is already held, return None without running SELECT or INSERT."""
    with patch("shared.draft_reply.locked", _locked_yields(False)):
        with patch("shared.draft_reply.fetch_val") as mock_fetch:
            with patch("shared.draft_reply.execute"):
                result = await create_draft_reply(
                    sender="8801111222333",
                    bridge="bridge1",
                    draft_text="body",
                    role="employee",
                    intent="salary_query",
                    source_module="bridge_poller",
                )

    assert result is None
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_duplicate_found_returns_none_no_insert():
    """When a pending draft already exists, suppress and do not INSERT."""
    with patch("shared.draft_reply.locked", _locked_yields(True)):
        with patch("shared.draft_reply.fetch_val", new_callable=AsyncMock) as mock_fetch:
            # First call = collision SELECT returning an existing id
            mock_fetch.return_value = 42

            result = await create_draft_reply(
                sender="8801111222333",
                bridge="bridge1",
                draft_text="body",
                role="employee",
                intent="salary_query",
                source_module="message_router",
            )

    assert result is None
    # Only the SELECT call should have been made — no INSERT RETURNING
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_happy_path_returns_new_draft_id():
    """When no duplicate exists and lock is held, INSERT succeeds and returns id."""
    with patch("shared.draft_reply.locked", _locked_yields(True)):
        with patch("shared.draft_reply.fetch_val", new_callable=AsyncMock) as mock_fetch:
            # First call = collision SELECT → no duplicate
            # Second call = INSERT RETURNING id
            mock_fetch.side_effect = [None, 99]

            result = await create_draft_reply(
                sender="8801555666777",
                bridge="meta",
                draft_text="Salary is processing...",
                role="employee",
                intent="salary_query",
                source_module="message_router",
            )

    assert result == 99
    assert mock_fetch.call_count == 2


@pytest.mark.asyncio
async def test_collision_check_db_error_fails_closed():
    """DB error in the collision SELECT must return None (fail closed, not fail open)."""
    with patch("shared.draft_reply.locked", _locked_yields(True)):
        with patch("shared.draft_reply.fetch_val", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("DB connection lost")

            result = await create_draft_reply(
                sender="8801777888999",
                bridge="bridge2",
                draft_text="body",
                role="employee",
                intent="salary_query",
            )

    assert result is None
    # fetch_val was called once (the SELECT) — no INSERT attempted
    assert mock_fetch.call_count == 1


@pytest.mark.asyncio
async def test_insert_db_error_returns_none():
    """DB error during INSERT returns None without raising."""
    with patch("shared.draft_reply.locked", _locked_yields(True)):
        with patch("shared.draft_reply.fetch_val", new_callable=AsyncMock) as mock_fetch:
            # Collision SELECT → no duplicate
            # INSERT → raises
            mock_fetch.side_effect = [None, Exception("constraint violation")]

            result = await create_draft_reply(
                sender="8801000111222",
                bridge="bridge1",
                draft_text="body",
                role="employee",
                intent="recruitment",
            )

    assert result is None


@pytest.mark.asyncio
async def test_sender_is_stripped_before_lock():
    """Whitespace around sender must be stripped; the stripped value reaches the lock key."""
    captured_key = []

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def capturing_locked(key, *, worker_id, ttl_s):
        captured_key.append(key)
        yield False

    with patch("shared.draft_reply.locked", capturing_locked):
        await create_draft_reply(
            sender="  8801234567890  ",
            bridge="meta",
            draft_text="x",
            role="new_lead",
            intent="greeting",
        )

    assert captured_key[0] == "draft_reply:meta:8801234567890"


@pytest.mark.asyncio
async def test_module_imports_cleanly():
    """Smoke test: the module and public function are importable."""
    from shared.draft_reply import create_draft_reply as fn
    import inspect
    sig = inspect.signature(fn)
    assert "sender" in sig.parameters
    assert "bridge" in sig.parameters
    assert "draft_text" in sig.parameters
    assert "source_module" in sig.parameters
