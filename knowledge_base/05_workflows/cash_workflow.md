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

## Transaction Fields
Name, employee ID if available, phone, payment method, amount, purpose, date, source message, admin actor.

## Cross References
- ../04_business_rules/cash_business_rules.md
- payment_workflow.md
