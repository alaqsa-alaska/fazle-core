from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_health_monitor_error_path_returns_degraded():
    """WBOM freeze check failure must not return a false 'ok' status."""
    import modules.health_monitor as hm

    with patch.object(hm, "fetch_val", new=AsyncMock(return_value=1)), \
         patch.object(hm, "check_wbom_write_delta", new=AsyncMock(side_effect=RuntimeError("db error"))), \
         patch.object(hm, "get_indexed_kb_count", new=AsyncMock(return_value=150)):
        result = await hm.check_system_health()

    assert result["status"] == "degraded"
    assert str(result["checks"].get("wbom_freeze", "")).startswith("error:")


@pytest.mark.asyncio
async def test_history_sync_duplicate_message_counts_reused_group(tmp_path):
    """Duplicate sender+timestamp message should increment groups_reused, not groups_created."""
    import modules.escort_roster.history_sync as hs

    sample = tmp_path / "conversation.txt"
    sample.write_text("dummy", encoding="utf-8")

    msg = hs.WaMessage(
        ts=hs.datetime(2026, 7, 4, 10, 0, tzinfo=hs.timezone.utc),
        direction="outbound",
        sender_alias="YOU",
        text="escort order",
    )
    rec = hs.LighterRecord(lighter_vessel="MV TEST")

    with patch.object(hs, "parse_conversation_file", return_value=[msg]), \
         patch.object(hs, "parse_message_to_lighters", return_value=[rec]), \
         patch.object(hs, "_match_to_program", new=AsyncMock(return_value=(None, 0.0, ""))), \
         patch.object(hs, "fetch_val", new=AsyncMock(side_effect=[None, 9001])), \
         patch.object(hs, "fetch_one", new=AsyncMock(return_value={"group_id": 701})):
        result = await hs.sync_conversation_history(str(sample), dry_run=False)

    assert result["groups_created"] == 0
    assert result["groups_reused"] == 1
    assert result["lighters_created"] == 1


def test_error_response_factory_returns_fresh_timestamp():
    """Factory responses should not reuse stale timestamps or shared dicts."""
    from shared.error_response import err_employee_not_found

    one = err_employee_not_found()
    two = err_employee_not_found()

    assert isinstance(one, dict) and isinstance(two, dict)
    assert one is not two
    assert "timestamp" in one and "timestamp" in two
    assert one["timestamp"] <= two["timestamp"]


@pytest.mark.asyncio
async def test_stale_worker_startup_and_shutdown_lifecycle():
    """Worker should start with a task handle and stop cleanly on cancellation."""
    import app.main as main

    with patch("modules.escort_roster.db.expire_stale_drafts", new=AsyncMock(return_value={"expired": 0})):
        task = main._start_stale_draft_expiry_worker()
        await asyncio.sleep(0)
        assert task is not None
        await main._stop_background_task(task, "test_worker")
        assert task.done()
