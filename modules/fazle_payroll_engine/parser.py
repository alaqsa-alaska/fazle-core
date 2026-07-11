"""
Fazle Payroll Engine — Deterministic regex payment message parser.

Design:
- Pure regex, zero network calls — runs in < 1 ms per message.
- AI enhancer (app.ollama) is invoked ONLY when confidence < 0.7.
- Patterns derived from actual owner→accountant conversation exports.

Observed message patterns (from wa_conversation_01880446111_01844836824.txt):
  1. "ID: 01725494969 Jakir 01725494969(N) 2200/-"
  2. "Md. Nasir SG - +8801318182022 ( B) = 200 /-"
  3. "Jolily MAX SG 01927317829(N) 510/-"
  4. "Saidul 01958122301(cash) 1530/-"
  5. "ID: 01786178090\\nAnis- 01786178090(B) 150/-"  (multi-line)
  6. "31/3/26=টোটাল বাকি =75,468/-"                 (balance summary)
  7. "Mainuddin 01933689128(B) 1500/-"

Confidence scoring:
  1.0 — all fields present (name + phone + method + amount)
  0.85 — amount + phone present, method missing
  0.75 — amount + name present, phone missing
  0.6  — amount only (triggers AI)
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Optional

from .models import (
    EscortPaymentEntry,
    MessageType,
    ParsedBalanceSummary,
    ParsedEscortPayment,
    ParsedPayment,
    ParseResult,
    PayoutMethod,
)
from .normalizer import normalize_amount, normalize_bd_phone, normalize_payout_method

# ── Regex building blocks ─────────────────────────────────────────────────────

# BD phone: +8801XXXXXXXXX | 8801XXXXXXXXX | 01XXXXXXXXX (9–13 digits with optional prefix)
_RE_PHONE = re.compile(
    r"(?:\+?880)?0[1-9]\d{9}",
    re.ASCII,
)

# Employee ID prefix line: "ID: 01XXXXXXXXX" or "ID: +8801XXXXXXXXX"
_RE_ID_PREFIX = re.compile(
    r"^[Ii][Dd]\s*[:：]\s*(\+?8{0,2}01\d+)",
    re.MULTILINE,
)

# Payment method in parentheses: (B), (N), (cash), ( B ), (bkash), (Nagad)
_RE_METHOD = re.compile(
    r"\(\s*([BbNnCcRr]|[Bb][Kk]|[Cc]ash|[Nn]agad|[Bb][Kk]ash|[Rr]ocket)\s*\)",
)

# Amount: digits/commas followed by "/", "/-", or "/+"
# Handles "2200/-", "2,200/-", "= 200 /-", "১,৫৩০/-", "3500/", "500/+"
_RE_AMOUNT = re.compile(
    r"[=\s]*([০-৯\d][০-৯\d,\.]*)\s*/(?:[-–+])?",
)

# Balance summary patterns (Bengali):  "31/3/26=টোটাল বাকি =75,468/-"
_RE_BAL_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
_RE_BAL_DUE  = re.compile(r"বাকি\s*[=:\s]+([০-৯\d][০-৯\d,]*)\s*/[-–]")
_RE_BAL_COLL = re.compile(r"জমা\s*[=:\s]+([০-৯\d][০-৯\d,]*)\s*/[-–]")

# Name heuristic: 1-5 Bengali/Latin words, may include dots and hyphens
# Appears BEFORE the phone number in the line
_RE_NAME_BEFORE_PHONE = re.compile(
    r"^([A-Za-z\u0980-\u09FF][A-Za-z\u0980-\u09FF\s\.\-]{1,50}?)"
    r"(?=\s*[\-–]?\s*(?:\+?8{0,2}01|\())",
)

_BENGALI_DIGITS_TRANS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Phase 3 — strip spaces/hyphens that fall BETWEEN digits so that formatted
# phone numbers like "+880 1849-258074" or "01849-258074" are matched by
# _RE_PHONE without changing any regex.  Applied only to the phone-extraction
# pass inside _parse_payment(); the original text is preserved everywhere else.
_RE_INTERDIGIT_NOISE = re.compile(r"(?<=\d)[\s\-](?=\d)")

# ── Cash / Income command patterns ────────────────────────────────────────────

# Matches: "Cash 01XXXXXXXXX ..." or "Income 01XXXXXXXXX ..."
_RE_CASH_CMD   = re.compile(r"^[Cc]ash\s+", re.MULTILINE)
_RE_INCOME_CMD = re.compile(r"^[Ii]ncome\s+", re.MULTILINE)

# Flexible amount at end of Cash/Income command:
#   5000 | 5000/ | 5000/- | 5,000.00 | -5000 | =5,000.00 | 5000.00/-
# Captures only the numeric part; sign/punctuation is stripped later.
_RE_CMD_AMOUNT = re.compile(
    r"""(?:[-=+]\s*)?          # optional leading sign or =
        ([0-9][0-9,\.]*?)      # the number (lazy)
        \s*[-/]{0,2}\s*$       # optional trailing /- or / or -
    """,
    re.VERBOSE,
)

PARSER_VERSION = "v1"

# ── Escort payment patterns ───────────────────────────────────────────────────

# Matches lines like: "Zakir=150/" | "Babul molla =300/+" | "S,Baharul=200/"
# Name: any non-newline chars before "="; Amount: digits after "=", ends with "/"
_RE_ESCORT_LINE = re.compile(
    r"^([A-Za-z\u0980-\u09FF][^=\n]{0,40}?)\s*=\s*(\d[\d,]*)\s*/",
    re.MULTILINE,
)

# Matches "Totall=" or "Total=" (case-insensitive) — used to skip total lines
_RE_ESCORT_TOTAL = re.compile(r"(?i)^\s*tota[l]{1,2}\s*=", re.MULTILINE)

# Shift indicator in escort messages: "Night shift", "Day shift", "(D)", "(N)"
_RE_SHIFT_WORD = re.compile(r"\b(Night|Day)\s+[Ss]hif[ty]\b", re.IGNORECASE)
_RE_SHIFT_PAREN = re.compile(r"\(\s*([DNdn])\s*\)")

# Date in escort messages: DD/MM/YY or DD/MM/YYYY or DD.MM.YY
_RE_ESCORT_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})")


def _is_escort_payment(text: str) -> bool:
    """Return True if the message contains 2+ NAME=AMOUNT/ lines (escort format)."""
    lines = _RE_ESCORT_LINE.findall(text)
    # Filter out "Total" lines; need at least 2 real employee entries
    real_entries = [(n, a) for n, a in lines if not _RE_ESCORT_TOTAL.match(n + "=")]
    return len(real_entries) >= 2


def _parse_escort_payment(text: str, msg_date: Optional[date]) -> ParseResult:
    """
    Parse multi-employee escort duty payment message.

    Format:
        Zakir=150/
        Babul molla=300/
        Totall=965/       <- total line, skipped
        04/5/26 Night shify
        S,Baha
    """
    all_entries = _RE_ESCORT_LINE.findall(text)
    entries: list[EscortPaymentEntry] = []
    for raw_name, raw_amt in all_entries:
        raw_name = raw_name.strip().strip(",")
        # Skip total lines
        if re.match(r"(?i)tota[l]{1,2}", raw_name):
            continue
        amt = normalize_amount(raw_amt)
        if amt is None or amt <= 0:
            continue
        entries.append(EscortPaymentEntry(name_raw=raw_name, amount=Decimal(str(amt))))

    if not entries:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    total = sum(e.amount for e in entries)

    # Detect shift
    shift: Optional[str] = None
    m_shift_word = _RE_SHIFT_WORD.search(text)
    if m_shift_word:
        shift = "D" if m_shift_word.group(1).upper() == "DAY" else "N"
    else:
        m_shift_paren = _RE_SHIFT_PAREN.search(text)
        if m_shift_paren:
            shift = m_shift_paren.group(1).upper()

    # Detect duty date
    duty_date: Optional[date] = msg_date
    m_date = _RE_ESCORT_DATE.search(text)
    if m_date:
        d, mo, yr = m_date.groups()
        year = int(yr) if len(yr) == 4 else 2000 + int(yr)
        try:
            duty_date = date(year, int(mo), int(d))
        except ValueError:
            pass

    ep = ParsedEscortPayment(
        entries=entries,
        shift=shift,
        duty_date=duty_date,
        total_amount=total,
        raw_text=text,
    )
    return ParseResult(
        message_type=MessageType.escort_payment,
        escort_payment=ep,
        confidence=0.95,
    )




def parse_message(text: Optional[str], msg_date: Optional[date] = None) -> ParseResult:
    """
    Parse a single WhatsApp message text.
    Returns a ParseResult with .message_type set.
    """
    if not text or not text.strip():
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    t = text.strip()

    # ── Cash command: "Cash 01XXXXXXXXX Name Amount" ───────────────────────────
    if _RE_CASH_CMD.match(t):
        return _parse_cash_or_income_command(t, msg_date, MessageType.cash_command)

    # ── Income command: "Income 01XXXXXXXXX Name Amount" ──────────────────────
    if _RE_INCOME_CMD.match(t):
        return _parse_cash_or_income_command(t, msg_date, MessageType.income_command)

    # ── Balance summary detection (check first — short-circuits) ──────────────
    if _is_balance_summary(t):
        return _parse_balance_summary(t)

    # ── Escort multi-employee payment (NAME=AMOUNT/ per line) ──────────────────
    if _is_escort_payment(t):
        return _parse_escort_payment(t, msg_date)

    # ── Payment detection ──────────────────────────────────────────────────────
    result = _parse_payment(t, msg_date)
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_cash_or_income_command(text: str, msg_date: Optional[date], mtype: MessageType) -> ParseResult:
    """
    Parse a command-style message:
        Cash   01XXXXXXXXX Employee Name Amount
        Income 01XXXXXXXXX Employee Name Amount

    Token structure (space-separated, first line only):
        tokens[0]  = "Cash" / "Income"  (already confirmed by caller)
        tokens[1]  = phone number
        tokens[-1] = amount (with optional trailing /-)
        tokens[2:-1] = employee name words (may be empty for unnamed income)

    Returns ParseResult with message_type = mtype.
    Uses message_type=MessageType.other on unrecoverable parse failure.
    """
    # Use only the first non-empty line (commands are always single-line)
    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)

    tokens = first_line.split()
    # Need at least: keyword + phone + amount  (3 tokens)
    if len(tokens) < 3:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    # tokens[0] is the keyword; skip it
    phone_raw = tokens[1]
    amount_raw = tokens[-1]
    name_tokens = tokens[2:-1]  # may be empty list

    # ── Phone ─────────────────────────────────────────────────────────────────
    phone = normalize_bd_phone(phone_raw)
    if phone is None:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    # ── Amount ────────────────────────────────────────────────────────────────
    m_amt = _RE_CMD_AMOUNT.search(amount_raw)
    if not m_amt:
        return ParseResult(message_type=MessageType.other, confidence=1.0)
    amount = normalize_amount(m_amt.group(1))
    if amount is None or amount <= 0:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    # ── Name ──────────────────────────────────────────────────────────────────
    name_raw: Optional[str] = " ".join(name_tokens).strip() if name_tokens else None
    if name_raw and not _is_valid_human_name(name_raw):
        name_raw = None

    p = ParsedPayment(
        raw_text=text,
        payout_phone=phone,
        employee_id_phone=phone,         # same phone doubles as employee ID
        employee_name_raw=name_raw,
        payout_method=PayoutMethod.cash,  # Cash/Income commands are always cash
        amount=Decimal(str(amount)),
        txn_date=msg_date,
        confidence=1.0,
    )

    return ParseResult(
        message_type=mtype,
        payment=p,
        confidence=1.0,
    )


def _is_balance_summary(text: str) -> bool:
    """Heuristic: contains Bengali accounting keywords."""
    return bool(
        re.search(r"টোটাল|বাকি|জমা", text)
        and _RE_AMOUNT.search(text)
    )


def _parse_balance_summary(text: str) -> ParseResult:
    bs = ParsedBalanceSummary(raw_text=text)

    m_date = _RE_BAL_DATE.search(text)
    if m_date:
        day, month, year = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
        if year < 100:
            year += 2000
        try:
            bs.summary_date = date(year, month, day)
        except ValueError:
            pass

    m_due = _RE_BAL_DUE.search(text)
    if m_due:
        v = normalize_amount(m_due.group(1).translate(_BENGALI_DIGITS_TRANS))
        if v is not None:
            bs.total_due = Decimal(str(v))

    m_coll = _RE_BAL_COLL.search(text)
    if m_coll:
        v = normalize_amount(m_coll.group(1).translate(_BENGALI_DIGITS_TRANS))
        if v is not None:
            bs.total_collected = Decimal(str(v))

    return ParseResult(
        message_type=MessageType.balance_summary,
        balance_summary=bs,
        confidence=1.0,
    )


def _parse_payment(text: str, msg_date: Optional[date]) -> ParseResult:
    """
    Try to extract payment fields from message text.
    Returns ParseResult with message_type='payment' on success,
    or message_type='other' if no amount found at all.
    """
    # ── Amount (required) ──────────────────────────────────────────────────────
    t_ascii = text.translate(_BENGALI_DIGITS_TRANS)
    m_amt = _RE_AMOUNT.search(t_ascii)
    if not m_amt:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    raw_amount_str = m_amt.group(1)
    amount = normalize_amount(raw_amount_str)
    if amount is None or amount <= 0:
        return ParseResult(message_type=MessageType.other, confidence=1.0)

    p = ParsedPayment(
        raw_text=text,
        amount=Decimal(str(amount)),
        txn_date=msg_date,
    )
    confidence = 0.6  # baseline: amount only

    # ── Employee ID prefix ─────────────────────────────────────────────────────
    id_match = _RE_ID_PREFIX.search(text)
    if id_match:
        p.employee_id_phone = normalize_bd_phone(id_match.group(1))

    # ── Payout phone ──────────────────────────────────────────────────────────
    # Phase 3: strip inter-digit spaces/hyphens BEFORE applying _RE_PHONE so
    # formatted numbers like "+880 1849-258074" are correctly extracted.
    # t_ascii is only used for phone finding; the original text is unchanged.
    t_for_phones = _RE_INTERDIGIT_NOISE.sub("", t_ascii)
    phones = _RE_PHONE.findall(t_for_phones)
    # The payout phone is typically the last phone before the method tag or amount,
    # and is NOT the employee ID phone
    resolved_phones = [normalize_bd_phone(ph) for ph in phones]
    resolved_phones = [ph for ph in resolved_phones if ph is not None]

    # Prefer payout phone = phone that appears adjacent to method tag
    # If there is exactly one phone: that is the payout phone
    if len(resolved_phones) == 1:
        p.payout_phone = resolved_phones[0]
        confidence = max(confidence, 0.75)
    elif len(resolved_phones) >= 2:
        # Multiple phones: last one is typically the payout phone
        p.payout_phone = resolved_phones[-1]
        confidence = max(confidence, 0.75)

    # ── Payout method ─────────────────────────────────────────────────────────
    m_method = _RE_METHOD.search(text)
    if m_method:
        raw_method = m_method.group(1)
        p.payout_method = PayoutMethod(normalize_payout_method(raw_method))
        if p.payout_phone:
            confidence = max(confidence, 0.9)
        else:
            confidence = max(confidence, 0.75)
    else:
        p.payout_method = PayoutMethod.unknown

    # Phase 3 — FALLBACK: when no explicit "ID: XXXXXXXXX" prefix was found,
    # the payout phone IS the employee identity in this company.
    # Setting employee_id_phone here enables the strict phone-first match path
    # in employee.py, preventing fuzzy-name-only auto-create.
    if p.payout_phone and not p.employee_id_phone:
        p.employee_id_phone = p.payout_phone

    # ── Employee name ─────────────────────────────────────────────────────────
    name = _extract_name(text, id_match)
    if name:
        p.employee_name_raw = name
        if p.payout_phone and p.payout_method != PayoutMethod.unknown:
            confidence = 1.0
        elif p.payout_phone:
            confidence = max(confidence, 0.85)
        else:
            confidence = max(confidence, 0.75)

    p.confidence = confidence

    return ParseResult(
        message_type=MessageType.payment,
        payment=p,
        confidence=confidence,
    )


def _is_valid_human_name(name: Optional[str]) -> bool:
    """Reject empty, pure-numeric, phone-like, or placeholder names."""
    if not name:
        return False
    s = name.strip()
    if len(s) < 2:
        return False
    if s.lower() in {"unknown", "unnamed", "none", "n/a", "na"}:
        return False
    # Pure digits / phone-shaped strings are not human names
    digits_only = re.sub(r"[\s\-\+\(\)\.]", "", s)
    if digits_only.isdigit():
        return False
    blocked_prefixes = (
        "প্রেরক",
        "sender",
        "from",
        "approve",
        "reject",
        "attendance",
        "draft",
    )
    lowered = s.lower().rstrip(":：")
    if lowered in blocked_prefixes:
        return False
    # Must contain at least one alphabetic char (Latin or Bengali)
    if not re.search(r"[A-Za-z\u0980-\u09FF]", s):
        return False
    return True


def _extract_name(text: str, id_match: Optional[re.Match]) -> Optional[str]:
    """
    Extract employee name from message.
    Strategy:
    1. If ID prefix present, STRIP it (don't drop the line) so single-line
       "ID: 019... Max Day 017...(B) 1070/-" still yields "Max Day".
    2. Try to find the name before the phone number.
    """
    lines = text.strip().splitlines()

    # Strip the ID-prefix span from whichever line contains it (if any),
    # but KEEP the rest of that line for name extraction.
    working_lines = []
    id_span_text = id_match.group(0) if id_match else None
    id_stripped_line: Optional[str] = None
    for l in lines:
        if id_span_text and id_span_text in l:
            l = l.replace(id_span_text, "", 1)
            id_stripped_line = l
        working_lines.append(l)

    # Search each remaining line for name before phone
    for line in working_lines:
        line = line.strip()
        # Skip very short lines or lines that are just phones
        if not line or len(line) < 3:
            continue

        # Try name-before-phone pattern
        m = _RE_NAME_BEFORE_PHONE.match(line)
        if m:
            name = m.group(1).strip().rstrip("-– ")
            if _is_valid_human_name(name):
                return name

        # Fallback: if line has a phone, take everything before the phone
        phone_match = re.search(r"(?:\+?8{0,2}01\d{9})", line)
        if phone_match:
            candidate = line[:phone_match.start()].strip().rstrip("-– ")
            # Remove residual "ID: XXXXXXXXX" prefix if any
            candidate = re.sub(r"^[Ii][Dd]\s*[:：]\s*\S+\s*", "", candidate).strip()
            if _is_valid_human_name(candidate):
                return candidate

    # Fallback: "ID:<phone> <Name>" written together on one line, with the
    # payout phone/method/amount on a following line instead of the same one
    # (e.g. "ID:01805668850 Junaid Islam Shami\n01773055387(B) 500/-").
    # Once the ID phone is stripped, nothing else on that line is a phone —
    # so whatever text remains, if it has no digits at all, is the name.
    if id_stripped_line is not None:
        candidate = id_stripped_line.strip().rstrip("-– ")
        if candidate and not re.search(r"\d", candidate) and _is_valid_human_name(candidate):
            return candidate

    return None
