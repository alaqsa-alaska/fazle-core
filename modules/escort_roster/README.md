# Escort Roster Module

## Overview

The Escort Roster module is the **projection layer** for vessel-escort operations
in the Fazle Core platform. It manages the complete lifecycle of escort security
duties for lighter vessels transporting cargo from mother vessels to destination
ports in Bangladesh.

## Architecture

```
wbom_escort_programs (source of truth)
    ↓ sync_program_to_roster()
escort_roster_entries (projection/cache)
    ↓ API + UI
escort-roster.html (frontend SPA)
```

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Public API exports |
| `routes.py` | FastAPI router — 30 endpoints under `/api/escort-roster/` |
| `db.py` | All database operations (sync, list, detail, update, slip queue, summary) |
| `calculations.py` | Shift counting and pay calculation (single source for `DATE_SHIFT_RE`) |
| `extractor.py` | AI extraction engine for slip matching (imports `DEST_ALIASES` from `modules.escort`) |
| `history_sync.py` | WhatsApp conversation file parser for historical import |

## Key Functions

### Sync
- `sync_program_to_roster(program_id)` — Upsert a program into roster entries (auto-called on save/confirm)
- `sync_all_programs()` — Bulk sync all programs
- `recalculate_entry(program_id)` — Recompute pay fields

### Queries
- `get_roster_list(...)` — Paginated, searchable, filterable list (case-insensitive status filter)
- `get_roster_detail(program_id)` — Full detail with slip matches, audit log, shift logs
- `get_roster_summary()` — Aggregate stats including `net_payable_total` and `pending_slip_reviews`

### Slip Queue
- `get_slip_queue(page, page_size)` — Pending OCR extractions with match candidates
- `confirm_slip_match(match_id)` — Admin confirms a match (`admin_action='keep'`)
- `reject_slip_match(match_id)` — Admin rejects a match (`admin_action='reject'`)

### Shift Logs
- `_write_shift_log(...)` — Called automatically on `sync_program_to_roster()` when status transitions to `active` or `completed`

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/escort-roster/summary` | Aggregate stats |
| GET | `/api/escort-roster` | Paginated list |
| POST | `/api/escort-roster` | Manual create (with phone validation) |
| GET | `/api/escort-roster/{id}` | Detail with timeline |
| PATCH | `/api/escort-roster/{id}` | Inline edit (with phone validation) |
| DELETE | `/api/escort-roster/{id}` | Delete draft only |
| POST | `/api/escort-roster/{id}/recalculate` | Recompute pay |
| POST | `/api/escort-roster/{id}/sync` | Sync single program |
| POST | `/api/escort-roster/sync-all` | Bulk sync all |
| GET | `/api/escort-roster/slip-queue` | Pending OCR slip reviews |
| POST | `/api/escort-roster/slip-queue/{match_id}/confirm` | Confirm slip match |
| POST | `/api/escort-roster/slip-queue/{match_id}/reject` | Reject slip match |
| GET | `/api/escort-roster/config` | Conveyance config |
| POST | `/api/escort-roster/config` | Upsert config |
| GET | `/api/escort-roster/escort-clients` | List client phones |
| POST | `/api/escort-roster/escort-clients` | Add client phone |
| DELETE | `/api/escort-roster/escort-clients/{phone}` | Remove client phone |
| GET | `/api/escort-roster/export` | CSV export |
| GET | `/api/escort-roster/drafts` | List drafts |
| POST | `/api/escort-roster/cleanup-drafts` | Delete all drafts |
| POST | `/api/escort-roster/cleanup-empty-drafts` | Delete empty drafts |
| POST | `/api/escort-roster/cleanup-junk-drafts` | Delete junk drafts |
| GET | `/api/escort-roster/audit-exact-duplicates` | Audit exact confirmed duplicates by date+mother+lighter+escort |
| POST | `/api/escort-roster/cleanup-exact-duplicates` | Remove exact confirmed duplicates (keeps earliest `program_id`) |
| POST | `/api/escort-roster/reconcile` | Bulk reconciliation |
| POST | `/api/escort-roster/sync-history` | Sync from WA file |
| POST | `/api/escort-roster/rebuild-history` | Full rebuild |
| POST | `/api/escort-roster/backfill-sqlite` | Import from bridge2 |
| POST | `/api/escort-roster/backfill-files` | Import from WA files |
| POST | `/api/escort-roster/match-slip` | OCR → program match |
| GET | `/api/escort-roster/active` | Active programs only |

## Database Tables

| Table | Purpose |
|---|---|
| `escort_roster_entries` | Projection/cache of escort programs |
| `escort_calculation_config` | Conveyance amounts and shift rates per destination |
| `escort_shift_logs` | Per-shift activity events (auto-written on status transitions) |
| `escort_release_matches` | OCR slip → program match suggestions |
| `escort_roster_audit_logs` | Audit trail for all roster changes |
| `escort_slip_extractions` | OCR extraction results from slip images |
| `escort_order_groups` | Parent: one row per WA message with escort order(s) |
| `escort_order_lighters` | Child: one row per lighter/escort assignment |

## Pay Calculation

```
total_shifts = count_shifts(start_date, start_shift, end_date, end_shift)
total_days   = total_shifts × 0.5
salary       = total_shifts × shift_rate    (from escort_calculation_config, default 200 BDT)
conveyance   = from escort_calculation_config by destination
total        = salary + conveyance
net_payable  = total - food_bill            (populated on sync)
```

**Note:** Roster `total` differs from payment draft `gross_amount` by design.
See `knowledge_base/06_developer_system/escort_roster_system.md` for details.

## Phone Normalization

This module uses `normalize_bd_phone()` from `modules.fazle_payroll_engine.normalizer`
for all phone validation in create/update routes. The canonical FPE format is
`01XXXXXXXXX` (11 digits). Cross-domain lookups use `phone_key9()` (last 9 digits).

## Auto-Sync Triggers

- `save_escort_programs()` → `_sync_roster_draft(pid)` — on client order save
- `handle_admin_escort_completion()` → `_sync_roster_after_confirm(pid)` — on admin confirmation
- Stale draft expiry loop — every 6 hours in `app/main.py` lifespan
- Exact confirmed duplicate cleanup — every 6 hours in `app/main.py` lifespan

## Deterministic Duplicate Guard (2026-07-05)

- Module-level cleanup utility: `cleanup_exact_confirmed_duplicates(days=...)`
- Audit utility: `audit_exact_confirmed_duplicates(days=...)`
- Duplicate key: `COALESCE(start_date, program_date) + mother_vessel + lighter_vessel + escort_name`
- Cleanup keeps the earliest `program_id` per key and removes leftovers when
    there are no dependent references.

## Frontend

The SPA at `app/static/escort-roster.html` has 5 tabs:
1. **Roster** — 15-column sortable/filterable table with search, status filter, pagination
2. **Kanban** — 6-column board by status
3. **Slip Queue** — Pending OCR slip reviews with confirm/reject buttons
4. **Shift Calculator** — Client-side shift/pay calculator
5. **Config** — Conveyance config + escort client phone management

## Related Modules

- `modules/escort/` — Order parser, confirmation handler, auto-employee create
- `modules/escort_lifecycle/` — Release/close workflow, attendance backfill
- `modules/escort_slip_extractor/` — OCR slip extraction from images
- `modules/payment_workflow/` — Payment draft creation after release
- `modules/fazle_payroll_engine/` — Employee matching, accounting ledger

## Knowledge Base

- `knowledge_base/06_developer_system/escort_roster_system.md` — System reference
- `knowledge_base/06_developer_system/escort_order_parser.md` — Parser reference
- `knowledge_base/06_developer_system/escort_slip_extractor.md` — OCR reference
- `knowledge_base/04_business_rules/escort_business_rules.md` — Business rules
- `knowledge_base/05_workflows/escort_workflow.md` — Workflow (canonical)
- `knowledge_base/03_ai_identity/escort_identity.md` — AI identity

## Production Hardening (2026-07-03)

All 20 tasks from the Production Hardening Plan have been implemented:
- ✅ `food_bill`/`net_payable` sync in `sync_program_to_roster()`
- ✅ Case-insensitive status filter
- ✅ Slip Queue tab with backend + frontend
- ✅ Stale draft expiry scheduler (every 6h)
- ✅ Phone validation on create/update
- ✅ Shift log write path on status transitions
- ✅ Destination normalization unified (single `DEST_ALIASES` in `modules.escort`)
- ✅ Date-shift regex unified (single `DATE_SHIFT_RE` in `calculations.py`)
- ✅ FK constraints on `escort_order_lighters`
- ✅ `net_payable` column in frontend roster table
- ✅ KB documentation updated