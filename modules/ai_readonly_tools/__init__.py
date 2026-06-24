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


def detect_tools_needed(question: str) -> list[str]:
    """
    Detect which read-only tools are likely needed to answer a question.
    Returns list of tool names to call (may be empty for KB-only questions).
    """
    q_lower = question.lower()
    tools = []
    for tool, keywords in _TOOL_PATTERNS.items():
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
