"""
Fazle Core — Invoice Number Generator (Phase 2B, Commit A)

Deterministic, concurrency-safe AQS-YYYY-NNNNNN invoice numbers.

Backed by the invoice_number_counters table + next_invoice_number() SQL
function (db/migrations/033_invoice_number_counters.sql), not a raw Postgres
SEQUENCE — see that migration's header for why: a SEQUENCE's nextval() is
non-transactional, so a number consumed by a transaction that later rolls
back would be permanently lost (a gap). The counter table's row-locked
UPDATE participates normally in the caller's transaction.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import asyncpg

from app.database import fetch_val

_PREFIX = "AQS"


async def next_invoice_number(
    year: Optional[int] = None,
    conn: Optional[asyncpg.Connection] = None,
) -> str:
    """
    Reserve and return the next invoice number for the given year
    (defaults to the current year), formatted as AQS-YYYY-NNNNNN.

    Safe to call concurrently and inside an existing transaction — if the
    caller's transaction rolls back, the reservation rolls back with it.
    """
    y = year or date.today().year
    seq = await fetch_val("SELECT next_invoice_number($1)", y, conn=conn)
    return f"{_PREFIX}-{y}-{seq:06d}"
