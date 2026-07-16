---
title: Escort Roster System
owner: Fazle Core Admin
status: active
last_verified: 2026-07-03
runtime_index: true
---

# Escort Roster System

## System Purpose

The escort roster module is the projection layer for escort operations.

Source table:
- `wbom_escort_programs`

Projection table:
- `escort_roster_entries`

The roster page, payroll view, and reports read the projection, but the authoritative assignment truth comes from the source program after bridge confirmation reconciliation.

## Salary Calculation — Roster vs Payment Draft (C-01 Resolution)

Roster `total` and payment draft `gross_amount` serve different purposes and may
differ. Both are correct for their respective context:

- **Roster `total`** = `shift_rate × total_shifts + conveyance`
  - `shift_rate` comes from `escort_calculation_config` (default 200 BDT/shift)
  - This is the **operational display** — what was worked

- **Payment draft `gross_amount`** = `basic_salary / 30 × duty_days`
  - `basic_salary` comes from the employee's HR profile (`wbom_employees.basic_salary`)
  - This is the **settlement amount** — based on HR pay grade

When `basic_salary = shift_rate × 2 × 30` (e.g. 200 × 2 × 30 = 12,000), both
calculations produce the same daily rate (400 BDT/day). They diverge when an
escort's contract salary differs from the standard rate.

**Net payable** = `total - food_bill - advance_deduction` (stored in
`escort_roster_entries.net_payable`, populated on sync when status is completed).

## Source Of Truth And Projection Rules

Source of truth:
- `bridge -> escort client` confirmation message parsed into `wbom_escort_programs`

Not source of truth:
- client message draft
- parser guess alone
- stale roster row

Projection rule:
- roster rows must mirror the final source program state
- confirmed source program must not leave roster status as `draft`

Current mapping:

| Program Status | Roster Status |
|---|---|
| `draft` | `draft` |
| `confirmed` | `confirmed` |
| `assigned` | `active` |
| `running` | `active` |
| `completed` | `completed` |
| `cancelled` | `cancelled` |
| `expired` | `expired` — draft that missed the 24h confirmation TTL. Should be transient: `expire_stale_drafts()` hard-deletes `status='draft'` rows past TTL directly (added 2026-07-05), so a row is not expected to sit in this state for long. A defensive second sweep (added 2026-07-16, Session 16) also deletes any financially-inert legacy `expired` row past TTL, in case a future edge case produces one instead of a clean delete. |

## Sync And Recalculate Behavior

`sync_program_to_roster(program_id)`:
- loads the source program
- maps operational status to roster status
- recalculates pay when full dates exist
- updates or inserts the roster row without duplication

`recalculate_entry(program_id)`:
- first tries source-program sync
- if no source program exists, falls back to the standalone roster row
- recalculates from the roster row's own dates when possible
- otherwise performs a safe no-op update instead of returning `404`

This fallback is required because the UI can create manual roster rows that do not originate from `wbom_escort_programs`.

## Search And Export API Rules

Endpoints:
- `GET /api/escort-roster`
- `GET /api/escort-roster/export`

Rule:
- all joined search columns must be fully qualified with aliases

Qualified fields:
- `e.mother_vessel`
- `e.lighter_vessel`
- `e.master_mobile`
- `e.escort_name`
- `e.escort_mobile`
- `e.destination`
- `COALESCE(e.start_date, p.program_date)`

Reason:
- both `wbom_escort_programs` and `escort_roster_entries` may expose overlapping column names
- unqualified search columns can raise `AmbiguousColumnError`

## Browser/API Runtime Rules

Roster UI expectations:
- search only sends non-empty query params
- invalid API responses surface real error text
- optional missing values render as `—`
- CSV export checks `response.ok` before creating a blob download

Escort Client Management API:
- `GET /api/escort-roster/escort-clients`
- `POST /api/escort-roster/escort-clients`
- `DELETE /api/escort-roster/escort-clients/{phone}`

Important route rule:
- `/escort-clients` routes must be registered before `/{program_id}`
- otherwise FastAPI may treat `escort-clients` as an integer path parameter and return `422`

## Escort Client Configuration

Storage:
- `.env`
- key: `ESCORT_CLIENT_PHONES`

Runtime behavior:
- add/remove writes the repo `.env`
- `update_repo_env_value()` clears the cached settings object
- no restart is required to make the whitelist visible to runtime route logic

Validation:
- numbers are normalized to Bangladesh format
- invalid numbers return `400`
- duplicates are not re-added

## Live Verification Performed

Verified on 2026-06-27:

### Backend
- draft creation passed
- invalid client intake ignored
- bridge confirmation updated same row
- corrupted draft lighter vessel was overwritten
- `Rupshi` normalized to `Narayanganj`
- roster projection moved from draft conflict to `confirmed`
- roster list search returned `200`
- roster export search returned `200`

### Browser
- `/escort-roster` sign-in passed
- searched `TEST_*` row rendered
- view drawer opened
- edit modal opened
- recalculate button completed without crash
- Config tab rendered
- Escort Client Management rendered
- CSV export succeeded from the UI
- no roster `422` or `500` remained during normal operations

## Failures Resolved In This Verification

### 422

Failing request:
- `GET /api/escort-roster/escort-clients`

Resolved by:
- ensuring escort-client routes exist and are registered before `/{program_id}`
- restarting the live service so it loaded the current router code

### 500

Failing request:
- `GET /api/escort-roster?page=1&page_size=50&sort_by=start_date&sort_dir=desc&search=TEST_*`

Resolved by:
- qualifying ambiguous joined search columns with table aliases in roster list/export queries
- restarting the live service so the fixed query path was active

### 404 Recalculate Edge

Failing request after the main fix:
- `POST /api/escort-roster/{program_id}/recalculate` for a manual roster-only row

Resolved by:
- adding standalone-row fallback logic in `recalculate_entry()`
- allowing safe recompute or no-op update when no source program exists

## Operational Limitation

Historical archived KB reports may still mention older `ESCORTCONFIRM`-centric confirmation language. Those reports are historical snapshots, not active production authority. Active production authority is now the updated escort business rules, workflow, parser, and roster-system articles dated 2026-06-27.
