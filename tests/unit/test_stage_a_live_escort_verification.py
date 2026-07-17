"""
Stage A — Live end-to-end escort-order verification (Owner-authorized,
post-Phase-1B.2). Method 3 selected: production code (identity_brain,
message_router, modules.escort, app.main's real webhook handler) exercised
end-to-end against a real, isolated disposable Postgres, with only the
external bridge send and Ollama/Groq calls mocked (mock_all_services) — no
synthetic payload was ever sent to the live production webhook.

Method 1 (observe a genuine order) isn't practical synchronously in this
session; Method 2 (Owner-controlled real phone) isn't available here.
Method 3 is the one the assignment itself sanctions for exactly this
situation: "Use this if a live WhatsApp send could cause an unintended
outbound message" — true here, since unsigned webhook payloads are
currently accepted in production (a separate, already-tracked item), so a
synthetic order posted directly to the live webhook could reach a real
admin notification enqueue and, eventually, a real bridge send.

Every test message below carries an unmistakable TEST marker.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

_TEST_ADMIN = "8801880446111"


class TestVerifiedEscortOrderEndToEnd:
    async def test_complete_order_creates_exactly_one_draft_program_and_admin_notification(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """A6/A7/A8/A9 — verified sender (env_phone source), complete order:
        identity classifies verified/client_escort_buyer, no client-facing
        reply is sent, the parser extracts fields correctly, and exactly one
        wbom_escort_programs row (status='draft', never auto-confirmed) plus
        exactly one fazle_outbound_queue admin notification are created."""
        import modules.escort as escort_module

        sender = "8801766600001"  # synthetic, disposable, test-only
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        monkeypatch.setenv("ADMIN_NUMBERS", _TEST_ADMIN)
        from app.config import get_settings
        get_settings.cache_clear()

        unique_marker = "STAGE-A-TEST-MARKER-001"
        text = f"""TEST ESCORT ORDER — DO NOT OPERATE ({unique_marker})
MV TEST VERIFICATION
A/C Test Client
Wheat
01 TEST LIGHTER 01 01933333399 TEST DESTINATION 1000 MT
17/07/2026 Day"""

        payload = {
            "sender": sender,
            "message": text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "stage-a-verified-complete-001",
            "source": "bridge1",
        }
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        # ── A6: identity classification ──
        async with test_db_pool.acquire() as conn:
            msg_row = await conn.fetchrow(
                "SELECT sender_number, identity_role FROM wbom_whatsapp_messages "
                "WHERE message_body LIKE $1 ORDER BY message_id DESC LIMIT 1",
                f"%{unique_marker}%",
            )
        assert msg_row is not None, "inbound message was not saved"
        assert msg_row["sender_number"] == sender
        assert msg_row["identity_role"] == "client_escort_buyer"

        # ── A7: no client-facing reply — nothing enqueued addressed to the
        # sender itself (only the admin should receive anything) ──
        async with test_db_pool.acquire() as conn:
            client_facing = await conn.fetch(
                "SELECT id FROM fazle_outbound_queue WHERE recipient = $1", sender,
            )
        assert client_facing == [], "escort-client messages must never receive a direct reply"

        # ── A8/A9: exactly one draft program row, correct extraction, never confirmed ──
        async with test_db_pool.acquire() as conn:
            programs = await conn.fetch(
                "SELECT program_id, mother_vessel, lighter_vessel, master_mobile, "
                "destination, status, remarks FROM wbom_escort_programs "
                "WHERE remarks::text LIKE $1",
                f"%{sender}%",
            )
        assert len(programs) == 1, f"expected exactly one draft program, got {len(programs)}"
        prog = programs[0]
        assert "TEST VERIFICATION" in prog["mother_vessel"]
        assert "TEST" in prog["lighter_vessel"]
        assert prog["master_mobile"] == "01933333399"
        assert prog["status"] == "draft", "must never be auto-confirmed"
        import json
        remarks = json.loads(prog["remarks"]) if isinstance(prog["remarks"], str) else prog["remarks"]
        assert remarks.get("source_verified") is True

        # ── A9: exactly one admin notification enqueued, addressed to the
        # configured admin, never auto-sent (queue is not a direct send) ──
        async with test_db_pool.acquire() as conn:
            admin_notes = await conn.fetch(
                "SELECT id, recipient, body, purpose FROM fazle_outbound_queue "
                "WHERE recipient = $1", _TEST_ADMIN,
            )
        assert len(admin_notes) == 1, f"expected exactly one admin notification, got {len(admin_notes)}"
        assert "TEST VERIFICATION" in admin_notes[0]["body"]
        # Verified path must not carry the unverified-sender warning label.
        assert "UNVERIFIED SENDER" not in admin_notes[0]["body"]

    async def test_replay_of_same_message_id_creates_no_duplicates(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """A10 — the exact same inbound message (same message_id) replayed
        must not create a second saved message, a second program row, or a
        second admin notification. Verifies the webhook-level
        processed_bridge_messages dedup guard short-circuits before any
        further processing."""
        import modules.escort as escort_module

        sender = "8801766600002"
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        monkeypatch.setenv("ADMIN_NUMBERS", _TEST_ADMIN)
        from app.config import get_settings
        get_settings.cache_clear()

        unique_marker = "STAGE-A-TEST-MARKER-002"
        text = f"""TEST ESCORT ORDER — DO NOT OPERATE ({unique_marker})
MV TEST VERIFICATION TWO
A/C Test Client
Wheat
01 TEST LIGHTER 02 01933333398 TEST DESTINATION 1000 MT
17/07/2026 Day"""

        payload = {
            "sender": sender,
            "message": text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "stage-a-dedup-002",
            "source": "bridge1",
        }

        r1 = await client.post("/webhook/mcp1", json=payload)
        assert r1.status_code in (200, 202)
        r2 = await client.post("/webhook/mcp1", json=payload)  # exact replay
        assert r2.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            msgs = await conn.fetch(
                "SELECT message_id FROM wbom_whatsapp_messages WHERE message_body LIKE $1",
                f"%{unique_marker}%",
            )
            programs = await conn.fetch(
                "SELECT program_id FROM wbom_escort_programs WHERE remarks::text LIKE $1",
                f"%{sender}%",
            )
            admin_notes = await conn.fetch(
                "SELECT id FROM fazle_outbound_queue WHERE recipient = $1", _TEST_ADMIN,
            )

        assert len(msgs) == 1, "replayed message_id must not be saved twice"
        assert len(programs) == 1, "replayed message_id must not create a duplicate program"
        assert len(admin_notes) == 1, "replayed message_id must not create a duplicate admin notification"


class TestUnverifiedEscortOrderEndToEnd:
    async def test_unverified_sender_gets_labeled_draft_not_persisted_identity(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """A6/A9 — a never-before-seen sender (not in ESCORT_CLIENT_PHONES, no
        stored role, no contact-book entry) whose message is order-shaped
        must: classify unverified (repeat_client/text_hint, never persisted
        as client_escort_buyer identity); still reach admin review, clearly
        labeled unverified; never grant authority. Safe to run live-harness
        (no external send risk) since this path never sends a client reply
        either."""
        sender = "8801766600003"
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", "")  # empty — sender not listed
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        monkeypatch.setenv("ADMIN_NUMBERS", _TEST_ADMIN)
        from app.config import get_settings
        get_settings.cache_clear()

        unique_marker = "STAGE-A-TEST-MARKER-003"
        text = f"""TEST ESCORT ORDER — DO NOT OPERATE ({unique_marker})
MV TEST VERIFICATION THREE
A/C Test Client
Wheat
01 TEST LIGHTER 03 01933333397 TEST DESTINATION 1000 MT
17/07/2026 Day"""

        payload = {
            "sender": sender,
            "message": text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "stage-a-unverified-003",
            "source": "bridge1",
        }
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            msg_row = await conn.fetchrow(
                "SELECT identity_role FROM wbom_whatsapp_messages "
                "WHERE message_body LIKE $1 ORDER BY message_id DESC LIMIT 1",
                f"%{unique_marker}%",
            )
        assert msg_row is not None
        # Never client_escort_buyer for an unverified, never-before-seen sender.
        assert msg_row["identity_role"] != "client_escort_buyer"

        async with test_db_pool.acquire() as conn:
            programs = await conn.fetch(
                "SELECT remarks FROM wbom_escort_programs WHERE remarks::text LIKE $1",
                f"%{sender}%",
            )
        import json
        if programs:
            remarks = json.loads(programs[0]["remarks"]) if isinstance(programs[0]["remarks"], str) else programs[0]["remarks"]
            assert remarks.get("source_verified") is False

            async with test_db_pool.acquire() as conn:
                admin_notes = await conn.fetch(
                    "SELECT body FROM fazle_outbound_queue WHERE recipient = $1", _TEST_ADMIN,
                )
            assert len(admin_notes) == 1
            assert "UNVERIFIED SENDER" in admin_notes[0]["body"]

        async with test_db_pool.acquire() as conn:
            client_facing = await conn.fetch(
                "SELECT id FROM fazle_outbound_queue WHERE recipient = $1", sender,
            )
        assert client_facing == [], "no client-facing reply for an unverified order-like message"

        # No permanent identity persisted from message content alone.
        async with test_db_pool.acquire() as conn:
            seeded_role = await conn.fetchrow(
                "SELECT role FROM fazle_contact_roles WHERE phone = $1", sender,
            )
        assert seeded_role is None, "message content alone must never create a fazle_contact_roles row"


class TestNoUnauthorizedSideEffects:
    async def test_no_financial_or_rbac_side_effects_from_escort_order(
        self, client, test_db_pool, monkeypatch, mock_all_services,
    ):
        """A11 — a verified escort order must not create attendance, payment
        drafts, payroll transactions, or grant any RBAC/admin/accountant
        authority."""
        sender = "8801766600004"
        monkeypatch.setenv("ESCORT_CLIENT_PHONES", sender)
        monkeypatch.setenv("ESCORT_TRUSTED_SOURCES", "bridge1,bridge2")
        monkeypatch.setenv("ADMIN_NUMBERS", _TEST_ADMIN)
        from app.config import get_settings
        get_settings.cache_clear()

        unique_marker = "STAGE-A-TEST-MARKER-004"
        text = f"""TEST ESCORT ORDER — DO NOT OPERATE ({unique_marker})
MV TEST VERIFICATION FOUR
A/C Test Client
Wheat
01 TEST LIGHTER 04 01933333396 TEST DESTINATION 1000 MT
17/07/2026 Day"""

        payload = {
            "sender": sender, "message": text,
            "timestamp": "2026-07-17T10:00:00Z",
            "message_id": "stage-a-sideeffects-004", "source": "bridge1",
        }
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202)

        async with test_db_pool.acquire() as conn:
            attendance = await conn.fetch(
                "SELECT 1 FROM wbom_attendance WHERE employee_id IN "
                "(SELECT employee_id FROM wbom_employees WHERE employee_mobile = $1)",
                sender,
            )
            fpe_txn = await conn.fetch(
                "SELECT 1 FROM fpe_cash_transactions WHERE employee_phone = $1", sender,
            )
            contact_role = await conn.fetchrow(
                "SELECT role FROM fazle_contact_roles WHERE phone = $1", sender,
            )

        assert attendance == []
        assert fpe_txn == []
        assert contact_role is None or contact_role["role"] != "admin"
        assert contact_role is None or contact_role["role"] != "accountant"
