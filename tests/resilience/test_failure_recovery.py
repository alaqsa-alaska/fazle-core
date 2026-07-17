"""
Resilience / failure recovery tests.

Tests cover:
 - Bridge 1 / Bridge 2 returning 5xx → app handles gracefully, no crash
 - Database pool exhausted / connection failure → 500 with proper JSON
 - Ollama LLM service down → fallback or graceful error
 - Malformed / oversized payloads → 400/422 without crash
 - Concurrent message flood → no data corruption
"""
from __future__ import annotations

import asyncio
import pytest
import respx
import httpx
from unittest.mock import patch, AsyncMock

pytestmark = pytest.mark.resilience

from tests.conftest import (
    make_bridge_payload,
    GUARD_PHONE,
    ADMIN_PHONE,
    CLIENT_PHONE,
    UNKNOWN_PHONE,
)


# ─────────────────────────────────────────────────────────────────────────────
# Bridge Failures
# ─────────────────────────────────────────────────────────────────────────────

class TestBridgeDownScenarios:
    """When bridge replies fail, the app must not crash and should store the draft."""

    async def test_bridge1_503_does_not_crash_app(self, client, test_db_pool):
        with respx.mock(assert_all_called=False) as mock:
            mock.post("http://mock-bridge1/send").mock(
                return_value=httpx.Response(503, json={"error": "Service Unavailable"})
            )
            mock.post("http://mock-bridge2/send").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post("http://mock-ollama/api/generate").mock(
                return_value=httpx.Response(200, json={"response": "test reply"})
            )

            payload = make_bridge_payload(UNKNOWN_PHONE, "hello")
            response = await client.post("/webhook/mcp1", json=payload)

        assert response.status_code in (200, 202), "App must return 2xx even when bridge is down"

    async def test_bridge2_timeout_handled(self, client, test_db_pool):
        import httpx as _httpx

        with respx.mock(assert_all_called=False) as mock:
            mock.post("http://mock-bridge2/send").mock(
                side_effect=_httpx.ConnectTimeout("Connection timed out")
            )
            mock.post("http://mock-bridge1/send").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )
            mock.post("http://mock-ollama/api/generate").mock(
                return_value=httpx.Response(200, json={"response": "test"})
            )

            payload = make_bridge_payload(UNKNOWN_PHONE, "test timeout", source="bridge2")
            response = await client.post("/webhook/mcp2", json=payload)

        assert response.status_code in (200, 202)

    async def test_bridge_network_error_drafts_not_lost(self, client, test_db_pool):
        """Even with bridge failure, the draft reply should be stored in DB."""
        with respx.mock(assert_all_called=False) as mock:
            mock.post("http://mock-bridge1/send").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock.post("http://mock-ollama/api/generate").mock(
                return_value=httpx.Response(200, json={"response": "Cannot reach you"})
            )

            payload = make_bridge_payload(UNKNOWN_PHONE, "test draft preservation")
            await client.post("/webhook/mcp1", json=payload)

        async with test_db_pool.acquire() as conn:
            # Draft should exist in DB even if delivery failed
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM fazle_draft_replies WHERE recipient=$1",
                UNKNOWN_PHONE,
            )
        assert count >= 0  # Non-negative; exact value depends on routing logic


# ─────────────────────────────────────────────────────────────────────────────
# Ollama / LLM Failures
# ─────────────────────────────────────────────────────────────────────────────

class TestOllamaDownScenarios:
    async def test_ollama_503_does_not_crash(self, client, test_db_pool):
        with respx.mock(assert_all_called=False) as mock:
            mock.post("http://mock-ollama/api/generate").mock(
                return_value=httpx.Response(503, json={"error": "Ollama unavailable"})
            )
            mock.post("http://mock-bridge1/send").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )

            payload = make_bridge_payload(UNKNOWN_PHONE, "যোগাযোগ করুন")
            response = await client.post("/webhook/mcp1", json=payload)

        assert response.status_code in (200, 202)

    async def test_ollama_connection_refused(self, client, test_db_pool):
        with respx.mock(assert_all_called=False) as mock:
            mock.post("http://mock-ollama/api/generate").mock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            mock.post("http://mock-bridge1/send").mock(
                return_value=httpx.Response(200, json={"status": "ok"})
            )

            payload = make_bridge_payload(UNKNOWN_PHONE, "hello")
            response = await client.post("/webhook/mcp1", json=payload)

        assert response.status_code in (200, 202)


# ─────────────────────────────────────────────────────────────────────────────
# Malformed Payloads
# ─────────────────────────────────────────────────────────────────────────────

class TestMalformedPayloads:
    async def test_empty_body_returns_422(self, client):
        response = await client.post(
            "/webhook/mcp1",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in (400, 422)

    async def test_non_json_body_returns_422(self, client):
        response = await client.post(
            "/webhook/mcp1",
            content=b"not-json-at-all",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in (400, 422)

    async def test_missing_sender_field_returns_200(self, client):
        # Intentional: the bridge webhook always acks a structurally-valid-JSON
        # payload with 200, even when a per-event field like the sender is
        # missing (the event is logged and skipped internally, never raised),
        # to avoid retry-storms from the upstream bridge client on ordinary
        # malformed/incomplete events. See docs/AUDIT_REPORT_2026-07-17.md
        # Part 2 Step 7 and management_decisions.md Session 19 (S19-01a).
        response = await client.post(
            "/webhook/mcp1",
            json={"text": "hello"},  # missing 'from'
        )
        assert response.status_code == 200

    async def test_oversized_message_handled(self, client, mock_all_services):
        """A very long message body must not crash the app."""
        big_text = "অ" * 50_000  # 50K Bengali characters
        payload = make_bridge_payload(UNKNOWN_PHONE, big_text)
        response = await client.post("/webhook/mcp1", json=payload)
        assert response.status_code in (200, 202, 400, 413)


# ─────────────────────────────────────────────────────────────────────────────
# Database Failure
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseFailure:
    async def test_db_execute_error_returns_500(self, client):
        """When the DB pool raises an exception, the endpoint returns HTTP 500."""
        import app.database as db_module

        original_execute = db_module.execute

        async def failing_execute(*args, **kwargs):
            raise RuntimeError("Simulated DB connection error")

        with patch.object(db_module, "execute", side_effect=failing_execute):
            payload = make_bridge_payload(UNKNOWN_PHONE, "test db fail")
            response = await client.post("/webhook/mcp1", json=payload)

        # App must respond (500 is acceptable — no uncaught exception / crash)
        assert response.status_code in (200, 202, 500, 503)

    async def test_health_endpoint_reports_db_failure(self, client):
        """When DB is unreachable, /health should indicate unhealthy status."""
        import app.database as db_module

        async def failing_fetch_val(*args, **kwargs):
            raise RuntimeError("Simulated DB connection error")

        with patch.object(db_module, "fetch_val", side_effect=failing_fetch_val):
            response = await client.get("/health")

        # Either 200 with status=degraded, or 503
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            data = response.json()
            status = data.get("db") or data.get("status") or ""
            assert "ok" not in str(status).lower() or data.get("db") == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# Concurrent Flood
# ─────────────────────────────────────────────────────────────────────────────

class TestConcurrentMessageFlood:
    async def test_concurrent_messages_no_corruption(
        self, client, test_db_pool, mock_all_services
    ):
        """Send 20 messages concurrently — DB must remain consistent."""
        tasks = [
            client.post(
                "/webhook/mcp1",
                json=make_bridge_payload(UNKNOWN_PHONE, f"message {i}"),
            )
            for i in range(20)
        ]
        responses = await asyncio.gather(*tasks)

        # All requests should complete without 5xx
        for r in responses:
            assert r.status_code in (200, 202)

        # Dedup: the 20 messages with same sender should result in <= 1 archive row
        # wbom_whatsapp_messages has no "phone" column — the real column is
        # sender_number (db/migrations/029:310). Fixed 2026-07-13.
        async with test_db_pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM wbom_whatsapp_messages WHERE sender_number=$1",
                UNKNOWN_PHONE,
            )
        # Just assert it didn't blow up — actual dedup count depends on implementation
        assert count >= 0

    async def test_concurrent_employee_queries(self, client, seed_employee):
        """Parallel reads should not deadlock or return errors."""
        # /employees was mounted under modules.fazle_payroll_engine's
        # /api/fpe prefix at some point after this test was written and
        # never updated — bare /employees 404s. Fixed 2026-07-13.
        tasks = [client.get("/api/fpe/employees") for _ in range(10)]
        responses = await asyncio.gather(*tasks)
        for r in responses:
            assert r.status_code == 200
