# Escort Lifecycle Module

Handles the duty close-out phase of escort operations: employee release,
payment draft creation, and program status transitions.

## Flow

```
Employee sends release slip (photo or text)
  → is_release_intent() detects release
  → find_active_program_for_employee() finds the open escort program
  → close_program() marks status = 'Closed', records end date/shift
  → backfill_attendance_for_program() fills wbom_attendance
  → create_escort_payment_draft() → fazle_payment_drafts (status='pending')
  → admin receives payment draft for approval
```

## Key Functions

| Function | Purpose |
|----------|---------|
| `is_release_intent(text)` | Detect release/close keywords (Bengali + English) |
| `find_active_program_for_employee(emp_id, date)` | Find open program for an employee |
| `find_existing_draft_for_program(program_id)` | Check if payment draft already created |
| `close_program(program_id, ...)` | Idempotent program close with end date/shift |
| `backfill_attendance_for_program(program_id)` | Fills `wbom_attendance` from program dates |

## Dedup Safety

`find_existing_draft_for_program()` must be called before
`create_escort_payment_draft()` to prevent duplicate payment drafts for the
same program.  The `shared.draft.find_existing_escort_draft()` helper also
provides this check from outside the module.

## 2026-07-05 Integration Note

- Upstream escort confirmation now enforces exact confirmed-key dedupe
  (`date + mother + lighter + escort`) before lifecycle close-out steps.

## DB Tables

- `wbom_escort_programs` — escort duty records
- `wbom_attendance` — daily attendance derived from duty periods
- `fazle_payment_drafts` — payment drafts awaiting admin approval
