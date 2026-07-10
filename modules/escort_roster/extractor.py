"""
Escort Roster — AI Extraction Engine

Extracts escort program fields from WhatsApp messages.

Responsibilities:
  1. Detect message type: client order vs admin reply vs duty-done signal
  2. Parse escort order messages:
     - Mother vessel: English name, usually near top or labelled, NO mobile number
     - Lighter vessel: Bangla/short name, HAS mobile number, serial numbers like "07. Atlas 2"
     - Support multi-lighter from single message (batch orders)
  3. Parse admin reply messages:
     - Date + shift: 13.05.2026(D) / 13.05.2026(N)
     - Escort name assignment
     - Destination detection
  4. Match admin replies to the closest unmatched draft program
  5. OCR slip matching against active programs (confidence scoring)

All parsing is fault-tolerant — returns partial results rather than failing.
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any, Optional

from modules.escort import DEST_ALIASES, normalize_destination

log = logging.getLogger("fazle.escort_roster.extractor")


# ─────────────────────────────────────────────────────────────────────────────
# Regexes
# ─────────────────────────────────────────────────────────────────────────────

# BD mobile numbers (01XXXXXXXXX — 11 digits starting with 01)
_MOBILE_RE = re.compile(r"\b(01[3-9]\d{8})\b")

# Task 10: import date-shift regex from calculations (single source of truth)
from .calculations import DATE_SHIFT_RE as _DATE_SHIFT_RE, DATE_ONLY_RE as _DATE_ONLY_RE

# Serial number prefix: "07." or "7." at line start
_SERIAL_LINE_RE = re.compile(r"^\s*(\d{1,3})\.\s*(.+)$", re.MULTILINE)

# Destination keywords — use canonical DEST_ALIASES from modules.escort (Task 9)
# Build a simple lookup dict from the canonical destination names
DESTINATIONS = {v.lower(): v for _, v in DEST_ALIASES}

# English letter pattern (for mother vessel detection)
_LATIN_RE = re.compile(r"[A-Za-z]")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(day: str, month: str, year_s: str) -> Optional[date]:
    year = int(year_s) if len(year_s) == 4 else 2000 + int(year_s)
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def _extract_mobile(text: str) -> Optional[str]:
    m = _MOBILE_RE.search(text)
    return m.group(1) if m else None


def _extract_all_mobiles(text: str) -> list[str]:
    return _MOBILE_RE.findall(text)


def _extract_date_shift(text: str) -> tuple[Optional[date], Optional[str]]:
    m = _DATE_SHIFT_RE.search(text)
    if m:
        d, mo, y, sh = m.groups()
        dt = _parse_date(d, mo, y)
        return dt, sh.upper() if dt else (None, None)
    m = _DATE_ONLY_RE.search(text)
    if m:
        d, mo, y = m.groups()
        dt = _parse_date(d, mo, y)
        return dt, None
    return None, None


def _detect_destination(text: str) -> Optional[str]:
    tl = text.lower()
    for key, label in DESTINATIONS.items():
        if key in tl:
            return label
    return None


def _is_english_name(text: str) -> bool:
    """True if text contains mostly Latin characters (mother vessel heuristic)."""
    latin = sum(1 for c in text if c.isalpha() and _LATIN_RE.match(c))
    total = sum(1 for c in text if c.isalpha())
    return total > 0 and (latin / total) >= 0.6


# ─────────────────────────────────────────────────────────────────────────────
# Message type classifier
# ─────────────────────────────────────────────────────────────────────────────

DUTY_DONE_SIGNALS = {
    "duty done", "duty done.", "duty complete", "duty ok", "কাজ শেষ",
    "কাজ হয়েছে", "ডিউটি শেষ", "ছাড়া হয়েছে", "ছেড়ে দিয়েছে",
}


def classify_message(text: str) -> str:
    """
    Returns: 'order' | 'admin_reply' | 'duty_done' | 'unknown'
    """
    stripped = text.strip().lower()

    # Duty done signals
    if stripped in DUTY_DONE_SIGNALS or any(s in stripped for s in DUTY_DONE_SIGNALS):
        return "duty_done"

    # Admin reply: has a date+shift marker (e.g. 13.05.2026(D))
    if _DATE_SHIFT_RE.search(text):
        return "admin_reply"

    # Client order: has one or more mobile numbers
    if _MOBILE_RE.search(text):
        return "order"

    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# Client order parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_escort_order(text: str, client_mobile: Optional[str] = None) -> list[dict]:
    """
    Parse a client WhatsApp escort order message.
    Returns a list of extracted program dicts (one per lighter vessel).

    Each dict contains:
      mother_vessel, lighter_vessel, master_mobile, destination,
      program_date (if found), shift, remarks_raw
    """
    lines = [l.rstrip() for l in text.splitlines()]
    non_empty = [l for l in lines if l.strip()]

    if not non_empty:
        return []

    # ── Step 1: Identify mother vessel ──────────────────────────────────────
    # Mother vessel: English name, does NOT have a mobile number on the same line
    mother_vessel: Optional[str] = None

    # Try first line (most common pattern)
    first_line = non_empty[0].strip()
    if _is_english_name(first_line) and not _MOBILE_RE.search(first_line):
        mother_vessel = first_line
    else:
        # Try last line
        last_line = non_empty[-1].strip()
        if _is_english_name(last_line) and not _MOBILE_RE.search(last_line):
            mother_vessel = last_line
        else:
            # Try any line that looks English and has no mobile
            for line in non_empty:
                l = line.strip()
                if l and _is_english_name(l) and not _MOBILE_RE.search(l) and len(l) >= 4:
                    mother_vessel = l
                    break

    destination = _detect_destination(text)

    # ── Step 2: Extract lighter vessels ─────────────────────────────────────
    # Lines that have a mobile number AND are not the mother vessel line
    programs: list[dict] = []

    # Try serial-prefixed lines first: "07. Atlas 2  01XXXXXXXXX"
    serial_lines = _SERIAL_LINE_RE.findall(text)
    if serial_lines:
        for serial_num, rest in serial_lines:
            mobile = _extract_mobile(rest)
            # Lighter name = rest of line minus the mobile number
            lighter = _MOBILE_RE.sub("", rest).strip(" -|:/,.")
            if not lighter and not mobile:
                continue
            programs.append({
                "serial_number": int(serial_num),
                "mother_vessel": mother_vessel,
                "lighter_vessel": lighter or None,
                "master_mobile": mobile,
                "destination": destination,
                "program_date": None,
                "shift": "D",
                "remarks_raw": text,
            })
    else:
        # Non-serial: each line with a mobile = one lighter
        for line in non_empty:
            mobile = _extract_mobile(line.strip())
            if not mobile:
                continue
            # Skip if this is a date+shift line (admin reply style)
            if _DATE_SHIFT_RE.search(line):
                continue
            lighter = _MOBILE_RE.sub("", line).strip(" -|:/,.")
            # Remove serial prefix
            lighter = re.sub(r"^\d+\.\s*", "", lighter).strip()
            if not lighter:
                lighter = None
            programs.append({
                "serial_number": None,
                "mother_vessel": mother_vessel,
                "lighter_vessel": lighter,
                "master_mobile": mobile,
                "destination": destination,
                "program_date": None,
                "shift": "D",
                "remarks_raw": text,
            })

    # If no programs found but we have the text, return one stub entry
    if not programs and mother_vessel:
        programs.append({
            "serial_number": None,
            "mother_vessel": mother_vessel,
            "lighter_vessel": None,
            "master_mobile": None,
            "destination": destination,
            "program_date": None,
            "shift": "D",
            "remarks_raw": text,
        })

    return programs


# ─────────────────────────────────────────────────────────────────────────────
# Admin reply parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_admin_reply(text: str) -> dict:
    """
    Parse an admin reply message.

    Typical format:
      "Escort: Karim  01XXXXXXXXX
       Vessel: MV Atlas 2
       Date: 13.05.2026(D)
       Destination: Noapara"

    Returns dict with any extracted fields.
    """
    result: dict[str, Any] = {}

    # Date + shift
    dt, shift = _extract_date_shift(text)
    if dt:
        result["start_date"] = dt
    if shift:
        result["start_shift"] = shift

    # Destination
    dest = _detect_destination(text)
    if dest:
        result["destination"] = dest

    # Mobile numbers: first = master_mobile (lighter master), second = escort_mobile
    mobiles = _extract_all_mobiles(text)
    if len(mobiles) >= 1:
        result["master_mobile"] = mobiles[0]
    if len(mobiles) >= 2:
        result["escort_mobile"] = mobiles[1]

    # Vessel names — look for labelled lines
    for line in text.splitlines():
        l = line.strip()
        if not l:
            continue
        ll = l.lower()
        for prefix in ("vessel:", "lighter:", "mv ", "mother vessel:", "lighter vessel:"):
            if ll.startswith(prefix):
                name = l[len(prefix):].strip()
                if not _is_english_name(name):
                    result["lighter_vessel"] = name
                else:
                    result["mother_vessel"] = name
        for prefix in ("escort:", "name:", "escorted by:"):
            if ll.startswith(prefix):
                result["escort_name"] = l[len(prefix):].strip()

    result["raw_text"] = text
    return result


# ─────────────────────────────────────────────────────────────────────────────
# OCR Slip matching
# ─────────────────────────────────────────────────────────────────────────────

def score_slip_match(extracted: dict, program: dict) -> tuple[float, list[str]]:
    """
    Compute a confidence score [0.0, 1.0] between an OCR extraction and a program.
    Returns (confidence, [matched_field_names]).

    Matching fields and weights:
      lighter_vessel: 0.30
      master_mobile:  0.25
      escort_mobile:  0.20
      escort_name:    0.15
      destination:    0.10
    """
    score = 0.0
    matched: list[str] = []

    def _norm(v: Any) -> str:
        return str(v or "").strip().lower()

    # lighter vessel match (partial OK)
    e_lighter = _norm(extracted.get("lighter_vessel") or extracted.get("vessel_name"))
    p_lighter = _norm(program.get("lighter_vessel"))
    if e_lighter and p_lighter:
        if e_lighter == p_lighter:
            score += 0.30
            matched.append("lighter_vessel")
        elif e_lighter in p_lighter or p_lighter in e_lighter:
            score += 0.20
            matched.append("lighter_vessel_partial")

    # master mobile
    e_mobile = _norm(extracted.get("master_mobile") or extracted.get("mobile"))
    p_mobile = _norm(program.get("master_mobile"))
    if e_mobile and p_mobile and (e_mobile[-8:] == p_mobile[-8:]):
        score += 0.25
        matched.append("master_mobile")

    # escort mobile
    e_escort_mob = _norm(extracted.get("escort_mobile"))
    p_escort_mob = _norm(program.get("escort_mobile"))
    if e_escort_mob and p_escort_mob and (e_escort_mob[-8:] == p_escort_mob[-8:]):
        score += 0.20
        matched.append("escort_mobile")

    # escort name
    e_name = _norm(extracted.get("escort_name"))
    p_name = _norm(program.get("escort_name"))
    if e_name and p_name:
        if e_name == p_name:
            score += 0.15
            matched.append("escort_name")
        elif e_name in p_name or p_name in e_name:
            score += 0.08
            matched.append("escort_name_partial")

    # destination
    e_dest = _norm(extracted.get("destination") or extracted.get("release_point"))
    p_dest = _norm(program.get("destination"))
    if e_dest and p_dest and (e_dest in p_dest or p_dest in e_dest):
        score += 0.10
        matched.append("destination")

    return round(score, 4), matched


async def find_slip_matches(extracted: dict, top_n: int = 5) -> list[dict]:
    """
    Find the top N active programs that best match an OCR extraction.
    Queries wbom_escort_programs for active/recent rows and scores them.
    Returns list of {program_id, confidence, matched_fields, ...}.
    """
    from app.database import fetch_all

    candidates = await fetch_all(
        """
        SELECT program_id, mother_vessel, lighter_vessel, master_mobile,
               escort_name, escort_mobile, destination, status, start_date
        FROM wbom_escort_programs
        WHERE status NOT IN ('completed', 'cancelled')
           OR (completion_time > NOW() - INTERVAL '7 days')
        ORDER BY program_date DESC
        LIMIT 200
        """
    )

    results: list[dict] = []
    for prog in candidates:
        prog_dict = dict(prog)
        confidence, matched = score_slip_match(extracted, prog_dict)
        if confidence > 0.1:
            results.append({
                "program_id": prog_dict["program_id"],
                "confidence": confidence,
                "matched_fields": matched,
                "mother_vessel": prog_dict["mother_vessel"],
                "lighter_vessel": prog_dict["lighter_vessel"],
                "master_mobile": prog_dict["master_mobile"],
                "status": prog_dict["status"],
            })

    results.sort(key=lambda x: -x["confidence"])
    return results[:top_n]
