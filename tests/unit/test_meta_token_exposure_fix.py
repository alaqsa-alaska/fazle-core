"""Facebook Page Access Token exposure containment (Owner directive,
2026-08-27, urgent credential-exposure investigation).

Root cause: several Meta/Graph API call sites passed access_token as a
URL query parameter (httpx.AsyncClient's params={"access_token": ...}),
and httpx's own INFO-level request logging writes the full request URL
--query string included-- to logs/fazle-core.log. Confirmed live: 2500+
occurrences of the real token across the current log + 5 rotations.

Fix, verified here two ways:
  1. every affected call site now sends the token via an Authorization:
     Bearer header instead (httpx does not log header contents at INFO
     level) -- these tests assert the token is in `headers`, never in
     the request URL or `params`, for every affected function.
  2. a defense-in-depth logging filter on the "httpx" logger redacts any
     access_token=<value>/token=<value>/api_key=<value> query-string-
     shaped substring from any log record that logger emits, so a future
     regression (a call site that reintroduces the query-param pattern)
     still can't leak a real token into the log file.

No real Graph API call is made anywhere in this file -- httpx.AsyncClient
is patched at the request boundary throughout.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_response(status_code: int = 200, json_body: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {"message_id": "mid.1"})
    resp.text = text or str(json_body or "")
    return resp


class TestSendQueueTokenTransport:
    """modules/social_auto_reply/send_queue.py"""
    pytestmark = pytest.mark.asyncio

    async def test_send_messenger_uses_authorization_header_not_url_param(self):
        from modules.social_auto_reply.send_queue import _send_messenger
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response())) as mock_post:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-secret-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            await _send_messenger("PSID1", "hi")
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
        assert "params" not in kwargs
        assert "fake-secret-token-not-real" not in mock_post.call_args.args[0]

    async def test_send_instagram_uses_authorization_header_not_url_param(self):
        from modules.social_auto_reply.send_queue import _send_instagram
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response())) as mock_post:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-secret-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            await _send_instagram("IGSID1", "hi")
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
        assert "params" not in kwargs

    async def test_send_comment_uses_authorization_header_not_url_param(self):
        from modules.social_auto_reply.send_queue import _send_comment
        with patch("modules.social_auto_reply.send_queue.get_settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response(json_body={"id": "c1"}))) as mock_post:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-secret-token-not-real",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            await _send_comment("comment123", "hi")
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
        assert "params" not in kwargs


class TestAppMainTokenTransport:
    """app/main.py's local _send_messenger / _send_fb_comment_reply -- the
    functions actually wired into the LIVE legacy Messenger/FB-comment
    paths (distinct from send_queue.py's own same-named functions)."""
    pytestmark = pytest.mark.asyncio

    async def test_main_send_messenger_uses_header(self):
        from app.main import _send_messenger
        with patch("app.main.settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response())) as mock_post:
            mock_settings.fb_page_access_token = "fake-secret-token-not-real"
            mock_settings.meta_api_url = "https://graph.facebook.com/v23.0"
            await _send_messenger("PSID1", "hi")
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
        assert "params" not in kwargs

    async def test_main_send_fb_comment_reply_uses_header(self):
        from app.main import _send_fb_comment_reply
        with patch("app.main.settings") as mock_settings, \
             patch("httpx.AsyncClient.post", new=AsyncMock(return_value=_fake_response())) as mock_post:
            mock_settings.fb_page_access_token = "fake-secret-token-not-real"
            mock_settings.meta_api_url = "https://graph.facebook.com/v23.0"
            await _send_fb_comment_reply("comment123", "hi")
        kwargs = mock_post.call_args.kwargs
        assert kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
        assert "params" not in kwargs


class TestBacklogProcessorTokenTransport:
    """modules/social_auto_reply/backlog_processor.py's two GET calls."""
    pytestmark = pytest.mark.asyncio

    async def test_scan_recent_uses_header_not_url_param(self):
        from modules.social_auto_reply import backlog_processor
        with patch("modules.social_auto_reply.backlog_processor.get_settings") as mock_settings, \
             patch("modules.social_auto_reply.backlog_processor.execute", new=AsyncMock()), \
             patch("modules.social_auto_reply.backlog_processor.ingest_social_event", new=AsyncMock()), \
             patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_fake_response(json_body={"data": []}))) as mock_get:
            mock_settings.return_value = MagicMock(
                fb_page_access_token="fake-secret-token-not-real",
                fb_page_id="12345",
                meta_api_url="https://graph.facebook.com/v23.0",
            )
            await backlog_processor.scan_recent(limit=5)
        assert mock_get.await_count == 2
        for call in mock_get.call_args_list:
            assert call.kwargs["headers"] == {"Authorization": "Bearer fake-secret-token-not-real"}
            assert "access_token" not in call.kwargs.get("params", {})
            assert "fake-secret-token-not-real" not in call.args[0]


class TestHttpxLoggerRedactionFilter:
    """Defense-in-depth: even if a future call site reintroduces the
    query-param pattern, the "httpx" logger must never emit the real
    token value."""

    def _install_filter_on_fresh_logger(self):
        from app.logging_setup import _RedactTokenQueryParamsFilter
        logger = logging.getLogger("httpx.test_redaction_isolated")
        logger.addFilter(_RedactTokenQueryParamsFilter())
        return logger

    def test_access_token_query_param_redacted_from_message(self, caplog):
        logger = self._install_filter_on_fresh_logger()
        with caplog.at_level(logging.INFO, logger="httpx.test_redaction_isolated"):
            logger.info(
                "HTTP Request: GET https://graph.facebook.com/v23.0/123/conversations"
                "?access_token=EAAQVNS7eFzoBRhR7csyEWabc123&limit=10 \"HTTP/1.1 200 OK\""
            )
        assert "EAAQVNS7eFzoBRhR7csyEWabc123" not in caplog.text
        assert "access_token=***REDACTED***" in caplog.text

    def test_message_without_token_unaffected(self, caplog):
        logger = self._install_filter_on_fresh_logger()
        with caplog.at_level(logging.INFO, logger="httpx.test_redaction_isolated"):
            logger.info("HTTP Request: GET https://graph.facebook.com/v23.0/me \"HTTP/1.1 200 OK\"")
        assert "graph.facebook.com/v23.0/me" in caplog.text

    def test_filter_never_raises_on_non_string_args(self, caplog):
        logger = self._install_filter_on_fresh_logger()
        with caplog.at_level(logging.INFO, logger="httpx.test_redaction_isolated"):
            logger.info("value=%s", 12345)  # non-string arg must not crash the filter
        assert "12345" in caplog.text

    def test_filter_is_installed_on_the_real_httpx_logger_after_setup_logging(self, tmp_path):
        from app.logging_setup import _RedactTokenQueryParamsFilter
        import app.logging_setup as ls
        ls._initialized = False  # force re-run in this isolated check
        with patch.object(ls, "_DEFAULT_LOG_DIR", str(tmp_path)):
            ls.setup_logging()
        assert any(
            isinstance(f, _RedactTokenQueryParamsFilter)
            for f in logging.getLogger("httpx").filters
        )
