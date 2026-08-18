"""Unit tests -- GET /api/identity/resolve, GET /api/intent/classify.

2026-08-18 (HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md):
new thin, read-only routes exposing the existing identity_brain.detect_identity()
and modules.intent.classify() to Hermes via fazle-mcp. Follows the same
httpx.AsyncClient + ASGITransport pattern as tests/unit/test_admin_directives_routes.py.
No new classification logic -- these tests only prove the routes wire through
correctly and that bkash_number/basic_salary are stripped from the identity
response.
"""
from __future__ import annotations

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


class TestResolveIdentity:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/identity/resolve", params={"phone": "01711234567"})
        assert response.status_code == 403

    async def test_strips_financial_pii_fields(self, auth_headers):
        fake_identity = {
            "role": "employee",
            "employee_id": 42,
            "employee_name": "Karim",
            "designation": "Guard",
            "basic_salary": 15000.0,
            "bkash_number": "01799999999",
            "contact_id": None,
            "display_name": "Karim",
            "company_name": None,
            "relation_name": "Employee",
            "confidence": 0.88,
            "identity_role": "employee",
            "identity_confidence": 88,
            "identity_source": "db_employee",
            "identity_priority": 30,
            "identity_notes": "",
            "identity_lifecycle": "active",
        }
        mock_detect = AsyncMock(return_value=fake_identity)
        with patch("modules.identity_brain.detect_identity", new=mock_detect):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/identity/resolve",
                    params={"phone": "01711234567", "text": "hello"},
                    headers=auth_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["identity_role"] == "employee"
        assert body["identity_confidence"] == 88
        assert body["identity_source"] == "db_employee"
        # The whole point of the strip -- must never leak these two fields.
        assert "bkash_number" not in body
        assert "basic_salary" not in body
        mock_detect.assert_awaited_once_with("01711234567", "hello")

    async def test_text_param_optional(self, auth_headers):
        mock_detect = AsyncMock(return_value={"role": "unknown", "identity_role": "unknown"})
        with patch("modules.identity_brain.detect_identity", new=mock_detect):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/identity/resolve",
                    params={"phone": "01711234567"},
                    headers=auth_headers,
                )
        assert response.status_code == 200
        mock_detect.assert_awaited_once_with("01711234567", "")


class TestClassifyIntent:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/intent/classify", params={"text": "amar betoner"})
        assert response.status_code == 403

    async def test_returns_classified_intent(self, auth_headers):
        async with await _client() as ac:
            response = await ac.get(
                "/api/intent/classify",
                params={"text": "আমার বেতন কত"},
                headers=auth_headers,
            )
        assert response.status_code == 200
        assert response.json() == {"intent": "salary_query"}
