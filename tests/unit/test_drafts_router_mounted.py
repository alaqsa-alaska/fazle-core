"""
Regression test for the 2026-07-08 audit finding: `modules.drafts.routes.router`
and `modules.social_auto_reply.routes.router` were imported in app/main.py but
never passed to app.include_router(), so their endpoints were unreachable
(despite being served by the currently-running, older in-memory process).
"""
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def test_drafts_routes_are_mounted_and_auth_gated():
    from httpx import ASGITransport, AsyncClient
    from app.config import get_settings
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        # No API key: must be a real auth rejection (403), never a 404 —
        # 404 would mean the router isn't mounted at all.
        no_auth = await ac.get("/api/drafts/stats")
        assert no_auth.status_code != 404, (
            "/api/drafts/stats returned 404 — drafts_router is not mounted"
        )
        assert no_auth.status_code == 403

        no_auth_list = await ac.get("/api/drafts")
        assert no_auth_list.status_code != 404
        assert no_auth_list.status_code == 403

        # approve/edit/delete must also be routable (not 404) even though a
        # fake draft id will fail downstream with 404 from the *handler*
        # logic — we only care that routing itself works, so assert the
        # auth gate (403) fires before any handler-level 404.
        # Note: /block was permanently removed (Owner decision, 2026-07-11 —
        # the "blocked number" concept no longer exists; see migration 042).
        for method, path in [
            ("post", "/api/drafts/999999999/approve"),
            ("post", "/api/drafts/999999999/edit"),
            ("post", "/api/drafts/999999999/delete"),
        ]:
            resp = await ac.request(method, path, json={})
            assert resp.status_code == 403, (
                f"{path} did not return the expected auth-gate 403 "
                f"(got {resp.status_code}) — check routing/auth wiring"
            )

        # With the real internal key, the route must be reachable (not 404).
        # DB calls are mocked so the test never needs a real connection.
        key = get_settings().internal_api_key
        with patch("modules.drafts.routes.fetch_all", new=AsyncMock(return_value=[])), \
             patch("modules.drafts.routes.fetch_one", new=AsyncMock(return_value=None)):
            with_auth = await ac.get(
                "/api/drafts/stats", headers={"X-Internal-Key": key}
            )
        assert with_auth.status_code != 404
        assert with_auth.status_code == 200


async def test_social_auto_reply_routes_are_mounted_and_auth_gated():
    from httpx import ASGITransport, AsyncClient
    from app.config import get_settings
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        no_auth = await ac.get("/api/social/status")
        assert no_auth.status_code != 404, (
            "/api/social/status returned 404 — social_auto_reply_router is not mounted"
        )
        assert no_auth.status_code == 403

        key = get_settings().internal_api_key
        with patch("modules.social_auto_reply.state_tracker.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.social_auto_reply.state_tracker.fetch_all", new=AsyncMock(return_value=[])), \
             patch("modules.social_auto_reply.state_tracker.fetch_val", new=AsyncMock(return_value=0)):
            with_auth = await ac.get(
                "/api/social/status", headers={"X-Internal-Key": key}
            )
        assert with_auth.status_code != 404
