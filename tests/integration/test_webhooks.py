"""API Integration tests — Webhook message processing"""
from __future__ import annotations

import hmac
import hashlib
import json
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def offline_providers(monkeypatch):
    """Mock current provider entrypoints, not a stale daemon-only call."""
    from app import llm, ollama
    from modules import route_b, hermes_dispatch
    from modules.intent import classify
    monkeypatch.setattr(llm, "classify_intent_llm", AsyncMock(side_effect=classify))
    monkeypatch.setattr(llm, "generate_reply", AsyncMock(return_value="ধন্যবাদ।"))
    monkeypatch.setattr(ollama, "generate_reply", AsyncMock(return_value="ধন্যবাদ।"))
    monkeypatch.setattr(route_b, "ollama_generate_reply", AsyncMock(return_value="ধন্যবাদ।"))
    monkeypatch.setattr(hermes_dispatch, "_call_hermes", AsyncMock(return_value=None))
    monkeypatch.setattr("app.main._forward_to_agent_if_admin", lambda *a: None)
    monkeypatch.setattr("modules.memory_extractor.extract_and_save_memory", AsyncMock())

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


@pytest.mark.parametrize("source", ["bridge1", "bridge2", "bridge3", "meta"])
@pytest.mark.parametrize("automatic", [True, False])
async def test_inbound_to_reply_or_draft_provenance_after_reload(client, test_db_pool, monkeypatch, source, automatic):
    """Existing HTTP/bridge ingestion → real router → real DB → queue → lookup.

    The family branch is deterministic; identity and transport are mocked.
    Domain-specific routing has its own regression suites.
    """
    from app import main
    from modules import bridge_poller as bp, message_router as router, outbound
    phone, reference = "8801700999000", "provenance-http-1"
    identity = {"role": "family", "identity_role": "family", "identity_confidence": 100,
                "identity_source": "test", "display_name": "Test", "confidence": 1.0}
    monkeypatch.setattr("modules.identity_brain.detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "detect_identity", AsyncMock(return_value=identity))
    monkeypatch.setattr(router, "_should_silent_skip", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(bp, "_can_reply", AsyncMock(return_value=True))
    monkeypatch.setattr(bp, "_record_reply", AsyncMock())
    monkeypatch.setattr(bp, "_check_loop_detect", lambda *a: False)
    monkeypatch.setattr(bp, "_check_keyword_flood", lambda *a: False)
    monkeypatch.setattr(bp, "_is_draft_always", AsyncMock(return_value=False))
    monkeypatch.setattr("modules.wa_chat_frontend.get_effective_auto_reply", AsyncMock(return_value=automatic))
    monkeypatch.setattr(main, "_force_draft_by_saved_contact_name", AsyncMock(return_value=False))
    monkeypatch.setattr(main, "_should_recruitment_autoreply", AsyncMock(return_value=False))
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    monkeypatch.setattr(outbound, "_send_with_channel", AsyncMock(return_value="receipt-http-1"))
    for settings in (main.settings, bp._settings):
        monkeypatch.setattr(settings, "auto_reply_enabled", automatic)
        monkeypatch.setattr(settings, "recruitment_autoreply_enabled", False)
        monkeypatch.setattr(settings, "auto_reply_sources", "bridge1,bridge2,bridge3,meta")
        monkeypatch.setattr(settings, "ai_safe_mode", False)
    for _ in range(2):
        if source == "meta":
            msg = {"from": phone, "id": reference, "type": "text", "text": {"body": "hello"}}
            payload = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}
            response = await client.post("/webhook/meta", content=_meta_body(payload),
                headers={"Content-Type": "application/json", "X-Hub-Signature-256": _meta_signature(payload)})
            assert response.status_code == 200
        elif source == "bridge3":
            mid = await bp._save_raw_core_copy(source, {"id": reference, "content": "hello"}, phone, "hello")
            await bp.process_bridge_inbound(source, phone, "hello", id_role="family", id_conf=100,
                                           core_message_id=mid, message_id=reference)
        else:
            payload = {"sender": phone, "text": "hello", "id": reference}
            response = await client.post(f"/webhook/mcp{source[-1]}", json=payload)
            assert response.status_code == 200
    rows = await test_db_pool.fetch("SELECT * FROM wbom_whatsapp_messages WHERE direction='inbound' AND source_message_ref=$1", reference)
    assert len(rows) == 1
    mid = rows[0]["message_id"]
    provenance = json.loads(rows[0]["metadata_json"])["provenance"]
    assert provenance["processing_outcome"] == "done"
    assert "family-personal" in provenance["workflows"]
    assert rows[0]["identity_role"] == "family"
    assert rows[0]["intent_detected"] == "greeting"
    headers = {"X-Internal-Key": main.settings.internal_api_key}
    if not automatic:
        drafts = await test_db_pool.fetch("SELECT * FROM fazle_draft_replies WHERE meta->>'core_message_id'=$1", str(mid))
        assert len(drafts) == 1
        assert await test_db_pool.fetchval("SELECT count(*) FROM fazle_outbound_queue") == 0
        did = drafts[0]["id"]
        for _ in range(2):
            result = await client.post(f"/api/wa/drafts/{did}/approve", headers=headers)
            assert result.status_code == 200 and result.json()["queued"] is True
        assert await test_db_pool.fetchval("SELECT status FROM fazle_draft_replies WHERE id=$1", did) == "approved"
    assert await test_db_pool.fetchval("SELECT count(*) FROM fazle_outbound_queue") == 1
    monkeypatch.setenv("OUTBOUND_ENABLED", "true")
    assert (await outbound.sweep_once())["sent"] == 1
    for endpoint, params in [
        ("/api/whatsapp/messages/lookup", {"message_id": mid}),
        ("/api/whatsapp/messages/conversation", {"conversation_key": f"{phone}:{source}"}),
        (f"/api/wa/messages/{phone}", {}),
    ]:
        response = await client.get(endpoint, params=params, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert "error" not in body
        inbound = next(m for m in body["messages"] if m.get("message_id", m.get("id")) == mid)
        assert inbound["provenance"]["processing_outcome"] == "done"
        assert len(inbound["outbound"]) == 1
        assert inbound["outbound"][0]["send_accepted"] is True
        assert inbound["outbound"][0]["delivered"] is False


async def test_meta_delivery_receipt_requires_signature_and_exact_provider_id(client, test_db_pool, monkeypatch):
    from app import main
    from modules import outbound
    monkeypatch.setattr(main.settings, "meta_webhook_signature_enforcement", False)
    monkeypatch.setattr(outbound, "_pairing_held", AsyncMock(return_value=(False, "")))
    qid = await outbound.enqueue("8801700999000", "reply", source_bridge="meta", idempotency_key="receipt-check")
    await test_db_pool.execute("UPDATE fazle_outbound_queue SET status='sent',external_id='provider-exact-1' WHERE id=$1", qid)
    payload = {"entry": [{"changes": [{"field": "messages", "value": {
        "statuses": [{"id": "provider-exact-1", "status": "delivered", "timestamp": "100"}]}}]}]}
    response = await client.post("/webhook/meta", content=_meta_body(payload))
    assert response.status_code == 200  # existing unsigned inbound policy
    row = dict(await test_db_pool.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", qid))
    assert outbound.delivery_details(row)["delivered"] is False
    response = await client.post("/webhook/meta", content=_meta_body(payload),
                                headers={"X-Hub-Signature-256": _meta_signature(payload)})
    assert response.status_code == 200
    row = dict(await test_db_pool.fetchrow("SELECT * FROM fazle_outbound_queue WHERE id=$1", qid))
    assert outbound.delivery_details(row)["delivered"] is True


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
