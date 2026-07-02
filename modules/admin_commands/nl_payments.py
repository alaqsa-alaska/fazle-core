"""
Fazle Core — Admin NL: payments handlers (Phase 1.2 / v1.1.0)

Reads canonical payments from `fpe_cash_transactions` joined with
`fpe_employees` for name resolution. No writes.

Public:
    intent_payments(text, admin_phone) -> reply str
    is_payments_query(text) -> bool

Triggers:
    today payments | আজকের পেমেন্ট | who got paid this week
    march payments | last 7 days payments | গত ৭ দিনের পেমেন্ট
    payments by 01XXXXXXXXX last month
"""
from __future__ import annotations

import logging
import re

from app.database import fetch_all, fetch_one
from .date_parser import parse_date_range
from .nl_router import extract_phone

log = logging.getLogger("fazle.admin_nl_pay")

_PAY_KEYWORDS = re.compile(
    r"\b(payments?|paid|cash|bkash|nagad|transactions?|"
    r"পেমেন্ট|পরিশোধ|ক্যাশ|বকেয়া|পেয়েছে)\b",
    re.IGNORECASE,
)

# ── Bengali → Latin transliteration (for cross-script name matching) ──────────
_BN_TRANS: dict[str, str] = {
    'ক': 'k', 'খ': 'kh', 'গ': 'g', 'ঘ': 'gh', 'ঙ': 'ng',
    'চ': 'ch', 'ছ': 'ch', 'জ': 'j', 'ঝ': 'jh',
    'ট': 't', 'ঠ': 'th', 'ড': 'd', 'ঢ': 'dh', 'ণ': 'n',
    'ত': 't', 'থ': 'th', 'দ': 'd', 'ধ': 'dh', 'ন': 'n',
    'প': 'p', 'ফ': 'f', 'ব': 'b', 'ভ': 'v', 'ম': 'm',
    'য': 'y', 'র': 'r', 'ল': 'l', 'শ': 'sh', 'ষ': 'sh',
    'স': 's', 'হ': 'h',
    'ড়': 'r', 'ঢ়': 'rh', 'য়': 'y',
    'অ': 'a', 'আ': 'a', 'ই': 'i', 'ঈ': 'i', 'উ': 'u', 'ঊ': 'u',
    'এ': 'e', 'ঐ': 'oi', 'ও': 'o', 'ঔ': 'ou',
    '\u09be': 'a',   # া
    '\u09bf': 'i',   # ি
    '\u09c0': 'i',   # ী
    '\u09c1': 'u',   # ু
    '\u09c2': 'u',   # ূ
    '\u09c3': 'ri',  # ৃ
    '\u09c7': 'e',   # ে
    '\u09c8': 'oi',  # ৈ
    '\u09cb': 'o',   # ো
    '\u09cc': 'ou',  # ৌ
    '\u09cd': '',    # ্ (virama — suppresses inherent vowel)
    '\u0982': 'n',   # ং
    '\u0983': 'h',   # ঃ
    '\u09bc': '',    # ় (nukta)
}


def _bn_to_latin(word: str) -> str:
    """Approximate Bengali → Latin transliteration for trgm fuzzy matching."""
    out: list[str] = []
    for ch in word:
        out.append(_BN_TRANS.get(ch, '' if '\u0980' <= ch <= '\u09FF' else ch))
    return ''.join(out)


# ── Employee totals intent ────────────────────────────────────────────────────
_PHONE_RE = re.compile(r"(?:\+?88)?(01[3-9]\d{8})")

# Triggers: "X নিয়েছে", "advance of <phone>", "total advance <phone>", etc.
_NIYECHE_RE = re.compile(r"নিয়েছে", re.UNICODE)  # no \b: Bengali boundary unreliable
_ADV_KEYWORD_RE = re.compile(r"\b(advance|অগ্রিম|মোট টাকা|total advance)\b", re.IGNORECASE)
_BN_WORD_RE = re.compile(r"[\u0980-\u09FF]{2,}")  # has Bengali chars → likely a name
# Capitalised proper nouns not in generic query stop list
_CAPITALIZED_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_NAME_STOP_WORDS = frozenset({
    "Today", "Yesterday", "This", "Last", "Week", "Month", "Year", "Day",
    "Find", "Search", "Show", "Get", "Tell", "List", "All", "Recent",
    "Cash", "Bkash", "Nagad", "Total", "Advance", "Payment", "Payments",
    "March", "April", "May", "June", "July", "August", "September",
    "October", "November", "December", "January", "February",
    "Chat", "Chats", "Message", "Messages", "History", "Salary",
})


def _has_latin_proper_name(text: str) -> bool:
    """True if text contains at least one capitalised proper noun not in the stop list."""
    caps = _CAPITALIZED_RE.findall(text)
    return any(w not in _NAME_STOP_WORDS for w in caps)


# Words to strip when extracting a person name
_NAME_NOISE_RE = re.compile(
    r"(?:মোট|কত|টাকা|নিয়েছে|পেয়েছে|দেওয়া\s+হয়েছে|অগ্রিম"
    r"|advance|total|find|search|of|এর|কে|সে|payment|পেমেন্ট"
    r"|লেনদেন|হিসাব|বিস্তারিত)",
    re.IGNORECASE | re.UNICODE,
)

# First financial split-point to isolate name prefix
_SPLIT_RE = re.compile(
    r"(?:মোট|কত\s+টাকা|টাকা|নিয়েছে|পেয়েছে|advance|total|অগ্রিম|find|search)",
    re.IGNORECASE | re.UNICODE,
)


def is_employee_totals_query(text: str) -> bool:
    """Detect name/phone-specific total payment queries."""
    # "X নিয়েছে" — 'has taken' in Bengali always implies a specific person
    if _NIYECHE_RE.search(text):
        return True
    if _ADV_KEYWORD_RE.search(text):
        # advance + phone number
        if _PHONE_RE.search(text):
            return True
        # advance + Bengali word (name)
        if _BN_WORD_RE.search(text):
            return True
        # advance + Latin proper noun (e.g. "Sujon total advance")
        if _has_latin_proper_name(text):
            return True
    return False


def _extract_employee_name(text: str) -> str | None:
    """Extract a person name from query text (multi-word Bengali or ASCII)."""
    # 1. Text before first financial keyword (name is usually before the query)
    m = _SPLIT_RE.search(text)
    candidate = text[: m.start()].strip() if m else text.strip()

    # 2. If empty, try text after "of" or after the first keyword
    if not candidate:
        of_m = re.search(r"\bof\b", text, re.IGNORECASE)
        if of_m:
            candidate = text[of_m.end() :].strip()
        elif m:
            candidate = text[m.end() :].strip()
            # strip a second financial word if present (e.g. "advance of ...")
            candidate = re.sub(r"^(?:of|এর)\s*", "", candidate, flags=re.IGNORECASE).strip()

    # 3. Strip residual noise and digits
    candidate = _NAME_NOISE_RE.sub(" ", candidate).strip()
    candidate = re.sub(r"\d+", " ", candidate).strip()
    candidate = re.sub(r"\s{2,}", " ", candidate).strip()

    # 4. Validate: must have at least 2 chars (or 2 tokens for better confidence)
    words = candidate.split()
    if not words:
        return None
    # Accept single word ≥3 chars or any multi-word candidate
    if len(words) >= 2:
        return " ".join(words[:3])
    if len(words[0]) >= 3:
        return words[0]
    return None


def is_payments_query(text: str) -> bool:
    return bool(_PAY_KEYWORDS.search(text))


async def intent_payments(text: str, admin_phone: str) -> str:
    rng = parse_date_range(text, default_days=1)  # default = today + last 24h
    assert rng is not None
    start, end, label = rng
    phone = extract_phone(text)

    sql_args: list = [start, end]
    where_phone = ""
    if phone:
        sql_args.append(phone)
        sql_args.append(phone[2:] if phone.startswith("88") else phone)
        where_phone = ("AND (t.payment_mobile = $3 OR t.payment_mobile = $4 "
                       "     OR e.employee_mobile = $3 OR e.employee_mobile = $4)")

    # C1B: read from canonical fpe_cash_transactions
    rows = await fetch_all(
        f"""
        SELECT t.id AS transaction_id, t.txn_date AS transaction_date, t.amount,
               t.payout_method AS payment_method, t.payout_phone AS payment_mobile,
               t.txn_category AS transaction_type, t.source,
               COALESCE(e.full_name, '') AS emp_name,
               COALESCE(e.primary_phone, '') AS emp_mobile
          FROM fpe_cash_transactions t
          LEFT JOIN fpe_employees e ON e.id = t.employee_id
         WHERE t.txn_date >= $1::date
           AND t.txn_date <  $2::date
           AND t.transaction_status = 'final'
           {where_phone}
         ORDER BY t.txn_date ASC, t.id ASC
        """,
        *sql_args,
    )

    if not rows:
        scope = f" {phone}" if phone else ""
        return f"💸 {label}{scope} — কোনো পেমেন্ট রেকর্ড নেই।"

    lines: list[str] = []
    total = 0.0
    by_method: dict[str, float] = {}
    for r in rows:
        amt = float(r["amount"] or 0)
        total += amt
        meth = (r["payment_method"] or "Cash").title()
        by_method[meth] = by_method.get(meth, 0.0) + amt
        d = r["transaction_date"].strftime("%d %b")
        mob = r["payment_mobile"] or r["emp_mobile"] or "-"
        nm = r["emp_name"] or ""
        nm_part = f" ({nm})" if nm else ""
        lines.append(f"{d} · {amt:,.0f} BDT · {meth} → {mob}{nm_part}")

    header = f"💸 {label}" + (f" · {phone}" if phone else "") + f" · {len(rows)} টি"
    method_summary = " · ".join(f"{m}: {v:,.0f}" for m, v in sorted(by_method.items()))
    body = (
        f"{header}\n"
        + "\n".join(lines)
        + f"\n\n📊 মোট: {total:,.0f} BDT\n   ({method_summary})"
    )
    return body


# ── Employee total lookup ─────────────────────────────────────────────────────

async def intent_employee_totals(text: str, admin_phone: str) -> str:
    """Return all-time payment total for a specific employee (by name or phone)."""
    phone = extract_phone(text)
    name = None if phone else _extract_employee_name(text)

    if not phone and not name:
        return "❌ কর্মীর নাম বা ফোন নম্বর পাইনি।"

    # ── Resolve employee_id from phone OR name ────────────────────────────────
    emp_row = None
    if phone:
        local = phone[2:] if phone.startswith("88") else phone
        emp_row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile FROM wbom_employees"
            " WHERE employee_mobile = $1 OR employee_mobile = $2 LIMIT 1",
            phone, local,
        )
        if not emp_row:
            # Also try payment_mobile match in transactions
            pass  # will do phone-based tx query below
    else:
        # Direct ILIKE (works for Latin names and exact Bengali matches)
        emp_row = await fetch_one(
            "SELECT employee_id, employee_name, employee_mobile FROM wbom_employees"
            " WHERE employee_name ILIKE $1 LIMIT 1",
            f"%{name}%",
        )
        if not emp_row:
            # Fallback: transliterate Bengali → Latin, use trgm similarity
            has_bn = bool(re.search(r"[\u0980-\u09FF]", name))
            if has_bn:
                words = name.split()
                latin_words = [_bn_to_latin(w) for w in words]
                # Sum similarity for each latin word against employee_name
                sim_expr = " + ".join(
                    f"similarity(LOWER(employee_name), ${i + 1})"
                    for i in range(len(latin_words))
                )
                emp_row = await fetch_one(
                    f"""SELECT employee_id, employee_name, employee_mobile,
                               ({sim_expr}) AS sim_score
                          FROM wbom_employees
                         ORDER BY sim_score DESC
                         LIMIT 1""",
                    *[w.lower() for w in latin_words],
                )
                if emp_row and float(emp_row.get("sim_score", 0)) < 0.25:
                    emp_row = None  # too weak a match

    # ── Build transaction query ───────────────────────────────────────────────
    sql_args: list
    where_clause: str
    if emp_row:
        eid = emp_row["employee_id"]
        sql_args = [eid]
        where_clause = "AND t.employee_id = $1"
    elif phone:
        local = phone[2:] if phone.startswith("88") else phone
        sql_args = [phone, local]
        where_clause = (
            "AND (e.employee_mobile = $1 OR e.employee_mobile = $2"
            " OR t.payment_mobile = $1 OR t.payment_mobile = $2)"
        )
    else:
        identifier = name or "?"
        return f"💸 \"{identifier}\" — কোনো কর্মী পাওয়া যায়নি।"

    # C1B: read from canonical fpe_cash_transactions
    rows = await fetch_all(
        f"""
        SELECT t.id AS transaction_id, t.txn_date AS transaction_date, t.amount,
               t.payout_method AS payment_method, t.txn_category AS transaction_type,
               t.source_message_text AS remarks,
               e.full_name AS employee_name, e.primary_phone AS employee_mobile
          FROM fpe_cash_transactions t
          JOIN fpe_employees e ON e.id = t.employee_id
         WHERE NOT t.is_reversal
               AND t.transaction_status = 'final'
               {where_clause}
         ORDER BY t.txn_date DESC, t.id DESC
         LIMIT 50
        """,
        *sql_args,
    )

    if not rows:
        identifier = (emp_row["employee_name"] if emp_row else phone or name) or "?"
        return f"💸 {identifier} — কোনো পেমেন্ট রেকর্ড নেই।"

    emp_name = rows[0]["employee_name"]
    emp_mobile = rows[0]["employee_mobile"]
    total = sum(float(r["amount"] or 0) for r in rows)

    by_method: dict[str, float] = {}
    lines: list[str] = []
    for r in rows[:20]:
        amt = float(r["amount"] or 0)
        meth = (r["payment_method"] or "Cash").title()
        by_method[meth] = by_method.get(meth, 0.0) + amt
        d = r["transaction_date"].strftime("%d %b %Y")
        txtype = (r["transaction_type"] or "").strip()
        type_part = f" [{txtype}]" if txtype else ""
        lines.append(f"• {d}: ৳{amt:,.0f} {meth}{type_part}")

    method_summary = " · ".join(f"{m}: ৳{v:,.0f}" for m, v in sorted(by_method.items()))
    overflow = f"\n  … আরো {len(rows) - 20}টি লেনদেন" if len(rows) > 20 else ""

    return (
        f"💸 {emp_name} ({emp_mobile})\n"
        f"{'─' * 30}\n"
        + "\n".join(lines)
        + overflow
        + f"\n\n📊 মোট: ৳{total:,.0f} BDT ({len(rows)}টি লেনদেন)\n"
        f"   {method_summary}"
    )
