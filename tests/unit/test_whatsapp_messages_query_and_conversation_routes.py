"""Stage C (2026-08-28, filtered conversation retrieval) -- unit tests for
GET /api/whatsapp/messages/query and GET /api/whatsapp/messages/conversation.

Follows this repo's existing pattern for this route family (see
tests/unit/test_whatsapp_messages_lookup_route.py): httpx ASGITransport
against the real FastAPI app, app.main.fetch_all monkeypatched so no live
Postgres connection is required. Verifies query construction (filters,
bound params, cursor math, ordering, has_more/limit-plus-one logic) rather
than re-testing Postgres itself.
"""
from __future__ import annotations

import base64
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


def _row(**overrides):
    base = {
        "message_id": 1, "direction": "inbound", "platform": "bridge3",
        "sender_number": "8801700000000", "canonical_phone": "8801700000000",
        "conversation_key": "8801700000000:bridge3", "actor_type": "external_user",
        "actor_id": None, "identity_role": "candidate", "identity_confidence": 80,
        "intent_detected": "recruitment", "workflow_triggered": None,
        "message_type": "text", "is_processed": True, "status": "done",
        "received_at": datetime(2026, 8, 28, 3, 0, 0, tzinfo=timezone.utc),
        "processed_at": None,
    }
    base.update(overrides)
    return base


class TestQueryRouteAuth:
    async def test_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/whatsapp/messages/query")
        assert response.status_code == 403

    async def test_conversation_requires_auth(self):
        async with await _client() as ac:
            response = await ac.get("/api/whatsapp/messages/conversation", params={"conversation_key": "x"})
        assert response.status_code == 403


class TestMetadataFirstDefault:
    async def test_default_excludes_message_body(self, auth_headers):
        """C4: include_body defaults to False -- message_body/extracted_text
        must never appear in the SQL column list, let alone the response."""
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/messages/query", headers=auth_headers)
        assert resp.status_code == 200
        assert "message_body" not in captured["sql"]
        assert "extracted_text" not in captured["sql"]
        assert resp.json()["include_body"] is False

    async def test_include_body_true_adds_content_columns(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/messages/query",
                    params={"include_body": "true"}, headers=auth_headers,
                )
        assert "message_body" in captured["sql"]
        assert "extracted_text" in captured["sql"]

    async def test_conversation_endpoint_always_includes_body(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/messages/conversation",
                    params={"conversation_key": "8801700000000:bridge3"}, headers=auth_headers,
                )
        assert "message_body" in captured["sql"]


class TestFilters:
    @pytest.mark.parametrize("param,value,expected_sql_fragment", [
        ("platform", "bridge3", "platform ="),
        ("direction", "inbound", "direction ="),
        ("actor_type", "hermes", "actor_type ="),
        ("actor_id", "hermes-runner", "actor_id ="),
        ("identity_role", "candidate", "identity_role ="),
        ("intent_detected", "salary_query", "intent_detected ="),
        ("workflow_triggered", "recruitment", "workflow_triggered ="),
        ("conversation_key", "8801700000000:bridge3", "conversation_key ="),
    ])
    async def test_each_filter_adds_its_own_condition(self, auth_headers, param, value, expected_sql_fragment):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/messages/query", params={param: value}, headers=auth_headers,
                )
        assert resp.status_code == 200
        assert expected_sql_fragment in captured["sql"]
        assert value in captured["args"]
        assert resp.json()["filters_applied"] is True

    async def test_time_range_filters(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/messages/query",
                    params={"start_time": "2026-08-28T00:00:00Z", "end_time": "2026-08-28T23:59:59Z"},
                    headers=auth_headers,
                )
        assert "received_at >=" in captured["sql"]
        assert "received_at <=" in captured["sql"]

    async def test_combined_filters(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/messages/query",
                    params={"platform": "bridge3", "direction": "inbound", "workflow_triggered": "recruitment"},
                    headers=auth_headers,
                )
        assert "platform =" in captured["sql"]
        assert "direction =" in captured["sql"]
        assert "workflow_triggered =" in captured["sql"]
        assert "bridge3" in captured["args"]
        assert "inbound" in captured["args"]
        assert "recruitment" in captured["args"]

    async def test_no_filters_means_filters_applied_false(self, auth_headers):
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[])):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/messages/query", headers=auth_headers)
        assert resp.json()["filters_applied"] is False


class TestPaginationAndCursor:
    async def test_ordering_is_received_at_and_message_id_desc(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/messages/query", headers=auth_headers)
        assert "ORDER BY received_at DESC, message_id DESC" in captured["sql"]

    async def test_fetches_limit_plus_one_to_detect_has_more(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/messages/query", params={"limit": 5}, headers=auth_headers)
        assert captured["args"][-1] == 6  # limit+1

    async def test_has_more_true_when_extra_row_present(self, auth_headers):
        rows = [_row(message_id=i) for i in range(3, 0, -1)]  # 3 rows, limit=2 below
        with patch("app.main.fetch_all", new=AsyncMock(return_value=rows)):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/messages/query", params={"limit": 2}, headers=auth_headers)
        body = resp.json()
        assert body["has_more"] is True
        assert body["count"] == 2
        assert body["next_cursor"] is not None

    async def test_has_more_false_when_exactly_at_limit(self, auth_headers):
        rows = [_row(message_id=i) for i in range(2, 0, -1)]  # exactly 2 rows for limit=2
        with patch("app.main.fetch_all", new=AsyncMock(return_value=rows)):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/messages/query", params={"limit": 2}, headers=auth_headers)
        body = resp.json()
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    async def test_limit_capped_at_50(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/messages/query", params={"limit": 999}, headers=auth_headers)
        assert captured["args"][-1] == 51  # capped 50 + 1

    async def test_limit_zero_falls_back_to_default_20(self, auth_headers):
        """`limit or 20` semantics (matching the sibling /lookup endpoint):
        0 is falsy, so it resolves to the default, not a literal zero-row
        page."""
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/messages/query", params={"limit": 0}, headers=auth_headers)
        assert captured["args"][-1] == 21  # default 20 + 1

    async def test_negative_limit_floored_to_1(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/messages/query", params={"limit": -5}, headers=auth_headers)
        assert captured["args"][-1] == 2  # floor 1 + 1

    async def test_valid_cursor_adds_tuple_condition_for_stable_no_skip_no_dup_paging(self, auth_headers):
        """The (received_at, message_id) < (cursor_ts, cursor_id) tuple
        comparison is what makes pagination stable across timestamp
        collisions -- a plain `received_at < cursor_ts` alone would skip
        or duplicate rows sharing the exact same timestamp."""
        ts = datetime(2026, 8, 28, 3, 0, 0, tzinfo=timezone.utc)
        raw = f"{ts.isoformat()}|100"
        cursor = base64.urlsafe_b64encode(raw.encode()).decode()
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/messages/query", params={"cursor": cursor}, headers=auth_headers,
                )
        assert resp.status_code == 200
        assert "(received_at, message_id) <" in captured["sql"]
        assert 100 in captured["args"]

    async def test_invalid_cursor_returns_clean_error_not_500(self, auth_headers):
        async with await _client() as ac:
            resp = await ac.get(
                "/api/whatsapp/messages/query", params={"cursor": "not-valid-base64!!"}, headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["error"] == "invalid cursor"

    async def test_next_cursor_decodes_back_to_last_row_on_the_page(self, auth_headers):
        """No duplicate/skip guarantee, proven end to end: the emitted
        cursor must decode to exactly the last row returned on this page."""
        from app.main import _decode_cursor
        rows = [
            _row(message_id=3, received_at=datetime(2026, 8, 28, 3, 2, 0, tzinfo=timezone.utc)),
            _row(message_id=2, received_at=datetime(2026, 8, 28, 3, 1, 0, tzinfo=timezone.utc)),
            _row(message_id=1, received_at=datetime(2026, 8, 28, 3, 0, 0, tzinfo=timezone.utc)),
        ]
        with patch("app.main.fetch_all", new=AsyncMock(return_value=rows)):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/messages/query", params={"limit": 2}, headers=auth_headers)
        body = resp.json()
        decoded_ts, decoded_id = _decode_cursor(body["next_cursor"])
        assert decoded_id == 2  # the last row actually returned on this page


class TestConversationHistory:
    async def test_requires_conversation_key(self, auth_headers):
        async with await _client() as ac:
            resp = await ac.get("/api/whatsapp/messages/conversation", headers=auth_headers)
        assert resp.status_code == 422  # FastAPI: required query param missing

    async def test_result_is_chronological_oldest_first(self, auth_headers):
        """The route fetches newest-first (for stable cursor math) but
        must reverse the page before returning -- a conversation's own
        history reads oldest-to-newest."""
        rows = [
            _row(message_id=3, direction="outbound", received_at=datetime(2026, 8, 28, 3, 2, 0, tzinfo=timezone.utc)),
            _row(message_id=2, direction="inbound", received_at=datetime(2026, 8, 28, 3, 1, 0, tzinfo=timezone.utc)),
            _row(message_id=1, direction="inbound", received_at=datetime(2026, 8, 28, 3, 0, 0, tzinfo=timezone.utc)),
        ]
        with patch("app.main.fetch_all", new=AsyncMock(return_value=rows)):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/messages/conversation",
                    params={"conversation_key": "x:bridge3"}, headers=auth_headers,
                )
        ids = [m["message_id"] for m in resp.json()["messages"]]
        assert ids == [1, 2, 3]

    async def test_conversation_key_is_bound_parameter(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/messages/conversation",
                    params={"conversation_key": "8801700000000:bridge3"}, headers=auth_headers,
                )
        assert "conversation_key = $1" in captured["sql"]
        assert captured["args"][0] == "8801700000000:bridge3"

    async def test_empty_result(self, auth_headers):
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[])):
            async with await _client() as ac:
                resp = await ac.get(
                    "/api/whatsapp/messages/conversation",
                    params={"conversation_key": "nonexistent:bridge3"}, headers=auth_headers,
                )
        body = resp.json()
        assert body["count"] == 0
        assert body["messages"] == []
        assert body["has_more"] is False
