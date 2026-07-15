---
title: Database Rules — Fazle AI Platform
owner: Fazle Core Admin
status: active
last_verified: 2026-07-02
runtime_index: true
---

# Database Rules — Fazle AI Platform

**Visibility:** DEVELOPER (table structure, schemas, implementation details)
**AI Exposure:** Table names and schema details must NOT be exposed to candidates or employees. Business behavior may be summarized at ADMIN level.
**Last Updated:** 2026-07-02 (Employee unification — fpe_employees is now canonical)
**Source:** Entity Ownership Audit 2026-06-22 + production migration files + Python inline DDL

---

## Overview

The Fazle AI Platform uses a single PostgreSQL database shared across all subsystems. 84 verified production tables are organized into 10 business domains. Tables are created via three mechanisms: numbered SQL migrations (`db/migrations/`), subsystem-specific migrations (`fazle_payroll_engine/migrations/`), standalone SQL files (`migrations/`), and Python inline DDL (module `__init__.py` files).

**Total Table Count:** 84 verified (corrected from PKMA estimate of 43)

**Core Principles (apply to all domains):**
- Every mutation requires an audit trail
- Phone numbers are always normalized before storage (3 variants tried: `01XXXXXXXXX`, `880XXXXXXXXX`, `+880XXXXXXXXX`)
- No hard deletes; use deactivation, soft-delete, or status flags
- Idempotency keys protect all financial and notification operations
- Never expose table names, column names, or schema details to candidates or employees

**Employee Identity (2026-07-02):** `fpe_employees` is now the single canonical
employee table. It carries both identity fields (phone, name) and HR profile
fields (`basic_salary`, `designation`, `joining_date`). All employee creation
paths (WhatsApp parser, escort confirmation, frontend Add Employee) now create
or link `fpe_employees` rows. Cross-reference with `wbom_employees` is
maintained via `wbom_employee_id` soft-link. Migration 011 backfilled all 178
WBOM employees into FPE.

---

## Migration Governance (added 2026-07-08, System Consolidation Sprint — Task 4)

**Status:** first safe consolidation step per `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 / Report 7 Tier 2 Task 2. This section declares policy only — **no migration files have been renamed, renumbered, dropped, or altered as part of this step**, and no production schema was changed.

### Canonical path going forward

**`db/migrations/` + `db/migrate.py` is the single canonical, tracked migration path for all new schema changes, effective immediately.** It is the only one of the four schema-creation mechanisms with a real tracking table (`schema_migrations`), transactional per-file apply, and dry-run support. **Every new schema change — for any domain (payroll, escort, social, AI, or otherwise) — must land as a new, sequentially-numbered `db/migrations/0NN_*.sql` file, using the next never-before-used integer.** As of 2026-07-08 that next integer is `029` (see "Known duplicate/missing numbers" below for why — do not reuse an existing number even where one looks "free").

### Legacy / historical paths (do not add new files here; do not delete yet)

These three remain as historical, unreconciled sources until a dedicated reconciliation pass (Report 4's recommended strategy) is scheduled and executed:

1. **`/home/azim/core/migrations/`** (6 files, `002`–`005` + 2 unnumbered schema files, no `001`) — has **no automated execution path**; historically applied via manual `psql -f`. Legacy/historical. Do not add new files here.
2. **`/home/azim/core/modules/fazle_payroll_engine/migrations/`** (14 files, own independent `001`–`014` sequence) — **re-run in full, statement-by-statement, on every app startup** (`fazle_payroll_engine/__init__.py:25-38`), with per-statement exceptions swallowed as warnings. Not tracked in `schema_migrations`. Legacy/historical. Do not add new files here.
3. **Inline Python DDL** in `modules/contact_sync/__init__.py`, `modules/social_auto_reply/__init__.py`, `modules/bridge_poller/__init__.py`, `modules/wa_chat_frontend/__init__.py` — all `CREATE TABLE IF NOT EXISTS`, re-executed at every startup, genuinely idempotent, not tracked. Treated as a deliberate idempotent-bootstrap pattern, not a restructuring target for now. Do not add new inline DDL to new modules going forward — use `db/migrations/` instead.

### Known duplicate/missing numbers in `db/migrations/` (documented, not renamed)

- **Duplicate `012`**: `012_ai_readonly_views.sql` and `012_queue_arbiter.sql` both exist. **Not renamed** — `schema_migrations` keys on full filename, not numeric prefix, so there is no runtime collision; renaming either file risks orphaning or re-triggering already-applied production history.
- **Duplicate `020`**: `020_escort_roster_employee_fk.sql` and `020_preflight_guard_tables.sql` both exist. Same reasoning — **not renamed**.
- **Missing `009`**: no `009_*.sql` file exists in `db/migrations/`. **Intentionally left as a gap, not backfilled** — inventing content for a number retroactively risks someone assuming it must exist and re-deriving the wrong content. If you are reading this and wondering "where is 009," the answer is: it never existed or was removed before this repository's current history; there is no production impact.
- **`023` fragmented into 5 files**: `023_c0d_001` through `023_c0d_004` plus `023_model_router_tables.sql`. This follows the same "new suffixed files, not renumbering" precedent already set by this repo's own Sprint-C0D pattern — the right approach, kept as-is.

**Rule going forward:** filename is the uniqueness key, not the leading number. Never assume the numeric prefix alone identifies a migration or that gaps must be filled.

### Known cross-directory schema-collision risk — re-investigated 2026-07-13, downgraded from "unknown severity" to "confirmed harmless"

`SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 found `ALTER TABLE` touches on the same 5 tables (`fpe_cash_transactions`, `fpe_income_transactions`, `escort_roster_entries`, `wbom_escort_programs`, `fazle_payment_drafts`) from more than one of the three governed directories, plus a fully duplicated schema definition for all 8 `social_auto_reply` tables (identical in both `db/migrations/013_social_auto_reply.sql` and the inline DDL in `modules/social_auto_reply/__init__.py`).

A table-by-table, file:line re-audit (2026-07-13, full detail in `db/MIGRATION_INVENTORY.md`) found **zero actually-conflicting statements** — every overlap is either the *same* column/constraint added twice, both idempotent (harmless, e.g. `fazle_payment_drafts.expires_at`, added by both `fazle_payroll_engine/migrations/009` and `db/migrations/021`, the latter's own comment already acknowledging the duplication), or *different*, non-overlapping columns added by different directories to the same table (harmless, just uncoordinated). `fazle_payroll_engine/migrations/` is confirmed live (re-run every restart via `start_fpe()`), not dead code, so these touches are real but not dangerous.

**One separate, unrelated finding from the same pass**: `db/migrations/003c_schema_align.sql` itself (not a cross-directory issue) has 8 non-idempotent `DROP CONSTRAINT`/`ADD CONSTRAINT` pairs and 2 unguarded `RENAME COLUMN` statements — would error if ever replayed against a database where it already ran. Already applied to production once; this is a replay/disaster-recovery-rebuild risk, not a current-state risk. Not fixed — deliberately left as an Owner decision (retrofit old migration files for idempotency, or treat them as immutable historical record and handle replay differently) rather than unilaterally edited.

**Practical takeaway: the reconciliation checklist below remains worth doing for maintainability, but is no longer an urgent correctness fire** — nothing here has been found to actually corrupt data or diverge in a way that matters, only to be messier than ideal.

### Checklist for future migration reconciliation (not started — tracked here for whoever picks it up)

- [ ] Port `/home/azim/core/migrations/*.sql` into `db/migrations/` under new numbers (≥029), annotated as reconciliation-only
- [ ] Port `fazle_payroll_engine/migrations/*.sql` into `db/migrations/` under new numbers, after diffing shared-table statements (`fpe_cash_transactions`, `fpe_income_transactions`, `fazle_payment_drafts`, `wbom_escort_programs`) against actual production columns
- [ ] Retire the FPE inline-DDL startup loop once its migrations are ported and confirmed already-applied in `schema_migrations`
- [ ] Dedupe the `social_auto_reply` schema definition — pick one of the two identical sources as canonical, have the other reference it
- [ ] Write one new `db/migrations/0NN_bootstrap_core_tables.sql` for the 24 tables with no schema source of truth outside `tests/conftest.py` (see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 4 §7 and Report 9 for the full list, including two confirmed live writers: `fazle_error_log`, `llm_conversation_log`) — use real production `\d <table>` output as the column source, **not** `conftest.py`'s guess, which is already known to disagree with production
- [ ] Commit or explicitly abandon the 4 previously-untracked `db/migrations/` files (`020_preflight_guard_tables.sql`, `023_model_router_tables.sql`, `027_assistant_feature_flags.sql`, `028_assistant_ext_location_audit.sql`)
- [ ] Update this document's table count and domain list once the above is complete (see "Unresolved Schema Questions" below for the existing staleness note)

### Migration dry-run safety rule (added 2026-07-09, incident-driven)

**Incident:** During Phase 2B preparation (`db/migrations/032_client_tier_status_model.sql`), a transaction-preview dry run against production (`BEGIN; <migration>; <inspect>; ROLLBACK;`) was used to safely inspect a migration's effect before requesting Owner approval — the same method that worked cleanly for migration 031. This time, the migration contained a `SELECT setval(...)` call (fixing a sequence desync left over from migration 031's explicit-ID insert). **The `setval()` call survived the `ROLLBACK` and permanently changed production** — PostgreSQL sequence state changes (`nextval()`, `setval()`, `currval()`, and by extension `IDENTITY`/`SERIAL` counter advances) are **not transactional in Postgres by design** (this is documented upstream behavior, not a bug in this codebase) — sequences exist specifically so concurrent transactions don't block on shared counter access, and their state is deliberately excluded from MVCC/rollback semantics. Every other statement in the same migration (the two new `CREATE TABLE`s, both `ALTER TABLE ADD COLUMN`s) rolled back correctly, confirmed by direct inspection immediately after.

**Impact of this specific incident:** low/accepted — the sequence fix was itself a disclosed, beneficial correction (see migration 031/032 headers), it touches no business/client data, and the Owner explicitly accepted keeping it rather than reverting (reverting would reintroduce the exact collision risk the fix closes). It is documented here as a **process exception**, not swept under a rug: the fix reached production one step earlier than intended, without having gone through the explicit-approval gate first.

**Rule going forward:**
1. **Never include `setval()`/`nextval()`/any sequence-mutating statement inside a rollback-based production dry run.** A `ROLLBACK` will not undo it — that "preview" is actually a permanent write.
2. **Sequence-affecting statements must be validated by one of these two paths instead, never by transaction-preview against production:**
   - A read-only pre-check query (e.g. `SELECT last_value FROM the_seq`, compared against `SELECT MAX(id) FROM the_table`) to confirm a fix is needed and to compute the exact target value, executed and reasoned about *before* deciding whether to apply — no preview-and-rollback needed for a `setval`, since there's nothing to "try and see" that a read-only comparison doesn't already tell you.
   - Scratch-DB-only validation (per the standard 5-step method) for confirming the *statement's syntax and effect* — the scratch DB is disposable, so a `setval()` running there is inconsequential.
3. When a migration file mixes sequence-mutating statements with regular transactional DDL/DML, split the transaction-preview step to **only include the transactional parts** — run the sequence-mutating statement separately, after everything else has been previewed and approved, or apply it via the read-only-pre-check-then-direct-apply path above with its own explicit approval callout.

---

## Domain 1 — ESCORT

**Business Purpose:** Manage the full lifecycle of escort programs — assignment, vessel tracking, shift logging, OCR slip extraction, and handover/release matching.

**Primary Owner:** Operations team (admin and supervisor roles)
**Visibility:** Supervisor (own assignments), Admin (all programs), Developer (config/audit), Restricted (vessel manifest, client vessel data)
**AI Exposure:** Assignment status may be confirmed to assigned employee. Financial amounts and vessel client details must NOT be shared.

### Business Behavior

An escort program begins when an admin registers a vessel assignment (`wbom_escort_programs`). The escort employee is linked via FK to `wbom_employees`. As the program progresses, the roster module creates a calculated payment record (`escort_roster_entries`) that mirrors key program fields and computes pay using the management-approved formula.

**Management-Approved Formula (Decision 2026-06-22):**
```
Per-shift salary = 12,000 ÷ 30 × duty_days
Transport (conveyance) = destination-based fixed rate (see escort_calculation_config)
Food = ৳150/day (with documented time exceptions)
Net payable = salary + conveyance + food_bill − advance_deduction
```

**Mongla transport rate = ৳800 (Management Decision 2026-06-22)** — this overrides any code value that differs.

When the escort duty ends, the escort sends a release slip image. The OCR pipeline extracts fields into `escort_slip_extractions`. The roster module matches the OCR result to an open program via `escort_release_matches` (admin_action: NULL=pending → keep/reject/edit). On keep, a `fazle_payment_draft` is created in the CASH/FPE domain for payment approval.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `wbom_escort_programs` | Physical Entity | Master record: vessel, escort employee FK, dates, shift, status, payment totals, food_bill | Created on assignment; updated through program lifecycle; status: Assigned→Active→Completed |
| `escort_calculation_config` | Lookup/Config | Conveyance rates per destination (9 default rows); rarely changes | Seeded on migration; admin-editable; drives payment calculation |
| `escort_roster_entries` | Physical Entity | Calculated pay per program: total_shifts, total_days, salary, conveyance, food_bill, advance_deduction, net_payable; lifecycle: draft→active→completed→cancelled→paid | One row per program; FK `program_id → wbom_escort_programs`; FK `escort_employee_id → wbom_employees` (added migration 020) |
| `escort_roster_audit_logs` | Audit Log | Immutable JSONB old_data/new_data per roster change | Append-only; never updated |
| `escort_shift_logs` | Audit Log | Per-shift activity events: start, ongoing, release, note | Append-only; granular timeline view |
| `escort_release_matches` | Physical Entity | OCR slip → program match candidates; confidence-scored; admin_action drives lifecycle | Created by matcher; updated by admin decision; persists indefinitely |
| `escort_slip_extractions` | Physical Entity | OCR-extracted vessel slip data: mother_vessel, lighter_vessel, master_mobile, escort_name, start_date, completion_date, signatures_json, confidence, raw_text | Created on OCR pipeline completion; never modified; linked to wbom_whatsapp_messages |
| `escort_order_groups` | Physical Entity | Parent order groups parsed from WhatsApp messages; direction: outbound(admin→client) or inbound(client→admin) | Created by order parser; persistent |
| `escort_order_lighters` | Physical Entity | Per-lighter escort assignments within an order group; match_confidence, match_method, status: unmatched→matched→linked | Created by order parser; status updated on match |

### Cross-Domain Dependencies

- `wbom_escort_programs` → `wbom_employees` (EMPLOYEE domain): escort employee is an HR employee
- `escort_roster_entries` → `wbom_employees` (EMPLOYEE domain): FK added in migration 020 to resolve 29 MB error log incident
- On program completion: ESCORT → CASH/FPE: `fazle_payment_drafts.escort_program_id → wbom_escort_programs.program_id`
- Escort pay contributes to PAYROLL: `wbom_payroll_run_items.source_table=escort_roster_entries`
- OCR slip images arrive via MESSAGING domain (`wbom_whatsapp_messages`)

### Current Uncertainties

- `escort_slip_extractions.signatures_json` format: 3 signature types mentioned in OCR code (`_classify_slip`); exact JSON structure not confirmed
- OCR confidence threshold below which admin review is mandatory: not formally documented; code uses 40 as low-confidence threshold for UI warning

**Source Modules:** `modules/escort/__init__.py`, `modules/ocr_processor/__init__.py`, `modules/escort_roster/`
**Migration Files:** `migrations/escort_roster_schema.sql`, `migrations/escort_history_schema.sql`, `db/migrations/001_safe_mode_and_escort_extractor.sql`, `db/migrations/020_escort_roster_employee_fk.sql`
**Related Articles:** `06_developer_system/ocr_engine.md`, `05_workflows/release_slip_workflow.md`

---

## Domain 2 — EMPLOYEE

**Business Purpose:** Maintain the authoritative record of all active and inactive employees — their identifiers, financial payment channels, attendance, requests, and cases.

**Primary Owner:** Admin / HR function
**Visibility:** Employee (own record summary), Supervisor (team records), Admin (full records), Restricted (NID, bank account, exact mobile, salary)
**AI Exposure:** Employee name and designation may be confirmed to the employee themselves. NID, bank account, bkash/nagad numbers, and salary figures must NEVER be exposed via AI responses.

### Business Behavior

`wbom_employees` is the HR master record. Every financial, attendance, and escort domain uses it as the person anchor via `employee_id` FK. Employee mobile numbers are stored in multiple formats; the identity brain tries all three normalized variants before resolving to "not an employee."

Attendance is recorded daily per employee with UNIQUE constraint on (employee_id, attendance_date) — one row per day, preventing duplicate recording. Requests (leave, advance, grievance) and cases (incidents) are attached per employee and managed by supervisors.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `wbom_employees` | Physical Entity | HR master: employee_mobile UNIQUE, designation (default: Security Guard), status (Active/Inactive), basic_salary, bkash_number, nagad_number, nid_number, bank_account, joining_date | Created on hiring; status toggled on exit; never hard-deleted |
| `wbom_contacts` | Physical Entity | WhatsApp contact directory: whatsapp_number, contact_name, contact_type; also used by `wbom_escort_programs.contact_id` for client contacts | Created/updated by contact sync; referenced by escort programs |
| `wbom_attendance` | Physical Entity | Daily attendance: UNIQUE(employee_id, attendance_date); status Present/Absent; check_in/out times | One row per employee per day; created by admin command or daily auto-record |
| `wbom_employee_requests` | Physical Entity | Leave, advance, grievance requests: request_type, amount, status (pending/approved/rejected) | Created by employee request; resolved by admin; never hard-deleted |
| `wbom_cases` | Physical Entity | Incident/case records per employee: case_type, description, status (open/closed) | Created by admin on incident; closed when resolved |

### Cross-Domain Dependencies

- ALL financial domains (PAYROLL, CASH/FPE, ESCORT) use `wbom_employees.employee_id` as person anchor
- IDENTITY domain resolves phone → employee by querying `wbom_employees` (Step 8 of identity algorithm)
- FPE has its own `fpe_employees` table; soft-linked to `wbom_employees.employee_id` via phone normalization

**Source Modules:** Python inline DDL (conftest.py confirmed)
**Related Articles:** `06_developer_system/identity_brain.md`, `06_developer_system/fpe_overview.md`

---

## Domain 3 — PAYROLL

**Business Purpose:** Compute, approve, and track monthly employee salaries including all components: basic pay, escort program allowances, advances, deductions, and final net salary.

**Primary Owner:** Accountant / Admin
**Visibility:** Restricted (all salary figures, payment references), Admin (approval actions and status)
**AI Exposure:** Salary figures must NEVER be exposed via AI. Payroll status (e.g., "payroll has been submitted") may be confirmed to admin only.

### Business Behavior

Payroll runs are computed monthly per employee. The `daily_payroll_compute` scheduler job runs at 02:00 daily and computes pending payroll. The approval FSM governs the lifecycle: draft → submitted → approved → locked → paid.

`wbom_payroll_run_items` stores every component (basic, escort allowance, advances deducted, etc.) as signed line items (`sign +/-`), with a `source_table/source_id` soft reference to the originating record. The approval log is immutable — every state transition is recorded with actor and reason.

**Important:** `payout_idempotency_key` on `wbom_payroll_runs` prevents double disbursement.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `wbom_payroll_runs` | Physical Entity | Monthly computation: basic_salary, total_programs, per_program_rate, program_allowance, total_advances, total_deductions, gross_salary, net_salary; `payout_idempotency_key` UNIQUE | Created by scheduler; status draft→submitted→approved→locked→paid |
| `wbom_payroll_run_items` | Physical Entity | Per-employee line items: component_type, amount, sign (+/-), source_table, source_id | Created with payroll run; immutable after locked |
| `wbom_payroll_approval_log` | Audit Log | Immutable approval transitions: action, from_status, to_status, actor, reason, payload_json | Append-only; never modified |
| `wbom_salary_records` | Physical Entity | Historical salary records per month: salary_month, amount, status, payment_date | Created on payroll completion; persistent salary history |
| `wbom_staging_payments` | Staging | Pre-confirmation staging for WhatsApp-parsed payments: extracted_name, extracted_mobile, amount, matched_employee_id (FK), status (pending/approved/rejected), idempotency_key | Temporary; consumed when approved → becomes `fpe_cash_transactions` via `create_transaction()`. `fpe_staging_payments` does NOT exist — this is the only staging table (U-03 resolved 2026-06-23). |

### Cross-Domain Dependencies

- `wbom_payroll_runs.employee_id → wbom_employees` (EMPLOYEE domain)
- `wbom_payroll_run_items.source_table=escort_roster_entries` (ESCORT domain soft-reference)
- `wbom_payroll_run_items.source_table=fpe_cash_transactions` (CASH/FPE domain soft-reference)

**Source Modules:** Python inline DDL (conftest.py confirmed), `modules/payroll_logic/__init__.py`
**Related Articles:** `04_business_rules/payroll_rules.md`, `06_developer_system/fpe_overview.md`

---

## Domain 4 — CASH / FPE (Fazle Payroll Engine)

**Business Purpose:** Automated ingestion, parsing, and immutable ledger recording of WhatsApp-based cash transactions. The FPE subsystem operates independently from the main WhatsApp router, maintains its own employee directory, and enforces a zero-loss invariant on every payment message.

**Primary Owner:** Accountant / Admin (review); Developer (infrastructure)
**Visibility:** Restricted (all ledger, transaction, and employee data), Admin (review queues, correction log), Developer (FPE infrastructure tables)
**AI Exposure:** No FPE table data, transaction amounts, or employee financial records may be exposed via AI responses to any role except admin with explicit context.

### Business Behavior

The FPE subsystem has 5 asyncio workers (all ACTIVE — see `fpe_overview.md`). All WhatsApp messages containing financial content are enqueued in `fazle_message_queue` and separately tracked in `fpe_wa_messages` (immutable source copy). The per-message FSM (`fpe_message_processing_state`) tracks processing state: pending → parsing → parsed → accounting → done | skipped | failed.

**Zero-Loss Invariant:** Any message that cannot be matched to an employee goes into `fpe_unmatched_messages` — a zero-loss review queue that an admin must clear. Messages are NEVER silently dropped. Every accounting action is recorded in `fpe_accounting_audit_logs` (immutable).

The FPE maintains its own employee directory (`fpe_employees`) separate from the HR master (`wbom_employees`). After migration 008, these are soft-linked: `fpe_employees.wbom_employee_id → wbom_employees.employee_id` (nullable FK matched via phone last-10 normalization).

**Status casing differs by table — do not assume one convention across both:** `wbom_employees.status` is Title-case (`Active`/`Inactive`); `fpe_employees.status` is lowercase (`active`/`inactive`). Any query comparing across both (e.g. `modules/fazle_payroll_engine/employee.py`'s WBOM cross-lookup) must compare case-insensitively (`LOWER(status)`) when checking `wbom_employees`, and can compare lowercase directly against `fpe_employees`. A 2026-07-15 fix corrected exactly this: a case-sensitive `!= 'inactive'` literal against `wbom_employees.status` was a permanent no-op (Postgres text comparison is case-sensitive, so `'Inactive' != 'inactive'` was always true), meaning Inactive WBOM employees were never actually excluded from that lookup.

**Payment Draft Lifecycle (cross-domain):**
Escort program completion → `fazle_payment_drafts` created (ST) → admin approves → `fpe_cash_transactions` written via `create_transaction()` + `escort_roster_entries` status updated.

**Immutable Ledger Rule:** `fpe_cash_transactions` and `fpe_income_transactions` are NEVER modified after INSERT (financial columns). Corrections use counter-rows: a new reversal row references the original via `reversed_txn_id` self-FK. `wbom_cash_transactions` is legacy archive only (Owner Directive 2026-06-29).

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `fpe_wa_messages` | Physical Entity | Immutable raw WhatsApp messages; source of truth for all FPE processing | Created on ingest; never modified |
| `fpe_message_processing_state` | Physical Entity | Per-message FSM: processing_status, worker assignment, error_count | Created on first FPE contact; mutated during processing; terminal on done/skipped/failed |
| `fpe_parser_results` | Physical Entity | Parser output per message: parsed amounts, employee name, confidence | Created when parser succeeds; persistent |
| `fpe_employees` | Physical Entity | FPE-specific employee directory: primary_phone, display_name, `wbom_employee_id` (nullable soft-link) | Created on first message from new sender; never hard-deleted |
| `fpe_employee_aliases` | Physical Entity | Multi-phone and name aliases per FPE employee; supports name variations and secondary numbers | Created on alias detection; persistent |
| `fpe_employee_resolution_links` | Audit Log | Canonical merge tracking: records when two FPE employees are merged; immutable once created | Append-only |
| `fpe_employee_review_queue` | Runtime Queue | Merge candidates awaiting admin decision; populated when normalization produces ambiguous match | Consumed when admin resolves; row updated to resolved |
| `fpe_cash_transactions` | Audit Log | Immutable verified transaction ledger: amount, employee, date, payment_method, idempotency | INSERT-only; corrections via counter-row with `reversal_of` FK |
| `fpe_income_transactions` | Audit Log | Immutable income (client payment received) ledger | INSERT-only |
| `fpe_employee_ledger` | Physical Entity | Running totals per employee per month: total_received, total_advances, net | Recalculated but persisted; updated on each new transaction |
| `fpe_unmatched_messages` | Runtime Queue | Zero-loss review queue: messages that could not be matched to any FPE employee | Created when match fails; consumed when admin resolves |
| `fpe_accounting_audit_logs` | Audit Log | Immutable accounting audit trail: every accounting action recorded with actor and result | Append-only |
| `fpe_sync_checkpoints` | Infrastructure | Bridge sync progress: last synced timestamp per bridge JID | Updated by sync workers; drives incremental sync |
| `fpe_gap_scan_runs` | Infrastructure | Gap scan tracking: ID-based diff scans for missed messages | Updated by gap_scan_loop worker |
| `fpe_normalization_audit_logs` | Audit Log | FPE-specific phone normalization audit: input, output, method | Append-only |
| `fpe_processing_diagnostics` | Audit Log | Processing latency and outcome per message: duration_ms, success/fail | Append-only; operational monitoring |
| `fpe_review_audit_logs` | Audit Log | Admin promote/dismiss action log on `fpe_employee_review_queue` | Append-only |
| `fpe_transaction_repairs` | Audit Log | Repair records for manually corrected FPE transactions | **U-02: Unresolved — table created by standalone repair script; production existence unconfirmed** |
| `fpe_cash_transactions` | Physical Entity | **Canonical** cash transaction record (Owner Directive 2026-06-29): employee_id FK, program_id, amount, payout_phone, txn_category, source, transaction_status, approval_status; `reversed_txn_id` self-FK for counter-row corrections; `deleted_at`/`deleted_by` for soft-delete | Created on payment via `create_transaction()`; corrections via new reversal row |
| `wbom_cash_transactions` | Legacy Archive | Historical cash transaction record (pre-2026-06-29). No new rows written. Data migrated to `fpe_cash_transactions` via `wbom_fpe_sync.py` with `legacy_wbom_transaction_id` preserving traceability. | Read-only archive |
| `fazle_payment_drafts` | Staging | Payment drafts pending admin approval: draft_type (escort_payment\|advance\|salary), all financial fields, correction chain via `correction_of` self-FK | Created by payment workflow; consumed when approved → produces `fpe_cash_transactions` via `create_transaction()` |
| `fazle_payment_correction_log` | Audit Log | Payment corrections: immutable; links payment_draft_id, original transaction, counter-transaction | Append-only |

### Cross-Domain Dependencies

- `fpe_employees.wbom_employee_id → wbom_employees` (EMPLOYEE domain soft-link via phone normalization)
- `fazle_payment_drafts.escort_program_id → wbom_escort_programs` (ESCORT domain)
- `fazle_payment_drafts.escort_roster_entry_id → escort_roster_entries` (ESCORT domain soft-link)
- `fpe_wa_messages` ← `fazle_message_queue` (MESSAGING domain — FPE pulls from central queue)
- `fpe_cash_transactions` referenced by PAYROLL `wbom_payroll_run_items` (soft-reference via source_table/source_id)

**Source Modules:** `modules/fazle_payroll_engine/`, `tools/repair_fpe_transactions.py`
**Migration Files:** `fazle_payroll_engine/migrations/001–009`
**Related Articles:** `06_developer_system/fpe_overview.md`

---

## Domain 5 — RECRUITMENT

**Business Purpose:** Manage the candidate intake funnel — conversation-based data collection, scoring, and funnel stage tracking for WhatsApp and Facebook recruitment inquiries.

**Primary Owner:** Admin (recruitment management)
**Visibility:** Admin (session management, scores), Restricted (candidate personal data — name, phone, address, age)
**AI Exposure:** The AI may conduct recruitment conversations and collect intake data. Scores, score_bucket labels, and funnel stages must NOT be shared with candidates.

### Business Behavior

When an unknown contact sends a recruitment-style message, a `fazle_recruitment_sessions` record is created with phone UNIQUE. The recruitment flow module guides the conversation through steps: name → age → area → job_preference → experience → confirm. On completion, a score is computed and the contact advances to scored/assigned/hired/rejected stages.

**Management Decision (BR-25):** Age range for eligibility = **18–55 years**. This is the authoritative value. Any code or document showing 18–45 is outdated.

The recruitment session is the temporary state machine for data collection. The permanent profile is stored in `user_profiles` (AI domain) after session completion.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `fazle_recruitment_sessions` | Physical Entity | Candidate intake state: phone UNIQUE, step (name→age→area→experience→confirm), funnel_stage (collecting→scored→assigned→hired→rejected), score (integer), score_bucket (hot/warm/cold), source (whatsapp/meta/bridge1/bridge2) | Created on first recruitment contact; updated through conversation; terminal on hired/rejected |

**Unresolved Schema Question U-01:** Migration 003 references `wbom_candidates(candidate_id)` as FK from `fazle_recruitment_sessions`. This table does not appear in current production schema (conftest mirror has no such FK). Status of `wbom_candidates` table is unknown — may be a legacy table dropped during schema evolution, or still present in production. Marked as Pending Verification.

### Cross-Domain Dependencies

- On recruitment completion → `user_profiles` created/updated (AI domain)
- Identity brain (IDENTITY domain) assigns `role=candidate` when active session exists (Step 10)
- Recruitment Q&A content lives in `fazle_knowledge_base` (AI domain)

**Source Modules:** `modules/recruitment_flow/`, `db/migrations/002_align_tables.sql`
**Related Articles:** `06_developer_system/identity_brain.md`, `02_recruitment/`

---

## Domain 6 — IDENTITY

**Business Purpose:** Resolve every inbound contact to exactly one role before any AI response, draft creation, or backend write. Maintain the authoritative phone→role mapping, admin registry, and contact deduplication system.

**Primary Owner:** Admin (role management)
**Visibility:** Restricted (phone→role mappings, admin credentials, seed data), Admin (role management UI), Developer (algorithm configuration)
**AI Exposure:** Role assignments, phone numbers, confidence scores, and admin credentials must NEVER be exposed via AI responses. Role behavior (e.g., "admin messages go to review") may be described abstractly.

### Business Behavior

The identity brain runs an 11-step priority algorithm on every inbound message (see `identity_brain.md`). Steps 1–9 read from `fazle_contact_roles`. Step 8 (employee) also queries `wbom_employees` with three phone variants. The resolved role is stamped onto `wbom_whatsapp_messages.identity_role` and carried through the routing pipeline.

`fazle_contact_roles` contains 23 seeded production phone→role rows (5 Supervisors, 3 Escort Buyers, 1 Accountant, 2 Family, 1 Vendor, 2 VIP Clients, 8 Repeat Clients). These are seeded in migration 004 and are Restricted (never expose specific phone numbers or role assignments to AI).

Admin accounts in `fazle_admins` have `api_key_hash` (SHA-256) stored — never the plain key. The 5-level RBAC hierarchy is: viewer < operator < accountant < admin < superadmin. Bootstrap admin creation: phones listed in `ADMIN_NUMBERS` env var become superadmin on their first WhatsApp message (HK-41).

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `fazle_admins` | Physical Entity | Admin user accounts: phone UNIQUE, name, status (active/inactive), `api_key_hash` (SHA-256) | Created on bootstrap or admin management; status toggled; credentials rotated via hash update |
| `fazle_admin_roles` | Physical Entity | Admin→role assignments: UNIQUE(admin_id, role_name), `granted_by` traceability | Created on role grant; deleted on role revoke |
| `fazle_admin_audit` | Audit Log | Immutable command audit: actor_phone, command, args, allowed (bool), denied_reason, result_summary | Append-only; every admin command recorded regardless of outcome |
| `fazle_roles` | Lookup/Config | Role definitions: name UNIQUE, level (int), description | Seeded at init; rarely changed |
| `fazle_contact_roles` | Physical Entity | Phone→role mapping: phone UNIQUE, role, label, confidence, source (seed_rule/learning), priority; ON CONFLICT (phone) DO UPDATE | Created by seed (migration 004) or admin action; updated on re-assignment |
| `fazle_contact_aliases` | Physical Entity | Alternate phone names per contact: (phone, alias_name) PRIMARY KEY; source_bridge; tracks last_seen | Created by bridge contact sync; persistent |
| `fazle_unified_contacts` | Physical Entity | Deduplicated contact registry: phone PRIMARY KEY, display_name, source_bridge; keeps longest display_name on conflict | Created/updated by contact sync on every bridge poll |
| `phone_normalization_log` | Audit Log | Phone normalization operations: raw_phone → canonical_phone | Append-only; created by user profile system (migration 016) |

### Cross-Domain Dependencies

- `fazle_contact_roles` → MESSAGING: every `wbom_whatsapp_messages` row gets `identity_role` stamped from this table
- `fazle_admins` → MESSAGING: admin command processing reads this table to validate admin identity
- `fazle_unified_contacts` ← contact_sync (pulls from bridge1, bridge2, meta contacts via `fazle_contact_aliases`)

**Source Modules:** `modules/identity_brain/`, `modules/contact_sync/__init__.py`, `db/migrations/004_identity_brain.sql`, `db/migrations/006_critical_contact_zero_loss.sql`
**Related Articles:** `06_developer_system/identity_brain.md`, `06_developer_system/role_permissions.md`

---

## Domain 7 — MESSAGING

**Business Purpose:** Ingest, route, queue, and deliver WhatsApp messages across three bridges (bridge1, bridge2, meta). Maintain deduplication, draft review, outbound delivery with retry, and safety enforcement.

**Primary Owner:** Developer (infrastructure), Admin (queue management and draft review)
**Visibility:** Admin (queues, drafts, incidents), Developer (bridge infrastructure, dedup tables), Restricted (message content)
**AI Exposure:** Message content, sender phone numbers, and queue internals must NOT be exposed via AI responses. Draft review counts and queue health status may be summarized to admin.

### Business Behavior

All inbound WhatsApp messages are deduplicated via `message_hash` UNIQUE on `wbom_whatsapp_messages` before any routing occurs. The central message queue (`fazle_message_queue`) receives all messages with an `idempotency_key` guard — preventing double-enqueue across bridge restarts.

Outbound messages go through `fazle_outbound_queue` (RQ) with status pending → sending → sent | failed → dlq. Exponential backoff governs retry timing. The DLQ alert job fires every 15 minutes if actionable DLQ > 0.

In safe mode (`AI_SAFE_MODE=true`) or for draft-always roles, AI replies go to `fazle_draft_replies` instead of the outbound queue. Admins review drafts via the wa_chat_frontend; approved drafts move to `fazle_reviewed_replies` for learning and then to `fazle_outbound_queue` for delivery.

The `outbound_safety_incidents` table records every safety block (prompt injection, poison filter, flood detection). These records are immutable and Admin-visible only.

**Important distinction (C-06):** `fazle_service_heartbeats` (general service liveness → SYSTEM domain) and `fazle_bridge_heartbeats` (WhatsApp bridge-specific liveness → MESSAGING domain) are two separate tables.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `wbom_whatsapp_messages` | Physical Entity | Inbound WhatsApp message log: sender_number, message_body, direction, platform, `message_hash` UNIQUE (dedup), `identity_role` (stamped by identity brain), `critical_contact` flag | Created on ingest; never hard-deleted; altered 4 times in migrations |
| `fazle_outbound_queue` | Runtime Queue | Outbound message queue: recipient, body, status (pending→sent→failed→dlq), `idempotency_key` UNIQUE, attempts, max_attempts, next_retry_at | Created on enqueue; consumed when sent; dlq rows retained for investigation |
| `fazle_draft_replies` | Runtime Queue | Admin-pending outbound messages: draft_type, meta JSONB, status (pending→approved→rejected); `reviewed_reply_id` FK for pre-approved reply re-use | Created by AI when message meets draft criteria; consumed by admin review |
| `fazle_reviewed_replies` | Physical Entity | Admin-reviewed reply store: intent, role, match_scope, reply_text, usage_count, last_used_at; drives conservative reply re-use across similar intents | Created from approved draft; persistent; usage_count incremented on re-use |
| `fazle_message_queue` | Runtime Queue | Central inbound message queue: ALL inbound messages (bridge1/2/meta); `idempotency_key` UNIQUE; status pending→processing→done→failed | Shared backbone; FPE workers and main router both consume from here |
| `fazle_queue_leases` | Infrastructure | Distributed lease management: UNIQUE(message_id, intent), expires_at (120s TTL); status leased→processing→completed→failed→dead_letter | Created on message pickup; released on completion |
| `fazle_bridge_heartbeats` | Infrastructure | Bridge liveness: bridge_id PK (bridge1/bridge2/meta), last_seen_at, status; seeded on migration 009 | Updated by bridge watchdog; read by health_summary job |
| `bridge_poller_cursor` | Infrastructure | Bridge polling position: bridge PK, last_ts; prevents re-processing historical messages on restart | Upserted on every poll cycle; single row per bridge |
| `processed_bridge_messages` | Infrastructure | Bridge message dedup: (message_id, bridge) PRIMARY KEY; prevents double-processing across service restarts | Inserted on first processing; never updated or deleted |
| `processed_outgoing_escort_messages` | Infrastructure | Escort outbound message dedup: (message_id, bridge) PRIMARY KEY | Same pattern as processed_bridge_messages |
| `outbound_safety_incidents` | Audit Log | Safety violations: recipient, bridge, blocked_reason (prompt_injection/poison_filter/flood), message_preview; immutable | Append-only; never modified; Admin-visible only |
| `wa_chat_groups` | Physical Entity | WhatsApp group registry: name, admin_phone, members TEXT[]; managed by admin via wa_chat_frontend | Created/updated by admin; persistent |

### Cross-Domain Dependencies

- Every inbound message → IDENTITY: `fazle_contact_roles` lookup for `identity_role` stamp
- `fazle_message_queue` → CASH/FPE: FPE workers consume financial messages from here
- `llm_learning_memory.draft_id → fazle_draft_replies` (AI domain learns from reviewed drafts)
- `fazle_reviewed_replies` → AI: `fazle_reviewed_reply_memory` distills reviewed replies for learning

**Source Modules:** `modules/bridge_poller/__init__.py`, `modules/outbound/`, `modules/wa_chat_frontend/__init__.py`, `fazle_payroll_engine/migrations/009_stabilization.sql`
**Migration Files:** `db/migrations/001`, `007`, `012`, `018`, `019`, FPE migrations `008`, `009`
**Related Articles:** `06_developer_system/automation_pipeline.md`, `06_developer_system/security_rules.md`

---

## Domain 8 — SOCIAL

**Business Purpose:** Manage the Facebook/Messenger/Meta external channel for recruitment auto-replies. Operates as a completely separate subsystem from the main WhatsApp router — separate queues, separate workers, separate rate limiting.

**Primary Owner:** Admin (flagged items and escalations), Developer (infrastructure)
**Visibility:** Admin (reply queues, flagged items, escalations), Developer (rate limit, retry, backlog cursor)
**AI Exposure:** Social channel internals must NOT be exposed. Auto-reply content and fee/salary figures follow the same restrictions as WhatsApp — management-sensitive values must not be shared with candidates.

### Business Behavior

The social auto-reply system is a standalone asyncio daemon with its own 8-table database schema (all created in migration 013). It does NOT use `fazle_message_queue` or `wbom_whatsapp_messages`. All social events have their own ingestion (`social_inbox_events`), queue (`social_reply_queue`), retry (`social_retry_queue`), and audit (`social_sent_log`) tables.

Rate limiting is per-channel: 30–45 second random delay between replies. Failed replies use exponential backoff (`2^attempts` minutes). Intent risk classification determines routing: 15 safe intents → auto-reply, 16 risky intents → flagged, 3 escalation intents → flagged + admin alert.

See `social_auto_reply_system.md` for full behavioral documentation.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `social_inbox_events` | Physical Entity | All inbound social events: source of truth for social channel activity | Created on event receipt; persistent |
| `social_reply_queue` | Runtime Queue | Outbound reply queue: consumed when reply sent | Created by intent classifier; consumed by reply worker |
| `social_sent_log` | Audit Log | Immutable sent record per reply; append-only | Created on successful send; never modified |
| `social_retry_queue` | Runtime Queue | Retry tracking: links to reply_queue row; exponential backoff (`2^attempts` minutes) | Created on first failure; updated on each retry attempt |
| `social_flagged_items` | Physical Entity | Risky/escalation items: persisted for admin review; not auto-resolved | Created by risk classifier; resolved by admin |
| `social_backlog_state` | Infrastructure | Platform backlog cursor per channel: polling position for each social source | Upserted on each poll; single row per channel |
| `social_rate_limit_state` | Infrastructure | Per-channel rate limit tracking: enforces 30–45s random delay | Upserted on reply send |
| `social_thread_state` | Physical Entity | Per-sender conversation context: persistent thread memory; used for context continuity | Created on first sender contact; updated on each message |

### Cross-Domain Dependencies

- Social channel reads `fazle_knowledge_base` (AI domain) for RAG-based recruitment reply generation
- Social channel reads `fazle_contact_roles` (IDENTITY domain) for role-based routing
- Social does NOT feed into `fazle_message_queue` (MESSAGING domain) — completely separate pipeline

**Source Modules:** `modules/social_auto_reply/__init__.py` (20-file system)
**Migration Files:** `db/migrations/013_social_auto_reply.sql`
**Related Articles:** `06_developer_system/social_auto_reply_system.md`

---

## Domain 9 — AI

**Business Purpose:** Provide the RAG knowledge base, LLM learning memory, per-contact conversation memory, and behavioral configuration that power all AI-generated responses across both WhatsApp and social channels.

**Primary Owner:** Admin (KB content management), Developer (learning tables, model configuration)
**Visibility:** Admin (KB content management), Developer (learning tables, model config), Restricted (user_memory — contains conversation context with PII)
**AI Exposure:** `fazle_knowledge_base` content is designed for AI retrieval. `llm_learning_memory`, `user_memory`, and `user_profiles` must NOT be exposed directly — they are AI working memory, not user-facing data.

### Business Behavior

`fazle_knowledge_base` is the RAG knowledge store — 90+ rows seeded with recruitment Q&A, FAQ, office information, and payment confirmation templates. Every row has: category, key (UNIQUE slug), trigger_keywords TEXT[], reply_text, confidence FLOAT, is_active BOOLEAN. The BM25 index is rebuilt daily at 18:00 from both `resources/*.txt` files and active KB rows.

The learning loop: LLM generates reply → `llm_learning_memory` row created → admin reviews draft → `fazle_reviewed_replies` updated → if high-value, `promoted_to_kb=true` → KB updated. `fazle_reviewed_reply_memory` is the distilled re-use store from reviewed replies (separate from `fazle_reviewed_replies` in MESSAGING domain).

`user_profiles` covers ALL contact types (buyer, employee, escort, supervisor, candidate, vendor, family, unknown) — not just recruitment candidates. It is the long-term memory anchor for `user_memory`. Both are Restricted.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `fazle_knowledge_base` | Physical Entity | RAG knowledge base: category, key UNIQUE, trigger_keywords TEXT[], reply_text, confidence FLOAT, is_active; ON CONFLICT (key) DO UPDATE | Created/updated by admin or migration seed; never hard-deleted; `is_active=false` to retire |
| `llm_learning_memory` | Physical Entity | LLM generated reply log: provider (github_models/ollama), model, trigger_text, intent, role, context_used, reply_text; `draft_id` FK → fazle_draft_replies; `promoted_to_kb` flag; `dismissed` flag | Created on every LLM reply; updated on promote/dismiss |
| `user_memory` | Physical Entity | Per-contact conversation memory: `phone_canonical` FK → user_profiles, memory_type (personal_info/work_info/conversation_fact/pending_matter/important_event), content, confidence, source | Created by memory_extractor job; persistent; grows over interactions |
| `user_profiles` | Physical Entity | Contact profile: phone_canonical UNIQUE, name, role (all contact types), relationship_type, notes; covers ALL roles not just candidates | Created on first contact; updated as role is learned |
| `role_reply_styles` | Lookup/Config | Role-based reply style config: role UNIQUE, tone, language, max_length, greeting_template, system_prompt_addition | Seeded at init; admin-editable |
| `fazle_reviewed_reply_memory` | Physical Entity | Distilled re-use memory from reviewed replies: intent, role, trigger_hash (normalized), reply_text, use_count, is_active | Created from high-quality reviewed replies; updated on reuse |

### Cross-Domain Dependencies

- `fazle_knowledge_base` → MESSAGING + SOCIAL: both channels query this table for RAG retrieval
- `llm_learning_memory.draft_id → fazle_draft_replies` (MESSAGING domain traceability)
- `user_profiles.phone_canonical` is referenced by `user_memory.phone_canonical` (internal FK)
- Memory extractor job reads `wbom_whatsapp_messages` (MESSAGING domain) to extract facts for `user_memory`

**Source Modules:** `modules/rag/__init__.py`, `app/llm.py`, `modules/memory_extractor/`, `modules/kb_upload/`
**Migration Files:** `db/migrations/002`, `014`, `015`, `016`, `017`
**Related Articles:** `06_developer_system/rag_strategy.md`, `06_developer_system/automation_pipeline.md`

---

## Domain 10 — SYSTEM

**Business Purpose:** Platform infrastructure — distributed locking, runtime node registration, service liveness monitoring, dynamic configuration, backup integrity tracking, migration management, and report caching.

**Primary Owner:** Developer
**Visibility:** Developer (all system tables), Admin (heartbeat and backup monitoring)
**AI Exposure:** No system table internals may be exposed via AI responses.

### Business Behavior

`fazle_processing_locks` provides distributed advisory locks with 60-second TTL — used by all processing workers to prevent double-processing under concurrent load. The `lock_cleanup` scheduler job runs every 5 minutes to remove expired locks.

`fazle_state_version` is a PostgreSQL fallback for the Redis `INCR fazle:state_version` counter. Redis is the primary; this table is only written when Redis is unavailable. Provides optimistic locking for all domain state updates.

`fazle_runtime_settings` persists auto-reply toggle states (auto_reply.all, auto_reply.family, auto_reply.recruitment, etc.) set via wa_chat_frontend. These settings survive service restarts.

`schema_migrations` is created and maintained by `db/migrate.py` — tracks which migration files have been applied. It is the source of truth for migration state.

### Tables

| Table | Type | Key Behavior | Lifecycle |
|---|---|---|---|
| `fazle_processing_locks` | Infrastructure | Distributed advisory locks: lock_key PRIMARY KEY, locked_by, expires_at (60s TTL); all processing workers use this for concurrency control | Created on lock acquire; expires naturally; swept by lock_cleanup job |
| `fazle_runtime_nodes` | Infrastructure | Runtime process registry: node_id PK, app_name, role (worker/leader), status (online/offline/degraded), last_seen, queue_depth, active_requests | Updated by each running process on heartbeat; stale entries swept |
| `fazle_runtime_settings` | Lookup/Config | Dynamic key-value settings: key PK, value; 7 auto-reply toggle keys seeded | Created on module init; updated by admin via /api/wa/settings PATCH |
| `fazle_state_version` | Infrastructure | PostgreSQL fallback state counter: version BIGINT; MAX(version) used for optimistic locking when Redis unavailable | Inserted (never updated) when Redis is down; seeded with version=0 |
| `fazle_service_heartbeats` | Infrastructure | General service liveness: service_name PRIMARY KEY, last_seen; written by social daemon and other services | Upserted on service heartbeat |
| `fazle_db_backups` | Audit Log | Backup integrity records: filename, path, size_bytes, sha256, status (ok/failed), duration_ms, started_at, finished_at, rotated_at | Append-only per backup run; 14 daily + 8 weekly retained |
| `fazle_report_cache` | Cache | Report caching: cache_key PRIMARY KEY, payload JSONB, expires_at; rebuildable from source data | Created on report generation; expired and rebuilt on next request |
| `schema_migrations` | Infrastructure | Migration run tracking: created by migration runner; records all applied migrations | Managed by `db/migrate.py` exclusively |
| `fazle_contact_sync_log` | Infrastructure | Per-bridge contact sync metadata: bridge PRIMARY KEY, synced_at, contacts_upserted | Upserted on each sync run; operational metadata only |

**Source Modules:** `db/migrate.py`, `modules/wa_chat_frontend/__init__.py`, `shared/locks.py`
**Migration Files:** `db/migrations/010`, `011`, `012`; Python inline DDL in multiple modules
**Related Articles:** `06_developer_system/automation_pipeline.md`, `06_developer_system/developer_notes.md`

---

## Cross-Domain Relationship Summary

| Relationship | Direction | Mechanism |
|---|---|---|
| Identity stamps every inbound message | IDENTITY → MESSAGING | `fazle_contact_roles.phone → wbom_whatsapp_messages.identity_role` |
| HR master is person anchor for all financial domains | EMPLOYEE → PAYROLL/CASH/ESCORT | `wbom_employees.employee_id` FK |
| FPE soft-links its employee to HR master | CASH/FPE ↔ EMPLOYEE | `fpe_employees.wbom_employee_id` (nullable, phone-matched) |
| Escort completion triggers payment draft | ESCORT → CASH/FPE | `fazle_payment_drafts.escort_program_id → wbom_escort_programs` |
| Payment draft approval creates transaction | CASH/FPE → CASH/FPE | `fazle_payment_drafts` → `fpe_cash_transactions` on approve via `create_transaction()` |
| FPE consumes from central message queue | MESSAGING → CASH/FPE | FPE workers poll `fazle_message_queue` |
| Both channels use same KB for RAG | AI → MESSAGING + SOCIAL | `fazle_knowledge_base` queried by both |
| AI learns from messaging draft reviews | MESSAGING → AI | `llm_learning_memory.draft_id → fazle_draft_replies` |
| Social is parallel to WhatsApp — NOT subordinate | SOCIAL ‖ MESSAGING | Separate queues, workers, and tables; shared only KB and contact_roles |
| Payroll line items soft-reference escort and cash records | PAYROLL → ESCORT + CASH/FPE | `wbom_payroll_run_items.source_table/source_id` |

---

## Unresolved Schema Questions (Pending Verification)

| # | Table | Issue |
|---|---|---|
| U-01 | `wbom_candidates` | ✅ RESOLVED 2026-06-23 — EXISTS in production. 21 columns. Schema documented in `recruitment_flow_system.md`. |
| U-02 | `fpe_transaction_repairs` | ✅ RESOLVED 2026-06-23 — EXISTS in production. 15 columns. Schema documented in `fpe_overview.md`. |
| U-03 | `wbom_staging_payments` | ✅ RESOLVED 2026-06-23 — `wbom_staging_payments` EXISTS (16 cols). `fpe_staging_payments` does NOT exist. One table only. Domain assignment C-02 (CASH/FPE) stands. |

---

## Global Mutation Rules

| Operation | Rule |
|---|---|
| Create | Validate permission; normalize phone (3 variants); prevent duplicate via UNIQUE or idempotency_key |
| Update | Preserve old value via audit log; use status flags not data overwrite |
| Delete | Prefer `is_active=false` or `status=cancelled`; hard delete requires explicit management approval |
| Merge | Record in audit log; preserve source references; use resolution_links pattern |
| Financial | Always immutable ledger; corrections via counter-row with `reversal_of` FK; idempotency key on every disbursement |
| Phone | Always normalize before storage; try all 3 variants on lookup |

---

**Revision History:**
- 2026-06-22: Wave-2B complete rewrite. Entity Ownership Audit applied. All 84 tables documented. Previous stub (23 lines) replaced.
- PKMA Report 17 (Database Maturity, Level 1) — this article addresses CR-03.
