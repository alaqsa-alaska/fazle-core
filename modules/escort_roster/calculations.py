"""
Escort Roster — Shift & Pay Calculations

Shift counting rules:
  - Each day has 2 shifts: D (day) then N (night)
  - Counting is inclusive: (start_date, start_shift) → (end_date, end_shift)
  - Each shift = 0.5 day
  - Salary = total_shifts × shift_rate   (default 200 BDT/shift)
  - Total  = Salary + Conveyance

Examples:
  start=2026-05-13 D, end=2026-05-13 D  → 1 shift  = 0.5 day  = 200 BDT
  start=2026-05-13 D, end=2026-05-13 N  → 2 shifts = 1.0 day  = 400 BDT
  start=2026-05-13 D, end=2026-05-14 N  → 4 shifts = 2.0 days = 800 BDT
  start=2026-05-13 N, end=2026-05-18 D  → 10 shifts = 5.0 days = 2000 BDT
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional


# ── Shift constants ───────────────────────────────────────────────────────────

SHIFT_ORDER = ["D", "N"]  # Day before Night


def count_shifts(
    start_date: date,
    start_shift: str,
    end_date: date,
    end_shift: str,
) -> int:
    """Count shifts inclusively between (start_date, start_shift) and (end_date, end_shift)."""
    start_shift = start_shift.upper()[:1]
    end_shift = end_shift.upper()[:1]

    if start_shift not in SHIFT_ORDER:
        start_shift = "D"
    if end_shift not in SHIFT_ORDER:
        end_shift = "N"

    if end_date < start_date:
        return 0

    if start_date == end_date:
        # Same day: count shifts from start to end inclusive
        si = SHIFT_ORDER.index(start_shift)
        ei = SHIFT_ORDER.index(end_shift)
        if ei < si:
            return 0  # invalid (end before start on same day)
        return ei - si + 1

    total = 0
    cur_date = start_date
    cur_shift_idx = SHIFT_ORDER.index(start_shift)

    # Count remaining shifts on start_date
    total += 2 - cur_shift_idx  # from start_shift to N inclusive

    # Count full days between start and end
    cur_date += timedelta(days=1)
    while cur_date < end_date:
        total += 2  # both D and N
        cur_date += timedelta(days=1)

    # Count shifts on end_date from D to end_shift inclusive
    end_shift_idx = SHIFT_ORDER.index(end_shift)
    total += end_shift_idx + 1  # 0-indexed: D=0 → 1 shift; N=1 → 2 shifts

    return total


def shifts_to_days(total_shifts: int) -> Decimal:
    """Convert shift count to days (1 shift = 0.5 day)."""
    return Decimal(str(total_shifts)) * Decimal("0.5")


def calculate_salary(total_shifts: int, shift_rate: Decimal = Decimal("200")) -> Decimal:
    """Salary = total_shifts × shift_rate."""
    return Decimal(str(total_shifts)) * shift_rate


def calculate_total(salary: Decimal, conveyance: Decimal) -> Decimal:
    return salary + conveyance


def calculate_pay(
    start_date: date,
    start_shift: str,
    end_date: date,
    end_shift: str,
    conveyance: Decimal = Decimal("0"),
    shift_rate: Decimal = Decimal("200"),
) -> dict:
    """
    Full pay calculation. Returns dict with all columns for roster entry.
    """
    total_shifts = count_shifts(start_date, start_shift, end_date, end_shift)
    total_days = shifts_to_days(total_shifts)
    salary = calculate_salary(total_shifts, shift_rate)
    total = calculate_total(salary, conveyance)

    return {
        "total_shifts": total_shifts,
        "total_days": float(total_days),
        "salary": float(salary),
        "conveyance": float(conveyance),
        "total": float(total),
    }


# ── Date / shift parsing ──────────────────────────────────────────────────────

# Matches: 13.05.2026(D) | 13.05.2026(N) | 13.05.26(D)
_DATE_SHIFT_RE = re.compile(
    r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\s*\(\s*([DNdn])\s*\)"
)
# Matches: 13.05.2026 | 13/05/2026 | 13-05-2026
_DATE_ONLY_RE = re.compile(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")

# Task 10: Public exports for cross-module reuse
DATE_SHIFT_RE = _DATE_SHIFT_RE
DATE_ONLY_RE = _DATE_ONLY_RE
# Extended variant that also matches bare "Day"/"Night" suffix (used by history_sync)
DATE_SHIFT_FULL_RE = re.compile(
    r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})\s*"
    r"[\(\s]*([DNdn](?:ay|ight)?)\s*[\)]*"
)


def parse_date_shift(text: str) -> tuple[Optional[date], Optional[str]]:
    """
    Extract date and optional shift marker from a string fragment.
    Returns (date_obj, shift_char) where shift_char is 'D', 'N', or None.
    """
    m = _DATE_SHIFT_RE.search(text)
    if m:
        day, month, year_s, shift = m.groups()
        year = int(year_s) if len(year_s) == 4 else 2000 + int(year_s)
        try:
            return date(year, int(month), int(day)), shift.upper()
        except ValueError:
            pass

    m = _DATE_ONLY_RE.search(text)
    if m:
        day, month, year_s = m.groups()
        year = int(year_s) if len(year_s) == 4 else 2000 + int(year_s)
        try:
            return date(year, int(month), int(day)), None
        except ValueError:
            pass

    return None, None
