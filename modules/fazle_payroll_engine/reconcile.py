"""
Fazle Payroll Engine — Reconciliation invariant.

Accounting principle (final architecture):
    ledger_sum + unmatched_review_sum  ==  parser_detected_sum

Where:
    parser_detected_sum        = total amount the parser detected from
                                 WhatsApp/payment messages. This can be
                                 unavailable when no numeric parser totals
                                 were stored for the filtered scope.
    ledger_sum                 = total amount already posted to the verified
                                 ledger / cash transactions table.
    unmatched_review_sum       = amount still waiting in the unmatched/review
                                 queue and eligible for Accounting Review.

This invariant tolerates pending review work — it does NOT require all parsed
amounts to be in the ledger. It only guarantees that no detected money has
silently disappeared between the parser and the operator's queue + ledger.

Excludes already-promoted unmatched rows (review_status='promoted') because
those amounts are now counted in the ledger, and counting them twice would
inflate the right-hand side.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from app.database import fetch_one, fetch_val

log = logging.getLogger("fazle.fpe.reconcile")

# Tolerance for floating-point / rounding drift in the equality check.
TOLERANCE = Decimal("0.01")


async def compute_reconciliation(
    period: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """
    Compute the reconciliation snapshot.

    Filters:
      period  — accounting period 'YYYY-MM' (only for ledger; parser/unmatched
                are filtered by message month derived from fpe_wa_messages.timestamp_wa)
      source  — bridge1|bridge2|meta
    """
    where_period_msg = ""
    where_period_txn = ""
    where_source_msg = ""
    args: list[Any] = []

    if period:
        args.append(period)
        idx = len(args)
        where_period_msg = (
            f" AND to_char(m.timestamp_wa AT TIME ZONE 'UTC', 'YYYY-MM') = ${idx}"
        )
        where_period_txn = f" AND accounting_period = ${idx}"

    if source:
        args.append(source)
        idx = len(args)
        where_source_msg = f" AND m.source = ${idx}"

    parser_sum_q = f"""
        SELECT
            SUM((pr.parsed_data->>'amount')::numeric) AS parser_sum,
            COUNT(*) FILTER (WHERE (pr.parsed_data->>'amount') IS NOT NULL) AS parser_count
        FROM fpe_parser_results pr
        JOIN fpe_wa_messages m ON m.id = pr.fpe_wa_message_id
        WHERE pr.message_type = 'payment'
          AND (pr.parsed_data->>'amount') IS NOT NULL
          AND m.is_from_me = TRUE
          {where_period_msg}
          {where_source_msg}
    """

    ledger_sum_q = f"""
        SELECT COALESCE(SUM(amount), 0)
        FROM fpe_cash_transactions
        WHERE NOT is_reversal
        {where_period_txn}
    """
    ledger_args = [period] if period else []

    unmatched_sum_q = f"""
        SELECT COALESCE(SUM(u.detected_amount), 0)
        FROM fpe_unmatched_messages u
        JOIN fpe_wa_messages m ON m.id = u.fpe_wa_message_id
        WHERE u.review_status = 'pending'
          AND u.detected_amount IS NOT NULL
          AND COALESCE(BTRIM(u.detected_employee_name), '') <> ''
          AND COALESCE(BTRIM(u.detected_payout_phone), '') <> ''
          AND COALESCE(BTRIM(u.detected_payout_method), '') <> ''
          AND LOWER(u.detected_payout_method) <> 'unknown'
          {where_period_msg}
          {where_source_msg}
    """

    parser_row = await fetch_one(parser_sum_q, *args)
    ledger_sum: Decimal = await fetch_val(ledger_sum_q, *ledger_args) or Decimal("0")
    unmatched_sum: Decimal = await fetch_val(unmatched_sum_q, *args) or Decimal("0")
    if parser_row:
        parser_row = dict(parser_row)
        parser_sum = parser_row.get("parser_sum")
        parser_count = int(parser_row.get("parser_count") or 0)
    else:
        parser_sum = None
        parser_count = 0

    accounted = ledger_sum + unmatched_sum
    delta = (parser_sum - accounted) if parser_sum is not None and parser_count > 0 else None
    ok = abs(delta) <= TOLERANCE if delta is not None else None

    # Counts for operator dashboard
    pending_review = await fetch_val(
        "SELECT COUNT(*) FROM fpe_unmatched_messages "
        "WHERE review_status='pending' "
        "AND detected_amount IS NOT NULL "
        "AND COALESCE(BTRIM(detected_employee_name), '') <> '' "
        "AND COALESCE(BTRIM(detected_payout_phone), '') <> '' "
        "AND COALESCE(BTRIM(detected_payout_method), '') <> '' "
        "AND LOWER(detected_payout_method) <> 'unknown'"
    ) or 0
    dlq_count = await fetch_val(
        """
        SELECT COUNT(*) FROM fpe_message_processing_state
        WHERE status='failed' AND attempts >= 5
        """
    ) or 0

    return {
        "filter": {"period": period, "source": source},
        "parser_detected_sum": str(parser_sum) if parser_sum is not None and parser_count > 0 else None,
        "parser_total_available": parser_sum is not None and parser_count > 0,
        "parser_detected_count": parser_count,
        "ledger_sum": str(ledger_sum),
        "unmatched_review_sum": str(unmatched_sum),
        "accounted_sum": str(accounted),
        "delta": str(delta) if delta is not None else None,
        "tolerance": str(TOLERANCE),
        "ok": ok,
        "pending_review_count": int(pending_review),
        "dlq_count": int(dlq_count),
        "invariant": "ledger_sum + unmatched_review_sum == parser_detected_sum",
    }
