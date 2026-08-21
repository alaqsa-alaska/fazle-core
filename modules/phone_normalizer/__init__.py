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


def format_for_display(canonical: str) -> str:
    """Return 01812-345678 display format."""
    if len(canonical) == 13 and canonical.startswith("880"):
        local = "0" + canonical[3:]
        return f"{local[:5]}-{local[5:]}"
    return canonical


def format_for_whatsapp(canonical: str) -> str:
    """Return +8801812345678 for WhatsApp API."""
    return "+" + canonical
