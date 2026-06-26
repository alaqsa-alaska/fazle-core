---
title: Cash Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Cash Workflow

## Flow
Admin Payment Message -> Accountant Receipt -> Transaction Parse -> Duplicate Check -> Cash Ledger Entry -> Employee Balance Update -> Payroll/Advance Sync.

## Source Of Truth

Daily cash payment has two separate paths:

1. Employee/Escort request path: employee asks for advance/payment -> system verifies identity/duty/slip/payment method -> creates `fazle_payment_drafts` only -> waits for admin action. This path never writes a final cash transaction.
2. Admin -> Accountant instruction path: admin sends a payment instruction to accountant -> message is treated as final payment proof -> parser writes `wbom_cash_transactions` directly. No additional verification or draft is required.

Admin's message reaching the accountant is the completion trigger.

## Transaction Fields
Name, employee ID if available, phone, payment method, amount, purpose, date, source message, admin actor.

## Final Instruction Format

With explicit employee-id mobile:

```text
ID: 01795122311 Manik Mea 01789123456(B) 5000/-
```

Without explicit ID:

```text
Manik Mea 01789123456(N) 200/-
```

Parsing rules:
- `ID:` mobile is the employee lookup key (`wbom_employees.employee_mobile`).
- Without `ID:`, the payout mobile is also the employee lookup key.
- `(B)` = bkash, `(N)` = nagad, `(C)` = cash, `(Bank)` = bank.
- Missing employee is auto-created using existing `wbom_employees` columns only.
- Final ledger row is stored in `wbom_cash_transactions`; existing DB schema is not changed.

## Non-Transaction Summary Rule

Accountant company summaries such as `জমা`, `টোটাল বাকি`, and `অগ্রিম জমা থাকে` are informational. They are acknowledged but not written to `wbom_cash_transactions`, because they are not employee-level payments.

## Duplicate Rule

WhatsApp message identity/timestamp is the duplicate boundary. The same employee can receive the same amount more than once in a day if the payment instruction is a distinct message/time.

## Cross References
- ../04_business_rules/cash_business_rules.md
- payment_workflow.md
