"""
Bangla / mixed-language label-aware field extraction (Phase B Stage 3).

Fixes a real, verified gap in modules.escort.parse_escort_message():
it has no Bangla label-stripping, so "ভেসেল নেম Horda" is read as a
literal vessel name containing the Bangla words themselves --
"MV ভেসেল নেম HORDA" instead of "MV HORDA".

This is a reusable label-aware extractor, not a one-off patch for that
one string: every label below is matched generically, longest-label-first
so a specific label ("মাদার ভেসেলের নাম") is never shadowed by a shorter
generic one ("ভেসেল") that happens to be a substring of it.

Context sensitivity (Part 12): the generic "ভেসেল নেম" / "ভেসেলের নাম"
labels do not say *which* vessel (mother or lighter) -- Bangla client
messages use them for whichever vessel is being clarified at that point
in the conversation. Rather than guessing, this module always returns a
LOW-confidence mother_vessel candidate for the generic label; the caller
(thread_reconstruction._observe_stage2) is the one with the conversation
context needed to decide whether to trust it -- promoting it to
high-confidence only when the thread already has lighter_vessel and
master_mobile (i.e. the mother vessel is the only thing left missing).
"""
from __future__ import annotations

import re
from typing import Optional

_BN_DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_MARKUP_CHARS = "*_~`"

# Longest-first within each group -- matched greedily so a specific label
# is never shadowed by a shorter one that happens to be its prefix/suffix.
_MOTHER_VESSEL_STRONG_LABELS = [
    "মাদার ভেসেলের নাম", "মাদার ভেসেল", "মাদার শিপ", "mother vessel",
]
_MOTHER_VESSEL_WEAK_LABELS = ["ভেসেলের নাম", "ভেসেল নেম"]
_LIGHTER_LABELS = ["লাইটার ভেসেল", "লাইটার নাম", "লাইটার"]
_MASTER_MOBILE_LABELS = ["মাস্টার মোবাইল", "মোবাইল নম্বর", "মাস্টার"]
_DESTINATION_LABELS = ["গন্তব্য", "ডেস্টিনেশন"]
_TRIP_LABELS = ["দ্বিতীয় ট্রিপ", "২য় ট্রিপ", "ট্রিপ"]
_CAPACITY_LABELS = ["ক্যাপাসিটি"]

_MV_PREFIX_RE = re.compile(r"^\s*(এমভি|m\.?\s*v\.?)\s+", re.IGNORECASE)
_CAPACITY_UNIT_RE = re.compile(r"টন|এমটি|\bmt\b", re.IGNORECASE)
_TRIP_SUFFIX_RE = re.compile(r"\(\s*(\d+)\s*(?:st|nd|rd|th)?\s*trip\s*\)", re.IGNORECASE)
_TRIP_ORDINAL_WORDS = {"1": "1st", "2": "2nd", "3": "3rd"}


def normalize_bangla_digits(text: str) -> str:
    """০-৯ -> 0-9. Every numeric-bearing extraction (mobile, capacity,
    trip number) runs through this first."""
    return text.translate(_BN_DIGIT_MAP)


def strip_whatsapp_markup(text: str) -> str:
    """Strips WhatsApp's *bold*/_italic_/~strike~/`mono` markers. Simple
    character strip rather than a real markdown parser -- these markers
    never appear as meaningful data in escort order text."""
    return "".join(ch for ch in text if ch not in _MARKUP_CHARS)


def _clean_value(raw: str) -> str:
    text = strip_whatsapp_markup(normalize_bangla_digits(raw))
    text = text.strip(" \t:：-–—।\n")
    return " ".join(text.split())


def normalize_mother_vessel_value(raw: str) -> str:
    """Strips an already-present MV/M.V./এমভি prefix (so it isn't
    doubled), then re-applies the canonical "MV " + uppercased name --
    the same canonical form modules.escort.parse_escort_message() already
    produces for English input, so downstream code never has to special-
    case which parser a value came from."""
    text = _clean_value(raw)
    text = _MV_PREFIX_RE.sub("", text).strip()
    return f"MV {text.upper()}" if text else ""


def normalize_capacity_value(raw: str) -> Optional[str]:
    text = _clean_value(raw)
    if not text:
        return None
    text = _CAPACITY_UNIT_RE.sub("MT", text)
    match = re.search(r"(\d+(?:\.\d+)?)\s*MT", text, re.IGNORECASE)
    if match:
        return f"{match.group(1)} MT"
    return text or None


def normalize_trip_value(raw: str) -> Optional[str]:
    text = _clean_value(raw)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        ordinal = _TRIP_ORDINAL_WORDS.get(digits, f"{digits}th")
        return f"{ordinal} Trip"
    return text


def split_trip_suffix(vessel_text: str) -> tuple[str, Optional[str]]:
    """"Bushra Biva-1(2nd Trip)" -> ("Bushra Biva-1", "2nd Trip"). Applies
    to whichever parser produced the lighter_vessel value (English or
    Bangla) -- trip normalization is language-independent, Section 13's
    mandatory example just happens to exercise it via the English
    parser's untouched lighter_vessel string."""
    if not vessel_text:
        return vessel_text, None
    text = normalize_bangla_digits(vessel_text)
    match = _TRIP_SUFFIX_RE.search(text)
    if not match:
        return vessel_text.strip(), None
    number = match.group(1)
    ordinal = _TRIP_ORDINAL_WORDS.get(number, f"{number}th")
    clean = (text[:match.start()] + text[match.end():]).strip()
    return clean, f"{ordinal} Trip"


def _find_label_value(text: str, labels: list[str]) -> Optional[str]:
    """First match wins, checked longest-label-first (labels lists are
    already ordered that way). Value is whatever follows the label up to
    the end of that line."""
    for line in text.split("\n"):
        lowered = line.lower()
        for label in labels:
            idx = lowered.find(label.lower())
            if idx != -1:
                return line[idx + len(label):]
    return None


def extract_labeled_fields(text: str, *, has_lighter: bool, has_master_mobile: bool) -> dict:
    """Main entry point. Returns {field_name: {"value", "confidence",
    "is_explicit_label", "extraction_method"}} for every label found.
    Only includes fields that actually matched a label -- callers merge
    this on top of (or instead of) the existing single-message parser's
    output.

    has_lighter / has_master_mobile: the calling thread's CURRENT
    accumulated state (Part 12's context-sensitivity requirement) -- not
    this message's own parse. Determines whether the generic "ভেসেল নেম"
    label is trusted as a high-confidence mother-vessel label or held
    back as a low-confidence, ambiguous one.
    """
    result: dict = {}

    strong_mv = _find_label_value(text, _MOTHER_VESSEL_STRONG_LABELS)
    if strong_mv is not None:
        value = normalize_mother_vessel_value(strong_mv)
        if value:
            result["mother_vessel"] = {
                "value": value, "confidence": 0.9,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    if "mother_vessel" not in result:
        weak_mv = _find_label_value(text, _MOTHER_VESSEL_WEAK_LABELS)
        if weak_mv is not None:
            value = normalize_mother_vessel_value(weak_mv)
            if value:
                # Context-sensitive promotion: the generic label only
                # becomes a confident, explicit mother-vessel candidate
                # once lighter+master are already known -- otherwise it's
                # genuinely ambiguous (could be naming either vessel) and
                # must not be force-treated as high confidence.
                if has_lighter and has_master_mobile:
                    result["mother_vessel"] = {
                        "value": value, "confidence": 0.85,
                        "is_explicit_label": True, "extraction_method": "bangla_label_generic_promoted",
                    }
                else:
                    result["mother_vessel"] = {
                        "value": value, "confidence": 0.3,
                        "is_explicit_label": False, "extraction_method": "bangla_label_generic_ambiguous",
                    }

    lighter = _find_label_value(text, _LIGHTER_LABELS)
    if lighter is not None:
        clean, trip = split_trip_suffix(_clean_value(lighter))
        if clean:
            result["lighter_vessel"] = {
                "value": clean, "confidence": 0.85,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }
        if trip:
            result["trip"] = {
                "value": trip, "confidence": 0.85,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    mobile = _find_label_value(text, _MASTER_MOBILE_LABELS)
    if mobile is not None:
        from modules.escort import _normalize_mobile
        normalized = _normalize_mobile(_clean_value(mobile))
        if normalized:
            result["master_mobile"] = {
                "value": normalized, "confidence": 0.9,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    dest = _find_label_value(text, _DESTINATION_LABELS)
    if dest is not None:
        value = _clean_value(dest)
        if value:
            result["destination"] = {
                "value": value, "confidence": 0.7,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    trip = _find_label_value(text, _TRIP_LABELS)
    if trip is not None and "trip" not in result:
        value = normalize_trip_value(trip)
        if value:
            result["trip"] = {
                "value": value, "confidence": 0.8,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    capacity = _find_label_value(text, _CAPACITY_LABELS)
    if capacity is not None:
        value = normalize_capacity_value(capacity)
        if value:
            result["capacity"] = {
                "value": value, "confidence": 0.75,
                "is_explicit_label": True, "extraction_method": "bangla_label",
            }

    return result
