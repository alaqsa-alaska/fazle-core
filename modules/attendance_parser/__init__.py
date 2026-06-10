"""
Fazle Core — Attendance Parser
Extracts structured attendance data from any WhatsApp message.

Handles formats like:
  "Jakir Day 24-04-2026"
  "Karim - Night shift - 24/04/26"
  "Name: Rahim, Shift: D, Date: 24.04.2026"
  "01712345678 Jakir D 24-04-2026"

Workflow: parse → create draft in fazle_draft_replies →
          admin APPROVE → save to wbom_attendance + confirm to sender
"""

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from app.database import execute, fetch_one, fetch_val
from shared.draft_reply import create_draft_reply

log = logging.getLogger("fazle.attendance_parser")

# ── Patterns ───────────────────────────────────────────────────────────────────
_DATE_PATTERNS = [
    re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\b"),  # DD-MM-YYYY
    re.compile(r"\b(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})\b"),    # YYYY-MM-DD
]

_SHIFT_RE = re.compile(
    r"\b(day|night|d|n)\b",
    re.IGNORECASE,
)

_MOBILE_RE = re.compile(r"\b((?:880|0)1[3-9]\d{8})\b")

_NAME_LABEL_RE = re.compile(
    r"(?:name|নাম|কর্মী)\s*[:\-]?\s*([A-Za-zঀ-৿][A-Za-zঀ-৿\s]{1,25}?)(?=\s*[,\n]|$)",
    re.IGNORECASE | re.MULTILINE,
)

_SHIFT_LABEL_RE = re.compile(
    r"(?:shift|শিফট)\s*[:\-]?\s*([DNdn]|day|night)",
    re.IGNORECASE,
)

_ATTENDANCE_KEYWORDS = [
    "হাজির", "উপস্থিত", "present", "day", "night", "shift",
    "ডিউটি", "duty", "d shift", "n shift",
]


def is_supervisor_attendance(text: str) -> bool:
    """Quick check: does this look like an attendance report?
    Requires BOTH a keyword AND a date pattern to avoid false positives."""
    t = text.lower()
    has_kw = any(kw in t for kw in _ATTENDANCE_KEYWORDS)
    has_date = bool(_DATE_PATTERNS[0].search(text) or _DATE_PATTERNS[1].search(text))
    return has_kw and has_date


def parse_attendance(text: str) -> dict:
    """
    Extract attendance fields from text.

    Returns:
        {
            "employee_name": str | None,
            "employee_mobile": str | None,
            "shift": "D" | "N" | None,
            "date": "YYYY-MM-DD",   # defaults to today
            "raw_text": str,
        }
    """
    result = {
        "employee_name": None,
        "employee_mobile": None,
        "shift": None,
        "date": date.today().isoformat(),
        "raw_text": text,
    }

    # ── Date extraction ────────────────────────────────────────────────────────
    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if m:
            try:
                g = m.groups()
                if len(g[0]) == 4:          # YYYY-MM-DD pattern
                    y, mo, d = int(g[0]), int(g[1]), int(g[2])
                else:                        # DD-MM-YYYY pattern
                    d, mo, y = int(g[0]), int(g[1]), int(g[2])
                    if y < 100:
                        y += 2000
                result["date"] = date(y, mo, d).isoformat()
            except Exception as _e:
                logging.getLogger("fazle.attendance_parser").debug(f"date parse: {_e}")
            break

    # ── Shift extraction ───────────────────────────────────────────────────────
    shift_label = _SHIFT_LABEL_RE.search(text)
    if shift_label:
        s = shift_label.group(1).upper()
        result["shift"] = "N" if s.startswith("N") else "D"
    else:
        shift_inline = _SHIFT_RE.search(text)
        if shift_inline:
            s = shift_inline.group(1).upper()
            result["shift"] = "N" if s.startswith("N") else "D"

    # ── Mobile extraction ──────────────────────────────────────────────────────
    mob_m = _MOBILE_RE.search(text)
    if mob_m:
        result["employee_mobile"] = mob_m.group(1)

    # ── Name extraction ────────────────────────────────────────────────────────
    name_label = _NAME_LABEL_RE.search(text)
    if name_label:
        result["employee_name"] = name_label.group(1).strip()
    else:
        cleaned = _remove_noise(text)
        words = [w.strip() for w in cleaned.split() if w.strip()]
        name = _extract_bare_name(words)
        if name:
            result["employee_name"] = name

    return result


def _remove_noise(text: str) -> str:
    """Strip dates, mobile numbers, shift tokens, punctuation."""
    t = text
    for pat in _DATE_PATTERNS:
        t = pat.sub(" ", t)
    t = _MOBILE_RE.sub(" ", t)
    t = _SHIFT_RE.sub(" ", t)
    t = re.sub(r"[,\.\-/:|;]", " ", t)
    return t.strip()


def _extract_bare_name(words: list[str]) -> Optional[str]:
    """Heuristic: find 1-3 consecutive non-keyword words that look like a name."""
    stop = {
        "day", "night", "shift", "duty", "d", "n", "present", "absent",
        "হাজির", "উপস্থিত", "অনুপস্থিত", "ডিউটি",
    }
    candidates = [w for w in words if w.lower() not in stop and len(w) > 1]
    if not candidates:
        return None
    name_parts = []
    for w in candidates[:3]:
        if re.match(r"^[A-Z][a-zA-Z]{1,}$|^[ঀ-৿]+$", w):
            name_parts.append(w)
        elif name_parts:
            break
    return " ".join(name_parts) if name_parts else candidates[0] if candidates else None


async def create_attendance_draft(
    parsed: dict,
    sender_phone: str,
    source: str,
) -> dict:
    """
    Look up employee by name/mobile, then create an approval draft in fazle_draft_replies.
    Admin must APPROVE before attendance is saved to wbom_attendance.

    Returns: {"draft_id": int | None, "admin_msg": str, "message": str}
    """
    emp_name   = parsed.get("employee_name")
    emp_mobile = parsed.get("employee_mobile")
    att_date_str = parsed.get("date") or date.today().isoformat()
    shift = parsed.get("shift") or "D"

    try:
        att_date = date.fromisoformat(att_date_str)
    except Exception:
        att_date = date.today()

    # ── Find employee ──────────────────────────────────────────────────────────
    emp = None

    if emp_mobile:
        from modules.user_role import normalize_phone
        norm = normalize_phone(emp_mobile)
        for v in ([norm, "880" + norm[1:]] if norm.startswith("0") else [norm]):
            emp = await fetch_one(
                "SELECT employee_id, employee_name, designation FROM wbom_employees WHERE employee_mobile = $1",
                v,
            )
            if emp:
                break

    if not emp and emp_name:
        emp = await fetch_one(
            """SELECT employee_id, employee_name, designation
               FROM wbom_employees
               WHERE LOWER(employee_name) LIKE LOWER($1) AND status = 'Active'
               LIMIT 1""",
            f"%{emp_name.split()[0]}%",
        )

    # ── Build draft content ────────────────────────────────────────────────────
    shift_label = "Day" if shift == "D" else "Night"
    date_display = att_date.strftime("%d/%m/%Y")

    if emp:
        emp_id   = emp["employee_id"]
        emp_disp = emp["employee_name"]
        desig    = emp.get("designation", "")
        draft_body = (
            f"কর্মী: {emp_disp}" + (f" ({desig})" if desig else "") + "\n"
            f"আইডি: {emp_id}\n"
            f"তারিখ: {date_display}\n"
            f"শিফট: {shift_label}\n"
            f"অবস্থান: —\n"
            f"স্ট্যাটাস: Present"
        )
        meta = {
            "employee_id":   emp_id,
            "employee_name": emp_disp,
            "shift":         shift,
            "att_date":      att_date_str,
        }
    else:
        emp_id   = None
        emp_disp = emp_name or emp_mobile or "অজ্ঞাত"
        draft_body = (
            f"কর্মী (DB-তে নেই): {emp_disp}\n"
            f"তারিখ: {date_display}\n"
            f"শিফট: {shift_label}\n"
            f"⚠️ APPROVE করলে নতুন কর্মী তৈরি হবে"
        )
        meta = {
            "employee_id":     None,
            "employee_name":   emp_name,
            "employee_mobile": emp_mobile,
            "shift":           shift,
            "att_date":        att_date_str,
        }

    # ── Save draft ─────────────────────────────────────────────────────────────
    draft_id = await create_draft_reply(
        sender=sender_phone,
        bridge=source,
        draft_text=draft_body,
        role="employee",
        intent="attendance",
        context=json.dumps(meta, ensure_ascii=False),
        source_module="attendance_parser",
    )

    admin_msg = (
        f"📋 নতুন হাজিরা ড্রাফট #{draft_id}:\n\n"
        f"{draft_body}\n"
        f"প্রেরক: {sender_phone}\n\n"
        f"APPROVE {draft_id} | REJECT {draft_id}"
    )

    return {
        "draft_id": draft_id,
        "admin_msg": admin_msg,
        "message": (
            "⏳ আপনার হাজিরা তথ্য পাঠানো হয়েছে।\n"
            "প্রশাসকের অনুমোদনের পর নিশ্চিত করা হবে।"
        ),
    }


# Keep old name as alias for backward compatibility
async def save_supervisor_attendance(parsed: dict, supervisor_phone: str) -> dict:
    """Deprecated: use create_attendance_draft() instead."""
    result = await create_attendance_draft(parsed, supervisor_phone, "bridge1")
    return {
        "saved": result["draft_id"] is not None,
        "employee_id": None,
        "message": result["message"],
    }
