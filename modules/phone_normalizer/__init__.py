"""
Phone number canonicalization for Bangladeshi numbers.

All internal storage uses the 13-digit canonical form: 8801XXXXXXXXX
"""
import re
from typing import Optional

BANGLADESH_COUNTRY_CODE = "880"
VALID_OPERATORS = {"11", "12", "13", "14", "15", "16", "17", "18", "19"}

# 2026-08-21 fix (Bengali-numeral phone parsing, real candidate case: a
# recruitment reply of "০১৭১৬০৮১৯২৫" -- a genuinely valid number, just
# typed in Bengali digits -- was never recognized). Root cause: Python's
# `\D`/`\d` already treat Bengali digits (০-৯, Unicode category Nd) as
# digits, so `re.sub(r"\D", "", raw)` below correctly LEFT them in place
# rather than stripping them -- but every check after that point compares
# against ASCII literals ("0", "880", VALID_OPERATORS's "11".."19"), which
# a Bengali "০"/"১" character can never equal even though it's the same
# digit semantically. Translating to ASCII first (this is "the existing
# canonical phone normalizer" the Bengali-numeral fix is meant to reuse,
# per Owner instruction) fixes every caller of normalize_phone() at once,
# not just recruitment -- no separate Bengali-phone code path invented.
_BENGALI_TO_ASCII_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def to_ascii_digits(text: str) -> str:
    """Transliterate Bengali numerals (০-৯) to ASCII (0-9); anything else
    passes through unchanged. Safe to call on mixed or all-ASCII input."""
    if not text:
        return text
    return text.translate(_BENGALI_TO_ASCII_DIGITS)


def normalize_phone(raw: str) -> Optional[str]:
    """
    Normalize any Bangladeshi phone format to 8801XXXXXXXXX.

    Accepted inputs:
        +8801812345678 / 8801812345678 (13 digits with country code)
        01812345678                    (11 digits, leading 0)
        1812345678                     (10 digits, no prefix)
        01812-345678                   (hyphenated)
        Bengali-numeral forms of all of the above (০১৮১২৩৪৫৬৭৮)

    Returns None for unrecognized or invalid numbers.
    """
    if not raw:
        return None

    raw = to_ascii_digits(raw)
    digits = re.sub(r"\D", "", raw)

    if digits.startswith("880") and len(digits) == 13:
        canonical = digits
    elif digits.startswith("0") and len(digits) == 11:
        canonical = "880" + digits[1:]
    elif digits.startswith("1") and len(digits) == 10:
        canonical = "880" + digits
    else:
        return None

    if canonical[3:5] not in VALID_OPERATORS:
        return None

    return canonical


def storage_canonical_phone(raw: Optional[str]) -> Optional[str]:
    """Best value for the ``wbom_whatsapp_messages.canonical_phone`` column.

    Rules (2026-09-01 — was previously stored verbatim with no normalization):
      * a Bangladeshi number in ANY accepted form (8801…, 01…, 1…, +880…,
        hyphenated, Bengali-numeral)  ->  canonical ``8801XXXXXXXXX``
      * a LID sentinel (``unresolved:<digits>`` / ``lid:<digits>``)  ->  ``None``
        (a LID is never a phone number)
      * a JID (``…@s.whatsapp.net`` / ``…@lid``) or a ``+``/space/hyphen
        formatted number  ->  digits only, JID suffix stripped
      * a plausible NON-BD international number (8–15 digits)  ->  digits only,
        no ``+`` — this column already legitimately holds foreign numbers and
        must not be nulled out for them
      * anything shorter than 8 digits, longer than 15 (e.g. a Facebook PSID),
        or otherwise unusable  ->  ``None``

    Never returns ``+``, spaces, hyphens, a JID suffix, or a LID sentinel.
    """
    if not raw:
        return None
    s = str(raw).strip()
    low = s.lower()
    if low.startswith("unresolved:") or low.startswith("lid:"):
        return None
    bd = normalize_phone(s)
    if bd:
        return bd
    s = to_ascii_digits(s)
    if "@" in s:
        s = s.split("@", 1)[0]
    digits = re.sub(r"\D", "", s)
    if 8 <= len(digits) <= 15:
        return digits
    return None


def storage_phone_last10(canonical_or_raw: Optional[str]) -> Optional[str]:
    """Last 10 digits of a canonicalized phone — a search aid only. Returns
    ``None`` for a LID sentinel or an unusable value (never a 10-digit slice
    of a LID). Feed it the output of :func:`storage_canonical_phone`."""
    c = storage_canonical_phone(canonical_or_raw)
    if not c:
        return None
    digits = re.sub(r"\D", "", c)
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def format_for_display(canonical: str) -> str:
    """Return 01812-345678 display format."""
    if len(canonical) == 13 and canonical.startswith("880"):
        local = "0" + canonical[3:]
        return f"{local[:5]}-{local[5:]}"
    return canonical


def format_for_whatsapp(canonical: str) -> str:
    """Return +8801812345678 for WhatsApp API."""
    return "+" + canonical
