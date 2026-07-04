"""
Fazle Core — Shared Phone Normalization
========================================

A thin, stable re-export layer so all modules can import from one
consistent location without depending on each other directly.

CANONICAL SOURCE OF TRUTH: modules.fazle_payroll_engine.normalizer
  - normalize_bd_phone() → 01XXXXXXXXX (11-digit, FPE canonical storage)
  - to_880()             → 8801XXXXXXXXX (13-digit, WBOM/RBAC domain)
  - from_880()           → 01XXXXXXXXX (convert from 13-digit)
  - phone_key9()         → last 9 digits (cross-domain match key)
  - phones_match()       → compare two phones by last 9 digits

USAGE
-----
    from shared.phone import normalize_phone, phones_match, phone_key9
    from shared.phone import extract_phone_candidates, to_880, from_880

DO NOT add business logic here — keep it as a pure passthrough.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Canonical re-exports from the single source of truth ─────────────────────
from modules.fazle_payroll_engine.normalizer import (
    normalize_bd_phone,
    to_880,
    from_880,
    phone_key9,
    phones_match,
)

# Legacy compatibility: number_identity still provides canonical_phone (13-digit)
# and phone_last10 for existing callers that expect the 13-digit format.
from modules.number_identity import (
    canonical_phone,
    phone_last10,
)

# ── Phone extraction helpers ──────────────────────────────────────────────────

# Strip spaces/hyphens that appear BETWEEN digits so:
#   "+880 1849-258074" → "+8801849258074"
#   "01849-258074"     → "01849258074"
_RE_INTERDIGIT_NOISE = re.compile(r"(?<=\d)[\s\-](?=\d)")

# Loose BD phone pattern applied AFTER inter-digit stripping
_RE_BD_PHONE_LOOSE = re.compile(r"(?:\+?880)?0[1-9]\d{9}", re.ASCII)


def extract_phone_candidates(text: Optional[str]) -> list[str]:
    """
    Extract all Bangladesh phone candidates from free-form text.

    Handles formatted variants that the strict `_RE_PHONE` regex cannot:
      +880 1849-258074   (space after country code, hyphen in number)
      01849-258074       (hyphen only)
      01849 258074       (space only)
      +8801849258074     (no separators — already works)

    Returns a deduplicated list of canonical 01XXXXXXXXX phones in order
    of first appearance.  Returns [] when text is None or empty.
    """
    if not text:
        return []
    # Collapse inter-digit noise so all BD formats become continuous digits
    normalized = _RE_INTERDIGIT_NOISE.sub("", str(text))
    raw_phones = _RE_BD_PHONE_LOOSE.findall(normalized)
    result: list[str] = []
    for ph in raw_phones:
        canon = normalize_bd_phone(ph)
        if canon and canon not in result:
            result.append(canon)
    return result


def normalize_phone(raw: Optional[str]) -> str:
    """
    Canonical Bangladesh phone number → 01XXXXXXXXX (11 digits).
    Returns empty string for unresolvable inputs.

    Delegates to normalize_bd_phone() — the single source of truth.
    """
    result = normalize_bd_phone(raw)
    return result if result else ""


# Make all names importable directly from shared.phone
__all__ = [
    "normalize_bd_phone",
    "normalize_phone",
    "to_880",
    "from_880",
    "phone_key9",
    "phones_match",
    "canonical_phone",
    "phone_last10",
    "extract_phone_candidates",
]
