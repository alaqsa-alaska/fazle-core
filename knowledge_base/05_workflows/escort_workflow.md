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
