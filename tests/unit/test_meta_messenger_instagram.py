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

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
        mock_save.assert_awaited_once_with(
            "messenger", "PSID123", "reply text", direction="outbound", actor_type="automation",
        )
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
        must NOT be called for this message.

        Owner directive (2026-08-27, Phase 10 fast-ack fix): dispatch is
        now scheduled via asyncio.create_task(..._background(...)) instead
        of awaited inline, so the webhook's HTTP response is never delayed
        by Hermes latency -- patch the background wrapper (what's actually
        passed to create_task) and yield once (asyncio.sleep(0)) so the
        scheduled task gets a turn before asserting."""
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message_background", new=AsyncMock()) as mock_dispatch, \
             patch("app.main.ingest_social_event", new=AsyncMock()) as mock_ingest:
            await _handle_messenger_message(self._messaging())
            await asyncio.sleep(0)
        mock_dispatch.assert_awaited_once()
        kwargs = mock_dispatch.call_args.kwargs
        assert kwargs["channel"] == "messenger"
        mock_ingest.assert_not_awaited()

    async def test_duplicate_delivery_short_circuits_everything(self):
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=True)), \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main._dispatch_meta_channel_message_background", new=AsyncMock()) as mock_dispatch:
            await _handle_messenger_message(self._messaging())
            await asyncio.sleep(0)
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
             patch("app.main._dispatch_meta_channel_message_background", new=AsyncMock()) as mock_dispatch:
            await _handle_instagram_message(self._messaging())
            await asyncio.sleep(0)
        mock_save.assert_awaited_once()
        assert mock_save.call_args.args[0] == "instagram"
        mock_dispatch.assert_not_awaited()

    async def test_flag_on_dispatches_via_unified_path(self):
        """Owner Phase 10 fast-ack fix: dispatch is scheduled via
        asyncio.create_task(..._background(...)), not awaited inline —
        patch the background wrapper and yield once before asserting."""
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message_background", new=AsyncMock()) as mock_dispatch:
            await _handle_instagram_message(self._messaging())
            await asyncio.sleep(0)
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


class TestMetaObservabilityLogging:
    """Owner directive (2026-08-27, Section 1): 14 named structured events
    must be observable, with non-secret identifiers only, and without
    polluting logs/fazle-core.log during test runs.

    caplog is pytest's built-in in-memory log-capture fixture — it adds a
    handler for the duration of one test and never touches a FileHandler
    or any real file, so these tests inherently satisfy "tests must not
    write fixture noise into the production log file" (the same lesson
    already applied to hermes_kernel.shadow_logger via conftest.py's
    _disable_shadow_logger_real_writes in this same session)."""

    def _messenger_msg(self, sender="PSID1", text="hello", mid="mid.obs1"):
        return {"sender": {"id": sender}, "message": {"text": text, "mid": mid}}

    def _instagram_msg(self, sender="IGSID1", text="hello", mid="ig-mid.obs1"):
        return {"sender": {"id": sender}, "message": {"text": text, "mid": mid}}

    async def test_duplicate_delivery_logs_meta_webhook_duplicate(self, caplog):
        from app.main import _meta_channel_duplicate_delivery
        with patch("app.main.fetch_one", new=AsyncMock(return_value={"1": 1})), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _meta_channel_duplicate_delivery("messenger", "mid.dup1")
        assert "event=meta_webhook_duplicate" in caplog.text
        assert "channel=messenger" in caplog.text
        assert "mid.dup1" in caplog.text

    async def test_messenger_normalized_logged_on_valid_message(self, caplog):
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock()), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _handle_messenger_message(self._messenger_msg())
        assert "event=meta_messenger_normalized" in caplog.text

    async def test_instagram_normalized_logged_on_valid_message(self, caplog):
        from app.main import _handle_instagram_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _handle_instagram_message(self._instagram_msg())
        assert "event=meta_instagram_normalized" in caplog.text
        # Flag off -> sync-only fallback, must also be observable
        assert "event=meta_fallback_used" in caplog.text
        assert "reason=inbound_flag_off_sync_only" in caplog.text

    async def test_legacy_engine_skip_logs_meta_fallback_used(self, caplog):
        from app.main import _handle_messenger_message
        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             patch("app.main.ingest_social_event", new=AsyncMock(return_value=1)), \
             patch("app.main._social_auto_reply_single_engine", return_value=True), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _handle_messenger_message(self._messenger_msg())
        assert "event=meta_fallback_used" in caplog.text
        assert "reason=legacy_social_auto_reply_single_engine" in caplog.text

    async def test_dispatch_logs_inbound_dispatched_reply_generated_and_queued(self, caplog):
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=("reply text", None))), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main.outbound_queue.enqueue", new=AsyncMock(return_value=42)), \
             patch("app.main._save_message", new=AsyncMock()), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _dispatch_meta_channel_message(
                channel="messenger", sender_id="PSID1", text="hi",
                external_message_id="mid.obs2", live_send_flag="meta_messenger_live_send",
            )
        assert "event=meta_inbound_dispatched" in caplog.text
        assert "event=meta_hermes_reply_generated" in caplog.text
        assert "event=meta_outbound_queued" in caplog.text
        assert "queue_id=42" in caplog.text

    async def test_dispatch_live_send_off_logs_fallback_used(self, caplog):
        from app.main import _dispatch_meta_channel_message
        with patch("app.main._process_message", new=AsyncMock(return_value=("reply text", None))), \
             patch("modules.preflight_guard.is_feature_active", return_value=False), \
             patch("app.main._save_draft", new=AsyncMock()), \
             caplog.at_level("INFO", logger="fazle.app"):
            await _dispatch_meta_channel_message(
                channel="instagram", sender_id="IGSID1", text="hi",
                external_message_id="mid.obs3", live_send_flag="meta_instagram_live_send",
            )
        assert "event=meta_fallback_used" in caplog.text
        assert "reason=live_send_flag_off_drafted" in caplog.text

    async def test_meta_log_event_never_includes_settings_object(self):
        """Structural guarantee, not just convention: _meta_log_event's
        compiled body references no global/attribute named `settings` (or
        any secret-holding attribute) — checked via co_names, which lists
        names the *code* actually looks up, not prose inside its own
        docstring (which legitimately discusses those names)."""
        from app.main import _meta_log_event
        referenced = _meta_log_event.__code__.co_names
        for forbidden in ("settings", "meta_app_secret", "meta_verify_token", "fb_page_access_token", "meta_api_token"):
            assert forbidden not in referenced


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


@pytest.mark.integration
class TestMetaOutboundQueueObservability:
    """Owner directive (2026-08-27, Section 1): meta_outbound_sent/
    meta_outbound_failed/meta_outbound_dlq must be observable — and must
    NOT fire for bridge1/2/3 traffic, since modules.outbound.sweep_once()
    is the one generic worker every channel shares (Owner: 'no schema/
    behavior change to shared abstractions unless demonstrably backward-
    compatible' — these events are gated at the log call site only, the
    retry/backoff/DLQ logic itself is untouched).

    Uses the real fazle_outbound_queue table (test_db_pool) with
    _send_with_channel mocked at the network boundary — caplog captures
    in-memory only, so no test writes into logs/fazle-core.log."""

    @pytest.fixture(autouse=True)
    def _wire_test_pool(self, test_db_pool):
        """modules.outbound.sweep_once() calls app.database.get_pool()
        directly — point the shared singleton at this test's pool, same
        pattern conftest.py's own `client` fixture uses."""
        import app.database as db_module
        db_module._pool = test_db_pool
        yield

    async def test_meta_channel_send_success_logs_meta_outbound_sent(self, test_db_pool, monkeypatch, caplog):
        monkeypatch.setenv("OUTBOUND_ENABLED", "true")
        from modules.outbound import enqueue, sweep_once
        await enqueue("PSID1", "hi", source_bridge="messenger", idempotency_key="obs-sent-1")
        with patch("modules.outbound._send_with_channel", new=AsyncMock(return_value="ext-1")), \
             caplog.at_level("INFO", logger="fazle.outbound"):
            result = await sweep_once(limit=5)
        assert result["sent"] == 1
        assert "event=meta_outbound_sent" in caplog.text
        assert "channel=messenger" in caplog.text

    async def test_bridge_channel_send_success_does_not_log_meta_event(self, test_db_pool, monkeypatch, caplog):
        """Regression: the channel gate must not fire for non-Meta bridges."""
        monkeypatch.setenv("OUTBOUND_ENABLED", "true")
        from modules.outbound import enqueue, sweep_once
        await enqueue("01811111111", "hi", source_bridge="bridge1", idempotency_key="obs-sent-2")
        with patch("modules.outbound._send_with_channel", new=AsyncMock(return_value="ext-2")), \
             caplog.at_level("INFO", logger="fazle.outbound"):
            await sweep_once(limit=5)
        assert "event=meta_outbound_sent" not in caplog.text

    async def test_meta_channel_permanent_failure_logs_meta_outbound_dlq(self, test_db_pool, monkeypatch, caplog):
        monkeypatch.setenv("OUTBOUND_ENABLED", "true")
        from modules.outbound import enqueue, sweep_once
        qid = await enqueue("IGSID1", "hi", source_bridge="instagram", idempotency_key="obs-dlq-1")
        async with test_db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE fazle_outbound_queue SET attempts=$2, max_attempts=$2 WHERE id=$1", qid, 1
            )
        with patch("modules.outbound._send_with_channel", new=AsyncMock(side_effect=RuntimeError("graph api down"))), \
             caplog.at_level("INFO", logger="fazle.outbound"):
            result = await sweep_once(limit=5)
        assert result["dlq"] == 1
        assert "event=meta_outbound_dlq" in caplog.text
        assert "channel=instagram" in caplog.text

    async def test_meta_channel_retryable_failure_logs_meta_outbound_failed(self, test_db_pool, monkeypatch, caplog):
        monkeypatch.setenv("OUTBOUND_ENABLED", "true")
        from modules.outbound import enqueue, sweep_once
        await enqueue("PSID2", "hi", source_bridge="messenger", idempotency_key="obs-failed-1")
        with patch("modules.outbound._send_with_channel", new=AsyncMock(side_effect=RuntimeError("timeout"))), \
             caplog.at_level("INFO", logger="fazle.outbound"):
            result = await sweep_once(limit=5)
        assert result["failed"] == 1
        assert "event=meta_outbound_failed" in caplog.text
        assert "channel=messenger" in caplog.text


def _fake_response(status_code: int, json_body: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    resp.text = text or str(json_body or "")
    return resp


class TestSendInstagramRequestConstruction:
    """Owner directive (2026-08-27, Section 4): validate _send_instagram()'s
    own request construction, token/config lookup, Graph endpoint
    construction, and error parsing purely via mocks — httpx.AsyncClient.post
    is patched at the transport boundary, no real Graph API call is made.

    PENDING OWNER ACCOUNT CONNECTION: this proves the function BUILDS the
    correct request and PARSES responses correctly; it does not and cannot
    prove a real Instagram DM would be delivered — that requires the
    Instagram Professional Account to actually be connected to the Page
    (Owner Dashboard action, see the handoff list), which has not happened
    yet. Do not read a pass here as production/E2E verification."""

    async def test_missing_token_raises_permission_error_before_any_http_call(self):
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock()) as mock_post:
            mock_settings.return_value = MagicMock(fb_page_access_token="", meta_api_url="https://graph.facebook.com/v23.0")
            with pytest.raises(PermissionError):
                await _send_instagram("IGSID1", "hello")
        mock_post.assert_not_awaited()

    async def test_success_posts_to_me_messages_with_page_token_and_returns_message_id(self):
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(
                 return_value=_fake_response(200, {"message_id": "mid.ig.999"})
             )) as mock_post:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-page-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            result = await _send_instagram("IGSID1", "hello there")
        assert result == "mid.ig.999"
        mock_post.assert_awaited_once()
        _, kwargs = mock_post.call_args
        assert mock_post.call_args.args[0] == "https://graph.facebook.com/v23.0/me/messages"
        # Owner directive (2026-08-27, token exposure containment): token
        # now travels via Authorization header, never a URL query param --
        # httpx logs full request URLs at INFO level, params would leak.
        assert kwargs["headers"] == {"Authorization": "Bearer fake-page-token-not-real"}
        assert "params" not in kwargs
        assert kwargs["json"] == {
            "recipient": {"id": "IGSID1"},
            "message": {"text": "hello there"},
            "messaging_type": "RESPONSE",
        }

    async def test_permission_denied_response_raises_permission_error_not_runtime_error(self):
        """A 403 carrying instagram_manage_messages/pages_messaging in the
        body means the Page/Instagram permission genuinely isn't granted
        yet (expected until the Owner connects the account + grants the
        permission) — must be distinguishable from a generic transient
        failure so callers (sweep_once) route it to 'blocked', not
        endless retry/DLQ."""
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(
                 return_value=_fake_response(403, text='{"error": {"message": "instagram_manage_messages permission required"}}')
             )):
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-page-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            with pytest.raises(PermissionError):
                await _send_instagram("IGSID1", "hello")

    async def test_other_failure_raises_runtime_error_not_permission_error(self):
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(
                 return_value=_fake_response(500, text="internal server error")
             )):
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-page-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            with pytest.raises(RuntimeError):
                await _send_instagram("IGSID1", "hello")

    async def test_missing_message_id_in_success_response_returns_empty_string_not_crash(self):
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response(200, {}))):
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-page-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            result = await _send_instagram("IGSID1", "hello")
        assert result == ""


@pytest.mark.integration
class TestMetaOutboundIdempotency:
    """Owner directive (2026-08-27, Section 4): idempotency for Meta-channel
    outbound rows uses the SAME fazle_outbound_queue.idempotency_key UNIQUE
    constraint every other channel already relies on — no new mechanism."""

    @pytest.fixture(autouse=True)
    def _wire_test_pool(self, test_db_pool):
        import app.database as db_module
        db_module._pool = test_db_pool
        yield

    async def test_duplicate_idempotency_key_for_instagram_is_not_double_queued(self, test_db_pool):
        from modules.outbound import enqueue
        key = "ig-idem-test-1"
        id1 = await enqueue("IGSID1", "reply one", source_bridge="instagram", idempotency_key=key)
        id2 = await enqueue("IGSID1", "reply two (should be deduplicated)", source_bridge="instagram", idempotency_key=key)
        assert id1 is not None
        assert id2 is None
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_outbound_queue WHERE idempotency_key=$1", key
            )
        assert count == 1


class TestMetaWebhookFastAcknowledgement:
    """Owner directive (2026-08-27, Meta reconfiguration Phase 10): 'Do not
    make Hermes latency delay Meta webhook acknowledgement.' Proves the
    ordering property directly, not just that dispatch was scheduled: the
    caller (_handle_messenger_message/_handle_instagram_message) must
    return control to the webhook handler BEFORE a slow Hermes-routed
    dispatch finishes, by driving a real (unmocked) asyncio.create_task
    against a dispatch that blocks on a controllable event."""

    async def test_messenger_handler_returns_before_slow_dispatch_completes(self):
        from app.main import _handle_messenger_message

        dispatch_started = asyncio.Event()
        dispatch_may_finish = asyncio.Event()
        dispatch_finished = asyncio.Event()

        async def slow_dispatch(**kwargs):
            dispatch_started.set()
            await dispatch_may_finish.wait()
            dispatch_finished.set()

        with patch("app.main._meta_channel_duplicate_delivery", new=AsyncMock(return_value=False)), \
             patch("app.main._save_message", new=AsyncMock()), \
             patch("modules.preflight_guard.is_feature_active", return_value=True), \
             patch("app.main._dispatch_meta_channel_message", new=AsyncMock(side_effect=slow_dispatch)), \
             patch("app.main.ingest_social_event", new=AsyncMock()):
            messaging = {"sender": {"id": "PSID_SLOW"}, "message": {"text": "hi", "mid": "mid.slow.1"}}
            await _handle_messenger_message(messaging)  # must return immediately

            # The handler returning is the webhook ack point. At this exact
            # moment, the background dispatch must NOT have finished yet --
            # proving the handler didn't wait for it.
            assert not dispatch_finished.is_set()

            # Give the scheduled background task a chance to actually start
            # running (it was only just created, not yet scheduled by the
            # loop) and confirm it does eventually start.
            await asyncio.wait_for(dispatch_started.wait(), timeout=2)
            assert not dispatch_finished.is_set()

            # Now let it complete and confirm it actually does run to
            # completion in the background (not silently dropped/orphaned).
            dispatch_may_finish.set()
            await asyncio.wait_for(dispatch_finished.wait(), timeout=2)

    async def test_background_dispatch_exception_is_caught_and_logged_not_raised(self, caplog):
        """A failure in the backgrounded dispatch must never escape as an
        unhandled task exception -- it must be caught and logged."""
        from app.main import _dispatch_meta_channel_message_background
        with patch("app.main._dispatch_meta_channel_message", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             caplog.at_level("ERROR", logger="fazle.app"):
            await _dispatch_meta_channel_message_background(
                channel="messenger", sender_id="PSID1", text="hi",
                external_message_id="mid.1", live_send_flag="meta_messenger_live_send",
            )  # must not raise
        assert "background dispatch failed" in caplog.text
        assert "boom" in caplog.text


class TestFbCommentSelfReplyLoopPrevention:
    """Owner directive (2026-08-27, Meta reconfiguration Phase 5): 'Prevent
    loops where the Page receives its own reply as a new customer event.'
    _handle_fb_comment() previously had NO identity check at all -- unlike
    modules.social_auto_reply.backlog_processor.scan_recent()'s existing
    ID-based check for /conversations, which this mirrors for the live
    webhook path."""

    async def test_page_own_comment_is_ignored(self):
        from app.main import _handle_fb_comment
        with patch("app.main.settings") as mock_settings, \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main.ingest_social_event", new=AsyncMock()) as mock_ingest:
            mock_settings.fb_page_id = "114154837962249"
            value = {
                "comment_id": "c_1", "sender_id": "114154837962249",
                "sender_name": "Al-Manaratayn Skill Solutions", "message": "Thanks for reaching out!",
                "post_id": "p_1",
            }
            await _handle_fb_comment(value)
        mock_save.assert_not_awaited()
        mock_ingest.assert_not_awaited()

    async def test_customer_comment_still_processed(self):
        from app.main import _handle_fb_comment
        with patch("app.main.settings") as mock_settings, \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main.ingest_social_event", new=AsyncMock()) as mock_ingest, \
             patch("app.main._social_auto_reply_single_engine", return_value=True):
            mock_settings.fb_page_id = "114154837962249"
            value = {
                "comment_id": "c_2", "sender_id": "999888777",
                "sender_name": "Real Customer", "message": "How do I apply?",
                "post_id": "p_1",
            }
            await _handle_fb_comment(value)
        mock_save.assert_awaited_once()
        mock_ingest.assert_awaited_once()

    async def test_fb_page_id_unset_never_blocks_processing(self):
        """If FB_PAGE_ID isn't configured, the guard must fail open to
        'process the comment' rather than silently drop all comments."""
        from app.main import _handle_fb_comment
        with patch("app.main.settings") as mock_settings, \
             patch("app.main._save_message", new=AsyncMock()) as mock_save, \
             patch("app.main.ingest_social_event", new=AsyncMock()), \
             patch("app.main._social_auto_reply_single_engine", return_value=True):
            mock_settings.fb_page_id = ""
            value = {"comment_id": "c_3", "sender_id": "999888777", "sender_name": "X", "message": "hi", "post_id": "p_1"}
            await _handle_fb_comment(value)
        mock_save.assert_awaited_once()
