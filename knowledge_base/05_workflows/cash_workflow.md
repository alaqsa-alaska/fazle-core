---
title: Cash Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-08-01
runtime_index: true
---

# Cash Workflow

## Flow
Admin Payment Message -> Accountant Receipt -> Transaction Parse -> Duplicate Check -> Cash Ledger Entry -> Employee Balance Update -> Payroll/Advance Sync.

## Source Of Truth

Daily cash payment has two separate paths:

1. Employee/Escort request path: employee asks for advance/payment -> system verifies identity/duty/slip/payment method -> creates `fazle_payment_drafts` only -> waits for admin action. This path never writes a final cash transaction.
2. Admin -> Accountant instruction path: admin sends a payment instruction to accountant -> message is treated as final payment proof -> parser writes `fpe_cash_transactions` directly via `create_transaction()`. No additional verification or draft is required.

Admin's message reaching the accountant is the completion trigger.

## Transaction Fields
Name, employee ID if available, phone, payment method, amount, purpose, date, source message, admin actor.

## Final Instruction Format

With explicit employee-id mobile:

```text
ID: 01795122311 Manik Mea 01789123456(B) 5000/-
```

Without explicit ID (name has no registered numeric ID — `ID:` is followed
directly by the name, not a phone):

```text
ID: Runo 01773558211(N) 800/-
```

Without any `ID:` prefix at all:

```text
Manik Mea 01789123456(N) 200/-
```

Name, phone, and method may appear in any order, and the message may span
multiple lines. All of the following parse identically (name=`Runo`,
phone=`01773558211`, method=nagad, amount=800):

```text
ID: Runo 01773558211(N) 800/-
Runo 01773558211 Nagad 800 taka
[ID: 01773558211] Runo (Bkash) 800/-
01773558211 Runo 800/- নগদ
Runo
01773558211
800 টাকা নগদ
```

Parsing rules (`modules/fazle_payroll_engine/parser.py::_extract_name`,
fixed 2026-08-01 — see incident note below):
- `ID:` mobile is the employee lookup key (`wbom_employees.employee_mobile`) — only when the token right after `ID:` is itself phone-shaped. When it isn't (e.g. `ID: Runo`), that token is the name, never the lookup key, and is never deleted during parsing.
- Without `ID:`, the payout mobile is also the employee lookup key.
- `(B)` = bkash, `(N)` = nagad, `(C)` = cash, `(R)` = rocket, `(BANK)` = bank. Full-word variants (`Cash`, `Nagad`, `Bkash`/`Bikash`, `Rocket`, `Bank`) are also accepted on input, case-insensitive, since 2026-07-13. Bengali words (`নগদ`, `বিকাশ`, `ক্যাশ`, `রকেট`) and a bare (non-parenthesized) method word anywhere in the message are also accepted since 2026-08-01. Outbound system-generated messages always use the single-letter/`BANK` codes.
- Amount accepts `/-`, `/`, `/+`, or a bare `taka`/`tk`/`টাকা` word suffix (no slash) since 2026-08-01, e.g. `800 taka`, `৮০০ টাকা`.
- Missing employee is auto-created using existing `wbom_employees` columns only.
- Final ledger row is stored in `fpe_cash_transactions` via `create_transaction()`; existing DB schema is not changed.

**2026-08-01 incident note:** a message shaped like `ID: <Name> <phone>(method) amount/-` (no numeric ID — the sender always referred to this contact by name only) was silently diverted to the review queue instead of the ledger. The old name-extraction logic stripped `ID:` plus whatever single token followed it, assuming that token was always the numeric ID, deleting the name along with the label whenever it wasn't. Confirmed against 4 real prior transactions for the same contact, all with the identical shape. Fixed by only consuming the post-`ID:` token when it is phone-shaped.

## Missing-Name Handling And The Review Queue

`employee_name_raw` is never required for a message to enter the review
queue (`fpe_unmatched_messages`) — only `amount` + `payout_phone` +
`payout_method` are (payment_business_rules.md: mobile-based lookup is the
authoritative employee identity). But `employee_name_raw` **is** required
for `validate_for_accounting()` to accept a `payment`-type message straight
into `fpe_cash_transactions` (`modules/fazle_payroll_engine/validation.py`,
`MESSAGE_RULES['payment'].required_fields`) — a name-extraction miss on a
message that genuinely doesn't mention a name (e.g. `01773558211(N) 800/-`)
correctly routes to the review queue rather than fabricating a name or
silently dropping the amount.

**Since 2026-08-01**, any review-queue row created with a detected amount
(i.e. it looked like a real cash instruction, not routine chit-chat/balance-
summary noise) triggers an immediate WhatsApp push to every configured
`ADMIN_NUMBERS` entry (`modules/fazle_payroll_engine/ingestion.py::store_unmatched`
→ `_notify_admin_unmatched`, purpose `fpe-unmatched-review`) — previously
these rows were only discoverable by proactively checking the Payroll →
Unmatched tab or the `DRAFTS`/`STATUS` WhatsApp command, so a message could
sit unreviewed until its 24h TTL expired with nobody ever having been told
it existed.

## Non-Transaction Summary Rule

Accountant company summaries such as `জমা`, `টোটাল বাকি`, and `অগ্রিম জমা থাকে` are informational. They are acknowledged but not written to `fpe_cash_transactions`, because they are not employee-level payments.

## Duplicate Rule

WhatsApp message identity/timestamp is the duplicate boundary. The same employee can receive the same amount more than once in a day if the payment instruction is a distinct message/time.

## Cross References
- ../04_business_rules/cash_business_rules.md
- payment_workflow.md
