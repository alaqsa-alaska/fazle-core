---
title: Escort Business Rules
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
---

# Escort Business Rules

## Message Direction Rules

Production escort-order parsing is direction-gated first. Parsing does not begin until the sender and recipient match one of the approved flows below.

### Client -> Bridge

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed client numbers:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- The client is requesting escort service.
- This message may create a draft only.
- It is never the source of truth.

Minimum required fields:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any minimum field cannot be identified with reasonable confidence:
- Ignore the message
- Create no draft
- Create no roster side effect

If the minimum fields are present:
- Parse with best effort
- Create one draft order only
- Keep status unconfirmed / waiting confirmation
- Never auto-confirm from the client message

### Bridge -> Escort Client

Allowed bridge numbers:
- `8801880446111`
- `8801958122300`

Allowed recipients:
- `ESCORT_CLIENT_PHONES` from `.env`

Meaning:
- Official escort confirmation
- This is the only source of truth for final escort assignment data

Required confirmation fields:
- Mother Vessel
- Lighter Vessel
- Escort Name
- Escort Mobile
- Start Date

If any required confirmation field is missing:
- Ignore the message as escort confirmation
- Do not update a draft
- Do not create a confirmed row

If all required confirmation fields are present:
- Match an existing draft by client phone, master mobile, mother vessel, and start date
- Update the existing row when matched
- Overwrite incorrect draft values with confirmation values
- Create one confirmed row only when no draft exists
- Never create duplicate rows

## Source Of Truth Policy

Not source of truth:
- Client order text
- AI parser guess
- Draft row

Source of truth:
- Bridge -> Escort Client confirmation message

Conflict rule:
- If draft data conflicts with confirmation data, confirmation always wins

## Parsing Rules

Primary signal:
- Master Mobile Number

Secondary signals:
- Mother vessel block
- Lighter vessel block
- Importer
- Cargo
- Destination
- Capacity

Rules:
- Every valid Bangladesh mobile belongs to one lighter block
- The vessel nearest that mobile is the lighter vessel
- Destination and capacity nearest that mobile belong to that lighter
- A block containing a mobile number cannot be the mother vessel
- `MV`, `M.V.`, `Mv`, and similar labels are helper signals only
- Those labels may appear before both mother vessels and lighter vessels
- Never classify mother vessel from `MV` label alone

Supported parsing modes:
- Block-based parsing has higher priority
- Line-based parsing remains supported as fallback

## Normalization Rules

Destination:
- `Local` and `CTG` -> `Chattogram`
- `Rupshi` -> `Narayanganj`
- `N.Bari` -> `Nagarbari`

Capacity:
- `700 m.t`
- `700 MT`
- `700mt`
- all normalize to `700 MT`

## Validation And Duplicate Prevention

Before saving any escort order row, the parser must have:
- Mother Vessel
- Lighter Vessel
- Master Mobile

If any of the three are missing:
- Discard the message
- Do not save draft data

Duplicate-prevention match priority:
1. Master Mobile
2. Mother Vessel
3. Client Phone
4. Start Date

## Roster Synchronization Rule

`wbom_escort_programs` is the operational parent.

`escort_roster_entries` is the roster projection.

When a program becomes confirmed by source-of-truth confirmation:
- Program status becomes `confirmed`
- Roster status must not remain `draft`
- Roster status is synced to `confirmed`
- UI must show a clean final state without program/roster conflict

## Escort Client Configuration Rule

`ESCORT_CLIENT_PHONES` is the whitelist for escort-order directions.

Rules:
- Numbers are configured in `.env`
- Add and remove operations must normalize Bangladesh mobile format
- Invalid numbers are rejected
- Duplicate numbers are not re-added
- Updating the list clears the cached settings object immediately
- No service restart is required for whitelist changes

## Browser And Admin Output Rules

- Generated admin notification must include only one Automated Reply footer
- Search and export endpoints must qualify joined SQL columns with table aliases
- The roster UI must not send empty or malformed query parameters
- Optional missing fields should render as `—`, not throw runtime errors

## Cross References

- `../05_workflows/escort_workflow.md`
- `../05_workflows/escort_source_of_truth_workflow.md`
- `../06_developer_system/escort_order_parser.md`
- `../06_developer_system/escort_roster_system.md`
