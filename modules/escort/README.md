# Escort Module

Vessel escort order processing — inbound client orders → admin drafts → finalized slips.

## Source Of Truth

`parse_escort_message(text)` is the single canonical escort order parser for `fazle-core`.
Knowledge base articles, workflow docs, and routing logic must point to this function.

Golden rule: a vessel grouped with a mobile number is a Lighter Vessel, even if it has an `MV` label. A vessel line without a mobile number and with `MV`, `M.V.`, `Mother Vessel`, `এমভি`, or `At-O/A` is a Mother Vessel candidate.

## Flow

```
Client sends MV / lighter message
  → role + context intent detection
  → code parser validates Mother Vessel + Lighter Vessel + Master Mobile
  → parse_escort_message()
  → save_escort_programs()   ← one draft program row per lighter, dedup-safe
  → sync_program_to_roster() ← one draft roster row per program_id
  → build_admin_message()    ← admin draft, no direct client reply
  → admin_note returned to message_router → sent to admin

Admin fills Escort Name + Escort Mobile, sends completed draft back
  → is_completed_escort_draft() detects it
  → handle_admin_escort_completion()
  → match existing draft by MV/lighter/master/date/client context
  → build_final_slip() → sent to original client
  → wbom_escort_programs status → 'confirmed'
  → roster sync
```

## Key Functions

| Function | Purpose |
|----------|---------|
| `parse_escort_message(text)` | Extract mother vessel, importer, cargo, lighters, master mobile, destination, capacity, date, shift, remarks |
| `save_escort_programs(order, phone, source)` | Insert to `wbom_escort_programs`; dedup-safe |
| `build_admin_draft(mv, lighter)` | Standard plain-text draft for one lighter |
| `build_admin_message(order, phone)` | Full admin message wrapping all lighter drafts |
| `handle_escort_client_message(text, phone, source, is_historical=False)` | Public entry point for client orders |
| `handle_admin_escort_completion(text, phone, source)` | Public entry point for admin completion |
| `is_completed_escort_draft(text)` | Detect admin's filled-in draft |

## Draft Format

```
Cargo: SOYBEAN
Client: 8801XXXXXXXXX

Mother Vessel: MV EXAMPLE
Importer: Nabil
Lighter Vessel: EXAMPLE LIGHTER
Master Mobile: 01XXXXXXXXX
Destination: Narayanganj
Capacity: 1000 MT
Escort Name:
Escort Mobile:
DD.MM.YYYY (D/N)
Al-Aqsa Security & Logistics Services Ltd
```

## Dedup Logic

`save_escort_programs()` checks `wbom_escort_programs` for a row matching:
- Same MV (case-insensitive, strips "MV " prefix)
- Same lighter vessel (case-insensitive)
- Same master mobile when available
- Status not cancelled
- `program_date >= CURRENT_DATE - 30 days`

If found, returns existing `program_id` and skips INSERT.

`handle_admin_escort_completion()` updates the same draft row by matching mother vessel, lighter vessel, master mobile, program date, and stored client phone when available. If no matching draft exists and admin sent a complete assignment, it may create one confirmed row directly.

## Order vs Review

Auto order creation requires buyer context (`Escort`/`TCIS` contact prefix or configured escort-buyer role) plus Mother Vessel, Lighter Vessel, and Master Mobile.

Do not auto-create an order when:
- the client message contains `Escort Name` + `Escort Mobile`
- the message is an escort complaint
- the message/OCR text is a duty slip or release slip
- the message discusses food bill, conveyance, release, or an escort leaving a vessel

Those cases become admin-review drafts and must be verified against DB/roster data.

## Confirmation Rules

- Draft row duplicate is forbidden: one order creates one draft program row and one draft roster row per lighter.
- Admin confirmation is required: `confirmed` needs admin-origin Escort Name + Escort Mobile.
- Existing draft update is required: confirmation updates the old program and roster rows; it does not create a second roster duty.
- Unconfirmed drafts expire by status (`expired`), not hard delete.

## Employee Identity

`wbom_employees.employee_id` is numeric. Escort mobile is the stable identity anchor in `employee_mobile`. If the mobile is not found, the module auto-creates an active `Escort` employee row and links the generated numeric employee ID to `wbom_escort_programs.escort_employee_id`.

## Historical Import

Pass `is_historical=True` to `handle_escort_client_message()` when importing
historical data. This saves the DB record but suppresses admin notification.

## DB Table

`wbom_escort_programs` — key columns:
- `program_id`, `mother_vessel`, `lighter_vessel`, `master_mobile`
- `status`: `draft` → `confirmed` → `Active` → `Closed`
- `remarks`: JSON blob with `sender_phone`, `source_bridge`, `escort_name`, `escort_mobile`
- `is_historical`: TRUE for imported records (added in migration 009)
