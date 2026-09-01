"""
tests/integration/test_phase2_read_tools.py
===========================================

The additive read-only search surface added 2026-09-01:
  * GET /api/whatsapp/messages/query        — new `phone` + `message_body_contains` filters
  * GET /api/identity/search-by-name        — name -> candidate phone(s), never guesses
  * GET /api/whatsapp/contact/summary       — one bounded "everything about this number" call

All read-only; exercised through the real FastAPI app.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_search_by_name_single_match_resolves(client, test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) "
            "VALUES ('8801733330001', 'Solo Uniqueperson', 'whatsapp', true)")
    r = await client.get("/api/identity/search-by-name", params={"name": "Solo Uniqueperson"})
    assert r.status_code == 200
    body = r.json()
    assert body["resolved"] == "8801733330001"
    assert body["ambiguous"] is False
    assert body["candidate_count"] == 1


@pytest.mark.asyncio
async def test_search_by_name_multiple_is_ambiguous(client, test_db_pool):
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO wbom_contacts (whatsapp_number, display_name, platform, is_active) VALUES "
            "('8801733330002', 'Kamal Hossain', 'whatsapp', true),"
            "('8801733330003', 'Kamal Uddin', 'whatsapp', true)")
    r = await client.get("/api/identity/search-by-name", params={"name": "Kamal"})
    body = r.json()
    assert body["resolved"] is None
    assert body["ambiguous"] is True
    assert body["candidate_count"] >= 2


@pytest.mark.asyncio
async def test_search_by_name_no_match(client):
    r = await client.get("/api/identity/search-by-name", params={"name": "Nobody Named This"})
    body = r.json()
    assert body["candidate_count"] == 0
    assert body["resolved"] is None
    assert body["ambiguous"] is False


@pytest.mark.asyncio
async def test_messages_query_phone_filter(client, test_db_pool):
    async with test_db_pool.acquire() as conn:
        for i, (canon, body) in enumerate([
            ("8801799990001", "hello from target"),
            ("8801799990001", "second from target"),
            ("8801799990009", "unrelated message"),
        ]):
            await conn.execute(
                "INSERT INTO wbom_whatsapp_messages "
                "(sender_number, canonical_phone, message_body, direction, platform, message_hash) "
                "VALUES ($1, $1, $2, 'inbound', 'bridge1', $3)",
                canon, body, f"qtest-{i}")
    r = await client.get("/api/whatsapp/messages/query",
                         params={"phone": "01799990001", "include_body": "true"})
    body = r.json()
    assert body["count"] == 2
    assert all(m["canonical_phone"] == "8801799990001" for m in body["messages"])


@pytest.mark.asyncio
async def test_messages_query_body_contains_filter(client, test_db_pool):
    async with test_db_pool.acquire() as conn:
        for i, txt in enumerate(["salary advance please", "attendance marked", "salary query again"]):
            await conn.execute(
                "INSERT INTO wbom_whatsapp_messages "
                "(sender_number, canonical_phone, message_body, direction, platform, message_hash) "
                "VALUES ($1, $1, $2, 'inbound', 'bridge2', $3)",
                "8801700001234", txt, f"bctest-{i}")
    r = await client.get("/api/whatsapp/messages/query",
                         params={"message_body_contains": "salary", "include_body": "true"})
    body = r.json()
    assert body["count"] == 2
    assert all("salary" in m["message_body"].lower() for m in body["messages"])


@pytest.mark.asyncio
async def test_contact_summary_aggregates_platforms(client, test_db_pool):
    async with test_db_pool.acquire() as conn:
        rows = [
            ("bridge1", "inbound", "hi b1"),
            ("bridge1", "outbound", "reply b1"),
            ("meta", "inbound", "hi meta"),
        ]
        for i, (plat, direction, txt) in enumerate(rows):
            await conn.execute(
                "INSERT INTO wbom_whatsapp_messages "
                "(sender_number, canonical_phone, message_body, direction, platform, message_hash) "
                "VALUES ($1, $1, $2, $3, $4, $5)",
                "8801766660001", txt, direction, plat, f"cs-{i}")
    r = await client.get("/api/whatsapp/contact/summary", params={"phone": "8801766660001"})
    assert r.status_code == 200
    body = r.json()
    assert set(body["platforms"]) == {"bridge1", "meta"}
    assert body["first_seen"] is not None and body["last_seen"] is not None
    assert len(body["recent_messages"]) == 3
    # provenance preserved on every row
    assert all("platform" in m and "direction" in m for m in body["recent_messages"])
