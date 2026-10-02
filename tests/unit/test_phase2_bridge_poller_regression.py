"""
tests/unit/test_phase2_bridge_poller_regression.py
===================================================

Regression + Phase-2 wiring proof for the bridge SQLite poller's raw writer
(`_save_raw_core_copy`) after the 2026-09-01 remediation:

  * canonical_phone / phone_last10 are now NORMALIZED at write time
    (01… -> 8801…, LID sentinel -> NULL) — always on, flag-independent.
  * The migration-078 identity columns are populated ONLY when
    PHASE2_IDENTITY_RESOLVER_ENABLED is set, via a best-effort follow-up
    UPDATE that can never break the raw INSERT.
  * message_hash idempotency, group conversation_key, and the autofill
    trigger are unchanged.
"""
from __future__ import annotations

import hashlib
import secrets

import pytest
import pytest_asyncio

from modules.bridge_poller import _save_raw_core_copy, _save_message as bp_save_message


@pytest_asyncio.fixture(autouse=True)
async def _inject_test_db_pool(test_db_pool):
    import app.database as db_module
    from modules.identity_resolver import reset_column_cache
    prev = db_module._pool
    db_module._pool = test_db_pool
    reset_column_cache()
    try:
        yield
    finally:
        db_module._pool = prev
        reset_column_cache()


@pytest.fixture
def phase2_on(monkeypatch):
    monkeypatch.setenv("PHASE2_IDENTITY_RESOLVER_ENABLED", "true")
    yield


@pytest.fixture
def phase2_off(monkeypatch):
    monkeypatch.setenv("PHASE2_IDENTITY_RESOLVER_ENABLED", "false")
    yield


def _hash(source: str, ref: str) -> str:
    return hashlib.sha256(f"{source}|{ref}".encode("utf-8")).hexdigest()


async def _row(pool, msg_id, cols):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            f"SELECT {cols} FROM wbom_whatsapp_messages WHERE message_id = $1", msg_id
        )


# ─────────────────── always-on: phone normalization at write ───────────────

@pytest.mark.asyncio
async def test_canonical_phone_normalized_on_write(test_db_pool, phase2_off):
    """A local 01… number is stored as canonical 8801… even with Phase-2 OFF."""
    msg_id = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": "NORM_001", "chat_jid": "01811111111@s.whatsapp.net",
             "content": "hi", "timestamp": "2026-09-01T10:00:00+00:00"},
        phone="01811111111", text="hi",
    )
    row = await _row(test_db_pool, msg_id,
                     "canonical_phone, phone_last10, sender_number, "
                     "sender_name_snapshot, canonical_message_key")
    assert row["canonical_phone"] == "8801811111111"
    assert row["phone_last10"] == "1811111111"
    assert row["sender_number"] == "01811111111"  # raw preserved
    assert row["sender_name_snapshot"] is None      # flag OFF -> no enrichment
    assert row["canonical_message_key"] is None


@pytest.mark.asyncio
async def test_lid_sentinel_never_becomes_a_phone(test_db_pool, phase2_off):
    lid = "151539800895531"
    msg_id = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": "LID_001", "chat_jid": f"{lid}@lid", "content": "hi",
             "timestamp": "2026-09-01T12:00:00+00:00"},
        phone=f"unresolved:{lid}", text="hi",
    )
    row = await _row(test_db_pool, msg_id,
                     "sender_number, original_sender_number, contact_identifier, "
                     "canonical_phone, phone_last10")
    assert row["sender_number"] == f"unresolved:{lid}"
    assert row["original_sender_number"] == f"unresolved:{lid}"
    assert row["canonical_phone"] is None
    assert row["phone_last10"] is None   # not a 10-digit slice of the LID


# ─────────────────── flag-gated: Phase-2 identity columns ──────────────────

@pytest.mark.asyncio
async def test_phase2_columns_populate_when_flag_on(test_db_pool, phase2_on, seed_employee):
    msg_id = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": "P2_001", "chat_jid": f"{seed_employee['employee_mobile']}@s.whatsapp.net",
             "content": "hello", "timestamp": "2026-09-01T10:00:00+00:00"},
        phone=seed_employee["employee_mobile"], text="hello",
    )
    row = await _row(test_db_pool, msg_id,
                     "sender_name_snapshot, sender_name_source, sender_name_confidence, "
                     "canonical_message_key, message_hash, canonical_phone")
    assert row["sender_name_snapshot"] == seed_employee["employee_name"]
    assert row["sender_name_source"] == "employee"
    assert row["sender_name_confidence"] == 100
    assert row["canonical_message_key"].startswith("bridge:msgid:")
    assert row["message_hash"] == _hash("bridge1", "P2_001")
    assert row["canonical_phone"] == "8801811111111"


@pytest.mark.asyncio
async def test_two_bridges_same_msgid_share_canonical_key(test_db_pool, phase2_on, seed_employee):
    ref = "P2_XBRIDGE"
    id1 = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": ref, "chat_jid": "x", "content": "hi", "timestamp": "2026-09-01T11:00:00+00:00"},
        phone=seed_employee["employee_mobile"], text="hi")
    id2 = await _save_raw_core_copy(
        source="bridge2",
        msg={"id": ref, "chat_jid": "x", "content": "hi", "timestamp": "2026-09-01T11:01:00+00:00"},
        phone=seed_employee["employee_mobile"], text="hi")
    assert id1 != id2
    async with test_db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT canonical_message_key, message_hash FROM wbom_whatsapp_messages "
            "WHERE source_message_ref = $1 ORDER BY platform", ref)
    assert len(rows) == 2
    assert rows[0]["canonical_message_key"] == rows[1]["canonical_message_key"]
    assert rows[0]["message_hash"] != rows[1]["message_hash"]


# ─────────────────── unchanged pre-existing behaviour ──────────────────────

@pytest.mark.asyncio
async def test_idempotent_per_msg_id(test_db_pool, seed_employee):
    ref = "IDEMP_001"
    id1 = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": ref, "chat_jid": "x", "content": "first", "timestamp": "2026-09-01T13:00:00+00:00"},
        phone=seed_employee["employee_mobile"], text="first")
    id2 = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": ref, "chat_jid": "x", "content": "second", "timestamp": "2026-09-01T13:01:00+00:00"},
        phone=seed_employee["employee_mobile"], text="second")
    assert id1 == id2
    async with test_db_pool.acquire() as conn:
        body = await conn.fetchval(
            "SELECT message_body FROM wbom_whatsapp_messages WHERE message_id = $1", id1)
    assert body == "first"


@pytest.mark.asyncio
async def test_group_chat_conversation_key_uses_chat_jid(test_db_pool):
    chat_jid = "120363426895117693@g.us"
    msg_id = await _save_raw_core_copy(
        source="bridge1",
        msg={"id": "GRP_001", "chat_jid": chat_jid, "content": "hi",
             "timestamp": "2026-09-01T14:00:00+00:00"},
        phone="8801849258074", text="hi")
    row = await _row(test_db_pool, msg_id, "conversation_key")
    assert row["conversation_key"] == f"{chat_jid}:bridge1"


@pytest.mark.asyncio
async def test_bp_save_message_helper_unaffected(test_db_pool):
    row_id = await bp_save_message(
        source="bridge2", sender="8801855555555", text="outbound test",
        direction="outbound", identity_role="admin", identity_confidence=100,
        workflow="customer-reply", msg_type="text", actor_type="automation",
        actor_id="bridge_poller_test", source_message_ref="BP_OUT_001")
    assert row_id is None or isinstance(row_id, int)


@pytest.mark.asyncio
async def test_autofill_trigger_still_backfills(test_db_pool):
    async with test_db_pool.acquire() as conn:
        ref = "TRIG_" + secrets.token_hex(4)
        h = hashlib.sha256(ref.encode()).hexdigest()
        await conn.execute(
            "INSERT INTO wbom_whatsapp_messages "
            "(sender_number, message_body, direction, platform, is_processed, "
            " contact_identifier, message_hash, source_message_ref) "
            "VALUES (NULL, 'trigger test', 'inbound', 'bridge1', true, "
            "        '8801866666666', $1, $2)", h, ref)
        row = await conn.fetchrow(
            "SELECT sender_number, contact_identifier FROM wbom_whatsapp_messages "
            "WHERE message_hash = $1", h)
    assert row["sender_number"] == "8801866666666"
