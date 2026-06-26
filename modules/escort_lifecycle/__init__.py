"""
Fazle Core — Escort Lifecycle (Batch 13)

Closes the loop:
  release intent (text or image OCR)
    → find_active_program_for_employee()
    → close_program()         (idempotent: status='Completed')
    → backfill_attendance_for_program()
    → create_escort_payment_draft()  (Batch 12 bridge)
    → admin notification dict

All operations idempotent. Re-running on a closed program returns
{already_closed:True} without creating duplicate drafts.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Optional

from app.database import fetch_one, fetch_all, execute, fetch_val, get_pool
from shared.draft_reply import create_draft_reply
from modules.payment_workflow import create_escort_payment_draft
from modules.escort_roster.calculations import count_shifts, shifts_to_days

log = logging.getLogger("fazle.escort_lifecycle")

# ── Release intent detection ──────────────────────────────────────────────────

RELEASE_KEYWORDS_BN = [
    "ডিউটি শেষ", "ডিউটি বন্ধ", "রিলিজ", "রিলিজ হয়েছি", "ছুটি দিন",
    "পেমেন্ট দেন", "অফ দিন", "শেষ হয়েছে", "ফিরে এসেছি",
    "কাজ শেষ", "ভেসেল ছেড়েছি", "প্রোগ্রাম শেষ",
]
RELEASE_KEYWORDS_EN = [
    "release", "released", "duty done", "duty finished", "duty completed",
    "off duty", "back home", "program completed", "vessel done",
]
# These are keywords that override is_advance_request (which is broader)
_ALL_RELEASE = [k.lower() for k in RELEASE_KEYWORDS_BN + RELEASE_KEYWORDS_EN]


def is_release_intent(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(kw in t for kw in _ALL_RELEASE)


# ── Program lookup ────────────────────────────────────────────────────────────

async def find_active_program_for_employee(
    employee_id: int,
    on_or_before: Optional[date] = None,
    conn=None,
) -> Optional[dict]:
    """Latest non-completed program for this employee whose program_date <= ref_date."""
    ref = on_or_before or date.today()
    row = await fetch_one(
        """SELECT program_id, mother_vessel, lighter_vessel, escort_employee_id,
                  escort_mobile, program_date, shift, status, start_date, end_date,
                  end_shift, release_point, day_count, conveyance, destination
           FROM wbom_escort_programs
           WHERE escort_employee_id = $1
             AND COALESCE(status,'') NOT IN ('Completed','Cancelled')
             AND program_date <= $2
           ORDER BY program_date DESC, program_id DESC
           LIMIT 1""",
        employee_id, ref, conn=conn,
    )
    return dict(row) if row else None


async def find_existing_draft_for_program(program_id: int, conn=None) -> Optional[dict]:
    row = await fetch_one(
        """SELECT id, employee_id, expected_amount, status, accountant_msg
           FROM fazle_payment_drafts
           WHERE escort_program_id = $1
           ORDER BY id DESC LIMIT 1""",
        program_id, conn=conn,
    )
    return dict(row) if row else None


# ── Close program (idempotent) ────────────────────────────────────────────────

async def close_program(
    program_id: int,
    end_date_v: date,
    end_shift: str,
    release_point: Optional[str],
    day_count: Optional[float],
    completed_by: str,
    food_bill: float = 0,
    conveyance: float = 0,
    conn=None,
) -> dict:
    """UPDATE only when status<>'Completed'. Return {ok, already_closed, day_count, program_id}."""
    cur = await fetch_one(
        "SELECT program_id, status, program_date, end_date, day_count "
        "FROM wbom_escort_programs WHERE program_id=$1",
        program_id, conn=conn,
    )
    if not cur:
        return {"ok": False, "error": f"program {program_id} not found"}
    if (cur["status"] or "").lower() == "completed":
        log.info(f"[escort-lifecycle] program {program_id} already Completed")
        return {
            "ok": True, "already_closed": True,
            "program_id": program_id,
            "day_count": float(cur["day_count"] or 0),
        }

    # Compute day_count if missing
    if day_count is None:
        start = cur.get("program_date")
        if start and end_date_v:
            d = (end_date_v - start).days + 1
            day_count = float(max(d, 1))
        else:
            day_count = 1.0

    end_shift = (end_shift or "D").upper()[:1]
    if end_shift not in ("D", "N"):
        end_shift = "D"

    await execute(
        """UPDATE wbom_escort_programs
           SET status='Completed',
               completion_time=NOW(),
               end_date=$2,
               end_shift=$3,
               release_point=COALESCE($4, release_point),
               day_count=$5,
               food_bill=$6,
               conveyance=$7,
               remarks=COALESCE(remarks,'') || ' | b13-closed-by:' || $8
           WHERE program_id=$1""",
        program_id, end_date_v, end_shift, release_point, day_count,
        food_bill, conveyance, completed_by, conn=conn,
    )
    log.info(f"[escort-lifecycle] closed program {program_id} days={day_count} by={completed_by}")
    return {
        "ok": True, "already_closed": False,
        "program_id": program_id, "day_count": float(day_count),
    }


# ── Attendance backfill ───────────────────────────────────────────────────────

async def backfill_attendance_for_program(program_id: int, conn=None) -> int:
    """INSERT wbom_attendance rows for program_date..end_date inclusive, ON CONFLICT DO NOTHING."""
    prog = await fetch_one(
        """SELECT escort_employee_id, program_date, end_date, mother_vessel
           FROM wbom_escort_programs WHERE program_id=$1""",
        program_id, conn=conn,
    )
    if not prog or not prog["escort_employee_id"]:
        return 0
    start = prog["program_date"]
    end = prog["end_date"] or start
    if not start:
        return 0
    if end < start:
        end = start
    eid = prog["escort_employee_id"]
    location = prog.get("mother_vessel") or "Escort Duty"
    inserted = 0
    cur = start
    while cur <= end:
        result = await execute(
            """INSERT INTO wbom_attendance
                  (employee_id, attendance_date, status, location, recorded_by)
               VALUES ($1, $2, 'Present', $3, 'escort-lifecycle')
               ON CONFLICT (employee_id, attendance_date) DO NOTHING""",
            eid, cur, location, conn=conn,
        )
        # asyncpg execute returns 'INSERT 0 1' or 'INSERT 0 0'
        if isinstance(result, str) and result.endswith(" 1"):
            inserted += 1
        cur += timedelta(days=1)
    log.info(f"[escort-lifecycle] backfilled {inserted} attendance rows for program {program_id}")
    return inserted


# ── Date parsing helper ───────────────────────────────────────────────────────

_DATE_RES = [
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b"),
    re.compile(r"\b(\d{1,2})-(\d{1,2})-(20\d{2})\b"),
]


def _parse_date(val) -> Optional[date]:
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if not isinstance(val, str):
        return None
    s = val.strip()
    for pat in _DATE_RES:
        m = pat.search(s)
        if not m:
            continue
        try:
            g = m.groups()
            if len(g[0]) == 4:
                return date(int(g[0]), int(g[1]), int(g[2]))
            return date(int(g[2]), int(g[1]), int(g[0]))
        except Exception:
            continue
    return None


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def handle_release_event(
    employee_id: int,
    extracted: Optional[dict] = None,
    source: str = "release-text",
    admin_confirmed: bool = False,
) -> dict:
    """
    Top-level: close active program, backfill attendance, create payment draft.
    Idempotent: returns existing draft if program already closed.
    """
    if not admin_confirmed:
        return {
            "ok": False,
            "status": "admin_confirmation_required",
            "message": "Release can only be finalized by an admin [RELEASE CONFIRMED] message.",
        }
    extracted = extracted or {}
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            return await _handle_release_event_tx(employee_id, extracted, source, conn)


async def _handle_release_event_tx(employee_id: int, extracted: dict, source: str, conn) -> dict:
    prog = await find_active_program_for_employee(employee_id, conn=conn)
    if not prog:
        return {"ok": False, "status": "no_active_program",
                "message": "No assigned/in-progress program found"}

    program_id = int(prog["program_id"])
    end_date_v = _parse_date(extracted.get("end_date")) or date.today()
    end_shift = (extracted.get("end_shift") or extracted.get("shift") or "D")
    release_point = extracted.get("release_point") or extracted.get("location")
    day_count = extracted.get("day_count") or extracted.get("days")
    food_bill = extracted.get("food_bill") or 0
    conveyance = extracted.get("conveyance") or 0
    if day_count is not None:
        try:
            day_count = float(day_count)
        except Exception:
            day_count = None
    try:
        food_bill = max(float(food_bill), 0)
        conveyance = max(float(conveyance), 0)
    except (TypeError, ValueError):
        raise ValueError("food_bill and conveyance must be non-negative numbers")

    start_date = prog.get("start_date") or prog.get("program_date")
    start_shift = (prog.get("shift") or "D").upper()[:1]
    if start_date and end_date_v:
        total_shifts = count_shifts(start_date, start_shift, end_date_v, end_shift)
        roster_days = float(shifts_to_days(total_shifts))
        if roster_days <= 0:
            raise ValueError("release end date/shift is before program start")
        day_count = roster_days

    closed = await close_program(
        program_id=program_id,
        end_date_v=end_date_v,
        end_shift=end_shift,
        release_point=release_point,
        day_count=day_count,
        completed_by=source,
        food_bill=food_bill,
        conveyance=conveyance,
        conn=conn,
    )
    if not closed.get("ok"):
        return {"ok": False, "status": "close_failed", **closed}

    inserted_att = await backfill_attendance_for_program(program_id, conn=conn)

    # Idempotency: if already closed, look up existing draft
    if closed.get("already_closed"):
        existing = await find_existing_draft_for_program(program_id, conn=conn)
        return {
            "ok": True, "status": "already_closed",
            "program_id": program_id,
            "day_count": closed.get("day_count"),
            "attendance_inserted": inserted_att,
            "draft_id": existing["id"] if existing else None,
            "existing_draft": existing,
        }

    # Create payment draft via Batch 12 bridge
    draft = await create_escort_payment_draft(
        employee_id=employee_id,
        escort_program_id=program_id,
        override_days=closed.get("day_count"),
        source=source,
        conn=conn,
    )
    if not draft.get("draft_id"):
        raise RuntimeError(draft.get("error") or "payment draft creation failed")

    await conn.execute(
        """
        INSERT INTO escort_roster_entries (
            program_id, mother_vessel, lighter_vessel, master_mobile, escort_name,
            escort_mobile, destination, start_date, start_shift, end_date, end_shift,
            total_shifts, total_days, salary, conveyance, food_bill, advance_deduction,
            net_payable, total, release_point, roster_status, calc_version, last_synced_at
        )
        SELECT p.program_id, p.mother_vessel, p.lighter_vessel, p.master_mobile,
               e.employee_name, p.escort_mobile, p.destination,
               COALESCE(p.start_date, p.program_date), p.shift, p.end_date, p.end_shift,
               $2, $3, d.gross_amount, d.conveyance, d.food_bill, d.advance_deduction,
               d.expected_amount, d.expected_amount, p.release_point, 'completed', 1, NOW()
        FROM wbom_escort_programs p
        JOIN wbom_employees e ON e.employee_id=p.escort_employee_id
        JOIN fazle_payment_drafts d ON d.escort_program_id=p.program_id
        WHERE p.program_id=$1 AND d.id=$4
        ON CONFLICT (program_id) DO UPDATE SET
            end_date=EXCLUDED.end_date, end_shift=EXCLUDED.end_shift,
            total_shifts=EXCLUDED.total_shifts, total_days=EXCLUDED.total_days,
            salary=EXCLUDED.salary, conveyance=EXCLUDED.conveyance,
            food_bill=EXCLUDED.food_bill, advance_deduction=EXCLUDED.advance_deduction,
            net_payable=EXCLUDED.net_payable, total=EXCLUDED.total,
            release_point=EXCLUDED.release_point, roster_status='completed',
            calc_version=escort_roster_entries.calc_version + 1,
            last_synced_at=NOW(), updated_at=NOW()
        """,
        program_id, total_shifts, day_count, draft["draft_id"],
    )

    return {
        "ok": True,
        "status": "closed",
        "program_id": program_id,
        "day_count": closed.get("day_count"),
        "attendance_inserted": inserted_att,
        "draft_id": draft.get("draft_id"),
        "draft_text": draft.get("draft_text"),
        "employee_name": draft.get("employee_name"),
        "error": draft.get("error"),
    }


# ── Phase 22: Release slip draft flow ────────────────────────────────────────
# After OCR detects a release slip, build an admin-review draft.
# Admin corrects and sends outbound → parse_release_confirmation() → close_program().

_RC_DATE_RE = re.compile(
    r"(?:end\s*date|release\s*date|completion\s*date|তারিখ)[:\s]+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)
_RC_SHIFT_RE = re.compile(r"\bShift[:\s]+([DN])\b", re.IGNORECASE)
_RC_POINT_RE = re.compile(
    r"(?:Release\s*Point|release\s*location|Location)[:\s]+([^\n]{3,50})", re.IGNORECASE
)
_RC_DAYS_RE = re.compile(r"\bDays?[:\s]+(\d+(?:\.\d+)?)\b", re.IGNORECASE)
_RC_CONV_RE = re.compile(r"\bConveyance[:\s]+([\d,]+)\b", re.IGNORECASE)
_RC_FOOD_RE = re.compile(r"\bFood(?:\s*Bill)?[:\s]+([\d,]+)\b", re.IGNORECASE)
_RC_ESCORT_RE = re.compile(r"\bEscort[:\s]+([^\n]{3,30})", re.IGNORECASE)
_RC_LIGHTER_RE = re.compile(r"\bLighter[:\s]+([^\n]{3,30})", re.IGNORECASE)
_RC_ANCHOR = re.compile(r"\[RELEASE CONFIRMED\]", re.IGNORECASE)


# ── Transport estimate table ──────────────────────────────────────────────────
# Management-approved rates aligned with escort_calculation_config DB and
# resources/ops/transport_allowances.txt (authoritative source). Updated 2026-05-29.
_TRANSPORT_RATES: list[tuple[list[str], int]] = [
    # ₺600 group — Dhaka / Narayanganj area
    (["dhaka", "narayanganj", "bhairab", "ashuganj", "kaliganj",
       "rupganj", "rupshi", "siddhirganj", "shah cement", "mir cement",
       "shah_cement", "mir_cement"], 600),
    # ₺700 — Faridpur
    (["faridpur"], 700),
    # ₺800 — Mongla (PAY-03; management decision 2026-06-23)
    (["mongla"], 800),
    # ₺900 group — Barishal / coastal / river routes
    (["barishal", "barisal", "bhola", "jhalokathi", "jhalokati",
       "nagarbari", "aricha"], 900),
    # ₺1000 group — Noapara / Jessore / Khulna
    (["noapara", "jessore", "jashore", "khulna"], 1000),
]
_DEFAULT_TRANSPORT = 600  # minimum if location not matched


def _estimate_transport(location: str) -> int:
    if not location:
        return _DEFAULT_TRANSPORT
    loc = location.lower()
    for keywords, rate in _TRANSPORT_RATES:
        if any(k in loc for k in keywords):
            return rate
    return _DEFAULT_TRANSPORT


def _calc_duty_days(raw_text: str, rel_date_str: str) -> tuple[Optional[int], str, str]:
    """Return (duty_days, start_date_str, end_date_str) from raw OCR text.
    Finds all date-like tokens and treats first as start, last as end."""
    all_dates = re.findall(
        r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b",
        raw_text or "",
    )
    start_str = all_dates[0] if len(all_dates) >= 2 else ""
    end_str = all_dates[-1] if all_dates else rel_date_str

    def _parse(s: str) -> Optional[date]:
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                    "%d.%m.%y", "%d/%m/%y", "%d-%m-%y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    start_d = _parse(start_str) if start_str else None
    end_d = _parse(end_str) if end_str else None
    if start_d and end_d and end_d >= start_d:
        days = (end_d - start_d).days + 1
        return days, start_str, end_str
    return None, start_str, end_str


def _validate_release_date(rel_date: str) -> tuple[bool, str]:
    """Return (is_valid, reason). Rejects future dates and obviously wrong values."""
    if not rel_date:
        return True, ""  # missing date is warned elsewhere
    def _try_parse(s: str) -> Optional[date]:
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                    "%d.%m.%y", "%d/%m/%y", "%d-%m-%y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None
    d = _try_parse(rel_date)
    if d is None:
        return True, ""  # unparseable — warn but don't block
    today = date.today()
    if d > today:
        return False, f"future date: {rel_date}"
    if (today - d).days > 365:
        return False, f"date too old (>1yr): {rel_date}"
    return True, ""


async def _fuzzy_employee_lookup(name: str) -> Optional[str]:
    """DB READ ONLY: find closest employee name match. Returns 'name (id=X)' or None."""
    if not name or len(name) < 3:
        return None
    try:
        # Use ILIKE for substring match — no fuzzy extension needed
        rows = await fetch_all(
            """SELECT e.employee_id, e.full_name
               FROM wbom_employees e
               WHERE e.full_name ILIKE $1
               LIMIT 3""",
            f"%{name}%",
        )
        if rows:
            return "; ".join(f"{r['full_name']} (id={r['employee_id']})" for r in rows)
    except Exception as _e:
        log.debug(f"[release-ocr] fuzzy lookup failed: {_e}")
    return None


def build_release_draft(ocr_result: dict) -> str:
    """
    Build an admin review draft from OCR-extracted release slip fields.
    Includes duty-day count and safe food + transport estimates (DRAFT ONLY).
    Admin receives this, corrects any mistakes, then sends as outbound to confirm.
    Format is designed to be parseable by parse_release_confirmation().
    """
    escort = ocr_result.get("employee_name") or ""
    lighter = ocr_result.get("vessel") or ""
    rel_date = ocr_result.get("date") or ""
    location = ocr_result.get("location") or ""
    amount = ocr_result.get("amount") or ""
    raw_text = ocr_result.get("raw_text") or ocr_result.get("raw_ocr_text") or ""

    # Validate required fields
    missing: list[str] = []
    if not escort:
        missing.append("Escort Name")
    if not lighter:
        missing.append("Lighter Vessel")
    if not rel_date:
        missing.append("Release Date")
    if not location:
        missing.append("Release Location")

    # Release date sanity check (Part F)
    warnings: list[str] = []
    date_valid, date_reason = _validate_release_date(rel_date)
    if not date_valid:
        warnings.append(f"⚠️ DATE WARNING: {date_reason}")

    # Calculate duty days from raw text dates
    duty_days, start_date_str, end_date_str = _calc_duty_days(raw_text, rel_date)

    # Reject negative or zero duty days (Part F)
    if duty_days is not None and duty_days <= 0:
        warnings.append(f"⚠️ INVALID DUTY DAYS: {duty_days} (calculated from dates)")
        duty_days = None

    # Reject implausibly large duty days (>90 days is suspicious)
    if duty_days is not None and duty_days > 90:
        warnings.append(f"⚠️ SUSPICIOUS DUTY DAYS: {duty_days} — verify dates")

    # OCR confidence check
    conf = ocr_result.get("confidence_score", 100)
    if conf < 40:
        warnings.append(f"⚠️ LOW OCR CONFIDENCE: {conf}/100 — verify all fields")

    # TASK 4: DRAFT ONLY — transport uses hardcoded _TRANSPORT_RATES, not DB.
    # escort_calculation_config table values differ (e.g. Mongla ৳1500 vs code ৳1000).
    # Do NOT auto-send release slip replies until DB sync is implemented.
    transport_est = _estimate_transport(location) if location else None
    food_est = (duty_days * 150) if duty_days else None

    status_tag = "⚠️ INCOMPLETE" if missing else ("⚠️ WARNINGS" if warnings else "✅ OCR DRAFT")

    lines = [
        f"[RELEASE CONFIRMED]  {status_tag}",
        f"Escort: {escort}",
        f"Lighter: {lighter}",
        f"Release Point: {location}",
        f"Start Date: {start_date_str or '?'}",
        f"End Date: {end_date_str or rel_date or '?'}",
        "Shift: D",
        f"Days: {duty_days if duty_days is not None else '?'}",
        f"Conveyance: {amount or (str(transport_est) if transport_est else '?')}",
    ]

    if missing:
        lines.append(f"Missing fields: {', '.join(missing)}")
    for w in warnings:
        lines.append(w)

    lines.append("---")
    lines.append("ESTIMATE (DRAFT ONLY — DO NOT SEND AS FINAL):")
    if food_est is not None:
        lines.append(f"  Food: {duty_days} days × ৳150 = ৳{food_est}")
    else:
        lines.append("  Food: ? days × ৳150 = ৳?")
    if transport_est is not None:
        lines.append(f"  Transport: ৳{transport_est} ({location})")
    else:
        lines.append("  Transport: ৳? (location unknown)")
    total = (food_est or 0) + (transport_est or 0)
    if food_est and transport_est:
        lines.append(f"  Total estimate: ৳{total}")
    lines.append("---")
    lines.append("OCR draft — admin must review, correct, then send to confirm.")
    # TASK 4: This output is intentionally DRAFT ONLY. Transport estimates are not
    # DB-synced; auto-send is disabled until escort_calculation_config is wired in.

    return "\n".join(lines)


def is_release_confirmation(text: str) -> bool:
    """Return True if admin message is a release confirmation (not an assignment completion)."""
    return bool(_RC_ANCHOR.search(text))


def parse_release_confirmation(text: str) -> dict:
    """Extract release fields from admin's confirmed release message."""
    fields: dict = {}

    m = _RC_DATE_RE.search(text)
    if m:
        fields["end_date"] = m.group(1).strip()

    m = _RC_SHIFT_RE.search(text)
    if m:
        fields["end_shift"] = m.group(1).upper()

    m = _RC_POINT_RE.search(text)
    if m:
        fields["release_point"] = m.group(1).strip()

    m = _RC_DAYS_RE.search(text)
    if m:
        fields["day_count"] = m.group(1).strip()

    m = _RC_CONV_RE.search(text)
    if m:
        fields["conveyance"] = m.group(1).replace(",", "").strip()
    m = _RC_FOOD_RE.search(text)
    if m:
        fields["food_bill"] = m.group(1).replace(",", "").strip()

    m = _RC_ESCORT_RE.search(text)
    if m:
        fields["escort_name"] = m.group(1).strip()

    m = _RC_LIGHTER_RE.search(text)
    if m:
        fields["lighter_vessel"] = m.group(1).strip()

    return fields


async def handle_admin_release_confirmation(
    text: str,
    chat_jid: str,
    source: str = "release-admin-confirm",
) -> dict:
    """
    Called when bridge_poller detects an admin outbound release confirmation
    (is_from_me=1, text contains [RELEASE CONFIRMED]).

    Looks up the active program for the employee in chat_jid, then closes it.
    Returns handle_release_event() result.
    """
    fields = parse_release_confirmation(text)
    if not fields:
        log.warning(f"[release-confirm] No fields parsed from text, skipping")
        return {"ok": False, "status": "no_fields"}

    # Identify employee by canonical employee_mobile. wbom_employees has no contact_id.
    phone = chat_jid.replace("@s.whatsapp.net", "").strip()
    emp = await fetch_one(
        """SELECT employee_id
           FROM wbom_employees
           WHERE regexp_replace(employee_mobile, '\\D', '', 'g')
                 LIKE '%' || RIGHT(regexp_replace($1, '\\D', '', 'g'), 11)
           LIMIT 1""",
        phone,
    )
    if not emp:
        # Try via escort_mobile on active program
        emp_row = await fetch_one(
            """SELECT escort_employee_id AS employee_id
               FROM wbom_escort_programs
               WHERE escort_mobile = $1 AND status NOT IN ('Completed', 'Cancelled')
               ORDER BY program_date DESC LIMIT 1""",
            phone,
        )
        if emp_row:
            emp = emp_row

    if not emp:
        log.warning(f"[release-confirm] No employee found for phone={phone}")
        return {"ok": False, "status": "employee_not_found", "phone": phone}

    employee_id = int(emp["employee_id"])
    log.info(f"[release-confirm] employee_id={employee_id} fields={fields}")

    return await handle_release_event(
        employee_id=employee_id,
        extracted=fields,
        source=source,
        admin_confirmed=True,
    )


async def _lookup_active_program_by_phone(phone: str) -> Optional[dict]:
    """DB READ ONLY: find active escort program by sender's phone number."""
    if not phone or phone == "unknown":
        return None
    try:
        row = await fetch_one(
            """SELECT ep.program_id, ep.mother_vessel,
                      ep.lighter_vessel AS lighter_name,
                      ep.program_date::text AS program_date,
                      ep.destination AS location,
                      COALESCE(emp.employee_name, ep.escort_name) AS escort_name,
                      ep.master_mobile AS lighter_master_mobile
               FROM wbom_escort_programs ep
               LEFT JOIN wbom_employees emp ON emp.employee_id = ep.escort_employee_id
               WHERE regexp_replace(ep.escort_mobile, '\\D', '', 'g')
                     LIKE '%' || RIGHT(regexp_replace($1, '\\D', '', 'g'), 11)
               AND ep.status NOT IN ('Completed', 'Cancelled')
               ORDER BY ep.program_date DESC LIMIT 1""",
            phone,
        )
        return dict(row) if row else None
    except Exception as _e:
        log.debug(f"[release-ocr] program lookup failed for phone={phone}: {_e}")
        return None


async def _lookup_active_program_by_slip(ocr_result: dict) -> Optional[dict]:
    """DB READ ONLY: fallback match by OCR vessel/lighter/escort fields."""
    vessel = (ocr_result.get("vessel") or "").strip()
    escort = (ocr_result.get("employee_name") or "").strip()
    if len(vessel) < 3 and len(escort) < 3:
        return None
    try:
        row = await fetch_one(
            """SELECT ep.program_id, ep.mother_vessel,
                      ep.lighter_vessel AS lighter_name,
                      ep.program_date::text AS program_date,
                      ep.destination AS location,
                      COALESCE(emp.employee_name, ep.escort_name) AS escort_name,
                      ep.master_mobile AS lighter_master_mobile
               FROM wbom_escort_programs ep
               LEFT JOIN wbom_employees emp ON emp.employee_id = ep.escort_employee_id
               WHERE ep.status NOT IN ('Completed', 'Cancelled')
                 AND (
                      ($1 <> '' AND (
                          ep.lighter_vessel ILIKE '%' || $1 || '%'
                          OR ep.mother_vessel ILIKE '%' || $1 || '%'
                      ))
                      OR ($2 <> '' AND (
                          emp.employee_name ILIKE '%' || $2 || '%'
                          OR ep.escort_name ILIKE '%' || $2 || '%'
                      ))
                 )
               ORDER BY ep.program_date DESC, ep.program_id DESC
               LIMIT 1""",
            vessel, escort,
        )
        return dict(row) if row else None
    except Exception as _e:
        log.debug(
            "[release-ocr] slip-field program lookup failed vessel=%r escort=%r err=%s",
            vessel, escort, _e,
        )
        return None


async def handle_ocr_release_slip(
    ocr_result: dict,
    source: str = "bridge_poller",
    phone: str = "unknown",
) -> Optional[str]:
    """
    Called when OCR identifies a release_slip (two-date rule or release keywords).
    Builds an admin review draft, saves it to fazle_draft_replies (DRAFT ONLY),
    and returns it as a string for the caller to forward to the admin bridge.
    Does NOT close the program and does NOT send automatically.
    Returns None if not a release slip.
    """
    import json
    if ocr_result.get("slip_type") != "release_slip":
        return None

    # DB lookup: find active escort program by sender phone — enriches OCR gaps
    program = await _lookup_active_program_by_phone(phone)
    match_source = "phone" if program else ""
    if not program:
        program = await _lookup_active_program_by_slip(ocr_result)
        match_source = "slip-fields" if program else ""
    if program:
        log.info(
            "[release-ocr] active program found: program_id=%s match_source=%s phone=%s",
            program.get("program_id"), match_source, phone,
        )
        # Fill in any fields the OCR missed, using DB as authoritative source
        if not ocr_result.get("employee_name") and program.get("escort_name"):
            ocr_result = dict(ocr_result)
            ocr_result["employee_name"] = program["escort_name"]
        if not ocr_result.get("vessel") and program.get("lighter_name"):
            ocr_result = dict(ocr_result) if not isinstance(ocr_result, dict) else ocr_result
            ocr_result["vessel"] = program["lighter_name"]
        if not ocr_result.get("location") and program.get("location"):
            ocr_result["location"] = program["location"]

    draft = build_release_draft(ocr_result)
    escort = ocr_result.get("employee_name") or phone
    missing = [f for f in ("employee_name", "vessel", "date", "location")
               if not ocr_result.get(f)]

    # Append DB program context to admin draft
    if program:
        prog_lines = [
            f"\n📋 DB Program (auto-matched by {match_source or 'unknown'}):",
            f"  Program ID: {program.get('program_id')}",
            f"  Mother Vessel: {program.get('mother_vessel', '?')}",
            f"  Lighter: {program.get('lighter_name', '?')}",
            f"  Start Date: {program.get('program_date', '?')}",
            f"  Location: {program.get('location', '?')}",
        ]
        if program.get("lighter_master_mobile"):
            prog_lines.append(f"  Lighter Master: {program['lighter_master_mobile']}")
        draft += "\n".join(prog_lines)
    else:
        # Fallback: try name-based fuzzy lookup
        db_match = await _fuzzy_employee_lookup(escort)
        if db_match:
            log.info(f"[release-ocr] fuzzy name match: {db_match}")
            draft += f"\nDB name match (verify): {db_match}"

    # Save to draft_replies for admin queue (READ-ONLY DB lookup, NO financial write)
    draft_id = await create_draft_reply(
        sender=phone,
        bridge=source,
        draft_text=draft,
        role="employee",
        intent="release_slip_ocr",
        context=json.dumps({
            "ocr_employee": escort,
            "missing_fields": missing,
            "incomplete": bool(missing),
            "program_id": program.get("program_id") if program else None,
            "db_program_matched": bool(program),
            "db_match_source": match_source,
            "confidence_score": ocr_result.get("confidence_score", 0),
            "draft_type": "release_slip",
        }),
        source_module="escort_lifecycle",
    )
    if draft_id:
        log.info(f"[release-ocr] draft #{draft_id} saved for escort={escort} missing={missing} program={bool(program)}")

    log.info(f"[release-ocr] admin draft built for escort={escort}")
    return draft


async def check_active_program_for_phone(phone: str) -> bool:
    """Return True if this phone has an active (non-completed) escort program."""
    prog = await _lookup_active_program_by_phone(phone)
    return prog is not None
