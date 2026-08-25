"""Unit tests -- GET/POST /admin/directives, POST /admin/directives/{id}/revoke.

Unified Admin AI Engine, Phase 3/5 gap-closure: these routes were scoped in
the original architecture plan but not built until this pass. Follows the
same httpx.AsyncClient + ASGITransport pattern already established in
tests/unit/test_chat_upload_endpoint.py -- exercises the real FastAPI
dependency chain (require_api_key / require_command), not a direct function
call, so the auth wiring itself is actually proven, not just business logic.
Auth uses the legacy internal_api_key (bypasses the RBAC-DB lookup inside
require_command(), since `key == settings.internal_api_key` short-circuits
before any DB call) -- modules.admin_directives' functions are monkeypatched
so no real Postgres is needed either.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

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


class TestGetAdminDirectives:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/admin/directives")
        assert response.status_code == 403

    async def test_returns_rows_and_count(self, auth_headers, monkeypatch):
        from modules import admin_directives
        fake_rows = [
            {"id": 1, "directive_text": "always confirm advances", "category": "general"},
            {"id": 2, "directive_text": "note about client X", "category": "contact_note"},
        ]
        monkeypatch.setattr(admin_directives, "list_directives", AsyncMock(return_value=fake_rows))

        async with await _client() as ac:
            response = await ac.get("/admin/directives", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["rows"] == fake_rows
        assert data["count"] == 2

    async def test_query_params_passed_through(self, auth_headers, monkeypatch):
        from modules import admin_directives
        mock_list = AsyncMock(return_value=[])
        monkeypatch.setattr(admin_directives, "list_directives", mock_list)

        async with await _client() as ac:
            response = await ac.get(
                "/admin/directives",
                headers=auth_headers,
                params={"subject_key": "8801712345678", "status": "revoked", "limit": 10},
            )
        assert response.status_code == 200
        mock_list.assert_awaited_once_with(subject_key="8801712345678", status="revoked", limit=10)


class TestCreateAdminDirective:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.post("/admin/directives", json={"directive_text": "test"})
        assert response.status_code == 403

    async def test_creates_directive_returns_id(self, auth_headers, monkeypatch):
        from modules import admin_directives
        mock_save = AsyncMock(return_value=77)
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)

        async with await _client() as ac:
            response = await ac.post(
                "/admin/directives",
                headers=auth_headers,
                json={"directive_text": "always confirm advances over 5000tk", "category": "general"},
            )
        assert response.status_code == 200
        assert response.json() == {"id": 77, "superseded_id": None}
        mock_save.assert_awaited_once()
        args, kwargs = mock_save.call_args
        assert args[0] == "always confirm advances over 5000tk"
        assert kwargs["created_by_channel"] == "web"
        assert kwargs["category"] == "general"

    async def test_missing_directive_text_returns_400(self, auth_headers, monkeypatch):
        from modules import admin_directives
        mock_save = AsyncMock()
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)

        async with await _client() as ac:
            response = await ac.post("/admin/directives", headers=auth_headers, json={})
        assert response.status_code == 400
        mock_save.assert_not_awaited()

    async def test_optional_subject_fields_passed_through(self, auth_headers, monkeypatch):
        from modules import admin_directives
        mock_save = AsyncMock(return_value=1)
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)
        monkeypatch.setattr(admin_directives, "find_conflicting_directive", AsyncMock(return_value=None))

        async with await _client() as ac:
            response = await ac.post(
                "/admin/directives",
                headers=auth_headers,
                json={
                    "directive_text": "block this number",
                    "category": "autoreply_policy",
                    "subject_type": "phone",
                    "subject_key": "8801712345678",
                },
            )
        assert response.status_code == 200
        _, kwargs = mock_save.call_args
        assert kwargs["subject_type"] == "phone"
        assert kwargs["subject_key"] == "8801712345678"

    async def test_conflicting_directive_returns_409_without_supersede(self, auth_headers, monkeypatch):
        """Phase 3, 2026-08-25: general Owner business-rule mutation
        architecture -- a second active directive on the same
        (subject_type, subject_key) with different text must not silently
        overwrite the first."""
        from modules import admin_directives
        existing = {
            "id": 42, "directive_text": "minimum age is 18",
            "created_at": None,
        }
        mock_conflict = AsyncMock(return_value=existing)
        mock_save = AsyncMock(return_value=99)
        monkeypatch.setattr(admin_directives, "find_conflicting_directive", mock_conflict)
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)

        async with await _client() as ac:
            response = await ac.post(
                "/admin/directives",
                headers=auth_headers,
                json={
                    "directive_text": "minimum age is 23",
                    "subject_type": "recruitment_rule",
                    "subject_key": "recruitment_minimum_age",
                },
            )
        assert response.status_code == 409
        body = response.json()
        assert body["detail"]["error"] == "conflicting_active_directive"
        assert body["detail"]["existing_directive"]["id"] == 42
        mock_save.assert_not_awaited()

    async def test_supersede_true_revokes_old_then_saves_new(self, auth_headers, monkeypatch):
        from modules import admin_directives
        existing = {"id": 42, "directive_text": "minimum age is 18", "created_at": None}
        mock_conflict = AsyncMock(return_value=existing)
        mock_revoke = AsyncMock(return_value=True)
        mock_save = AsyncMock(return_value=99)
        monkeypatch.setattr(admin_directives, "find_conflicting_directive", mock_conflict)
        monkeypatch.setattr(admin_directives, "revoke_directive", mock_revoke)
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)

        async with await _client() as ac:
            response = await ac.post(
                "/admin/directives",
                headers=auth_headers,
                json={
                    "directive_text": "minimum age is 23",
                    "subject_type": "recruitment_rule",
                    "subject_key": "recruitment_minimum_age",
                    "supersede": True,
                },
            )
        assert response.status_code == 200
        assert response.json()["superseded_id"] == 42
        mock_revoke.assert_awaited_once()
        assert mock_revoke.call_args.args[0] == 42
        mock_save.assert_awaited_once()

    async def test_same_text_no_conflict_saves_directly(self, auth_headers, monkeypatch):
        """find_conflicting_directive() itself treats a re-statement of
        the same rule as no-conflict (see its own docstring/unit test) --
        this proves the route path honors that (None means proceed)."""
        from modules import admin_directives
        mock_conflict = AsyncMock(return_value=None)
        mock_save = AsyncMock(return_value=100)
        monkeypatch.setattr(admin_directives, "find_conflicting_directive", mock_conflict)
        monkeypatch.setattr(admin_directives, "save_directive", mock_save)

        async with await _client() as ac:
            response = await ac.post(
                "/admin/directives",
                headers=auth_headers,
                json={
                    "directive_text": "minimum age is 18",
                    "subject_type": "recruitment_rule",
                    "subject_key": "recruitment_minimum_age",
                },
            )
        assert response.status_code == 200
        assert response.json()["superseded_id"] is None


class TestRevokeAdminDirective:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.post("/admin/directives/1/revoke")
        assert response.status_code == 403

    async def test_revoked_true(self, auth_headers, monkeypatch):
        from modules import admin_directives
        mock_revoke = AsyncMock(return_value=True)
        monkeypatch.setattr(admin_directives, "revoke_directive", mock_revoke)

        async with await _client() as ac:
            response = await ac.post("/admin/directives/42/revoke", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {"revoked": True}
        mock_revoke.assert_awaited_once()
        args, kwargs = mock_revoke.call_args
        assert args[0] == 42
        assert "revoked_by_phone" in kwargs

    async def test_revoked_false_when_not_found(self, auth_headers, monkeypatch):
        from modules import admin_directives
        monkeypatch.setattr(admin_directives, "revoke_directive", AsyncMock(return_value=False))

        async with await _client() as ac:
            response = await ac.post("/admin/directives/9999/revoke", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {"revoked": False}
