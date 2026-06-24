---
title: Admin Payment Handling
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Admin Payment Handling

## Purpose
Define how admin handles payment requests, employee advances, escort final bills, and accountant handoff.

## Scope
Level 2 admin knowledge. Not visible to employees or candidates.

## Workflow
1. Employee or escort requests money.
2. AI/operations verifies registered number, duty location, duty selfie, duty slip, or release slip as needed.
3. Payment method and number are confirmed every time.
4. Payment draft is prepared or admin manually prepares payment message.
5. Admin sends payment message manually to accountant WhatsApp.
6. When accountant receives admin message, payment is considered complete.
7. Ledger/database updates immediately from this completion state.

## Business Rules
- Current source of truth: admin manually sends all payment messages to accountant.
- Future high-level option: admin sends approval to the system; system sends message to accountant.
- In both modes, accountant handoff is the completion trigger.
- Advance range ৳500-৳1,000 is a normal guideline, not a hard cap.
- Duplicate prevention is mandatory before final ledger update.

## Cross References
- ../04_business_rules/payment_business_rules.md
- ../04_business_rules/cash_business_rules.md
- ../05_workflows/payment_workflow.md

## Revision History
- 2026-06-19: Created from resolved payment conflict.
