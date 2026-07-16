---
title: Escort Source Of Truth Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
---

# Escort Source Of Truth Workflow

## Workflow Diagram

```text
Escort Client
      |
      v
Client -> Bridge
      |
      v
AI Parser
      |
      v
Draft (Not Source Of Truth)
      |
      v
Operation/Admin Review
      |
      v
Bridge -> Escort Client Confirmation
      |
      v
SOURCE OF TRUTH
      |
      v
Overwrite Draft
      |
      v
wbom_escort_programs
      |
      v
Escort Roster Projection
      |
      v
UI / Payroll / Reports
```

## Practical Meaning

- Client-origin messages are intake only.
- Drafts are operational placeholders only.
- Final assignment truth comes from the outbound bridge confirmation to the approved escort client.
- Confirmation overwrites draft mistakes instead of creating a second row.
- The roster projection must follow the final confirmed state.

## Direction Gate

Accepted draft direction:
- `ESCORT_CLIENT_PHONES -> bridge`

Accepted confirmation direction:
- `bridge -> ESCORT_CLIENT_PHONES`

Everything else:
- ignored for source-of-truth escort confirmation

## Matching Logic

Draft-to-confirmation reconciliation uses:
1. Client phone
2. Master mobile
3. Mother vessel
4. Start date

Bridge confirmation always wins when a match is found.

## Final State Requirements

After confirmation:
- Same program row is updated
- No duplicate row is created
- Destination aliases are normalized before save
- Program status is final
- Roster status is synced to the confirmed-equivalent visible state

## Production Verification

Live non-destructive verification completed on 2026-06-27 confirmed:
- draft creation works
- invalid client intake is ignored
- bridge confirmation overwrites corrupted draft values
- destination normalization works
- duplicate prevention works
- roster list and export search both work
- browser search, view, edit, recalculate, config, escort-client management, and export all work

## Enforcement — Where The Gates Actually Live

Added in commit `41916b0` (2026-06-27) and unchanged since:

- **Draft creation gate** (`modules/escort/__init__.py`, `handle_escort_client_message()`):
  `_is_trusted_escort_source(source)` (bridge must be in `ESCORT_TRUSTED_SOURCES`) and
  `_is_allowed_escort_client(sender_phone)` (sender must be in `ESCORT_CLIENT_PHONES`),
  both checked before parsing; `is_minimum_escort_order(order)` (mother vessel + at
  least one lighter with lighter_vessel + master_mobile) checked before saving.
- **Confirmation gate** (`handle_admin_escort_completion()`):
  `_is_authoritative_bridge_confirmation(ctx)` — sender must be one of
  `ADMIN_NUMBERS`/`ADMIN_META_NUMBER`/`ADMIN_BRIDGE1_NUMBER`/`ADMIN_BRIDGE2_NUMBER`, and
  if a recipient is given it must be a registered `ESCORT_CLIENT_PHONES` entry.
- **Draft TTL**: unconfirmed drafts (`status='draft'`) are hard-**deleted**, not
  soft-expired, after `DRAFT_TTL_HOURS` (default 24) — `expire_stale_drafts()` in
  `modules/escort_roster/db.py`, added in commit `82f2b33` (2026-07-05), run on a
  schedule by `modules/scheduler`. Full row content is written to
  `escort_roster_audit_logs` before each delete.

Session 16 (2026-07-16) audited this against an explicit non-negotiable business-rule
set and found all of the above already enforced; the only gap was 18 historical rows
predating the 2026-06-27 gate that had gone through the *old* soft-expire path and were
never retroactively swept — cleaned up one-time, see
`knowledge_base/00_governance/escort_sender_validation_audit_2026-07-16.md`. A
defensive second sweep for any future financially-inert legacy `status='expired'` row
was added to `expire_stale_drafts()` so this specific failure mode can't recur even in
an edge case.
