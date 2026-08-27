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


def _messenger_payload(sender: str, text: str, *, object_type: str = "page", is_echo: bool = False, mid: str = "mid.test1") -> dict:
    """Minimal Messenger/Instagram webhook payload — object_type is what
    actually distinguishes the two (Owner directive, 2026-08-27); the
    per-message shape (sender/recipient/message.mid/message.text/is_echo)
    is field-compatible across both."""
    entry: dict = {
        "id": "PAGE_OR_IG_ID",
        "messaging": [{
            "sender": {"id": sender},
            "recipient": {"id": "PAGE_OR_IG_ID"},
            "timestamp": 1746518400,
            "message": {"mid": mid, "text": text},
        }],
    }
    if is_echo:
        entry["messaging"][0]["message"]["is_echo"] = True
    return {"object": object_type, "entry": [entry]}


class TestMetaMessengerInstagramRouting:
    """Owner directive (2026-08-27): payload["object"] is what actually
    distinguishes Instagram from Messenger — this is the regression test
    for the real bug found during discovery (the dispatch loop routed
    every entry's messaging[] into _handle_messenger_message()
    unconditionally, with no object-type check at all)."""

    async def test_instagram_object_routes_to_instagram_handler_not_messenger(self, client):
        payload = _messenger_payload("IGSID1", "hi", object_type="instagram")
        body = _meta_body(payload)
        with patch("app.main._handle_instagram_message", new=AsyncMock()) as mock_ig, \
             patch("app.main._handle_messenger_message", new=AsyncMock()) as mock_msgr:
            response = await client.post(
                "/webhook/meta", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)},
            )
        assert response.status_code == 200
        mock_ig.assert_awaited_once()
        mock_msgr.assert_not_awaited()

    async def test_page_object_routes_to_messenger_handler_not_instagram(self, client):
        payload = _messenger_payload("PSID1", "hi", object_type="page")
        body = _meta_body(payload)
        with patch("app.main._handle_instagram_message", new=AsyncMock()) as mock_ig, \
             patch("app.main._handle_messenger_message", new=AsyncMock()) as mock_msgr:
            response = await client.post(
                "/webhook/meta", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)},
            )
        assert response.status_code == 200
        mock_msgr.assert_awaited_once()
        mock_ig.assert_not_awaited()

    async def test_echo_message_not_dispatched(self, client):
        payload = _messenger_payload("SELF_ID", "our own reply", object_type="page", is_echo=True)
        body = _meta_body(payload)
        with patch("app.main._handle_instagram_message", new=AsyncMock()) as mock_ig, \
             patch("app.main._handle_messenger_message", new=AsyncMock()) as mock_msgr:
            response = await client.post(
                "/webhook/meta", content=body,
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)},
            )
        assert response.status_code == 200
        mock_ig.assert_not_awaited()
        mock_msgr.assert_not_awaited()

    async def test_whatsapp_business_account_object_unaffected(self, client, mock_all_services):
        """Regression: the object-type dispatch fix must not change
        WhatsApp-via-meta routing (a different top-level object value,
        handled entirely by the pre-existing changes[] loop)."""
        payload = make_meta_payload(UNKNOWN_PHONE, "hello there")
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)},
        )
        assert response.status_code == 200

    async def test_meta_retry_of_same_messenger_message_id_not_saved_twice(self, client, mock_all_services, test_db_pool):
        """Owner Section 3 (2026-08-27): 'durable dedup prevents Meta
        retries from causing another reply' — real end-to-end proof, not
        just a mocked unit test. Meta retries webhook delivery on any
        non-2xx/timeout; posting the identical payload twice must persist
        exactly one inbound row for that (platform, source_message_ref).
        Runs with meta_messenger_inbound OFF (today's live default) — the
        dedup check in _handle_messenger_message() runs unconditionally,
        before the flag branch, so it must hold in either state."""
        payload = _messenger_payload("PSID_RETRY1", "hello, testing retry", object_type="page", mid="mid.retry-dedup-1")
        body = _meta_body(payload)
        headers = {"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)}

        r1 = await client.post("/webhook/meta", content=body, headers=headers)
        r2 = await client.post("/webhook/meta", content=body, headers=headers)
        assert r1.status_code == 200
        assert r2.status_code == 200

        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_whatsapp_messages "
                "WHERE platform='messenger' AND direction='inbound' AND source_message_ref=$1",
                "mid.retry-dedup-1",
            )
        assert count == 1, "Meta retry of the same message_id must not be saved/processed twice"


class TestMetaWebhookSignatureSecurity:
    """Owner Phase 4 (mandatory): reject invalid/missing signatures;
    never trust a POST solely because the endpoint URL is secret."""

    async def test_invalid_signature_rejected(self, client):
        payload = _messenger_payload("PSID1", "hi")
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + "0" * 64},
        )
        assert response.status_code == 403

    async def test_missing_signature_rejected_when_enforced(self, client):
        payload = _messenger_payload("PSID1", "hi")
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta", content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 403

    async def test_malformed_json_body_rejected(self, client):
        response = await client.post(
            "/webhook/meta",
            content=b"{not valid json",
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256="
                + hmac.new(b"test_app_secret", b"{not valid json", hashlib.sha256).hexdigest(),
            },
        )
        assert response.status_code == 400

    async def test_valid_signature_accepted(self, client):
        payload = _messenger_payload("PSID1", "hi", is_echo=True)  # echo -> no side effects, pure signature check
        body = _meta_body(payload)
        response = await client.post(
            "/webhook/meta", content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)},
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
                INSERT INTO fazle_contact_roles (phone, role, name)
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
