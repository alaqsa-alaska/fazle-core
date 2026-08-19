"""Unit tests -- POST/GET /api/payment-drafts/* (modules/draft_approval/routes.py).

2026-08-19, Task 2 of HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_
AUDIT_2026-08-19.md's approved plan: new routes wrapping the existing,
unmodified modules.draft_approval.approve_draft/edit_draft/reject_draft --
the same functions the WhatsApp APPROVED/DREDIT/DREJECT admin commands
already call. No new business logic -- these tests mock draft_approval.*
directly and assert delegation + RBAC + actor-identity resolution, same
httpx.AsyncClient + ASGITransport pattern as
tests/unit/test_kernel_lookup_routes.py.
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


class TestApproveRoute:
    async def test_requires_auth(self):
        # check_permission() itself queries the DB for the required role
        # level before it even looks at whether a key was supplied (same
        # pre-existing characteristic as _require_admin_role elsewhere in
        # this codebase) -- mock it to simulate "no/unknown actor" so this
        # test doesn't need a live DB to prove the 403 path.
        mock_check = AsyncMock(return_value={"allowed": False, "reason": "unknown actor"})
        with patch("modules.rbac.check_permission", new=mock_check):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/approve", json={"amount": 1000, "method": "bkash"}
                )
        assert response.status_code == 403

    async def test_operator_tier_key_allowed(self, internal_key_headers):
        """The internal key (owner-tier) must be allowed -- same shortcut
        every other _require_command-style dependency in this codebase
        grants it."""
        mock_approve = AsyncMock(return_value={
            "ok": True, "draft_id": 1, "status": "completed",
            "transaction_id": 55, "txn_ref": "TXN-ABC", "employee_name": "Sumon",
            "amount": 3000.0, "method": "bkash", "accountant_msg": "...",
        })
        with patch("modules.draft_approval.approve_draft", new=mock_approve):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/approve",
                    json={"amount": 3000, "method": "bkash"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["transaction_id"] == 55
        # Server-resolved actor identity, never a client-supplied field --
        # the internal key resolves to "owner-internal-key".
        mock_approve.assert_called_once_with(1, 3000.0, "bkash", "owner-internal-key")

    async def test_viewer_tier_key_rejected(self):
        """A real admin key with only viewer-level role must be denied --
        this action requires operator tier (COMMAND_ROLE['paid'])."""
        mock_check = AsyncMock(return_value={"allowed": False, "reason": "requires operator role or higher"})
        with patch("modules.rbac.check_permission", new=mock_check):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/approve",
                    json={"amount": 3000, "method": "bkash"},
                    headers={"X-Internal-Key": "some-viewer-tier-key"},
                )
        assert response.status_code == 403

    async def test_draft_approval_failure_maps_to_400(self, internal_key_headers):
        mock_approve = AsyncMock(return_value={"ok": False, "error": "Draft #1 already completed", "draft_id": 1})
        with patch("modules.draft_approval.approve_draft", new=mock_approve):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/approve",
                    json={"amount": 3000, "method": "bkash"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 400
        assert "already completed" in response.json()["detail"]

    async def test_missing_amount_is_422(self, internal_key_headers):
        async with await _client() as ac:
            response = await ac.post(
                "/api/payment-drafts/1/approve",
                json={"method": "bkash"},
                headers=internal_key_headers,
            )
        assert response.status_code == 422


class TestEditRoute:
    async def test_delegates_with_all_fields(self, internal_key_headers):
        mock_edit = AsyncMock(return_value={"ok": True, "draft_id": 1, "version": 2})
        with patch("modules.draft_approval.edit_draft", new=mock_edit):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/edit",
                    json={"new_amount": 4000, "new_method": "cash", "reason": "correction"},
                    headers=internal_key_headers,
                )
        assert response.status_code == 200
        mock_edit.assert_called_once_with(1, 4000.0, "cash", None, "owner-internal-key", "correction")

    async def test_all_fields_optional(self, internal_key_headers):
        mock_edit = AsyncMock(return_value={"ok": True, "draft_id": 1, "version": 2})
        with patch("modules.draft_approval.edit_draft", new=mock_edit):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/edit", json={}, headers=internal_key_headers,
                )
        assert response.status_code == 200
        mock_edit.assert_called_once_with(1, None, None, None, "owner-internal-key", None)


class TestRejectRoute:
    async def test_delegates(self, internal_key_headers):
        mock_reject = AsyncMock(return_value={"ok": True, "draft_id": 1, "status": "rejected", "reason": "dup"})
        with patch("modules.draft_approval.reject_draft", new=mock_reject):
            async with await _client() as ac:
                response = await ac.post(
                    "/api/payment-drafts/1/reject", json={"reason": "dup"}, headers=internal_key_headers,
                )
        assert response.status_code == 200
        mock_reject.assert_called_once_with(1, "owner-internal-key", "dup")


class TestAuditAndBalanceRoutes:
    async def test_get_draft_audit(self, internal_key_headers):
        mock_events = AsyncMock(return_value=[{"id": 1, "event": "approved"}])
        with patch("modules.draft_approval.get_draft_audit_events", new=mock_events):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/payment-drafts/1/audit", headers=internal_key_headers,
                )
        assert response.status_code == 200
        assert response.json()["events"] == [{"id": 1, "event": "approved"}]

    async def test_get_employee_balance(self, internal_key_headers):
        mock_balance = AsyncMock(return_value={"employee_id": 5, "period": "2026-08", "closing_balance": 100})
        with patch("modules.draft_approval.get_employee_balance", new=mock_balance):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/payment-drafts/employee/5/balance", headers=internal_key_headers,
                )
        assert response.status_code == 200
        assert response.json()["employee_id"] == 5
