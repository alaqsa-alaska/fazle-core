---
title: Accountant Summary Detector
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Accountant Summary Detector

**Source:** `modules/accountant_summary/__init__.py` (158 lines — read 2026-06-23)
**Priority:** P3
**Triggered by:** `message_router` when sender is accountant role

---

## Purpose

Detects and acknowledges company-level cash-flow summary messages sent by the accountant
via WhatsApp. These are **informational only** — the module never writes to any DB table.

**Why it cannot write to `wbom_cash_transactions`:**
That table requires `employee_id NOT NULL`. Accountant summary messages record
company-level totals (total deposits, rent, outstanding balance) — not per-employee transactions.
Individual employee advance records must use `nl_advance_record` commands instead.

---

## Recognised Message Formats

```
7/5/26=জমা =75,000/-         → date=07/05/2026, label=জমা, amount=75000
4/5/26=টোটাল বাকি =51,238/-  → date=04/05/2026, label=টোটাল বাকি, amount=51238
অগ্রিম জমা থাকে =23,762/-    → no date, label=অগ্রিম জমা, amount=23762
মোট বাকি = 1,23,456/-        → no date, label=মোট বাকি, amount=123456
7/5/26= অফিস ভাড়া বাবদ = 12,000/-  → date + rent expense
```

---

## Detection Logic

`is_accountant_summary(text) → bool`

Two conditions BOTH required:
1. Text contains `= <digits>/-` pattern (Bengali accounting notation)
2. Text matches a known label (জমা, বাকি, অগ্রিম, ভাড়া, আয়, ব্যয়, etc.) OR
   contains `থাকে/বাকি/জমা` before `= digits/-`

Bengali digits (০–৯) are normalised to ASCII before matching.

---

## Recognised Labels (13)

| Bengali | English |
|---|---|
| জমা | Received / Deposit |
| টোটাল বাকি | Total Outstanding |
| মোট বাকি | Total Outstanding |
| অগ্রিম জমা | Advance Deposit |
| অগ্রিম | Advance Balance |
| অফিস ভাড়া | Office Rent |
| ভাড়া বাবদ | Rent Expense |
| বেতন বাকি | Salary Outstanding |
| মোট জমা | Total Received |
| আয় | Income |
| ব্যয় | Expense |
| লাভ | Profit |
| ক্ষতি | Loss |

---

## API

```python
from modules.accountant_summary import is_accountant_summary, ack_accountant_summary

# Detection
if is_accountant_summary(text):
    reply = ack_accountant_summary(text)
    # reply is a formatted Bengali acknowledgment (no DB write)
```

`ack_accountant_summary(text)` reply format:
```
সারসংক্ষেপ পেয়েছি।

তারিখ: 7/5/26
ধরন: জমা (Received)
পরিমাণ: ৳75,000

📝 ব্যক্তিগত কর্মীর অগ্রিম রেকর্ড করতে:
advance দিলাম ID <কর্মী নং> <পরিমাণ> bkash/cash
```

---

## Routing Position

Accountant role detection occurs in `message_router` before LLM fallback.
If `identity_brain` resolves the sender as `accountant` role, the router
calls `is_accountant_summary()` before any other intent path.

---

## What It Does NOT Do

- Does NOT write to `wbom_cash_transactions` (no employee_id)
- Does NOT create a payment draft
- Does NOT forward to accountant queue
- Does NOT parse multi-line accounting reports (one summary line at a time)

For multi-line or structured payroll data, see `modules/fazle_payroll_engine/parser.py`.

---

## Related Modules

- `modules/admin_commands/nl_advance_record.py` — handles `advance দিলাম ID <N> <amount>`
- `modules/fazle_payroll_engine/parser.py` — structured financial message parsing
- `modules/identity_brain/` — accountant identity detection (triggers this module)
