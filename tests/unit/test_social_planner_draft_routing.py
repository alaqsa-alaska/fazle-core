"""Unit tests — modules/social_auto_reply/planner_worker draft-routing gate.

2026-07-15 Phase 4 (Owner-confirmed policy: Selective, not blanket): only
senders matching the existing DRAFT_ALWAYS_ROLES/PHONES/NAMES config get
routed to a fazle_draft_replies row + admin notification instead of a real
send. Everyone else gets enqueue_reply() -> social_reply_queue, same as
pre-2026-06-23 behavior. See
docs/TASK_BRIEF_chat_sync_and_reply_gap_2026-07-15.md.
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

    async def test_enqueue_reply_is_wired_for_non_draft_always_senders(self):
        """2026-07-15 Phase 4 policy reversal: unlike the interim blanket-draft
        commit, real auto-send is NOT retired -- it's restored for senders
        that don't match DRAFT_ALWAYS_*, per Owner-confirmed Selective policy."""
        import modules.social_auto_reply.planner_worker as planner
        from modules.social_auto_reply.send_queue import enqueue_reply

        assert planner.enqueue_reply is enqueue_reply


class TestSelectiveDraftRoutingGate:
    """_is_draft_always_sender() decides the draft-vs-real-send branch --
    must reuse modules.bridge_poller._is_draft_always(), not reimplement it."""

    async def test_draft_always_role_routes_to_draft(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain
        import modules.bridge_poller as bridge_poller

        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"role": "accountant", "display_name": "Test Accountant"}),
        )
        # Exercise the real _is_draft_always() -- "accountant" is in the
        # default DRAFT_ALWAYS_ROLES set, so no need to mock it too.
        result = await planner._is_draft_always_sender(
            "8801811111111", {"message_text": "hi"},
        )
        assert result is True

    async def test_unknown_role_routes_to_real_send(self, monkeypatch):
        import modules.social_auto_reply.planner_worker as planner
        import modules.identity_brain as identity_brain

        monkeypatch.setattr(
            identity_brain, "detect_identity",
            AsyncMock(return_value={"role": "unknown", "display_name": None}),
        )
        result = await planner._is_draft_always_sender(
            "8801799999999", {"message_text": "hi"},
        )
        assert result is False

    async def test_non_phone_target_id_falls_back_to_unknown_and_real_send(self, monkeypatch):
        """Facebook comment/PSID identifiers aren't phone numbers --
        detect_identity() must safely fall back rather than raise, and the
        result must not accidentally match DRAFT_ALWAYS_*."""
        import modules.social_auto_reply.planner_worker as planner

        result = await planner._is_draft_always_sender(
            "969218865858934_1528952915612330", {"message_text": "hi"},
        )
        assert result is False
