---
title: PKMA Report 17 — Database Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 17 — Database Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of database knowledge in the Fazle AI Platform Knowledge Base. Database maturity is measured by how well the 43 production tables, their relationships, idempotency guarantees, advisory locks, soft-delete patterns, and query behaviors are documented and governed.

---

## Database Architecture Overview

| Aspect | Detail |
|---|---|
| Engine | PostgreSQL |
| Table Count | 43 production tables (identified by PKCA) |
| Schema Naming | wbom_* (business objects), fazle_* (platform objects) |
| KB Article | `06_developer_system/database_rules.md` — abstract only, not enriched in Wave-1 |
| Overall DB Coverage | <3% post-Wave-1 (database_rules.md was P2 — not targeted) |

---

## Database Component Assessments

---

## DB-01 — Table Inventory (43 tables)

**KB Article:** `06_developer_system/database_rules.md`
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| Tables Documented | 0 of 43 |
| Table Schema | Not in KB |
| Column Definitions | Not in KB |
| Index Definitions | Not in KB |
| Foreign Key Relationships | Not in KB |
| PKCA Reference | 15_knowledge_extraction_tracker.md (table names listed) |

**Tables known from production evidence (PKCA reads) but not in KB:**

**wbom_* (Business Objects):**
- `wbom_employees` — employee master record
- `wbom_attendance` — daily attendance log
- `wbom_cash_transactions` — cash/payment transactions
- `wbom_payroll_runs` — payroll computation results
- `wbom_payroll_approval_log` — payroll state transitions
- `wbom_escort_programs` — escort program records
- `wbom_contacts` — contact directory
- `wbom_messages` — inbound message log
- `wbom_outbound_messages` — outbound queue
- `wbom_reminders` — scheduled reminders

**fazle_* (Platform Objects):**
- `fazle_admins` — admin/RBAC user records
- `fazle_draft_replies` — pending drafts (all types)
- `fazle_recruitment_sessions` — candidate sessions
- `fazle_blocked_numbers` — blocked sender list
- `fazle_scheduled_jobs` — APScheduler job store
- `fazle_reminders_sent` — sent reminder dedup
- `fazle_reconciliation_log` — payment reconciliation log
- `fazle_db_backups` — backup integrity records
- `fazle_contacts` — external contact registry
- `fazle_incident_log` — safety incident records
- `outbound_safety_incidents` — prompt injection incidents

**fpe_* (FPE Tables — not in KB):**
- `fpe_messages` — FPE inbound messages
- `fpe_transactions` — FPE financial transactions
- `fpe_workers` — worker status
- `fpe_audit` — FPE audit trail

**Risk:** Critical — 43 tables completely undocumented. Any new developer must read source code to understand data model.

---

## DB-02 — Phone Lookup Multi-Variant Pattern

**KB Article:** `03_ai_identity/identity_overview.md` (brief reference only)
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| Pattern | SQL queries use LIKE '880%' and LIKE '01%' and exact match to cover all 3 phone variants |
| Source | `modules/identity_brain` — phone lookup |
| Documented in KB | Partially — phone normalization documented; SQL pattern not in KB |
| Production Verified | No (SQL pattern not verified by KB) |

**Risk:** Medium — phone lookup failure = wrong identity resolution.

---

## DB-03 — Idempotency Patterns

**KB Article:** Partial (in workflow articles, not in database_rules.md)
**Maturity: Level 1 (Documented — scattered)**

| Table | Idempotency Mechanism | KB Location |
|---|---|---|
| wbom_payroll_runs | UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' | salary_workflow.md (Wave-1) |
| wbom_attendance | ON CONFLICT UPDATE on (employee_id, attendance_date) | attendance_workflow.md (Wave-1) |
| wbom_outbound_messages | idempotency_key column | automation_pipeline.md (Wave-1) |
| fazle_draft_replies | No idempotency — duplicate drafts possible | NOT in KB |
| fazle_recruitment_sessions | phone_number UNIQUE | recruitment_workflow.md (partial) |

**Assessment:** Idempotency patterns are scattered across workflow articles but not consolidated in database_rules.md. The gap for fazle_draft_replies (no idempotency) is a notable undocumented behavior.
**Risk:** Medium — duplicate draft creation is a real operational risk.

---

## DB-04 — Soft-Delete Pattern

**KB Article:** None
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| Tables Using Soft-Delete | fazle_admins (USER REMOVE → disabled flag, not DELETE) |
| Other Tables | Unknown — whether wbom_employees, wbom_escort_programs use soft-delete is not documented |
| KB Documentation | 0% |
| Production Verified | No |

**Risk:** High — if soft-delete is inconsistently applied, hard deletes may corrupt referential integrity.

---

## DB-05 — Advisory Lock Pattern

**KB Article:** `06_developer_system/database_rules.md` (abstract only)
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| Purpose | Prevent concurrent payroll computation for same employee/period |
| Source | `modules/locks.acquire_advisory_lock()` |
| Cleanup | `lock_cleanup` scheduler job (J-11, every 5min) |
| Lock Key Format | NOT in KB |
| Timeout | NOT in KB |
| KB Documentation | Mentioned abstractly in database_rules.md; not enriched in Wave-1 |
| Production Verified | No |

**Risk:** Medium — advisory lock failure causes duplicate payroll records.

---

## DB-06 — Message Hash Deduplication

**KB Article:** None
**Maturity: Level 0 (Unknown)**

| Dimension | Status |
|---|---|
| Source | `modules/message_hash` or `modules/bridge_poller` |
| Behavior | SHA-256 hash of incoming message content; duplicate hash = skip processing |
| Storage | NOT documented — which table/column |
| Window | NOT documented — how long hashes are retained |
| KB Documentation | 0% |
| Production Verified | No |

**Risk:** High — if hash dedup fails, same message processed twice, creating duplicate drafts.

---

## DB-07 — Backup Integrity Record

**KB Article:** `06_developer_system/developer_notes.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Table | `fazle_db_backups` |
| Columns | filename, sha256_hash, created_at, backup_type (daily/weekly) |
| Rotation | 14 daily, 8 weekly (documented Wave-1) |
| Verification | SHA-256 integrity check on restore |
| Admin Visibility | `backup_staleness_alert` job alerts if no recent backup |
| Production Verified | Yes (Wave-1) |
| Management Decision | None for retention policy |

**Risk:** Medium — retention policy (14d/8w) not formally management-approved.

---

## DB-08 — Reconciliation Log

**KB Article:** `05_workflows/payment_workflow.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Table | `fazle_reconciliation_log` |
| Key Columns | outbound_id, sms_tail, matched_at, status |
| Match Logic | mobile-tail-11 — last 11 digits of phone number |
| Max Records Per Run | 50 (documented Wave-1) |
| Production Verified | Yes (Wave-1) |
| Management Decision | None |

**Risk:** Medium — reconciliation gaps = unmatched payments.

---

## DB-09 — Payroll Approval Log

**KB Article:** `05_workflows/salary_workflow.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Table | `wbom_payroll_approval_log` |
| Logs | Every state transition with actor, timestamp, note |
| Scope | All PAYROLL command executions |
| Production Verified | Yes (Wave-1) |
| Management Decision | CON-01 (payroll formula governs what is logged) |

**Strongest documented DB component — audit log with management backing.**

---

## Database Maturity Summary

| Component | Level | Risk |
|---|---|---|
| DB-01: 43-Table Inventory | 0 | Critical |
| DB-02: Phone Lookup Pattern | 1 | Medium |
| DB-03: Idempotency Patterns | 1 | Medium |
| DB-04: Soft-Delete Pattern | 0 | High |
| DB-05: Advisory Lock | 1 | Medium |
| DB-06: Message Hash Dedup | 0 | High |
| DB-07: Backup Integrity | 2 | Medium |
| DB-08: Reconciliation Log | 2 | Medium |
| DB-09: Payroll Approval Log | 3 | Low |

**Database Domain Average: 1.1 / 5.0**
**Level 0 count: 3 (Table Inventory, Soft-Delete, Message Hash)**
**Critical risk count: 1 (Table Inventory — 43 undocumented tables)**

---

## Database Domain Verdict

**Domain Maturity: Level 1 (Documented — minimal)**

The database domain is the weakest technical domain in the platform. With 43 production tables entirely absent from KB documentation, and 3 components at Level 0, this domain poses the highest onboarding and maintenance risk of any technical area. The payroll audit log (DB-09) is the only component with management backing.

**Minimum for Level 2:**
1. Document all 43 tables in database_rules.md (Wave-2 highest priority)
2. Consolidate idempotency patterns into database_rules.md
3. Document soft-delete and advisory lock patterns

**This domain requires the most investment to reach acceptable maturity.**

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
