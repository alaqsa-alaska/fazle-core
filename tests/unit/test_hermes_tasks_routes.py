"""Unit tests -- POST/GET /api/tasks/*, /api/actions/* (modules/hermes_tasks/routes.py).

2026-08-19, Owner-directed follow-on to the P0-P2 pass. Mocks
modules.hermes_tasks.* directly and asserts delegation + RBAC + actor-
identity resolution, same httpx.AsyncClient + ASGITransport pattern as
test_draft_approval_routes.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


@pytest.fixture
def internal_key_headers():
    from app.config import get_settings
    return {"X-Internal-Key": get_settings().internal_api_key}


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


class TestCreateTaskRoute:
    async def test_requires_auth(self):
        mock_check = AsyncMock(return_value={"allowed": False, "reason": "unknown actor"})
        with patch("modules.rbac.check_permission", new=mock_check):
            async with await _client() as ac:
                response = await ac.post("/api/tasks", json={"title": "Do a thing"})
        assert response.status_code == 403

    async def test_internal_key_creates_task(self, internal_key_headers):
        mock_create = AsyncMock(return_value=42)
        with patch("modules.hermes_tasks.create_task", new=mock_create):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/tasks", json={"title": "Fix bug", "priority": "high"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "task_id": 42}
        mock_create.assert_awaited_once()

    async def test_bad_priority_returns_400(self, internal_key_headers):
        from modules.hermes_tasks import TaskError
        mock_create = AsyncMock(side_effect=TaskError("priority must be one of ..."))
        with patch("modules.hermes_tasks.create_task", new=mock_create):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/tasks", json={"title": "X", "priority": "urgent-ish"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 400


class TestGetTaskRoute:
    async def test_not_found_returns_404(self, internal_key_headers):
        with patch("modules.hermes_tasks.get_task", new=AsyncMock(return_value=None)):
            async with await _client() as ac:
                response = await ac.get("/api/tasks/999", headers=internal_key_headers)
        assert response.status_code == 404

    async def test_found_returns_task(self, internal_key_headers):
        task = {"id": 5, "title": "X", "status": "BACKLOG", "events": []}
        with patch("modules.hermes_tasks.get_task", new=AsyncMock(return_value=task)):
            async with await _client() as ac:
                response = await ac.get("/api/tasks/5", headers=internal_key_headers)
        assert response.status_code == 200
        assert response.json()["id"] == 5


class TestApproveActionRoute:
    async def test_requires_superadmin_not_just_operator(self):
        """task_approve is superadmin-tier -- an operator-tier caller must
        still be denied, distinct from task_write's lower bar."""
        mock_check = AsyncMock(return_value={
            "allowed": False, "required_role": "superadmin", "reason": "Insufficient role",
        })
        with patch("modules.rbac.check_permission", new=mock_check):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/actions/7/approve", headers={"X-Internal-Key": "operator-test-key"},
                )
        assert response.status_code == 403

    async def test_internal_key_approves(self, internal_key_headers):
        mock_approve = AsyncMock(return_value={"ok": True, "action_id": 7, "status": "approved"})
        with patch("modules.hermes_tasks.approve_action", new=mock_approve):
            async with await _client() as ac:
                response = await ac.post("/api/actions/7/approve", headers=internal_key_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
        # actor resolved server-side from the key, never from a client body field
        mock_approve.assert_awaited_once_with(7, "owner-internal-key")

    async def test_not_pending_returns_400(self, internal_key_headers):
        from modules.hermes_tasks import TaskError
        mock_approve = AsyncMock(side_effect=TaskError("action #7 is not pending (status=executed)"))
        with patch("modules.hermes_tasks.approve_action", new=mock_approve):
            async with await _client() as ac:
                response = await ac.post("/api/actions/7/approve", headers=internal_key_headers)
        assert response.status_code == 400


class TestProposeActionRoute:
    async def test_operator_tier_key_allowed(self, internal_key_headers):
        """propose_action is task_write (operator) tier, not task_approve --
        a lower bar than approving, since proposing isn't executing."""
        mock_propose = AsyncMock(return_value=7)
        with patch("modules.hermes_tasks.propose_action", new=mock_propose):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/actions",
                    json={"action_type": "file_mutation", "summary": "fix typo", "risk": "low"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 200
        assert response.json() == {"ok": True, "action_id": 7}
