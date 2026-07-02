#!/usr/bin/env python3
"""
DEPRECATED — One-time historical import script.

Import missing advance transactions from WhatsApp bridge2 SQLite DB
into wbom_cash_transactions.

This script was used for a one-time backfill of missing data from Jan/Apr 2026.
It writes to wbom_cash_transactions (legacy table). Per Owner Directive
(2026-06-29), fpe_cash_transactions is the ONLY canonical table. This script
should NOT be run again — use wbom_fpe_sync.py to migrate any remaining
wbom_cash_transactions rows into fpe_cash_transactions.

Missing periods:
  - Jan 22–31 2026  (10 days, ~166,888 BDT never loaded)
  - Apr 14–22 2026  (8 days,  ~69,502 BDT never loaded)

Source: Bridge-2 WhatsApp DB (Admin 8801880446111 ↔ Accountant 8801844836824)
        /home/azim/bridges/bridge2/store/messages.db
"""

import asyncio
import os
import re
import sqlite3
import sys
import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault(
    "DATABASE_URL", "postgresql://postgres:postgres@172.20.0.4:5432/postgres"
)

from modules.fazle_payroll_engine.normalizer import normalize_bd_phone as _norm_phone

BRIDGE2_DB = "/home/azim/bridges/bridge2/store/messages.db"
ACCOUNTANT_JID_FRAGMENT = "8801844836824"

# Missing date ranges to import
MISSING_RANGES = [
    ("2026-01-22", "2026-01-31"),
    ("2026-04-14", "2026-04-22"),
]

# ── Parsing helpers ───────────────────────────────────────────────────────────

# Phone: 01XXXXXXXXX (with optional dash/space), or +880...
_PHONE_RE = re.compile(
    r"(?:\+?880)?0?1[3-9][\d\s\-]{8,11}",
)

# Amount: handles "20*140=2800/-" → 2800, or "(B) 1000/-" → 1000
# Strategy: look for amount AFTER the payment method indicator to avoid
# phone number fragments (e.g. 01874-784767) being mistaken for amounts.
_MULT_AMT_RE = re.compile(r"\d+\s*\*\s*\d+\s*=\s*(\d+)\s*/?-?")
# Amount after method indicator: "(B) 900/-" or "(N) = 200 /-" or "cash 500/-"
_AFTER_METHOD_RE = re.compile(
    r"\([BbNnCc][^)]*\)\s*[=\s]*(\d{2,6})\s*/?\s*-"
    r"|cash\s+(\d{2,6})\s*/?\s*-"
    r"|[=\s](\d{2,6})\s*/\s*-",   # "= 500/-" with slash mandatory
    re.IGNORECASE,
)
_TRAILING_AMT_RE = re.compile(r"(\d{2,6})\s*/?-\s*$")

def _parse_amount(text: str) -> Optional[int]:
    # multiplication shorthand wins: "20*140=2800/-"
    m = _MULT_AMT_RE.search(text)
    if m:
        return int(m.group(1))
    # Amount after method indicator
    for m in _AFTER_METHOD_RE.finditer(text):
        for g in m.groups():
            if g and 50 <= int(g) <= 200_000:
                return int(g)
    # Trailing "500/-" or "500/-" at end of line
    m = _TRAILING_AMT_RE.search(text)
    if m:
        v = int(m.group(1))
        if 50 <= v <= 200_000:
            return v
    return None

# Payment method: (B) = Bkash, (N) = Nagad, (Cash) = Cash
_METHOD_RE = re.compile(
    r"\(([Bb]|[Nn]|[Cc]ash|bkash|nagad|[Bb]kash|[Nn]agad)\)",
    re.IGNORECASE,
)
_METHOD_MAP = {
    "b": "Bkash", "bkash": "Bkash",
    "n": "Nagad", "nagad": "Nagad",
    "cash": "Cash",
}

def _parse_method(text: str) -> str:
    m = _METHOD_RE.search(text)
    if m:
        return _METHOD_MAP.get(m.group(1).lower(), "Bkash")
    if re.search(r"\bcash\b", text, re.IGNORECASE):
        return "Cash"
    return ""

# Employee name: text before the first phone number or method indicator
_NAME_STOP_RE = re.compile(
    r"\s*(0[1-9][\d\s\-]{8,}|\+880|\(b\)|\(n\)|\(cash\))\s*",
    re.IGNORECASE,
)

def _parse_name(text: str) -> str:
    # strip leading "ID: PHONE " pattern
    text = re.sub(r"^ID\s*:\s*[\d\s\-]+", "", text, flags=re.IGNORECASE).strip()
    m = _NAME_STOP_RE.search(text)
    if m:
        return text[: m.start()].strip(" -")
    return text.strip()

# ── Parse one raw WhatsApp message into a list of transactions ────────────────

def parse_message(content: str, msg_date: date) -> list[dict]:
    """
    A single message may contain multiple payment lines (multiline bulk).
    Returns list of dicts with keys:
      name, phone, amount, method, date
    """
    results = []
    # Split on newlines; each line may be a separate payment
    lines = [l.strip() for l in content.strip().splitlines() if l.strip()]

    for line in lines:
        amount = _parse_amount(line)
        if not amount:
            continue
        # must have a payment method OR contain "cash"
        method = _parse_method(line)
        if not method:
            # skip lines without a payment method (e.g. Mamun's summary lines)
            continue
        # extract phone
        phones = _PHONE_RE.findall(line)
        phone = _norm_phone(phones[0]) if phones else None
        name = _parse_name(line)
        results.append(
            {
                "name": name,
                "phone": phone,
                "amount": amount,
                "method": method,
                "date": msg_date,
                "raw": line,
            }
        )
    return results


# ── DB helpers ────────────────────────────────────────────────────────────────

async def find_employee(pool, phone: Optional[str], name: str) -> Optional[int]:
    """
    Resolve employee_id by:
      1. Exact mobile match (last 10 digits)
      2. trgm name similarity ≥ 0.3
    Returns None if not found.
    """
    from app.database import fetch_one

    if phone:
        last10 = phone[-10:]
        row = await fetch_one(
            "SELECT employee_id FROM wbom_employees WHERE RIGHT(employee_mobile,10)=$1 LIMIT 1",
            last10,
        )
        if row:
            return row["employee_id"]

    # Fallback: name similarity
    if name and len(name) > 2:
        row = await fetch_one(
            """SELECT employee_id, employee_name,
                      similarity(LOWER(employee_name), LOWER($1)) AS s
               FROM wbom_employees
               WHERE similarity(LOWER(employee_name), LOWER($1)) >= 0.3
               ORDER BY s DESC LIMIT 1""",
            name,
        )
        if row:
            return row["employee_id"]

    return None


async def already_imported(pool, employee_id: int, txdate: date, amount: int, method: str) -> bool:
    """Check if an identical transaction was already imported (idempotency)."""
    from app.database import fetch_one
    row = await fetch_one(
        """SELECT 1 FROM wbom_cash_transactions
           WHERE employee_id=$1
             AND transaction_date=$2
             AND amount=$3
             AND payment_method=$4
             AND source='whatsapp_import'
           LIMIT 1""",
        employee_id, txdate, Decimal(amount), method,
    )
    return row is not None


async def insert_transaction(pool, tx: dict, employee_id: int, dry_run: bool) -> bool:
    """Insert a single transaction. Returns True if inserted."""
    from app.database import execute

    if dry_run:
        return True

    await execute(
        """INSERT INTO wbom_cash_transactions
             (employee_id, transaction_type, amount, payment_method, payment_mobile,
              transaction_date, source, remarks, is_reversed)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        employee_id,
        "advance",
        Decimal(tx["amount"]),
        tx["method"],
        tx["phone"],
        tx["date"],
        "whatsapp_import",
        f"Imported from accountant chat: {tx['raw'][:100]}",
        False,
    )
    return True


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(dry_run: bool = False, filter_date: Optional[str] = None):
    from app.database import init_db

    await init_db()
    print(f"{'[DRY RUN] ' if dry_run else ''}Importing missing advance transactions...")
    print()

    # Connect to bridge2 SQLite
    conn = sqlite3.connect(BRIDGE2_DB)
    conn.row_factory = sqlite3.Row

    # Build date filter
    date_clauses = []
    for start, end in MISSING_RANGES:
        date_clauses.append(f"(date(timestamp) BETWEEN '{start}' AND '{end}')")
    if filter_date:
        date_clauses = [f"date(timestamp) = '{filter_date}'"]
    date_sql = " OR ".join(date_clauses)

    cursor = conn.execute(
        f"""
        SELECT timestamp, content
        FROM messages
        WHERE chat_jid LIKE '%{ACCOUNTANT_JID_FRAGMENT}%'
          AND is_from_me = 1
          AND content IS NOT NULL
          AND content != ''
          AND ({date_sql})
        ORDER BY timestamp
        """
    )
    raw_messages = cursor.fetchall()
    conn.close()

    print(f"Found {len(raw_messages)} raw messages in bridge2 DB for missing dates.")
    print()

    inserted = 0
    skipped_no_employee = 0
    skipped_duplicate = 0
    skipped_no_payment = 0
    errors = []

    for row in raw_messages:
        ts_str = row["timestamp"]
        content = row["content"]

        # Parse timestamp to date (handle timezone offset)
        try:
            # SQLite stores as "2026-01-22 09:47:11+01:00"
            ts_str_clean = re.sub(r"([+-]\d{2}):(\d{2})$", r"+\1\2", ts_str)
            dt = datetime.fromisoformat(ts_str_clean.replace("+0100", "+01:00").replace("+0200", "+02:00"))
            tx_date = dt.date()
        except Exception:
            tx_date = date.fromisoformat(ts_str[:10])

        txns = parse_message(content, tx_date)

        if not txns:
            skipped_no_payment += 1
            continue

        for tx in txns:
            employee_id = await find_employee(None, tx["phone"], tx["name"])

            if employee_id is None:
                skipped_no_employee += 1
                print(
                    f"  ⚠️  No employee found: name={tx['name']!r} phone={tx['phone']} "
                    f"amt={tx['amount']} date={tx['date']}"
                )
                continue

            # Idempotency check
            if not dry_run and await already_imported(None, employee_id, tx["date"], tx["amount"], tx["method"]):
                skipped_duplicate += 1
                continue

            ok = await insert_transaction(None, tx, employee_id, dry_run)
            if ok:
                inserted += 1
                verb = "Would insert" if dry_run else "Inserted"
                print(
                    f"  ✅ {verb}: [{tx['date']}] emp={employee_id} "
                    f"{tx['name']!r} {tx['phone']} {tx['method']} {tx['amount']:,}/- "
                )

    print()
    print("=" * 60)
    print(f"{'DRY RUN ' if dry_run else ''}RESULTS:")
    print(f"  Inserted:              {inserted}")
    print(f"  Skipped (no employee): {skipped_no_employee}")
    print(f"  Skipped (duplicate):   {skipped_duplicate}")
    print(f"  Skipped (no payment):  {skipped_no_payment}")
    if errors:
        print(f"  Errors:                {len(errors)}")
        for e in errors[:5]:
            print(f"    {e}")

    # Final totals from DB after import
    if not dry_run:
        from app.database import fetch_one
        for start, end in MISSING_RANGES:
            row = await fetch_one(
                """SELECT COUNT(*) as n, COALESCE(SUM(amount),0) as total
                   FROM wbom_cash_transactions
                   WHERE transaction_date BETWEEN $1 AND $2
                     AND source='whatsapp_import'""",
                date.fromisoformat(start),
                date.fromisoformat(end),
            )
            print(f"  DB ({start}→{end}): {row['n']} rows, {int(row['total']):,} BDT imported")


def main():
    parser = argparse.ArgumentParser(description="Import missing advance transactions from WhatsApp bridge2 DB")
    parser.add_argument("--dry-run", action="store_true", help="Parse and match but do NOT write to DB")
    parser.add_argument("--date", help="Only import a specific date (YYYY-MM-DD)", default=None)
    args = parser.parse_args()

    asyncio.run(run(dry_run=args.dry_run, filter_date=args.date))


if __name__ == "__main__":
    main()
