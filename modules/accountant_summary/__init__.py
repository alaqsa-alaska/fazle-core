"""
Fazle Core — Accountant Summary Detector & Acknowledger

Recognises and acknowledges daily Bengali accounting summary messages sent by
the accountant via WhatsApp.  These messages contain company-level cash-flow
totals — NOT individual employee records — so they cannot be written to
fpe_cash_transactions (which requires employee_id NOT NULL).

Recognised formats (examples):
    "7/5/26=জমা =75,000/-"           date + deposit + total
    "4/5/26=জমা =35,000/-"
    "7/5/26=টোটাল বাকি =51,238/-"    date + outstanding balance
    "অগ্রিম জমা থাকে =23,762/-"      advance balance (no date prefix)
    "7/5/26= অফিস ভাড়া বাবদ = 12,000/-"  date + rent label + amount
    "মোট বাকি = 1,23,456/-"          total outstanding, no date

The module intentionally does NOT write to any table; it only validates the
format and returns a confirmation reply.  Individual employee advance records
should instead use "advance দিলাম ID <N> <amount> bkash" which routes to
`nl_advance_record.intent_advance_record()`.
"""
from __future__ import annotations

import re
from typing import Optional

# ── Bengali digit normalisation ────────────────────────────────────────────────
_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# ── Patterns ───────────────────────────────────────────────────────────────────
# Date prefix  D/M/YY or D/M/YYYY (optional)
_DATE_PREFIX = r"(?:\d{1,2}/\d{1,2}/\d{2,4}\s*[=:]\s*)?"

# Bengali accounting verbs / labels
_LABELS = (
    "জমা",          # deposit / received
    "টোটাল বাকি",   # total outstanding
    "মোট বাকি",     # total outstanding (variant)
    "অগ্রিম জমা",   # advance deposit
    "অগ্রিম",       # advance balance
    "অফিস ভাড়া",   # office rent
    "ভাড়া বাবদ",   # rent expense
    "বেতন বাকি",    # salary outstanding
    "মোট জমা",      # total deposit
    "আয়",           # income
    "ব্যয়",         # expense
    "লাভ",          # profit
    "ক্ষতি",        # loss
)
_LABEL_PAT = "|".join(re.escape(l) for l in _LABELS)

# Taka amount: optional label word(s) + "= 1,23,000/-" or "= ১২৩/-" or "টাকা ৳"
_AMOUNT_PAT = r"[=:\s]*[\d,৳৳]+[\d,]*\s*(?:/-|টাকা|taka)?"

# Full summary line:  (optional date=)(optional label  =)(amount)
_SUMMARY_RE = re.compile(
    rf"^{_DATE_PREFIX}"                        # optional date prefix
    rf"(?:{_LABEL_PAT})\s*[বাবদ]*\s*"         # known accounting label
    rf"{_AMOUNT_PAT}",
    re.UNICODE,
)

# Simpler fallback: any line with "= <digits>/-" style Bangla accounting notation
_AMOUNT_LINE_RE = re.compile(
    r"=\s*[\d,]+\s*/-",
    re.UNICODE,
)


def is_accountant_summary(text: str) -> bool:
    """Return True when *text* looks like a Bengali accounting summary line."""
    if not text:
        return False
    t = text.strip().translate(_BN_DIGITS)
    # Must contain a taka-style amount marker
    if not _AMOUNT_LINE_RE.search(t):
        return False
    # Must match a known label OR have date= prefix before the amount
    if _SUMMARY_RE.search(t):
        return True
    # Fallback: "অগ্রিম জমা থাকে =23,762/-" (no date, specific keyword)
    if re.search(r"(?:থাকে|বাকি|জমা)\s*=\s*[\d,]+/-", t):
        return True
    return False


def _parse_summary(text: str) -> dict:
    """Best-effort extraction of (date_str, label, amount) from a summary line."""
    t = text.strip().translate(_BN_DIGITS)

    date_str: Optional[str] = None
    label: Optional[str] = None
    amount: Optional[float] = None

    # Extract date
    dm = re.match(r"(\d{1,2}/\d{1,2}/\d{2,4})", t)
    if dm:
        date_str = dm.group(1)

    # Extract label
    lm = re.search(rf"({_LABEL_PAT})", t)
    if lm:
        label = lm.group(1)

    # Extract amount (largest number in "12,345/-" format)
    amounts = re.findall(r"([\d,]+)/-", t)
    candidates = []
    for a in amounts:
        try:
            v = float(a.replace(",", ""))
            if v > 0:
                candidates.append(v)
        except ValueError:
            pass
    if candidates:
        amount = max(candidates)

    return {"date_str": date_str, "label": label, "amount": amount}


# ── Label translations ─────────────────────────────────────────────────────────
_LABEL_EN: dict[str, str] = {
    "জমা": "জমা (Received)",
    "টোটাল বাকি": "মোট বাকি (Total Outstanding)",
    "মোট বাকি": "মোট বাকি (Total Outstanding)",
    "অগ্রিম জমা": "অগ্রিম জমা (Advance Deposit)",
    "অগ্রিম": "অগ্রিম (Advance)",
    "অফিস ভাড়া": "অফিস ভাড়া (Office Rent)",
    "ভাড়া বাবদ": "ভাড়া (Rent)",
    "বেতন বাকি": "বেতন বাকি (Salary Due)",
    "মোট জমা": "মোট জমা (Total Received)",
    "আয়": "আয় (Income)",
    "ব্যয়": "ব্যয় (Expense)",
    "লাভ": "লাভ (Profit)",
    "ক্ষতি": "ক্ষতি (Loss)",
}


def ack_accountant_summary(text: str) -> str:
    """Return a concise Bangla acknowledgment for an accounting summary line."""
    parsed = _parse_summary(text)
    date_str = parsed["date_str"]
    label = parsed["label"]
    amount = parsed["amount"]

    label_display = _LABEL_EN.get(label, label) if label else "এন্ট্রি"
    date_display = f"তারিখ: {date_str}\n" if date_str else ""
    amount_display = f"৳{amount:,.0f}" if amount else "?"

    return (
        f"সারসংক্ষেপ পেয়েছি।\n\n"
        f"{date_display}"
        f"ধরন: {label_display}\n"
        f"পরিমাণ: {amount_display}\n\n"
        f"📝 ব্যক্তিগত কর্মীর অগ্রিম রেকর্ড করতে:\n"
        f"advance দিলাম ID <কর্মী নং> <পরিমাণ> bkash/cash"
    )
