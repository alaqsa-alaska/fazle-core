# Escort Workflow

## Flow
Client Order -> AI Parser -> Escort Draft -> Admin Edit -> Client Confirmation -> Escort Assignment -> Duty Start -> Release Slip -> Payment Draft -> Admin Payment Handling -> Accountant.

## Required Order Fields
Mother vessel, lighter vessel, master mobile, destination/release point, product, capacity, escort name/mobile, start date, shift.

## Admin Required
Admin review is mandatory before final client confirmation and assignment.

## Cross References
- client_order_workflow.md
- release_slip_workflow.md
- ../04_business_rules/escort_business_rules.md

---

## Escort Program State Machine

### Purpose
Escort programs follow a defined lifecycle. Each state has specific conditions for entry and exit.

### States

```
draft → confirmed → Assigned → Running → Completed
   ↓         ↓          ↓         ↓
   └─────────┴──────────┴─────────┘ → Cancelled (from any pre-Completed state)
```

| State | Meaning | Entry Condition |
|---|---|---|
| draft | Client order received; escort not yet assigned | AI parser creates program from client message |
| confirmed | Admin has assigned escort | Admin runs `ESCORTCONFIRM <id>\|<name>\|<mobile>\|<date>\|<shift>` |
| Assigned | Program assigned; escort not yet on-site | Status updated after ESCORTCONFIRM |
| Running | Escort is on active duty | Duty start date reached |
| Completed | Release confirmed; attendance backfilled; payment drafted | Admin processes release slip |
| Cancelled | Program cancelled before completion | Admin runs `ESCORTCANCEL <id> <reason>` |

**Business Rule:** An escort program cannot skip states. Completion requires release slip processing. Cancellation is possible from any state before Completed.

**Source Module:** `modules/escort`, `modules/escort_lifecycle`
**PKCA Report:** 09_state_machine_coverage_report.md (SM-02)
**Management Authority:** Production evidence; documented 2026-06-22

---

## ESCORTCONFIRM Command

**Syntax:** `ESCORTCONFIRM <program_id>|<escort_name>|<escort_mobile>|<start_date>|<shift>`

**Required Role:** operator

**Example:**
```
ESCORTCONFIRM 142|মোঃ রফিকুল|01711234567|2026-06-25|D
```

**Fields:**
- `program_id` — numeric program ID from draft
- `escort_name` — full name of assigned escort
- `escort_mobile` — escort's mobile number
- `start_date` — `YYYY-MM-DD` format
- `shift` — `D` (Day) or `N` (Night)

**Source Module:** `modules/admin_commands`
**Source Function:** `_cmd_escort_confirm()`
**PKCA Report:** 12_command_coverage_report.md

---

## Escort Order Parser Formats

### Purpose
The AI parser supports 4 distinct client message formats for escort orders. All 4 formats extract the same set of required fields.

| Format | Description | Example |
|---|---|---|
| Labeled block | Key-value lines with labels | `Mother Vessel: MV XYZ\nLighter: Shah Amanat` |
| Inline compact | All fields on one or two lines | `MV XYZ / Shah Amanat / Master: 01711...` |
| MV-block | Multi-MV grouped sections | One block per mother vessel with lighters listed below |
| Numbered | Lighter vessels numbered 1., 2., 3. | Used in multi-lighter programs |

**Business Rule:** If the parser cannot extract all required fields, the order goes to manual review draft. Admin fills in the missing fields.

**Required fields:** mother_vessel, lighter_vessel, master_mobile, destination, product, capacity, start_date, shift

**Source Module:** `modules/escort`
**Source Function:** `parse_escort_order()`, `_MV_LABEL_RE`, `_IMPORTER_RE`
**PKCA Report:** 05_parser_coverage_report.md

---

## Stale Program Alert

**Business Rule:** Escort programs in `Active` or `Assigned` status for more than 30 days trigger a daily alert to the admin (at 09:00, via `stale_escort_reminder` scheduler job).

**Each program is alerted only once** — reminder key is stored in `fazle_reminders_sent` (ON CONFLICT DO NOTHING).

**Override:** `ESCORT_STALE_DAYS` environment variable (default: 30 days)

**Source Module:** `modules/scheduler`
**Source Function:** `job_stale_escort_reminder()`
**PKCA Report:** 07_scheduler_coverage_report.md

---

## [RELEASE CONFIRMED] — Escort Release Closure (Wave-4)

### Purpose

When a release slip is processed via OCR, the system generates a draft `[RELEASE CONFIRMED]` message. The admin reviews the draft, corrects any errors, and sends it to the escort's WhatsApp. The bridge poller detects this outbound message and triggers `handle_admin_release_confirmation()` in `modules/escort_lifecycle`, which closes the program and creates attendance + payment records.

**RELEASE command is deprecated.** As of Wave-4: `_cmd_release()` returns an error message instructing the admin to use the `[RELEASE CONFIRMED]` WhatsApp format instead.

### `[RELEASE CONFIRMED]` Message Format

The admin sends a message containing exactly this anchor text:
```
[RELEASE CONFIRMED]
```

**Full template (8 parsed fields):**
```
[RELEASE CONFIRMED]  ✅ OCR DRAFT
Escort: মোঃ রফিকুল
Lighter: Shah Amanat
Release Point: Mongla
Start Date: 10.06.2026
End Date: 20.06.2026
Shift: D
Days: 11
Conveyance: 800
```

### Parsed Fields — `parse_release_confirmation()`

| Field in Dict | Parsed From | Regex Pattern |
|---|---|---|
| `end_date` | `End Date: DD.MM.YYYY` | `_RC_DATE_RE` |
| `end_shift` | `Shift: D` or `Shift: N` | `_RC_SHIFT_RE` |
| `release_point` | `Release Point: ...` | `_RC_POINT_RE` |
| `day_count` | `Days: N` | `_RC_DAYS_RE` |
| `conveyance` | `Conveyance: amount` | `_RC_CONV_RE` |
| `food_bill` | `Food Bill: amount` | `_RC_FOOD_RE` |
| `escort_name` | `Escort: name` | `_RC_ESCORT_RE` |
| `lighter_vessel` | `Lighter: vessel_name` | `_RC_LIGHTER_RE` |

**Detection:** `is_release_confirmation(text)` checks for `[RELEASE CONFIRMED]` anchor (case-insensitive).

**Trigger:** `bridge_poller` detects outbound messages with `is_from_me=1` containing `[RELEASE CONFIRMED]`.

**Source Module:** `modules/escort_lifecycle`
**Source Functions:** `is_release_confirmation()`, `parse_release_confirmation()`, `handle_admin_release_confirmation()`

---

## `wbom_escort_programs.remarks` — JSON Structure

The `remarks` column in `wbom_escort_programs` stores a JSON object. It is initialized at draft creation and extended (merged) by ESCORTCONFIRM.

**8 core fields:**

| Field | Set By | Value |
|---|---|---|
| `sender_phone` | `save_escort_programs()` | Client phone who sent the escort order |
| `source_bridge` | `save_escort_programs()` | `bridge1`, `bridge2`, or `meta` |
| `escort_name` | `save_escort_programs()` | `null` initially; text string after ESCORTCONFIRM |
| `escort_mobile` | `save_escort_programs()` | `null` initially; phone number after ESCORTCONFIRM |
| `capacity` | `save_escort_programs()` | Lighter capacity (from parsed client order) |
| `importer` | `save_escort_programs()` | Importer company name (from parsed order) |
| `cargo_type` | `save_escort_programs()` | Cargo type (from parsed order) |
| `confirmed_by` | `_cmd_escort_confirm()` | Admin phone who ran ESCORTCONFIRM |

**Note:** The `remarks` column may contain corrupted JSON (text appended after the closing brace). `_safe_json()` in `escort_roster/db.py` handles fault-tolerant parsing by truncating at the last `}`.

**Used by:** `_cmd_escort_confirm()` reads `sender_phone` and `source_bridge` to forward the assignment slip directly to the client's WhatsApp bridge.

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Initial Wave-2B escort state machine, ESCORTCONFIRM, parser formats, stale alert | KSP Wave-2B |
| 2026-06-23 | Wave-4: Added [RELEASE CONFIRMED] format, 8 parsed fields, remarks JSON 8-field schema | W4-AUTH |
