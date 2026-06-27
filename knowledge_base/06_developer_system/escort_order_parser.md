---
title: Escort Order Parser Source Of Truth
owner: Fazle Core Admin
status: active
last_verified: 2026-06-27
runtime_index: true
source_module: modules/escort
---

# Escort Order Parser Source Of Truth

`modules/escort` contains the deterministic escort-order parser and message-direction gates. This article is the developer-facing source of truth for how escort intake and confirmation parsing work in production.

## Parser Philosophy

Primary signal:
- Master Mobile Number

Secondary signals:
- Mother vessel block
- Lighter vessel block
- Importer
- Cargo
- Destination
- Capacity

Important rules:
- Mobile always anchors the lighter block
- The nearest vessel name to that mobile is the lighter vessel
- A block with a mobile number cannot be the mother vessel
- `MV`, `M.V.`, `Mv`, and similar labels are helper signals only
- `MV` may appear before both mother and lighter vessels
- Never rely on `MV` label alone

## Message Direction Gates

### Draft Intake Gate

Accept only:
- sender in `ESCORT_CLIENT_PHONES`
- recipient is bridge `8801880446111` or `8801958122300`

Minimum required fields:
- `mother_vessel`
- `lighter_vessel`
- `master_mobile`

If minimum fields are missing:
- ignore message
- do not create draft

If minimum fields are present:
- parse best effort
- create one draft row
- keep it unconfirmed

### Confirmation Gate

Accept only:
- sender is bridge `8801880446111` or `8801958122300`
- recipient in `ESCORT_CLIENT_PHONES`

Required confirmation fields:
- `mother_vessel`
- `lighter_vessel`
- `escort_name`
- `escort_mobile`
- `start_date`

If required confirmation fields are missing:
- ignore message as confirmation

If present:
- treat message as source of truth
- update matching draft when found
- create confirmed row only when no draft exists
- never create duplicates

## Supported Parsing Modes

- Block-based parsing has priority
- Line-based parsing remains supported

Typical mother vessel block:
- mother vessel name
- importer
- cargo
- account / A/C
- sometimes date

Typical lighter block:
- lighter vessel
- master mobile
- destination
- capacity

## Field Detection Rules

### Lighter Vessel

Accepted Bangladesh mobile formats:
- `017...`
- `018...`
- `019...`
- `88017...`
- `+88017...`

Each mobile belongs to one lighter block.

Serial numbers such as:
- `8. Haji Salim`
- `9. Banglar Odhinayok`
- `1.sheikh enterprise 2-017...`

must be stripped so only the vessel name remains.

### Mother Vessel

Mother vessel is usually the nearby vessel block without a mobile number.

Supporting clues:
- `MV`
- `M.V.`
- `Mother Vessel`
- `At-O/A`
- importer / cargo / account fields

Hard rule:
- if a candidate block contains a mobile, it is not the mother vessel

### Importer

Usually near the mother vessel block.

Supported labels include:
- `A/C`
- `A/c`
- `Account`
- `Importer`

### Cargo

Cargo belongs to the mother vessel block when possible.

Fallback:
- search globally
- longest cargo keyword wins

### Destination

Destination belongs to the lighter block nearest the mobile.

Normalization:
- `Local` and `CTG` -> `Chattogram`
- `Rupshi` -> `Narayanganj`
- `N.Bari` -> `Nagarbari`

### Capacity

Capacity belongs to the lighter block nearest the mobile.

Normalization:
- `700 m.t`
- `700 MT`
- `700mt`
- all become `700 MT`

## Confirmation Overwrite Rules

When bridge confirmation matches an existing draft, confirmation may overwrite:
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

Destination is normalized before save.

If the live schema does not expose `cargo_type` or `importer` as physical columns, those values are preserved in `remarks` instead of forcing a schema change.

## Duplicate Prevention

Matching priority:
1. master mobile
2. mother vessel
3. client phone
4. start date

Rules:
- one client request creates at most one draft
- bridge confirmation updates that row when matched
- confirmed duplicates must not be created

## Notes For Developers

- Do not reintroduce text-only heuristic parsing without direction gates.
- Do not treat client drafts as final truth.
- Do not let roster search/export use ambiguous joined columns.
- UI requests should omit empty params and surface real API errors.

## Verified Examples

Production tests cover:
- Dubai Eco example
- TRUONG MINH PROSPERITY example
- MV label on lighter vessel
- multiple serial numbers
- missing cargo
- missing destination
- missing importer
- `Local -> Chattogram`
- `Rupshi -> Narayanganj`
- draft updated by confirmation
- duplicate prevention
- wrong-direction confirmations ignored
