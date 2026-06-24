---
title: PKCA Report 06: Database Behavior Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 06: Database Behavior Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Production Database Tables Inventory

### Core Operation Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `wbom_whatsapp_messages` | All inbound/outbound messages (with canonical_phone, message_hash columns) | INSERT always, SELECT for history | 0% |
| `wbom_employees` | Employee registry | SELECT by mobile (multiple variants), INSERT/UPDATE soft-delete | 0% |
| `wbom_escort_programs` | Vessel escort programs | INSERT (draft), UPDATE (status/close), SELECT (active lookup), remarks JSON | 0% |
| `wbom_attendance` | Daily attendance | INSERT ON CONFLICT UPDATE on (employee_id, attendance_date) | 0% |
| `wbom_cash_transactions` | All cash/payment records | INSERT with idempotency_key, SELECT advance sum | 0% |
| `wbom_contacts` | Contact directory | SELECT for identity resolution, ON CONFLICT DO UPDATE | 0% |
| `wbom_relation_types` | Contact relation categories | JOIN with wbom_contacts | 0% |
| `wbom_staging_payments` | Unmatched payment staging | INSERT, UPDATE matched_employee_id | 0% |
| `wbom_salary_records` | Salary records | SELECT by employee | 0% |

### Draft and Approval Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `fazle_draft_replies` | General message drafts (attendance, OCR, complaint, NL) | INSERT, UPDATE status, SELECT pending | 5% (mentioned) |
| `fazle_payment_drafts` | Payment/advance drafts (financial approval path) | INSERT, UPDATE, SELECT by program_id | 5% (mentioned) |

### Identity and Access Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `fazle_contact_roles` | Seed identity rules (highest trust) | SELECT by phone + is_active | 0% |
| `fazle_admins` | Admin user registry | INSERT, UPDATE status | 0% |
| `fazle_roles` | Role definitions (5 rows: viewer/operator/accountant/admin/superadmin) | Seeded on startup | 0% |
| `fazle_admin_roles` | Admin-to-role mapping | INSERT ON CONFLICT DO NOTHING | 0% |
| `fazle_admin_audit` | Admin command audit trail | INSERT per command | 0% |

### Business Logic Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `fazle_recruitment_sessions` | Recruitment funnel state | INSERT, UPDATE per step, TTL 24h | 0% |
| `fazle_knowledge_base` | KB rows for RAG | SELECT is_active=true | 0% |
| `escort_roster_entries` | Computed roster entries | INSERT ON CONFLICT UPDATE | 0% |
| `fazle_reminders_sent` | Reminder dedup | INSERT ON CONFLICT DO NOTHING | 0% |

### Payroll Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `wbom_payroll_runs` | Monthly payroll records | INSERT, UPDATE (state transitions) | 0% |
| `wbom_payroll_run_items` | Payroll line items | INSERT per compute | 0% |
| `wbom_payroll_approval_log` | Payroll state transition audit | INSERT per transition | 0% |

### Infrastructure Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `bridge_poller_cursor` | Message polling position per bridge | UPSERT per bridge | 0% |
| `processed_bridge_messages` | Message deduplication | INSERT ON CONFLICT DO NOTHING | 0% |
| `processed_outgoing_escort_messages` | Outbound message dedup | INSERT ON CONFLICT DO NOTHING | 0% |
| `outbound_safety_incidents` | Blocked-send log (poison filter hits) | INSERT | 0% |
| `fazle_outbound_queue` | Persistent outbound message queue | INSERT (idempotency_key), UPDATE (status/retry), sweep | 0% |
| `fazle_service_heartbeats` | Service liveness monitoring | UPSERT per heartbeat | 0% |
| `fazle_scheduled_jobs` | Scheduler run history | UPSERT per job run | 0% |
| `fazle_db_backups` | Backup metadata | INSERT per backup run | 0% |
| `llm_learning_memory` | LLM response memory | INSERT per reply | 0% |

### Payment Correction and Reconciliation Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `wbom_staging_payments` | Unmatched payment staging | INSERT, UPDATE matched | 0% |
| `fazle_reconciliation_log` | Payment reconciliation audit | INSERT per attempt | 0% |
| `fazle_payment_correction_log` | Payment corrections | INSERT (dormant) | 0% |

### Report and Cache Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `fazle_report_cache` | 10-minute report cache | INSERT ON CONFLICT DO UPDATE, SELECT WHERE expires_at > now() | 0% |
| `fazle_report_runs` | Report execution audit | INSERT per run | 0% |

### FPE (Fazle Payroll Engine) Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `fpe_wa_messages` | FPE WhatsApp message store | INSERT per ingested message | 0% |
| `fpe_message_processing_state` | FPE processing pipeline state | UPSERT per message | 0% |
| `fpe_cash_transactions` | FPE transaction records (separate from wbom_cash_transactions) | INSERT, UPDATE (reversal), SELECT | 0% |
| `fpe_employees` | FPE employee registry (synced from wbom_employees) | INSERT ON CONFLICT UPDATE | 0% |

### User Memory Tables

| Table | Purpose | Key Operations | KB Coverage |
|---|---|---|---|
| `user_profiles` | Per-contact profiles | INSERT ON CONFLICT DO NOTHING, SELECT | 0% |
| `user_memory` | Extracted facts per contact | INSERT per extraction | 0% |

---

## Critical Database Behavior Patterns

| Pattern | Production Behavior | KB Coverage |
|---|---|---|
| Phone multi-variant lookup | Always try 01XXXXXXXXX, 880XXXXXXXXX, +880XXXXXXXXX, unresolved: variants | 0% |
| Idempotency keys | Payment: `payment-draft:{id}` format prevents double-payment | 0% |
| Payroll idempotency | UNIQUE(employee_id, period_year, period_month) WHERE status != 'cancelled' | 0% |
| Attendance dedup | UNIQUE(employee_id, attendance_date) → ON CONFLICT UPDATE | 0% |
| Message dedup | processed_bridge_messages ON CONFLICT DO NOTHING | 0% |
| Advisory locks | Concurrent payment writes use Postgres advisory locks | 0% |
| Soft delete | wbom_employees uses status='Inactive' never hard-delete | 0% |
| FPE dual-table | wbom_cash_transactions (live) vs fpe_cash_transactions (FPE) are separate | 0% |
| Message hash | wbom_whatsapp_messages has UNIQUE INDEX on message_hash | 0% |
| Draft architecture | fazle_draft_replies (conversational) vs fazle_payment_drafts (financial) split-by-purpose | 5% |

---

## DB Coverage Summary

| Category | Tables | Covered | Coverage |
|---|---|---|---|
| Core operation | 9 | 0 | 0% |
| Drafts | 2 | 0.1 | 5% |
| Identity/access | 5 | 0 | 0% |
| Business logic | 4 | 0 | 0% |
| Payroll | 3 | 0 | 0% |
| Infrastructure | 9 | 0 | 0% |
| Payment/reconciliation | 3 | 0 | 0% |
| Reports/cache | 2 | 0 | 0% |
| FPE tables | 4 | 0 | 0% |
| User memory | 2 | 0 | 0% |
| **Total** | **43** | **~0.1** | **<1%** |

**Database Behavior Coverage: <1%**

The KB currently has `06_developer_system/database_rules.md` which lists abstract principles (frontend sync, mutation rules, audit trail) but does not document a single table by name, any idempotency pattern, or any specific DB operation.

**Enrichment Target:** `06_developer_system/database_rules.md` — add table inventory and critical patterns. No table names should be exposed in EMPLOYEE/PUBLIC articles.
