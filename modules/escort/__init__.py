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
      → updates DB status to 'Assigned'

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
from contextlib import asynccontextmanager
from datetime import date
from difflib import SequenceMatcher
from typing import Optional, TypedDict

from app.config import get_settings
from app.database import execute, fetch_one, fetch_val, fetch_all, db_conn
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
from modules.identity_brain import _phone_variants

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
    r"([A-Za-z][A-Za-z0-9\s.\-]{1,40}?)(?=\s*[,\n/]|\s+lighter\b|$)",
    re.IGNORECASE | re.MULTILINE,
)
_IMPORTER_RE = re.compile(
    r"(?:a/?c\.?\s*[:.\-]?\s*|account\s*:?\s*|importer\s*:?\s*)\*?([A-Za-z][\w\s&.\-/]{2,80}?)[*_~]*\s*(?=[\n]|$)",
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
    r"(?:lighter(?:\s*(?:vessel|name))?|lv)(?:\s*/\s*lighter\s*(?:vessel|name))?\s*:\s*([A-Za-z0-9][A-Za-z0-9_/\s.\-]{1,80}?)(?=\s*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_CD_MASTER_MOB_RE = re.compile(
    r"(?:master(?:'?s)?\s*(?:number|mobile|mob)?|mob)\s*:\s*(\b(?:880|0)1[3-9]\d{8}\b)",
    re.IGNORECASE,
)
_CD_DEST_RE = re.compile(
    # [ \t]* (not \s*) around the colon and in the lookahead so this can never
    # cross a newline into the following line -- \s* previously let a blank
    # "Destination:" line consume the newline and capture the next label's
    # whole line (e.g. "Escort Name: X") instead of empty. See
    # docs/AUDIT_REPORT_2026-07-17.md Section 1C.
    r"destination[ \t]*:[ \t]*(.+?)(?=[ \t]*\n|$)",
    re.IGNORECASE | re.MULTILINE,
)
_AL_AQSA_RE = re.compile(r"al.?aqsa|আল.?আকসা|al.?manaratayn|আল.?মানারাতাইন", re.IGNORECASE)
_TRAILING_MV_PAREN_RE = re.compile(r"\(\s*(mv\.?\s+[A-Za-z0-9][A-Za-z0-9\s.\-]{1,60})\s*\)$", re.IGNORECASE)

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
    """Normalize a raw mobile string to canonical 01XXXXXXXXX.

    Thin wrapper around modules.fazle_payroll_engine.normalizer.
    normalize_bd_phone() -- THE declared single source of truth for BD
    phone normalization repo-wide (Owner Directive 2026-07-01). Previously
    a separate, weaker reimplementation here that didn't handle Bengali
    digits, "88"-without-leading-8 country codes, or 10-digit inputs
    missing their leading zero -- fixed 2026-07-20 as part of the
    ESCORT_CLIENT_PHONES format-mismatch incident (see
    knowledge_base/00_governance/management_decisions.md). Every existing
    caller here already only ever fed it space/dash/+-only-formatted
    11-or-13-digit strings, so this is a strict superset of previously
    accepted input with identical output on the overlap -- not a
    behavior change for any currently-passing case."""
    return normalize_bd_phone(raw)


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
    """Canonical 01XXXXXXXXX form -- MUST match _normalize_mobile()'s
    output format, since _is_allowed_escort_client() and
    _is_authoritative_bridge_confirmation() compare this list directly
    against _normalize_mobile(sender_phone). Previously normalized to
    880XXXXXXXXXXX (the separate WBOM/RBAC-domain format) via to_880(),
    which meant those two comparisons could never match any real
    configured client -- fixed 2026-07-20. NOTE: this is a different,
    separate function from modules.escort_roster.routes's own
    _escort_client_phone_list() (used by the admin API and
    modules.client_billing.profiles, both of which correctly compare
    880-format against 880-format already) -- that one is untouched by
    this fix, on purpose, since it isn't broken."""
    raw = getattr(get_settings(), "escort_client_phones", "") or ""
    numbers: list[str] = []
    for item in raw.split(","):
        mobile = normalize_bd_phone(item.strip())
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
    # Field messages often end a lighter line with a bare capacity number
    # (e.g. "...local 800"). 700 MT is the KB's own documented example
    # (escort_business_rules.md Normalization Rules), so the floor must cover
    # it. Keep the range narrow enough that serials, dates, and phone
    # fragments do not turn into capacity.
    bare = re.search(r"(?<!\d)([7-9]\d{2}|1[01]\d{2}|1200)(?!\d)", scrubbed)
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


def _looks_like_lighter_candidate_line(lines: list[str], idx: int) -> bool:
    """
    A vessel-labeled line is likely a lighter when the nearby lines carry
    capacity and/or a master mobile number.
    """
    line = lines[idx] if 0 <= idx < len(lines) else ""
    if not _MV_LABEL_ANY_RE.search(line):
        return False
    # Mother-like metadata on the same line should not be treated as lighter.
    if _IMPORTER_RE.search(line) or _CARGO_RE.search(line) or re.search(r"(?i)\b(?:a/?c|account|importer|cargo|product)\b", line):
        return False
    window = "\n".join(lines[idx: min(len(lines), idx + 4)])
    has_mobile_nearby = bool(_MOBILE_FMT_RE.search(window))
    has_capacity_nearby = bool(_CAPACITY_RE.search(window) or _CAPACITY_LABEL_RE.search(window))
    # Capacity + mobile in the same block is a strong lighter signature.
    if has_mobile_nearby and has_capacity_nearby:
        return True
    # Mobile-only proximity is still likely a lighter in compact order formats.
    if has_mobile_nearby and not re.search(r"(?i)^\s*\(?\s*mother\s*vessel\s*[:\-]", line):
        return True
    return False


def _extract_mother_vessel_canonical(lines: list[str], full_text: str) -> Optional[str]:
    # Strong signal: a trailing parenthesized MV line is mother vessel.
    for raw in reversed(lines):
        m = _TRAILING_MV_PAREN_RE.search(raw.strip())
        if m:
            name = _clean_vessel_name(m.group(1), force_mv=True)
            if name:
                return name

    candidates: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        if _line_has_mobile(line):
            continue
        if _looks_like_lighter_candidate_line(lines, idx):
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


# ── Text-level order-completeness gate (2026-07-22 extraction) ─────────────────
# Moved here, behavior-preserving, from modules.message_router's private
# _is_strict_escort_order_text()/_looks_like_slip_or_complaint()/
# _has_client_supplied_escort_assignment() so the Route B handoff module
# (modules.route_b_handoff) can reuse the exact same canonical completeness
# check without a reverse import into message_router (which already imports
# from this module) or a second, drift-prone copy of the regexes.
# message_router now delegates to these instead of keeping its own copies.
_ESCORT_ASSIGNMENT_FIELDS_RE = re.compile(
    r"escort\s*(?:name)?\s*:\s*\S.+?escort\s*(?:mobile|mob|number)?\s*:\s*(?:\+?880|0)1[3-9][\d\s\-]{8,}",
    re.IGNORECASE | re.DOTALL,
)
_SLIP_OR_RELEASE_HINT_RE = re.compile(
    r"(?:\b(?:release|released|completion|completed|duty\s+slip|release\s+slip|"
    r"food|conveyance|transport|car\s*fare)\b|ভাড়া|ভাড়া|খাবার|রিলিজ|স্লিপ|পালিয়েছে|পালিয়েছে)",
    re.IGNORECASE,
)


def has_client_supplied_escort_assignment(text: str) -> bool:
    return bool(_ESCORT_ASSIGNMENT_FIELDS_RE.search(text or ""))


def looks_like_slip_or_complaint(text: str) -> bool:
    return bool(_SLIP_OR_RELEASE_HINT_RE.search(text or "")) or has_client_supplied_escort_assignment(text)


def is_order_complete(text: str) -> bool:
    """Code-based order gate on raw text: mother vessel + lighter + master
    mobile, no assignment/slip/release fields. Text-level wrapper around
    is_minimum_escort_order() — the single canonical completeness check,
    reused by both message_router's Tier 9 safety net and
    modules.route_b_handoff's multi-message escort handoff (Stage 3)."""
    if not text or looks_like_slip_or_complaint(text):
        return False
    try:
        order = parse_escort_message(text)
    except Exception as exc:
        log.debug("[escort] order-completeness parse failed: %s", exc)
        return False
    return is_minimum_escort_order(order)


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
    """Build the strict standardized draft to send to admin for one lighter."""
    d = date_str or _TODAY()
    shift_label = shift or lighter.get("shift") or "D/N"
    lines = [
        "Notun escort order:",
        f"Client: {client_phone or ''}",
        "",
        f"Mother Vessel: {mv or ''}",
        f"Importer: {importer or ''}",
        f"Lighter Vessel: {lighter['lighter_vessel']}",
        f"Master Mobile: {lighter['master_mobile'] or ''}",
        f"Destination: {lighter.get('destination') or ''}",
        "Escort Name:",
        "Escort Mobile:",
        f"{d} ({shift_label})",
        "",
        "Al-Manaratayn Skill Solutions",
    ]
    return "\n".join(lines)


def build_admin_message(order: EscortOrder, sender_phone: str) -> str:
    """Build strict admin draft message(s) for one escort order."""
    mv = order["mother_vessel"] or "Unknown MV"
    parts = []

    for i, lighter in enumerate(order["lighters"], 1):
        _ = i  # keeps the loop intent explicit without altering output format
        draft_text = build_admin_draft(
            mv,
            lighter,
            order.get("date_hint"),
            client_phone=sender_phone,
            importer=order.get("importer"),
            cargo_type=order.get("cargo_type"),
            shift=order.get("shift"),
        )
        parts.append(draft_text)
        if i < len(order["lighters"]):
            parts.append("")

    if not order["lighters"]:
        # Should not happen in strict gate flow; keep defensive fallback.
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
        parts.append(draft_text)

    return "\n".join(parts).rstrip()


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
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

    def _extract_labeled_line_value(pattern: str) -> Optional[str]:
        rx = re.compile(pattern, re.IGNORECASE)
        for ln in lines:
            m = rx.match(ln)
            if m:
                return (m.group(1) or "").strip()
        return None

    mother_label = _extract_labeled_line_value(r"^\s*mother\s*vessel\s*:\s*(.+)$")
    lighter_label = _extract_labeled_line_value(
        r"^\s*(?:lighter\s*(?:vessel|name)(?:\s*/\s*lighter\s*(?:vessel|name))?|lv)\s*:\s*(.+)$"
    )
    master_label = _extract_labeled_line_value(r"^\s*master\s*(?:mobile|number|mob|no)?\s*:\s*(.+)$")
    destination_label = _extract_labeled_line_value(r"^\s*destination\s*:\s*(.+)$")

    mv_m = _CD_MV_RE.search(text)
    lv_m = _CD_LV_RE.search(text)
    master_mob_m = _CD_MASTER_MOB_RE.search(text)
    escort_name_m = _CD_ESCORT_NAME_RE.search(text)
    escort_mob_m = _CD_ESCORT_MOB_RE.search(text)
    date_m = _DATE_RE.search(text)
    shift_m = _CD_SHIFT_RE.search(text)
    dest_m = _CD_DEST_RE.search(text)

    mv_raw = mother_label or (mv_m.group(1).strip() if mv_m else None)
    mv = mv_raw
    if mv:
        # Strip double MV/MV. prefix (e.g. 'MV. EVA PARIS' → 'MV EVA PARIS')
        mv = re.sub(r"^MV\.?\s+", "", mv, flags=re.IGNORECASE).strip()
        if not mv.upper().startswith("MV"):
            mv = f"MV {mv.upper()}"
        else:
            mv = mv.upper()

    lv_raw = lighter_label or (lv_m.group(1).strip() if lv_m else None)
    # Guardrail: never accept a Mother Vessel label as lighter value.
    if lv_raw and re.match(r"(?i)^mother\s*vessel\s*:", lv_raw.strip()):
        lv_raw = None

    master_mobile = _extract_mobile(master_label or "") or (master_mob_m.group(1) if master_mob_m else None)

    # Parse shift from '(D)'/'(N)' OR bare 'Day'/'Night' text
    shift = None
    if shift_m:
        if shift_m.group(1):   # matched (D) or (N)
            shift = shift_m.group(1).upper()
        elif shift_m.group(2):  # matched 'Day' or 'Night'
            shift = "D" if shift_m.group(2).lower() == "day" else "N"

    raw_destination = destination_label or (dest_m.group(1).strip() if dest_m else None)
    destination = _normalize_destination(raw_destination or "") or raw_destination
    capacity = _normalize_capacity(text)
    importer = _extract_importer(text)
    cargo_type = _normalize_cargo(text)

    # Fallback: if lighter label is missing, infer from mobile-grouped parser.
    if not lv_raw:
        try:
            inferred = parse_escort_message(text)
            if inferred.get("lighters"):
                lv_raw = inferred["lighters"][0].get("lighter_vessel")
            if not mv and inferred.get("mother_vessel"):
                mv = inferred.get("mother_vessel")
        except Exception:
            pass

    return CompletedDraft(
        mother_vessel=mv,
        lighter_vessel=lv_raw,
        master_mobile=master_mobile,
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
      Escort Name / Escort Mobile / Start Date (D/N) / Al-Manaratayn Skill Solutions
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
        "Al-Manaratayn Skill Solutions",
    ]
    return "\n".join(lines)


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _get_contact_id(phone: str) -> Optional[int]:
    """Resolve a raw phone string to wbom_contacts.contact_id via normalized
    variants (0XXXXXXXXXX / 880XXXXXXXXXX / +880XXXXXXXXXX), matching the
    same lookup identity_brain._lookup_contact() uses — a raw un-normalized
    exact-string match silently misses any format drift between however the
    phone arrived here and however whatsapp_number was originally stored.
    """
    variants = _phone_variants(phone or "")
    if not variants:
        return None
    row = await fetch_one(
        "SELECT contact_id FROM wbom_contacts WHERE whatsapp_number = ANY($1) ORDER BY contact_id DESC LIMIT 1",
        variants,
    )
    return row["contact_id"] if row else None


async def _backfill_contact_id_if_missing(program_id: int, phone: Optional[str]) -> None:
    """Best-effort: resolve and set contact_id on an existing program row
    only if it doesn't already have one — never overwrites a resolved value.
    """
    if not phone:
        return
    contact_id = await _get_contact_id(phone)
    if not contact_id:
        return
    try:
        await execute(
            "UPDATE wbom_escort_programs SET contact_id = $2 WHERE program_id = $1 AND contact_id IS NULL",
            program_id,
            contact_id,
        )
    except Exception as e:
        log.warning(f"[escort] contact_id backfill failed for program_id={program_id}: {e}")


async def _find_existing_draft_for_order(
    mv_clean: str, master_mobile: str, program_date,
) -> Optional[int]:
    """2026-07-22: broader, channel-agnostic dedup check for the same
    real-world order arriving via more than one source (e.g. a DM from the
    client AND the admin's own group order for the same duty). Deliberately
    does NOT restrict on contact_id/sender_phone the way
    save_escort_programs()'s own inline dedup does -- that restriction is
    correct and left unchanged for the existing DM-vs-DM case (Rule 2: do
    not modify the existing flow), but it can under-dedup across genuinely
    different senders reporting the identical order (master_mobile +
    mother_vessel + date all matching is already strong enough evidence on
    its own). Used as an EXTRA check by the new group-order path only --
    save_escort_programs()'s own internal query is completely unchanged."""
    # 2026-07-22 (Final Instruction Section 3 review -- "confirm it cannot
    # merge two different real orders"): unlike save_escort_programs()'s
    # own inline dedup (bounded by contact_id/sender_phone even when date
    # is unknown), this function drops that restriction on purpose, which
    # means master_mobile+mother_vessel alone is the ONLY remaining signal
    # once date is unresolved -- and a master who regularly tows the same
    # mother vessel on different dates/duties is a plausible real scenario,
    # not a hypothetical. A NULL-date wildcard here could silently merge
    # two genuinely different orders. So program_date is now REQUIRED --
    # with no date, this returns None (no cross-channel match) and the
    # caller falls through to creating a new draft. A false negative
    # (occasional duplicate draft, resolvable by admin review) is the safe
    # failure mode; a false positive (two orders silently merged) is not.
    if not (mv_clean and master_mobile and program_date):
        return None
    try:
        row = await fetch_one(
            """
            SELECT program_id FROM wbom_escort_programs
            WHERE COALESCE(master_mobile, '') = $1
              AND UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $2
              AND status NOT IN ('cancelled')
              AND program_date = $3
            ORDER BY program_id DESC LIMIT 1
            """,
            master_mobile, mv_clean, program_date,
        )
        return row["program_id"] if row else None
    except Exception as e:
        log.error(f"[escort] cross-channel dedup check error: {e}")
        return None


async def save_escort_programs(
    order: EscortOrder,
    sender_phone: str,
    source: str,
    is_verified: bool = True,
    *,
    group_sourced: bool = False,
    group_jid: Optional[str] = None,
) -> list:
    """Save one DB row per valid lighter block only.

    is_verified (Phase 1B.2): recorded in remarks.source_verified — audit
    evidence only, does not change save behavior. False means the sender's
    client_escort_buyer routing came from message-content keyword inference
    (identity_brain Step 6), not a genuine identity source.

    group_sourced/group_jid (2026-07-22, Trusted Group): recorded in
    remarks so expire_stale_drafts() can exempt these rows from TTL
    deletion (Owner Decision B) and so the source is auditable. Does not
    change the save/dedup mechanics below at all -- purely additive tags.
    """
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
            "source_verified": is_verified,
            "group_sourced": group_sourced,
            "group_jid": group_jid if group_sourced else None,
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
                # 2026-07-22: for a group-sourced save specifically, also
                # check the broader, sender-agnostic dedup (see
                # _find_existing_draft_for_order docstring) — catches the
                # case where the SAME order already exists via a different
                # channel (e.g. a client DM) that the narrower check above
                # would miss because contact_id/sender_phone differ.
                if not dup and group_sourced:
                    cross_pid = await _find_existing_draft_for_order(
                        mv_clean, lighter.get("master_mobile") or "",
                        _parse_program_date(order.get("date_hint")),
                    )
                    if cross_pid:
                        dup = {"program_id": cross_pid}
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


_PENDING_PROGRAM_MATCH_THRESHOLD = 5.5


async def _score_pending_program_candidates(
    mv: Optional[str],
    lv: Optional[str],
    *,
    master_mobile: Optional[str] = None,
    program_date=None,
    client_phone: Optional[str] = None,
) -> list[dict]:
    """Score every draft candidate against a confirmation's parsed fields.

    Returns every candidate (not just the winner), sorted by score
    descending, each as {"program_id", "score", "matched_fields",
    "warnings"}. Shared by _find_pending_program() (takes the top one if
    it clears _PENDING_PROGRAM_MATCH_THRESHOLD) and the no-match review-
    queue path in handle_admin_escort_completion(), which needs the full
    ranked list as admin-reviewable evidence, not just a single winner.

    Scoring formula unchanged from the pre-refactor single-winner version
    (master_mobile exact +5, program_date exact +3, mother_vessel
    similarity ratio *3, lighter_vessel similarity ratio *1) -- this
    function only adds visibility into the score, it does not change it.
    """
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
            return []

        scored: list[dict] = []
        for row in candidates:
            if client_phone and not _remarks_client_matches(row["remarks"], client_phone):
                continue
            score = 0.0
            matched_fields: list[str] = []
            warnings: list[str] = []
            if master_norm and (row["master_mobile"] or "") == master_norm:
                score += 5.0
                matched_fields.append("master_mobile")
            if program_date and row["program_date"] == program_date:
                score += 3.0
                matched_fields.append("program_date")
            elif program_date and not row["program_date"]:
                warnings.append("candidate_program_date_missing")
            row_mv = (row["mother_vessel"] or "").upper().replace("MV.", "").replace("MV ", "").strip()
            if mv_clean:
                mv_ratio = SequenceMatcher(None, mv_clean, row_mv).ratio()
                score += mv_ratio * 3.0
                if mv_ratio >= 0.8:
                    matched_fields.append("mother_vessel")
            if lv_clean and row.get("lighter_vessel"):
                lv_ratio = SequenceMatcher(None, lv_clean.lower(), (row["lighter_vessel"] or "").lower()).ratio()
                score += lv_ratio
                if lv_ratio >= 0.8:
                    matched_fields.append("lighter_vessel")
            elif lv_clean and not row.get("lighter_vessel"):
                warnings.append("candidate_lighter_vessel_missing")
            if not program_date:
                warnings.append("confirmation_start_date_missing")
            scored.append({
                "program_id": row["program_id"],
                "score": round(score, 2),
                "matched_fields": matched_fields,
                "warnings": warnings,
            })
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored
    except Exception as e:
        log.error(f"[escort] score candidates error: {e}")
        return []


async def _find_pending_program(
    mv: Optional[str],
    lv: Optional[str],
    *,
    master_mobile: Optional[str] = None,
    program_date=None,
    client_phone: Optional[str] = None,
) -> Optional[dict]:
    """Find most recent draft with master mobile as primary match signal.

    Thin wrapper over _score_pending_program_candidates(): same threshold,
    same winner-selection behavior as before this function was split --
    only the scoring internals moved, not the matching semantics.
    """
    scored = await _score_pending_program_candidates(
        mv, lv, master_mobile=master_mobile, program_date=program_date, client_phone=client_phone,
    )
    if not scored or scored[0]["score"] < _PENDING_PROGRAM_MATCH_THRESHOLD:
        return None
    try:
        row = await fetch_one(
            """SELECT program_id, remarks, mother_vessel, lighter_vessel,
                      destination, master_mobile, program_date
               FROM wbom_escort_programs WHERE program_id = $1""",
            scored[0]["program_id"],
        )
        return dict(row) if row else None
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

    After resolving/creating the WBOM record, this function also ensures an
    fpe_employees row exists and is linked via wbom_employee_id. This makes
    the escort visible in /payroll/employees immediately.
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
            wbom_id = row["employee_id"]
            # Ensure FPE link exists (non-fatal)
            await _ensure_fpe_employee_link(wbom_id, escort_mobile, escort_name)
            return wbom_id
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
        # Ensure FPE link exists (non-fatal)
        await _ensure_fpe_employee_link(emp_id, mobile_to_store, escort_name)
        return emp_id
    except Exception as e:
        log.warning(f"[escort] employee_id lookup error: {e}")
    return None


async def _ensure_fpe_employee_link(
    wbom_employee_id: int,
    mobile: str,
    name: Optional[str] = None,
) -> None:
    """
    Ensure an fpe_employees row exists for this escort and is linked to WBOM.
    Non-fatal: any error is logged and swallowed.
    Called after resolving/creating a wbom_employees row for an escort.
    Does NOT touch fpe_cash_transactions — only fpe_employees identity table.
    """
    try:
        from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
        from app.database import execute as _exec, fetch_one as _fo, fetch_val as _fv

        canon = normalize_bd_phone(mobile)
        if not canon:
            return

        # 1. FPE record already linked to this WBOM id?
        already = await _fo(
            "SELECT id FROM fpe_employees WHERE wbom_employee_id = $1 LIMIT 1",
            wbom_employee_id,
        )
        if already:
            return  # already properly linked

        # 2. FPE record exists by phone but wbom_employee_id not set → just link it
        fpe_by_phone = await _fo(
            "SELECT id FROM fpe_employees WHERE primary_phone = $1 OR employee_id_phone = $1 LIMIT 1",
            canon,
        )
        if fpe_by_phone:
            await _exec(
                "UPDATE fpe_employees SET wbom_employee_id = $1 WHERE id = $2 AND wbom_employee_id IS NULL",
                wbom_employee_id, fpe_by_phone["id"],
            )
            log.debug("[escort] linked fpe_employee id=%d → wbom_employee_id=%d", fpe_by_phone["id"], wbom_employee_id)
            return

        # 3. No FPE record at all → create one
        clean_name = (name or "Unknown Escort").strip()
        name_norm = clean_name.lower()
        new_fpe_id = await _fv(
            """INSERT INTO fpe_employees
                   (full_name, name_normalized, primary_phone, employee_id_phone,
                    department, status, created_source, resolution_status, confidence_score,
                    wbom_employee_id, designation)
               VALUES ($1, $2, $3, $3, 'Escort', 'active', 'escort_program_confirm',
                       'resolved', 1.0, $4, 'Escort')
               ON CONFLICT DO NOTHING
               RETURNING id""",
            clean_name, name_norm, canon, wbom_employee_id,
        )
        if new_fpe_id:
            await _exec(
                "UPDATE fpe_employees SET employee_code = 'EMP-' || LPAD(id::text, 5, '0') WHERE id = $1 AND employee_code IS NULL",
                new_fpe_id,
            )
            log.info("[escort] created fpe_employee id=%d for escort mobile=%s wbom_id=%d", new_fpe_id, canon, wbom_employee_id)
    except Exception as _err:
        log.debug("[escort] _ensure_fpe_employee_link failed: %s", _err)


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

    2026-07-22 (Owner Decision, Requirement 5): before writing, compare the
    incoming escort_name/escort_mobile/mother_vessel/lighter_vessel against
    the row's current values. If every field would resolve to an identical
    value, skip the UPDATE entirely and log it as a no-op duplicate --
    covers both the same confirmation replayed and the same confirmation
    arriving via a second channel (e.g. DM then group, or vice versa) after
    the first one already applied. Uses the exact same resolution semantics
    the SET clause below already uses (escort_name/escort_mobile are always
    overwritten even if empty; mother_vessel/lighter_vessel only overwrite
    on a non-empty incoming value) so this check can never disagree with
    what the real UPDATE below would actually do.
    """
    try:
        current = await fetch_one(
            """SELECT escort_name, escort_mobile, mother_vessel, lighter_vessel
               FROM wbom_escort_programs WHERE program_id = $1""",
            program_id,
        )
        if current:
            effective_mv = mother_vessel if (mother_vessel or "").strip() else current["mother_vessel"]
            effective_lv = lighter_vessel if (lighter_vessel or "").strip() else current["lighter_vessel"]
            if (
                (escort_name or "") == (current["escort_name"] or "")
                and (escort_mobile or "") == (current["escort_mobile"] or "")
                and (effective_mv or "") == (current["mother_vessel"] or "")
                and (effective_lv or "") == (current["lighter_vessel"] or "")
            ):
                log.info(
                    f"[escort] confirmation no-op: program_id={program_id} — "
                    f"escort_name/escort_mobile/mother_vessel/lighter_vessel all "
                    f"identical to current row, skipping UPDATE"
                )
                return True
    except Exception as e:
        log.warning(f"[escort] no-op comparison failed for program_id={program_id}, proceeding with update: {e}")

    try:
        normalized_destination = _normalize_destination(destination or "") or destination
        columns = await _escort_program_columns()
        args: list[object] = [program_id]
        # Phase 2A.3 Decision 4: never downgrade an already-running/completed
        # program back to 'confirmed' -- both statuses can only be reached
        # via the attendance-gated lifecycle (mark_program_running_on_attendance/
        # close_program), so a later admin message re-confirming/correcting
        # metadata must not silently revert real operational progress.
        set_clauses = [
            "status = CASE WHEN status IN ('running', 'completed') THEN status ELSE 'confirmed' END"
        ]

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
        contact_id = await _get_contact_id(client_phone) if client_phone else None

        # Resolve escort_employee_id from mobile
        emp_id = await _resolve_escort_employee_id(escort_mobile, escort_name, auto_create=True)
        if emp_id:
            log.info(f"[escort] resolved escort_employee_id={emp_id} mobile={escort_mobile}")

        # Clean MV for comparison
        mv_clean = re.sub(r"^MV\.?\s+", "", mv, flags=re.IGNORECASE).strip().upper()

        # Strong dedupe key for assigned rows: date + mother + lighter + escort.
        # This blocks backfill/replay from creating exact duplicates.
        exact_existing = await fetch_one(
            """
            SELECT program_id
            FROM wbom_escort_programs
            WHERE status = 'confirmed'
              AND COALESCE(start_date, program_date) = COALESCE($1::date, COALESCE(start_date, program_date))
              AND UPPER(TRIM(COALESCE(mother_vessel, ''))) = UPPER(TRIM($2))
              AND UPPER(TRIM(COALESCE(lighter_vessel, ''))) = UPPER(TRIM($3))
              AND UPPER(TRIM(COALESCE(escort_name, ''))) = UPPER(TRIM($4))
            ORDER BY program_id ASC
            LIMIT 1
            """,
            program_date,
            mv,
            lv,
            escort_name,
        )
        if exact_existing:
            pid = exact_existing["program_id"]
            log.info(
                "[escort] exact confirmed duplicate prevented: existing=%s mv=%s lv=%s escort=%s",
                pid,
                mv,
                lv,
                escort_name,
            )
            await _update_program_confirmed(
                pid,
                escort_name,
                escort_mobile,
                lighter_vessel=lv,
                shift=shift,
                program_date=program_date,
                escort_employee_id=emp_id,
                mother_vessel=mv,
                destination=normalized_destination,
                master_mobile=draft.get("master_mobile"),
                cargo_type=cargo_type,
                importer=importer,
                capacity=capacity,
            )
            await _backfill_contact_id_if_missing(pid, client_phone)
            await _sync_roster_after_confirm(pid)
            return pid

        # Dedup check: prefer the same vessel/lighter/master/date. This prevents
        # a completed draft from creating a second confirmed row on re-runs.
        existing = await fetch_one(
            """SELECT program_id, status, escort_name, escort_mobile, escort_employee_id, lighter_vessel
               FROM wbom_escort_programs
               WHERE UPPER(REPLACE(REPLACE(mother_vessel, 'MV. ', ''), 'MV ', '')) = $1
                 AND ($2::text = '' OR LOWER(lighter_vessel) = LOWER($2))
                 AND ($3::text = '' OR COALESCE(master_mobile, '') = $3)
                 AND ($4::date IS NULL OR program_date = $4)
                 AND status IN ('confirmed', 'running', 'completed')
               ORDER BY program_id DESC LIMIT 1""",
            mv_clean, lv, draft.get("master_mobile") or "", program_date,
        )
        if existing:
            pid = existing["program_id"]

            # Phase 2A.3 Decision 3: this dedup query's own WHERE clause
            # treats an empty incoming lighter_vessel as "matches anything"
            # (deliberately, for the master_mobile-based dedup path where
            # lighter_vessel may legitimately never have been captured) --
            # that broader matching behavior is intentionally left alone
            # here (out of scope, real production rows rely on it). But
            # for the NEW same-date escort-change guard below specifically,
            # an empty lighter_vessel on either side is insufficient
            # evidence to run automatic escort-identity resolution against
            # -- route straight to manual review rather than silently
            # falling back to mother-vessel-only confidence.
            lighter_vessel_insufficient = not (lv or "").strip() or not (existing.get("lighter_vessel") or "").strip()

            # Phase 2A.3 Decision 4: same vessel+date match, but the row
            # already has running/completed (= attendance-gated) evidence.
            # If the incoming admin message names a DIFFERENT escort, this
            # is a possible real replacement, not a metadata correction --
            # do not let _update_program_confirmed() overwrite escort
            # identity or (previously) silently downgrade status. Route to
            # manual review instead. A same-date-different-escort case
            # never qualifies as a Stage 3 automatic replacement segment
            # either (that requires a LATER start boundary, not an equal
            # one) -- this is permanent manual-review routing, not a
            # temporary Stage 2 limitation.
            existing_escort_name = (existing.get("escort_name") or "").strip().lower()
            existing_escort_mobile = (existing.get("escort_mobile") or "").strip()
            incoming_escort_name = (escort_name or "").strip().lower()
            incoming_escort_mobile = (escort_mobile or "").strip()
            escort_differs = (
                (incoming_escort_name and incoming_escort_name != existing_escort_name)
                or (incoming_escort_mobile and existing_escort_mobile
                    and incoming_escort_mobile != existing_escort_mobile)
            )
            has_duty_evidence = existing["status"] in ("running", "completed")

            # Decision 3 applies even without duty evidence: an empty
            # lighter_vessel on either side is never confident enough to
            # auto-apply an escort-identity change, regardless of status.
            if escort_differs and (has_duty_evidence or lighter_vessel_insufficient):
                review_reason = (
                    "same_date_vessel_match_different_escort_with_duty_evidence" if has_duty_evidence
                    else "insufficient_data_empty_lighter_vessel"
                )
                log.warning(
                    f"[escort] same-date vessel match on program_id={pid} (status={existing['status']}, "
                    f"lighter_vessel_insufficient={lighter_vessel_insufficient}) but incoming escort "
                    f"({escort_name!r}/{escort_mobile!r}) differs from existing "
                    f"({existing['escort_name']!r}/{existing['escort_mobile']!r}) -- routing to manual "
                    f"review instead of overwriting, per Phase 2A.3 Decision 3/4 (reason={review_reason})"
                )
                try:
                    from modules.escort_roster.db import create_roster_change_request, log_audit
                    await create_roster_change_request(
                        program_id=pid,
                        action="manual_review_same_date_escort_change",
                        payload={
                            "reason": review_reason,
                            "existing_status": existing["status"],
                            "existing_escort_name": existing["escort_name"],
                            "existing_escort_mobile": existing["escort_mobile"],
                            "incoming_escort_name": escort_name,
                            "incoming_escort_mobile": escort_mobile,
                            "incoming_source": source,
                            "mother_vessel": mv,
                            "lighter_vessel": lv,
                            "program_date": str(program_date) if program_date else None,
                        },
                        requested_by=f"system:{source}",
                        requested_by_phone=client_phone,
                    )
                except Exception as review_exc:
                    log.error(f"[escort] failed to create manual-review request for program_id={pid}: {review_exc}")
                return pid

            # Safe correction path: no duty evidence blocks it, or the
            # escort is unchanged -- audit the before/after and proceed
            # exactly as before.
            try:
                from modules.escort_roster.db import log_audit
                await log_audit(
                    program_id=pid,
                    action="vessel_match_correction",
                    old_data={
                        "status": existing["status"], "escort_name": existing["escort_name"],
                        "escort_mobile": existing["escort_mobile"],
                        "escort_employee_id": existing["escort_employee_id"],
                    },
                    new_data={
                        "escort_name": escort_name, "escort_mobile": escort_mobile,
                        "escort_employee_id": emp_id, "mother_vessel": mv, "lighter_vessel": lv,
                    },
                    performed_by=f"system:{source}",
                    source="admin_message",
                )
            except Exception as audit_exc:
                log.error(f"[escort] failed to write vessel_match_correction audit for program_id={pid}: {audit_exc}")

            log.info(
                f"[escort] assigned record already exists (program_id={pid}) "
                f"— updating missing fields from admin msg"
            )
            await _update_program_confirmed(
                pid, escort_name, escort_mobile,
                lighter_vessel=lv, shift=shift, program_date=program_date,
                escort_employee_id=emp_id, mother_vessel=mv,
                destination=normalized_destination, master_mobile=draft.get("master_mobile"),
                cargo_type=cargo_type, importer=importer, capacity=capacity,
            )
            await _backfill_contact_id_if_missing(pid, client_phone)
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
                    f"[escort] assigned record already exists (program_id={pid}) "
                    f"— updating missing fields from admin msg"
                )
                await _update_program_confirmed(
                    pid, escort_name, escort_mobile,
                    lighter_vessel=lv, shift=shift, program_date=program_date,
                    escort_employee_id=emp_id, mother_vessel=mv,
                    destination=normalized_destination, master_mobile=draft.get("master_mobile"),
                    cargo_type=cargo_type, importer=importer, capacity=capacity,
                )
                await _backfill_contact_id_if_missing(pid, client_phone)
                await _sync_roster_after_confirm(pid)
                return pid

        # Phase 2A.3 Stage 3: before falling through to a plain new-program
        # insert, check whether this is actually a REPLACEMENT segment for
        # an existing, different-escort, earlier-dated program on the same
        # vessel pair. Only fires when the vessel-pair match is confident
        # (Decision 3: both mother_vessel and lighter_vessel non-empty and
        # matching) and the new start is strictly later than the
        # predecessor's -- find_replacement_predecessor() already enforces
        # both. A same-date match belongs to Stage 2's guard above, never
        # here (this dedup branch structurally never reaches this point
        # for a same-date match -- it would have already matched the
        # `existing` branch's date equality condition and returned there).
        if program_date and mv_clean and lv:
            try:
                from modules.dispatch import find_replacement_predecessor, create_replacement_segment, DispatchAssignmentError
                predecessor = await find_replacement_predecessor(
                    mother_vessel=mv, lighter_vessel=lv, escort_name=escort_name,
                    escort_mobile=escort_mobile, start_date=program_date,
                )
                if predecessor is not None:
                    try:
                        result = await create_replacement_segment(
                            earlier_program_id=predecessor["program_id"],
                            mother_vessel=mv, lighter_vessel=lv,
                            escort_employee_id=emp_id, escort_name=escort_name, escort_mobile=escort_mobile,
                            start_date=program_date, start_shift=shift,
                            master_mobile=draft.get("master_mobile"), destination=normalized_destination,
                            performed_by=f"system:{source}",
                        )
                        log.info(
                            f"[escort] created replacement segment: earlier program_id={predecessor['program_id']} "
                            f"({result['earlier_status']}) -> new program_id={result['new_program_id']} "
                            f"(rule={result['classification_rule']}, delta={result['date_delta_days']}d)"
                        )
                        await _backfill_contact_id_if_missing(result["new_program_id"], client_phone)
                        return result["new_program_id"]
                    except DispatchAssignmentError as repl_exc:
                        # Re-verification inside create_replacement_segment's
                        # own transaction disagreed with the read-only
                        # predecessor lookup above (race, or a condition
                        # find_replacement_predecessor() doesn't itself
                        # check) -- fall through to the plain insert rather
                        # than guessing; never silently drop the message.
                        log.warning(
                            f"[escort] replacement segment creation declined for predecessor="
                            f"{predecessor['program_id']}: {repl_exc.code} -- {repl_exc.message} "
                            f"-- falling back to plain new-program insert"
                        )
            except Exception as repl_lookup_exc:
                log.error(f"[escort] replacement-predecessor lookup failed, falling back to plain insert: {repl_lookup_exc}")

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
            "contact_id",
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
            contact_id,
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
    is_verified: bool = True,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for messages from escort_client-eligible senders.
    Extracts vessel data, saves to DB, sends draft to admin.
    Returns ("", admin_note) — no reply to client.

    is_historical=True: save DB record but suppress admin notification.
    Use this when importing historical messages.

    is_verified (Phase 1B.2): True when the sender's client_escort_buyer
    identity came from a genuine source (ESCORT_CLIENT_PHONES, a stored
    fazle_contact_roles row, or a contact-book name marker — see
    modules.identity_brain.resolve_escort_client_identity). False when the
    caller only matched via message-content keyword inference (identity_brain
    Step 6) — a real, previously-unseen sender whose message merely looked
    escort-shaped. The unverified path still runs the same safe extraction
    and admin-review routing, but the draft is clearly labeled and the saved
    row's evidence records the sender as unverified; it never grants any
    escort-client identity or authority on its own.
    """
    from modules.message_router import get_primary_admin

    if not _is_trusted_escort_source(source):
        log.info("[escort] ignore client order from untrusted source=%s sender=%s", source, sender_phone)
        return "", None

    if not is_verified:
        # Unverified path (Phase 1B.2): the phone-allowlist gate's existing
        # closed/open-mode behavior is preserved exactly as before — this is
        # unchanged from pre-1B.2 code. A genuinely closed allowlist can
        # still suppress an unrecognized stranger's order entirely.
        if not _is_allowed_escort_client(sender_phone):
            log.info("[escort] ignore unverified client order from non-whitelisted sender=%s", sender_phone)
            return "", None
    # Verified callers (Phase 1B.2): trust already came from identity
    # resolution (env_phone, stored role, or contact-name marker) — a
    # verified sender whose phone happens not to be in ESCORT_CLIENT_PHONES
    # (e.g. verified only via a stored contact-role) must not be rejected by
    # a phone-list check that isn't how they were verified in the first
    # place. Fixes a real identity/gate inconsistency found during the
    # Phase 1B.2 convergence audit.

    order = parse_escort_message(text)
    log.info(
        f"[escort] client={sender_phone} mv={order['mother_vessel']} "
        f"lighters={len(order['lighters'])} historical={is_historical} verified={is_verified}"
    )

    if not is_minimum_escort_order(order):
        log.info("[escort] ignore non-order/no-minimum-fields sender=%s source=%s", sender_phone, source)
        return "", None

    await save_escort_programs(order, sender_phone, source, is_verified=is_verified)

    if is_historical:
        return "", None

    admin_msg = build_admin_message(order, sender_phone)
    if not is_verified:
        admin_msg = (
            "⚠️ UNVERIFIED SENDER — order-like message from a number not "
            "recognized as a known escort client (no .env/stored-role/"
            "contact-name match). Please verify manually before treating "
            "as a confirmed order.\n\n" + admin_msg
        )
    admin_phone = get_primary_admin()

    if not admin_phone:
        log.warning("[escort] no admin phone configured")
        return "", None

    return "", {
        "admin_phone": admin_phone,
        "text": admin_msg,
        "bridge": source,
    }


def _is_authoritative_bridge_confirmation(ctx: ConfirmationContext) -> bool:
    recipient = _normalize_mobile(ctx.get("recipient_phone") or "")
    bridge_number = _normalize_mobile(ctx.get("bridge_number") or "")
    if not bridge_number:
        return False
    if not _is_trusted_escort_source(ctx.get("source") or ""):
        return False

    # Direct system reply from an authorised admin is also authoritative.
    s = get_settings()
    admin_numbers: list[str] = []
    for raw in [s.admin_meta_number, s.admin_bridge1_number, s.admin_bridge2_number, *(s.admin_number_list or [])]:
        norm = _normalize_mobile(raw or "")
        if norm and norm not in admin_numbers:
            admin_numbers.append(norm)

    if not recipient:
        return bridge_number in admin_numbers

    if recipient not in _escort_client_phone_list():
        return False
    mapped = _bridge_number_map().get(ctx.get("source") or "")
    return bool((mapped and bridge_number == mapped) or (bridge_number in admin_numbers))


async def _enqueue_unmatched_confirmation(
    *,
    text: str,
    draft: dict,
    source: str,
    admin_phone: str,
    client_phone: Optional[str],
    message_id: Optional[str],
    candidates: list[dict],
    best_score: Optional[float],
) -> int:
    """Insert a no-confident-match admin confirmation into the review queue
    (migration 052, wbom_unmatched_confirmations), idempotently.

    admin_phone here is the caller's already-authenticated bridge/webhook
    routing identity (handle_admin_escort_completion() only reaches this
    point after _is_authoritative_bridge_confirmation() has already passed)
    -- never a value parsed from message content, per the no-match queue's
    own trust requirement.

    Idempotent on (source, message_id): if this exact message was already
    queued -- a rare cross-path double-delivery neither
    processed_bridge_messages nor processed_outgoing_escort_messages catches
    on its own, see migration 052's header comment -- returns the existing
    row's id instead of inserting a second one. Relies on the same partial
    unique index the migration validated (ON CONFLICT targets it by
    repeating its exact predicate, required by Postgres for partial
    indexes).
    """
    if message_id:
        existing_id = await fetch_val(
            """SELECT id FROM wbom_unmatched_confirmations
               WHERE source_bridge = $1 AND source_message_id = $2""",
            source, message_id,
        )
        if existing_id:
            return existing_id

    queue_id = await fetch_val(
        """INSERT INTO wbom_unmatched_confirmations
               (source_bridge, source_message_id, source,
                admin_phone, client_phone,
                raw_confirmation_text, parsed_confirmation,
                candidate_matches, best_candidate_score)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (source_bridge, source_message_id)
               WHERE source_message_id IS NOT NULL AND source_message_id <> ''
               DO NOTHING
           RETURNING id""",
        source, message_id or None, source,
        admin_phone, client_phone,
        text, json.dumps(draft, default=str),
        json.dumps(candidates), best_score,
    )
    if queue_id is not None:
        return queue_id

    # ON CONFLICT DO NOTHING means a concurrent insert of the identical
    # message won the race between our own pre-check and this INSERT --
    # re-select rather than silently dropping the confirmation.
    existing_id = await fetch_val(
        """SELECT id FROM wbom_unmatched_confirmations
           WHERE source_bridge = $1 AND source_message_id = $2""",
        source, message_id,
    )
    if existing_id:
        return existing_id
    raise RuntimeError(f"unmatched-confirmation insert returned no id and no existing row found (source={source}, message_id={message_id})")


# ── Unmatched-confirmation review actions (Workstream 2 Stage 3) ───────────────
#
# Concurrency model: a non-blocking session-level advisory lock
# (pg_try_advisory_lock) per queue item, and a second one per target
# program_id when a target is involved -- NOT a row-level `SELECT ... FOR
# UPDATE`. A real row lock only holds for the life of an open transaction;
# _update_program_confirmed()/_resolve_escort_employee_id() each acquire
# their own separate pool connection (they don't accept a `conn` param),
# so holding a row lock on one connection while awaiting a write through a
# different connection is exactly the nested-connection-while-locked
# deadlock class Phase 1I found and fixed in sync_program_to_roster() (see
# escort_dualwrite_concurrency_debug_2026-07-17.md) -- not repeating it
# here. Advisory locks don't have that failure mode: they block other
# *lock acquirers*, not other writers, so the nested pool acquisitions
# inside _update_program_confirmed() etc. are unaffected. A concurrent
# reviewer action on the same queue item or target program fails fast
# with a clear conflict (mapped to HTTP 409 in routes.py) instead of
# blocking or silently racing.


class UnmatchedConfirmationConflict(Exception):
    """Raised on any state that means the requested action can no longer
    safely proceed -- lock contention, stale expected-status, already
    resolved, invalid target. routes.py maps this to HTTP 409."""


class UnmatchedConfirmationNotFound(Exception):
    """routes.py maps this to HTTP 404."""


def _advisory_lock_key(*parts: object) -> str:
    return "unmatched_confirmation:" + ":".join(str(p) for p in parts)


@asynccontextmanager
async def _try_locked(conn, *key_parts: object):
    """Non-blocking advisory lock scoped to `conn`. Yields once acquired;
    raises UnmatchedConfirmationConflict immediately if already held by
    another session. Always releases in `finally`, even on error."""
    key = _advisory_lock_key(*key_parts)
    got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", key)
    if not got:
        raise UnmatchedConfirmationConflict(f"another review action is already in progress ({key})")
    try:
        yield
    finally:
        await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", key)


def _parsed_confirmation_dict(queue_row: dict) -> dict:
    parsed = queue_row.get("parsed_confirmation")
    if isinstance(parsed, str):
        try:
            return json.loads(parsed)
        except (json.JSONDecodeError, TypeError):
            return {}
    return parsed or {}


async def link_unmatched_confirmation_to_existing_draft(
    item_id: int,
    target_program_id: int,
    *,
    performed_by: str,
    reason: str,
    expected_queue_status: Optional[str] = None,
    expected_target_status: Optional[str] = None,
) -> dict:
    """Resolve a queued no-match confirmation by linking it to an existing
    draft/program -- Stage 3 §8. Reuses _update_program_confirmed() and
    _resolve_escort_employee_id() verbatim (never a second implementation
    of either). Never creates a second program."""
    async with db_conn() as conn:
        async with _try_locked(conn, "queue", item_id):
            queue_row = await conn.fetchrow(
                "SELECT * FROM wbom_unmatched_confirmations WHERE id = $1", item_id
            )
            if not queue_row:
                raise UnmatchedConfirmationNotFound(f"unmatched confirmation {item_id} not found")
            queue_row = dict(queue_row)

            if queue_row["status"] not in ("pending", "deferred"):
                raise UnmatchedConfirmationConflict(
                    f"queue item {item_id} is already {queue_row['status']!r}, not pending/deferred"
                )
            if expected_queue_status and queue_row["status"] != expected_queue_status:
                raise UnmatchedConfirmationConflict(
                    f"expected queue status {expected_queue_status!r}, found {queue_row['status']!r}"
                )
            if queue_row["resolved_program_id"] is not None:
                raise UnmatchedConfirmationConflict(
                    f"queue item {item_id} already resolved to program {queue_row['resolved_program_id']}"
                )

            async with _try_locked(conn, "program", target_program_id):
                target = await conn.fetchrow(
                    "SELECT * FROM wbom_escort_programs WHERE program_id = $1", target_program_id
                )
                if not target:
                    raise UnmatchedConfirmationNotFound(f"target program {target_program_id} not found")
                target = dict(target)
                if target["status"] not in ("draft", "confirmed"):
                    raise UnmatchedConfirmationConflict(
                        f"target program {target_program_id} is {target['status']!r}, "
                        f"no longer a valid link target"
                    )
                if expected_target_status and target["status"] != expected_target_status:
                    raise UnmatchedConfirmationConflict(
                        f"expected target status {expected_target_status!r}, found {target['status']!r}"
                    )

                old_target_snapshot = dict(target)
                parsed = _parsed_confirmation_dict(queue_row)
                escort_mobile = parsed.get("escort_mobile") or ""
                escort_name = parsed.get("escort_name")

                # Certified employee lookup/create service, reused verbatim --
                # same function the matched-confirmation path already uses.
                emp_id = await _resolve_escort_employee_id(escort_mobile, escort_name, auto_create=True)

                program_date = _parse_program_date(parsed.get("date_str"))
                confirmation_destination = (
                    _normalize_destination(parsed.get("destination") or "") or parsed.get("destination")
                )

                ok = await _update_program_confirmed(
                    target_program_id,
                    escort_name or "",
                    escort_mobile,
                    lighter_vessel=parsed.get("lighter_vessel"),
                    shift=parsed.get("shift"),
                    program_date=program_date,
                    escort_employee_id=emp_id,
                    mother_vessel=parsed.get("mother_vessel"),
                    destination=confirmation_destination,
                    master_mobile=parsed.get("master_mobile"),
                    cargo_type=parsed.get("cargo_type"),
                    importer=parsed.get("importer"),
                    capacity=parsed.get("capacity"),
                )
                if not ok:
                    raise RuntimeError(f"_update_program_confirmed failed for program {target_program_id}")

                await _sync_roster_after_confirm(target_program_id)

                new_target = await fetch_one(
                    "SELECT * FROM wbom_escort_programs WHERE program_id = $1", target_program_id
                )

                await conn.execute(
                    """UPDATE wbom_unmatched_confirmations
                       SET status = 'linked_existing', resolved_program_id = $2,
                           reviewed_by = $3, reviewed_at = NOW(), review_reason = $4,
                           updated_at = NOW()
                       WHERE id = $1""",
                    item_id, target_program_id, performed_by, reason,
                )

                from modules.escort_roster.db import log_audit
                await log_audit(
                    target_program_id,
                    "unmatched_confirmation_linked",
                    old_data=old_target_snapshot,
                    new_data=dict(new_target) if new_target else None,
                    performed_by=performed_by,
                    source="admin_review",
                )

                return {
                    "queue_id": item_id,
                    "resolved_program_id": target_program_id,
                    "employee_id": emp_id,
                    "status": "linked_existing",
                }


async def confirm_unmatched_confirmation_as_new(
    item_id: int,
    *,
    performed_by: str,
    reason: str,
    expected_queue_status: Optional[str] = None,
) -> dict:
    """Resolve a queued no-match confirmation by explicitly confirming it
    is a genuinely separate duty segment -- Stage 3 §9. This is the ONLY
    remaining call path to _create_confirmed_from_admin(); it is never
    reached automatically, only via this explicit, reasoned admin action.
    """
    if not reason or not reason.strip():
        raise ValueError("a mandatory reason is required to confirm a new program")

    async with db_conn() as conn:
        async with _try_locked(conn, "queue", item_id):
            queue_row = await conn.fetchrow(
                "SELECT * FROM wbom_unmatched_confirmations WHERE id = $1", item_id
            )
            if not queue_row:
                raise UnmatchedConfirmationNotFound(f"unmatched confirmation {item_id} not found")
            queue_row = dict(queue_row)

            if queue_row["status"] not in ("pending", "deferred"):
                raise UnmatchedConfirmationConflict(
                    f"queue item {item_id} is already {queue_row['status']!r}, not pending/deferred"
                )
            if expected_queue_status and queue_row["status"] != expected_queue_status:
                raise UnmatchedConfirmationConflict(
                    f"expected queue status {expected_queue_status!r}, found {queue_row['status']!r}"
                )
            if queue_row["resolved_program_id"] is not None:
                raise UnmatchedConfirmationConflict(
                    f"queue item {item_id} already resolved to program {queue_row['resolved_program_id']}"
                )

            # Revalidate a confident match hasn't since appeared (e.g. the
            # real draft arrived/was parsed correctly after this was
            # queued) -- confirm-new must never be used merely because the
            # fuzzy score was low at admission time.
            parsed = _parsed_confirmation_dict(queue_row)
            program_date = _parse_program_date(parsed.get("date_str"))
            now_matching = await _find_pending_program(
                parsed.get("mother_vessel"),
                parsed.get("lighter_vessel"),
                master_mobile=parsed.get("master_mobile"),
                program_date=program_date,
                client_phone=queue_row.get("client_phone"),
            )
            if now_matching:
                raise UnmatchedConfirmationConflict(
                    f"a confident matching draft now exists (program_id="
                    f"{now_matching['program_id']}) -- use link-existing instead"
                )

            draft = parsed
            pid = await _create_confirmed_from_admin(
                draft, queue_row["source"], client_phone=queue_row.get("client_phone")
            )
            if not pid:
                raise RuntimeError(f"_create_confirmed_from_admin failed for queue item {item_id}")

            new_program = await fetch_one(
                "SELECT * FROM wbom_escort_programs WHERE program_id = $1", pid
            )

            await conn.execute(
                """UPDATE wbom_unmatched_confirmations
                   SET status = 'confirmed_new', resolved_program_id = $2,
                       reviewed_by = $3, reviewed_at = NOW(), review_reason = $4,
                       updated_at = NOW()
                   WHERE id = $1""",
                item_id, pid, performed_by, reason,
            )

            from modules.escort_roster.db import log_audit
            await log_audit(
                pid,
                "unmatched_confirmation_confirmed_new",
                old_data=None,
                new_data=dict(new_program) if new_program else None,
                performed_by=performed_by,
                source="admin_review",
            )

            return {"queue_id": item_id, "resolved_program_id": pid, "status": "confirmed_new"}


async def _set_unmatched_confirmation_terminal_status(
    item_id: int,
    *,
    new_status: str,
    performed_by: str,
    reason: Optional[str],
    require_reason: bool,
) -> dict:
    """Shared body for reject/defer -- neither touches wbom_escort_programs,
    so no target-program lock is needed, only the per-queue-item one. The
    queue row's own status/reviewed_by/reviewed_at/review_reason columns
    ARE the audit trail for these two actions (see Stage 3 governance
    note: escort_roster_audit_logs.program_id is NOT NULL, so it cannot
    represent an action with no associated program)."""
    if require_reason and (not reason or not reason.strip()):
        raise ValueError(f"a reason is required to set status={new_status!r}")

    async with db_conn() as conn:
        async with _try_locked(conn, "queue", item_id):
            queue_row = await conn.fetchrow(
                "SELECT * FROM wbom_unmatched_confirmations WHERE id = $1", item_id
            )
            if not queue_row:
                raise UnmatchedConfirmationNotFound(f"unmatched confirmation {item_id} not found")
            queue_row = dict(queue_row)

            if queue_row["status"] not in ("pending", "deferred"):
                raise UnmatchedConfirmationConflict(
                    f"queue item {item_id} is already {queue_row['status']!r}, not pending/deferred"
                )

            await conn.execute(
                """UPDATE wbom_unmatched_confirmations
                   SET status = $2, reviewed_by = $3, reviewed_at = NOW(),
                       review_reason = $4, updated_at = NOW()
                   WHERE id = $1""",
                item_id, new_status, performed_by, reason,
            )
            return {"queue_id": item_id, "status": new_status}


async def reject_unmatched_confirmation(item_id: int, *, performed_by: str, reason: str) -> dict:
    return await _set_unmatched_confirmation_terminal_status(
        item_id, new_status="rejected", performed_by=performed_by, reason=reason, require_reason=True,
    )


async def defer_unmatched_confirmation(item_id: int, *, performed_by: str, reason: Optional[str] = None) -> dict:
    return await _set_unmatched_confirmation_terminal_status(
        item_id, new_status="deferred", performed_by=performed_by, reason=reason, require_reason=False,
    )


async def handle_admin_escort_completion(
    text: str,
    admin_phone: str,
    source: str,
    recipient_phone: Optional[str] = None,
    message_id: Optional[str] = None,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for admin's completed escort draft.
    Finds original client, sends final slip to them, confirms to admin.

    message_id: the bridge's own id for this specific confirmation message,
    when the caller has one (bridge_poller's outgoing-scan omsg_id, or the
    webhook path's event id threaded through process_bridge_inbound() ->
    process_message()). Used only for wbom_unmatched_confirmations' dedup
    identity (migration 052) on the no-match path -- not used on the
    matched-draft path, which already updates idempotently by program_id.
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
        # Draft-Confirmation Invariant (Workstream 2 Stage 2, 2026-07-19):
        # this used to call _create_confirmed_from_admin() directly here
        # ("admin reply is the source of truth"), silently creating a
        # second, unlinked confirmed program row on every fuzzy-match miss.
        # Owner decision: no-match must never auto-create — it now queues
        # for admin review instead (migration 052,
        # wbom_unmatched_confirmations). No draft rows are touched or
        # reconciled here anymore either, since no target draft has been
        # confidently identified — the old post-hoc same-lighter-vessel
        # cleanup pass only make sense once a human has actually linked
        # this confirmation to something.
        candidates = await _score_pending_program_candidates(
            draft["mother_vessel"],
            draft["lighter_vessel"],
            master_mobile=draft.get("master_mobile"),
            program_date=program_date,
            client_phone=recipient_phone,
        )
        top_candidates = candidates[:5]
        best_score = top_candidates[0]["score"] if top_candidates else None

        log.warning(
            f"[escort] no confident matching draft: mv={draft['mother_vessel']} "
            f"lv={draft['lighter_vessel']} escort={draft['escort_name']} "
            f"best_score={best_score} → routing to unmatched-confirmation review queue"
        )

        try:
            queue_id = await _enqueue_unmatched_confirmation(
                text=text,
                draft=draft,
                source=source,
                admin_phone=admin_phone,
                client_phone=recipient_phone,
                message_id=message_id,
                candidates=top_candidates,
                best_score=best_score,
            )
        except Exception as _queue_err:
            log.error(f"[escort] unmatched-confirmation queue insert failed: {_queue_err}")
            return ("Save failed (review queue error). Check logs.", None)

        return (
            f"Queued for review (#{queue_id}). No confident matching draft found — "
            f"an admin must link this to an existing draft or confirm it is a new duty "
            f"before a program is created.",
            None,
        )

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


# ── Trusted Group order intake (2026-07-22, Owner-approved) ────────────────────

async def handle_group_escort_order(
    text: str,
    admin_phone: str,
    source: str,
    group_jid: str,
    message_id: Optional[str] = None,
) -> tuple[str, Optional[dict]]:
    """
    Entry point for a bare (escort-not-yet-assigned) order posted by the
    admin directly into the Trusted Group. The caller (bridge_poller's
    outgoing-scan loop) is responsible for the New-Order-vs-Confirmation
    split: this function is only reached for a message that does NOT match
    is_completed_escort_draft() -- a message that already has escort_name +
    escort_mobile + the company signature line goes to the existing,
    unmodified handle_admin_escort_completion() instead.

    Reuses _is_authoritative_bridge_confirmation() with recipient_phone=""
    (the same "admin messaging with no specific individual recipient"
    branch the DM confirmation flow already relies on) -- no new trust
    logic, no _is_allowed_escort_client() client-allowlist check (that
    check answers "is this stranger a real client," which doesn't apply
    here; authority for this path comes entirely from the verified
    admin-number + verified-group-JID combination, already true by the
    time this function is called).

    Returns ("", None) for anything that doesn't parse as a real order --
    silent, matching every other escort-order safety net in this module.
    The caller does not currently act on the returned strings (mirroring
    handle_admin_escort_completion()'s existing call site, which also
    fires-and-forgets) -- returned for API consistency and testability.
    """
    ctx: ConfirmationContext = {"bridge_number": admin_phone, "recipient_phone": "", "source": source}
    if not _is_authoritative_bridge_confirmation(ctx):
        log.info(
            "[escort] ignore group order from non-authoritative sender=%s source=%s group=%s",
            admin_phone, source, group_jid,
        )
        return "", None

    try:
        order = parse_escort_message(text)
    except Exception as exc:
        log.debug(f"[escort] group order parse failed: {exc}")
        return "", None

    if not is_minimum_escort_order(order):
        return "", None

    log.info(
        f"[escort] group order: group={group_jid} mv={order['mother_vessel']} "
        f"lighters={len(order['lighters'])} msg={message_id}"
    )

    program_ids = await save_escort_programs(
        order, admin_phone, source, is_verified=True,
        group_sourced=True, group_jid=group_jid,
    )
    if not program_ids:
        return "", None

    return (
        f"✅ Order captured as draft (program_id={program_ids[0]}) from Trusted Group, "
        f"TTL-exempt until confirmed or manually cancelled.",
        None,
    )


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


# ── Public exports for cross-module reuse ─────────────────────────────────────
# Task 9: single source of truth for destination normalization
DEST_ALIASES = _DEST_ALIASES
normalize_destination = _normalize_destination
