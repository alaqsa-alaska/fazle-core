---
title: Release Slip Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Release Slip Workflow

## Purpose
Define the admin-only release slip handling workflow for escort duty completion, attendance finalization, and payment preparation.

## Scope
Level 2 admin system knowledge. Visible to Admin, Operation Officer, Supervisor, Accountant, and Management only.

## Workflow
1. Escort sends release slip image through WhatsApp after duty completion.
2. System or admin checks whether the slip contains release date, release time, and supervisor/ghat in-charge signature.
3. Data is extracted from the release slip where possible.
4. Duty days are calculated from duty start to release date/time.
5. Shift, food money, conveyance, and destination-based support are calculated.
6. Previous advance is deducted from total earned amount.
7. Final payment draft is prepared for admin handling.

## Business Rules
- Release slip confirmation is required before final escort attendance is calculated.
- Missing or unclear release date/time/signature requires manual review.
- Release slip is used to finalize duty days, food money, transport/conveyance, and advance adjustment.
- Final payment should not proceed until release slip review is complete unless management explicitly approves an exception.

## Examples
OCR/Data Review -> Duty Days -> Shift -> Food -> Conveyance -> Advance Deduction -> Payment Draft -> Admin Handling.

## Exceptions
If release slip image is unreadable, ask for a clearer photo or escalate to admin/manual review.

## AI Notes
Do not disclose OCR, validation, internal calculation, or approval details to employees.

## Cross References
- escort_workflow.md
- payment_workflow.md
- ../01_employee_knowledge/release_slip.md

## Revision History
- 2026-06-19: Created from release slip workflow source.
