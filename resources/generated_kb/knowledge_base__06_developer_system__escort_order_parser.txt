---
title: Escort Order Parser Source Of Truth
owner: Fazle Core Admin
status: active
last_verified: 2026-06-26
runtime_index: true
source_module: modules/escort
source_function: parse_escort_message
---

# Escort Order Parser Source Of Truth

`modules/escort.parse_escort_message()` is the single canonical parser for escort-buyer client orders. Other modules, KB articles, prompts, and workflows must treat this function as the source of truth.

## Runtime Flow

Escort buyer/client message -> role + context intent detection -> `modules/escort.handle_escort_client_message()` -> `parse_escort_message()` -> one `wbom_escort_programs` draft row per lighter -> admin review draft -> admin fills escort name/mobile -> completed draft is detected from outgoing bridge polling -> the same draft row is updated to `confirmed` -> roster sync. The client does not receive a direct auto-reply for an order.

Strict order detection is code-based, not AI-based. AI may explain or review, but production extraction and routing must be done by deterministic parser logic.

## Priority Rules

1. If a vessel line has a mobile number, it is a Lighter Vessel.
2. A mobile number has higher priority than an `MV` label. Example: `02) MV AL MORIUM` followed by `01711273432` is a lighter, not the mother vessel.
3. If a vessel line has no mobile number and has `MV`, `M.V.`, `Mother Vessel`, `এমভি`, or `At-O/A`, it is a Mother Vessel candidate.
4. Mother Vessel normally appears on the first or last non-mobile vessel line.
5. Importer, cargo, destination, capacity, and mobile lines may be grouped across multiple lines into one lighter record.
6. Same-line mother/importer/cargo is supported for known importer patterns, for example `MV MARIMYR A Nabil Soybean` -> mother `MV MARIMYR A`, importer `Nabil`, cargo `Soybean`.
7. A client-side order requires buyer context (`Escort`/`TCIS` contact prefix or configured escort-buyer role) plus Mother Vessel, Lighter Vessel, and Lighter/Master Mobile.
8. If the client message already contains Escort Name + Escort Mobile, it is not a buying order. Treat it as complaint/assignment-like review and draft it to admin.
9. If the client sends an escort duty/release slip image or OCR text, it is not a buying order. Extract text, look up DB/roster, and draft a verification/release/payment note to admin.

## Extracted Fields

- `mother_vessel`
- `importer`
- `cargo_type`
- `lighters[].lighter_vessel`
- `lighters[].master_mobile`
- `lighters[].destination`
- `lighters[].capacity`
- `lighters[].shift`
- `lighters[].remarks` for additional mobile numbers
- `date_hint`
- `shift`

## Supported Labels

Mother Vessel labels: `MV`, `M.V.`, `Mv.`, `Mother Vessel`, `এমভি`, `At-O/A`.

Importer labels: `A/c.`, `A/c:`, `A/C`, `A/C:`, `A/C-`, `A/c-`.

Capacity labels: `Cap`, `Capacity`.

Destination labels/patterns: `Dest`, `Destination`, `O/A to`, `Alongside`, `At-O/A`.

Mobile labels: `Mob`, `Mobile`, `Master`, `Phone`, `Tel`.

## Normalization Rules

Cargo:
- soybean/soyabean/soya meal variants -> `Soybean Meal`
- soybean/soyabean ext variants -> `Soybean Extract`
- soybean/soyabean/soya -> `Soybean`
- wheat/গম -> `Wheat`
- corn/ভুট্টা -> `Corn`
- mustard/mustard oil -> `Mustard`
- coal/কয়লা -> `Coal`
- sugar/চিনি -> `Sugar`
- rice/চাল -> `Rice`
- salt/লবণ -> `Salt`
- yellow peas/y.peas/y peas -> `Yellow Peas`

Destination:
- `N.Bari`, `Nagar Bari`, `N Bari` -> `Nagarbari`
- `N.Para`, `Noapara`, `Nowapara`, `Nopara`, `N Para`, `Rupsi` -> `Noapara`
- `N.Gonj`, `N.Ganj`, `Narayanganj`, `Narayan Gonj`, `N Gonj`, `Rupshi`, `Rupahi` -> `Narayanganj`
- `Barisal` -> `Barishal`
- `Local`, `CTG` -> `Chittagong`

Capacity:
- `Cap: 1700 Mt`, `Capacity: 1200`, `700 m.t`, `1,100 M.T.`, `1200 m t` are normalized to `XXXX MT`.
- Bare lighter-line values in the normal field range, for example `900`, `1000`, `1200`, are normalized to `XXXX MT` only when the line/context is a lighter block. This avoids treating serials, dates, or phone fragments as capacity.

Shift:
- `(D)`, `(N)`, `Day`, `Night`, `Shift: D`, `Shift: N`, `D shift`, and `N shift` are normalized to `D` or `N`.

Mobile:
- `+8801711273432`, `01711-273432`, `8801711273432` are normalized to `01711273432`.
- First mobile number is `master_mobile`.
- Additional mobile numbers are preserved in `remarks`.

## Admin Draft And Confirmation Rules

The admin draft includes cargo, client phone, mother vessel, importer, lighter vessel, master mobile, destination, capacity, blank escort name/mobile, date with shift placeholder, company footer, and an automated-system warning.

When admin sends the completed assignment, the system matches the existing draft by mother vessel, lighter vessel, master mobile, program date, and stored client phone when those fields are available. A matching draft is always updated; a duplicate confirmed row must not be created.

Mandatory production rules:
- Draft Row Duplicate Rule: the same order must not create duplicate draft program or duplicate draft roster rows.
- Admin Confirmation Rule: no program may become `confirmed` unless an admin-origin message/slip contains Escort Name and Escort Mobile.
- Existing Draft Update Rule: confirmation updates the old draft program and its linked roster row; it does not create a new roster duty.

If no matching draft exists and the admin message clearly contains a full confirmed escort assignment, the system may create one confirmed program directly.

`wbom_employees.employee_id` is numeric. Escort mobile is the operational identity anchor stored as `wbom_employees.employee_mobile`. If an escort mobile is not found, the system auto-creates an active `Escort` employee row and stores the generated numeric `employee_id` on `wbom_escort_programs.escort_employee_id`.

Draft expiry is status-based, not hard delete. If admin confirmation does not arrive within the configured draft TTL, set `wbom_escort_programs.status='expired'` and `escort_roster_entries.roster_status='expired'` so audit trail remains intact.
