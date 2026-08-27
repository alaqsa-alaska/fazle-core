"""Meta Messenger/Instagram integration — unit tests (Owner directive,
2026-08-27).

Covers the function-level behavior of app.main's Meta DM handlers:
  - _meta_channel_duplicate_delivery() — durable dedup
  - _dispatch_meta_channel_message() — shared unified-path dispatch
  - _handle_messenger_message() — flag-gated migration onto the unified
    path, legacy behavior preserved when the flag is off
  - _handle_instagram_message() — built on the unified path from day one

HTTP-level webhook routing/signature/echo tests (the object="instagram"
vs "page" dispatch bug fix, signature validation, echo suppression) live
in tests/integration/test_webhooks.py — those need a real webhook POST
through the FastAPI app to exercise meta_webhook()'s own dispatch loop,
which this file does not re-test.

Every DB/network boundary is mocked — no real DB or Graph API call.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


class TestMetaChannelDuplicateDelivery:
    async def test_no_external_id_never_flagged_duplicate(self):
        from app.main import _meta_channel_duplicate_delivery
        result = await _meta_channel_duplicate_delivery("messenger", None)
        assert result is False

    async def test_existing_row_flagged_duplicate(self):
        from app.main import _meta_channel_duplicate_delivery
        with patch("app.main.fetch_one", new=AsyncMock(return_value={"1": 1})):
            result = await _meta_channel_duplicate_delivery("messenger", "mid.123")
        assert result is True

    async def test_no_existing_row_not_duplicate(self):
        from app.main import _meta_channel_duplicate_delivery
        with patch("app.main.fetch_one", new=AsyncMock(return_value=None)):
            result = await _meta_channel_duplicate_delivery("instagram", "mid.456")
        assert result is False

    async def test_db_error_fails_open_not_duplicate(self):
        """A dedup check that fails must never itself block a genuine
        new message — matches this codebase's existing best-effort
        dedup convention (Phase 7 doc: 'no schema migration', 'never
        block on this')."""
        from app.main import _meta_channel_duplicate_delivery
        with patch("app.main.fetch_one", new=AsyncMock(side_effect=RuntimeError("db down"))):
            result = await _meta_channel_duplicate_delivery("messenger", "mid.789")
        assert result is False

    async def test_query_scoped_to_platform_and_direction(self):
        from app.main import _meta_channel_duplicate_delivery
        mock_fetch = AsyncMock(return_value=None)
        with patch("app.main.fetch_one", new=mock_fetch):
            await _meta_channel_duplicate_delivery("instagram", "mid.abc")
        args, _ = mock_fetch.call_args
        query = args[0]
        assert "direction='inbound'" in query
        assert args[1] == "instagram"
        assert args[2] == "mid.abc"


class TestDispatchMetaChannelMessage:
    async def test_live_send_enqueues_and_saves_outbound(self):
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=("reply text", None))), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main.outbound_queue.enqueue", new=AsyncMock(return_value=999)) as mock_enqueue, \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main._save_draft", new=AsyncMock()) as mock_draft:
            await _dispatch_meta_channel_message(
                channel="messenger", sender_id="PSID123", text="hi",
                external_message_id="mid.1", live_send_flag="meta_messenger_live_send",
            )
        mock_enqueue.assert_awaited_once()
        kwargs = mock_enqueue.call_args.kwargs
        assert kwargs["source_bridge"] == "messenger"
        assert kwargs["purpose"] == "customer-reply:messenger"
        assert kwargs["idempotency_key"]  # non-empty, derived from sender+message id
        mock_save.assert_awaited_once_with("messenger", "PSID123", "reply text", direction="outbound")
        mock_draft.assert_not_awaited()

    async def test_live_send_off_drafts_instead_of_sending(self):
        """Owner Phase 18: receive/test webhook traffic safely before
        enabling Hermes live replies."""
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=("reply text", None))), \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             patch("app.main.outbound_queue.enqueue", new=AsyncMock()) as mock_enqueue, \
             patch("app.main._save_draft", new=AsyncMock()) as mock_draft:
            await _dispatch_meta_channel_message(
                channel="instagram", sender_id="IGSID456", text="hi",
                external_message_id="mid.2", live_send_flag="meta_instagram_live_send",
            )
        mock_enqueue.assert_not_awaited()
        mock_draft.assert_awaited_once()
        assert mock_draft.call_args.args[0] == "instagram"

    async def test_no_reply_generated_no_send_no_draft(self):
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=(None, None))), \
             patch("app.main.outbound_queue.enqueue", new=AsyncMock()) as mock_enqueue, \
             patch("app.main._save_draft", new=AsyncMock()) as mock_draft:
            await _dispatch_meta_channel_message(
                channel="messenger", sender_id="PSID1", text="hi",
                external_message_id=None, live_send_flag="meta_messenger_live_send",
            )
        mock_enqueue.assert_not_awaited()
        mock_draft.assert_not_awaited()

    async def test_dedup_enqueue_skip_does_not_save_outbound(self):
        """outbound.enqueue() returns None on idempotency-key dedup —
        must not double-record the outbound message either."""
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=("reply text", None))), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main.outbound_queue.enqueue", new=AsyncMock(return_value=None)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save:
            await _dispatch_meta_channel_message(
                channel="messenger", sender_id="PSID1", text="hi",
                external_message_id="mid.3", live_send_flag="meta_messenger_live_send",
            )
        mock_save.assert_not_awaited()

    async def test_send_to_admin_notification_forwarded(self):
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message",
                   new=AsyncMock(return_value=(None, {"purpose": "some_admin_note", "text": "note"}))), \
             patch("app.main._notify_admin", new=AsyncMock()) as mock_notify:
            await _dispatch_meta_channel_message(
                channel="messenger", sender_id="PSID1", text="hi",
                external_message_id=None, live_send_flag="meta_messenger_live_send",
            )
        mock_notify.assert_awaited_once()


class TestHandleMessengerMessageMigration:
    """Owner directive (2026-08-27): meta_messenger_inbound gates whether
    this message goes through the unified Hermes path or the legacy
    social_auto_reply engine. Default OFF -- zero behavior change."""

    def _messaging(self, sender="PSID1", text="hello", mid="mid.1"):
        return {"sender": {"id": sender}, "message": {"text": text, "mid": mid}}

    async def test_flag_off_uses_legacy_path_not_unified_dispatch(self):
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()) as mock_dispatch, \
             patch("app.main.ingest_social_event", new=AsyncMock(return_value=1)), \
             patch("app.main._social_auto_reply_single_engine", return_value=True):
            await _handle_messenger_message(self._messaging())
        mock_dispatch.assert_not_awaited()

    async def test_flag_on_uses_unified_dispatch_skips_legacy_ingest(self):
        """The double-reply-prevention guarantee: once migrated,
        ingest_social_event() (which the social_auto_reply daemon reads)
        must NOT be called for this message."""
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()) as mock_dispatch, \
             patch("app.main.ingest_social_event", new=AsyncMock()) as mock_ingest:
            await _handle_messenger_message(self._messaging())
        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["channel"] == "messenger"
        mock_ingest.assert_not_awaited()

    async def test_duplicate_delivery_short_circuits_everything(self):
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=True)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()) as mock_dispatch:
            await _handle_messenger_message(self._messaging())
        mock_save.assert_not_awaited()
        mock_dispatch.assert_not_awaited()

    async def test_empty_text_or_sender_ignored(self):
        from app.main import _handle_messenger_message
        with patch("app.main._save_message", new=AsyncMock()) as mock_save:
            await _handle_messenger_message({"sender": {"id": ""}, "message": {"text": "hi"}})
            await _handle_messenger_message({"sender": {"id": "PSID1"}, "message": {"text": ""}})
        mock_save.assert_not_awaited()


class TestHandleInstagramMessage:
    """Owner directive (2026-08-27): Instagram built directly on the
    unified path from day one -- no legacy fallback exists."""

    def _messaging(self, sender="IGSID1", text="hello", mid="ig-mid.1"):
        return {"sender": {"id": sender}, "message": {"text": text, "mid": mid}}

    async def test_flag_off_saves_but_does_not_dispatch(self):
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()) as mock_dispatch:
            await _handle_instagram_message(self._messaging())
        mock_save.assert_awaited_once()
        assert mock_save.call_args.args[0] == "instagram"
        mock_dispatch.assert_not_awaited()

    async def test_flag_on_dispatches_via_unified_path(self):
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()) as mock_dispatch:
            await _handle_instagram_message(self._messaging())
        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["channel"] == "instagram"
        assert kwargs["live_send_flag"] == "meta_instagram_live_send"

    async def test_duplicate_delivery_ignored(self):
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=True)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save:
            await _handle_instagram_message(self._messaging())
        mock_save.assert_not_awaited()

    async def test_sender_id_stored_as_channel_scoped_not_phone(self):
        """Owner Phase 7: an Instagram sender ID must never be assumed to
        be a phone number. This test proves the raw IGSID is stored
        verbatim (no phone-normalization applied) under platform=
        'instagram', which app.main._save_message keys on separately
        from wbom_whatsapp_messages' phone-based rows for other
        platforms."""
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("modules.preflight_guard.is_feature_active", return_value=False):
            await _handle_instagram_message(self._messaging(sender="17841400000000000"))
        assert mock_save.call_args.args == ("instagram", "17841400000000000", "hello")
        assert mock_save.call_args.kwargs["direction"] == "inbound"


class TestOutboundChannelWiring:
    """Owner Phase 10: Instagram must route through the existing
    fazle_outbound_queue / _send_with_channel, not a bespoke sender."""

    async def test_send_with_channel_routes_instagram(self):
        from modules.outbound import _send_with_channel
        with patch(
            "modules.social_auto_reply.send_queue._send_instagram",
            new=AsyncMock(return_value="ig-msg-1"),
        ) as mock_send:
            result = await _send_with_channel("instagram", "IGSID1", "hello")
        mock_send.assert_awaited_once_with("IGSID1", "hello")
        assert result == "ig-msg-1"

    async def test_send_with_channel_still_routes_messenger(self):
        """Regression: adding the instagram branch must not disturb the
        existing messenger routing."""
        from modules.outbound import _send_with_channel
        with patch(
            "modules.social_auto_reply.send_queue._send_messenger",
            new=AsyncMock(return_value="msg-1"),
        ) as mock_send:
            result = await _send_with_channel("messenger", "PSID1", "hello")
        mock_send.assert_awaited_once_with("PSID1", "hello")
        assert result == "msg-1"
