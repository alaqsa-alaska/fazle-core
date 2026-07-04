---
title: Escort Workflow
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
---

# Escort Workflow

## End-To-End Flow

```text
Escort Client
  ->
Client -> Bridge
  ->
Deterministic Escort Parser
  ->
Draft Program + Draft Roster Projection
  ->
Operations / Admin Review
  ->
Bridge -> Escort Client Confirmation
  ->
Source Of Truth Overwrite
  ->
wbom_escort_programs
  ->
escort_roster_entries
  ->
UI / Payroll / Reports
```

## Operational Sequence

1. An inbound message is first checked for direction.
2. Only `ESCORT_CLIENT_PHONES -> bridge number` can create a draft request.
3. The parser requires mother vessel, lighter vessel, and master mobile before creating any draft.
4. The draft remains non-authoritative and unconfirmed.
5. Operations or admin may review the draft, but review does not make it source of truth.
6. Only `bridge number -> ESCORT_CLIENT_PHONES` with the required confirmation fields can finalize the order.
7. When confirmation matches an existing draft, the same row is updated and overwritten.
8. The roster projection is synced so the visible roster state matches the confirmed source program.
9. Later release-slip and payment workflows continue from the confirmed roster/program state.

## Client -> Bridge Draft Flow

Required direction:
- Sender is in `ESCORT_CLIENT_PHONES`
- Recipient is `8801880446111` or `8801958122300`

Required minimum fields:
- Mother Vessel
- Lighter Vessel
- Master Mobile

Result:
- One draft row only
- One draft roster projection only
- Never confirmed from this step

Rejected cases:
- Missing mother vessel
- Missing lighter vessel
- Missing master mobile
- Wrong message direction

## Bridge -> Client Confirmation Flow

Required direction:
- Sender is `8801880446111` or `8801958122300`
- Recipient is in `ESCORT_CLIENT_PHONES`

Required confirmation fields:
- Mother Vessel
- Lighter Vessel
- Escort Name
- Escort Mobile
- Start Date

Result:
- This message becomes the source of truth
- Existing draft is updated when matched
- Incorrect draft values are overwritten
- Duplicate rows are not created

Overwrite set:
- `mother_vessel`
- `lighter_vessel`
- `master_mobile`
- `destination`
- `cargo_type`
- `importer`
- `capacity`
- `escort_name`
- `escort_mobile`
- `start_date`
- `shift`
- `status`

## Status Flow

```text
Client request
  -> draft

Bridge confirmation
  -> confirmed

Roster projection sync
  -> roster_status = confirmed
```

Important rule:
- A confirmed source program must not leave `escort_roster_entries.roster_status = draft`

## Parsing Workflow Notes

- Block-based detection has priority over line-based parsing.
- Master mobile is the strongest signal for identifying lighter blocks.
- `MV` labels help but never decide the result by themselves.
- A block containing a valid mobile number cannot be the mother vessel.

## Escort Client Management Workflow

Config tab capabilities:
- Load current escort clients
- Add escort client number
- Remove escort client number

Runtime behavior:
- The UI writes to `ESCORT_CLIENT_PHONES`
- Settings cache is cleared immediately
- No application restart is required

## Browser/API Workflow

Roster page normal flow:
1. Sign in with internal key
2. Load summary
3. Load roster list
4. Search with sanitized query params only
5. Open view drawer
6. Open edit modal
7. Recalculate without crash
8. Export CSV with response validation
9. Open Config tab
10. Load Escort Client Management

## Live Verification Snapshot

Verified on 2026-06-27:
- Draft-only client flow
- Source-of-truth confirmation overwrite
- Destination normalization
- Duplicate prevention
- Search API `200`
- Export API `200`
- Browser search render
- View/edit/recalculate/export actions
- Config tab and Escort Client Management render

## Cross References

- `escort_source_of_truth_workflow.md`
- `client_order_workflow.md`
- `release_slip_workflow.md`
- `../04_business_rules/escort_business_rules.md`
