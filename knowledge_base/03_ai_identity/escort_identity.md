---
title: Escort Identity
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Escort Identity

## Purpose
Identify escort workers and route their messages to duty slip, release slip, attendance, advance, conveyance, and final payment workflows.

## Detection Signals
- Employee database or escort roster mobile match.
- Mentions vessel, duty slip, release slip, master, ship, lighter, mother vessel, destination, food money, conveyance, or final bill.

## Permissions
Allowed:
- Escort attendance rules.
- Release slip submission guidance.
- Conveyance and payment-safe guidance.
- Advance request process.

Not allowed:
- OCR internals.
- Escort table/database names.
- Admin approval and ledger update details.

## Business Rules
- Escort training is 45 days and paid ৳10,000-৳15,000/month.
- Escort duty day is 24 hours, day plus night.
- Final payment requires release slip review.

## Cross References
- ../04_business_rules/escort_business_rules.md
- ../05_workflows/escort_workflow.md
- ../01_employee_knowledge/release_slip.md

## Revision History
- 2026-06-19: Created from escort identity and resolved policy rules.
