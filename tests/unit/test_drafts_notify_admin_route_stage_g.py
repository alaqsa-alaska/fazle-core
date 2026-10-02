"""Stage G5/G6/G7/G27 (2026-08-28) -- POST /api/drafts/{id}/notify-admin."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def auth_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


class TestNotifyAdminRoute:
    async def test_requires_auth(self):
        async with await _client() as ac:
            resp = await ac.post("/api/drafts/5/notify-admin")
        assert resp.status_code == 403

    async def test_success_returns_ok(self, auth_headers):
        with patch("modules.supervised_draft.notify_admin_of_pending_draft", new=AsyncMock(return_value={
            "ok": True, "draft_id": 5, "admin": "8801880446111", "enqueue_result": {},
        })):
            async with await _client() as ac:
                resp = await ac.post("/api/drafts/5/notify-admin", params={"category": "Client"}, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_failure_returns_400_not_500(self, auth_headers):
        with patch("modules.supervised_draft.notify_admin_of_pending_draft", new=AsyncMock(return_value={
            "ok": False, "error": "draft #5 not found",
        })):
            async with await _client() as ac:
                resp = await ac.post("/api/drafts/5/notify-admin", headers=auth_headers)
        assert resp.status_code == 400
