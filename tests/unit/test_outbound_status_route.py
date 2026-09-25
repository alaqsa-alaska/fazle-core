"""Unit test -- GET /api/outbound/status (2026-08-21, urgent bridge3
recruitment-reply recovery, Task 5: enqueue success != delivery success).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def auth_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


class TestOutboundStatus:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/outbound/status", params={"queue_id": 1})
        assert response.status_code == 403

    async def test_requires_queue_id_or_recipient(self, auth_headers):
        async with await _client() as ac:
            response = await ac.get("/api/outbound/status", headers=auth_headers)
        body = response.json()
        assert "error" in body
        assert body["items"] == []

    async def test_queued_but_not_yet_sent_is_not_delivered(self, auth_headers):
        row = {
            "id": 2371, "recipient": "01966011609", "source_bridge": "bridge2",
            "purpose": "hermes_admin_send", "status": "pending", "attempts": 0,
            "max_attempts": 3, "last_error": None,
            "created_at": datetime(2026, 8, 21, 11, 4, tzinfo=timezone.utc),
            "sent_at": None, "updated_at": datetime(2026, 8, 21, 11, 4, tzinfo=timezone.utc),
        }
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[row])):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/outbound/status", params={"queue_id": 2371}, headers=auth_headers
                )
        item = response.json()["items"][0]
        assert item["delivered"] is False
        assert item["in_flight"] is True
        assert item["definitively_failed"] is False

    async def test_failed_and_exhausted_is_definitively_failed_not_delivered(self, auth_headers):
        row = {
            "id": 2371, "recipient": "01966011609", "source_bridge": "bridge2",
            "purpose": "hermes_admin_send", "status": "failed", "attempts": 3,
            "max_attempts": 3, "last_error": "transport: ",
            "created_at": datetime(2026, 8, 21, 11, 4, tzinfo=timezone.utc),
            "sent_at": None, "updated_at": datetime(2026, 8, 21, 11, 6, tzinfo=timezone.utc),
        }
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[row])):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/outbound/status", params={"queue_id": 2371}, headers=auth_headers
                )
        item = response.json()["items"][0]
        assert item["delivered"] is False
        assert item["in_flight"] is False
        assert item["definitively_failed"] is True

    async def test_sent_status_is_accepted_but_not_confirmed_delivered(self, auth_headers):
        row = {
            "id": 2375, "recipient": "8801880446111", "source_bridge": "bridge2",
            "purpose": "hermes_admin_send", "status": "sent", "attempts": 1,
            "max_attempts": 3, "last_error": None,
            "created_at": datetime(2026, 8, 21, 11, 19, tzinfo=timezone.utc),
            "sent_at": datetime(2026, 8, 21, 11, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 8, 21, 11, 19, tzinfo=timezone.utc),
        }
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[row])):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/outbound/status", params={"queue_id": 2375}, headers=auth_headers
                )
        item = response.json()["items"][0]
        assert item["delivered"] is False
        assert item["send_accepted"] is True
        assert item["delivery_outcome"] == "unconfirmed"
        assert item["in_flight"] is False
        assert item["definitively_failed"] is False

    async def test_manually_stopped_dlq_row_is_definitively_failed_even_below_max_attempts(self, auth_headers):
        # Live-found edge case (2026-08-21, queue id 2371): a row moved
        # straight to 'dlq' administratively, not by exhausting retries --
        # attempts (1) < max_attempts (3), but it will never retry.
        row = {
            "id": 2371, "recipient": "01966011609", "source_bridge": "bridge2",
            "purpose": "hermes_admin_send", "status": "dlq", "attempts": 1,
            "max_attempts": 3, "last_error": "MANUALLY STOPPED",
            "created_at": datetime(2026, 8, 21, 11, 4, tzinfo=timezone.utc),
            "sent_at": None, "updated_at": datetime(2026, 8, 21, 11, 6, tzinfo=timezone.utc),
        }
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[row])):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/outbound/status", params={"queue_id": 2371}, headers=auth_headers
                )
        item = response.json()["items"][0]
        assert item["delivered"] is False
        assert item["definitively_failed"] is True

    async def test_lookup_by_recipient_when_queue_id_unknown(self, auth_headers):
        mock_fetch_all = AsyncMock(return_value=[])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/outbound/status", params={"recipient": "8801966011609"}, headers=auth_headers
                )
        query_arg = mock_fetch_all.call_args[0][0]
        assert "recipient" in query_arg
