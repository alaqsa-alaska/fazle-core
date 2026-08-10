"""
Unit tests for POST /api/drafts (2026-08-10, Hermes Capability Expansion
Level 1) — the new external-caller entry point wrapping
shared.draft_reply.create_draft_reply(). Covers:
  - missing recipient/draft_text -> 400, create_draft_reply never called
  - happy path -> 200, draft_id returned, create_draft_reply called with
    the right positional mapping (recipient->sender, bridge->bridge)
  - suppressed (create_draft_reply returns None) -> 200, ok=False, no 500
  - auth gate (403 without a valid key) already covered by
    test_drafts_router_mounted.py; not repeated here.
"""
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


async def _client():
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def test_create_draft_missing_fields_rejected_without_calling_create_draft_reply():
    from app.config import get_settings

    key = get_settings().internal_api_key
    async with await _client() as ac:
        with patch("shared.draft_reply.create_draft_reply", new=AsyncMock()) as mock_create:
            resp = await ac.post(
                "/api/drafts",
                json={"recipient": "", "bridge": "bridge1", "draft_text": ""},
                headers={"X-Internal-Key": key},
            )
    assert resp.status_code == 400
    mock_create.assert_not_called()


async def test_create_draft_happy_path_returns_draft_id():
    from app.config import get_settings

    key = get_settings().internal_api_key
    async with await _client() as ac:
        with patch(
            "shared.draft_reply.create_draft_reply", new=AsyncMock(return_value=42)
        ) as mock_create:
            resp = await ac.post(
                "/api/drafts",
                json={
                    "recipient": "8801700000000",
                    "bridge": "bridge2",
                    "draft_text": "আপনার বেতন ৫ তারিখে পাঠানো হবে।",
                    "role": "employee",
                    "intent": "salary_query",
                    "context": "asked about this month's salary date",
                },
                headers={"X-Internal-Key": key},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "draft_id": 42, "status": "pending"}
    mock_create.assert_awaited_once()
    _, kwargs = mock_create.await_args
    assert kwargs["sender"] == "8801700000000"
    assert kwargs["bridge"] == "bridge2"
    assert kwargs["draft_text"].startswith("আপনার বেতন")
    assert kwargs["role"] == "employee"
    assert kwargs["intent"] == "salary_query"
    assert kwargs["source_module"] == "hermes_admin"


async def test_create_draft_suppressed_returns_ok_false_not_a_500():
    from app.config import get_settings

    key = get_settings().internal_api_key
    async with await _client() as ac:
        with patch("shared.draft_reply.create_draft_reply", new=AsyncMock(return_value=None)):
            resp = await ac.post(
                "/api/drafts",
                json={
                    "recipient": "8801700000000",
                    "bridge": "bridge2",
                    "draft_text": "duplicate attempt",
                },
                headers={"X-Internal-Key": key},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "suppressed" in body["reason"]


async def test_create_draft_defaults_role_and_intent_when_omitted():
    from app.config import get_settings

    key = get_settings().internal_api_key
    async with await _client() as ac:
        with patch(
            "shared.draft_reply.create_draft_reply", new=AsyncMock(return_value=7)
        ) as mock_create:
            resp = await ac.post(
                "/api/drafts",
                json={"recipient": "8801700000001", "bridge": "bridge1", "draft_text": "hi"},
                headers={"X-Internal-Key": key},
            )
    assert resp.status_code == 200
    _, kwargs = mock_create.await_args
    assert kwargs["role"] == "unknown"
    assert kwargs["intent"] == "hermes_suggested"
