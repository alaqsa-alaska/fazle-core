"""Unit tests — modules/social_auto_reply/planner_worker draft-routing gate.

2026-07-15 Phase 3: the social daemon must never auto-send. Every reply the
planner would previously have pushed into social_reply_queue for real
dispatch must instead become a fazle_draft_replies row plus an admin
notification. See docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.unit


class TestDraftInsteadOfSend:
    async def test_creates_draft_and_notifies_admin(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner

        fake_create_draft = AsyncMock(return_value=42)
        fake_enqueue = AsyncMock(return_value=99)
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)
        monkeypatch.setattr(planner, "_primary_admin_number", lambda: "8801880446111")

        draft_id = await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="আসসালামু আলাইকুম, ধন্যবাদ।",
            intent="greeting",
            sender_role="UNKNOWN",
        )

        assert draft_id == 42
        fake_create_draft.assert_awaited_once()
        create_kwargs = fake_create_draft.call_args.kwargs
        assert create_kwargs["sender"] == "8801711111111"
        assert create_kwargs["bridge"] == "meta_whatsapp"
        assert create_kwargs["draft_text"] == "আসসালামু আলাইকুম, ধন্যবাদ।"
        assert create_kwargs["source_module"] == "social_auto_reply.planner_worker"

        fake_enqueue.assert_awaited_once()
        enqueue_args, enqueue_kwargs = fake_enqueue.call_args
        assert enqueue_args[0] == "8801880446111"
        assert "42" in enqueue_args[1]
        assert enqueue_kwargs["purpose"] == "social-draft-pending"
        assert enqueue_kwargs["idempotency_key"] == "social-draft-notify:42"

    async def test_deduped_draft_does_not_notify_admin(self, monkeypatch):
        """create_draft_reply returns None on its own 5-minute collision
        guard -- no draft means no admin notification either."""
        import modules.social_auto_reply.planner_worker as planner

        fake_create_draft = AsyncMock(return_value=None)
        fake_enqueue = AsyncMock()
        monkeypatch.setattr(planner, "create_draft_reply", fake_create_draft)
        monkeypatch.setattr(planner, "enqueue_outbound", fake_enqueue)

        draft_id = await planner._draft_instead_of_send(
            platform="meta_whatsapp",
            target_id="8801711111111",
            reply_text="dup reply",
            intent="greeting",
            sender_role="UNKNOWN",
        )

        assert draft_id is None
        fake_enqueue.assert_not_awaited()

    async def test_never_imports_social_reply_queue_enqueue(self):
        """Hard guarantee: the planner module must not reference the real
        send-queue enqueue function anywhere -- auto-send is fully retired
        for this module, not just bypassed in one branch."""
        import modules.social_auto_reply.planner_worker as planner

        assert not hasattr(planner, "enqueue_reply")
