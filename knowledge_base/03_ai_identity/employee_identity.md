---
title: Employee Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Employee Identity

## Purpose
Identify active or previous employees and route to employee-safe attendance, salary, advance, duty, leave, or FAQ answers.

## Detection Signals
- Registered employee phone match.
- Payroll/mobile match.
- Escort roster mobile match.
- Message asks about salary, advance, duty, payment, release, attendance, absence, or leave.

## Verification Rules
- Employee-specific requests should come from the registered number.
- Alternate number claims require verification draft/manual review.
- Payment-related requests require payment method confirmation every time.
- Duty selfie, duty slip, or release slip may be required.

## Permissions
Allowed:
- Employee policies.
- Own duty/payment request guidance.
- Safe salary, attendance, leave, release slip, and transport answers.

Not allowed:
- Internal DB, approval, OCR, ledger, or workflow engine details.

## Cross References
- permission_matrix.md
- ../04_business_rules/payment_business_rules.md
- ../01_employee_knowledge/faq_employee.md

## Revision History
- 2026-06-19: Created from employee identity and verification rules.
