"""
Fazle Payroll Engine — Background workers.

All workers are asyncio tasks (no Celery / RQ — consistent with existing fazle-core pattern).

Worker pipeline:
  1. message_processor_worker — polls fpe_message_processing_state for 'pending' rows,
       runs parser, calls AI enhancer if needed, stores parser_result.
  2. accounting_worker — polls for 'parsed' rows that are payment type,
       runs employee match, creates transactions, updates ledger.
  3. historical_sync_worker — calls historical_sync.historical_sync_loop() continuously.

Workers are started in FPE module __init__ via start_workers() and stopped on shutdown.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.database import fetch_all, fetch_one
from .accounting import create_income_transaction, create_transaction
from .ai_enhancer import ai_enhance_parse
from .employee import match_or_create_employee
from .gap_scan import gap_scan_loop
from .historical_sync import historical_sync_loop
from .ingestion import mark_processing_status, store_parser_result, store_unmatched
from .models import (
    IngestionRequest,
    MessageType,
    PayoutMethod,
    TxnCategory,
    TransactionCreateRequest,
)
from .normalizer import normalize_bd_phone
from .parser import parse_message
from .validation import ACCOUNTING_TYPES, validate_for_accounting
from .diagnostics import bridge_health_loop, record_outcome

log = logging.getLogger("fazle.fpe.workers")

POLL_INTERVAL = 3       # seconds between DB polls
BATCH_SIZE = 20         # messages per worker tick
MAX_ATTEMPTS = 5        # give up after this many failures


def _eligible_for_accounting_review(pdata: dict) -> bool:
    """Only complete money/payment parses may enter the Accounting Review queue."""
    name_raw = (pdata.get("employee_name_raw") or "").strip()
    payout_phone = normalize_bd_phone(pdata.get("payout_phone") or "")
    payout_method = (pdata.get("payout_method") or "").strip().lower()
    amount_raw = pdata.get("amount")
    try:
        amount = Decimal(str(amount_raw)) if amount_raw is not None and str(amount_raw).strip() else None
    except Exception:
        amount = None
    return bool(
        name_raw
        and payout_phone
        and payout_method
        and payout_method != "unknown"
        and amount is not None
        and amount > 0
    )


# ── Worker management ─────────────────────────────────────────────────────────

_tasks: list[asyncio.Task] = []


async def start_workers(chat_jids: Optional[list[str]] = None) -> None:
    """Start all FPE background workers. Called from module __init__ on startup."""
    global _tasks
    _tasks = [
        asyncio.create_task(message_processor_worker(), name="fpe_msg_processor"),
        asyncio.create_task(accounting_worker(), name="fpe_accounting"),
        asyncio.create_task(historical_sync_loop(chat_jids), name="fpe_hsync"),
        asyncio.create_task(gap_scan_loop(chat_jids), name="fpe_gapscan"),
        asyncio.create_task(bridge_health_loop(), name="fpe_bridge_health"),
    ]
    log.info("[fpe.workers] started %d workers: %s", len(_tasks), [t.get_name() for t in _tasks])


async def stop_workers() -> None:
    """Cancel all FPE workers gracefully. Called on app shutdown."""
    global _tasks
    for task in _tasks:
        if not task.done():
            task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
    _tasks = []
    log.info("[fpe.workers] all workers stopped")


# ── Worker 1: Message processor (pending → parsing → parsed | skipped | failed) ──

async def message_processor_worker() -> None:
    """
    Poll fpe_message_processing_state for 'pending' rows.
    Run parser + optional AI enhancement on each message.
    """
    log.info("[fpe.worker.parser] started")
    while True:
        try:
            await _process_pending_batch()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("[fpe.worker.parser] error: %s", exc, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


async def _process_pending_batch() -> None:
    rows = await fetch_all(
        """
        SELECT mps.id AS mps_id, mps.fpe_wa_message_id, mps.attempts,
               m.raw_content, m.is_from_me, m.timestamp_wa, m.source, m.chat_jid,
               m.sender_phone
        FROM fpe_message_processing_state mps
        JOIN fpe_wa_messages m ON m.id = mps.fpe_wa_message_id
        WHERE mps.status = 'pending'
          AND mps.attempts < $1
        ORDER BY mps.queued_at ASC
        LIMIT $2
        """,
        MAX_ATTEMPTS, BATCH_SIZE,
    )

    for row in rows:
        msg_id = row["fpe_wa_message_id"]
        content = row["raw_content"] or ""
        is_from_me = row["is_from_me"]

        try:
            await mark_processing_status(msg_id, "parsing")

            # Parse the message
            msg_date = row["timestamp_wa"].date() if row["timestamp_wa"] else None
            result = parse_message(content, msg_date)

            # AI enhancement if confidence low
            ai_enhanced = False
            ai_notes = None
            if result.confidence < 0.7 and content.strip():
                ai_data = await ai_enhance_parse(content, result.confidence)
                if ai_data and ai_data.get("is_payment"):
                    result = _ai_data_to_parse_result(ai_data, content, msg_date)
                    ai_enhanced = True
                    ai_notes = ai_data.get("notes")

            # Determine if we should skip (not from owner, or non-payment)
            if not is_from_me and result.message_type == MessageType.other:
                await store_unmatched(
                    msg_id, "accountant_other", content,
                    parser_confidence=result.confidence,
                )
                await mark_processing_status(msg_id, "skipped")
                continue

            # Store parser result
            parsed_data = {}
            if result.payment:
                p = result.payment
                parsed_data = {
                    "employee_id_phone": p.employee_id_phone,
                    "employee_name_raw": p.employee_name_raw,
                    "payout_phone": p.payout_phone,
                    "payout_method": p.payout_method.value if p.payout_method else None,
                    "amount": str(p.amount) if p.amount else None,
                    "txn_date": p.txn_date.isoformat() if p.txn_date else None,
                }
            elif result.balance_summary:
                bs = result.balance_summary
                parsed_data = {
                    "summary_date": bs.summary_date.isoformat() if bs.summary_date else None,
                    "total_due": str(bs.total_due) if bs.total_due else None,
                    "total_collected": str(bs.total_collected) if bs.total_collected else None,
                }
            elif result.escort_payment:
                ep = result.escort_payment
                parsed_data = {
                    "entry_count": len(ep.entries),
                    "shift": ep.shift,
                    "duty_date": ep.duty_date.isoformat() if ep.duty_date else None,
                    "total_amount": str(ep.total_amount) if ep.total_amount else None,
                    "entries": [{"name": e.name_raw, "amount": str(e.amount)} for e in ep.entries],
                }

            await store_parser_result(
                msg_id,
                result.message_type.value,
                parsed_data,
                result.confidence,
                ai_enhanced,
                ai_notes,
            )

            # Transition to 'parsed' or 'skipped'
            if result.message_type == MessageType.payment:
                next_status = "parsed"
            elif result.message_type == MessageType.escort_payment:
                next_status = "parsed"
            elif result.message_type in (MessageType.cash_command, MessageType.income_command):
                # Sender authorization check
                from app.config import get_settings as _get_settings
                _settings = _get_settings()
                sender_raw = row.get("sender_phone") or ""
                sender_norm = normalize_bd_phone(sender_raw) or sender_raw
                if result.message_type == MessageType.cash_command:
                    authorized = sender_norm in _settings.fpe_cash_authorized_phone_list
                else:
                    authorized = sender_norm in _settings.fpe_income_authorized_phone_list

                if not authorized:
                    log.info(
                        "[fpe.worker.parser] unauthorized %s sender=%s msg=%d",
                        result.message_type.value, sender_norm, msg_id,
                    )
                    await mark_processing_status(msg_id, "skipped")
                    continue

                next_status = "parsed"
            else:
                next_status = "skipped"
                # Surface non-payment messages in the review queue so an admin
                # can inspect them (balance summaries, admin chitchat, etc.).
                if result.message_type.value == "balance_summary":
                    skip_reason = "balance_summary"
                elif is_from_me:
                    skip_reason = "admin_other"
                else:
                    skip_reason = "accountant_other"
                await store_unmatched(
                    msg_id, skip_reason, content,
                    detected_employee_name=parsed_data.get("employee_name_raw"),
                    detected_payout_phone=parsed_data.get("payout_phone"),
                    detected_payout_method=parsed_data.get("payout_method"),
                    parser_confidence=result.confidence,
                )
            await mark_processing_status(msg_id, next_status)

        except Exception as exc:
            log.error("[fpe.worker.parser] failed msg_id=%d: %s", msg_id, exc, exc_info=True)
            try:
                await store_unmatched(
                    msg_id, "parser_failed", content,
                )
            except Exception as ue:
                log.debug("[fpe.worker.parser] store_unmatched failed: %s", ue)
            await mark_processing_status(msg_id, "failed", str(exc)[:500])


# ── Worker 2: Accounting (parsed → accounting → done | failed) ───────────────

async def accounting_worker() -> None:
    """
    Poll for 'parsed' messages, run employee match, create transactions + ledger.
    """
    log.info("[fpe.worker.accounting] started")
    while True:
        try:
            await _process_parsed_batch()
            await _tick_zero_loss_gauges()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.error("[fpe.worker.accounting] error: %s", exc, exc_info=True)
        await asyncio.sleep(POLL_INTERVAL + 2)  # slight offset from parser


async def _tick_zero_loss_gauges() -> None:
    """Refresh Prometheus gauges for review queue + DLQ depth."""
    try:
        from app.database import fetch_val
        from modules import observability as obs
        from .routes import _cleanup_stale_review_queue_rows

        await _cleanup_stale_review_queue_rows()
        pending_review = await fetch_val(
            "SELECT COUNT(*) FROM fpe_unmatched_messages "
            "WHERE review_status='pending' "
            "AND detected_amount IS NOT NULL "
            "AND COALESCE(BTRIM(detected_employee_name), '') <> '' "
            "AND COALESCE(BTRIM(detected_payout_phone), '') <> '' "
            "AND COALESCE(BTRIM(detected_payout_method), '') <> '' "
            "AND LOWER(detected_payout_method) <> 'unknown'"
        ) or 0
        dlq = await fetch_val(
            "SELECT COUNT(*) FROM fpe_message_processing_state "
            "WHERE status='failed' AND attempts >= $1",
            MAX_ATTEMPTS,
        ) or 0
        obs.gauge("fpe_pending_review_count", float(pending_review))
        obs.gauge("fpe_dlq_count", float(dlq))
    except Exception as exc:
        log.debug("[fpe.gauges] tick failed: %s", exc)


async def _process_parsed_batch() -> None:
    rows = await fetch_all(
        """
        SELECT mps.fpe_wa_message_id,
               pr.message_type, pr.parsed_data, pr.confidence,
               m.raw_content, m.is_from_me, m.source, m.chat_jid, m.sender_phone
        FROM fpe_message_processing_state mps
        JOIN fpe_parser_results pr ON pr.fpe_wa_message_id = mps.fpe_wa_message_id
        JOIN fpe_wa_messages m ON m.id = mps.fpe_wa_message_id
        WHERE mps.status = 'parsed'
          AND pr.message_type = ANY($3::text[])
          AND mps.attempts < $1
        ORDER BY mps.queued_at ASC
        LIMIT $2
        """,
        MAX_ATTEMPTS, BATCH_SIZE, list(ACCOUNTING_TYPES),
    )

    for row in rows:
        msg_id = row["fpe_wa_message_id"]
        msg_type = row["message_type"]
        _raw_pdata = row["parsed_data"]
        pdata = _json.loads(_raw_pdata) if isinstance(_raw_pdata, str) else (_raw_pdata or {})
        _t0 = time.monotonic()
        _diag_status: str = "failed"
        _diag_reason: Optional[str] = None

        try:
            await mark_processing_status(msg_id, "accounting")

            # ── Type-aware validation — replaces all generic field guards ───
            #    See validation.py MESSAGE_RULES for per-type requirements.
            #    Adding a new message type? Add its rule there, NOT here.
            vr = validate_for_accounting(msg_type, pdata)
            if not vr.valid:
                confidence = float(row["confidence"]) if row["confidence"] is not None else None
                if _eligible_for_accounting_review(pdata):
                    await store_unmatched(
                        msg_id, vr.failure_code, row["raw_content"],
                        detected_amount=Decimal(str(pdata.get("amount"))) if pdata.get("amount") is not None else None,
                        detected_payout_phone=normalize_bd_phone(pdata.get("payout_phone")),
                        detected_employee_name=pdata.get("employee_name_raw"),
                        detected_payout_method=pdata.get("payout_method") or "unknown",
                        parser_confidence=confidence,
                    )
                await mark_processing_status(msg_id, "skipped")
                _diag_status, _diag_reason = "skipped", vr.failure_code
                log.info(
                    "[fpe.worker.acct] validation_fail msg=%d type=%s reason=%s | %s",
                    msg_id, msg_type, vr.failure_code, vr.failure_detail,
                )
                continue

            # ── escort_payment: multi-entry, dispatched directly ─────────
            if msg_type == "escort_payment":
                await _handle_escort_payment(msg_id, pdata, row)
                _diag_status = "done"
                continue

            # ── Amount-based handlers ────────────────────────────────────
            #    validate_for_accounting() guarantees amount is present and > 0.
            name_raw = pdata.get("employee_name_raw")
            payout_phone = normalize_bd_phone(pdata.get("payout_phone"))
            id_phone = normalize_bd_phone(pdata.get("employee_id_phone"))
            amount_str = pdata.get("amount")
            method_str = pdata.get("payout_method") or "unknown"
            txn_date_str = pdata.get("txn_date")
            confidence = float(row["confidence"]) if row["confidence"] is not None else None

            amount = Decimal(amount_str)  # safe: validate_for_accounting() passed
            txn_date = datetime.fromisoformat(txn_date_str).date() if txn_date_str else datetime.utcnow().date()

            # ── Cash command: employee MUST exist (strict lookup, reply on error) ──
            if msg_type == "cash_command":
                from app.database import fetch_one as _fetch_one
                from app.bridge import get_bridge1, get_bridge2

                emp_phone = payout_phone or id_phone
                emp_row = None
                if emp_phone:
                    emp_row = await _fetch_one(
                        "SELECT id FROM fpe_employees "
                        "WHERE primary_phone = $1 OR employee_id_phone = $1 "
                        "LIMIT 1",
                        emp_phone,
                    )

                if not emp_row:
                    # Employee not found — send WhatsApp error reply
                    reply = (
                        f"❌ Cash command failed: employee not found for "
                        f"'{emp_phone or name_raw}'.\n"
                        f"Please create the employee first, then retry."
                    )
                    try:
                        bridge = get_bridge1() if row["source"] == "bridge1" else get_bridge2()
                        await bridge.send(row["chat_jid"], reply)
                    except Exception as br_exc:
                        log.warning("[fpe.worker.acct] bridge reply failed: %s", br_exc)
                    await store_unmatched(
                        msg_id, "cash_command_no_employee", row["raw_content"],
                        detected_amount=amount,
                        detected_payout_phone=emp_phone,
                        detected_employee_name=name_raw,
                        parser_confidence=confidence,
                    )
                    await mark_processing_status(msg_id, "skipped")
                    _diag_status, _diag_reason = "skipped", "cash_command_no_employee"
                    continue

                # Employee found — create salary transaction
                req = TransactionCreateRequest(
                    fpe_wa_message_id=msg_id,
                    employee_id=emp_row["id"],
                    employee_name_raw=name_raw,
                    amount=amount,
                    payout_phone=emp_phone,
                    payout_method=PayoutMethod.cash,
                    txn_date=txn_date,
                    txn_category=TxnCategory.salary,
                    source_message_text=row["raw_content"],
                    created_by=normalize_bd_phone(row.get("sender_phone")) or "system",
                )
                txn = await create_transaction(req)
                await mark_processing_status(msg_id, "done")
                log.info(
                    "[fpe.worker.acct] cash_cmd done msg=%d emp=%d txn=%s amount=%s",
                    msg_id, emp_row["id"], txn.txn_ref[:12], amount,
                )
                _diag_status = "done"
                continue

            # ── Income command: auto-create employee, write income table ──────
            if msg_type == "income_command":
                emp_phone = payout_phone or id_phone
                emp = await match_or_create_employee(name_raw, emp_phone, emp_phone)
                emp_id = emp.employee_id if emp else None

                await create_income_transaction(
                    fpe_wa_message_id=msg_id,
                    employee_id=emp_id,
                    employee_name_raw=name_raw,
                    amount=amount,
                    txn_date=txn_date,
                    reported_by_phone=normalize_bd_phone(row.get("sender_phone")),
                    source_message_text=row["raw_content"],
                )
                await mark_processing_status(msg_id, "done")
                log.info(
                    "[fpe.worker.acct] income_cmd done msg=%d emp=%s amount=%s",
                    msg_id, emp_id, amount,
                )
                _diag_status = "done"
                continue

            # ── Standard payment (is_from_me required) ────────────────────────
            if not row["is_from_me"]:
                await mark_processing_status(msg_id, "skipped")
                _diag_status, _diag_reason = "skipped", "not_from_owner"
                continue

            # Employee matching / auto-create
            emp = await match_or_create_employee(name_raw, payout_phone, id_phone)

            if not emp:
                # IMPORTANT: amount detected but no employee. Money MUST remain
                # visible in the review queue. Do NOT insert into the immutable
                # ledger — that would corrupt accounting integrity.
                if _eligible_for_accounting_review(pdata):
                    await store_unmatched(
                        msg_id, "no_employee_match", row["raw_content"],
                        detected_amount=amount,
                        detected_payout_phone=payout_phone,
                        detected_employee_name=name_raw,
                        detected_payout_method=method_str,
                        detected_txn_date=txn_date,
                        parser_confidence=confidence,
                    )
                await mark_processing_status(msg_id, "failed", "employee match returned None")
                _diag_status, _diag_reason = "failed", "no_employee_match"
                continue

            # Create transaction
            try:
                method = PayoutMethod(method_str) if method_str in PayoutMethod._value2member_map_ else PayoutMethod.unknown
            except (ValueError, KeyError):
                method = PayoutMethod.unknown

            req = TransactionCreateRequest(
                fpe_wa_message_id=msg_id,
                employee_id=emp.employee_id,
                employee_name_raw=name_raw,
                amount=amount,
                payout_phone=payout_phone,
                payout_method=method,
                txn_date=txn_date,
                txn_category=TxnCategory.salary,
                source_message_text=row["raw_content"],
            )
            txn = await create_transaction(req)

            await mark_processing_status(msg_id, "done")
            log.info(
                "[fpe.worker.acct] done msg=%d emp=%d txn=%s amount=%s",
                msg_id, emp.employee_id, txn.txn_ref[:12], amount,
            )
            _diag_status = "done"

        except Exception as exc:
            _diag_reason = str(exc)[:200]
            log.error("[fpe.worker.acct] failed msg_id=%d: %s", msg_id, exc, exc_info=True)
            try:
                if _eligible_for_accounting_review(pdata):
                    await store_unmatched(
                        msg_id, "accounting_failed", row["raw_content"],
                        detected_amount=Decimal(pdata.get("amount")) if pdata.get("amount") else None,
                        detected_payout_phone=normalize_bd_phone(pdata.get("payout_phone")),
                        detected_employee_name=pdata.get("employee_name_raw"),
                        detected_payout_method=pdata.get("payout_method"),
                        parser_confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                    )
            except Exception as ue:
                log.debug("[fpe.worker.acct] store_unmatched failed: %s", ue)
            await mark_processing_status(msg_id, "failed", str(exc)[:500])
        finally:
            await record_outcome(
                msg_id=msg_id,
                msg_type=msg_type,
                worker_name="accounting",
                status=_diag_status,
                failure_reason=_diag_reason,
                processing_ms=(time.monotonic() - _t0) * 1000.0,
            )


# ── Escort payment handler ────────────────────────────────────────────────────

async def _handle_escort_payment(msg_id: int, pdata: dict, row: dict) -> None:
    """
    Create one fazle_payment_drafts row per escort entry in the parsed data.
    Marks the FPE message as 'done' after all drafts are created.
    """
    from app.database import execute as _execute

    entries = pdata.get("entries") or []
    shift = pdata.get("shift")
    duty_date_str = pdata.get("duty_date")
    source = row.get("source") or "bridge1"

    if not entries:
        await mark_processing_status(msg_id, "skipped")
        log.info("[fpe.worker.acct] escort_payment msg=%d has no entries — skipped", msg_id)
        return

    # Best-effort: try to link drafts to a matching escort program
    program_id: Optional[int] = None
    if duty_date_str:
        try:
            from datetime import date as _date
            _duty_date = _date.fromisoformat(duty_date_str)
            _prog = await fetch_one(
                """
                SELECT program_id FROM wbom_escort_programs
                WHERE program_date = $1
                  AND ($2::text IS NULL OR shift = $2)
                  AND status != 'Cancelled'
                ORDER BY assignment_time DESC
                LIMIT 1
                """,
                _duty_date, (shift or None),
            )
            if _prog:
                program_id = _prog["program_id"]
        except Exception as _pe:
            log.debug("[fpe.worker.acct] escort_program lookup skipped: %s", _pe)

    created = 0
    for entry in entries:
        name_raw = (entry.get("name") or "").strip()
        amount_str = entry.get("amount")
        if not name_raw or not amount_str:
            continue
        try:
            amount = Decimal(amount_str)
        except Exception:
            continue

        draft_text = f"{name_raw}={amount}/\nShift: {shift or '?'}  Date: {duty_date_str or '?'}"

        await _execute(
            """
            INSERT INTO fazle_payment_drafts
                (employee_name, expected_amount, draft_text, draft_type, source,
                 escort_program_id, status, created_at, updated_at)
            VALUES ($1, $2, $3, 'escort_payment', $4, $5, 'pending', now(), now())
            ON CONFLICT DO NOTHING
            """,
            name_raw, amount, draft_text, source, program_id,
        )
        created += 1

    await mark_processing_status(msg_id, "done")
    log.info(
        "[fpe.worker.acct] escort_payment done msg=%d drafts_created=%d program_id=%s",
        msg_id, created, program_id,
    )


# ── AI helpers ────────────────────────────────────────────────────────────────

def _ai_data_to_parse_result(ai_data: dict, content: str, msg_date):
    """Convert Ollama JSON response to a ParseResult."""
    from decimal import Decimal
    from .models import ParsedPayment, ParseResult, MessageType, PayoutMethod
    from .normalizer import normalize_bd_phone, normalize_payout_method

    raw_amount = ai_data.get("amount")
    amount = Decimal(str(raw_amount)) if raw_amount else None
    raw_phone = ai_data.get("payout_phone")
    phone = normalize_bd_phone(str(raw_phone)) if raw_phone else None
    raw_method = ai_data.get("payout_method") or "unknown"
    method_str = normalize_payout_method(raw_method)

    try:
        method = PayoutMethod(method_str)
    except ValueError:
        method = PayoutMethod.unknown

    confidence = float(ai_data.get("confidence", 0.7))

    if not amount:
        from .models import ParseResult, MessageType
        return ParseResult(message_type=MessageType.other, confidence=confidence)

    p = ParsedPayment(
        employee_name_raw=ai_data.get("employee_name"),
        payout_phone=phone,
        payout_method=method,
        amount=amount,
        txn_date=msg_date,
        confidence=confidence,
        raw_text=content,
    )
    return ParseResult(
        message_type=MessageType.payment,
        payment=p,
        confidence=confidence,
        ai_enhanced=True,
        ai_notes=ai_data.get("notes"),
    )
