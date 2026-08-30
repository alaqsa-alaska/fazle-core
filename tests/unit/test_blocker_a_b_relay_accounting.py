"""
Owner Accounting Blocker A + B (2026-08-30) — payment-relay accounting.

A — transport identity: body sha256 is NOT financial identity. Direct
    idempotency stays payment-relay-send:<queue_id>:<line_idx>. The FPE-worker
    echo suppression correlates to a real relay SEND EVENT (queue row + time +
    booked txns), not a bare body hash.

B — atomicity: employee resolve/create + cash txn + audit + ledger + activation
    for one payment line commit together or not at all. A rollback removes a
    just-created employee row.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from unittest.mock import AsyncMock

pytestmark = pytest.mark.unit

RELAY_PURPOSE = "owner-accountant-payment-relay"


@pytest.fixture(autouse=True)
def _enable_relay_accounting(monkeypatch):
    monkeypatch.setenv("PAYMENT_RELAY_ACCOUNTING_ENABLED", "true")


def _queue_row(qid, body, meta=None):
    return {
        "id": qid, "recipient": "8801844836824", "body": body,
        "source_bridge": "bridge2", "purpose": RELAY_PURPOSE,
        "meta_json": json.dumps(meta or {"inbound_bridge": "bridge2",
                                         "inbound_message_id": f"WAMID{qid}",
                                         "source_number": "8801817201596"}),
        "idempotency_key": f"payment-relay:bridge2:WAMID{qid}",
        "external_id": "bridge2:8801844836824",
    }


async def _count_txns(conn, prefix):
    return await conn.fetchval(
        "SELECT count(*) FROM fpe_cash_transactions WHERE source_message_id LIKE $1",
        prefix + "%",
    )


async def _insert_sent_queue_row(conn, qid, body, sent_at):
    await conn.execute(
        """INSERT INTO fazle_outbound_queue
               (id, recipient, body, source_bridge, purpose, status, sent_at,
                meta_json, external_id, idempotency_key)
           VALUES ($1,'8801844836824',$2,'bridge2',$3,'sent',$4,'{}'::jsonb,
                   'bridge2:8801844836824',$5)""",
        qid, body, RELAY_PURPOSE, sent_at, f"payment-relay:bridge2:WAMID{qid}",
    )


# ══ BLOCKER A ═══════════════════════════════════════════════════════════════

async def test_A_replay_same_relay_event_one_txn(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    body = "ID: 01710000201 Karim 01710000201(N) 300/-"
    qr = _queue_row(93101, body)

    r1 = await on_relay_send_success(qr)
    r2 = await on_relay_send_success(qr)          # exact replay of the same queue event
    r3 = await on_relay_send_success(dict(qr))    # and again

    assert r1["booked"] == 1
    assert r2["ok"] and r2["booked"] == 0        # nothing new
    assert r3["ok"] and r3["booked"] == 0
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:93101:") == 1


async def test_A_new_relay_event_identical_body_two_txns(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    body = "ID: 01710000202 Rahim 01710000202(N) 300/-"   # byte-identical text

    r1 = await on_relay_send_success(_queue_row(93201, body))
    r2 = await on_relay_send_success(_queue_row(93202, body))   # DIFFERENT queue id

    assert r1["booked"] == 1 and r2["booked"] == 1
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:93201:") == 1
        assert await _count_txns(conn, "payment-relay-send:93202:") == 1
        total = await conn.fetchval(
            "SELECT count(*) FROM fpe_cash_transactions "
            "WHERE source_channel=$1 AND source_message_id LIKE 'payment-relay-send:932%'",
            RELAY_PURPOSE,
        )
        assert total == 2                                       # two genuine payments


async def test_A_echo_selfecho_correlated_is_suppressed(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000203 Jsim 01710000203(B) 450/-"
    now = datetime.now(timezone.utc)

    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93301, body, now)
    await on_relay_send_success(_queue_row(93301, body))        # books the relay send

    async with test_db_pool.acquire() as conn:
        # self-echo of that same send, WA timestamp seconds later
        assert await is_relay_payment_already_accounted(
            body, conn=conn, message_ts=now + timedelta(seconds=8)) is True


async def test_A_manual_identical_body_not_suppressed(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000204 Sohel 01710000204(N) 500/-"
    now = datetime.now(timezone.utc)

    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93401, body, now)
    await on_relay_send_success(_queue_row(93401, body))

    async with test_db_pool.acquire() as conn:
        # (a) identical text, but 2 hours later — a genuine separate payment
        assert await is_relay_payment_already_accounted(
            body, conn=conn, message_ts=now + timedelta(hours=2)) is False
        # (b) identical text that never went through the relay queue at all
        never_queued = "ID: 01710000299 Someone Else 01710000299(N) 500/-"
        assert await is_relay_payment_already_accounted(
            never_queued, conn=conn, message_ts=now) is False


async def test_A_echo_requires_a_booked_queue_row(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import is_relay_payment_already_accounted
    body = "ID: 01710000205 Nur 01710000205(N) 100/-"
    now = datetime.now(timezone.utc)

    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93501, body, now)     # sent, but NEVER booked
        # no fpe_cash_transactions row exists for queue 93501
        assert await is_relay_payment_already_accounted(
            body, conn=conn, message_ts=now + timedelta(seconds=5)) is False


# ══ BLOCKER B ═══════════════════════════════════════════════════════════════

async def test_B_rollback_removes_just_created_employee(test_db_pool, monkeypatch):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    phone = "01715559001"
    body = f"ID: {phone} Rollback Victim {phone}(N) 700/-"

    async with test_db_pool.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM fpe_employees WHERE primary_phone=$1 OR employee_id_phone=$1", phone)
    assert before == 0

    boom = AsyncMock(side_effect=RuntimeError("deliberate failure before commit"))
    monkeypatch.setattr("modules.fazle_payroll_engine.accounting.create_transaction", boom)

    res = await on_relay_send_success(_queue_row(93601, body))
    assert res["ok"] is True                       # never raises
    assert res["booked"] == 0
    assert any(r["status"] == "error" for r in res["results"])

    async with test_db_pool.acquire() as conn:
        after = await conn.fetchval(
            "SELECT count(*) FROM fpe_employees WHERE primary_phone=$1 OR employee_id_phone=$1", phone)
        assert after == 0, "auto-created employee row must NOT survive the rollback"
        assert await _count_txns(conn, "payment-relay-send:93601:") == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_employee_aliases WHERE alias_value=$1", phone) == 0


async def test_B_success_commits_all_effects_atomically(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    phone = "01715559002"
    # seed an INACTIVE employee so activation is one of the effects
    async with test_db_pool.acquire() as conn:
        emp_id = await conn.fetchval(
            """INSERT INTO fpe_employees
                   (full_name, name_normalized, primary_phone, employee_id_phone,
                    status, created_source, resolution_status, basic_salary)
               VALUES ('Atomic Worker','atomic worker',$1,$1,'inactive',
                       'whatsapp_auto_create','resolved',10000) RETURNING id""",
            phone,
        )

    res = await on_relay_send_success(
        _queue_row(93701, f"ID: {phone} Atomic Worker {phone}(N) 900/-"))
    assert res["booked"] == 1

    async with test_db_pool.acquire() as conn:
        txn = await conn.fetchrow(
            "SELECT id, employee_id, amount FROM fpe_cash_transactions "
            "WHERE source_message_id='payment-relay-send:93701:0'")
        assert txn and txn["employee_id"] == emp_id and txn["amount"] == Decimal("900.00")
        assert await conn.fetchval("SELECT status FROM fpe_employees WHERE id=$1", emp_id) == "active"
        assert await conn.fetchval(
            "SELECT txn_count FROM fpe_employee_ledger WHERE employee_id=$1", emp_id) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_accounting_audit_logs "
            "WHERE entity_id=$1 AND action='activate_on_payment'", emp_id) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_accounting_audit_logs "
            "WHERE entity_type='transaction' AND entity_id=$1 AND action='create'", txn["id"]) == 1


async def test_B_existing_caller_without_conn_still_creates(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    r = await match_or_create_employee("Legacy Path", "01715559003", "01715559003")
    assert r is not None and r.match_type == "auto_created"
    async with test_db_pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_employees WHERE primary_phone='01715559003'") == 1
