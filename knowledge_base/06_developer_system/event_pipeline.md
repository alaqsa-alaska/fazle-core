# Event Pipeline

## Purpose
Define internal event flow across message, identity, draft, approval, payment, attendance, and payroll systems.

## Events
message.received, media.extracted, identity.resolved, draft.created, admin.reviewed, attendance.approved, escort.confirmed, release.received, payment.handed_to_accountant, ledger.updated, payroll.synced.

## Rules
Events must be idempotent, auditable, and duplicate-safe.
