"""Stage G5/G6/G7/G27 (2026-08-28) -- modules.supervised_draft."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from modules.supervised_draft import notify_admin_of_pending_draft

pytestmark = pytest.mark.asyncio


class TestNotifyAdminOfPendingDraft:
    async def test_draft_not_found_returns_error_not_exception(self):
        with patch("app.database.fetch_one", new=AsyncMock(return_value=None)):
            result = await notify_admin_of_pending_draft(999)
        assert result["ok"] is False

    async def test_already_sent_draft_refused(self):
        with patch("app.database.fetch_one", new=AsyncMock(return_value={
            "id": 5, "recipient": "x", "reply_text": "y", "source": "bridge2", "status": "sent", "meta": {},
        })):
            result = await notify_admin_of_pending_draft(5)
        assert result["ok"] is False
        assert "not pending" in result["error"]

    async def test_no_admin_configured_returns_error(self):
        with patch("app.database.fetch_one", new=AsyncMock(return_value={
            "id": 5, "recipient": "x", "reply_text": "y", "source": "bridge2", "status": "pending", "meta": {},
        })), \
             patch("modules.message_router.get_primary_admin", return_value=""):
            result = await notify_admin_of_pending_draft(5)
        assert result["ok"] is False

    async def test_pending_draft_pushes_card_with_category_and_idempotency_key(self):
        enqueue_mock = AsyncMock(return_value={"queued": True})
        with patch("app.database.fetch_one", new=AsyncMock(return_value={
            "id": 5, "recipient": "8801700000000", "reply_text": "draft body",
            "source": "bridge2", "status": "pending",
            "meta": {"sender_name": "Client A", "sender_message": "hello", "ai_reply_raw": "draft body"},
        })), \
             patch("modules.message_router.get_primary_admin", return_value="8801880446111"), \
             patch("modules.outbound.enqueue", new=enqueue_mock):
            result = await notify_admin_of_pending_draft(5, category="Client")
        assert result["ok"] is True
        assert result["admin"] == "8801880446111"
        enqueue_mock.assert_awaited_once()
        args, kwargs = enqueue_mock.await_args
        assert args[0] == "8801880446111"
        assert "Category: Client" in args[1]
        assert "Draft No: 5" in args[1]
        assert kwargs["idempotency_key"] == "draft-review-notify-5"

    async def test_idempotency_key_is_stable_per_draft(self):
        """Calling twice for the same draft must use the same
        idempotency_key -- outbound.enqueue()'s own idempotency then
        prevents a second real send."""
        enqueue_mock = AsyncMock(return_value={"queued": True})
        with patch("app.database.fetch_one", new=AsyncMock(return_value={
            "id": 5, "recipient": "8801700000000", "reply_text": "draft body",
            "source": "bridge2", "status": "pending", "meta": {},
        })), \
             patch("modules.message_router.get_primary_admin", return_value="8801880446111"), \
             patch("modules.outbound.enqueue", new=enqueue_mock):
            await notify_admin_of_pending_draft(5)
            await notify_admin_of_pending_draft(5)
        k1 = enqueue_mock.await_args_list[0].kwargs["idempotency_key"]
        k2 = enqueue_mock.await_args_list[1].kwargs["idempotency_key"]
        assert k1 == k2 == "draft-review-notify-5"

    async def test_no_category_omits_category_line(self):
        enqueue_mock = AsyncMock(return_value={"queued": True})
        with patch("app.database.fetch_one", new=AsyncMock(return_value={
            "id": 5, "recipient": "8801700000000", "reply_text": "draft body",
            "source": "bridge2", "status": "pending", "meta": {},
        })), \
             patch("modules.message_router.get_primary_admin", return_value="8801880446111"), \
             patch("modules.outbound.enqueue", new=enqueue_mock):
            await notify_admin_of_pending_draft(5)
        args, _ = enqueue_mock.await_args
        assert "Category:" not in args[1]
