"""Unit tests — social_auto_reply backlog-flood guard (activation watermark).

2026-07-15 Phase 4: activating the social daemon must never sweep the
month-plus of pending backlog accumulated while it was disabled. Only events
received at/after the daemon's first-activation watermark may be processed.
See docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md Phase 4 part B.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.unit


class TestActivationWatermark:
    async def test_returns_existing_watermark_without_reinserting(self, monkeypatch):
        import modules.social_auto_reply.state_tracker as state_tracker

        existing = datetime(2026, 7, 15, 9, 0, 0, tzinfo=timezone.utc)
        fake_fetch_one = AsyncMock(return_value={"last_checked_at": existing})
        fake_execute = AsyncMock()
        monkeypatch.setattr(state_tracker, "fetch_one", fake_fetch_one)
        monkeypatch.setattr(state_tracker, "execute", fake_execute)

        watermark = await state_tracker.get_activation_watermark()

        assert watermark == existing
        fake_execute.assert_not_awaited()

    async def test_creates_watermark_on_first_call(self, monkeypatch):
        import modules.social_auto_reply.state_tracker as state_tracker

        created = datetime(2026, 7, 15, 10, 30, 0, tzinfo=timezone.utc)
        calls = {"n": 0}

        async def fake_fetch_one(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # no row yet
            return {"last_checked_at": created}  # re-read after insert

        fake_execute = AsyncMock()
        monkeypatch.setattr(state_tracker, "fetch_one", fake_fetch_one)
        monkeypatch.setattr(state_tracker, "execute", fake_execute)

        watermark = await state_tracker.get_activation_watermark()

        fake_execute.assert_awaited_once()
        assert "activation_watermark" in fake_execute.call_args.args[0]
        assert "ON CONFLICT (state_key) DO NOTHING" in fake_execute.call_args.args[0]
        assert watermark == created


class TestPendingThreadsRespectsWatermark:
    async def test_pending_threads_passes_watermark_to_query(self, monkeypatch):
        import modules.social_auto_reply.conversation_history as ch

        watermark = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(ch.state_tracker, "get_activation_watermark", AsyncMock(return_value=watermark))
        fake_fetch_all = AsyncMock(return_value=[])
        monkeypatch.setattr(ch, "fetch_all", fake_fetch_all)

        await ch.pending_threads(compression_seconds=25, limit=10)

        fake_fetch_all.assert_awaited_once()
        query, compression_seconds, limit, passed_watermark = fake_fetch_all.call_args.args
        assert "received_at >= $3" in query
        assert compression_seconds == 25
        assert limit == 10
        assert passed_watermark == watermark

    async def test_pending_events_passes_watermark_to_query(self, monkeypatch):
        import modules.social_auto_reply.conversation_history as ch

        watermark = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(ch.state_tracker, "get_activation_watermark", AsyncMock(return_value=watermark))
        fake_fetch_all = AsyncMock(return_value=[])
        monkeypatch.setattr(ch, "fetch_all", fake_fetch_all)

        await ch.pending_events("meta_whatsapp", "8801711111111")

        fake_fetch_all.assert_awaited_once()
        query, platform, target_id, passed_watermark = fake_fetch_all.call_args.args
        assert "received_at >= $3" in query
        assert platform == "meta_whatsapp"
        assert target_id == "8801711111111"
        assert passed_watermark == watermark
