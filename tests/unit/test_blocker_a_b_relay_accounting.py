"""
Owner Accounting Blocker A + B (2026-08-30) — payment-relay accounting.

A — transport identity (Python exact-ID fix): the SOLE suppression authority is
    exact WhatsApp stanza-ID equality — the FPE-worker message's
    fpe_wa_messages.wa_message_id must equal a BOOKED relay transaction's
    metadata->>'relay_inbound_message_ref' (or relay_send_stanza_id). NO
    body / timestamp / queue-row heuristic can suppress a financial event.
    Direct idempotency stays payment-relay-send:<queue_id>:<line_idx>.

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


async def _insert_sent_queue_row(conn, qid, body, sent_at, inbound_message_id=None):
    import json as _j
    meta = _j.dumps({"inbound_message_id": inbound_message_id or f"WAMID{qid}"})
    await conn.execute(
        """INSERT INTO fazle_outbound_queue
               (id, recipient, body, source_bridge, purpose, status, sent_at,
                meta_json, external_id, idempotency_key)
           VALUES ($1,'8801844836824',$2,'bridge2',$3,'sent',$4,$5::jsonb,
                   'bridge2:8801844836824',$6)""",
        qid, body, RELAY_PURPOSE, sent_at, meta,
        f"payment-relay:bridge2:{inbound_message_id or ('WAMID'+str(qid))}",
    )


async def _insert_manual_txn(conn, *, emp_id, amount, method="bkash", category="advance",
                             created_by="Shah Azim", txn_date=None):
    """Mirror the shape of the real production manual txn 4476 (source='manual',
    source_channel NULL, source_message_id NULL, txn_category='advance')."""
    import json as _j
    from datetime import date as _d
    tid = await conn.fetchval(
        """INSERT INTO fpe_cash_transactions
               (txn_ref, employee_id, employee_name_raw, amount, payout_phone,
                payout_method, txn_date, txn_category, accounting_period, created_by,
                source, source_channel, source_message_id, transaction_status,
                metadata, is_reversal)
           VALUES ($1,$2,'MD: Billal Mia',$3,'01881894579',$4,$5,$6,$7,$8,
                   'manual', NULL, NULL, 'final', $9::jsonb, FALSE)
           RETURNING id""",
        f"fpe-manual-{emp_id}-{amount}", emp_id, amount, method,
        txn_date or _d(2026, 8, 30), category, "2026-08", created_by,
        _j.dumps({"admin_api": True, "employee_id_phone": "01881894579"}),
    )
    await conn.execute(
        """INSERT INTO fpe_employee_ledger (employee_id, accounting_period, total_paid, txn_count)
           VALUES ($1,'2026-08',$2,1)
           ON CONFLICT (employee_id, accounting_period)
           DO UPDATE SET total_paid = fpe_employee_ledger.total_paid + $2,
                         txn_count = fpe_employee_ledger.txn_count + 1""",
        emp_id, amount,
    )
    return tid


async def _seed_active_emp(conn, *, phone, name):
    return await conn.fetchval(
        """INSERT INTO fpe_employees
               (full_name, name_normalized, primary_phone, employee_id_phone,
                status, created_source, resolution_status, basic_salary)
           VALUES ($1,$2,$3,$3,'active','whatsapp_auto_create','resolved',10000)
           RETURNING id""",
        name, name.lower(), phone,
    )


# ══ RULE B — explicit queue → existing-transaction reconciliation link ══════

async def test_link_zero_new_txns_and_reconciler_skips(test_db_pool):
    """1. queue explicitly linked to an existing manual txn → reconciler /
    on_relay_send_success create ZERO new financial transactions."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        link_existing_transaction_to_relay_queue, on_relay_send_success,
        reconcile_pending_relay_accounting,
    )
    from datetime import datetime, timezone
    body = "ID: 01881894579 MD: Billal Mia 01881894579(B) 400/-"
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400)
        await _insert_sent_queue_row(conn, 96738, body, datetime.now(timezone.utc),
                                     inbound_message_id="3EB0A967FF6CED3A904318")
        total_before = await conn.fetchval("SELECT count(*) FROM fpe_cash_transactions")

    res = await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96738, inbound_message_ref="3EB0A967FF6CED3A904318",
        reason="test: existing manual booking", approved_by="Shah Azim",
    )
    assert res["ok"] and res["linked"] and res["reconciliation_type"] == "existing_manual_booking"

    r1 = await on_relay_send_success(_queue_row(96738, body))
    r2 = await reconcile_pending_relay_accounting()

    assert r1["booked"] == 0
    assert r1["results"][0]["status"] == "reconciled_existing_transaction"
    assert r1["reconciled_existing_txn_id"] == txn
    async with test_db_pool.acquire() as conn:
        total_after = await conn.fetchval("SELECT count(*) FROM fpe_cash_transactions")
        assert total_after == total_before, "no new financial row may be created"
        assert await _count_txns(conn, "payment-relay-send:96738:") == 0


async def test_link_repeated_reconciler_passes_still_zero(test_db_pool):
    """2. repeated reconciler passes for a linked queue → still ZERO new txns."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        link_existing_transaction_to_relay_queue, on_relay_send_success,
        reconcile_pending_relay_accounting,
    )
    from datetime import datetime, timezone
    body = "ID: 01881894579 MD: Billal Mia 01881894579(B) 400/-"
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400)
        await _insert_sent_queue_row(conn, 96739, body, datetime.now(timezone.utc))
    await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96739, inbound_message_ref="WAMID96739",
        reason="test", approved_by="Shah Azim")

    async with test_db_pool.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM fpe_cash_transactions")
    for _ in range(4):
        await on_relay_send_success(_queue_row(96739, body))
        await reconcile_pending_relay_accounting()
    async with test_db_pool.acquire() as conn:
        after = await conn.fetchval("SELECT count(*) FROM fpe_cash_transactions")
        assert after == before


async def test_link_does_not_suppress_a_different_unlinked_queue(test_db_pool):
    """3. a NEW queue with an identical body but NO explicit link → booked."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        link_existing_transaction_to_relay_queue, on_relay_send_success,
    )
    from datetime import datetime, timezone
    body = "ID: 01881894579 MD: Billal Mia 01881894579(B) 400/-"
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400)
    await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96740, inbound_message_ref="WAMID96740",
        reason="test", approved_by="Shah Azim")

    # a genuinely new relay event, identical body, DIFFERENT queue id, no link
    res = await on_relay_send_success(_queue_row(96741, body))
    assert res["booked"] == 1
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:96741:") == 1
        assert await _count_txns(conn, "payment-relay-send:96740:") == 0


async def test_link_is_never_inferred_from_employee_amount_date_method(test_db_pool):
    """4. same employee / amount / date / method but a DIFFERENT queue with no
    explicit link → NOT treated as reconciled (books normally)."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        link_existing_transaction_to_relay_queue, on_relay_send_success,
    )
    from datetime import datetime, timezone
    body = "ID: 01881894579 MD: Billal Mia 01881894579(B) 400/-"
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        # a manual txn: emp/amount/date/method all match the relay body
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400, method="bkash")
        await _insert_sent_queue_row(conn, 96742, body, datetime.now(timezone.utc))
    # link it to a DIFFERENT queue id
    await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=99999, inbound_message_ref="WAMIDX",
        reason="test", approved_by="Shah Azim")

    res = await on_relay_send_success(_queue_row(96742, body))
    assert res["booked"] == 1, "matching facts must NOT auto-reconcile without an explicit link for THIS queue"
    async with test_db_pool.acquire() as conn:
        assert await _count_txns(conn, "payment-relay-send:96742:") == 1


async def test_unlinked_queue_6725_style_books_normally(test_db_pool):
    """5. a queue with no link and no relay txn → normal canonical booking,
    existing inactive employee reused + activated."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import on_relay_send_success
    from decimal import Decimal
    body = "ID: 01849258074 Saiful Operation 01849258074(N) 300/-"
    async with test_db_pool.acquire() as conn:
        emp = await conn.fetchval(
            """INSERT INTO fpe_employees
                   (full_name, name_normalized, primary_phone, employee_id_phone,
                    status, created_source, resolution_status, basic_salary)
               VALUES ('Saiful Islam','saiful islam','01849258074','01849258074',
                       'inactive','whatsapp_auto_create','resolved',10000) RETURNING id""")
    res = await on_relay_send_success(_queue_row(96725, body))
    assert res["booked"] == 1
    async with test_db_pool.acquire() as conn:
        t = await conn.fetchrow(
            "SELECT employee_id, amount, transaction_status FROM fpe_cash_transactions "
            "WHERE source_message_id='payment-relay-send:96725:0'")
        assert t["employee_id"] == emp and t["amount"] == Decimal("300.00")
        assert t["transaction_status"] == "final"
        assert await conn.fetchval("SELECT status FROM fpe_employees WHERE id=$1", emp) == "active"
        assert await conn.fetchval(
            "SELECT count(*) FROM fpe_employees WHERE primary_phone='01849258074'") == 1


async def test_link_preserves_all_original_provenance_and_ledger(test_db_pool):
    """6. linking does not change amount / category / employee_id / ledger /
    manual provenance."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import link_existing_transaction_to_relay_queue
    from decimal import Decimal
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400, method="bkash", category="advance")
        before = dict(await conn.fetchrow(
            "SELECT amount, txn_category, employee_id, source, source_channel, "
            "source_message_id, created_by, is_reversal FROM fpe_cash_transactions WHERE id=$1", txn))
        led_before = await conn.fetchval(
            "SELECT total_paid FROM fpe_employee_ledger WHERE employee_id=$1", emp)

    await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96743, inbound_message_ref="3EB0A967FF6CED3A904318",
        reason="test", approved_by="Shah Azim")

    async with test_db_pool.acquire() as conn:
        after = dict(await conn.fetchrow(
            "SELECT amount, txn_category, employee_id, source, source_channel, "
            "source_message_id, created_by, is_reversal, metadata FROM fpe_cash_transactions WHERE id=$1", txn))
        led_after = await conn.fetchval(
            "SELECT total_paid FROM fpe_employee_ledger WHERE employee_id=$1", emp)
        for k in ("amount", "txn_category", "employee_id", "source", "source_channel",
                  "source_message_id", "created_by", "is_reversal"):
            assert before[k] == after[k], f"{k} changed: {before[k]!r} -> {after[k]!r}"
        assert led_before == led_after == Decimal("400.00")
        import json as _j
        md = after["metadata"]
        md = _j.loads(md) if isinstance(md, str) else md
        assert md["admin_api"] is True                     # original key preserved
        assert md["relay_reconciled_queue_id"] == 96743
        assert md["relay_reconciliation_type"] == "existing_manual_booking"
        assert md["relay_inbound_message_ref"] == "3EB0A967FF6CED3A904318"


async def test_link_audit_row_is_exactly_once_idempotent(test_db_pool):
    """7. the reconciliation-link audit row is written exactly once; re-linking
    the same (txn, queue) is a no-op with no extra audit row; relinking to a
    different queue is refused."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import link_existing_transaction_to_relay_queue
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        txn = await _insert_manual_txn(conn, emp_id=emp, amount=400)

    r1 = await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96744, inbound_message_ref="WAMID96744",
        reason="test", approved_by="Shah Azim")
    r2 = await link_existing_transaction_to_relay_queue(
        txn_id=txn, queue_id=96744, inbound_message_ref="WAMID96744",
        reason="test again", approved_by="Shah Azim")
    assert r1.get("linked") is True
    assert r2.get("already_linked") is True and "linked" not in r2

    async with test_db_pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT count(*) FROM fpe_accounting_audit_logs "
            "WHERE entity_type='transaction' AND entity_id=$1 AND action='relay_reconciliation_link'", txn)
        assert n == 1, f"expected exactly one link-audit row, got {n}"

    # relinking to a DIFFERENT queue is refused
    with pytest.raises(ValueError):
        await link_existing_transaction_to_relay_queue(
            txn_id=txn, queue_id=90000, inbound_message_ref="X",
            reason="x", approved_by="Shah Azim")


async def test_link_refuses_reversal_and_deleted(test_db_pool):
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import link_existing_transaction_to_relay_queue
    async with test_db_pool.acquire() as conn:
        emp = await _seed_active_emp(conn, phone="01881894579", name="Billal Mia")
        rev = await conn.fetchval(
            """INSERT INTO fpe_cash_transactions
                   (txn_ref, employee_id, amount, txn_date, txn_category,
                    accounting_period, created_by, source, transaction_status, is_reversal)
               VALUES ('fpe-rev-x',$1,-400,'2026-08-30','advance','2026-08','sys',
                       'manual','final',TRUE) RETURNING id""", emp)
        deleted = await conn.fetchval(
            """INSERT INTO fpe_cash_transactions
                   (txn_ref, employee_id, amount, txn_date, txn_category,
                    accounting_period, created_by, source, transaction_status,
                    is_reversal, deleted_at, deleted_by)
               VALUES ('fpe-del-x',$1,400,'2026-08-30','advance','2026-08','sys',
                       'manual','final',FALSE, NOW(), 'sys') RETURNING id""", emp)
    with pytest.raises(ValueError):
        await link_existing_transaction_to_relay_queue(
            txn_id=rev, queue_id=1, inbound_message_ref="x", reason="x", approved_by="x")
    with pytest.raises(ValueError):
        await link_existing_transaction_to_relay_queue(
            txn_id=deleted, queue_id=2, inbound_message_ref="x", reason="x", approved_by="x")


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


async def test_A_proof1_same_original_stanza_id_is_suppressed(test_db_pool):
    """1. the exact stanza id of the booked relay's original message → suppressed."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000203 Jsim 01710000203(B) 450/-"
    r = await on_relay_send_success(_queue_row(93301, body))     # inbound_message_id = "WAMID93301"
    assert r["booked"] == 1

    async with test_db_pool.acquire() as conn:
        ref = await conn.fetchval(
            "SELECT metadata->>'relay_inbound_message_ref' FROM fpe_cash_transactions "
            "WHERE source_message_id='payment-relay-send:93301:0'")
        assert ref == "WAMID93301"
        # the FPE worker later re-sees the owner's original message: same stanza id
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="WAMID93301") is True
        # and the WA timestamp is irrelevant to the decision
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="WAMID93301",
            message_ts=datetime.now(timezone.utc) + timedelta(days=99)) is True


async def test_A_proof4_manual_identical_payment_10min_different_stanza_not_suppressed(test_db_pool):
    """4. a genuine manual payment, identical body, +10 min, DIFFERENT stanza id
    → NOT suppressed (the exact case the owner flagged)."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000204 Sohel 01710000204(N) 500/-"
    now = datetime.now(timezone.utc)
    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93401, body, now)
    await on_relay_send_success(_queue_row(93401, body))         # booked; ref = "WAMID93401"

    async with test_db_pool.acquire() as conn:
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="3EB0MANUAL_DIFFERENT",
            message_ts=now + timedelta(minutes=10)) is False


async def test_A_proof5_identical_body_no_stanza_match_not_suppressed(test_db_pool):
    """5. identical body, a stanza id that matches nothing → NOT suppressed.
    Also: no stanza id at all → NOT suppressed (heuristic cannot stand in)."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000205 Nur 01710000205(N) 100/-"
    now = datetime.now(timezone.utc)
    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93501, body, now)
    await on_relay_send_success(_queue_row(93501, body))

    async with test_db_pool.acquire() as conn:
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="NOPE", message_ts=now + timedelta(seconds=5)) is False
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id=None, message_ts=now + timedelta(seconds=5)) is False


async def test_A_proof6_sent_queue_row_but_not_booked_not_suppressed(test_db_pool):
    """6. a 'sent' relay queue row exists but accounting never booked it →
    NOT suppressed even with the exact inbound stanza id."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import is_relay_payment_already_accounted
    body = "ID: 01710000206 Amin 01710000206(N) 250/-"
    now = datetime.now(timezone.utc)
    async with test_db_pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO fazle_outbound_queue
                   (id, recipient, body, source_bridge, purpose, status, sent_at,
                    meta_json, external_id, idempotency_key)
               VALUES (93601,'8801844836824',$1,'bridge2',$2,'sent',$3,
                       '{"inbound_message_id":"WAMID93601"}'::jsonb,
                       'bridge2:8801844836824','payment-relay:bridge2:WAMID93601')""",
            body, RELAY_PURPOSE, now,
        )
        # no fpe_cash_transactions row for 93601
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="WAMID93601", message_ts=now + timedelta(seconds=3)) is False


async def test_A_proof7_unrelated_message_same_body_not_suppressed(test_db_pool):
    """7. an unrelated WhatsApp message that happens to share the body →
    NOT suppressed. Proves the body/time correlation cannot suppress even
    inside its own window."""
    import app.database as db_module
    db_module._pool = test_db_pool
    from modules.payment_relay_accounting import (
        on_relay_send_success, is_relay_payment_already_accounted,
    )
    body = "ID: 01710000207 Bilal 01710000207(N) 900/-"
    now = datetime.now(timezone.utc)
    async with test_db_pool.acquire() as conn:
        await _insert_sent_queue_row(conn, 93701, body, now)
    await on_relay_send_success(_queue_row(93701, body))         # booked; ref = "WAMID93701"

    async with test_db_pool.acquire() as conn:
        # unrelated inbound: identical body, WA timestamp INSIDE the ±30 min
        # window, but its own distinct stanza id → still booked
        assert await is_relay_payment_already_accounted(
            body, conn=conn, stanza_id="3EB0UNRELATED", message_ts=now + timedelta(minutes=1)) is False


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
