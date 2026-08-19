"""Unit test -- GET /api/whatsapp/messages/lookup.

2026-08-18 fix (HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md):
this route's SELECT previously listed `classification` (a legacy column never
written for wbom_whatsapp_messages -- confirmed by a repo-wide grep and a live
schema check) but omitted `identity_role`/`identity_confidence`/
`intent_detected`, which bridge_poller actually populates for every message.
This test asserts the query now selects the real columns and that the route
returns them in the response, without needing a real Postgres connection
(fetch_all is monkeypatched, following the same httpx.AsyncClient +
ASGITransport pattern as tests/unit/test_admin_directives_routes.py).

2026-08-19 follow-up: the SELECT still omitted `message_body`/`extracted_text`
-- Hermes could see that a message was processed but never what it said or
what was replied. Widened again, same additive pattern.
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


class TestWhatsappMessagesLookup:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/whatsapp/messages/lookup")
        assert response.status_code == 403

    async def test_selects_and_returns_identity_and_intent_columns(self, auth_headers):
        fake_row = {
            "message_id": 1,
            "sender_number": "01711234567",
            "canonical_phone": "8801711234567",
            "platform": "bridge1",
            "direction": "inbound",
            "message_type": "text",
            "is_processed": True,
            "status": "done",
            "received_at": None,
            "processed_at": None,
            "identity_role": "employee",
            "identity_confidence": 88,
            "intent_detected": "salary_query",
            "classification": "unclassified",
            "workflow_triggered": "payroll",
            "message_body": "amar betoner koto",
            "extracted_text": None,
        }
        mock_fetch_all = AsyncMock(return_value=[fake_row])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/whatsapp/messages/lookup",
                    params={"phone": "01711234567"},
                    headers=auth_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        returned = body["messages"][0]
        # The columns this fix adds -- must actually be present, not just
        # silently dropped by the SELECT as they were before.
        assert returned["identity_role"] == "employee"
        assert returned["identity_confidence"] == 88
        assert returned["intent_detected"] == "salary_query"
        # Existing fields must still be present -- purely additive change.
        assert returned["classification"] == "unclassified"
        assert returned["workflow_triggered"] == "payroll"
        # 2026-08-19: the content fields -- the whole point of this fix.
        assert returned["message_body"] == "amar betoner koto"
        assert returned["extracted_text"] is None

        # Assert the actual SQL sent to fetch_all requests the new columns --
        # guards against a future edit reverting the SELECT list silently.
        query_arg = mock_fetch_all.call_args[0][0]
        assert "identity_role" in query_arg
        assert "identity_confidence" in query_arg
        assert "intent_detected" in query_arg
        assert "message_body" in query_arg
        assert "extracted_text" in query_arg
