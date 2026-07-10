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

from app.database import execute, fetch_one, fetch_val, get_pool
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
    entry_source: str = "legacy",
    approval_channel: Optional[str] = None,
    approved_by: Optional[str] = None,
    source_draft_id: Optional[int] = None,
    conn=None,
) -> bool:
    """
    Write attendance record to wbom_attendance.
    Handles UNIQUE (employee_id, attendance_date) conflict by updating.

    entry_source/approval_channel/approved_by/source_draft_id (migration 040,
    Owner decision 2026-07-11) record WHERE this row came from and HOW it was
    finalized -- office payroll's duty-day query must filter on entry_source
    rather than trusting every row in this shared (escort + office) table.
    Accepts an optional conn so callers (e.g. finalize_attendance_draft) can
    compose this write into a larger transaction atomically.
    """
    approved_at = datetime.now(timezone.utc) if approved_by else None
    try:
        await execute(
            """INSERT INTO wbom_attendance
                   (employee_id, attendance_date, status, location,
                    check_in_time, remarks, recorded_by,
                    entry_source, approval_channel, approved_by, approved_at,
                    source_draft_id)
               VALUES ($1, $2, $3, $4, NOW(), $5, $6, $7, $8, $9, $10, $11)
               ON CONFLICT (employee_id, attendance_date)
               DO UPDATE SET
                   status = EXCLUDED.status,
                   location = EXCLUDED.location,
                   check_in_time = EXCLUDED.check_in_time,
                   remarks = EXCLUDED.remarks,
                   recorded_by = EXCLUDED.recorded_by,
                   entry_source = EXCLUDED.entry_source,
                   approval_channel = EXCLUDED.approval_channel,
                   approved_by = EXCLUDED.approved_by,
                   approved_at = EXCLUDED.approved_at,
                   source_draft_id = EXCLUDED.source_draft_id""",
            employee_id, attendance_date, status,
            location[:100], remarks[:500], recorded_by,
            entry_source, approval_channel, approved_by, approved_at,
            source_draft_id,
            conn=conn,
        )
        log.info(f"[attendance] saved emp={employee_id} date={attendance_date} status={status} source={entry_source}")
        return True
    except Exception as e:
        log.error(f"[attendance] save error: {e}")
        return False


# ── Shared attendance-finalization service (Owner decision, 2026-07-11) ───────
# Called from BOTH modules.admin_commands._cmd_approve (WhatsApp APPROVE <id>)
# and modules.drafts.routes.approve_draft (frontend Review Queue) for any
# fazle_draft_replies row where intent == 'attendance'. Never called for any
# other intent -- those keep their existing, untouched behavior. This is the
# single place attendance-draft approval logic lives, so the two channels
# cannot drift from each other.

def _parse_attendance_draft_fields(draft: dict) -> dict:
    """
    Defensive parse handling both meta shapes found in real production data:
      - legacy/flat: meta = {"employee_id":.., "att_date":.., "shift":..,
        "employee_name":.., "employee_mobile":..} directly at the top level.
      - current (shared.draft_reply.create_draft_reply's wrapping
        convention): meta = {"role":.., "context": "<json-string>",
        "source_module":.., ...} -- the actual fields are double-encoded
        inside meta["context"] as a JSON string, NOT top-level. Every draft
        created by modules.attendance_parser.create_attendance_draft() uses
        this shape; a naive meta.get("employee_id") always returns None for
        them, which is exactly why this parser exists.
    Returns the flat field dict, or {} if nothing usable is found.
    """
    import json as _json

    raw_meta = draft.get("meta")
    if isinstance(raw_meta, str):
        try:
            raw_meta = _json.loads(raw_meta)
        except Exception:
            raw_meta = {}
    raw_meta = raw_meta or {}

    context = raw_meta.get("context")
    if context:
        try:
            inner = _json.loads(context) if isinstance(context, str) else context
            if isinstance(inner, dict) and inner:
                return inner
        except Exception:
            pass

    if any(k in raw_meta for k in ("employee_id", "employee_mobile", "employee_name")):
        return raw_meta

    return {}


async def _resolve_attendance_employee(
    fields: dict, sender_phone: str = "", conn=None,
) -> Optional[dict]:
    """
    Resolve the employee this attendance draft is about, against wbom_employees
    only. Never auto-creates an employee (Owner decision: a parser draft must
    not create an independent duplicate employee identity) -- if nothing
    resolves, the caller must treat this as a hard validation failure, not a
    fallback creation.
    """
    emp_id = fields.get("employee_id")
    if emp_id:
        row = await fetch_one(
            "SELECT employee_id, employee_name, status FROM wbom_employees WHERE employee_id = $1",
            emp_id, conn=conn,
        )
        if row:
            return dict(row)

    emp_mobile = fields.get("employee_mobile")
    if emp_mobile:
        row = await fetch_one(
            "SELECT employee_id, employee_name, status FROM wbom_employees WHERE employee_mobile = $1",
            emp_mobile, conn=conn,
        )
        if row:
            return dict(row)

    emp_name = fields.get("employee_name")
    if emp_name and emp_name.split():
        row = await fetch_one(
            """SELECT employee_id, employee_name, status FROM wbom_employees
               WHERE LOWER(employee_name) LIKE LOWER($1) AND status = 'Active' LIMIT 1""",
            f"%{emp_name.split()[0]}%", conn=conn,
        )
        if row:
            return dict(row)

    # Self-report fallback (modules.attendance.handle_attendance_message's
    # drafts carry no employee_id/name in meta at all -- only the sender's
    # own WhatsApp number is known): resolve by the draft's own sender phone.
    if sender_phone:
        for variant in ([sender_phone, "880" + sender_phone[1:]] if sender_phone.startswith("0") else [sender_phone]):
            row = await fetch_one(
                "SELECT employee_id, employee_name, status FROM wbom_employees WHERE employee_mobile = $1",
                variant, conn=conn,
            )
            if row:
                return dict(row)

    return None


async def finalize_attendance_draft(
    draft_id: int,
    *,
    channel: str,
    actor: str,
) -> dict:
    """
    Validate, resolve, and save one attendance draft -- atomically with
    marking the draft approved, so a failure never leaves the draft marked
    approved without a real attendance row behind it.

    channel: 'whatsapp_message' | 'frontend_review_queue'
    actor:   reviewer identifier (admin phone, or resolved admin identity)

    Returns a result dict, never raises:
      {"ok": True, "already_approved": bool, "employee_id":, "employee_name":,
       "attendance_date":, "shift":, "recipient":, "source":}
      {"ok": False, "error": "<reason>"}

    Idempotent: a second call for an already-approved draft returns
    ok=True, already_approved=True without re-running any side effect (no
    duplicate attendance row, no duplicate confirmation message sent by the
    caller).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            draft = await fetch_one(
                "SELECT id, intent, status, recipient, source, meta FROM fazle_draft_replies "
                "WHERE id = $1 FOR UPDATE",
                draft_id, conn=conn,
            )
            if not draft:
                return {"ok": False, "error": f"draft {draft_id} not found"}
            if draft["intent"] != "attendance":
                return {"ok": False, "error": f"draft {draft_id} is not an attendance draft (intent={draft['intent']!r})"}
            if draft["status"] == "approved":
                return {"ok": True, "already_approved": True}
            if draft["status"] not in ("pending", "edited"):
                return {"ok": False, "error": f"draft {draft_id} has status {draft['status']!r}, cannot approve"}

            fields = _parse_attendance_draft_fields(dict(draft))
            emp = await _resolve_attendance_employee(fields, sender_phone=draft["recipient"] or "", conn=conn)
            if not emp:
                return {"ok": False, "error": "employee could not be resolved from wbom_employees"}
            if (emp.get("status") or "").strip().lower() != "active":
                return {"ok": False, "error": f"employee {emp['employee_id']} is not Active"}

            att_date_str = fields.get("att_date")
            try:
                att_date = date.fromisoformat(att_date_str) if att_date_str else date.today()
            except Exception:
                att_date = date.today()
            shift = fields.get("shift") or "D"

            raw_meta = draft.get("meta")
            if isinstance(raw_meta, str):
                import json as _json
                try:
                    raw_meta = _json.loads(raw_meta)
                except Exception:
                    raw_meta = {}
            source_module = (raw_meta or {}).get("source_module") or ""
            entry_source = "message_parser" if source_module == "attendance_parser" else "whatsapp_escort"

            saved = await save_attendance(
                employee_id=emp["employee_id"],
                attendance_date=att_date,
                status="Present",
                location="",
                recorded_by=actor,
                remarks=f"Shift:{shift} — approved via {channel}, draft #{draft_id}",
                entry_source=entry_source,
                approval_channel=channel,
                approved_by=actor,
                source_draft_id=draft_id,
                conn=conn,
            )
            if not saved:
                # Force a rollback of this transaction -- do not leave the
                # draft marked approved without a real attendance row.
                raise RuntimeError(f"save_attendance failed for draft {draft_id}")

            await conn.execute(
                "UPDATE fazle_draft_replies SET status='approved', admin_phone=$1, approved_at=NOW() WHERE id=$2",
                actor, draft_id,
            )

            try:
                from modules.escort_lifecycle import mark_program_running_on_attendance
                await mark_program_running_on_attendance(emp["employee_id"], conn=conn)
            except Exception as run_err:
                log.warning(f"[attendance] confirmed->running check failed for emp={emp['employee_id']}: {run_err}")

            return {
                "ok": True,
                "already_approved": False,
                "employee_id": emp["employee_id"],
                "employee_name": emp.get("employee_name"),
                "attendance_date": att_date,
                "shift": shift,
                "recipient": draft["recipient"],
                "source": draft["source"],
            }


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
