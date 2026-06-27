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
