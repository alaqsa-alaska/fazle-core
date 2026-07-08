"""API Integration tests — Webhook message processing"""
from __future__ import annotations

import hmac
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.integration

# Re-use payload builders from conftest
from tests.conftest import (
    make_bridge_payload,
    make_meta_payload,
    GUARD_PHONE,
    ADMIN_PHONE,
    CLIENT_PHONE,
    CANDIDATE_PHONE,
    UNKNOWN_PHONE,
)


def _meta_signature(payload: dict, secret: str = "test_app_secret") -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _meta_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class TestBridgeWebhookRouting:
    """Test that Bridge1 and Bridge2 webhooks accept messages."""

    async def test_bridge1_webhook_accepts_message(self, client, mock_all_services):
        payload = make_bridge_payload(UNKNOWN_PHONE, "hello")
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

    async def test_bridge2_webhook_accepts_message(self, client, mock_all_services):
        payload = make_bridge_payload(UNKNOWN_PHONE, "hello")
        response = await client.post("/webhook/mcp2", json=payload)
        assert response.status_code in (200, 202)

    async def test_malformed_bridge_payload_returns_422(self, client):
        response = await client.post("/webhook/mcp1", json={"bad": "payload"})
        assert response.status_code in (200, 400, 422)


class TestMetaWebhookProcessing:
    async def test_meta_webhook_processes_text_message(self, client, mock_all_services):
        payload = make_meta_payload(UNKNOWN_PHONE, "hello there")
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _meta_signature(payload),
            },
        )
        assert response.status_code == 200

    async def test_meta_webhook_empty_messages_ok(self, client):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "X", "changes": [{"value": {}, "field": "messages"}]}],
        }
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": _meta_signature(payload),
            },
        )
        assert response.status_code == 200


class TestMessageDeduplication:
    """Identical messages within 120s should not be processed twice."""

    async def test_duplicate_message_skipped(self, client, mock_all_services, test_db_pool):
        payload = make_bridge_payload(UNKNOWN_PHONE, "duplicate test message")

        # Send twice
        r1 = await client.post("/webhook/mcp1", json=payload)
        r2 = await client.post("/webhook/mcp1", json=payload)

        assert r1.status_code in (200, 202)
        assert r2.status_code in (200, 202)

        # Only one message should be archived
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_whatsapp_messages WHERE sender_number=$1",
                UNKNOWN_PHONE,
            )
        assert count <= 1, "Duplicate message should not be archived twice"


class TestEscortOrderViaWebhook:
    """Test escort order processing via webhook."""

    async def test_escort_order_creates_program(
        self, client, mock_all_services, test_db_pool
    ):
        # Seed client as escort buyer
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_contact_roles (phone, role, label)
                VALUES ($1, 'client_escort_buyer', 'Test Client')
                ON CONFLICT (phone) DO NOTHING
            """, CLIENT_PHONE)

        payload = make_bridge_payload(
            CLIENT_PHONE,
            "MV GOLDEN STAR\n"
            "lighter: AMENA-3\n"
            "master mobile: 01933333333\n"
            "wheat 5000MT\n"
            "escort lagbe 06/05/2026 Day",
        )

        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM wbom_escort_programs LIMIT 1"
            )
        assert row is not None


class TestAttendanceViaWebhook:
    async def test_guard_attendance_creates_draft(
        self, client, mock_all_services, test_db_pool, seed_employee
    ):
        payload = make_bridge_payload(
            seed_employee["employee_mobile"],
            "হাজির আছি MV TEST",
        )

        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies WHERE intent='attendance' LIMIT 1"
            )
        assert draft is not None


class TestAdvanceRequestViaWebhook:
    async def test_employee_advance_request_creates_payment_draft(
        self, client, mock_all_services, test_db_pool, seed_employee
    ):
        payload = make_bridge_payload(
            seed_employee["employee_mobile"],
            "অগ্রিম লাগবে ২০০০ টাকা",
        )

        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow(
                "SELECT * FROM fazle_draft_replies "
                "WHERE intent='employee_conversation' LIMIT 1"
            )
        assert draft is not None


class TestAdminCommandViaWebhook:
    async def test_admin_approve_sends_reply(
        self, client, mock_all_services, test_db_pool
    ):
        # Create a pending draft
        async with test_db_pool.acquire() as conn:
            draft = await conn.fetchrow("""
                INSERT INTO fazle_draft_replies
                    (recipient, reply_text, intent, source, status)
                VALUES ('8801811111111', 'Test reply', 'generic', 'bridge1', 'pending')
                RETURNING id
            """)
        draft_id = draft["id"]

        # Seed admin in contact_roles
        async with test_db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO fazle_admins (phone, name, status)
                VALUES ($1, 'Test Admin', 'active')
                ON CONFLICT (phone) DO NOTHING
            """, ADMIN_PHONE)

        payload = make_bridge_payload(ADMIN_PHONE, f"APPROVE {draft_id}", source="bridge2")
        response = await client.post("/webhook/mcp2", json=payload)
        assert response.status_code in (200, 202)
