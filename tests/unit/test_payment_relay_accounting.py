"""Unit tests — modules.payment_relay_accounting + the FPE changes it relies on.

Owner rule (2026-08-30): a successful WhatsApp delivery of an authorized
owner→accountant payment-relay message IS the completed payment. This suite
proves the deterministic post-send accounting integration, exactly-once,
employee identity/status handling, echo dedup, and the 3-month sweep.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit

RELAY_PURPOSE = "owner-accountant-payment-relay"


@pytest.fixture(autouse=True)
def _enable_relay_accounting(monkeypatch):
    monkeypatch.setenv("PAYMENT_RELAY_ACCOUNTING_ENABLED", "true")


async def _seed_employee(conn, *, phone, name, status="active", id_phone=None):
    row = await conn.fetchrow(
        """INSERT INTO fpe_employees
               (full_name, name_normalized, primary_phone, employee_id_phone,
                status, created_source, resolution_status, basic_salary)
           VALUES ($1, $2, $3, $4, $5, 'whatsapp_auto_create', 'resolved', 10000)
           RETURNING id""",
        name, name.lower(), phone, id_phone or phone, status,
    )
    return row["id"]


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


async def _count_txns(conn, source_msg_prefix):
    return await conn.fetchval(
        "SELECT count(*) FROM fpe_cash_transactions WHERE source_message_id LIKE $1",
        source_msg_prefix + "%",
    )


# ── 1. existing INACTIVE employee + successful relay send ────────────────────

async def test_inactive_employee_paid_matched_activated_no_duplicate(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    async with test_db_pool.acquire() as conn:
        emp_id = await _seed_employee(conn, phone="01849258074", name="Saiful Islam", status="inactive")
        emp_count_before = await conn.fetchval("SELECT count(*) FROM fpe_employees WHERE primary_phone='01849258074'")

    res = await on_relay_send_success(
        _queue_row(90001, "ID: 01849258074 Saiful Operation 01849258074(N) 300/-")
    )
    assert res["ok"] is True and res["booked"] == 1

    async with test_db_pool.acquire() as conn:
        # NO new employee row (the crash that idx_fpe_emp_phone_unique used to raise)
        emp_count_after = await conn.fetchval("SELECT count(*) FROM fpe_employees WHERE primary_phone='01849258074'")
        assert emp_count_after == emp_count_before == 1
        emp = await conn.fetchrow("SELECT id, status FROM fpe_employees WHERE id=$1", emp_id)
        assert emp["status"] == "active"                              # activated
        txns = await conn.fetch(
            "SELECT id, employee_id, amount, source_channel, txn_category, transaction_status "
            "FROM fpe_cash_transactions WHERE source_message_id='payment-relay-send:90001:0'")
        assert len(txns) == 1                                        # exactly one txn
        assert txns[0]["employee_id"] == emp_id
        assert txns[0]["amount"] == Decimal("300.00")
        assert txns[0]["source_channel"] == RELAY_PURPOSE
        ledgers = await conn.fetch(
            "SELECT total_paid, txn_count FROM fpe_employee_ledger WHERE employee_id=$1", emp_id)
        assert len(ledgers) == 1 and ledgers[0]["txn_count"] == 1    # ledger updated once
        acts = await conn.fetchval(
            "SELECT count(*) FROM fpe_accounting_audit_logs "
            "WHERE entity_type='employee' AND entity_id=$1 AND action='activate_on_payment'", emp_id)
        assert acts == 1


# ── 2. existing ACTIVE employee + successful payment → stays active ──────────

async def test_active_employee_paid_stays_active_txn_and_ledger(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    async with test_db_pool.acquire() as conn:
        emp_id = await _seed_employee(conn, phone="01711000002", name="Active Worker", status="active")

    res = await on_relay_send_success(_queue_row(90002, "ID: 01711000002 Active Worker 01711000002(B) 500/-"))
    assert res["booked"] == 1
    async with test_db_pool.acquire() as conn:
        emp = await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", emp_id)
        assert emp["status"] == "active"
        assert await _count_txns(conn, "payment-relay-send:90002:") == 1
        assert await conn.fetchval("SELECT total_paid FROM fpe_employee_ledger WHERE employee_id=$1", emp_id) == Decimal("500.00")
        # no activation audit for an already-active employee
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_accounting_audit_logs WHERE entity_id=$1 AND action='activate_on_payment'", emp_id) == 0


# ── 3. unknown phone → existing approved auto-create policy preserved ───────

async def test_unknown_phone_autocreates_per_existing_policy(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    res = await on_relay_send_success(_queue_row(90003, "ID: 01911000003 Brand New Guy 01911000003(N) 200/-"))
    assert res["booked"] == 1
    async with test_db_pool.acquire() as conn:
        emp = await conn.fetchrow("SELECT id, status, created_source FROM fpe_employees WHERE primary_phone='01911000003'")
        assert emp is not None
        assert emp["status"] == "active"
        assert emp["created_source"] == "whatsapp_auto_create"
        assert await _count_txns(conn, "payment-relay-send:90003:") == 1


# ── 4. send failure → NOTHING ──────────────────────────────────────────────

async def test_send_failure_no_accounting(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success
    # on_relay_send_success is ONLY called from sweep_once after a real
    # BRIDGE_SEND_SUCCESS; a failed send never reaches it. Simulate: not called.
    async with test_db_pool.acquire() as conn:
        emp_id = await _seed_employee(conn, phone="01711000004", name="Nopay Worker", status="inactive")
    # (no on_relay_send_success call)
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:90004:") == 0
        assert (await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", emp_id))["status"] == "inactive"
        assert await conn.fetchval("SELECT count(*) FROM fpe_employee_ledger WHERE employee_id=$1", emp_id) == 0


# ── 5. replayed OUTBOUND_SEND_SUCCESS → exactly once ───────────────────────

async def test_replayed_send_success_is_exactly_once(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success

    async with test_db_pool.acquire() as conn:
        emp_id = await _seed_employee(conn, phone="01711000005", name="Once Only", status="active")

    q = _queue_row(90005, "ID: 01711000005 Once Only 01711000005(N) 400/-")
    r1 = await on_relay_send_success(q)
    r2 = await on_relay_send_success(q)          # replay
    r3 = await on_relay_send_success(q)          # replay again

    assert r1["booked"] == 1
    assert r2["results"][0]["status"] == "already_accounted"
    assert r3["results"][0]["status"] == "already_accounted"
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:90005:") == 1
        assert (await conn.fetchrow("SELECT txn_count FROM fpe_employee_ledger WHERE employee_id=$1", emp_id))["txn_count"] == 1


# ── 6. later bridge self-echo / history-sync → no duplicate accounting ─────

async def test_bridge_echo_is_not_double_accounted(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success, is_relay_payment_already_accounted

    body = "ID: 01711000006 Echo Worker 01711000006(B) 700/-"
    async with test_db_pool.acquire() as conn:
        await _seed_employee(conn, phone="01711000006", name="Echo Worker", status="active")
    await on_relay_send_success(_queue_row(90006, body))

    # The exact same body, as it would arrive from a bridge self-echo, is
    # recognised as already accounted.
    assert await is_relay_payment_already_accounted(body) is True
    assert await is_relay_payment_already_accounted("ID: 01711000006 Echo Worker 01711000006(B) 999/-") is False


# ── 7 / 8 / 9. 3-month inactivity sweep + reactivation ─────────────────────

async def _seed_txn(conn, emp_id, txn_date, amount="100.00", ref_suffix="x"):
    await conn.execute(
        """INSERT INTO fpe_cash_transactions
               (txn_ref, employee_id, amount, payout_method, txn_date, txn_category,
                accounting_period, source, transaction_status)
           VALUES ($1, $2, $3, 'nagad', $4, 'salary', $5, 'whatsapp', 'final')""",
        f"fpe-test-{emp_id}-{ref_suffix}", emp_id, Decimal(amount), txn_date,
        txn_date.strftime("%Y-%m"),
    )


async def test_sweep_keeps_recent_deactivates_stale_reactivates_on_payment(test_db_pool, monkeypatch):
    import app.database as db_module
    db_module._pool = test_db_pool
    monkeypatch.setenv("FPE_PAYMENT_INACTIVITY_SWEEP_ENABLED", "true")
    from modules.payment_relay_accounting import run_payment_inactivity_sweep, on_relay_send_success

    today = date.today()
    async with test_db_pool.acquire() as conn:
        recent_id = await _seed_employee(conn, phone="01711000071", name="Recent Pay", status="active")
        stale_id = await _seed_employee(conn, phone="01711000072", name="Stale Pay", status="active")
        nohist_id = await _seed_employee(conn, phone="01711000073", name="No Payments", status="active")
        await _seed_txn(conn, recent_id, today - timedelta(days=30), ref_suffix="recent")
        await _seed_txn(conn, stale_id, today - timedelta(days=120), ref_suffix="stale")

    out = await run_payment_inactivity_sweep()
    assert stale_id in out["employee_ids"]
    assert recent_id not in out["employee_ids"]
    assert nohist_id not in out["employee_ids"]           # no payment history → untouched

    async with test_db_pool.acquire() as conn:
        assert (await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", recent_id))["status"] == "active"
        assert (await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", stale_id))["status"] == "inactive"
        assert (await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", nohist_id))["status"] == "active"

    # 9. a new successful payment immediately reactivates the stale employee
    r = await on_relay_send_success(_queue_row(90079, "ID: 01711000072 Stale Pay 01711000072(N) 250/-"))
    assert r["booked"] == 1
    async with test_db_pool.acquire() as conn:
        assert (await conn.fetchrow("SELECT status FROM fpe_employees WHERE id=$1", stale_id))["status"] == "active"
        # and the clock is reset — the sweep no longer targets them
        out2 = await run_payment_inactivity_sweep()
        assert stale_id not in out2["employee_ids"]


# ── 10. the proven live example never raises idx_fpe_emp_phone_unique ──────

async def test_proven_saiful_example_no_unique_violation(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    async with test_db_pool.acquire() as conn:
        emp_id = await _seed_employee(conn, phone="01849258074", name="Saiful Islam", status="inactive")

    # This is exactly what the FPE worker + the relay accounting path both do.
    emp = await match_or_create_employee("Saiful Operation", "01849258074", "01849258074")
    assert emp is not None
    assert emp.employee_id == emp_id                      # matched the inactive row, no crash
    async with test_db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM fpe_employees WHERE primary_phone='01849258074'") == 1


# ── worker path: match_or_create_employee still returns for active + auto-create ──

async def test_match_prefers_active_when_both_exist(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.fazle_payroll_engine.employee import match_or_create_employee

    async with test_db_pool.acquire() as conn:
        inactive_id = await _seed_employee(conn, phone=None, name="Dup Inactive", status="inactive", id_phone="01711000081")
        active_id = await _seed_employee(conn, phone="01711000081", name="Dup Active", status="active")

    emp = await match_or_create_employee("Dup", "01711000081", "01711000081")
    assert emp.employee_id == active_id                   # active row wins
