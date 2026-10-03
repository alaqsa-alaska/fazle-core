"""Stage F -- POST /api/business-actions and GET /api/whatsapp/office-group/activity.

Same ASGITransport + mocked app.database convention as the Stage C/D/E
route test families.
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


class TestBusinessActionsRouteAuth:
    async def test_requires_auth(self):
        async with await _client() as ac:
            resp = await ac.post("/api/business-actions", json={
                "domain": "recruitment", "business_reference": "x:bridge3",
                "proposed_action_type": "business_record_review", "reason": "r",
                "evidence_refs": [{"type": "message_id", "value": 1}], "source_event_ref": "e1",
            })
        assert resp.status_code == 403


class TestBusinessActionsRoute:
    async def test_valid_proposal_creates_pending_action(self, auth_headers):
        with patch("app.database.fetch_one", new=AsyncMock(return_value=None)), \
             patch("modules.hermes_tasks.fetch_val", new=AsyncMock(return_value=99)), \
             patch("modules.hermes_tasks.fetch_one", new=AsyncMock(return_value={
                 "id": 99, "status": "pending", "domain": "recruitment",
             })), \
             patch("modules.hermes_tasks.execute", new=AsyncMock()), \
             patch("modules.hermes_tasks._event", new=AsyncMock()):
            async with await _client() as ac:
                resp = await ac.post(
                    "/api/business-actions",
                    json={
                        "domain": "recruitment", "business_reference": "8801784396009:bridge3",
                        "proposed_action_type": "business_reply_draft", "reason": "candidate agreed, needs joining follow-up",
                        "evidence_refs": [{"type": "message_id", "value": 19779}],
                        "source_event_ref": "msg:19779",
                    },
                    headers=auth_headers,
                )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "pending"

    async def test_unsupported_domain_returns_400_not_500(self, auth_headers):
        async with await _client() as ac:
            resp = await ac.post(
                "/api/business-actions",
                json={
                    "domain": "payroll", "business_reference": "x:bridge3",
                    "proposed_action_type": "business_record_review", "reason": "r",
                    "evidence_refs": [{"type": "message_id", "value": 1}], "source_event_ref": "e1",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 400

    async def test_office_internal_domain_returns_400(self, auth_headers):
        async with await _client() as ac:
            resp = await ac.post(
                "/api/business-actions",
                json={
                    "domain": "office_internal", "business_reference": "x:bridge2",
                    "proposed_action_type": "business_record_review", "reason": "r",
                    "evidence_refs": [{"type": "message_id", "value": 1}], "source_event_ref": "e1",
                },
                headers=auth_headers,
            )
        assert resp.status_code == 400


class TestListActionsDomainFilter:
    async def test_domain_query_param_is_forwarded(self, auth_headers):
        captured = {}

        async def _fake_fetch_all(sql, *args):
            captured["sql"] = sql
            captured["args"] = args
            return []

        with patch("modules.hermes_tasks.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/actions", params={"domain": "escort", "status": "pending"}, headers=auth_headers)
        assert "domain = " in captured["sql"]
        assert "escort" in captured["args"]


def _msg_row(**overrides):
    base = {
        "message_id": 1, "direction": "inbound", "platform": "bridge2",
        "sender_number": "unresolved:1", "canonical_phone": None,
        "conversation_key": "120363426895117693@g.us:bridge2", "actor_type": "external_user",
        "actor_id": None, "identity_role": "unknown", "identity_confidence": None,
        "intent_detected": None, "workflow_triggered": None,
        "message_type": "text", "is_processed": False, "status": "sent",
        "received_at": datetime(2026, 8, 28, 16, 0, 0, tzinfo=timezone.utc),
        "processed_at": None,
    }
    base.update(overrides)
    return base


class TestOfficeGroupActivityRoute:
    async def test_requires_auth(self):
        async with await _client() as ac:
            resp = await ac.get("/api/whatsapp/office-group/activity")
        assert resp.status_code == 403

    async def test_no_approved_group_returns_empty_not_error(self, auth_headers):
        with patch("app.main.fetch_all", new=AsyncMock(return_value=[])):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/office-group/activity", headers=auth_headers)
        body = resp.json()
        assert body["returned"] == 0
        assert body["messages"] == []
        assert "error" not in body

    async def test_approved_group_activity_is_metadata_only_by_default(self, auth_headers):
        calls = {"n": 0}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"group_jid": "120363426895117693@g.us", "source_bridge": "bridge2"}]
            return [_msg_row()]

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                resp = await ac.get("/api/whatsapp/office-group/activity", headers=auth_headers)
        body = resp.json()
        assert body["returned"] == 1
        assert body["groups"] == ["120363426895117693@g.us"]
        for m in body["messages"]:
            assert "message_body" not in m

    async def test_include_body_true_adds_content(self, auth_headers):
        calls = {"n": 0}
        captured = {}

        async def _capture(sql, *args):
            captured["sql"] = sql
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"group_jid": "120363426895117693@g.us", "source_bridge": "bridge2"}]
            return []

        with patch("app.main.fetch_all", new=_capture):
            async with await _client() as ac:
                await ac.get(
                    "/api/whatsapp/office-group/activity",
                    params={"include_body": "true"}, headers=auth_headers,
                )
        assert "message_body" in captured["sql"]

    async def test_merges_across_both_source_bridges_for_the_same_jid(self, auth_headers):
        calls = {"n": 0}
        captured = {}

        async def _fake_fetch_all(sql, *args):
            calls["n"] += 1
            if calls["n"] == 1:
                return [
                    {"group_jid": "120363426895117693@g.us", "source_bridge": "bridge1"},
                    {"group_jid": "120363426895117693@g.us", "source_bridge": "bridge2"},
                ]
            captured["args"] = args
            return []

        with patch("app.main.fetch_all", new=_fake_fetch_all):
            async with await _client() as ac:
                await ac.get("/api/whatsapp/office-group/activity", headers=auth_headers)
        conv_keys = captured["args"][0]
        assert "120363426895117693@g.us:bridge1" in conv_keys
        assert "120363426895117693@g.us:bridge2" in conv_keys
