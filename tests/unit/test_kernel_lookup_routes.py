"""Unit tests -- GET /api/hermes/decisions/lookup, GET /api/hermes/kernel/events.

2026-08-19 (same-day follow-up to
HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md): new thin,
read-only routes exposing modules.hermes_kernel's hermes_decision_audit_log/
hermes_event_log to Hermes via fazle-mcp. Both tables already exist
(migration 060) and have been live-written since HERMES_DECISION_AUDIT_ENABLED/
HERMES_EVENT_LOG_ENABLED were flipped true (2026-08-17) but were previously
unreachable by any route or tool. No new logic -- fetch_all is monkeypatched,
same httpx.AsyncClient + ASGITransport pattern as
tests/unit/test_identity_intent_routes.py.
"""
from __future__ import annotations

import uuid
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


class TestLookupDecisions:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/hermes/decisions/lookup")
        assert response.status_code == 403

    async def test_returns_decision_rows(self, auth_headers):
        trace_id = uuid.uuid4()
        fake_row = {
            "trace_id": trace_id,
            "timestamp": None,
            "sender_phone": "01711234567",
            "identity_role": "employee",
            "identity_confidence": 0.88,
            "intent": "salary_query",
            "intent_method": "llm",
            "chosen_action": "reply",
            "explanation": "employee-payroll",
            "escalation_reason": None,
            "provider_used": "groq",
            "source": "bridge1",
            "raw_input_text": "amar betoner koto",
        }
        mock_fetch_all = AsyncMock(return_value=[fake_row])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/hermes/decisions/lookup",
                    params={"phone": "01711234567"},
                    headers=auth_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        row = body["decisions"][0]
        # The whole point of this fix -- policy/permission and domain/section
        # facts, previously written to this table but never exposed.
        assert row["chosen_action"] == "reply"
        assert row["explanation"] == "employee-payroll"
        assert row["intent_method"] == "llm"

        query_arg = mock_fetch_all.call_args[0][0]
        assert "hermes_decision_audit_log" in query_arg
        assert "chosen_action" in query_arg
        assert "explanation" in query_arg

    async def test_filters_are_bound_params_not_raw_sql(self, auth_headers):
        mock_fetch_all = AsyncMock(return_value=[])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/hermes/decisions/lookup",
                    params={"phone": "01711234567", "chosen_action": "draft"},
                    headers=auth_headers,
                )
        assert response.status_code == 200
        query_arg, *params = mock_fetch_all.call_args[0]
        assert "01711234567" in params
        assert "draft" in params
        assert "01711234567" not in query_arg
        assert "draft" not in query_arg

    async def test_no_filters_returns_error_free_default(self, auth_headers):
        mock_fetch_all = AsyncMock(return_value=[])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/hermes/decisions/lookup", headers=auth_headers
                )
        assert response.status_code == 200
        body = response.json()
        assert body["filters_applied"] is False
        assert body["count"] == 0


class TestLookupKernelEvents:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get(
                "/api/hermes/kernel/events", params={"trace_id": str(uuid.uuid4())}
            )
        assert response.status_code == 403

    async def test_returns_events_with_parsed_payload(self, auth_headers):
        trace_id = uuid.uuid4()
        event_id = uuid.uuid4()
        fake_row = {
            "event_id": event_id,
            "trace_id": trace_id,
            "event_name": "EVT-001 message.received",
            "timestamp": None,
            "payload": '{"sender_phone": "01711234567", "source": "bridge1"}',
        }
        mock_fetch_all = AsyncMock(return_value=[fake_row])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/hermes/kernel/events",
                    params={"trace_id": str(trace_id)},
                    headers=auth_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1
        event = body["events"][0]
        assert event["event_name"] == "EVT-001 message.received"
        # JSONB comes back from asyncpg as a string -- must be parsed into a
        # real object for the tool caller, not left as a raw JSON string.
        assert event["payload"] == {"sender_phone": "01711234567", "source": "bridge1"}

    async def test_malformed_payload_string_passed_through_unchanged(self, auth_headers):
        fake_row = {
            "event_id": uuid.uuid4(),
            "trace_id": uuid.uuid4(),
            "event_name": "EVT-002 whatever",
            "timestamp": None,
            "payload": "not-json",
        }
        mock_fetch_all = AsyncMock(return_value=[fake_row])
        with patch("app.main.fetch_all", new=mock_fetch_all):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/hermes/kernel/events",
                    params={"trace_id": str(uuid.uuid4())},
                    headers=auth_headers,
                )
        assert response.status_code == 200
        assert response.json()["events"][0]["payload"] == "not-json"
