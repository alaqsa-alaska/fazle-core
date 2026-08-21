"""Unit test -- GET /api/recruitment/interested-candidates.

Owner Correction 2 / Section 16 (2026-08-21): exhaustive, date-range
job-interest search across every ingest path, without get_recent_messages
(limit=N)'s silent truncation. fetch_all/fetch_one are monkeypatched,
following the same httpx.AsyncClient + ASGITransport pattern as
tests/unit/test_whatsapp_messages_lookup_route.py.
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


class TestRecruitmentInterestedCandidates:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/recruitment/interested-candidates", params={"start": "2026-08-01"})
        assert response.status_code == 403

    async def test_rejects_invalid_window(self, auth_headers):
        async with await _client() as ac:
            response = await ac.get(
                "/api/recruitment/interested-candidates",
                params={"start": "2026-08-21", "end": "2026-08-01"},
                headers=auth_headers,
            )
        assert response.status_code == 200
        assert "error" in response.json()

    async def test_rejects_window_over_92_days(self, auth_headers):
        async with await _client() as ac:
            response = await ac.get(
                "/api/recruitment/interested-candidates",
                params={"start": "2026-01-01", "end": "2026-08-21"},
                headers=auth_headers,
            )
        body = response.json()
        assert "error" in body
        assert "92" in body["error"]

    async def test_finds_explicit_job_interest_and_ignores_unrelated_messages(self, auth_headers):
        rows = [
            {
                "canonical_phone": "8801966011609",
                "sender_number": "8801966011609",
                "platform": "bridge3",
                "received_at": datetime(2026, 8, 21, 5, 46, 23, tzinfo=timezone.utc),
                "message_body": "Hello! Can I get more info on this?",
            },
            {
                "canonical_phone": "8801966011609",
                "sender_number": "8801966011609",
                "platform": "bridge3",
                "received_at": datetime(2026, 8, 21, 5, 46, 59, tzinfo=timezone.utc),
                "message_body": "আমি কাজ করতে চাই",
            },
            {
                "canonical_phone": "8801700000099",
                "sender_number": "8801700000099",
                "platform": "bridge1",
                "received_at": datetime(2026, 8, 21, 6, 0, 0, tzinfo=timezone.utc),
                "message_body": "আজকের হাজিরা কেমন?",  # unrelated, must not appear
            },
            {
                "canonical_phone": "unresolved:19999@lid",
                "sender_number": None,
                "platform": "bridge2",
                "received_at": datetime(2026, 8, 21, 6, 5, 0, tzinfo=timezone.utc),
                "message_body": "চাকরি লাগবে",  # job interest but identity unresolved
            },
        ]
        mock_fetch_all = AsyncMock(return_value=rows)
        mock_fetch_one = AsyncMock(return_value=None)
        with patch("app.main.fetch_all", new=mock_fetch_all), \
             patch("app.main.fetch_one", new=mock_fetch_one):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/recruitment/interested-candidates",
                    params={"start": "2026-08-21", "end": "2026-08-22"},
                    headers=auth_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["completeness"] == "exhaustive_within_requested_window"
        assert body["inbound_messages_scanned"] == 4
        assert body["unresolved_identity_messages_in_window"] == 1
        # Only the two phones with genuine job-interest text are surfaced;
        # the attendance question is excluded even though it's in-window.
        phones = {c["phone"] for c in body["candidates"]}
        assert phones == {"8801966011609", "unresolved:19999@lid"}

        target = next(c for c in body["candidates"] if c["phone"] == "8801966011609")
        assert target["sources"] == ["bridge3"]
        assert target["message_count"] == 1  # only the trigger message counts, not the greeting
        assert target["lead_id"] is None
        assert target["conversation_status"] == "no_lead_yet"
        assert set(target["missing_information"]) == {
            "full_name", "age", "area", "job_preference", "experience_years",
        }

    async def test_known_lead_reports_lead_id_and_missing_fields(self, auth_headers):
        rows = [{
            "canonical_phone": "8801865499694",
            "sender_number": "8801865499694",
            "platform": "bridge1",
            "received_at": datetime(2026, 8, 21, 3, 0, 0, tzinfo=timezone.utc),
            "message_body": "চাকরি চাই",
        }]
        lead = {
            "candidate_id": 5,
            "full_name": None,  # still polluted/unset in this scenario
            "job_preference": "Assistant Supervisor",
            "area": "Chittagong",
            "funnel_stage": "collecting",
            "age": None,
            "experience_years": None,
            "session_id": 31,
            "collection_step": "name",
        }
        with patch("app.main.fetch_all", new=AsyncMock(return_value=rows)), \
             patch("app.main.fetch_one", new=AsyncMock(return_value=lead)):
            async with await _client() as ac:
                response = await ac.get(
                    "/api/recruitment/interested-candidates",
                    params={"start": "2026-08-21", "end": "2026-08-22"},
                    headers=auth_headers,
                )

        body = response.json()
        target = body["candidates"][0]
        assert target["lead_id"] == 5
        assert target["session_id"] == 31
        assert target["position_interest"] == "Assistant Supervisor"
        assert target["known_location"] == "Chittagong"
        assert target["conversation_status"] == "collecting"
        assert "full_name" in target["missing_information"]
        assert "job_preference" not in target["missing_information"]
