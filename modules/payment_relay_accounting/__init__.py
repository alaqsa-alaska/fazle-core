"""
Fazle Core — Owner→Accountant payment-relay ACCOUNTING integration.

Owner business rule (2026-08-30, supersedes "FPE must wait for bridge-SQLite
ingestion"):

    successful WhatsApp delivery of an authorized payment-format message
    from the Admin/Super-Admin flow, to the accountant
        ==  PAYMENT TRANSACTION COMPLETED

So the authoritative accounting trigger is the OUTBOUND SEND SUCCESS boundary
of a payment-relay queue item — NOT an eventual whatsmeow self-echo into the
bridge SQLite store (which is unreliable; see the 2026-08-30 live proof).

Flow:
    qualifying payment relay  (modules.payment_relay, FROZEN — not touched here)
      -> fazle_outbound_queue  (purpose = "owner-accountant-payment-relay")
      -> modules.outbound.sweep_once : bridge2 SendMessage SUCCESS + status='sent'
      -> on_relay_send_success()      [this module]
      -> parse each payment line deterministically (FPE parser, NO LLM)
      -> resolve employee (status-agnostic; existing inactive row is matched,
         never duplicated)
      -> fazle_payroll_engine.accounting.create_transaction(req, conn=<txn>)
           = INSERT fpe_cash_transactions
           + fpe_accounting_audit_logs
           + employee activation (inactive -> active)   [in create_transaction]
           + fpe_employee_ledger upsert                 [in create_transaction]
         ALL in one DB transaction.
      -> STOP

This module NEVER: routes to Hermes / an LLM / recruitment / generic message
classification; parses the body for a routing decision; creates a second cash
transaction / ledger entry / activation for the same successful relay send.

Exactly-once: keyed on the OUTBOUND QUEUE ID (stable transport identity, one
row per relay send) + the payment line index, carried as
fpe_cash_transactions.source_message_id = "payment-relay-send:<queue_id>:<idx>"
and enforced by create_transaction()'s own txn_ref idempotency plus an
explicit pre-check here. Never dedupes by employee+amount+date alone.

Later bridge self-echo / history-sync of the same successfully-relayed message
is recognised as already-accounted by is_relay_payment_already_accounted() —
which correlates the FPE-worker message to a real relay SEND EVENT: a
fazle_outbound_queue row (purpose = owner-accountant-payment-relay, status =
'sent') whose body is byte-identical, whose sent_at is within ±30 min of the
message's WhatsApp timestamp, and which actually produced FPE transactions.
Body sha256 is NOT used as financial identity — two genuine payments may carry
identical text; a manual/unrelated identical-body message that never went
through the relay queue is booked normally.

Blocker B (2026-08-30): employee resolve/create + cash transaction + audit +
ledger + activation for each payment line run in ONE atomic asyncpg
transaction (match_or_create_employee now takes conn=). A failure before
commit rolls back a just-created employee row too; a sibling line that already
committed is untouched; the reconcile pass retries the unbooked ones.

Gated by env flag PAYMENT_RELAY_ACCOUNTING_ENABLED (default: false).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.database import db_conn, fetch_all, fetch_one

log = logging.getLogger("fazle.payment_relay_accounting")

RELAY_PURPOSE = "owner-accountant-payment-relay"
SOURCE_CHANNEL = "owner-accountant-payment-relay"

# Echo/self-echo correlation window (Owner Blocker A, 2026-08-30): a bridge
# self-echo / history-sync copy of a relayed payment message lands within
# seconds of the send; ±30 min is generous slack for clock skew and
# history-sync backfill lag while staying far away from "a genuine identical
# payment days later". A hard 14-day outer bound on the queue row caps it.
_ECHO_CORRELATION_MINUTES = 30
_ECHO_QUEUE_MAX_AGE_DAYS = 14


class _LineRollback(Exception):
    """Internal: abort one payment line's transaction without persisting
    anything, while leaving already-committed sibling lines intact."""


def _enabled() -> bool:
    return os.getenv("PAYMENT_RELAY_ACCOUNTING_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _payment_lines(body: str) -> list[str]:
    """Split a relay body into candidate payment lines. The owner sends one
    payment per WhatsApp message in practice, but the relay body is allowed to
    carry several `ID: ... /-` lines — account each independently, mirroring
    what a human forwarding line-by-line would produce."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    return lines or ([body.strip()] if body and body.strip() else [])


async def _coerce_meta(meta_json: Any) -> dict:
    if isinstance(meta_json, dict):
        return meta_json
    if isinstance(meta_json, str) and meta_json.strip():
        try:
            return json.loads(meta_json)
        except Exception:  # noqa: BLE001
            return {}
    return {}


# ── Main entry point — called from modules.outbound.sweep_once ────────────────

async def on_relay_send_success(queue_row: dict, external_id: Optional[str] = None) -> dict:
    """Account a payment-relay queue row that just reached OUTBOUND_SEND_SUCCESS.

    Never raises — the relay SEND already succeeded and must not be undone by
    an accounting error. Idempotent: safe to call again for the same
    queue_row (a re-observed / retried send-success).
    """
    try:
        if (queue_row.get("purpose") or "") != RELAY_PURPOSE:
            return {"ok": True, "skipped": "not_a_payment_relay"}
        if not _enabled():
            log.info(
                "[relay_acct] PAYMENT_RELAY_ACCOUNTING_ENABLED is off — relay queue id=%s "
                "delivered but NOT booked to FPE",
                queue_row.get("id"),
            )
            return {"ok": True, "skipped": "accounting_disabled"}
        return await _account_relay_send(
            queue_id=int(queue_row["id"]),
            body=queue_row.get("body") or "",
            recipient=queue_row.get("recipient") or "",
            meta=await _coerce_meta(queue_row.get("meta_json")),
            idempotency_key=queue_row.get("idempotency_key"),
            external_id=external_id or queue_row.get("external_id"),
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "[relay_acct] accounting failed for outbound queue id=%s (send already "
            "succeeded; reconcile pass will retry): %s",
            queue_row.get("id"), exc, exc_info=True,
        )
        return {"ok": False, "error": str(exc)[:300], "queue_id": queue_row.get("id")}


async def _account_relay_send(
    *,
    queue_id: int,
    body: str,
    recipient: str,
    meta: dict,
    idempotency_key: Optional[str],
    external_id: Optional[str],
) -> dict:
    from modules.fazle_payroll_engine.parser import parse_message
    from modules.fazle_payroll_engine.models import (
        MessageType, PayoutMethod, TransactionCreateRequest, TxnCategory, PaymentSource,
        TransactionStatus,
    )
    from modules.fazle_payroll_engine.employee import match_or_create_employee
    from modules.fazle_payroll_engine.accounting import create_transaction

    lines = _payment_lines(body)
    msg_sha = _sha256(body)
    today = date.today()

    parsed: list[dict] = []
    for idx, line in enumerate(lines):
        pr = parse_message(line, today)
        if pr.message_type != MessageType.payment or not pr.payment or not pr.payment.amount:
            log.warning(
                "[relay_acct] queue id=%s line %d did not parse as a payment: %r",
                queue_id, idx, line[:120],
            )
            continue
        parsed.append({"idx": idx, "line": line, "p": pr.payment})

    if not parsed:
        log.error(
            "[relay_acct] queue id=%s: NO payment line parsed from a relayed message "
            "(shape check passed at relay time) — nothing booked; body_sha256=%s",
            queue_id, msg_sha,
        )
        return {"ok": False, "queue_id": queue_id, "reason": "no_payment_line_parsed"}

    results: list[dict] = []
    async with db_conn() as conn:
        for item in parsed:
            idx, line, p = item["idx"], item["line"], item["p"]
            src_msg_id = f"payment-relay-send:{queue_id}:{idx}"

            # Exactly-once — this exact (queue send, line) already booked?
            existing = await fetch_one(
                "SELECT id, txn_ref, employee_id, amount FROM fpe_cash_transactions "
                "WHERE source_message_id = $1 AND source_channel = $2",
                src_msg_id, SOURCE_CHANNEL, conn=conn,
            )
            if existing:
                results.append({"line": idx, "status": "already_accounted",
                                "txn_id": existing["id"], "txn_ref": existing["txn_ref"]})
                continue

            # ── ONE atomic transaction PER payment line (Owner Blocker B) ──
            # employee resolve/create + cash txn + audit + ledger + activation
            # all commit together or not at all. A failure here rolls back the
            # just-created employee row too (conn= threaded into
            # match_or_create_employee). A sibling line that already committed
            # is untouched; the reconcile pass retries only the unbooked ones.
            try:
                async with conn.transaction():
                    emp = await match_or_create_employee(
                        p.employee_name_raw, p.payout_phone, p.employee_id_phone,
                        conn=conn,
                    )
                    if emp is None:
                        log.error(
                            "[relay_acct] queue id=%s line %d: employee unresolved "
                            "(name=%r payout=%s id=%s) — line NOT booked, needs manual review",
                            queue_id, idx, p.employee_name_raw, p.payout_phone, p.employee_id_phone,
                        )
                        results.append({"line": idx, "status": "employee_unresolved"})
                        raise _LineRollback()  # abort this line's txn, keep siblings

                    req = TransactionCreateRequest(
                        fpe_wa_message_id=None,
                        employee_id=emp.employee_id,
                        employee_name_raw=p.employee_name_raw,
                        amount=Decimal(str(p.amount)),
                        payout_phone=p.payout_phone,
                        payout_method=p.payout_method or PayoutMethod.unknown,
                        txn_date=p.txn_date or today,
                        txn_category=TxnCategory.salary,
                        source_message_text=line[:500],
                        source=PaymentSource.whatsapp,
                        source_channel=SOURCE_CHANNEL,
                        source_message_id=src_msg_id,
                        employee_id_phone=p.employee_id_phone,
                        employee_phone=p.payout_phone,
                        transaction_status=TransactionStatus.final,
                        created_by="payment_relay_accounting",
                        metadata={
                            "relay_outbound_queue_id": queue_id,
                            "relay_line_index": idx,
                            "relay_line_count": len(lines),
                            "relay_message_sha256": msg_sha,
                            "relay_line_sha256": _sha256(line),
                            "relay_idempotency_key": idempotency_key,
                            "relay_inbound_message_ref": meta.get("inbound_message_id"),
                            "relay_inbound_bridge": meta.get("inbound_bridge"),
                            "relay_source_number": meta.get("source_number"),
                            "relay_dest": recipient,
                            "relay_external_id": external_id,
                            "accounting_trigger": "outbound_send_success",
                        },
                    )
                    txn = await create_transaction(req, conn=conn)
                    _line_result = {
                        "line": idx, "status": "booked", "txn_id": txn.id, "txn_ref": txn.txn_ref,
                        "employee_id": emp.employee_id, "amount": str(txn.amount),
                        "match_type": emp.match_type,
                    }
                # transaction committed here (context manager exit)
                results.append(_line_result)
                log.info(
                    "[relay_acct] BOOKED queue id=%s line %d txn_id=%s ref=%s emp=%s amount=%s "
                    "match=%s",
                    queue_id, idx, txn.id, txn.txn_ref[:12], emp.employee_id, txn.amount,
                    emp.match_type,
                )
            except _LineRollback:
                # deliberate abort (employee unresolved) — already recorded;
                # nothing from this line's transaction persists.
                continue
            except Exception as _line_exc:  # noqa: BLE001
                log.error(
                    "[relay_acct] queue id=%s line %d accounting FAILED and rolled back "
                    "(no partial rows); reconcile pass will retry: %s",
                    queue_id, idx, _line_exc, exc_info=True,
                )
                results.append({"line": idx, "status": "error", "detail": str(_line_exc)[:200]})
                continue

    booked = [r for r in results if r["status"] == "booked"]
    return {
        "ok": True, "queue_id": queue_id, "lines": len(lines),
        "booked": len(booked), "results": results, "body_sha256": msg_sha,
    }


# ── Reconcile safety net — for crashes between status='sent' and accounting ──

async def reconcile_pending_relay_accounting(limit: int = 25) -> dict:
    """Find payment-relay queue rows that were successfully SENT but have no
    corresponding fpe_cash_transactions row, and book them. Idempotent; run
    periodically from the FPE accounting worker."""
    if not _enabled():
        return {"ok": True, "skipped": "disabled", "processed": 0}
    rows = await fetch_all(
        """
        SELECT q.id, q.recipient, q.body, q.meta_json, q.external_id, q.idempotency_key,
               q.purpose, q.sent_at
        FROM fazle_outbound_queue q
        WHERE q.purpose = $1
          AND q.status = 'sent'
          AND q.sent_at > NOW() - INTERVAL '7 days'
          AND NOT EXISTS (
              SELECT 1 FROM fpe_cash_transactions t
              WHERE t.source_channel = $2
                AND t.source_message_id LIKE ('payment-relay-send:' || q.id::text || ':%')
          )
        ORDER BY q.sent_at ASC
        LIMIT $3
        """,
        RELAY_PURPOSE, SOURCE_CHANNEL, limit,
    )
    processed = 0
    for r in rows:
        res = await on_relay_send_success(dict(r), r.get("external_id"))
        if res.get("ok") and res.get("booked"):
            processed += 1
    if rows:
        log.info("[relay_acct] reconcile pass: %d/%d pending relay sends booked", processed, len(rows))
    return {"ok": True, "candidates": len(rows), "processed": processed}


# ── Echo dedup — used by the FPE accounting worker (historical_sync / gap_scan) ──

async def is_relay_payment_already_accounted(
    raw_content: str,
    conn: Any = None,
    *,
    message_ts: Any = None,
) -> bool:
    """True iff this FPE-worker bridge-store message is the self-echo /
    history-sync copy of an owner→accountant relay SEND EVENT that was already
    booked by the direct accounting path — so re-booking it would double-count.

    Owner Blocker A (2026-08-30): body sha256 is NOT financial identity (two
    genuine payments can carry identical text). Suppression requires a real
    correlated transport event:

      * a fazle_outbound_queue row q with purpose = owner-accountant-payment-relay
        AND status = 'sent'
      * q.body byte-identical to this message body (the relay forwards verbatim)
      * q.sent_at within ±_ECHO_CORRELATION_MINUTES of this message's WA
        timestamp (a self-echo lands within seconds); when no timestamp is
        available, fall back to q.sent_at within the last 3 days
      * q was actually booked — an fpe_cash_transactions row exists with
        source_channel = SOURCE_CHANNEL and
        source_message_id LIKE 'payment-relay-send:<q.id>:%'

    A manual / unrelated message with identical text that never went through
    the relay queue, or is outside the time window, matches nothing → NOT
    suppressed → the FPE worker books it normally.

    Fail-open (returns False) on error so a real payment is never silently
    dropped. The relay_message_sha256 stored in txn metadata is kept for
    audit/forensics only and is no longer consulted as identity.
    """
    if not raw_content or not raw_content.strip():
        return False
    body = raw_content  # exact — the relay forwards the verbatim inbound body
    try:
        if message_ts is not None:
            row = await fetch_one(
                """
                SELECT q.id
                FROM fazle_outbound_queue q
                WHERE q.purpose = $1
                  AND q.status = 'sent'
                  AND q.body = $2
                  AND q.sent_at >  ($3::timestamptz - make_interval(mins => $4))
                  AND q.sent_at <  ($3::timestamptz + make_interval(mins => $4))
                  AND q.sent_at >  NOW() - make_interval(days => $5)
                  AND EXISTS (
                      SELECT 1 FROM fpe_cash_transactions t
                      WHERE t.source_channel = $6
                        AND NOT t.is_reversal
                        AND t.source_message_id LIKE ('payment-relay-send:' || q.id::text || ':%')
                  )
                LIMIT 1
                """,
                RELAY_PURPOSE, body, message_ts, _ECHO_CORRELATION_MINUTES,
                _ECHO_QUEUE_MAX_AGE_DAYS, SOURCE_CHANNEL,
                conn=conn,
            )
        else:
            row = await fetch_one(
                """
                SELECT q.id
                FROM fazle_outbound_queue q
                WHERE q.purpose = $1
                  AND q.status = 'sent'
                  AND q.body = $2
                  AND q.sent_at > NOW() - INTERVAL '3 days'
                  AND EXISTS (
                      SELECT 1 FROM fpe_cash_transactions t
                      WHERE t.source_channel = $3
                        AND NOT t.is_reversal
                        AND t.source_message_id LIKE ('payment-relay-send:' || q.id::text || ':%')
                  )
                LIMIT 1
                """,
                RELAY_PURPOSE, body, SOURCE_CHANNEL,
                conn=conn,
            )
        return row is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("[relay_acct] echo-dedup check errored (treating as not-accounted): %s", exc)
        return False


# ── 3-month payment-inactivity sweep (Owner rule) — flag-gated OFF by default ──

def _sweep_enabled() -> bool:
    return os.getenv("FPE_PAYMENT_INACTIVITY_SWEEP_ENABLED", "false").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def run_payment_inactivity_sweep() -> dict:
    """Owner rule 2026-08-30: an employee with qualifying payment history whose
    LATEST successful qualifying transaction is older than 3 months becomes
    inactive. A later successful payment reactivates immediately (handled in
    accounting.create_transaction) and resets the clock.

    SCOPE (deliberately bounded — see the pre-deployment report): only
    employees that (a) are currently 'active', AND (b) have >= 1 non-reversal
    'final' salary/advance/bonus transaction, AND (c) whose MAX(txn_date) over
    those is < CURRENT_DATE - 3 months. Employees with NO qualifying payment
    history are never touched by this sweep — their status is governed
    elsewhere.

    Flag-gated by FPE_PAYMENT_INACTIVITY_SWEEP_ENABLED (default false).
    """
    if not _sweep_enabled():
        return {"ok": True, "skipped": "disabled", "deactivated": 0}

    async with db_conn() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                WITH latest AS (
                    SELECT employee_id, MAX(txn_date) AS last_txn_date
                    FROM fpe_cash_transactions
                    WHERE employee_id IS NOT NULL
                      AND NOT is_reversal
                      AND transaction_status = 'final'
                      AND txn_category IN ('salary', 'advance', 'bonus')
                    GROUP BY employee_id
                )
                SELECT e.id, e.full_name, latest.last_txn_date
                FROM fpe_employees e
                JOIN latest ON latest.employee_id = e.id
                WHERE e.status = 'active'
                  AND latest.last_txn_date < (CURRENT_DATE - INTERVAL '3 months')
                """
            )
            ids = [r["id"] for r in rows]
            if ids:
                await conn.execute(
                    "UPDATE fpe_employees SET status = 'inactive', updated_at = NOW() "
                    "WHERE id = ANY($1::bigint[]) AND status = 'active'",
                    ids,
                )
                for r in rows:
                    await conn.execute(
                        """
                        INSERT INTO fpe_accounting_audit_logs
                            (entity_type, entity_id, action, after_state, performed_by, reason)
                        VALUES ('employee', $1, 'deactivate_payment_inactivity', $2,
                                'payment_relay_accounting.sweep', $3)
                        """,
                        r["id"],
                        json.dumps({"status": "inactive", "last_txn_date": r["last_txn_date"].isoformat()}),
                        f"no qualifying payment since {r['last_txn_date'].isoformat()} (>3 months)",
                    )
    log.info("[relay_acct] payment-inactivity sweep: %d employee(s) -> inactive", len(rows))
    return {"ok": True, "deactivated": len(rows), "employee_ids": [r["id"] for r in rows]}
