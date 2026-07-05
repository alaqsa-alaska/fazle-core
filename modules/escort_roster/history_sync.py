"""
Escort Roster — Historical Conversation Sync Engine

Parses WhatsApp conversation export files to reconstruct escort duty history.

Flow:
  1. Parse conversation file → list of WA messages with timestamps
  2. For each admin outbound message → try to extract escort order(s)
  3. Each message → one escort_order_groups row
  4. Each lighter assignment within that message → one escort_order_lighters row
  5. Match each lighter to existing wbom_escort_programs rows
  6. Upsert matched programs into escort_roster_entries

Handles multiple message formats:
  • Standard: MV <mother> / <lighter> / Master nmbr-<mob> / Escort name: ...
  • Labeled:  Lighter: <name> / Mob: <num>
  • Numbered: 1.<lighter>-<mob> / Escort Name: ...
  • Multi-lighter in one message (split by "Lighter:" keyword)
  • Multi-MV in one message (split by "MV " keyword)

Matching strategy (highest confidence wins):
  1.0  exact escort_mobile match in wbom_escort_programs
  0.95 exact master_mobile match  + lighter name fuzzy ≥ 0.8
  0.85 lighter_vessel fuzzy ≥ 0.85 + date match
  0.70 lighter_vessel fuzzy ≥ 0.70 + mother_vessel fuzzy ≥ 0.70
  0.50 escort_name exact + date match
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

from app.database import execute, fetch_all, fetch_one, fetch_val
from modules.escort import DEST_ALIASES, normalize_destination

log = logging.getLogger("fazle.escort_roster.history_sync")

# ─────────────────────────────────────────────────────────────────────────────
# Regexes & constants
# ─────────────────────────────────────────────────────────────────────────────

# Conversation file header line
_MSG_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] (→ YOU|← [A-Z0-9]+)\s*(.*)"
)

# BD mobile (01X XXXXXXXX — 11 digits) — allow spaces/dashes
_MOBILE_RAW = re.compile(r"\b(01[3-9][\d\s\-]{8,12})\b")
_MOBILE_CLEAN = re.compile(r"\D")

# Date + optional shift — Task 10: import from calculations (single source of truth)
from .calculations import DATE_SHIFT_FULL_RE as _DATE_SHIFT_RE, DATE_ONLY_RE as _DATE_ONLY_RE

# Mother-vessel prefix
_MV_PREFIX_RE = re.compile(
    r"(?i)^[\*\s]*(?:mv\.?|mother[:\s]+mv\.?)\s*(.+)", re.MULTILINE
)

# Escort field labels
_ESCORT_NAME_RE = re.compile(
    r"(?i)(?:escort\s*name|escort)\s*[:=]\s*(.+)"
)
_ESCORT_MOB_RE = re.compile(
    r"(?i)(?:escort\s*(?:mobile|number|mob|no)\s*[:=]|mobile\s*[:=])\s*(.+)"
)
_MASTER_MOB_RE = re.compile(
    r"(?i)(?:master\s*(?:nmbr|number|mob|no|m\.?\s*no)\s*[-:=]?|mob\s*[:=])\s*(.+)"
)
_LIGHTER_LABEL_RE = re.compile(
    r"(?i)^\s*(?:lighter\s*(?:vessel|name)?(?:\s*/\s*lighter\s*(?:vessel|name))?|lv)\s*[:=]\s*(.+)",
    re.MULTILINE,
)
_MOTHER_LABEL_RE = re.compile(r"(?i)^\s*mother\s*vessel\s*[:=]\s*(.+)", re.MULTILINE)
_DEST_RE = re.compile(r"(?i)(?:destination|dest)\s*[:=]\s*(.+)")

NOISE_LINES = {"al aqsa surveillance force", "al-aqsa security service",
               "al aqsa", "al-aqsa", "al aqsa security", "al-aqsa surveillance",
               "al aqsa security service"}

# Destination keywords — use canonical DEST_ALIASES from modules.escort (Task 9)
DESTINATIONS = {v.lower(): v for _, v in DEST_ALIASES}

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WaMessage:
    ts: datetime
    direction: str        # 'outbound' | 'inbound'
    sender_alias: str     # 'YOU' or 'HRIDOY' etc.
    text: str


@dataclass
class LighterRecord:
    mother_vessel: Optional[str] = None
    lighter_vessel: Optional[str] = None
    master_mobile: Optional[str] = None
    escort_name: Optional[str] = None
    escort_mobile: Optional[str] = None
    start_date: Optional[date] = None
    start_shift: Optional[str] = None
    destination: Optional[str] = None
    raw_block: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Conversation file parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_conversation_file(filepath: str) -> list[WaMessage]:
    """Parse a WA conversation export file into WaMessage objects."""
    messages: list[WaMessage] = []
    current: Optional[dict] = None

    with open(filepath, encoding="utf-8") as fh:
        for raw_line in fh:
            m = _MSG_RE.match(raw_line)
            if m:
                if current:
                    messages.append(WaMessage(
                        ts=current["ts"],
                        direction=current["direction"],
                        sender_alias=current["alias"],
                        text="\n".join(current["lines"]),
                    ))
                ts_str, direction_raw, first = m.groups()
                ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M").replace(
                    tzinfo=timezone.utc
                )
                alias = "YOU" if "→" in direction_raw else direction_raw.replace("← ", "").strip()
                direction = "outbound" if alias == "YOU" else "inbound"
                current = {
                    "ts": ts, "direction": direction, "alias": alias,
                    "lines": [first.strip()] if first.strip() else [],
                }
            elif current is not None:
                stripped = raw_line.strip()
                if stripped:
                    current["lines"].append(stripped)

    if current:
        messages.append(WaMessage(
            ts=current["ts"],
            direction=current["direction"],
            sender_alias=current["alias"],
            text="\n".join(current["lines"]),
        ))

    return messages


# ─────────────────────────────────────────────────────────────────────────────
# Field extractors
# ─────────────────────────────────────────────────────────────────────────────

def _clean_mobile(raw: str) -> Optional[str]:
    """Strip non-digits and validate Bangladesh mobile (11 digits, starts 01[3-9])."""
    digits = _MOBILE_CLEAN.sub("", raw)
    if len(digits) == 11 and digits.startswith("01") and digits[2] in "3456789":
        return digits
    if len(digits) == 13 and digits.startswith("880"):
        inner = "0" + digits[3:]
        if inner[2] in "3456789":
            return inner
    return None


def _extract_mobile(text: str) -> Optional[str]:
    for m in _MOBILE_RAW.finditer(text):
        cleaned = _clean_mobile(m.group(1))
        if cleaned:
            return cleaned
    return None


def _extract_all_mobiles(text: str) -> list[str]:
    found = []
    for m in _MOBILE_RAW.finditer(text):
        c = _clean_mobile(m.group(1))
        if c and c not in found:
            found.append(c)
    return found


def _parse_date(d: str, mo: str, y: str) -> Optional[date]:
    year = int(y) if len(y) == 4 else 2000 + int(y)
    try:
        return date(year, int(mo), int(d))
    except ValueError:
        return None


def _extract_date_shift(text: str) -> tuple[Optional[date], Optional[str]]:
    m = _DATE_SHIFT_RE.search(text)
    if m:
        d, mo, y, sh = m.groups()
        dt = _parse_date(d, mo, y)
        shift = "N" if sh.upper().startswith("N") else "D"
        return dt, shift
    m = _DATE_ONLY_RE.search(text)
    if m:
        d, mo, y = m.groups()
        return _parse_date(d, mo, y), None
    return None, None


def _extract_destination(text: str) -> Optional[str]:
    # Explicit label
    m = _DEST_RE.search(text)
    if m:
        val = m.group(1).strip()
        for key, label in DESTINATIONS.items():
            if key in val.lower():
                return label
    # Keyword scan
    tl = text.lower()
    for key, label in DESTINATIONS.items():
        if key in tl:
            return label
    return None


def _norm_vessel(name: str) -> str:
    """Normalise vessel name for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", " ", name.lower()).split()


def _vessel_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _is_noise(line: str) -> bool:
    return line.strip().lower() in NOISE_LINES


# ─────────────────────────────────────────────────────────────────────────────
# Order parser — one message → list[LighterRecord]
# ─────────────────────────────────────────────────────────────────────────────

def _detect_escort_message(text: str) -> bool:
    """Quick check: does this message look like an escort order?"""
    tl = text.lower()
    return bool(
        re.search(r"escort\s*(name|number|mob|mobile)", tl) or
        re.search(r"\bescort\s*[:=]", tl) or
        (re.search(r"\bmv\.?\s+\w", tl) and re.search(r"01[3-9]\d{8}", tl))
    )


def _extract_mv_from_line(line: str) -> Optional[str]:
    """Extract mother vessel name from a line starting with MV/Mv."""
    m = _MV_PREFIX_RE.match(line)
    if m:
        vessel = m.group(1).strip().lstrip("*").rstrip("*").strip()
        # Remove trailing mobile numbers
        vessel = re.sub(r"\b01[3-9]\d{8}\b.*$", "", vessel).strip()
        if vessel and len(vessel) >= 3:
            return vessel
    return None


def _parse_block(block: str, mother_vessel: Optional[str]) -> LighterRecord:
    """
    Parse a text block into a LighterRecord.
    The block is everything associated with ONE lighter assignment.
    """
    rec = LighterRecord(mother_vessel=mother_vessel, raw_block=block)
    lines = [l.strip() for l in block.split("\n") if l.strip() and not _is_noise(l)]

    mobiles = _extract_all_mobiles(block)
    assigned_mobiles: set[str] = set()

    # --- Escort name ---
    for ln in lines:
        m = _ESCORT_NAME_RE.search(ln)
        if m:
            val = m.group(1).strip().lstrip("*").rstrip("*").strip()
            # Remove trailing mobile/noise
            val = re.sub(r"\b01[3-9]\d{8}.*$", "", val).strip()
            val = re.sub(r"\(.*?\)$", "", val).strip()
            if val:
                rec.escort_name = val
            break

    # --- Escort mobile ---
    for ln in lines:
        m = _ESCORT_MOB_RE.search(ln)
        if m:
            raw_mob = m.group(1).strip()
            cleaned = _clean_mobile(raw_mob)
            if not cleaned:
                cleaned = _extract_mobile(raw_mob)
            if cleaned:
                rec.escort_mobile = cleaned
                assigned_mobiles.add(cleaned)
            break

    # --- Master mobile ---
    for ln in lines:
        m = _MASTER_MOB_RE.search(ln)
        if m:
            raw_mob = m.group(1).strip()
            cleaned = _clean_mobile(raw_mob)
            if not cleaned:
                cleaned = _extract_mobile(raw_mob)
            if cleaned:
                rec.master_mobile = cleaned
                assigned_mobiles.add(cleaned)
            break

    # --- Date + shift ---
    dt, sh = _extract_date_shift(block)
    rec.start_date = dt
    rec.start_shift = sh or "D"

    # --- Destination ---
    rec.destination = _extract_destination(block)

    # --- Mother vessel override from explicit label inside block ---
    m = _MOTHER_LABEL_RE.search(block)
    if m:
        mv_label = m.group(1).strip()
        mv_label = re.sub(r"\b01[3-9][\d\s\-]{8,12}\b.*$", "", mv_label).strip()
        if mv_label:
            rec.mother_vessel = mv_label

    # --- Lighter vessel ---
    # Priority 1: explicit "Lighter: <name>" label
    m = _LIGHTER_LABEL_RE.search(block)
    if m:
        val = m.group(1).strip()
        # Remove mobile from end: "Freedom Fighter 01721168020" → "Freedom Fighter"
        val = re.sub(r"\s*01[3-9][\d\s\-]{8,12}\b.*$", "", val).strip()
        # Remove capacity: "Jewel-6, Cap: 900 MT" → "Jewel-6"
        val = re.sub(r"[,\s]+cap.*$", "", val, flags=re.IGNORECASE).strip()
        if val:
            rec.lighter_vessel = val

    if not rec.lighter_vessel:
        # Priority 2: numbered serial "1. <name> - <mobile>" or "2. <name>"
        serial_re = re.compile(r"^\s*\d+[.)]\s*(.+?)(?:\s*[-–]\s*(01[3-9]\d{8}))?\s*$")
        for ln in lines:
            # Skip lines that are actually dates (e.g. "29.01.2026", "2901.2026 (Day)")
            if re.search(r"\d{2}[.\-/]\d{2}[.\-/]\d{2,4}", ln) or re.search(r"\d{4}[.\-/]\d{4}", ln):
                continue
            sm = serial_re.match(ln)
            if sm:
                val = sm.group(1).strip()
                val = re.sub(r"\b01[3-9][\d\s\-]{8,12}\b.*$", "", val).strip()
                # Also skip if captured val is a date fragment (e.g. "01.2026 (night)", "2026 (Day)")
                if re.match(r"^\d{1,2}[.\-/]\d{2,4}", val) or re.match(r"^20\d{2}\b", val):
                    continue
                if val and not val.lower().startswith(("escort", "master", "mob")):
                    rec.lighter_vessel = val
                    # Mobile on same line
                    if sm.group(2) and not rec.master_mobile:
                        rec.master_mobile = sm.group(2)
                    break

    if not rec.lighter_vessel:
        # Priority 3: second non-empty line that's not a field label and not MV
        for ln in lines:
            if _is_noise(ln):
                continue
            if re.search(r"(?i)^\s*mother\s*vessel\s*[:=]", ln):
                continue
            if re.search(r"(?i)^(escort|master|mob|mob[:\s]|date|start|al aqsa)", ln):
                continue
            if re.search(r"\d{2}[.\-/]\d{2}[.\-/]\d{2,4}", ln) or re.search(r"\d{4}[.\-/]\d{4}", ln):
                continue  # date line (also catches "2901.2026" typo format)
            if _extract_mv_from_line(ln):
                continue  # another MV line
            # Must not be pure mobile
            if _clean_mobile(ln):
                # If the line is only a mobile number, it's the master mobile
                if not rec.master_mobile:
                    rec.master_mobile = _clean_mobile(ln)
                continue
            val = re.sub(r"\b01[3-9][\d\s\-]{8,12}\b.*$", "", ln).strip()
            val = re.sub(r"\(.*?\)$", "", val).strip()
            if val and len(val) >= 3 and not val.lower().startswith("al aqsa"):
                rec.lighter_vessel = val
                break

    # --- Fill master_mobile from unassigned mobiles ---
    if not rec.master_mobile:
        for mob in mobiles:
            if mob not in assigned_mobiles:
                rec.master_mobile = mob
                break

    return rec


def parse_message_to_lighters(msg: WaMessage) -> list[LighterRecord]:
    """
    Parse a single WA message into one or more LighterRecord objects.
    One message = one escort_order_groups.
    Multiple lighters in message = multiple escort_order_lighters.
    """
    text = msg.text

    # Skip media-only and clearly non-order messages
    if not _detect_escort_message(text):
        return []

    # ── Step 1: Extract mother vessel(s) ──────────────────────────────────
    lines = text.split("\n")
    current_mv: Optional[str] = None

    # Find first MV line as the primary mother vessel
    for ln in lines:
        mv = _extract_mv_from_line(ln.strip())
        if mv:
            current_mv = mv
            break

    # ── Step 2: Split message into sub-blocks ─────────────────────────────
    # Strategy: split on "Lighter:" keyword or new "MV " declarations

    # First check: does message have "Lighter:" keyword?
    if re.search(r"(?i)lighter\s*:", text):
        # Split into blocks by "Lighter:" — each block is one lighter
        blocks = re.split(r"(?i)(?=lighter\s*:)", text)
        records = []
        active_mv = current_mv
        for blk in blocks:
            blk = blk.strip()
            if not blk:
                continue
            # Check if block starts a new MV
            blk_lines = blk.split("\n")
            for bl in blk_lines:
                mv_test = _extract_mv_from_line(bl.strip())
                if mv_test:
                    active_mv = mv_test
                    break
            if re.search(r"(?i)lighter\s*:", blk):
                rec = _parse_block(blk, active_mv)
                if rec.lighter_vessel or rec.escort_name:
                    records.append(rec)
        return records if records else [_parse_block(text, current_mv)]

    # Second check: multiple MV lines in one message (e.g. April 8 complex batch)
    mv_positions = [(m.start(), _extract_mv_from_line(lines[i].strip()))
                    for i, ln in enumerate(lines)
                    for m in [re.match(r"(?i)\*?mv[.:\s]", lines[i].strip())]
                    if m and _extract_mv_from_line(lines[i].strip())]

    if len(mv_positions) > 1:
        # Multiple MV sections — split message at each MV line.
        # Pattern: "MV mother_vessel\nMv lighter_vessel\n..." where both start
        # with "Mv/MV". Carry a pending_mother from header-only MV blocks into
        # the next block that has escort data.
        full_lines = text.split("\n")
        mv_line_indices = [i for i, ln in enumerate(full_lines)
                           if _extract_mv_from_line(ln.strip())]
        records = []
        pending_mother: Optional[str] = None
        for j, start_idx in enumerate(mv_line_indices):
            end_idx = mv_line_indices[j + 1] if j + 1 < len(mv_line_indices) else len(full_lines)
            block = "\n".join(full_lines[start_idx:end_idx])
            mv_here = _extract_mv_from_line(full_lines[start_idx].strip())

            # Check whether this block has escort data
            has_escort = bool(re.search(r"(?i)escort\s*(name|number|mob)", block))

            if not has_escort:
                # Header-only MV line (mother vessel) — remember for next block
                pending_mother = mv_here
                continue

            # Parse block; if there was a pending mother, this MV line IS the lighter
            rec = _parse_block(block, pending_mother or mv_here)
            if pending_mother and not rec.lighter_vessel:
                rec.lighter_vessel = mv_here
            if pending_mother:
                pending_mother = None

            if rec.lighter_vessel or rec.escort_name:
                records.append(rec)
        return records if records else [_parse_block(text, current_mv)]

    # Default: single order per message
    rec = _parse_block(text, current_mv)
    if rec.lighter_vessel or rec.escort_name:
        return [rec]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Matching engine: LighterRecord → wbom_escort_programs
# ─────────────────────────────────────────────────────────────────────────────

async def _match_to_program(rec: LighterRecord) -> tuple[Optional[int], float, str]:
    """
    Find the best matching wbom_escort_programs row for a LighterRecord.
    Returns (program_id, confidence, method).
    """
    # 1. Exact escort mobile match (highest confidence)
    if rec.escort_mobile:
        row = await fetch_one(
            "SELECT program_id FROM wbom_escort_programs "
            "WHERE escort_mobile = $1 OR escort_mobile = $2 "
            "ORDER BY program_id DESC LIMIT 1",
            rec.escort_mobile, f"0{rec.escort_mobile[2:]}"  # trim leading 0
        )
        if row:
            return row["program_id"], 1.0, "exact_mobile"

    # 2. Master mobile + lighter vessel fuzzy
    if rec.master_mobile:
        candidates = await fetch_all(
            "SELECT program_id, lighter_vessel, mother_vessel, start_date "
            "FROM wbom_escort_programs WHERE master_mobile = $1",
            rec.master_mobile,
        )
        for c in candidates:
            lv_sim = _vessel_sim(rec.lighter_vessel or "", c["lighter_vessel"] or "")
            if lv_sim >= 0.7:
                return c["program_id"], min(0.95, 0.7 + lv_sim * 0.3), "master_mobile_vessel"

    # 3. Lighter vessel + date match (fuzzy vessel ≥ 0.75, ±14 day window)
    if rec.lighter_vessel and rec.start_date:
        candidates = await fetch_all(
            "SELECT program_id, lighter_vessel, start_date, escort_name "
            "FROM wbom_escort_programs "
            "WHERE ABS(program_date::date - $1::date) <= 14 "
            "   OR ABS(start_date::date  - $1::date) <= 14",
            rec.start_date,
        )
        best_id, best_conf = None, 0.0
        for c in candidates:
            lv_sim = _vessel_sim(rec.lighter_vessel, c["lighter_vessel"] or "")
            if lv_sim >= 0.75:
                conf = 0.75 + lv_sim * 0.2
                if conf > best_conf:
                    best_conf, best_id = conf, c["program_id"]
        if best_id:
            return best_id, best_conf, "vessel_date"

    # 3b. Lighter vessel + mother vessel fuzzy (when lighter is correct but date is off)
    if rec.lighter_vessel and rec.mother_vessel:
        candidates = await fetch_all(
            "SELECT program_id, lighter_vessel, mother_vessel "
            "FROM wbom_escort_programs ORDER BY program_id DESC LIMIT 600"
        )
        best_id, best_conf = None, 0.0
        for c in candidates:
            lv_sim = _vessel_sim(rec.lighter_vessel, c["lighter_vessel"] or "")
            mv_sim = _vessel_sim(rec.mother_vessel, c["mother_vessel"] or "")
            if lv_sim >= 0.80 and mv_sim >= 0.70:
                conf = (lv_sim * 0.6 + mv_sim * 0.4) * 0.85
                if conf > best_conf:
                    best_conf, best_id = conf, c["program_id"]
        if best_id:
            return best_id, best_conf, "vessel_pair"

    # 4. Lighter vessel only (high similarity ≥ 0.90)
    if rec.lighter_vessel:
        candidates = await fetch_all(
            "SELECT program_id, lighter_vessel FROM wbom_escort_programs "
            "ORDER BY program_id DESC LIMIT 500"
        )
        best_id, best_sim = None, 0.0
        for c in candidates:
            s = _vessel_sim(rec.lighter_vessel, c["lighter_vessel"] or "")
            if s > best_sim:
                best_sim, best_id = s, c["program_id"]
        if best_id and best_sim >= 0.90:
            return best_id, best_sim * 0.75, "fuzzy_vessel"

    # 5. Escort name + date
    if rec.escort_name and rec.start_date:
        row = await fetch_one(
            "SELECT program_id FROM wbom_escort_programs "
            "WHERE LOWER(escort_name) = LOWER($1) "
            "  AND ABS(program_date::date - $2::date) <= 3 "
            "LIMIT 1",
            rec.escort_name, rec.start_date,
        )
        if row:
            return row["program_id"], 0.50, "name_date"

    return None, 0.0, ""


# ─────────────────────────────────────────────────────────────────────────────
# Main sync functions
# ─────────────────────────────────────────────────────────────────────────────

async def sync_conversation_history(
    filepath: str,
    sender_phone: str = "01670535255",
    actor: str = "history_sync",
    dry_run: bool = False,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> dict:
    """
    Parse a conversation file and upsert all extractable escort orders
    into escort_order_groups / escort_order_lighters, then match to programs.

    date_from / date_to: optional inclusive date filters (message timestamp).
    Returns a summary dict.
    """
    if not Path(filepath).exists():
        raise FileNotFoundError(f"Conversation file not found: {filepath}")

    messages = parse_conversation_file(filepath)
    log.info(f"[history_sync] Parsed {len(messages)} messages from {filepath}")

    stats = {
        "messages_parsed": len(messages),
        "orders_found": 0,
        "groups_created": 0,
        "groups_reused": 0,
        "lighters_created": 0,
        "matched": 0,
        "unmatched": 0,
        "errors": [],
    }

    for msg in messages:
        # Only parse outbound admin messages for escort orders
        if msg.direction != "outbound":
            continue

        # Optional date range filter
        if date_from and msg.ts.date() < date_from:
            continue
        if date_to and msg.ts.date() > date_to:
            continue

        try:
            lighter_records = parse_message_to_lighters(msg)
        except Exception as e:
            stats["errors"].append(f"parse_error@{msg.ts}: {e}")
            continue

        if not lighter_records:
            continue

        stats["orders_found"] += len(lighter_records)

        if dry_run:
            continue

        # Insert parent group (dedup by sender+timestamp)
        group_id = await fetch_val(
            """
            INSERT INTO escort_order_groups
                (source, sender_phone, direction, message_ts, raw_text,
                 mother_vessel, destination, lighter_count)
            VALUES ('conversation_file', $1, 'outbound', $2, $3, $4, $5, $6)
            ON CONFLICT (sender_phone, message_ts) DO NOTHING
            RETURNING group_id
            """,
            sender_phone,
            msg.ts,
            msg.text[:2000],
            lighter_records[0].mother_vessel,
            lighter_records[0].destination,
            len(lighter_records),
        )
        group_inserted = bool(group_id)

        if not group_id:
            # Check if already exists (duplicate message_ts + first 200 chars)
            existing = await fetch_one(
                "SELECT group_id FROM escort_order_groups "
                "WHERE sender_phone = $1 AND message_ts = $2 LIMIT 1",
                sender_phone, msg.ts,
            )
            group_id = existing["group_id"] if existing else None

        if not group_id:
            continue

        if group_inserted:
            stats["groups_created"] += 1
        else:
            stats["groups_reused"] += 1

        # Insert child lighters
        for rec in lighter_records:
            try:
                prog_id, conf, method = await _match_to_program(rec)
                status = "matched" if prog_id else "unmatched"

                lighter_id = await fetch_val(
                    """
                    INSERT INTO escort_order_lighters
                        (group_id, program_id, mother_vessel, lighter_vessel,
                         master_mobile, escort_name, escort_mobile,
                         start_date, start_shift, destination,
                         match_confidence, match_method, status, raw_block)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                    RETURNING lighter_id
                    """,
                    group_id, prog_id,
                    rec.mother_vessel, rec.lighter_vessel,
                    rec.master_mobile, rec.escort_name, rec.escort_mobile,
                    rec.start_date, rec.start_shift, rec.destination,
                    conf, method or None, status, rec.raw_block[:1000],
                )
                stats["lighters_created"] += 1
                if prog_id:
                    stats["matched"] += 1
                else:
                    stats["unmatched"] += 1

                # If matched with high confidence → sync to roster
                if prog_id and conf >= 0.75:
                    try:
                        from modules.escort_roster.db import sync_program_to_roster
                        await sync_program_to_roster(prog_id, actor)
                    except Exception as e:
                        log.warning(f"[history_sync] roster sync failed for {prog_id}: {e}")

            except Exception as e:
                stats["errors"].append(f"lighter_error@{msg.ts}: {e}")
                log.warning(f"[history_sync] lighter insert failed: {e}")

    log.info(f"[history_sync] Done: {stats}")
    return stats


async def sync_from_bridge_sqlite(
    db_path: str,
    bridge_number: str,
    client_phones: list[str],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    actor: str = "sqlite_backfill",
    dry_run: bool = False,
) -> dict:
    """
    Backfill historical escort assignments from a bridge SQLite database.

    Reads is_from_me=1 (outbound admin) messages in chats with client_phones,
    parses each message for escort assignments via parse_message_to_lighters(),
    and inserts program rows into wbom_escort_programs with dedup protection.

    Dedup: skips INSERT if an existing program matches on
    (UPPER(lighter_vessel), date ±1 day).

    Returns a summary dict.
    """
    import sqlite3 as _sqlite3

    if not Path(db_path).exists():
        raise FileNotFoundError(f"Bridge SQLite not found: {db_path}")

    stats = {
        "messages_scanned": 0,
        "orders_found": 0,
        "programs_created": 0,
        "programs_skipped_dup": 0,
        "errors": [],
    }

    # Build JID patterns for each client phone
    jid_patterns = [f"%{ph.lstrip('0').lstrip('880')}%" for ph in client_phones]
    jid_patterns += [f"%{ph}%" for ph in client_phones]

    # Read from SQLite (read-only)
    try:
        con = _sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False, timeout=5.0)
        con.row_factory = _sqlite3.Row

        # Build WHERE clause dynamically for multiple phones
        placeholders = " OR ".join(["chat_jid LIKE ?"] * len(jid_patterns))
        query = f"""
            SELECT id, chat_jid, content, timestamp
            FROM messages
            WHERE is_from_me = 1
              AND ({placeholders})
              AND content IS NOT NULL AND content != ''
            ORDER BY datetime(timestamp) ASC
        """
        rows = con.execute(query, jid_patterns).fetchall()
        con.close()
    except Exception as e:
        log.error(f"[sqlite_backfill] SQLite read error ({db_path}): {e}")
        stats["errors"].append(str(e))
        return stats

    for row in rows:
        content = (row["content"] or "").strip()
        if not content:
            continue

        # Parse timestamp
        try:
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)

        # Date range filter
        msg_date = ts.date()
        if date_from and msg_date < date_from:
            continue
        if date_to and msg_date > date_to:
            continue

        stats["messages_scanned"] += 1

        # Parse message for escort assignments using existing parser
        wa_msg = WaMessage(ts=ts, direction="outbound", sender_alias="YOU", text=content)
        try:
            lighter_records = parse_message_to_lighters(wa_msg)
        except Exception as e:
            stats["errors"].append(f"parse@{ts.date()}: {e}")
            continue

        if not lighter_records:
            continue

        stats["orders_found"] += len(lighter_records)

        if dry_run:
            continue

        for rec in lighter_records:
            try:
                lv = (rec.lighter_vessel or "").strip()
                mv = (rec.mother_vessel or "").strip()
                if not lv and not mv:
                    continue

                # Dedup: skip if a matching program already exists
                # Check: same lighter (UPPER) within ±1 day of start_date
                prog_date = rec.start_date or msg_date
                existing = await fetch_one(
                    """
                    SELECT program_id FROM wbom_escort_programs
                    WHERE UPPER(TRIM(lighter_vessel)) = UPPER(TRIM($1))
                      AND ABS(program_date::date - $2::date) <= 1
                      AND status != 'cancelled'
                    LIMIT 1
                    """,
                    lv, prog_date,
                ) if lv else None

                if existing:
                    stats["programs_skipped_dup"] += 1
                    log.debug(
                        f"[sqlite_backfill] dedup skip: lighter={lv!r} "
                        f"date={prog_date} existing={existing['program_id']}"
                    )
                    continue

                # Insert new program as confirmed (outbound admin message = confirmed state)
                import json as _json
                remarks = {
                    "source": "sqlite_backfill",
                    "bridge_number": bridge_number,
                    "raw_block": rec.raw_block[:500] if rec.raw_block else "",
                }
                new_row = await fetch_one(
                    """
                    INSERT INTO wbom_escort_programs
                        (mother_vessel, lighter_vessel, master_mobile,
                         escort_name, escort_mobile,
                         shift, destination, status, program_date,
                         is_historical, remarks)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'confirmed',
                            COALESCE($8, CURRENT_DATE), TRUE, $9)
                    RETURNING program_id
                    """,
                    mv or "",
                    lv or "",
                    rec.master_mobile or "",
                    rec.escort_name or "",
                    rec.escort_mobile or "",
                    rec.start_shift or "D",
                    rec.destination or "",
                    prog_date,
                    _json.dumps(remarks, ensure_ascii=False),
                )
                if new_row:
                    pid = new_row["program_id"]
                    stats["programs_created"] += 1
                    log.info(
                        f"[sqlite_backfill] created program_id={pid} "
                        f"mv={mv!r} lv={lv!r} escort={rec.escort_name!r} date={prog_date}"
                    )
                    # Sync to roster
                    try:
                        from modules.escort_roster.db import sync_program_to_roster
                        await sync_program_to_roster(pid, actor)
                    except Exception as _sync_err:
                        log.warning(f"[sqlite_backfill] roster sync failed for {pid}: {_sync_err}")

            except Exception as e:
                stats["errors"].append(f"insert@{ts.date()}: {e}")
                log.warning(f"[sqlite_backfill] insert error: {e}")

    log.info(f"[sqlite_backfill] Done — {stats}")
    return stats


async def rebuild_roster_from_history(actor: str = "rebuild") -> dict:
    """
    Re-sync all matched escort_order_lighters (confidence ≥ 0.75) to roster.
    Also re-syncs all wbom_escort_programs → roster.
    """
    from modules.escort_roster.db import sync_all_programs

    log.info("[history_sync] Starting full roster rebuild from history")

    # 1. Sync all programs (idempotent)
    result = await sync_all_programs(actor)

    # 2. Re-process unmatched lighters to see if new programs exist
    unmatched = await fetch_all(
        "SELECT lighter_id, escort_mobile, master_mobile, lighter_vessel, "
        "       start_date, escort_name "
        "FROM escort_order_lighters WHERE status = 'unmatched' LIMIT 500"
    )
    newly_matched = 0
    for row in unmatched:
        from modules.escort_roster.history_sync import LighterRecord
        rec = LighterRecord(
            escort_mobile=row["escort_mobile"],
            master_mobile=row["master_mobile"],
            lighter_vessel=row["lighter_vessel"],
            start_date=row["start_date"],
            escort_name=row["escort_name"],
        )
        prog_id, conf, method = await _match_to_program(rec)
        if prog_id and conf >= 0.50:
            await execute(
                "UPDATE escort_order_lighters "
                "SET program_id=$1, match_confidence=$2, match_method=$3, status='matched' "
                "WHERE lighter_id=$4",
                prog_id, conf, method, row["lighter_id"],
            )
            newly_matched += 1

    result["newly_matched_lighters"] = newly_matched
    return result
