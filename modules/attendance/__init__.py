"""
Fazle Core — Attendance Module
Handles security guard attendance reports via WhatsApp.

Business logic:
  Guard/employee sends attendance message (e.g., "হাজির" / "Present" / "উপস্থিত")
  with optional site/location info.
  → System creates a draft for admin approval
  → Admin APPROVEs draft → saved to wbom_attendance
  OR admin can use: ATTENDANCE SAVE <employee_id> <location>

Flow:
  1. Detect attendance message (is_attendance_message)
  2. Look up employee by phone → get employee_id, name
  3. Create draft in fazle_draft_replies (intent='attendance')
  4. Admin approves → save_attendance() writes to wbom_attendance

DB:
  wbom_attendance: employee_id, attendance_date, status, location,
                   check_in_time, remarks, recorded_by
"""
import logging
import re
from datetime import date, datetime, timezone
from typing import Optional

from app.database import execute, fetch_one, fetch_val
from shared.draft_reply import create_draft_reply

log = logging.getLogger("fazle.attendance")

# ── Keyword patterns ───────────────────────────────────────────────────────────
_PRESENT_KEYWORDS = [
    "হাজির", "উপস্থিত", "present", "হাজির আছি", "ডিউটিতে আছি",
    "আছি", "on duty", "duty start", "ডিউটি শুরু", "চেক ইন",
    "check in", "checked in",
]

_ABSENT_KEYWORDS = [
    "অনুপস্থিত", "absent", "আসতে পারব না", "আসতে পারছি না",
    "অসুস্থ", "sick", "ছুটি", "leave",
]

_LOCATION_RE = re.compile(
    r"(?:location|loc|পোস্ট|পোষ্ট|সাইট|site|লোকেশন)\s*[:\-]?\s*(.+?)(?=\n|$)",
    re.IGNORECASE,
)


def is_attendance_message(text: str) -> bool:
    """Quick check if message is an attendance report."""
    t = text.lower().strip()
    return any(kw.lower() in t for kw in _PRESENT_KEYWORDS + _ABSENT_KEYWORDS)


def _extract_location(text: str) -> Optional[str]:
    """Try to extract site/location from attendance message."""
    m = _LOCATION_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _detect_status(text: str) -> str:
    """Return 'Present' or 'Absent' based on message keywords."""
    t = text.lower()
    if any(kw.lower() in t for kw in _ABSENT_KEYWORDS):
        return "Absent"
    return "Present"


# ── Main handler ───────────────────────────────────────────────────────────────

async def handle_attendance_message(
    text: str,
    sender_phone: str,
    source: str,
) -> tuple[str, Optional[dict]]:
    """
    Process an incoming attendance report.
    Returns (reply_to_sender, admin_notification | None).
    """
    try:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, designation
               FROM wbom_employees
               WHERE employee_mobile = $1 AND status = 'Active'""",
            sender_phone,
        )
        if not emp:
            # Try without leading 0
            variants = [sender_phone]
            if sender_phone.startswith("0"):
                variants.append("880" + sender_phone[1:])
            for v in variants:
                emp = await fetch_one(
                    """SELECT employee_id, employee_name, designation
                       FROM wbom_employees
                       WHERE employee_mobile = $1 AND status = 'Active'""",
                    v,
                )
                if emp:
                    break

        if not emp:
            log.warning(f"[attendance] unknown sender: {sender_phone}")
            return (
                "আপনার নম্বর আমাদের সিস্টেমে নেই। অনুগ্রহ করে অফিসে যোগাযোগ করুন।",
                None,
            )

        status = _detect_status(text)
        location = _extract_location(text) or ""
        today = date.today()
        now_ts = datetime.now(timezone.utc).isoformat()

        emp_id = emp["employee_id"]
        emp_name = emp["employee_name"]
        designation = emp.get("designation") or ""

        draft_text = (
            f"📋 উপস্থিতি রিপোর্ট:\n\n"
            f"কর্মী: {emp_name} ({designation})\n"
            f"আইডি: {emp_id}\n"
            f"তারিখ: {today.strftime('%d/%m/%Y')}\n"
            f"অবস্থান: {location or '—'}\n"
            f"স্ট্যাটাস: {status}\n"
            f"সময়: {now_ts[:16].replace('T', ' ')}\n\n"
            f"✅ সংরক্ষণ করতে APPROVE করুন।"
        )

        # Save draft for admin approval
        draft_id = await create_draft_reply(
            sender=sender_phone,
            bridge=source,
            draft_text=draft_text,
            role="employee",
            intent="attendance",
            source_module="attendance",
        )

        # Store attendance payload in remarks JSON in draft_text is sufficient for now
        # Admin approval via APPROVE <draft_id> will call save_attendance via lookup

        from app.config import get_settings
        settings = get_settings()
        admin_phone = settings.admin_bridge1_number if source == "bridge1" else settings.admin_bridge2_number

        admin_note = {
            "admin_phone": admin_phone,
            "text": draft_text,
            "bridge": source,
        }

        reply = (
            f"✅ আপনার উপস্থিতি রিপোর্ট পেয়েছি, {emp_name}।\n"
            f"অ্যাডমিন অনুমোদনের পরে সংরক্ষিত হবে।"
        )
        log.info(f"[attendance] draft #{draft_id} for emp {emp_id} ({emp_name})")
        return reply, admin_note

    except Exception as e:
        log.error(f"[attendance] error: {e}")
        return "উপস্থিতি রেকর্ড করতে সমস্যা হয়েছে। পরে চেষ্টা করুন।", None


async def save_attendance(
    employee_id: int,
    attendance_date: date,
    status: str = "Present",
    location: str = "",
    recorded_by: str = "system",
    remarks: str = "",
) -> bool:
    """
    Write attendance record to wbom_attendance.
    Handles UNIQUE (employee_id, attendance_date) conflict by updating.
    """
    try:
        await execute(
            """INSERT INTO wbom_attendance
                   (employee_id, attendance_date, status, location,
                    check_in_time, remarks, recorded_by)
               VALUES ($1, $2, $3, $4, NOW(), $5, $6)
               ON CONFLICT (employee_id, attendance_date)
               DO UPDATE SET
                   status = EXCLUDED.status,
                   location = EXCLUDED.location,
                   check_in_time = EXCLUDED.check_in_time,
                   remarks = EXCLUDED.remarks,
                   recorded_by = EXCLUDED.recorded_by""",
            employee_id, attendance_date, status,
            location[:100], remarks[:500], recorded_by,
        )
        log.info(f"[attendance] saved emp={employee_id} date={attendance_date} status={status}")
        return True
    except Exception as e:
        log.error(f"[attendance] save error: {e}")
        return False


async def get_attendance_summary(
    target_date: Optional[date] = None,
    location_filter: Optional[str] = None,
) -> str:
    """Return a formatted attendance summary for admin."""
    try:
        from app.database import fetch_all
        d = target_date or date.today()
        if location_filter:
            rows = await fetch_all(
                """SELECT e.employee_name, e.designation, a.status, a.location
                   FROM wbom_attendance a
                   JOIN wbom_employees e ON e.employee_id = a.employee_id
                   WHERE a.attendance_date = $1 AND a.location ILIKE $2
                   ORDER BY a.status, e.employee_name""",
                d, f"%{location_filter}%",
            )
        else:
            rows = await fetch_all(
                """SELECT e.employee_name, e.designation, a.status, a.location
                   FROM wbom_attendance a
                   JOIN wbom_employees e ON e.employee_id = a.employee_id
                   WHERE a.attendance_date = $1
                   ORDER BY a.status, e.employee_name""",
                d,
            )

        if not rows:
            return f"📋 {d.strftime('%d/%m/%Y')} — কোনো উপস্থিতি রেকর্ড নেই।"

        present = [r for r in rows if r.get("status") == "Present"]
        absent  = [r for r in rows if r.get("status") == "Absent"]

        lines = [f"📋 উপস্থিতি {d.strftime('%d/%m/%Y')}:"]
        lines.append(f"✅ উপস্থিত: {len(present)} | ❌ অনুপস্থিত: {len(absent)}\n")
        for r in present:
            loc = f" [{r.get('location','')}]" if r.get("location") else ""
            lines.append(f"  ✅ {r['employee_name']}{loc}")
        for r in absent:
            lines.append(f"  ❌ {r['employee_name']}")
        return "\n".join(lines)

    except Exception as e:
        log.error(f"[attendance] summary error: {e}")
        return f"উপস্থিতি লোড ব্যর্থ: {e}"
