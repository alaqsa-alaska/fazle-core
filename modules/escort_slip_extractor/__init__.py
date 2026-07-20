"""
Fazle Core — Escort Slip Extractor (Phases 2-9)

Intelligent extraction from:
  1. Printed company escort slip (template form)
  2. Fully handwritten blank-paper slip by ghat supervisor
  3. Mixed handwritten/printed images
  4. WhatsApp low-quality camera images

OCR stack: media-processor /ocr (Tesseract) → direct tesseract CLI fallback
Field extraction: region-box cropping for templates, NLP patterns for handwritten.
"""

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, TypedDict

import httpx

from app.config import get_settings
from app.database import execute, fetch_one

log = logging.getLogger("fazle.escort_extractor")


# ── Output schema ──────────────────────────────────────────────────────────────

class SignatureResult(TypedDict):
    lighter_master_signed: bool
    ghat_supervisor_signed: bool
    company_signed: bool
    unknown_signature: bool
    signature_date: Optional[str]
    confidence: float


class EscortSlipResult(TypedDict):
    document_type: str          # printed_template_slip | handwritten_blank_slip | mixed_form | unknown_document
    mother_vessel: Optional[str]
    lighter_vessel: Optional[str]
    master_mobile: Optional[str]
    escort_name: Optional[str]
    escort_mobile: Optional[str]
    start_date: Optional[str]
    start_time: Optional[str]
    completion_date: Optional[str]
    completion_time: Optional[str]
    release_place: Optional[str]
    start_shift: Optional[str]     # D or N (parser-only, not saved to DB)
    end_shift: Optional[str]       # D or N (parser-only, not saved to DB)
    signatures: SignatureResult
    confidence: float
    missing_fields: list[str]
    raw_ocr_text: str
    extraction_id: Optional[int]   # DB row id after save


# ── Required field list ────────────────────────────────────────────────────────

REQUIRED_FIELDS = [
    "mother_vessel", "lighter_vessel", "escort_name",
    "escort_mobile", "start_date", "completion_date",
]

FULL_FIELDS = REQUIRED_FIELDS + [
    "master_mobile", "start_time", "completion_time", "release_place",
]


# ── Label blacklist — these strings can NEVER be a business field value ───────

_LABEL_BLACKLIST: frozenset = frozenset({
    "appointed by", "escort appointed by", "mobile no", "mobile no.",
    "mobde no", "mobde no.", "mob no", "mob no.",
    "no.", "date", "time", "date & time", "date time", "signature",
    "release place", "lighter master", "master mobile", "escort mobile",
    "name of escort", "date & time start", "date & time complection",
    "date & time completion", "authorized", "company", "escort",
    "escort name", "master mob", "escort mob", "ghat supervisor",
    "lighter vessel", "mother vessel", "name of mother vessel",
    "name of lighter vessel", "present address", "house village",
    "advanced pay", "father/mother name", "ghat escort advance",
    "escort suppliers", "suppliers", "word thana", "district",
    "village/area", "m. no", "m.no",
})

# Words that disqualify a release_place candidate
_SIGNATURE_WORDS: frozenset = frozenset({
    "signature", "authorized", "sign", "seal", "signed",
    "lighter master", "ghat supervisor", "company",
})


# ── Document type detection ────────────────────────────────────────────────────

# Keywords that strongly indicate an official printed template
_TEMPLATE_SIGNALS = [
    "escort slip", "name of mother vessel", "name of lighter", "lighter vessel",
    "name of escort", "escort mobile", "master mobile", "master mob",
    "date & time start", "date & time completion", "completion time",
    "release place", "ghat supervisor", "lighter master", "company side",
    "al-aqsa", "al aqsa", "al-manaratayn", "al manaratayn", "skill solutions", "security service", "bangladesh",
]

# Keywords common in supervisor handwritten notes
_HANDWRITTEN_SIGNALS = [
    "mv ", "m.v", "lighter", "escort", "guard", "নাম", "মোবাইল",
    "তারিখ", "সময়", "রিলিজ", "মুক্তি", "সকাল", "রাত", "দুপুর",
]


def detect_document_type(text: str) -> str:
    """Classify image into printed_template_slip / handwritten_blank_slip / mixed_form / unknown_document."""
    t = text.lower()
    template_score = sum(1 for kw in _TEMPLATE_SIGNALS if kw in t)
    handwritten_score = sum(1 for kw in _HANDWRITTEN_SIGNALS if kw in t)

    if template_score >= 3:
        if handwritten_score >= 3:
            return "mixed_form"
        return "printed_template_slip"
    if handwritten_score >= 2:
        return "handwritten_blank_slip"
    if template_score >= 1 or handwritten_score >= 1:
        return "mixed_form"
    return "unknown_document"


# ── OCR stack ──────────────────────────────────────────────────────────────────

async def _ocr_via_media_processor(file_path: str) -> str:
    """Call the local media-processor /ocr endpoint."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            r = await client.post(
                f"{settings.media_processor_url.rstrip('/')}/ocr",
                json={"file_path": file_path},
            )
            if r.status_code == 200:
                return r.json().get("text", "").strip()
            log.warning(f"[escort_extractor] media-processor returned {r.status_code}")
    except Exception as e:
        log.warning(f"[escort_extractor] media-processor error: {e}")
    return ""


def _ocr_tesseract_cli(file_path: str, lang: str = "eng+ben") -> str:
    """Direct tesseract CLI call — fallback or enhanced pass."""
    try:
        result = subprocess.run(
            ["tesseract", file_path, "stdout", "-l", lang, "--psm", "6",
             "--oem", "1"],  # LSTM engine
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        log.warning(f"[escort_extractor] tesseract error: {result.stderr[:200]}")
    except FileNotFoundError:
        log.warning("[escort_extractor] tesseract not found in PATH")
    except subprocess.TimeoutExpired:
        log.warning("[escort_extractor] tesseract timed out")
    except Exception as e:
        log.error(f"[escort_extractor] tesseract exception: {e}")
    return ""


def _preprocess_image(file_path: str) -> Optional[str]:
    """
    Try to preprocess image with ImageMagick (grayscale, sharpen, threshold, contrast)
    to improve OCR quality on low-quality WhatsApp photos.
    Returns temp file path or None if ImageMagick not available.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        result = subprocess.run(
            [
                "convert", file_path,
                "-colorspace", "Gray",
                "-normalize",
                "-sharpen", "0x1.5",
                "-contrast-stretch", "2%x2%",
                "-threshold", "60%",
                tmp_path,
            ],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0 and Path(tmp_path).stat().st_size > 0:
            return tmp_path
    except (FileNotFoundError, subprocess.TimeoutExpired) as _e:
        log.debug(f"preprocess (tool missing/timeout): {_e}")
    except Exception as _e:
        log.warning(f"preprocess error: {_e}")
    Path(tmp_path).unlink(missing_ok=True)
    return None


async def _run_full_ocr(file_path: str) -> str:
    """
    Full OCR pipeline:
      1. media-processor /ocr  (Tesseract, no preprocessing)
      2. Direct tesseract CLI on original image
      3. If preprocessing available: tesseract on preprocessed image
    Merges all results, deduplicates lines, returns best combined text.
    """
    loop = asyncio.get_event_loop()

    # Run OCR passes — media processor + raw CLI in parallel (IO-bound)
    mp_text, cli_text = await asyncio.gather(
        _ocr_via_media_processor(file_path),
        loop.run_in_executor(None, _ocr_tesseract_cli, file_path),
    )

    # Preprocessed pass (sync, may fail silently)
    preproc_text = ""
    preprocessed_path = await loop.run_in_executor(None, _preprocess_image, file_path)
    if preprocessed_path:
        preproc_text = await loop.run_in_executor(None, _ocr_tesseract_cli, preprocessed_path)
        Path(preprocessed_path).unlink(missing_ok=True)

    # Merge: pick longest result, combine unique lines from others
    candidates = [t for t in [mp_text, cli_text, preproc_text] if t]
    if not candidates:
        return ""

    base = max(candidates, key=len)
    base_lines = set(l.strip() for l in base.splitlines() if l.strip())
    extras = []
    for text in candidates:
        if text is base:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line and line not in base_lines and len(line) > 3:
                base_lines.add(line)
                extras.append(line)

    return base + ("\n" + "\n".join(extras) if extras else "")


# ── Field normalization ────────────────────────────────────────────────────────

def _normalize_vessel(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = name.strip().upper()
    # common OCR confusions in vessel names
    name = re.sub(r"\bII\b", "LI", name)   # II → LI (KARNAFUII → KARNAFULI)
    name = name.replace("0", "O").replace("1", "I")  # digits in names
    # Prefix normalization
    name = re.sub(r"^M\s*\.\s*V\s*\.?\s*", "MV ", name)
    name = re.sub(r"^MOTOR\s+VESSEL\s+", "MV ", name)
    return name.strip()


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    """Normalize a phone number from OCR output to canonical 01XXXXXXXXX.

    Handles common OCR confusions (O→0, I→1, l→1, S→5, B→8, Z→2) before
    delegating to the canonical normalize_bd_phone() from the FPE normalizer.
    Returns 11-digit 01XXXXXXXXX (storage format), NOT display format.
    """
    if not phone:
        return None
    # Replace common OCR confusions: O→0, I→1, l→1, S→5, B→8, Z→2
    phone = phone.strip().translate(str.maketrans("OIlSBZ", "011582"))
    from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
    return normalize_bd_phone(phone)


def _normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    # Normalize OCR char confusions before digit processing
    raw = raw.translate(str.maketrans("OIlS", "0115"))
    # Fix OCR spacing: "9-02-20 6" → "9-02-2026"
    raw = re.sub(r"(\d)\s+(\d)", r"\1\2", raw)
    # Normalize separators (also allow space as separator e.g. "10 05 2026")
    raw = re.sub(r"[./ ]", "-", raw)
    # Collapse multiple dashes
    raw = re.sub(r"-{2,}", "-", raw)

    m = re.match(r"(\d{1,2})-(\d{1,2})-(\d{2,4})", raw)
    if not m:
        return None  # reject non-date garbage
    day, month, year = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = "20" + year
    try:
        day_i, month_i, year_i = int(day), int(month), int(year)
        if not (1 <= day_i <= 31 and 1 <= month_i <= 12 and 2000 <= year_i <= 2100):
            # Attempt day/month swap before rejecting
            if month_i > 12 and day_i <= 12:
                day_i, month_i = month_i, day_i
            else:
                return None  # out of range — reject
        return f"{day_i:02d}-{month_i:02d}-{year_i}"
    except ValueError:
        return None


def _normalize_time(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    m = re.match(r"(\d{1,2})\s*:\s*(\d{2})", raw)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.match(r"(\d{1,2})\s*(am|pm|AM|PM)", raw)
    if m:
        h, meridiem = int(m.group(1)), m.group(2).lower()
        if meridiem == "pm" and h != 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return raw


# ── Field validators ──────────────────────────────────────────────────────────

def _is_valid_name(val: str) -> bool:
    """Accept human-name-like values; reject label words, digits, garbage."""
    if not val:
        return False
    v = val.strip()
    if len(v) < 3:
        return False
    vl = v.lower()
    # Reject exact blacklist match
    if vl in _LABEL_BLACKLIST:
        return False
    # Reject if starts with any blacklisted phrase
    for lbl in _LABEL_BLACKLIST:
        if vl.startswith(lbl):
            return False
    # Reject if contains signature/date keywords
    for sw in _SIGNATURE_WORDS:
        if sw in vl:
            return False
    # Reject if mostly digits (more than 40%)
    digits = sum(c.isdigit() for c in v)
    if digits > len(v) * 0.4:
        return False
    # Must have at least 3 alphabetic chars (latin or bengali)
    alpha = re.sub(r"[^a-zA-Z\u0980-\u09FF]", "", v)
    if len(alpha) < 3:
        return False
    return True


def _ocr_phone_normalize(val: str) -> str:
    """Normalize OCR confusions in phone number candidates."""
    val = val.translate(str.maketrans("OoIlSBZ", "0011582"))
    # Collapse spaces/dashes inside digit runs
    val = re.sub(r"(\d)[\s\-]+(\d)", r"\1\2", val)
    return val


def _is_valid_phone_candidate(val: str) -> bool:
    """Return True if val can be normalized to a valid Bangladesh mobile number."""
    if not val:
        return False
    normalized = _ocr_phone_normalize(val)
    digits = re.sub(r"\D", "", normalized)
    if len(digits) == 11 and digits.startswith("01") and digits[2] in "3456789":
        return True
    if len(digits) == 13 and digits.startswith("880") and digits[3] == "1" and digits[4] in "3456789":
        return True
    return False


def _is_valid_date_candidate(val: str) -> bool:
    """Return True if val looks like a real date (after OCR normalization)."""
    if not val:
        return False
    v = val.strip().translate(str.maketrans("OIlS", "0115"))
    v = re.sub(r"(\d)\s+(\d)", r"\1\2", v)
    # Must match a recognizable date pattern
    if re.search(r"\d{1,2}[./ \-]\d{1,2}[./ \-]\d{2,4}", v):
        return True
    if re.search(r"\d{4}-\d{2}-\d{2}", v):
        return True
    return False


def _is_valid_release_place(val: str) -> bool:
    """Reject signature/label contamination in release_place."""
    if not val or len(val.strip()) < 3:
        return False
    vl = val.strip().lower()
    for sw in _SIGNATURE_WORDS:
        if sw in vl:
            return False
    if vl in _LABEL_BLACKLIST:
        return False
    # Reject if value is mostly digits / looks like a reference number
    digits = sum(c.isdigit() for c in vl)
    if digits > len(vl) * 0.4:
        return False
    # Must have at least 3 alphabetic characters
    alpha = re.sub(r"[^a-zA-Z\u0980-\u09FF]", "", vl)
    if len(alpha) < 3:
        return False
    return True


def _score_field_value(field: str, val: str) -> int:
    """Score a candidate value for a field. Negative = invalid/reject."""
    v = val.strip() if val else ""
    if not v or len(v) < 2:
        return -1
    if field == "escort_name":
        return 2 if _is_valid_name(v) else -5
    if field in ("escort_mobile", "master_mobile"):
        return 2 if _is_valid_phone_candidate(v) else -5
    if field in ("start_date", "completion_date"):
        return 2 if _is_valid_date_candidate(v) else -5
    if field == "release_place":
        return 2 if _is_valid_release_place(v) else -5
    return 1  # generic non-empty pass


def _extract_shift(text: str) -> tuple:
    """Extract start_shift and end_shift (D or N) from OCR text."""
    # Keyed by the D/N character position to avoid duplicates
    found_by_pos: dict = {}
    # Explicit markers take priority: (D), (N), DAY, NIGHT
    for pat, norm in [(r"\(D\)", "D"), (r"\(N\)", "N"),
                      (r"\bDAY\b", "D"), (r"\bNIGHT\b", "N")]:
        for m in re.finditer(pat, text, re.IGNORECASE):
            # Key on the D or N char inside the match, not the full-match start
            inner = re.search(r"[DN]", m.group(0), re.IGNORECASE)
            key = m.start() + (inner.start() if inner else 0)
            found_by_pos[key] = norm
    # Standalone D or N only near time/date context (don't overwrite explicit)
    for m in re.finditer(r"\b([DN])\b", text, re.IGNORECASE):
        if m.start() in found_by_pos:
            continue
        ctx = text[max(0, m.start() - 40): m.end() + 40].lower()
        if any(kw in ctx for kw in ("time", "date", "shift", "start", "comp")):
            found_by_pos[m.start()] = m.group(1).upper()
    shifts = [s for _, s in sorted(found_by_pos.items())]
    start_shift: Optional[str] = shifts[0] if shifts else None
    end_shift: Optional[str] = shifts[1] if len(shifts) >= 2 else None
    return start_shift, end_shift


# ── Template extraction (printed form — region patterns) ──────────────────────

# Label patterns for each field, matched against lines of OCR text
_TEMPLATE_PATTERNS: dict[str, list[str]] = {
    "mother_vessel":    [r"mother\s+vessel[:\s]+(.+)", r"m\.?v\.?\s*[:\s]+(.+)", r"vessel\s*name[:\s]+(.+)"],
    "lighter_vessel":   [r"lighter\s+vessel[:\s]+(.+)", r"lighter[:\s]+(.+)", r"l\.?v\.?\s*[:\s]+(.+)"],
    "master_mobile":    [r"master\s+mob(?:ile)?[.:\s]+(.+)", r"master\s+phone[:\s]+(.+)"],
    "escort_name":      [r"name\s+of\s+escort[:\s]+(.+)", r"escort\s+name[:\s]+(.+)", r"escort[:\s]+([A-Za-zঀ-৿\s.]{3,30})"],
    "escort_mobile":    [r"escort\s+mob(?:ile)?[.:\s]+(.+)", r"escort\s+phone[:\s]+(.+)"],
    "start_date":       [r"start\s+date[:\s]+(.+)", r"date\s*&?\s*time\s+start[:\s]+(.+)", r"date\s+start[:\s]+(.+)"],
    "start_time":       [r"start\s+time[:\s]+(.+)", r"time\s+start[:\s]+(.+)"],
    "completion_date":  [r"completion\s+date[:\s]+(.+)", r"date\s*&?\s*time\s+comp[:\s]+(.+)", r"end\s+date[:\s]+(.+)"],
    "completion_time":  [r"completion\s+time[:\s]+(.+)", r"time\s+comp[:\s]+(.+)", r"end\s+time[:\s]+(.+)"],
    "release_place":    [r"release\s+place[:\s]+(.+)", r"release\s+location[:\s]+(.+)", r"place[:\s]+(.+)"],
}


def _extract_template_fields(text: str) -> dict:
    """
    Extract fields from printed template form.

    Improvement: collects ALL candidate matches across all lines,
    scores by (pattern specificity + value validity), picks best.
    No first-match lock — better candidate always wins.
    """
    # best candidate per field: (score, value)
    best: dict[str, tuple] = {k: (-999, None) for k in _TEMPLATE_PATTERNS}
    lines = text.splitlines()

    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        if not line_lower:
            continue

        # Zone: same line text + next 3 non-empty lines (for multi-line label/value)
        zone: list = [line.strip()]
        for j in range(i + 1, min(i + 5, len(lines))):
            nl = lines[j].strip()
            if nl:
                zone.append(nl)
            if len(zone) >= 4:
                break

        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        combined = line_lower + " " + next_line.lower()

        for field, patterns in _TEMPLATE_PATTERNS.items():
            n_pats = len(patterns)
            for pat_idx, pat in enumerate(patterns):
                m = re.search(pat, combined, re.IGNORECASE)
                if not m:
                    continue

                raw_val = m.group(1).strip()

                # If inline capture is short/empty, scan zone lines for actual value
                if not raw_val or len(raw_val) < 2:
                    for zone_line in zone[1:]:
                        candidate = zone_line.strip()
                        if candidate and len(candidate) >= 2:
                            raw_val = candidate
                            break

                if not raw_val or len(raw_val) < 2:
                    continue

                # Reject if the captured value is itself a label/blacklisted phrase
                rv_lower = raw_val.strip().lower()
                if rv_lower in _LABEL_BLACKLIST:
                    continue
                if any(rv_lower.startswith(lbl) for lbl in _LABEL_BLACKLIST):
                    continue

                # Type-specific validity score
                val_score = _score_field_value(field, raw_val)
                if val_score < 0:
                    # Try zone lines as alternative values before giving up
                    found_alt = False
                    for zone_line in zone[1:]:
                        alt = zone_line.strip()
                        if alt and len(alt) >= 2:
                            alt_lower = alt.lower()
                            if alt_lower not in _LABEL_BLACKLIST and not any(
                                alt_lower.startswith(lbl) for lbl in _LABEL_BLACKLIST
                            ):
                                alt_score = _score_field_value(field, alt)
                                if alt_score >= 0:
                                    raw_val = alt
                                    val_score = alt_score
                                    found_alt = True
                                    break
                    if not found_alt:
                        continue

                # Score: earlier (more specific) pattern = higher specificity bonus
                score = (n_pats - pat_idx) + val_score
                if score > best[field][0]:
                    best[field] = (score, raw_val)
                # Do not break — allow later more-specific patterns to win

    return {k: v for k, (_, v) in best.items()}


# ── Handwritten / NLP extraction ───────────────────────────────────────────────

_DATE_RE = re.compile(
    r"\b(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)
_TIME_RE = re.compile(
    r"\b(\d{1,2}:\d{2}(?:\s*(?:am|pm|AM|PM))?|\d{1,2}\s*(?:am|pm|AM|PM))\b",
    re.IGNORECASE,
)
_MOBILE_RE = re.compile(r"\b((?:880|0)?1[3-9]\d[\d\s\-]{6,9})\b")

# Field keyword triggers for handwritten notes
_HW_PATTERNS: dict[str, list[str]] = {
    "mother_vessel":   ["mother vessel", "mv ", "m.v", "mother ship", "ship name", "মাদার", "মাদার ভেসেল"],
    "lighter_vessel":  ["lighter vessel", "lighter ", "lv ", "barge", "boat", "লাইটার", "নৌকা"],
    "escort_name":     ["escort", "guard", "নাম", "escort name", "এস্কর্ট"],
    "escort_mobile":   ["escort mob", "escort phone", "escort no", "এস্কর্ট মোবাইল", "মোবাইল"],
    "master_mobile":   ["master mob", "master phone", "master no", "মাস্টার মোবাইল"],
    "release_place":   ["release place", "release at", "রিলিজ", "ছাড়া", "মুক্তি"],
}


def _extract_handwritten_fields(text: str) -> dict:
    """
    Extract fields from handwritten slip.

    Improvements:
    - OCR-normalized mobile scanning (O→0, I→1 etc.)
    - Validates dates/mobiles before accepting
    - Candidate collection with scoring (no first-match lock)
    - _score_field_value guards escort_name / release_place from labels
    """
    fields: dict[str, Optional[str]] = {
        "mother_vessel": None, "lighter_vessel": None, "master_mobile": None,
        "escort_name": None, "escort_mobile": None, "start_date": None,
        "start_time": None, "completion_date": None, "completion_time": None,
        "release_place": None,
    }
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # OCR-normalized copy for mobile scanning
    norm_text = text.translate(str.maketrans("OoIlSBZ", "0011582"))

    # Extract all dates (validated) and times in document order
    raw_dates = _DATE_RE.findall(text)
    valid_dates = [d for d in raw_dates if _is_valid_date_candidate(d)]
    all_times = _TIME_RE.findall(text)

    # Mobile scan on OCR-normalized text; validate each hit
    raw_mobiles = _MOBILE_RE.findall(norm_text)
    valid_mobiles = [m for m in raw_mobiles if _is_valid_phone_candidate(m)]

    # Positional assignment (start/completion dates, start/completion times)
    if len(valid_dates) >= 1:
        fields["start_date"] = valid_dates[0]
    if len(valid_dates) >= 2:
        fields["completion_date"] = valid_dates[1]
    if len(all_times) >= 1:
        fields["start_time"] = all_times[0]
    if len(all_times) >= 2:
        fields["completion_time"] = all_times[1]
    if len(valid_mobiles) >= 1:
        fields["master_mobile"] = valid_mobiles[0]
    if len(valid_mobiles) >= 2:
        fields["escort_mobile"] = valid_mobiles[-1]

    # Candidate collection for label-based fields (no first-match lock)
    candidates: dict[str, list] = {k: [] for k in _HW_PATTERNS}

    for i, line in enumerate(lines):
        line_lower = line.lower()
        next_val = lines[i + 1] if i + 1 < len(lines) else ""

        for field, triggers in _HW_PATTERNS.items():
            for t_idx, trigger in enumerate(triggers):
                if trigger not in line_lower:
                    continue

                # Extract text after trigger on same line (preserve original case)
                trig_pos = line_lower.find(trigger)
                orig_after = line[trig_pos + len(trigger):].strip(" :.")

                val = orig_after if len(orig_after) >= 2 else next_val.strip()

                if not val or len(val) < 2:
                    continue

                # For vessel names: prefer capitalized word run
                if field in ("mother_vessel", "lighter_vessel"):
                    vessel_m = re.search(r"[A-Z][A-Z\s\-\.]{2,30}", line)
                    if vessel_m:
                        val = vessel_m.group(0).strip()

                # Validate
                score = _score_field_value(field, val)
                if score < 0:
                    # Try next_val as fallback
                    if next_val and len(next_val) >= 2:
                        score2 = _score_field_value(field, next_val)
                        if score2 >= 0:
                            val = next_val
                            score = score2
                        else:
                            continue
                    else:
                        continue

                # More specific triggers (earlier in list) score higher
                total = (len(triggers) - t_idx) + score
                candidates[field].append((total, val))
                break  # matched a trigger; move to next field for this line

    # Pick best candidate per label-based field
    for field, cands in candidates.items():
        if cands:
            _, best_val = max(cands, key=lambda x: x[0])
            # For non-positional fields: allow overwrite only if validated
            if field in ("escort_name", "escort_mobile", "master_mobile", "release_place"):
                fields[field] = best_val
            elif not fields.get(field):
                fields[field] = best_val

    # MV / M.V pattern for vessel names (fallback)
    if not fields["mother_vessel"]:
        m = re.search(r"(?:M\.?V\.?|Motor\s+Vessel)\s+([A-Z][A-Z\s\-]{2,30})", text, re.IGNORECASE)
        if m:
            fields["mother_vessel"] = m.group(1).strip()

    return fields


# ── Signature detection ────────────────────────────────────────────────────────

_SIG_TRIGGERS = {
    "lighter_master":    ["lighter master", "lt master", "master sign", "লাইটার মাস্টার"],
    "ghat_supervisor":   ["ghat supervisor", "ghat sup", "supervisor", "ঘাট সুপারভাইজার", "সুপার"],
    "company":           ["company", "company side", "co. sign", "কোম্পানি"],
}


def detect_signatures(text: str) -> SignatureResult:
    """
    Heuristic signature detection from OCR text.
    Looks for signature section keywords + checks for presence of
    short ink-stroke-like tokens (non-word character clusters).
    """
    t = text.lower()
    result: SignatureResult = {
        "lighter_master_signed": False,
        "ghat_supervisor_signed": False,
        "company_signed": False,
        "unknown_signature": False,
        "signature_date": None,
        "confidence": 0.0,
    }

    found_any = False
    for role, triggers in _SIG_TRIGGERS.items():
        for trig in triggers:
            if trig in t:
                # Look for content near the trigger (signature area)
                idx = t.find(trig)
                surrounding = t[max(0, idx - 30): idx + 80]
                # Signed if there's non-whitespace content after/near the label
                has_content = bool(re.search(r"[A-Za-zঀ-৿]{3,}", surrounding[len(trig):]))
                result[f"{role}_signed"] = has_content  # type: ignore[literal-required]
                found_any = True
                break

    # Unknown signature: non-ascii ink strokes at bottom of text
    last_section = text[-400:] if len(text) > 400 else text
    if re.search(r"[_\-\/\\|]{3,}", last_section):
        result["unknown_signature"] = True

    # Look for date near signature section
    sig_area = text[-600:] if len(text) > 600 else text
    date_m = _DATE_RE.search(sig_area)
    if date_m:
        result["signature_date"] = _normalize_date(date_m.group(0))

    # Confidence: higher if we found labelled signature areas
    signed_count = sum([
        result["lighter_master_signed"],
        result["ghat_supervisor_signed"],
        result["company_signed"],
    ])
    if found_any:
        result["confidence"] = min(0.4 + signed_count * 0.2, 1.0)
    else:
        result["confidence"] = 0.1

    return result


# ── Confidence scoring ─────────────────────────────────────────────────────────

def _score_confidence(fields: dict, doc_type: str) -> float:
    filled = sum(1 for k in REQUIRED_FIELDS if fields.get(k))
    score = filled / len(REQUIRED_FIELDS)
    # Bonus for known document type
    if doc_type in ("printed_template_slip", "mixed_form"):
        score = min(score + 0.1, 1.0)
    return round(score, 2)


# ── Public entry point ─────────────────────────────────────────────────────────

async def extract_escort_slip(
    file_path: str,
    source_label: str = "upload",
    save_to_db: bool = True,
) -> EscortSlipResult:
    """
    Full pipeline: OCR → detect type → extract fields → normalize → signatures → DB save.
    """
    log.info(f"[escort_extractor] Processing {file_path}")
    t0 = time.monotonic()

    raw_text = await _run_full_ocr(file_path)
    doc_type = detect_document_type(raw_text)
    log.info(f"[escort_extractor] doc_type={doc_type} text_len={len(raw_text)}")

    # Choose extraction strategy
    if doc_type == "handwritten_blank_slip":
        fields = _extract_handwritten_fields(raw_text)
    else:
        # printed_template_slip / mixed_form / unknown:
        # Template extraction first, then let validated handwritten fill gaps
        # or overwrite any template value that fails type-specific validation.
        fields = _extract_template_fields(raw_text)
        hw = _extract_handwritten_fields(raw_text)
        for k, v in hw.items():
            if not v:
                continue
            template_val = fields.get(k)
            if not template_val:
                fields[k] = v
            elif k == "escort_name" and not _is_valid_name(template_val):
                fields[k] = v
            elif k in ("escort_mobile", "master_mobile") and not _is_valid_phone_candidate(template_val):
                fields[k] = v
            elif k in ("start_date", "completion_date") and not _is_valid_date_candidate(template_val):
                fields[k] = v
            elif k == "release_place" and not _is_valid_release_place(template_val):
                fields[k] = v

    # Normalize all fields
    fields["mother_vessel"] = _normalize_vessel(fields.get("mother_vessel"))
    fields["lighter_vessel"] = _normalize_vessel(fields.get("lighter_vessel"))
    fields["master_mobile"] = _normalize_phone(fields.get("master_mobile"))
    fields["escort_mobile"] = _normalize_phone(fields.get("escort_mobile"))
    fields["start_date"] = _normalize_date(fields.get("start_date"))
    fields["start_time"] = _normalize_time(fields.get("start_time"))
    fields["completion_date"] = _normalize_date(fields.get("completion_date"))
    fields["completion_time"] = _normalize_time(fields.get("completion_time"))

    # Signature detection
    signatures = detect_signatures(raw_text)

    # Shift extraction (parser-only output, not persisted to DB)
    start_shift, end_shift = _extract_shift(raw_text)

    # Missing fields
    missing = [f for f in FULL_FIELDS if not fields.get(f)]
    confidence = _score_confidence(fields, doc_type)

    elapsed = round(time.monotonic() - t0, 2)
    log.info(f"[escort_extractor] confidence={confidence} missing={missing} elapsed={elapsed}s")

    result: EscortSlipResult = {
        "document_type": doc_type,
        "mother_vessel": fields.get("mother_vessel"),
        "lighter_vessel": fields.get("lighter_vessel"),
        "master_mobile": fields.get("master_mobile"),
        "escort_name": fields.get("escort_name"),
        "escort_mobile": fields.get("escort_mobile"),
        "start_date": fields.get("start_date"),
        "start_time": fields.get("start_time"),
        "completion_date": fields.get("completion_date"),
        "completion_time": fields.get("completion_time"),
        "release_place": fields.get("release_place"),
        "start_shift": start_shift,
        "end_shift": end_shift,
        "signatures": signatures,
        "confidence": confidence,
        "missing_fields": missing,
        "raw_ocr_text": raw_text,
        "extraction_id": None,
    }

    if save_to_db:
        result["extraction_id"] = await _save_extraction(file_path, source_label, result)

    return result


# ── Database save ──────────────────────────────────────────────────────────────

async def _save_extraction(file_path: str, source_label: str, r: EscortSlipResult) -> Optional[int]:
    """Save extraction result to escort_slip_extractions table."""
    try:
        row = await fetch_one(
            """
            INSERT INTO escort_slip_extractions (
                source_file, document_type,
                mother_vessel, lighter_vessel,
                escort_name, escort_mobile, master_mobile,
                start_date, completion_date, release_place,
                signatures_json, confidence, raw_text,
                created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW()
            )
            RETURNING id
            """,
            source_label or file_path,
            r["document_type"],
            r["mother_vessel"],
            r["lighter_vessel"],
            r["escort_name"],
            r["escort_mobile"],
            r["master_mobile"],
            r["start_date"],
            r["completion_date"],
            r["release_place"],
            json.dumps(r["signatures"]),
            r["confidence"],
            r["raw_ocr_text"][:8000],  # cap to avoid oversized text
        )
        if row:
            log.info(f"[escort_extractor] Saved extraction id={row['id']}")
            return row["id"]
    except Exception as e:
        log.error(f"[escort_extractor] DB save error: {e}")
    return None


# ── Live test report ───────────────────────────────────────────────────────────

async def test_report(file_path: str) -> str:
    """Generate a human-readable test report for a single image."""
    r = await extract_escort_slip(file_path, save_to_db=False)
    lines = [
        f"FILE: {file_path}",
        f"Type: {r['document_type']}",
        f"Confidence: {r['confidence']:.0%}",
        "",
        "Fields extracted:",
        f"  Mother Vessel    : {r['mother_vessel'] or '—'}",
        f"  Lighter Vessel   : {r['lighter_vessel'] or '—'}",
        f"  Master Mobile    : {r['master_mobile'] or '—'}",
        f"  Escort Name      : {r['escort_name'] or '—'}",
        f"  Escort Mobile    : {r['escort_mobile'] or '—'}",
        f"  Start Date       : {r['start_date'] or '—'}",
        f"  Start Time       : {r['start_time'] or '—'}",
        f"  Completion Date  : {r['completion_date'] or '—'}",
        f"  Completion Time  : {r['completion_time'] or '—'}",
        f"  Release Place    : {r['release_place'] or '—'}",
        f"  Start Shift      : {r.get('start_shift') or '—'}",
        f"  End Shift        : {r.get('end_shift') or '—'}",
        "",
        "Signatures:",
        f"  Lighter Master   : {'YES' if r['signatures']['lighter_master_signed'] else 'NO'}",
        f"  Ghat Supervisor  : {'YES' if r['signatures']['ghat_supervisor_signed'] else 'NO'}",
        f"  Company          : {'YES' if r['signatures']['company_signed'] else 'NO'}",
        f"  Unknown          : {'YES' if r['signatures']['unknown_signature'] else 'NO'}",
        f"  Sig Date         : {r['signatures']['signature_date'] or '—'}",
    ]
    if r["missing_fields"]:
        lines += ["", f"Missing: {', '.join(r['missing_fields'])}"]
    lines += [
        "",
        "Manual correction suggestions:",
    ]
    if not r["mother_vessel"]:
        lines.append("  - Mother vessel not detected. Look for 'MV ...' text near top of slip.")
    if not r["escort_name"]:
        lines.append("  - Escort name not detected. Check 'Name of Escort:' label area.")
    if not r["start_date"]:
        lines.append("  - Start date not found. Check 'Date & Time Start:' area.")
    if not r["missing_fields"]:
        lines.append("  - All required fields extracted successfully.")

    return "\n".join(lines)
