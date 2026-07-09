"""
Fazle Payroll Engine — Unified message ingestion layer.

Accepts a message from any source (bridge1, bridge2, meta) and:
  1. Stores it in fpe_wa_messages (idempotent — unique on wa_message_id + source)
  2. Creates a pending fpe_message_processing_state row
  3. Optionally pushes the fpe_wa_messages.id into the in-process asyncio queue

The actual parsing and accounting is done by workers (workers.py), not here.
This layer is intentionally thin — fast ingest, deferred heavy processing.
"""
from __future__ import annotations

import re
import logging
from decimal import Decimal
from typing import Optional

from app.config import get_settings
from app.database import execute, fetch_one, fetch_val
from .normalizer import normalize_bd_phone
from .models import IngestionRequest

log = logging.getLogger("fazle.fpe.ingestion")

_PAYROLL_COMMAND_RE = re.compile(r"^\s*(cash|income)\b", re.IGNORECASE)


def _configured_sync_jids() -> set[str]:
    settings = get_settings()
    raw = getattr(settings, "fpe_sync_chat_jids", None) or ""
    jids = {jid.strip() for jid in raw.split(",") if jid.strip()}
    if not jids:
        accountant = getattr(settings, "accountant_phone", "") or ""
        if accountant:
            jids.add(f"{accountant}@s.whatsapp.net")
    return jids


def _is_authorized_payroll_sender(req: IngestionRequest, content: str) -> bool:
    settings = get_settings()
    match = _PAYROLL_COMMAND_RE.match(content or "")
    if not match:
        return False

    sender = normalize_bd_phone(req.sender_phone or "") or (req.sender_phone or "").strip()
    if not sender:
        return False

    command = match.group(1).lower()
    if command == "cash":
        return sender in settings.fpe_cash_authorized_phone_list
    if command == "income":
        return sender in settings.fpe_income_authorized_phone_list
    return False


def _should_enqueue_for_parsing(req: IngestionRequest, content: str) -> tuple[bool, str]:
    text = (content or "").strip()
    if not text:
        return False, "empty_content"

    if req.chat_jid in _configured_sync_jids():
        return True, ""

    if _is_authorized_payroll_sender(req, text):
        return True, ""

    return False, "non_payroll_or_unauthorized_source"


async def ingest_message(req: IngestionRequest) -> Optional[int]:
    """
    Insert a WhatsApp message into fpe_wa_messages.
    Returns the fpe_wa_messages.id, or None if this message_id+source already exists
    (idempotent — caller should treat None as "already processed, skip").
    """
    # Idempotency check
    existing = await fetch_one(
        "SELECT id FROM fpe_wa_messages WHERE wa_message_id = $1 AND source = $2",
        req.wa_message_id, req.source,
    )
    if existing:
        return None  # already ingested

    fpe_id: int = await fetch_val(
        """
        INSERT INTO fpe_wa_messages
            (wa_message_id, source, source_number, chat_jid, sender_phone,
             is_from_me, raw_content, media_type, timestamp_wa, ingested_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
        ON CONFLICT (wa_message_id, source) DO NOTHING
        RETURNING id
        """,
        req.wa_message_id,
        req.source,
        req.source_number,
        req.chat_jid,
        req.sender_phone,
        req.is_from_me,
        req.raw_content,
        req.media_type,
        req.timestamp_wa,
    )

    if fpe_id is None:
        # Race condition — another worker inserted first
        await fetch_one(
            "SELECT id FROM fpe_wa_messages WHERE wa_message_id = $1 AND source = $2",
            req.wa_message_id, req.source,
        )
        return None  # treat as duplicate

    # Create processing state row
    await execute(
        """
        INSERT INTO fpe_message_processing_state (fpe_wa_message_id, status, queued_at)
        VALUES ($1, 'pending', NOW())
        ON CONFLICT (fpe_wa_message_id) DO NOTHING
        """,
        fpe_id,
    )

    content = req.raw_content or ""
    should_enqueue, blocked_reason = _should_enqueue_for_parsing(req, content)
    if not should_enqueue:
        await mark_processing_status(fpe_id, "skipped", blocked_reason)
        log.info(
            "[fpe.ingest] blocked id=%d source=%s chat_jid=%s reason=%s",
            fpe_id, req.source, req.chat_jid, blocked_reason,
        )
        return fpe_id

    log.debug(
        "[fpe.ingest] ingested id=%d source=%s wa_id=%s from_me=%s",
        fpe_id, req.source, req.wa_message_id[:12], req.is_from_me,
    )
    return fpe_id


async def mark_processing_status(
    fpe_wa_message_id: int,
    status: str,
    error: Optional[str] = None,
) -> None:
    """Update the processing FSM status for a message."""
    await execute(
        """
        UPDATE fpe_message_processing_state
        SET status       = $1,
            last_error   = $2,
            attempts     = attempts + 1,
            processed_at = CASE WHEN $1 IN ('done','skipped','failed') THEN NOW() ELSE processed_at END
        WHERE fpe_wa_message_id = $3
        """,
        status, error, fpe_wa_message_id,
    )


async def store_parser_result(
    fpe_wa_message_id: int,
    message_type: str,
    parsed_data: dict,
    confidence: float,
    ai_enhanced: bool = False,
    ai_notes: Optional[str] = None,
) -> None:
    """Write parser result row."""
    import json
    await execute(
        """
        INSERT INTO fpe_parser_results
            (fpe_wa_message_id, message_type, parsed_data, confidence,
             ai_enhanced, ai_notes)
        VALUES ($1, $2, $3::jsonb, $4, $5, $6)
        """,
        fpe_wa_message_id,
        message_type,
        json.dumps(parsed_data),
        confidence,
        ai_enhanced,
        ai_notes,
    )


async def store_unmatched(
    fpe_wa_message_id: int,
    reason: str,
    raw_content: Optional[str],
    *,
    detected_amount: Optional["Decimal"] = None,
    detected_payout_phone: Optional[str] = None,
    detected_employee_name: Optional[str] = None,
    detected_payout_method: Optional[str] = None,
    detected_txn_date: Optional["date"] = None,
    parser_confidence: Optional[float] = None,
) -> None:
    """
    Record a message that could not be parsed or matched.

    For "Pending Accounting Candidates" we persist whatever the parser detected
    (amount, phone, name, method, date, confidence) so an operator can review
    and promote it to a real fpe_cash_transactions entry. The ledger itself is
    NEVER touched here — this row is review-queue only.
    """
    await execute(
        """
        INSERT INTO fpe_unmatched_messages
            (fpe_wa_message_id, reason, raw_content,
             detected_amount, detected_payout_phone, detected_employee_name,
             detected_payout_method, detected_txn_date, parser_confidence)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT DO NOTHING
        """,
        fpe_wa_message_id, reason, raw_content,
        detected_amount, detected_payout_phone, detected_employee_name,
        detected_payout_method, detected_txn_date, parser_confidence,
    )
