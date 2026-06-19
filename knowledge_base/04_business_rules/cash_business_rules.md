# Cash Business Rules

## Purpose
Define cash ledger behavior and accountant handoff rules.

## Rules
- Accountant handoff from admin payment message is payment completion trigger.
- Each payment message becomes a transaction record.
- Multiple payment transactions may exist in one admin message.
- Ledger update must preserve transaction amount, method, employee/name, phone, purpose, and date.
- Duplicate prevention must run before final ledger update.

## Payment Methods
- bKash.
- Nagad.
- Cash.
- Conveyance marker may be included as conv.

## Cross References
- payment_business_rules.md
- ../05_workflows/cash_workflow.md
