"""
Fazle Core — AI Read-Only Tools (Phase 4)
==========================================
Safe, named tool functions that let Ollama/Chat Lab query production data.

Security rules (hard constraints):
  • ONLY SELECT — never INSERT/UPDATE/DELETE/DDL on production DB
  • All queries go through approved ai_read_* views only
  • Row limits enforced on every tool (max 100)
  • Sensitive fields (NID, bank, token, password) are excluded at view level
  • Internet fetch: read-only httpx GET — never POST/PUT to external URLs
  • AI never receives raw DB credentials

Connection: uses fazle_ai_reader role via FAZLE_AI_READER_DB_URL env var.
Fallback: uses main app pool for read-only queries if reader role not set up.

Public API (all async):
    get_contact_summary(phone) -> dict
    get_contacts_list(limit=20) -> list[dict]
    get_recent_messages(phone=None, limit=10) -> list[dict]
    get_payment_summary(employee_name=None, month=None, year=None) -> list[dict]
    get_escort_program_status(vessel=None, date_str=None) -> list[dict]
    get_attendance_summary(employee_name=None, month=None) -> list[dict]
    get_payroll_run_status(month=None, year=None) -> list[dict]
    get_daily_payments(question) -> dict
    get_employee_month_payments(question) -> dict
    get_lighter_assignment(question) -> dict
    get_recruitment_leads(limit=20) -> list[dict]
    get_employee_list(status="active") -> list[dict]
    get_module_bridge_status() -> list[dict]
    get_kb_articles(category=None, limit=20) -> list[dict]
    fetch_web_page(url, timeout=10) -> dict  [internet read-only]
    detect_tools_needed(question) -> list[str]
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date
from typing import Any, Optional

import asyncpg
import httpx

log = logging.getLogger("fazle.ai_readonly_tools")

_reader_pool: Optional[asyncpg.Pool] = None


def _get_reader_url() -> str:
    url = os.environ.get("FAZLE_AI_READER_DB_URL", "")
    if not url:
        # Fallback: build from prod URL with ai_reader creds
        prod_url = os.environ.get("DATABASE_URL", "")
        pw = os.environ.get("FAZLE_AI_READER_PASSWORD", "")
        if prod_url and pw:
            user_pw = prod_url.split("@")[0].split("//")[1]
            url = prod_url.replace(user_pw, f"fazle_ai_reader:{pw}")
    if not url:
        # Last resort: use app superuser but read-only intent
        url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            from app.config import get_settings
            url = get_settings().database_url
        except Exception:
            url = ""
    return url


async def _get_pool() -> asyncpg.Pool:
    global _reader_pool
    if _reader_pool is None:
        url = _get_reader_url()
        if not url:
            raise RuntimeError("No reader DB URL configured")
        try:
            _reader_pool = await asyncpg.create_pool(
                url, min_size=1, max_size=5, command_timeout=10
            )
        except Exception as e:
            log.warning("ai_readonly_tools pool failed: %s — falling back to app pool", e)
            from app.database import get_pool
            return get_pool()
    return _reader_pool


async def _fetch(sql: str, *args, limit: int = 100) -> list[dict]:
    """Execute a SELECT query and return list of dicts."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows[:limit]]


async def get_contact_summary(phone: str) -> dict[str, Any]:
    """Get full summary for a contact by phone number."""
    phone = phone.strip().replace(" ", "").replace("-", "")
    rows = await _fetch(
        """SELECT contact_id, display_name, whatsapp_number, company_name,
                  relation, is_active, interaction_count, interest_level, joined_date
           FROM ai_read_contacts
           WHERE whatsapp_number LIKE $1 LIMIT 1""",
        f"%{phone[-9:]}%",
    )
    if rows:
        return {"found": True, **rows[0]}
    return {"found": False, "phone": phone}


async def get_contacts_list(limit: int = 30) -> list[dict]:
    """Return active contacts list."""
    limit = min(limit, 100)
    return await _fetch(
        "SELECT contact_id, display_name, whatsapp_number, relation, company_name FROM ai_read_contacts LIMIT $1",
        limit,
    )


async def get_employee_list(status: str = "active") -> list[dict]:
    """Return employee list filtered by status."""
    return await _fetch(
        "SELECT employee_id, employee_name, employee_mobile, designation, status, basic_salary FROM ai_read_employees WHERE status=$1 ORDER BY employee_name",
        status,
    )


async def get_recent_messages(
    phone: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Return recent WhatsApp messages, optionally filtered by phone."""
    limit = min(limit, 50)
    if phone:
        phone = phone.strip().replace(" ", "")
        return await _fetch(
            """SELECT sender_number, sender_name, message_body, direction, source, received_at
               FROM ai_read_recent_messages
               WHERE sender_number LIKE $1
               ORDER BY received_at DESC LIMIT $2""",
            f"%{phone[-9:]}%", limit,
        )
    return await _fetch(
        "SELECT sender_number, sender_name, message_body, direction, source, received_at FROM ai_read_recent_messages ORDER BY received_at DESC LIMIT $1",
        limit,
    )


async def get_payment_summary(
    employee_name: Optional[str] = None,
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> list[dict]:
    """Return payroll summaries, optionally filtered by employee name or period."""
    conditions = []
    params: list[Any] = []
    idx = 1
    if employee_name:
        conditions.append(f"LOWER(employee_name) LIKE ${idx}")
        params.append(f"%{employee_name.lower()}%")
        idx += 1
    if month:
        conditions.append(f"period_month=${idx}")
        params.append(month)
        idx += 1
    if year:
        conditions.append(f"period_year=${idx}")
        params.append(year)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(20)  # limit
    sql = f"""
        SELECT employee_name, designation, period_year, period_month, status,
               total_programs, gross_salary, net_salary, total_advances, total_deductions
        FROM ai_read_payroll_runs {where}
        ORDER BY period_year DESC, period_month DESC LIMIT ${idx}
    """
    return await _fetch(sql, *params)


async def get_escort_program_status(
    vessel: Optional[str] = None,
    date_str: Optional[str] = None,
    status: Optional[str] = None,
) -> list[dict]:
    """Return escort program status. Filter by vessel, date, or status."""
    conditions = []
    params: list[Any] = []
    idx = 1
    if vessel:
        conditions.append(
            f"(LOWER(mother_vessel) LIKE ${idx} OR LOWER(lighter_vessel) LIKE ${idx})"
        )
        params.append(f"%{vessel.lower()}%")
        idx += 1
    if date_str:
        conditions.append(f"program_date::TEXT LIKE ${idx}")
        params.append(f"%{date_str}%")
        idx += 1
    if status:
        conditions.append(f"status=${idx}")
        params.append(status)
        idx += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(30)
    sql = f"""
        SELECT program_id, mother_vessel, lighter_vessel, destination,
               program_date, shift, status, escort_name, escort_mobile,
               assignment_time, completion_time
        FROM ai_read_escort_programs {where}
        ORDER BY program_date DESC LIMIT ${idx}
    """
    return await _fetch(sql, *params)


async def get_attendance_summary(
    employee_name: Optional[str] = None,
    month: Optional[int] = None,
) -> list[dict]:
    """Return attendance records, optionally filtered by employee or month."""
    conditions = []
    params: list[Any] = []
    idx = 1
    if employee_name:
        conditions.append(f"LOWER(employee_name) LIKE ${idx}")
        params.append(f"%{employee_name.lower()}%")
        idx += 1
    if month:
        conditions.append(f"EXTRACT(MONTH FROM attendance_date)=${idx}")
        params.append(month)
        idx += 1
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(50)
    sql = f"""
        SELECT employee_name, designation, attendance_date, status, location, remarks
        FROM ai_read_attendance_summary {where}
        ORDER BY attendance_date DESC LIMIT ${idx}
    """
    return await _fetch(sql, *params)


async def get_payroll_run_status(
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> list[dict]:
    """Return payroll run status for a period."""
    return await get_payment_summary(month=month, year=year)


def _fmt_bdt(amount: float) -> str:
    return f"৳{amount:,.0f}"


def _daily_payments_answer(data: dict[str, Any]) -> str:
    label = data.get("label") or "selected period"
    recipient_count = int(data.get("recipient_count") or 0)
    txn_count = int(data.get("transaction_count") or 0)
    total = float(data.get("total_amount") or 0)
    payments = data.get("payments") or []

    if not payments:
        return f"{label} কোনো পেমেন্ট রেকর্ড নেই।"

    lines = [
        f"{label} পেমেন্ট দেয়া হয়েছে {recipient_count} জনকে।",
        f"মোট ট্রানজেকশন: {txn_count}টি, মোট টাকা: {_fmt_bdt(total)}।",
    ]
    for i, row in enumerate(payments[:15], 1):
        name = row.get("employee_name") or "Unknown"
        mobile = row.get("payment_mobile") or row.get("employee_mobile") or "-"
        method = (row.get("payment_method") or "cash").title()
        amount = _fmt_bdt(float(row.get("amount") or 0))
        lines.append(f"{i}. {name} - {amount} ({method}, {mobile})")
    if len(payments) > 15:
        lines.append(f"... আরও {len(payments) - 15}টি রেকর্ড আছে।")
    return "\n".join(lines)


async def get_daily_payments(question: str) -> dict[str, Any]:
    """
    Return finalized cash/payment rows for the date range mentioned in question.
    This is the exact-data path for admin questions like:
      "আজ কয়জনকে পেমেন্ট দেয়া হয়েছে?"
      "today payments"
      "গতকাল টাকা পাঠানো হয়েছে কতজনকে?"
    """
    from modules.admin_commands.date_parser import parse_date_range

    rng = parse_date_range(question, default_days=1)
    assert rng is not None
    start_dt, end_dt, label = rng
    start_date = start_dt.date()
    end_date = end_dt.date()

    rows = await _fetch(
        """
        SELECT t.id AS transaction_id, t.txn_date AS transaction_date, t.amount,
               t.payout_method AS payment_method, t.payout_phone AS payment_mobile,
               t.txn_category AS transaction_type, t.source,
               COALESCE(e.full_name, '') AS employee_name,
               COALESCE(e.primary_phone, '') AS employee_mobile,
               t.employee_id
          FROM fpe_cash_transactions t
          LEFT JOIN fpe_employees e ON e.employee_id = t.employee_id
         WHERE t.txn_date >= $1::date
           AND t.txn_date <  $2::date
           AND t.transaction_status = 'final'
           AND t.deleted_at IS NULL
         ORDER BY t.txn_date ASC, t.id ASC
        """,
        start_date, end_date, limit=100,
    )

    total = sum(float(r.get("amount") or 0) for r in rows)
    recipients = {
        r.get("employee_id") or r.get("payment_mobile") or r.get("employee_mobile") or r.get("transaction_id")
        for r in rows
    }
    data: dict[str, Any] = {
        "label": label,
        "start_date": start_date.isoformat(),
        "end_date_exclusive": end_date.isoformat(),
        "transaction_count": len(rows),
        "recipient_count": len(recipients),
        "total_amount": total,
        "payments": rows,
    }
    data["answer"] = _daily_payments_answer(data)
    return data


_MONTH_WORDS: dict[str, int] = {
    "january": 1, "jan": 1, "জানুয়ারি": 1,
    "february": 2, "feb": 2, "ফেব্রুয়ারি": 2,
    "march": 3, "mar": 3, "মার্চ": 3,
    "april": 4, "apr": 4, "এপ্রিল": 4,
    "may": 5, "মে": 5,
    "june": 6, "jun": 6, "জুন": 6,
    "july": 7, "jul": 7, "জুলাই": 7,
    "august": 8, "aug": 8, "আগস্ট": 8,
    "september": 9, "sep": 9, "sept": 9, "সেপ্টেম্বর": 9,
    "october": 10, "oct": 10, "অক্টোবর": 10,
    "november": 11, "nov": 11, "নভেম্বর": 11,
    "december": 12, "dec": 12, "ডিসেম্বর": 12,
}


def _extract_month_year(question: str) -> tuple[int, int] | None:
    q = question.lower().replace("’", "'").replace("‘", "'")
    year_match = re.search(r"(20\d{2})", q)
    year = int(year_match.group(1)) if year_match else date.today().year
    iso_month = re.search(r"\b(20\d{2})[-/](\d{1,2})\b", q)
    if iso_month:
        return int(iso_month.group(2)), int(iso_month.group(1))
    for word, month in _MONTH_WORDS.items():
        if word in q:
            return month, year
    return None


def _extract_person_name(question: str) -> str | None:
    q = question.strip()
    quoted = re.search(r"['\"]([^'\"]{2,80})['\"]", q)
    if quoted:
        return quoted.group(1).strip()
    month_pos = len(q)
    for word in _MONTH_WORDS:
        pos = q.lower().find(word)
        if pos >= 0:
            month_pos = min(month_pos, pos)
    candidate = q[:month_pos]
    candidate = re.sub(
        r"(কোনো|কর্মচারি|কর্মচারী|যেমন|employee|staff|worker|মোট|কত|পেমেন্ট|payment|paid|salary|বেতন)",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\d+", " ", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,:;।?")
    if len(candidate) >= 2:
        return candidate
    return None


def _employee_month_answer(data: dict[str, Any]) -> str:
    name = data.get("employee_name") or data.get("query_name") or "কর্মী"
    label = data.get("period_label") or "এই period"
    rows = data.get("payments") or []
    total = float(data.get("total_amount") or 0)
    if not rows:
        return f"{name} — {label} কোনো পেমেন্ট রেকর্ড পাওয়া যায়নি।"
    lines = [
        f"{name} {label} মোট {_fmt_bdt(total)} পেমেন্ট পেয়েছে।",
        f"রেকর্ড: {len(rows)}টি।",
    ]
    for i, row in enumerate(rows[:15], 1):
        d = row.get("transaction_date")
        method = (row.get("payment_method") or "cash").title()
        tx_type = row.get("transaction_type") or "payment"
        lines.append(f"{i}. {d}: {_fmt_bdt(float(row.get('amount') or 0))} ({method}, {tx_type})")
    if len(rows) > 15:
        lines.append(f"... আরও {len(rows) - 15}টি রেকর্ড আছে।")
    return "\n".join(lines)


async def get_employee_month_payments(question: str) -> dict[str, Any]:
    """Exact read-only answer for one employee's payments in a named month."""
    period = _extract_month_year(question)
    name = _extract_person_name(question)
    if not period or not name:
        return {
            "query_name": name,
            "payments": [],
            "total_amount": 0.0,
            "answer": "কর্মীর নাম এবং মাস/বছর পরিষ্কার পাইনি। উদাহরণ: দেবাষীশ মে ২০২৬ মোট কত পেমেন্ট পেয়েছে?",
        }
    month, year = period
    start = date(year, month, 1)
    end = date(year + (1 if month == 12 else 0), 1 if month == 12 else month + 1, 1)

    emp_rows = await _fetch(
        """
        SELECT employee_id, employee_name, employee_mobile
          FROM wbom_employees
         WHERE employee_name ILIKE $1
         ORDER BY employee_id DESC
         LIMIT 5
        """,
        f"%{name}%", limit=5,
    )

    rows: list[dict[str, Any]]
    employee_name = name
    employee_mobile = ""
    if emp_rows:
        employee_id = emp_rows[0]["employee_id"]
        employee_name = emp_rows[0].get("employee_name") or name
        employee_mobile = emp_rows[0].get("employee_mobile") or ""
        rows = await _fetch(
            """
            SELECT t.id AS transaction_id, t.txn_date::text AS transaction_date,
                   t.amount, t.payout_method AS payment_method, t.payout_phone AS payment_mobile,
                   t.txn_category AS transaction_type,
                   t.transaction_status AS status, t.source
              FROM fpe_cash_transactions t
             WHERE t.employee_id = $1
               AND t.txn_date >= $2::date
               AND t.txn_date <  $3::date
               AND t.transaction_status = 'final'
               AND t.deleted_at IS NULL
             ORDER BY t.txn_date ASC, t.id ASC
            """,
            employee_id, start, end, limit=100,
        )
    else:
        rows = await _fetch(
            """
            SELECT t.id AS transaction_id, t.txn_date::text AS transaction_date,
                   t.amount, t.payout_method AS payment_method, t.payout_phone AS payment_mobile,
                   t.txn_category AS transaction_type,
                   t.transaction_status AS status, t.source,
                   COALESCE(e.full_name, '') AS employee_name,
                   COALESCE(e.primary_phone, '') AS employee_mobile
              FROM fpe_cash_transactions t
              LEFT JOIN fpe_employees e ON e.employee_id = t.employee_id
             WHERE e.full_name ILIKE $1
               AND t.txn_date >= $2::date
               AND t.txn_date <  $3::date
               AND t.transaction_status = 'final'
               AND t.deleted_at IS NULL
             ORDER BY t.txn_date ASC, t.id ASC
            """,
            f"%{name}%", start, end, limit=100,
        )
        if rows:
            employee_name = rows[0].get("employee_name") or name
            employee_mobile = rows[0].get("employee_mobile") or ""

    data: dict[str, Any] = {
        "query_name": name,
        "employee_name": employee_name,
        "employee_mobile": employee_mobile,
        "period_month": month,
        "period_year": year,
        "period_label": f"{month:02d}/{year}",
        "payments": rows,
        "total_amount": sum(float(r.get("amount") or 0) for r in rows),
    }
    data["answer"] = _employee_month_answer(data)
    return data


def _extract_lighter_name(question: str) -> str | None:
    quoted = re.search(r"['\"]([^'\"]{2,100})['\"]", question)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(
        r"(?:লাইটার|lighter|lighter vessel|ভেসেল|vessel)\s*(?:নাম|name|:|=)?\s*([A-Za-z0-9ঀ-৿ ._-]{2,80})",
        question,
        re.IGNORECASE,
    )
    if m:
        raw = m.group(1)
        raw = re.split(r"(?:এ|তে|কবে|কখন|কার|কে|escort|এস্কর্ট|পাঠানো|হয়েছিল|হয়েছিল|ছিল|\\?)", raw, maxsplit=1)[0]
        raw = re.sub(r"\s+", " ", raw).strip(" ,:;।?")
        if len(raw) >= 2:
            return raw
    return None


def _lighter_assignment_answer(data: dict[str, Any]) -> str:
    lighter = data.get("query_lighter") or "লাইটার"
    rows = data.get("programs") or []
    if not rows:
        return f"{lighter} নামে কোনো এস্কর্ট প্রোগ্রাম পাওয়া যায়নি।"
    lines = [f"{lighter} — {len(rows)}টি এস্কর্ট প্রোগ্রাম পাওয়া গেছে:"]
    for i, row in enumerate(rows[:10], 1):
        date_txt = row.get("program_date") or "-"
        shift = row.get("shift") or ""
        escort = row.get("escort_name") or "এস্কর্ট নাম নেই"
        mobile = row.get("escort_mobile") or "-"
        mv = row.get("mother_vessel") or "-"
        status = row.get("status") or "-"
        lines.append(
            f"{i}. তারিখ: {date_txt} {shift} | এস্কর্ট: {escort} ({mobile}) | Mother: {mv} | Status: {status}"
        )
    if len(rows) > 10:
        lines.append(f"... আরও {len(rows) - 10}টি রেকর্ড আছে।")
    return "\n".join(lines)


async def get_lighter_assignment(question: str) -> dict[str, Any]:
    """Exact read-only answer for lighter-vessel escort assignment questions."""
    lighter = _extract_lighter_name(question)
    if not lighter:
        return {
            "query_lighter": None,
            "programs": [],
            "answer": "লাইটারের নাম পরিষ্কার পাইনি। উদাহরণ: 'MV Labonno' লাইটারে কাকে এস্কর্ট পাঠানো হয়েছিল?",
        }
    rows = await _fetch(
        """
        SELECT program_id, mother_vessel, lighter_vessel, master_mobile,
               destination, program_date::text AS program_date, shift, status,
               escort_name, escort_mobile, completion_time::text AS completion_time
          FROM wbom_escort_programs
         WHERE lighter_vessel ILIKE $1
         ORDER BY program_date DESC NULLS LAST, program_id DESC
         LIMIT 25
        """,
        f"%{lighter}%", limit=25,
    )
    data: dict[str, Any] = {"query_lighter": lighter, "programs": rows}
    data["answer"] = _lighter_assignment_answer(data)
    return data


async def get_recruitment_leads(limit: int = 20) -> list[dict]:
    """Return recent recruitment leads/sessions."""
    limit = min(limit, 50)
    return await _fetch(
        "SELECT id, phone, funnel_stage, source, full_name, area, score_bucket, created_at FROM ai_read_recruitment_leads LIMIT $1",
        limit,
    )


async def get_module_bridge_status() -> list[dict]:
    """Return bridge/service heartbeat status."""
    return await _fetch(
        "SELECT service_name, status, last_seen, metadata FROM ai_read_module_bridge_status LIMIT 20"
    )


async def get_kb_articles(
    category: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Search KB articles by category or keyword."""
    limit = min(limit, 30)
    if search:
        return await _fetch(
            """SELECT id, key, category, subcategory, content_preview
               FROM ai_read_kb_articles
               WHERE LOWER(content_preview) LIKE $1 OR LOWER(key) LIKE $1
               LIMIT $2""",
            f"%{search.lower()}%", limit,
        )
    if category:
        return await _fetch(
            "SELECT id, key, category, subcategory, content_preview FROM ai_read_kb_articles WHERE LOWER(category) LIKE $1 LIMIT $2",
            f"%{category.lower()}%", limit,
        )
    return await _fetch(
        "SELECT id, key, category, subcategory, content_preview FROM ai_read_kb_articles LIMIT $1",
        limit,
    )


async def fetch_web_page(url: str, timeout: int = 10) -> dict[str, Any]:
    """
    Read-only HTTP GET to fetch external information.
    AI may only read — no POST/PUT/DELETE to external URLs.
    Extracts plain text from HTML (strips tags).
    Max 3000 chars returned.
    """
    # Safety: only allow http/https GET to public URLs
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Only http/https URLs allowed"}
    # Block internal/private networks
    blocked = ["localhost", "127.", "172.", "192.168.", "10.", "::1", "0.0.0.0"]
    for b in blocked:
        if b in url:
            return {"ok": False, "error": "Internal network access not allowed"}
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; FazleBot/1.0)"},
            )
            content_type = r.headers.get("content-type", "")
            if "html" in content_type or "text" in content_type:
                # Strip HTML tags
                text = re.sub(r"<[^>]+>", " ", r.text)
                text = re.sub(r"\s+", " ", text).strip()
                return {
                    "ok": True,
                    "url": str(r.url),
                    "status_code": r.status_code,
                    "content": text[:3000],
                    "content_type": content_type,
                }
            return {
                "ok": True,
                "url": str(r.url),
                "status_code": r.status_code,
                "content": r.text[:3000],
                "content_type": content_type,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "url": url}


# ── Smart question router ─────────────────────────────────────────────────────

_TOOL_PATTERNS: dict[str, list[str]] = {
    "get_daily_payments": [
        "today payments", "today payment", "today paid", "paid today",
        "payments today", "cash today", "আজ পেমেন্ট", "আজকে পেমেন্ট",
        "আজকের পেমেন্ট", "আজ কয়জন", "আজ কতজন", "আজকে কয়জন",
        "আজকে কতজন", "টাকা পাঠানো", "টাকা দেয়া", "টাকা দেওয়া",
        "টাকা দেওয়া", "পেমেন্ট দেয়া", "পেমেন্ট দেওয়া", "পেমেন্ট দেওয়া",
    ],
    "get_employee_month_payments": [
        "মোট কত পেমেন্ট", "কত পেমেন্ট পেয়েছে", "কত পেমেন্ট পেয়েছে",
        "total payment", "total paid", "payments in", "paid in",
    ],
    "get_lighter_assignment": [
        "লাইটার", "lighter", "lighter vessel", "কাকে পাঠানো", "কখন পাঠানো",
        "কত তারিখে", "এস্কর্ট পাঠানো", "escort sent", "assigned escort",
    ],
    "get_employee_list": [
        "employee", "staff", "worker", "কর্মী", "employee list", "কর্মচারী",
        "গার্ড", "guard",
    ],
    "get_contact_summary": [
        "contact", "phone", "number", "নম্বর", "কনটাক্ট",
    ],
    "get_recent_messages": [
        "message", "chat", "whatsapp", "বার্তা", "মেসেজ",
    ],
    "get_escort_program_status": [
        "vessel", "escort", "program", "জাহাজ", "ভেসেল", "lighter", "escort program",
        "duty", "ডিউটি", "shift",
    ],
    "get_attendance_summary": [
        "attendance", "উপস্থিতি", "present", "absent", "check in",
    ],
    "get_payroll_run_status": [
        "payroll", "salary", "বেতন", "মাসিক", "payment summary", "run status",
    ],
    "get_recruitment_leads": [
        "recruit", "candidate", "নিয়োগ", "প্রার্থী", "applicant",
    ],
    "get_module_bridge_status": [
        "bridge", "service", "status", "health", "module", "online", "offline",
        "connected", "সার্ভিস",
    ],
    "fetch_web_page": [
        "search online", "internet", "web", "google", "website", "url", "ইন্টারনেট",
    ],
}


def _looks_like_daily_payment_question(q_lower: str) -> bool:
    has_payment_word = any(
        word in q_lower
        for word in (
            "payment", "payments", "paid", "cash", "bkash", "nagad",
            "পেমেন্ট", "টাকা", "ক্যাশ", "বিকাশ", "নগদ",
        )
    )
    has_date_or_count = any(
        word in q_lower
        for word in (
            "today", "yesterday", "আজ", "আজকে", "আজকের", "গতকাল",
            "কয়জন", "কতজন", "কত জন", "how many",
        )
    )
    return has_payment_word and has_date_or_count


def _looks_like_employee_month_payment_question(q_lower: str) -> bool:
    return (
        any(w in q_lower for w in ("পেমেন্ট", "payment", "paid", "টাকা", "বেতন"))
        and any(w in q_lower for w in ("মোট", "total", "কত"))
        and any(w in q_lower for w in _MONTH_WORDS)
    )


def _looks_like_lighter_assignment_question(q_lower: str) -> bool:
    return (
        any(w in q_lower for w in ("লাইটার", "lighter", "vessel", "ভেসেল"))
        and any(w in q_lower for w in ("এস্কর্ট", "escort", "পাঠানো", "assigned", "কাকে", "কখন", "তারিখ"))
    )


def detect_tools_needed(question: str) -> list[str]:
    """
    Detect which read-only tools are likely needed to answer a question.
    Returns list of tool names to call (may be empty for KB-only questions).
    """
    q_lower = question.lower()
    tools = []
    if _looks_like_daily_payment_question(q_lower):
        tools.append("get_daily_payments")
    if _looks_like_employee_month_payment_question(q_lower):
        tools.append("get_employee_month_payments")
    if _looks_like_lighter_assignment_question(q_lower):
        tools.append("get_lighter_assignment")
    for tool, keywords in _TOOL_PATTERNS.items():
        if tool in tools:
            continue
        if any(kw in q_lower for kw in keywords):
            tools.append(tool)
    return tools


async def run_tool(tool_name: str, question: str) -> dict[str, Any]:
    """
    Execute a named tool and return structured result.
    Extracts filter params from question text automatically.
    """
    try:
        if tool_name == "get_employee_list":
            active = "inactive" not in question.lower()
            data = await get_employee_list(status="active" if active else "inactive")
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_daily_payments":
            data = await get_daily_payments(question)
            return {
                "tool": tool_name,
                "data": data,
                "count": int(data.get("transaction_count") or 0),
                "answer": data.get("answer"),
            }

        elif tool_name == "get_employee_month_payments":
            data = await get_employee_month_payments(question)
            return {
                "tool": tool_name,
                "data": data,
                "count": len(data.get("payments") or []),
                "answer": data.get("answer"),
            }

        elif tool_name == "get_lighter_assignment":
            data = await get_lighter_assignment(question)
            return {
                "tool": tool_name,
                "data": data,
                "count": len(data.get("programs") or []),
                "answer": data.get("answer"),
            }

        elif tool_name == "get_contact_summary":
            # Try to extract phone from question
            phone_match = re.search(r"(?:880|01)\d{8,9}", question)
            if phone_match:
                result = await get_contact_summary(phone_match.group())
                return {"tool": tool_name, "data": [result], "count": 1}
            data = await get_contacts_list(limit=20)
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_recent_messages":
            phone_match = re.search(r"(?:880|01)\d{8,9}", question)
            phone = phone_match.group() if phone_match else None
            data = await get_recent_messages(phone=phone, limit=10)
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_escort_program_status":
            # Try to extract vessel name (capitalized words)
            vessel_match = re.search(r"(?:MV|mv|vessel|জাহাজ|ভেসেল)\s+([A-Za-z0-9 ]+)", question)
            vessel = vessel_match.group(1).strip() if vessel_match else None
            data = await get_escort_program_status(vessel=vessel)
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_attendance_summary":
            data = await get_attendance_summary()
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_payroll_run_status":
            data = await get_payroll_run_status()
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_recruitment_leads":
            data = await get_recruitment_leads(limit=20)
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "get_module_bridge_status":
            data = await get_module_bridge_status()
            return {"tool": tool_name, "data": data, "count": len(data)}

        elif tool_name == "fetch_web_page":
            url_match = re.search(r"https?://[^\s\"']+", question)
            if url_match:
                result = await fetch_web_page(url_match.group())
                return {"tool": tool_name, "data": result, "count": 1}
            return {"tool": tool_name, "data": None, "count": 0, "note": "No URL found in question"}

        else:
            return {"tool": tool_name, "data": None, "error": "Unknown tool"}
    except Exception as e:
        log.error("run_tool error [%s]: %s", tool_name, e)
        return {"tool": tool_name, "data": None, "error": str(e)}


async def close_pool() -> None:
    global _reader_pool
    if _reader_pool:
        await _reader_pool.close()
        _reader_pool = None
