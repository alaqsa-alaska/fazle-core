"""
Fazle Payroll Engine — Phone & text normalization utilities.

This module is the SINGLE SOURCE OF TRUTH for Bangladeshi phone normalization
across the entire codebase.  All other modules must import from here.

Canonical storage format:  01XXXXXXXXX  (11 digits, starts with 01)
WBOM/RBAC domain format:   8801XXXXXXXXX (13 digits, for wbom_*/user_profiles)
Cross-domain match key:     last 9 digits (the true phone identity)

Owner Directive (2026-07-01):
  The last 9 digits of a Bangladeshi mobile number are the real identity.
  Adding "01" before them gives the 11-digit local form.
  Adding "880" before the 11-digit form gives the 13-digit E.164 form.
  Adding "+880" or "00880" before the 13-digit form gives international format.
  If the last 9 digits are wrong, no prefix will fix it.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

_DIGITS = re.compile(r"\D+")
_BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def normalize_bd_phone(raw: Optional[str]) -> Optional[str]:
    """
    Normalize any Bangladesh phone number to canonical 01XXXXXXXXX (11 digits).
    Returns None if the input cannot be resolved to a valid BD number.

    Accepted inputs:
      - 017XXXXXXXX                  (already canonical)
      - +88017XXXXXXXX / 88017XXXXXXXX
      - 1712345678                   (10-digit, missing leading zero — common
                                      when typed in WhatsApp without the 0)
    Bengali numerals are converted to ASCII before parsing.
    """
    if not raw:
        return None
    # Convert Bengali/Arabic-Indic numerals to ASCII
    s = str(raw).translate(_BENGALI_DIGITS)
    # Strip all non-digits
    d = _DIGITS.sub("", s)
    if not d:
        return None
    # Strip leading country code (880 / 88)
    if d.startswith("880") and len(d) >= 13:
        d = d[3:]
    elif d.startswith("88") and len(d) >= 12:
        d = d[2:]
    # 10-digit form (e.g. 1725494969) — prepend leading zero
    if len(d) == 10 and d.startswith("1"):
        d = "0" + d
    # Must now be exactly 11 digits starting with 01
    if len(d) != 11 or not d.startswith("01"):
        return None
    return d


# ── Cross-domain conversion helpers ───────────────────────────────────────────

def to_880(canonical_11: Optional[str]) -> str:
    """Convert 01XXXXXXXXX → 8801XXXXXXXXX (for WBOM/RBAC/WhatsApp API domain).

    >>> to_880("01712345678")
    '8801712345678'
    >>> to_880("8801712345678")
    '8801712345678'
    >>> to_880(None)
    ''
    """
    if not canonical_11:
        return ""
    if canonical_11.startswith("880"):
        return canonical_11
    if canonical_11.startswith("01") and len(canonical_11) == 11:
        return "880" + canonical_11[1:]
    return canonical_11


def from_880(canonical_13: Optional[str]) -> str:
    """Convert 8801XXXXXXXXX → 01XXXXXXXXX (for FPE domain).

    >>> from_880("8801712345678")
    '01712345678'
    >>> from_880("01712345678")
    '01712345678'
    >>> from_880(None)
    ''
    """
    if not canonical_13:
        return ""
    if canonical_13.startswith("8801") and len(canonical_13) == 13:
        return "0" + canonical_13[3:]
    return canonical_13


def phone_key9(phone: Optional[str]) -> str:
    """Extract the last 9 digits — the true identity for cross-domain matching.

    All Bangladeshi mobile numbers share the same last 9 digits regardless
    of prefix (01, 880, +880, 00880).  This function extracts those 9 digits
    for use in WHERE clauses that must match across FPE (11-digit) and
    WBOM/RBAC (13-digit) tables.

    >>> phone_key9("01712345678")
    '712345678'
    >>> phone_key9("8801712345678")
    '712345678'
    >>> phone_key9("+8801712345678")
    '712345678'
    >>> phone_key9(None)
    ''
    """
    digits = _DIGITS.sub("", str(phone or ""))
    # Also handle Bengali numerals
    digits = digits.translate(_BENGALI_DIGITS)
    return digits[-9:] if len(digits) >= 9 else digits


def phones_match(a: Optional[str], b: Optional[str]) -> bool:
    """Check if two phone numbers refer to the same person (last 9 digits match).

    >>> phones_match("01712345678", "8801712345678")
    True
    >>> phones_match("01712345678", "01812345678")
    False
    >>> phones_match(None, "01712345678")
    False
    """
    ka, kb = phone_key9(a), phone_key9(b)
    if not ka or not kb:
        return False
    return ka == kb


def normalize_name(raw: Optional[str]) -> str:
    """
    Normalize a name for comparison:
    - lowercase
    - NFKC unicode normalization
    - strip leading/trailing whitespace
    - collapse internal whitespace
    - remove trailing punctuation like '-', '.'
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", raw.strip().lower())
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip("-. ")
    return s


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PUNCT_TO_SPACE = re.compile(r"[^a-z0-9\s]+")


def normalize_search_text(raw: Optional[str]) -> str:
    """Search-friendly form: lowercase, NFKC, hyphens/punct → space, collapsed.

    Examples:
        "Al-Momin"          → "al momin"
        "  Al  Momin "      → "al momin"
        "Debashish, S.G."   → "debashish s g"
        "ALMOMIN"           → "almomin"
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).lower()
    s = _PUNCT_TO_SPACE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def collapse_search_text(raw: Optional[str]) -> str:
    """Collapsed token form (no spaces, no punctuation).

    Examples:
        "Al-Momin"          → "almomin"
        "Al Momin"          → "almomin"
        "Shohel Halisahar"  → "shohelhalisahar"
        "Debashish SG"      → "debashishsg"
    """
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", str(raw)).lower()
    return _NON_ALNUM.sub("", s)


def normalize_amount(raw: Optional[str]) -> Optional[float]:
    """
    Parse amount strings like "2,200", "2200", "1,530" → float.
    Returns None if unparseable.
    """
    if not raw:
        return None
    s = str(raw).translate(_BENGALI_DIGITS)
    s = s.replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def normalize_payout_method(raw: Optional[str]) -> str:
    """
    Normalize method code to canonical name.
    Handles single-letter codes and full names.
    """
    if not raw:
        return "unknown"
    m = raw.strip().lower()
    mapping = {
        "b": "bkash", "bkash": "bkash",
        "n": "nagad",  "nagad": "nagad",
        "c": "cash",   "cash": "cash",
        "r": "rocket", "rocket": "rocket",
        "bk": "bkash",
    }
    return mapping.get(m, "unknown")


def jid_to_phone(jid: str) -> Optional[str]:
    """
    Extract phone number from WhatsApp JID.
    '8801958122300@s.whatsapp.net' → '01958122300'
    '101481722183752@lid' → None (LID, not directly resolvable)
    """
    if "@s.whatsapp.net" in jid:
        raw = jid.split("@")[0]
        return normalize_bd_phone(raw)
    return None
