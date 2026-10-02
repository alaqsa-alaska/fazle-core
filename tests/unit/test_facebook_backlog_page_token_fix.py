"""Facebook backlog-scanner #190 fix (Owner directive, 2026-08-27).

Root cause, confirmed live and read-only (not guessed from the error
string): FB_PAGE_ACCESS_TOKEN is a System User token -- it works for
every other Meta call this app makes, but Meta's legacy Page Inbox/
Conversations API (GET /{page-id}/conversations) and the Posts read edge
(GET /{page-id}/posts) specifically reject a System User token with
(#190) "This method must be called with a Page Access Token". Fix:
modules.social_auto_reply.backlog_processor._get_page_scoped_token()
derives a genuine Page-scoped token via GET /{page-id}?fields=access_token
using the configured System User token, and scan_recent() now uses that
derived token for both /conversations and /posts.

No real Graph API call is made anywhere in this file -- httpx.AsyncClient
is patched at the request boundary throughout. Never logs/asserts on a
real token value.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _fake_response(status_code: int = 200, json_body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    return resp


class TestGetPageScopedToken:
    async def test_success_returns_derived_token_via_correct_request(self):
        from modules.social_auto_reply.backlog_processor import _get_page_scoped_token
        settings = MagicMock(
            fb_page_access_token="fake-system-user-token-not-real",
            fb_page_id="114154837962249",
            meta_api_url="https://graph.facebook.com/v23.0",
        )
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_fake_response(200, {"access_token": "fake-derived-page-token-not-real", "id": "114154837962249"}))
        result = await _get_page_scoped_token(mock_client, settings)
        assert result == "fake-derived-page-token-not-real"
        args, kwargs = mock_client.get.call_args
        assert args[0] == "https://graph.facebook.com/v23.0/114154837962249"
        assert kwargs["headers"] == {"Authorization": "Bearer fake-system-user-token-not-real"}
        assert kwargs["params"] == {"fields": "access_token"}
        assert "access_token" not in args[0]  # never in the URL

    async def test_non_200_returns_none_not_raises(self):
        from modules.social_auto_reply.backlog_processor import _get_page_scoped_token
        settings = MagicMock(fb_page_access_token="x", fb_page_id="p", meta_api_url="https://graph.facebook.com/v23.0")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_fake_response(400, {"error": {"message": "bad"}}))
        result = await _get_page_scoped_token(mock_client, settings)
        assert result is None

    async def test_missing_access_token_field_returns_none(self):
        from modules.social_auto_reply.backlog_processor import _get_page_scoped_token
        settings = MagicMock(fb_page_access_token="x", fb_page_id="p", meta_api_url="https://graph.facebook.com/v23.0")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_fake_response(200, {"id": "p"}))
        result = await _get_page_scoped_token(mock_client, settings)
        assert result is None

    async def test_exception_returns_none_not_raises(self):
        from modules.social_auto_reply.backlog_processor import _get_page_scoped_token
        settings = MagicMock(fb_page_access_token="x", fb_page_id="p", meta_api_url="https://graph.facebook.com/v23.0")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("network down"))
        result = await _get_page_scoped_token(mock_client, settings)
        assert result is None

    async def test_derived_token_never_logged(self, caplog):
        from modules.social_auto_reply.backlog_processor import _get_page_scoped_token
        settings = MagicMock(fb_page_access_token="x", fb_page_id="p", meta_api_url="https://graph.facebook.com/v23.0")
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=_fake_response(200, {"access_token": "SUPER-SECRET-DERIVED-TOKEN-VALUE"}))
        with caplog.at_level(logging.DEBUG, logger="fazle.social.backlog"):
            await _get_page_scoped_token(mock_client, settings)
        assert "SUPER-SECRET-DERIVED-TOKEN-VALUE" not in caplog.text


class TestScanRecentUsesPageScopedToken:
    """Owner Step 6, items 1-2: both backlog read requests must use the
    derived page-scoped token, not the raw configured System User token."""

    async def test_conversations_and_posts_use_derived_token_not_configured_one(self):
        from modules.social_auto_reply import backlog_processor
        with patch.object(backlog_processor, "get_settings") as mock_settings, \
             patch.object(backlog_processor, "execute", new=AsyncMock()), \
             patch.object(backlog_processor, "ingest_social_event", new=AsyncMock()), \
             patch.object(backlog_processor, "_get_page_scoped_token", new=AsyncMock(return_value="derived-page-token-not-real")), \
             patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_fake_response(200, {"data": []}))) as mock_get:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="system-user-token-not-real",
                fb_page_id="114154837962249",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            result = await backlog_processor.scan_recent(limit=5)
        assert result["status"] == "ok"
        assert mock_get.await_count == 2
        for call in mock_get.call_args_list:
            assert call.kwargs["headers"] == {"Authorization": "Bearer derived-page-token-not-real"}
            # explicitly NOT the raw configured System User token
            assert call.kwargs["headers"]["Authorization"] != "Bearer system-user-token-not-real"

    async def test_derivation_failure_skips_scan_cleanly_no_crash(self):
        """If the page-token derivation itself fails (e.g. transient Graph
        API error), scan_recent() must skip cleanly, not crash the daemon
        loop or fall back to the known-broken System User token."""
        from modules.social_auto_reply import backlog_processor
        with patch.object(backlog_processor, "get_settings") as mock_settings, \
             patch.object(backlog_processor, "_get_page_scoped_token", new=AsyncMock(return_value=None)), \
             patch("httpx.AsyncClient.get", new=AsyncMock()) as mock_get:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="system-user-token-not-real",
                fb_page_id="114154837962249",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            result = await backlog_processor.scan_recent(limit=5)
        assert result["status"] == "skipped"
        assert result["reason"] == "page-token derivation failed"
        mock_get.assert_not_awaited()  # never attempts /conversations or /posts without a valid token

    async def test_missing_credentials_still_skips_before_any_http_call(self):
        """Regression: the existing early-exit for missing credentials
        must be unaffected by this change."""
        from modules.social_auto_reply import backlog_processor
        with patch.object(backlog_processor, "get_settings") as mock_settings, \
             patch("httpx.AsyncClient.get", new=AsyncMock()) as mock_get:
            mock_settings.return_value = MagicMock(fb_page_access_token="", fb_page_id="p", meta_api_url="https://graph.facebook.com/v23.0")
            result = await backlog_processor.scan_recent(limit=5)
        assert result["status"] == "skipped"
        assert result["reason"] == "facebook credentials missing"
        mock_get.assert_not_awaited()
