---
title: Accountant Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Accountant Identity

## Purpose
Identify accountant role and route payment/accounting messages correctly.

## Detection Signals
- Seeded accountant phone number or backend accountant role.
- Payment handoff context.
- Cash ledger/accounting workflow context.

## Permissions
Allowed:
- Receive admin payment messages.
- View payment execution context needed for accounting.
- Confirm payment handling where system supports it.

Not allowed:
- Modify identity brain or developer prompt rules unless separately authorized.

## Business Rules
- Accountant receiving admin payment message is the payment-complete trigger.
- Ledger/database update follows accountant handoff.

## Cross References
- ../02_admin_knowledge/admin_payment_handling.md
- ../04_business_rules/cash_business_rules.md
- permission_matrix.md

## Revision History
- 2026-06-19: Created from accountant/payment completion decision.
