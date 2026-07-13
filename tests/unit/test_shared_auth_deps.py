"""
Unit tests — shared.auth_deps.require_api_key, and that the 5 route
modules that switched to importing it from there (2026-07-13, replacing
5 independent copy-pasted implementations) still gate correctly end-to-end.
"""
from __future__ import annotations

import pytest

from tests.conftest import TEST_API_KEY

pytestmark = pytest.mark.unit

# One real GET endpoint per module that switched to the shared dependency.
_ENDPOINTS = [
    "/admin/contact-roles/",
    "/api/drafts/stats",
    "/admin/kb/stats",
    "/api/assistant/ops/flags",
    "/api/social/status",
]


class TestSharedAuthDepsWiring:
    @pytest.mark.parametrize("path", _ENDPOINTS)
    async def test_unauthenticated_request_rejected(self, client, path):
        r = await client.get(path, headers={"X-Internal-Key": ""})
        assert r.status_code == 403, f"{path} should reject an empty key, got {r.status_code}"

    @pytest.mark.parametrize("path", _ENDPOINTS)
    async def test_invalid_key_rejected(self, client, path):
        r = await client.get(path, headers={"X-Internal-Key": "definitely-not-a-real-key"})
        assert r.status_code == 403, f"{path} should reject an invalid key, got {r.status_code}"

    @pytest.mark.parametrize("path", _ENDPOINTS)
    async def test_internal_key_accepted(self, client, path, monkeypatch):
        # app.config.get_settings() is @lru_cache'd — other tests elsewhere
        # in the suite call cache_clear() mid-test to pick up their own
        # monkeypatched env, then don't restore it, leaving a stale
        # settings.internal_api_key cached for whichever test runs next.
        # Force it back to what the `client` fixture actually authenticates
        # with (TEST_API_KEY) so this test doesn't depend on suite run order.
        from app.config import get_settings
        monkeypatch.setenv("INTERNAL_API_KEY", TEST_API_KEY)
        get_settings.cache_clear()

        # client fixture is pre-authenticated with the real internal key —
        # any status other than 403 means the auth dependency let it through
        # (the endpoint's own logic may still 404/500 for unrelated reasons,
        # this only asserts the auth gate itself passed).
        r = await client.get(path)
        assert r.status_code != 403, f"{path} rejected the valid internal key"
