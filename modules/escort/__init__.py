"""
Fazle Core — Vessel Escort & Client Order Processing

FLOW:
  1. escort_client sends message
       → extract MV / lighter(s) / master mobile
       → save to wbom_escort_programs (status='draft', extras in remarks JSON)
       → build admin draft(s)
       → return ("", admin_note)   ← NO reply to client
  2. Admin fills escort name/mobile, sends completed draft back
       → system detects completed draft
       → looks up original client from DB remarks
       → sends finalized slip to client via admin_note routing
       → replies to admin with delivery confirmation
       → updates DB status to 'confirmed'

DB usage (no schema change):
  wbom_escort_programs columns used:
    mother_vessel, lighter_vessel, master_mobile,
    destination, status, contact_id, program_date, remarks,
    escort_name, escort_mobile, escort_employee_id, shift
  remarks stores JSON:
    {sender_phone, source_bridge, escort_name, escort_mobile,
     capacity, importer, cargo_type}
"""

import json
import logging
import re
from datetime import date
from difflib import SequenceMatcher
from typing import Optional, TypedDict

from app.config import get_settings
from app.database import execute, fetch_one, fetch_val, fetch_all

log = logging.getLogger("fazle.escort")
_ESCORT_PROGRAM_COLUMNS_CACHE: Optional[set[str]] = None


# ── TypedDicts ─────────────────────────────────────────────────────────────────

class LighterInfo(TypedDict, total=False):
    lighter_vessel: str
    master_name: Optional[str]
    master_mobile: Optional[str]
    capacity: Optional[str]
    destination: Optional[str]
    shift: Optional[str]
    remarks: Optional[str]


class EscortOrder(TypedDict):
    mother_vessel: Optional[str]
    importer: Optional[str]
    cargo_type: Optional[str]
    lighters: list           # list[LighterInfo]
    date_hint: Optional[str]
    shift: Optional[str]
    raw_text: str


class CompletedDraft(TypedDict):
    mother_vessel: Optional[str]
    lighter_vessel: Optional[str]
    master_mobile: Optional[str]
    cargo_type: Optional[str]
    importer: Optional[str]
    capacity: Optional[str]
    escort_name: Optional[str]
    escort_mobile: Optional[str]
    date_str: Optional[str]
    shift: Optional[str]     # "D" or "N"
    destination: Optional[str]


class ConfirmationContext(TypedDict, total=False):
    bridge_number: str
    recipient_phone: str
    source: str


# ── Compiled patterns ──────────────────────────────────────────────────────────

# Plain BD numbers: 01XXXXXXXXX or 8801XXXXXXXXX (no spaces/dashes)
_MOBILE_RE = re.compile(r"\b((?:880|0)1[3-9]\d{8})\b")

# Formatted BD numbers: +880 1729-455965, +880 1811-505325, etc.
# Pattern: country code + 1[3-9] + 8 more digits (optionally split by space/dash)
_MOBILE_FMT_RE = re.compile(
    r"\+?(?:880|0)[\s\-]?1[3-9](?:[\s\-]?\d){8}"
)

_MV_LABEL_RE = re.compile(
    r"(?:m\.?v\.?|এমভি|mother\s*vessel)\s*[:\s.]*"
    r"([A-Za-z][A-Za-z0-9\s.\-]{1,40}?)(?=\s*[,\n/]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_IMPORTER_RE = re.compile(
    r"(?:a/?c\.?\s*[:.\-]?\s*|account\s*:?\s*|importer\s*:?\s*)\*?([A-Za-z][\w\s&.\-/]{2,80}?)(?=\s*[\n$]|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CARGO_RE = re.compile(
    r"\b(soybeans?\s*meals?|soyabean\s*meal|soybean\s*meal|soya\s*meal|"
    r"soyabean\s*ext|soybean\s*ext|soyabean|soybean|soya|wheat|corn|"
    r"mustard(?:\s*oil)?|coal|sugar|rice|salt|yellow\s*peas|y\.?\s*peas|"
    r"y\s*peas|গম|ভুট্টা|চিনি|কয়লা|চাল|লবণ)\b",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(r"(\d[\d,]*)\s*(?:m\s*\.?\s*t\.?|mt|mts)\b", re.IGNORECASE)
_CAPACITY_LABEL_RE = re.compile(r"(?:cap|capacity)\s*[:.\-]?\s*(\d[\d,]*)\b", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?)\b")
_SHIFT_RE = re.compile(
    r"\(\s*([DN])\s*\)|\b(Day|Night)\b|\b(?:shift\s*[:.\-]?\s*([DN])|([DN])\s*shift)\b",
    re.IGNORECASE,
)

# Completed draft detection
_CD_ESCORT_NAME_RE = re.compile(
    r"escort\s*(?:name)?\s*:\s*([A-Za-zঀ-৿][A-Za-zঀ-৿ \t]{1,20})",
    re.IGNORECASE,
)
_CD_ESCORT_MOB_RE = re.compile(
    r"escort\s*(?:mobile|mob|number)?\s*:\s*(\b(?:880|0)1[3-9]\d{8}\b)",
    re.IGNORECASE,
)
_CD_SHIFT_RE = re.compile(
    r"\(\s*([DN])\s*\)|(?:^|\s)(Day|Night)(?:\s|$)",
    re.IGNORECASE | re.MULTILINE,
)  # also matches bare 'Day' / 'Night' text (no parentheses)
_CD_MV_RE = re.compile(
    r"(?:m\.?v\.?|mother\s*vessel)\s*[:\s.]*([A-Za-z][A-Za-z0-9_/\s.\-]{1,60}?)(?=\s*[\n/]|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_CD_LV_RE = re.compile(
    r"(?:lighter(?:\s*(?:vessel|name))?|lv)\s*:\s*([A-Za-z0-9][A-Za-z0-9_/\s.\-]{1,60}?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CD_MASTER_MOB_RE = re.compile(
    r"(?:master(?:'?s)?\s*(?:number|mobile|mob)?|mob)\s*:\s*(\b(?:880|0)1[3-9]\d{8}\b)",
    re.IGNORECASE,
)
_CD_DEST_RE = re.compile(
    r"destination\s*:\s*(.+?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_AL_AQSA_RE = re.compile(r"al.?aqsa|আল.?আকসা", re.IGNORECASE)

# O/a to <destination> pattern for inbound messages
_OA_DEST_RE = re.compile(
    r"(?:o/?a|alongside)\s+(?:to\s+)?([A-Za-z][A-Za-z0-9\s,.\-]{2,25}?)(?=\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

# detect inbound messages where lighter is listed first with At-O/A marker
_AT_OA_MV_RE = re.compile(
    r"(?:at[-\s]*o/?a|alongside)\s*[:\s]*([A-Za-z][A-Za-z0-9\s.\-]{1,40}?)(?=\s*[\n/]|\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

# Mv. <name> block line detection (for multi-Mv block format)
_MV_BLOCK_LINE_RE = re.compile(
    r"^\s*(?:[^\w]*)(?:M\.?V\.?)\s+([^,/\n]{2,40}?)(?:[,/].*)?$",
    re.IGNORECASE,
)

# Master: / Mob: <phone> on a line (formatted or plain, handles +880 1729-455965 style)
_MASTER_LINE_RE = re.compile(
    r"(?:master|mob|mobile|phone|tel)\s*[:\s]+(\+?(?:880|0)[\s\-]?1[3-9](?:[\s\-]?\d){8})",
    re.IGNORECASE,
)

_MV_LABEL_ANY_RE = re.compile(r"\b(?:m\.?\s*v\.?|mother\s+vessel|এমভি|at[-\s]*o/?a)\b", re.IGNORECASE)
_SERIAL_PREFIX_RE = re.compile(r"^\s*(?:[-–]?\s*\d{1,3}\s*[\).\-/]?\s*)")

_CARGO_MAP = [
    (re.compile(r"soy(?:a|a?bean)?s?\s*meals?|soya\s*meal", re.I), "Soybean Meal"),
    (re.compile(r"soy(?:a|a?bean)?\s*ext", re.I), "Soybean Extract"),
    (re.compile(r"soyabean|soybean|soya", re.I), "Soybean"),
    (re.compile(r"wheat|গম", re.I), "Wheat"),
    (re.compile(r"corn|ভুট্টা", re.I), "Corn"),
    (re.compile(r"mustard(?:\s*oil)?", re.I), "Mustard"),
    (re.compile(r"coal|কয়লা", re.I), "Coal"),
    (re.compile(r"sugar|চিনি", re.I), "Sugar"),
    (re.compile(r"rice|চাল", re.I), "Rice"),
    (re.compile(r"salt|লবণ", re.I), "Salt"),
    (re.compile(r"yellow\s*peas|y\.?\s*peas|y\s*peas", re.I), "Yellow Peas"),
]

_KNOWN_IMPORTER_RE = re.compile(
    r"\b(new\s+hope(?:\s+feed)?(?:\s*&\s*new\s+hope\s+animal)?|"
    r"kazi\s+firm|abul\s+khair|nabil|akij|sarkar|quality)\b",
    re.IGNORECASE,
)

_DEST_ALIASES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(?:n\.?\s*bari|nagar\s*bari|nagarbari)\b", re.I), "Nagarbari"),
    (re.compile(r"\b(?:n\.?\s*para|noapara|nowapara|nopara|rupsi)\b", re.I), "Noapara"),
    (re.compile(r"\b(?:n\.?\s*g[oa]nj|narayan\s*g[oa]nj|narayanganj|rupshi|rupahi)\b", re.I), "Narayanganj"),
    (re.compile(r"\bashuganj\b", re.I), "Ashuganj"),
    (re.compile(r"\baricha\b", re.I), "Aricha"),
    (re.compile(r"\bbhairab\b", re.I), "Bhairab"),
    (re.compile(r"\bchandpur\b", re.I), "Chandpur"),
    (re.compile(r"\bbarishal|barisal\b", re.I), "Barishal"),
    (re.compile(r"\bmongla\b", re.I), "Mongla"),
    (re.compile(r"\b(?:local|ctg|chittagong|chattogram)\b", re.I), "Chattogram"),
)


# ── Mobile helpers ─────────────────────────────────────────────────────────────

def _normalize_mobile(raw: str) -> Optional[str]:
    """Normalize a raw mobile string (may contain +, spaces, dashes) to 01XXXXXXXXX."""
    digits = re.sub(r"[\s\-+]", "", raw)
    if digits.startswith("880") and len(digits) == 13:
        return "0" + digits[3:]
    if len(digits) == 11 and digits.startswith("01") and digits[2] in "3456789":
        return digits
    return None


def _extract_mobile(text: str) -> Optional[str]:
    """Extract and normalize the first mobile number from text (plain or formatted)."""
    m = _MOBILE_RE.search(text)
    if m:
        return _normalize_mobile(m.group(1))
    m = _MOBILE_FMT_RE.search(text)
    if m:
        return _normalize_mobile(m.group(0))
    return None


# ── Extraction helpers ─────────────────────────────────────────────────────────

def _extract_all_mobiles(text: str) -> list[str]:
    seen: list[str] = []
    for m in _MOBILE_FMT_RE.finditer(text):
        mobile = _normalize_mobile(m.group(0))
        if mobile and mobile not in seen:
            seen.append(mobile)
    return seen


def _same_phone(a: Optional[str], b: Optional[str]) -> bool:
    na = _normalize_mobile(a or "")
    nb = _normalize_mobile(b or "")
    return bool(na and nb and na == nb)


def _escort_client_phone_list() -> list[str]:
    raw = getattr(get_settings(), "escort_client_phones", "") or ""
    numbers: list[str] = []
    for item in raw.split(","):
        mobile = _normalize_mobile(item.strip())
        if mobile and mobile not in numbers:
            numbers.append(mobile)
    return numbers


def _bridge_number_map() -> dict[str, str]:
    s = get_settings()
    return {
        "bridge1": _normalize_mobile(s.bridge1_number or "") or "",
        "bridge2": _normalize_mobile(s.bridge2_number or "") or "",
    }


def _is_allowed_escort_client(sender_phone: str) -> bool:
    clients = _escort_client_phone_list()
    if not clients:
        return True
    sender_norm = _normalize_mobile(sender_phone or "")
    return bool(sender_norm and sender_norm in clients)


def _is_trusted_escort_source(source: str) -> bool:
    raw = getattr(get_settings(), "escort_trusted_sources", "bridge1,bridge2,meta") or ""
    return source in {s.strip() for s in raw.split(",") if s.strip()}


def _normalize_capacity(value: str, *, allow_bare: bool = False) -> Optional[str]:
    m = _CAPACITY_LABEL_RE.search(value or "") or _CAPACITY_RE.search(value or "")
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        return f"{digits} MT" if digits else None
    if not allow_bare:
        return None
    scrubbed = _MOBILE_FMT_RE.sub(" ", value or "")
    # Field messages often end a lighter line with bare 900/1000/1200.
    # Keep the range narrow so serials, dates, and phone fragments do not turn
    # into capacity.
    bare = re.search(r"(?<!\d)(9\d{2}|1[01]\d{2}|1200)(?!\d)", scrubbed)
    digits = bare.group(1) if bare else ""
    return f"{digits} MT" if digits else None


def _extract_shift(text: str) -> Optional[str]:
    m = _SHIFT_RE.search(text or "")
    if not m:
        return None
    for group in m.groups():
        if not group:
            continue
        value = group.upper()
        if value in ("D", "N"):
            return value
        if value == "DAY":
            return "D"
        if value == "NIGHT":
            return "N"
    return None


def _normalize_cargo(text: str) -> Optional[str]:
    for pattern, value in _CARGO_MAP:
        if pattern.search(text or ""):
            return value
    return None


def _normalize_destination(text: str) -> Optional[str]:
    if not text:
        return None
    for pattern, value in _DEST_ALIASES:
        if pattern.search(text):
            return value
    labeled = re.search(
        r"(?:dest(?:ination)?|o/?a\s+to|o/?a|alongside|at[-\s]*o/?a)\s*[:.\-]?\s*([A-Za-z][A-Za-z\s.\-]{2,30})",
        text,
        re.IGNORECASE,
    )
    if labeled:
        return labeled.group(1).strip(" .,-")
    return None


def _clean_vessel_name(raw: str, *, force_mv: bool = False) -> str:
    name = raw or ""
    name = re.sub(r"\$+", "", name)
    name = _SERIAL_PREFIX_RE.sub("", name)
    name = re.sub(r"\b(?:mother\s+vessel|lighter\s+vessel|lighter|vessel|m\.?\s*v\.?|m/v|এমভি)\b", "", name, flags=re.I)
    name = re.sub(r"(?:a/?c\.?\s*[:.\-]?.*)$", "", name, flags=re.I)
    name = re.sub(r"(?:cap(?:acity)?|dest(?:ination)?|master|mob(?:ile)?|phone)\s*[:.\-]?.*$", "", name, flags=re.I)
    name = _MOBILE_FMT_RE.sub("", name)
    name = _CAPACITY_RE.sub("", name)
    name = _CARGO_RE.sub("", name)
    for pattern, _value in _DEST_ALIASES:
        name = pattern.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" -|:/,.\n\t")
    if force_mv and name and not name.upper().startswith("MV "):
        return f"MV {name.upper()}"
    return name.upper() if force_mv else name


def _looks_like_non_vessel_context(line: str) -> bool:
    return bool(re.search(
        r"\b(?:a/?c|account|importer|cargo|product|cap(?:acity)?|dest(?:ination)?|"
        r"master|mob(?:ile)?|phone|tel|escort|date|shift|best regards|thanks|"
        r"o/?a|alongside|at[-\s]*o/?a)\b",
        line or "",
        re.I,
    ))


def _strip_inline_importer_from_mv(line: str) -> str:
    cargo_m = _CARGO_RE.search(line or "")
    if not cargo_m:
        return line
    before_cargo = line[:cargo_m.start()].strip(" .,-")
    importer_matches = list(_KNOWN_IMPORTER_RE.finditer(before_cargo))
    if not importer_matches:
        return before_cargo
    last = importer_matches[-1]
    if before_cargo[last.end():].strip(" .,-"):
        return before_cargo
    return before_cargo[:last.start()].strip(" .,-") or before_cargo


def _line_has_mobile(line: str) -> bool:
    return bool(_extract_mobile(line))


def _extract_mother_vessel_canonical(lines: list[str], full_text: str) -> Optional[str]:
    candidates: list[tuple[int, str]] = []
    for line in lines:
        if _line_has_mobile(line):
            continue
        score = 0
        if _MV_LABEL_ANY_RE.search(line):
            score += 4
        if _IMPORTER_RE.search(line):
            score += 2
        if _CARGO_RE.search(line):
            score += 2
        if re.search(r"\b(?:a/?c|account|importer|cargo|product)\b", line, re.I):
            score += 1
        if score <= 0:
            continue
        line_for_mv = _strip_inline_importer_from_mv(line)
        if re.search(r"\bat[-\s]*o/?a\b", line, re.I):
            m = re.search(r"\bat[-\s]*o/?a\s*[:.\-]?\s*(.+)$", line, re.I)
            if m:
                candidates.append((score, _strip_inline_importer_from_mv(m.group(1))))
                continue
        candidates.append((score, line_for_mv))

    if not candidates:
        non_mobile = [ln for ln in lines if not _line_has_mobile(ln)]
        for idx, line in enumerate(non_mobile):
            if _looks_like_non_vessel_context(line):
                continue
            score = 1 if idx == 0 else 0
            if line.upper().startswith("MV "):
                score += 2
            if score > 0:
                candidates.append((score, line))

    candidates.sort(key=lambda item: item[0], reverse=True)
    for _score, candidate in candidates:
        name = _clean_vessel_name(candidate, force_mv=True)
        if name:
            return name
    return _extract_mother_vessel(full_text)


def _extract_importer(text: str) -> Optional[str]:
    m = _IMPORTER_RE.search(text or "")
    if m:
        importer = m.group(1).strip(" .,-")
        importer = re.split(r"\b(?:wheat|corn|soy|coal|sugar|rice|salt|cap|capacity|dest|destination)\b", importer, flags=re.I)[0]
        return importer.strip(" .,-") or None

    for line in (text or "").splitlines():
        if _line_has_mobile(line) or not _MV_LABEL_ANY_RE.search(line) or not _CARGO_RE.search(line):
            continue
        before_cargo = line[:_CARGO_RE.search(line).start()].strip(" .,-")
        importer_matches = list(_KNOWN_IMPORTER_RE.finditer(before_cargo))
        if importer_matches:
            return importer_matches[-1].group(1).strip(" .,-")
    return None


def _line_context(lines: list[str], idx: int) -> str:
    start = max(0, idx - 2)
    end = min(len(lines), idx + 3)
    return "\n".join(lines[start:end])


def _extract_lighter_name_from_context(lines: list[str], idx: int, raw_mobile: str) -> Optional[str]:
    line = lines[idx]
    mobile_pos = line.find(raw_mobile)
    before = line[:mobile_pos] if mobile_pos >= 0 else _MOBILE_FMT_RE.split(line)[0]
    name = _clean_vessel_name(before)
    if name and not re.fullmatch(r"(?:master|mob|mobile|phone|tel|mst|m no)", name, flags=re.I):
        return name

    # If the mobile is on a separate Master/Mob line, the previous vessel-like
    # line is the lighter. This resolves "02) MV AL MORIUM" + next-line mobile.
    for j in range(idx - 1, max(-1, idx - 4), -1):
        prev = lines[j].strip()
        if not prev or _line_has_mobile(prev):
            continue
        if _looks_like_non_vessel_context(prev):
            continue
        candidate = _clean_vessel_name(prev)
        if candidate:
            return candidate
    return None


def _parse_mobile_grouped_lighters(lines: list[str]) -> list[LighterInfo]:
    lighters: list[LighterInfo] = []
    seen: set[tuple[str, str]] = set()
    for idx, line in enumerate(lines):
        mobile_matches = list(_MOBILE_FMT_RE.finditer(line))
        mobiles = [_normalize_mobile(m.group(0)) for m in mobile_matches]
        mobiles = [m for m in mobiles if m]
        if not mobiles:
            continue
        context = _line_context(lines, idx)
        name = _extract_lighter_name_from_context(lines, idx, mobile_matches[0].group(0))
        if not name:
            continue
        capacity = _normalize_capacity(line, allow_bare=True) or _normalize_capacity(context, allow_bare=True)
        destination = _normalize_destination(line) or _normalize_destination(context)
        shift = _extract_shift(context)
        remarks = None
        if len(mobiles) > 1:
            remarks = "Additional mobile(s): " + ", ".join(mobiles[1:])
        key = (name.lower(), mobiles[0])
        if key in seen:
            continue
        seen.add(key)
        lighters.append(LighterInfo(
            lighter_vessel=name,
            master_name=None,
            master_mobile=mobiles[0],
            capacity=capacity,
            destination=destination,
            shift=shift,
            remarks=remarks,
        ))
    return lighters

def _extract_mother_vessel(text: str) -> Optional[str]:
    # If 'At-O/A MV ...' pattern present, use that as the mother vessel
    at_oa = _AT_OA_MV_RE.search(text)
    if at_oa:
        name = at_oa.group(1).strip().rstrip(".,- ")
        name = re.sub(r"^MV\.?\s*", "", name, flags=re.IGNORECASE).strip()
        return f"MV {name.upper()}"

    m = _MV_LABEL_RE.search(text)
    if not m:
        return None
    name = m.group(1).strip().rstrip(".,- ")
    # Strip double MV/MV. prefix (e.g. 'MV. EVA PARIS' → 'MV EVA PARIS')
    name = re.sub(r"^MV\.?\s+", "", name, flags=re.IGNORECASE).strip() or name
    return f"MV {name.upper()}"


def _parse_lighter_block(section: str) -> Optional[LighterInfo]:
    """Parse one labeled 'Lighter: ...' block."""
    lines = section.strip().splitlines()
    if not lines:
        return None

    # First line: lighter name (strip the "Lighter[: ...]" label)
    lighter_name = re.sub(
        r"^lighter\s*(?:vessel\s*)?:?\s*", "", lines[0], flags=re.IGNORECASE
    ).strip()
    if not lighter_name:
        return None

    rest = "\n".join(lines[1:])

    master_name_m = re.search(
        r"master\s*:?\s*(.+?)(?=\s*\n|mob|$)", rest, re.IGNORECASE | re.MULTILINE
    )
    master_name = master_name_m.group(1).strip() if master_name_m else None

    master_mobile = _extract_mobile(rest)

    cap_m = _CAPACITY_RE.search(rest)
    capacity = f"{cap_m.group(1)} MT" if cap_m else None

    dest_m = re.search(
        r"(?:dest(?:ination)?|going\s*to|o/?a\s+(?:to\s+)?)\s*:?\s*(.+?)(?=\s*\n|$)",
        rest, re.IGNORECASE | re.MULTILINE,
    )
    raw_destination = dest_m.group(1).strip() if dest_m else None
    destination = _normalize_destination(raw_destination or "") or raw_destination

    return LighterInfo(
        lighter_vessel=lighter_name,
        master_name=master_name,
        master_mobile=master_mobile,
        capacity=capacity,
        destination=destination,
    )


def _parse_labeled_lighters(text: str) -> list:
    """Split on 'Lighter:' or 'Lighter Vessel:' labels and parse each block."""
    # Split keeping the delimiter in each segment
    parts = re.split(r"(?=\blighter\s*(?:vessel\s*)?:)", text, flags=re.IGNORECASE)
    lighters = []
    for part in parts:
        if re.match(r"lighter\s*(?:vessel\s*)?:", part, re.IGNORECASE):
            li = _parse_lighter_block(part)
            if li:
                lighters.append(li)
    return lighters


def _parse_inline_lighters(text: str) -> list:
    """
    Handle compact format: '1.LighterName-01712345678 Destination 700 MT'
    or bare 'LighterName 01712345678 Destination capacity' lines.
    """
    lighters = []

    # Numbered format with PHONE: label: "15. MAKSUDA, PHONE: 01745377025"
    numbered_phone = re.compile(
        r"^\d+\.\s*([A-Za-z][A-Za-z0-9\s]{1,30}?)\s*,\s*PHONE\s*:\s*"
        r"(\b(?:880|0)1[3-9]\d{8}\b)\s*(.+)?$",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in numbered_phone.finditer(text):
        name = m.group(1).strip()
        mobile = m.group(2)
        rest = (m.group(3) or "").strip()
        cap_m = _CAPACITY_RE.search(rest)
        capacity = f"{cap_m.group(1)} MT" if cap_m else None
        dest = re.sub(_CAPACITY_RE, "", rest).strip() or None
        lighters.append(LighterInfo(
            lighter_vessel=name,
            master_name=None,
            master_mobile=mobile,
            capacity=capacity,
            destination=dest,
        ))

    if lighters:
        return lighters

    # Numbered format: digit. Name-mobile rest
    numbered = re.compile(
        r"^\d+\.\s*([A-Za-z][A-Za-z0-9\s]{1,30}?)\s*[-–]\s*"
        r"(\b(?:880|0)1[3-9]\d{8}\b)\s*(.+)?$",
        re.MULTILINE,
    )
    for m in numbered.finditer(text):
        name = m.group(1).strip()
        mobile = m.group(2)
        rest = (m.group(3) or "").strip()
        cap_m = _CAPACITY_RE.search(rest)
        capacity = f"{cap_m.group(1)} MT" if cap_m else None
        dest = re.sub(_CAPACITY_RE, "", rest).strip() or None
        lighters.append(LighterInfo(
            lighter_vessel=name,
            master_name=None,
            master_mobile=mobile,
            capacity=capacity,
            destination=dest,
        ))

    if lighters:
        return lighters

    # Last resort: any mobile number on same line as a vessel-like word
    for m in _MOBILE_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.end())
        if line_end == -1:
            line_end = len(text)
        line = text[line_start:line_end]

        before = line[: m.start() - line_start].strip()
        after = line[m.end() - line_start :].strip()

        # Skip if "before" looks like a label (master, mob, escort…)
        if re.match(r"(master|mob|mobile|escort|phone|tel)", before, re.IGNORECASE):
            continue
        if before:
            cap_m = _CAPACITY_RE.search(after)
            lighters.append(LighterInfo(
                lighter_vessel=before,
                master_name=None,
                master_mobile=m.group(1),
                capacity=f"{cap_m.group(1)} MT" if cap_m else None,
                destination=None,
            ))

    return lighters


def _parse_mv_block_lighters(text: str) -> list:
    """
    Parse messages where lighter vessels are 'Mv. <name>' blocks followed by
    O/a to / Capacity / Master lines (and the mother vessel is the first MV line
    with no phone in its lookahead).

    Handles examples like:
      MV MARIMYR A , SOYBEAN MEAL        ← mother vessel (first, no adjacent phone)
      Mv. Fazlay Khoda                   ← lighter name
      O/a to Narayanganj                 ← destination
      Capacity: 1100                     ← capacity
      Master: +880 1811-505325           ← master mobile (formatted)
    """
    lines = text.splitlines()
    lighters = []
    i = 0

    _cap_re = re.compile(r"(?:capacity|cap)\s*[:\s]*(\d[\d,]+)", re.IGNORECASE)

    while i < len(lines):
        line = lines[i].strip()
        mv_m = _MV_BLOCK_LINE_RE.match(line)

        if mv_m:
            name_raw = mv_m.group(1).strip().rstrip("., ")

            # Collect lookahead lines (stop at next Mv. block or blank line)
            j = i + 1
            lookahead = []
            while j < len(lines) and len(lookahead) < 5:
                nxt = lines[j].strip()
                if not nxt:
                    break
                if _MV_BLOCK_LINE_RE.match(nxt):
                    break
                lookahead.append(nxt)
                j += 1

            lookahead_text = "\n".join(lookahead)
            master_m = _MASTER_LINE_RE.search(lookahead_text)

            if master_m:
                mobile = _normalize_mobile(master_m.group(1))
                dest_m = _OA_DEST_RE.search(lookahead_text)
                destination = dest_m.group(1).strip().rstrip(",. ") if dest_m else None
                cap_m = _cap_re.search(lookahead_text)
                capacity = f"{cap_m.group(1)} MT" if cap_m else None

                if name_raw and mobile:
                    lighters.append(LighterInfo(
                        lighter_vessel=name_raw,
                        master_name=None,
                        master_mobile=mobile,
                        capacity=capacity,
                        destination=destination,
                    ))
                i = j
                continue

        i += 1

    return lighters


def parse_escort_message(text: str) -> EscortOrder:
    """Extract vessel/lighter info from a client escort-buying order.

    Canonical rule: a vessel line with a mobile number is a lighter vessel,
    even when it contains an MV label. A vessel line without a mobile number
    and with an MV/Mother/At-O/A label is the mother vessel.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    mother_vessel = _extract_mother_vessel_canonical(lines, text)

    importer = _extract_importer(text)
    cargo_type = _normalize_cargo(text)

    date_m = _DATE_RE.search(text)
    date_hint = date_m.group(1) if date_m else None
    shift = _extract_shift(text)

    # Canonical phone-first parser, then legacy fallbacks.
    lighters = _parse_mobile_grouped_lighters(lines)
    if not lighters:
        lighters = _parse_labeled_lighters(text)
    if not lighters:
        lighters = _parse_inline_lighters(text)
    if not lighters:
        lighters = _parse_mv_block_lighters(text)

    for lighter in lighters:
        if lighter.get("capacity"):
            lighter["capacity"] = _normalize_capacity(lighter["capacity"]) or lighter["capacity"]
        if lighter.get("destination"):
            lighter["destination"] = _normalize_destination(lighter["destination"]) or lighter["destination"]
        if shift and not lighter.get("shift"):
            lighter["shift"] = shift

    return EscortOrder(
        mother_vessel=mother_vessel,
        importer=importer,
        cargo_type=cargo_type,
        lighters=lighters,
        date_hint=date_hint,
        shift=shift,
        raw_text=text,
    )


def is_minimum_escort_order(order: EscortOrder) -> bool:
    """Client order is valid only when at least one lighter has MV + LV + master mobile."""
    if not order.get("mother_vessel"):
        return False
    for lighter in order.get("lighters", []):
        if lighter.get("lighter_vessel") and lighter.get("master_mobile"):
            return True
    return False


# ── Draft builders ─────────────────────────────────────────────────────────────

_TODAY = lambda: date.today().strftime("%d.%m.%Y")


def build_admin_draft(
    mv: str,
    lighter: LighterInfo,
    date_str: Optional[str] = None,
    *,
    client_phone: str = "",
    importer: Optional[str] = None,
    cargo_type: Optional[str] = None,
    shift: Optional[str] = None,
) -> str:
    """Build the standardized draft to send to admin for one lighter."""
    d = date_str or _TODAY()
    shift_label = shift or lighter.get("shift") or "D/N"
    lines = [
        f"Cargo: {cargo_type or ''}",
        f"Client: {client_phone or ''}",
        "",
        f"Mother Vessel: {mv or ''}",
        f"Importer: {importer or ''}",
        f"Lighter Vessel: {lighter['lighter_vessel']}",
        f"Master Mobile: {lighter['master_mobile'] or ''}",
        f"Destination: {lighter.get('destination') or ''}",
        f"Capacity: {lighter.get('capacity') or ''}",
        "Escort Name:",
        "Escort Mobile:",
        f"{d} ({shift_label})",
        "Al-Aqsa Security & Logistics Services Ltd",
        "",
        "─────────────────",
        "Automated Reply System",
        "এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।",
    ]
    return "\n".join(lines)


def build_admin_message(order: EscortOrder, sender_phone: str) -> str:
    """Build the full admin message for one escort order (may include multiple lighters)."""
    mv = order["mother_vessel"] or "Unknown MV"
    parts = []
    footer = [
        "─────────────────",
        "Automated Reply System",
        "এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।",
    ]

    for i, lighter in enumerate(order["lighters"], 1):
        if len(order["lighters"]) > 1:
            parts.append(f"--- Lighter {i} ---")
        draft_text = build_admin_draft(
            mv,
            lighter,
            order.get("date_hint"),
            client_phone=sender_phone,
            importer=order.get("importer"),
            cargo_type=order.get("cargo_type"),
            shift=order.get("shift"),
        )
        draft_lines = draft_text.splitlines()
        if len(draft_lines) >= 3 and draft_lines[-3:] == footer:
            draft_text = "\n".join(draft_lines[:-3]).rstrip()
        parts.append(draft_text)
        parts.append("")

    if not order["lighters"]:
        # No lighter extracted — send partial draft for admin to fill
        dummy = LighterInfo(
            lighter_vessel="(লাইটার নাম যোগ করুন)",
            master_name=None,
            master_mobile=None,
            capacity=None,
            destination=None,
            shift=order.get("shift"),
        )
        draft_text = build_admin_draft(
            mv,
            dummy,
            order.get("date_hint"),
            client_phone=sender_phone,
            importer=order.get("importer"),
            cargo_type=order.get("cargo_type"),
            shift=order.get("shift"),
        )
        draft_lines = draft_text.splitlines()
        if len(draft_lines) >= 3 and draft_lines[-3:] == footer:
            draft_text = "\n".join(draft_lines[:-3]).rstrip()
        parts.append(draft_text)

    body = "\n".join(parts).rstrip()
    return body + "\n\n" + "\n".join(footer)


# ── Completed draft detection & parsing ───────────────────────────────────────

def is_completed_escort_draft(text: str) -> bool:
    """Return True if admin message is a filled-in escort draft."""
    draft = parse_completed_draft(text)
    has_al_aqsa = bool(_AL_AQSA_RE.search(text))
    return bool(
        has_al_aqsa
        and draft.get("mother_vessel")
        and draft.get("lighter_vessel")
        and draft.get("escort_name")
        and draft.get("escort_mobile")
        and draft.get("date_str")
    )


def parse_completed_draft(text: str) -> CompletedDraft:
    """Extract all fields from admin's completed draft."""
    mv_m = _CD_MV_RE.search(text)
    lv_m = _CD_LV_RE.search(text)
    master_mob_m = _CD_MASTER_MOB_RE.search(text)
    escort_name_m = _CD_ESCORT_NAME_RE.search(text)
    escort_mob_m = _CD_ESCORT_MOB_RE.search(text)
    date_m = _DATE_RE.search(text)
    shift_m = _CD_SHIFT_RE.search(text)
    dest_m = _CD_DEST_RE.search(text)

    mv = mv_m.group(1).strip() if mv_m else None
    if mv:
        # Strip double MV/MV. prefix (e.g. 'MV. EVA PARIS' → 'MV EVA PARIS')
        mv = re.sub(r"^MV\.?\s+", "", mv, flags=re.IGNORECASE).strip()
        if not mv.upper().startswith("MV"):
            mv = f"MV {mv.upper()}"
        else:
            mv = mv.upper()

    # Parse shift from '(D)'/'(N)' OR bare 'Day'/'Night' text
    shift = None
    if shift_m:
        if shift_m.group(1):   # matched (D) or (N)
            shift = shift_m.group(1).upper()
        elif shift_m.group(2):  # matched 'Day' or 'Night'
            shift = "D" if shift_m.group(2).lower() == "day" else "N"

    raw_destination = dest_m.group(1).strip() if dest_m else None
    destination = _normalize_destination(raw_destination or "") or raw_destination
    capacity = _normalize_capacity(text)
    importer = _extract_importer(text)
    cargo_type = _normalize_cargo(text)

    return CompletedDraft(
        mother_vessel=mv,
        lighter_vessel=lv_m.group(1).strip() if lv_m else None,
        master_mobile=master_mob_m.group(1) if master_mob_m else None,
        cargo_type=cargo_type,
        importer=importer,
        capacity=capacity,
        escort_name=escort_name_m.group(1).strip() if escort_name_m else None,
        escort_mobile=escort_mob_m.group(1) if escort_mob_m else None,
        date_str=date_m.group(1) if date_m else _TODAY(),
        shift=shift,
        destination=destination,
    )


def build_final_slip(draft: CompletedDraft, db_destination: Optional[str] = None) -> str:
    """Build the finalized slip to send to the original client.

    Field labels and footer match the exact format used in the field:
      Mother Vessel / Lighter Vessel / Master Mobile / Destination /
      Escort Name / Escort Mobile / Start Date (D/N) / Al-Aqsa Security Service
    """
    shift_label = f" ({draft['shift']})" if draft.get("shift") else ""
    destination = draft.get("destination") or db_destination or ""
    lines = [
        f"Mother Vessel: {draft['mother_vessel'] or '—'}",
        f"Lighter Vessel: {draft['lighter_vessel'] or '—'}",
        f"Master Mobile: {draft['master_mobile'] or '—'}",
        f"Destination: {destination or '—'}",
        f"Escort Name: {draft['escort_name'] or '—'}",
        f"Escort Mobile: {draft['escort_mobile'] or '—'}",
        f"Start Date: {draft['date_str'] or _TODAY()}{shift_label}",
        "Al-Aqsa Security Service",
    ]
    return "\n".join(lines)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_contact_id(phone: str) -> Optional[int]:
    row = await fetch_one(
        "SELECT contact_id FROM wbom_contacts WHERE whatsapp_number = $1 LIMIT 1",
        phone,
    )
    return row["contact_id"] if row else None


async def save_escort_programs(
    order: EscortOrder,
    sender_phone: str,
    source: str,
) -> list:
    """Save one DB row per valid lighter block only."""
    program_ids = []
    mv = order["mother_vessel"] or ""
    contact_id = await _get_contact_id(sender_phone)
    if not mv:
        return program_ids

    lighters_to_save = [
        lighter for lighter in order["lighters"]
        if lighter.get("lighter_vessel") and lighter.get("master_mobile")
    ]
    if not lighters_to_save:
        return program_ids

    mv_clean = mv.upper().replace("MV ", "").strip()
    order_shift = order.get("shift") or "D"

    for lighter in lighters_to_save:
        remarks_data = {
            "sender_phone": sender_phone,
            "source_bridge": source,
            "escort_name": None,
            "escort_mobile": None,
            "capacity": lighter.get("capacity"),
            "importer": order.get("importer"),
            "cargo_type": order.get("cargo_type"),
            "date_hint": order.get("date_hint"),
            "shift": lighter.get("shift") or order_shift,
        }
        try:
            if mv_clean:
                lv_clean = (lighter["lighter_vessel"] or "").strip()
                dup = await fetch_one(
                    """
                    SELECT program_id FROM wbom_escort_programs
                    WHERE COALESCE(master_mobile, '') = $1
                      AND UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $2
                      AND status NOT IN ('cancelled')
                      AND ($3::date IS NULL OR program_date = $3)
                      AND (
                           $4::int IS NULL
                           OR contact_id = $4
                           OR remarks::text ILIKE '%' || $5 || '%'
                      )
                    ORDER BY program_id DESC LIMIT 1
                    """,
                    lighter.get("master_mobile") or "",
                    mv_clean,
                    _parse_program_date(order.get("date_hint")),
                    contact_id,
                    sender_phone,
                )
                if dup:
                    pid = dup["program_id"]
                    log.info(f"[escort] dedup skip: mv={mv} lv={lighter['lighter_vessel']} existing={pid}")
                    program_ids.append(pid)
                    await _sync_roster_draft(pid)
                    continue

            row = await fetch_one(
                """
                INSERT INTO wbom_escort_programs (
                    mother_vessel, lighter_vessel, master_mobile,
                    destination, status, contact_id,
                    program_date, shift, remarks
                ) VALUES ($1, $2, $3, $4, 'draft', $5, CURRENT_DATE, $6, $7)
                RETURNING program_id
                """,
                mv,
                lighter["lighter_vessel"],
                lighter["master_mobile"] or "",
                lighter.get("destination") or "",
                contact_id,
                lighter.get("shift") or order_shift,
                json.dumps(remarks_data, ensure_ascii=False),
            )
            if row:
                pid = row["program_id"]
                program_ids.append(pid)
                log.info(f"[escort] saved program_id={pid} mv={mv} lv={lighter['lighter_vessel']}")
                await _sync_roster_draft(pid)
        except Exception as e:
            log.error(f"[escort] save error: {e}")

    return program_ids


async def _find_pending_program(
    mv: Optional[str],
    lv: Optional[str],
    *,
    master_mobile: Optional[str] = None,
    program_date=None,
    client_phone: Optional[str] = None,
) -> Optional[dict]:
    """Find most recent draft with master mobile as primary match signal."""
    mv_clean = (mv or "").upper().replace("MV.", "").replace("MV ", "").strip()
    lv_clean = (lv or "").strip()
    master_norm = _normalize_mobile(master_mobile or "") if master_mobile else None

    try:
        candidates = await fetch_all(
            """SELECT program_id, remarks, mother_vessel, lighter_vessel,
                      destination, master_mobile, program_date
               FROM wbom_escort_programs
               WHERE status = 'draft'
                 AND ($1::text IS NULL OR COALESCE(master_mobile, '') = $1)
                 AND ($2::date IS NULL OR program_date = $2)
               ORDER BY program_date DESC, program_id DESC
               LIMIT 50""",
            master_norm,
            program_date,
        )
        if not candidates and mv_clean:
            candidates = await fetch_all(
                """SELECT program_id, remarks, mother_vessel, lighter_vessel,
                          destination, master_mobile, program_date
                   FROM wbom_escort_programs
                   WHERE status = 'draft'
                     AND UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $1
                     AND ($2::date IS NULL OR program_date = $2)
                   ORDER BY program_date DESC, program_id DESC
                   LIMIT 50""",
                mv_clean,
                program_date,
            )
        if not candidates:
            return None

        best = None
        best_score = -1.0
        for row in candidates:
            if client_phone and not _remarks_client_matches(row["remarks"], client_phone):
                continue
            score = 0.0
            if master_norm and (row["master_mobile"] or "") == master_norm:
                score += 5.0
            if program_date and row["program_date"] == program_date:
                score += 3.0
            row_mv = (row["mother_vessel"] or "").upper().replace("MV.", "").replace("MV ", "").strip()
            if mv_clean:
                mv_ratio = SequenceMatcher(None, mv_clean, row_mv).ratio()
                score += mv_ratio * 3.0
            if lv_clean and row.get("lighter_vessel"):
                lv_ratio = SequenceMatcher(None, lv_clean.lower(), (row["lighter_vessel"] or "").lower()).ratio()
                score += lv_ratio
            if score > best_score:
                best_score = score
                best = row
        if best and best_score >= 5.5:
            return dict(best)
    except Exception as e:
        log.error(f"[escort] find program error: {e}")
    return None


def _remarks_client_matches(remarks_text: Optional[str], client_phone: Optional[str]) -> bool:
    if not client_phone:
        return True
    try:
        remarks = json.loads(remarks_text or "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    return (remarks.get("sender_phone") or "") == client_phone


def _parse_program_date(date_str: Optional[str]):
    """Parse date strings like '11-05-2026', '11.05.26', '11/05/2026'."""
    if not date_str:
        return None
    from datetime import datetime as _dt
    for fmt in ("%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y",
                "%d-%m-%y", "%d.%m.%y", "%d/%m/%y"):
        try:
            return _dt.strptime(date_str.strip(), fmt).date()
        except ValueError:
            pass
    return None


async def _resolve_escort_employee_id(
    escort_mobile: str,
    escort_name: Optional[str] = None,
    *,
    auto_create: bool = False,
) -> Optional[int]:
    """Look up or create wbom_employees row by escort mobile.

    wbom_employees.employee_id is a numeric primary key; the escort mobile is
    stored as employee_mobile and used as the stable identity anchor.
    """
    if not escort_mobile:
        return None
    try:
        from modules.number_identity import normalize_phone as get_phone_variants
        variants = get_phone_variants(escort_mobile)
        if not variants:
            return None
        row = await fetch_one(
            "SELECT employee_id FROM wbom_employees WHERE employee_mobile = ANY($1) AND status = 'Active' LIMIT 1",
            variants,
        )
        if row:
            return row["employee_id"]
        if not auto_create:
            return None

        mobile_to_store = variants[1] if len(variants) > 1 else variants[0]
        emp_id = await fetch_val(
            """INSERT INTO wbom_employees
                   (employee_name, employee_mobile, designation, status, joining_date)
               VALUES ($1, $2, 'Escort', 'Active', CURRENT_DATE)
               ON CONFLICT (employee_mobile) DO UPDATE
                   SET employee_name = COALESCE(NULLIF(EXCLUDED.employee_name, ''), wbom_employees.employee_name),
                       designation = 'Escort',
                       status = 'Active'
               RETURNING employee_id""",
            escort_name or "Unknown Escort",
            mobile_to_store,
        )
        log.info(f"[escort] auto-created/resolved escort employee_id={emp_id} mobile={mobile_to_store}")
        return emp_id
    except Exception as e:
        log.warning(f"[escort] employee_id lookup error: {e}")
    return None


async def _sync_roster_after_confirm(program_id: int) -> None:
    """Auto-sync program to escort_roster_entries after confirmation."""
    try:
        from modules.escort_roster.db import sync_program_to_roster
        await sync_program_to_roster(program_id, actor="auto_confirm")
        log.info(f"[escort] roster synced after confirmation: program_id={program_id}")
    except Exception as _sync_err:
        log.warning(f"[escort] roster sync failed after confirmation: {_sync_err}")


async def _sync_roster_draft(program_id: int) -> None:
    """Create/update the draft roster row for a draft escort program."""
    try:
        from modules.escort_roster.db import sync_program_to_roster
        await sync_program_to_roster(program_id, actor="auto_draft")
        log.info(f"[escort] roster draft synced: program_id={program_id}")
    except Exception as _sync_err:
        log.warning(f"[escort] roster draft sync failed: {_sync_err}")


async def _escort_program_columns() -> set[str]:
    global _ESCORT_PROGRAM_COLUMNS_CACHE
    if _ESCORT_PROGRAM_COLUMNS_CACHE is not None:
        return _ESCORT_PROGRAM_COLUMNS_CACHE
    try:
        rows = await fetch_all(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'wbom_escort_programs'
            """
        )
        _ESCORT_PROGRAM_COLUMNS_CACHE = {str(row["column_name"]) for row in rows}
    except Exception as e:
        log.warning(f"[escort] column introspection failed: {e}")
        _ESCORT_PROGRAM_COLUMNS_CACHE = set()
    return _ESCORT_PROGRAM_COLUMNS_CACHE


async def _merge_program_remarks(program_id: int, updates: dict[str, object]) -> None:
    if not updates:
        return
    try:
        row = await fetch_one(
            "SELECT remarks FROM wbom_escort_programs WHERE program_id = $1",
            program_id,
        )
        try:
            merged = json.loads(row.get("remarks") or "{}") if row else {}
        except (TypeError, json.JSONDecodeError):
            merged = {}
        for key, value in updates.items():
            if value not in (None, ""):
                merged[key] = value
        await execute(
            "UPDATE wbom_escort_programs SET remarks = $2 WHERE program_id = $1",
            program_id,
            json.dumps(merged, ensure_ascii=False),
        )
    except Exception as e:
        log.warning(f"[escort] remarks merge failed for program_id={program_id}: {e}")


async def _update_program_confirmed(
    program_id: int,
    escort_name: str,
    escort_mobile: str,
    lighter_vessel: Optional[str] = None,
    shift: Optional[str] = None,
    program_date=None,
    escort_employee_id: Optional[int] = None,
    mother_vessel: Optional[str] = None,
    destination: Optional[str] = None,
    master_mobile: Optional[str] = None,
    cargo_type: Optional[str] = None,
    importer: Optional[str] = None,
    capacity: Optional[str] = None,
) -> bool:
    """Update an existing draft/program to confirmed status.
    Bridge confirmation is authoritative and overwrites draft fields when present.
    """
    try:
        normalized_destination = _normalize_destination(destination or "") or destination
        columns = await _escort_program_columns()
        args: list[object] = [program_id]
        set_clauses = ["status = 'confirmed'"]

        def push(value: object) -> str:
            args.append(value)
            return f"${len(args)}"

        set_clauses.append(f"escort_name = {push(escort_name)}")
        set_clauses.append(f"escort_mobile = {push(escort_mobile)}")
        set_clauses.append(
            f"mother_vessel = COALESCE(NULLIF({push(mother_vessel)}::text, ''), mother_vessel)"
        )
        set_clauses.append(
            f"lighter_vessel = COALESCE(NULLIF({push(lighter_vessel)}::text, ''), lighter_vessel)"
        )
        set_clauses.append(f"shift = COALESCE(NULLIF({push(shift)}::text, ''), shift)")
        date_placeholder = push(str(program_date) if program_date else None)
        set_clauses.append(
            f"program_date = CASE WHEN {date_placeholder}::text IS NOT NULL THEN {date_placeholder}::text::date ELSE program_date END"
        )
        set_clauses.append(
            f"escort_employee_id = COALESCE({push(escort_employee_id)}, escort_employee_id)"
        )
        set_clauses.append(
            f"destination = COALESCE(NULLIF({push(normalized_destination)}::text, ''), destination)"
        )
        set_clauses.append(
            f"master_mobile = COALESCE(NULLIF({push(master_mobile)}::text, ''), master_mobile)"
        )
        if "capacity" in columns:
            set_clauses.append(
                f"capacity = COALESCE(NULLIF({push(capacity)}::text, ''), capacity)"
            )
        if "cargo_type" in columns:
            set_clauses.append(
                f"cargo_type = COALESCE(NULLIF({push(cargo_type)}::text, ''), cargo_type)"
            )
        if "importer" in columns:
            set_clauses.append(
                f"importer = COALESCE(NULLIF({push(importer)}::text, ''), importer)"
            )
        if "start_date" in columns:
            set_clauses.append(
                f"start_date = CASE WHEN {date_placeholder}::text IS NOT NULL THEN {date_placeholder}::text::date ELSE COALESCE(start_date, program_date) END"
            )

        await execute(
            f"UPDATE wbom_escort_programs SET {', '.join(set_clauses)} WHERE program_id = $1",
            *args,
        )
        await _merge_program_remarks(
            program_id,
            {
                "mother_vessel": mother_vessel,
                "lighter_vessel": lighter_vessel,
                "master_mobile": master_mobile,
                "destination": normalized_destination,
                "cargo_type": cargo_type,
                "importer": importer,
                "capacity": capacity,
                "escort_name": escort_name,
                "escort_mobile": escort_mobile,
                "program_date": str(program_date) if program_date else None,
                "shift": shift,
                "source_of_truth": "bridge_confirmation",
            },
        )
        return True
    except Exception as e:
        log.error(f"[escort] update confirmed error: {e}")
        return False


async def _create_confirmed_from_admin(
    draft: "CompletedDraft",
    source: str,
    client_phone: Optional[str] = None,
) -> Optional[int]:
    """Create a new confirmed escort program directly from admin's completed message.
    Called when no matching draft exists — admin reply is the primary source of truth.
    If an existing confirmed record matches (same MV + escort_name), updates it
    instead of creating a duplicate (safe for re-runs / backfills).
    """
    try:
        mv = draft["mother_vessel"] or ""
        lv = draft["lighter_vessel"] or ""
        escort_name = draft["escort_name"] or ""
        escort_mobile = draft["escort_mobile"] or ""
        shift = draft["shift"] or "D"
        program_date = _parse_program_date(draft.get("date_str"))
        destination = draft.get("destination") or ""
        normalized_destination = _normalize_destination(destination) or destination
        cargo_type = draft.get("cargo_type") or ""
        importer = draft.get("importer") or ""
        capacity = draft.get("capacity") or ""

        # Resolve escort_employee_id from mobile
        emp_id = await _resolve_escort_employee_id(escort_mobile, escort_name, auto_create=True)
        if emp_id:
            log.info(f"[escort] resolved escort_employee_id={emp_id} mobile={escort_mobile}")

        # Clean MV for comparison
        mv_clean = re.sub(r"^MV\.?\s+", "", mv, flags=re.IGNORECASE).strip().upper()

        # Dedup check: prefer the same vessel/lighter/master/date. This prevents
        # a completed draft from creating a second confirmed row on re-runs.
        existing = await fetch_one(
            """SELECT program_id FROM wbom_escort_programs
               WHERE UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $1
                 AND ($2::text = '' OR LOWER(lighter_vessel) = LOWER($2))
                 AND ($3::text = '' OR COALESCE(master_mobile, '') = $3)
                 AND ($4::date IS NULL OR program_date = $4)
                 AND status IN ('confirmed', 'Assigned', 'Running', 'Completed')
               ORDER BY program_id DESC LIMIT 1""",
            mv_clean, lv, draft.get("master_mobile") or "", program_date,
        )
        if existing:
            pid = existing["program_id"]
            log.info(
                f"[escort] confirmed record already exists (program_id={pid}) "
                f"— updating missing fields from admin msg"
            )
            await _update_program_confirmed(
                pid, escort_name, escort_mobile,
                lighter_vessel=lv, shift=shift, program_date=program_date,
                escort_employee_id=emp_id, mother_vessel=mv,
                destination=normalized_destination, master_mobile=draft.get("master_mobile"),
                cargo_type=cargo_type, importer=importer, capacity=capacity,
            )
            await _sync_roster_after_confirm(pid)
            return pid

        # Legacy fallback: same MV + escort name when vessel details were weak.
        if escort_name:
            existing = await fetch_one(
                """SELECT program_id FROM wbom_escort_programs
                   WHERE UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $1
                     AND LOWER(escort_name) = LOWER($2)
                     AND status = 'confirmed'
                   ORDER BY program_id DESC LIMIT 1""",
                mv_clean, escort_name,
            )
            if existing:
                pid = existing["program_id"]
                log.info(
                    f"[escort] confirmed record already exists (program_id={pid}) "
                    f"— updating missing fields from admin msg"
                )
            await _update_program_confirmed(
                pid, escort_name, escort_mobile,
                lighter_vessel=lv, shift=shift, program_date=program_date,
                escort_employee_id=emp_id, mother_vessel=mv,
                destination=normalized_destination, master_mobile=draft.get("master_mobile"),
                cargo_type=cargo_type, importer=importer, capacity=capacity,
            )
            await _sync_roster_after_confirm(pid)
            return pid

        remarks_data = {
            "auto_created": True,
            "source_bridge": source,
            "escort_name": escort_name,
            "escort_mobile": escort_mobile,
            "sender_phone": client_phone,
            "cargo_type": cargo_type or None,
            "importer": importer or None,
            "capacity": capacity or None,
            "destination": normalized_destination or None,
        }

        columns = await _escort_program_columns()
        insert_cols = [
            "mother_vessel",
            "lighter_vessel",
            "master_mobile",
            "destination",
            "escort_name",
            "escort_mobile",
            "escort_employee_id",
            "shift",
            "status",
            "program_date",
            "remarks",
        ]
        values: list[object] = [
            mv,
            lv,
            draft.get("master_mobile") or "",
            normalized_destination,
            escort_name,
            escort_mobile,
            emp_id,
            shift,
            "confirmed",
            program_date,
            json.dumps(remarks_data, ensure_ascii=False),
        ]
        if "capacity" in columns:
            insert_cols.insert(4, "capacity")
            values.insert(4, capacity)
        if "cargo_type" in columns:
            insert_cols.insert(4, "cargo_type")
            values.insert(4, cargo_type)
        if "importer" in columns:
            insert_cols.insert(4, "importer")
            values.insert(4, importer)

        placeholders = []
        for idx, col in enumerate(insert_cols, start=1):
            if col == "program_date":
                placeholders.append(f"COALESCE(${idx}, CURRENT_DATE)")
            else:
                placeholders.append(f"${idx}")

        row = await fetch_one(
            f"""INSERT INTO wbom_escort_programs (
                   {', '.join(insert_cols)}
               ) VALUES ({', '.join(placeholders)})
               RETURNING program_id""",
            *values,
        )
        if row:
            pid = row["program_id"]
            log.info(
                f"[escort] created confirmed from admin msg: "
                f"program_id={pid} mv={mv} lv={lv} escort={escort_name} emp_id={emp_id}"
            )
            await _sync_roster_after_confirm(pid)
            return pid
    except Exception as e:
        log.error(f"[escort] create confirmed from admin error: {e}")
    return None


# ── Public entry points ────────────────────────────────────────────────────────

async def handle_escort_client_message(
    text: str,
    sender_phone: str,
    source: str,
    is_historical: bool = False,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for messages from escort_client role.
    Extracts vessel data, saves to DB, sends draft to admin.
    Returns ("", admin_note) — no reply to client.

    is_historical=True: save DB record but suppress admin notification.
    Use this when importing historical messages.
    """
    from modules.message_router import get_primary_admin

    if not _is_trusted_escort_source(source):
        log.info("[escort] ignore client order from untrusted source=%s sender=%s", source, sender_phone)
        return "", None
    if not _is_allowed_escort_client(sender_phone):
        log.info("[escort] ignore client order from non-whitelisted sender=%s", sender_phone)
        return "", None

    order = parse_escort_message(text)
    log.info(
        f"[escort] client={sender_phone} mv={order['mother_vessel']} "
        f"lighters={len(order['lighters'])} historical={is_historical}"
    )

    if not is_minimum_escort_order(order):
        log.info("[escort] ignore non-order/no-minimum-fields sender=%s source=%s", sender_phone, source)
        return "", None

    await save_escort_programs(order, sender_phone, source)

    if is_historical:
        return "", None

    admin_msg = build_admin_message(order, sender_phone)
    admin_phone = get_primary_admin()

    if not admin_phone:
        log.warning("[escort] no admin phone configured")
        return "", None

    return "", {
        "admin_phone": admin_phone,
        "text": f"Notun escort order:\n\n{admin_msg}",
        "bridge": source,
    }


def _is_authoritative_bridge_confirmation(ctx: ConfirmationContext) -> bool:
    recipient = _normalize_mobile(ctx.get("recipient_phone") or "")
    bridge_number = _normalize_mobile(ctx.get("bridge_number") or "")
    if not recipient or not bridge_number:
        return False
    if recipient not in _escort_client_phone_list():
        return False
    mapped = _bridge_number_map().get(ctx.get("source") or "")
    return bool(mapped and bridge_number == mapped)


async def handle_admin_escort_completion(
    text: str,
    admin_phone: str,
    source: str,
    recipient_phone: Optional[str] = None,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for admin's completed escort draft.
    Finds original client, sends final slip to them, confirms to admin.
    """
    draft = parse_completed_draft(text)
    ctx: ConfirmationContext = {
        "bridge_number": admin_phone,
        "recipient_phone": recipient_phone or "",
        "source": source,
    }
    if not _is_authoritative_bridge_confirmation(ctx):
        log.info(
            "[escort] ignore non-authoritative confirmation source=%s bridge=%s recipient=%s",
            source, admin_phone, recipient_phone,
        )
        return "", None
    log.info(
        f"[escort] admin completion: mv={draft['mother_vessel']} "
        f"lv={draft['lighter_vessel']} escort={draft['escort_name']}"
    )

    program_date = _parse_program_date(draft.get("date_str"))
    confirmation_destination = _normalize_destination(draft.get("destination") or "") or draft.get("destination")
    program = await _find_pending_program(
        draft["mother_vessel"],
        draft["lighter_vessel"],
        master_mobile=draft.get("master_mobile"),
        program_date=program_date,
        client_phone=recipient_phone,
    )

    if not program:
        log.warning(
            f"[escort] no matching draft: mv={draft['mother_vessel']} "
            f"lv={draft['lighter_vessel']} escort={draft['escort_name']} "
            f"→ creating confirmed record from admin message"
        )
        # Admin reply is the source of truth — create a confirmed record
        # even when no draft exists (inbound parse may have failed).
        pid = await _create_confirmed_from_admin(draft, source, client_phone=recipient_phone)
        if pid:
            # Reconcile: delete any other draft rows for the same lighter vessel
            if draft.get("lighter_vessel"):
                try:
                    from modules.escort_roster.db import reconcile_drafts_for_confirmation
                    await reconcile_drafts_for_confirmation(
                        lighter_vessel=draft["lighter_vessel"],
                        mother_vessel=draft.get("mother_vessel"),
                        actor="auto_reconcile",
                    )
                except Exception as _rec_err:
                    log.warning(f"[escort] reconcile after create failed: {_rec_err}")
            return (f"Saved (new record #{pid}). No draft found — created directly.", None)
        return ("Save failed. Check logs.", None)

    log.info(
        f"[escort] matched program_id={program['program_id']} "
        f"mv={program['mother_vessel']} lv={program['lighter_vessel']} "
        f"→ escort={draft['escort_name']}"
    )
    # Parse extras from remarks JSON
    try:
        remarks = json.loads(program.get("remarks") or "{}")
    except (json.JSONDecodeError, TypeError):
        remarks = {}

    client_phone = remarks.get("sender_phone")
    client_bridge = remarks.get("source_bridge", source)

    # Resolve escort_employee_id from mobile
    emp_id = await _resolve_escort_employee_id(
        draft["escort_mobile"] or "",
        draft.get("escort_name"),
        auto_create=True,
    )
    if emp_id:
        log.info(f"[escort] resolved escort_employee_id={emp_id} mobile={draft['escort_mobile']}")

    # Update DB — fill escort fields, lighter_vessel, shift, program_date, escort_employee_id
    await _update_program_confirmed(
        program["program_id"],
        draft["escort_name"] or "",
        draft["escort_mobile"] or "",
        lighter_vessel=draft.get("lighter_vessel"),
        shift=draft.get("shift"),
        program_date=program_date,
        escort_employee_id=emp_id,
        mother_vessel=draft.get("mother_vessel"),
        destination=confirmation_destination,
        master_mobile=draft.get("master_mobile"),
        cargo_type=draft.get("cargo_type") or remarks.get("cargo_type"),
        importer=draft.get("importer") or remarks.get("importer"),
        capacity=draft.get("capacity") or remarks.get("capacity"),
    )

    # Auto-sync to roster after confirmation
    await _sync_roster_after_confirm(program["program_id"])

    # Reconcile: delete any other orphaned draft rows for the same lighter vessel
    if draft.get("lighter_vessel"):
        try:
            from modules.escort_roster.db import reconcile_drafts_for_confirmation
            await reconcile_drafts_for_confirmation(
                lighter_vessel=draft["lighter_vessel"],
                mother_vessel=draft.get("mother_vessel"),
                actor="auto_reconcile",
            )
        except Exception as _rec_err:
            log.warning(f"[escort] reconcile after update failed: {_rec_err}")

    # Destination: prefer admin's completed draft; fallback to DB record
    db_destination = program.get("destination") or None
    final_slip = build_final_slip(draft, db_destination=db_destination)

    if not client_phone:
        # No client phone stored — only confirm to admin
        return (
            f"DB updated. Client phone not found — send manually.\n\n{final_slip}",
            None,
        )

    # Send final slip to original client via admin_note routing
    admin_confirm = (
        f"Sent to {client_phone}\n\nSlip:\n{final_slip}"
    )

    return admin_confirm, {
        "admin_phone": client_phone,
        "text": final_slip,
        "bridge": client_bridge,
    }


# ── Legacy compatibility shim (called by old message_router path) ──────────────

async def handle_escort_order(
    text: str,
    sender_phone: str,
    source: str,
) -> tuple[str, bool]:
    """
    Kept for backward compatibility with any old call sites.
    Delegates to handle_escort_client_message — returns (reply, complete).
    """
    reply, _ = await handle_escort_client_message(text, sender_phone, source)
    return reply, False
