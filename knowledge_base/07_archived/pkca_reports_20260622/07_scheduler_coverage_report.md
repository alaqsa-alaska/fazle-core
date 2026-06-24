---
title: PKCA Report 07: Scheduler Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 07: Scheduler Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Scheduler Overview

**Module:** `modules/scheduler/__init__.py` (690 lines)
**Engine:** APScheduler
**Timezone:** Asia/Dhaka (configurable via SCHEDULER_TIMEZONE)
**KB Coverage:** 0% — No scheduler article exists in the Knowledge Base.

---

## Complete Job Inventory

| # | Job Name | Schedule | Function | Purpose | KB Coverage |
|---|---|---|---|---|---|
| 1 | `daily_payroll_compute` | 02:00 daily | `job_daily_payroll()` | Compute payroll for all Active employees | 0% |
| 2 | `dlq_alert` | Every 15 min (env: DLQ_ALERT_INTERVAL_MIN) | `job_dlq_alert()` | Alert admin if actionable DLQ >0 in last 24h | 0% |
| 3 | `health_summary` | Every 6h | `job_health_summary()` | Alert admin if any health probe degraded | 0% |
| 4 | `agent_incident_summary` | Every 6h | `job_agent_incident_summary()` | Summarize unresolved monitoring incidents | 0% |
| 5 | `stale_escort_reminder` | 09:00 daily | `job_stale_escort_reminder()` | Alert for escort programs Active >N days (env: ESCORT_STALE_DAYS, default 30) | 0% |
| 6 | `payment_reconciliation` | Hourly | `job_payment_reconciliation()` | Re-match unmatched staging payments | 0% |
| 7 | `backup_staleness_alert` | 03:00 daily | `job_backup_staleness()` | Alert if newest DB backup >48h old (env: BACKUP_STALE_HOURS) | 0% |
| 8 | `combined_draft_cleanup` | Hourly | `job_combined_draft_cleanup()` | Expire escort + payment drafts >24h | 0% |
| 9 | `daily_memory_review` | 09:00 daily (env: MEMORY_REVIEW_HOUR) | `job_daily_memory_review()` | Extract facts from conversations via LLM | 0% |
| 10 | `rag_rebuild` | 18:00 daily (env: RAG_REBUILD_HOUR) | `job_rag_rebuild()` | Rebuild BM25 RAG index | 0% |
| 11 | `daily_admin_digest` | 08:00 daily (env: DAILY_DIGEST_HOUR) | `job_daily_admin_digest()` | Daily summary to admin via bridge1 | 0% |
| 12 | `daily_db_backup` | 02:30 daily (env: DAILY_BACKUP_HOUR:DAILY_BACKUP_MIN) | `job_daily_db_backup()` | Run pg_dump database backup | 0% |
| 13 | `lock_cleanup` | Every 5 min | `cleanup_expired_locks()` | Clean expired processing locks | 0% |
| 14 | `draft_ttl_cleanup` | Every 30 min | `expire_stale_drafts()` | Mark pending drafts as expired based on draft_ttl setting | 0% |
| 15 | `bridge_watchdog` | Every 5 min | Bridge stale check | Alert if bridge silent >10 min | 0% |

---

## Environment Overrides

| Setting | Default | Affects Job |
|---|---|---|
| `SCHEDULER_TIMEZONE` | Asia/Dhaka | All jobs |
| `PAYROLL_AUTO_COMPUTE_HOUR` | 2 | daily_payroll_compute |
| `DLQ_ALERT_INTERVAL_MIN` | 15 | dlq_alert |
| `MEMORY_REVIEW_HOUR` | 9 | daily_memory_review |
| `RAG_REBUILD_HOUR` | 18 | rag_rebuild |
| `DAILY_DIGEST_HOUR` | 8 | daily_admin_digest |
| `DAILY_BACKUP_HOUR` | 2 | daily_db_backup |
| `DAILY_BACKUP_MIN` | 30 | daily_db_backup |
| `ESCORT_STALE_DAYS` | 30 | stale_escort_reminder |
| `BACKUP_STALE_HOURS` | 48 | backup_staleness_alert |

---

## Scheduler Idempotency

All notification jobs use idempotency keys to prevent duplicate alerts:
- Format: `{purpose}-{date}` or `{purpose}-{signature}`
- Uses `outbound.enqueue()` with `idempotency_key` parameter
- **KB Coverage:** 0%

---

## Job Audit

All job runs recorded in `fazle_scheduled_jobs`:
- Fields: job_name, last_run_at, last_status, last_duration_ms, last_error, run_count
- **KB Coverage:** 0%

---

## Admin Scheduler Commands

The scheduler supports WhatsApp admin commands:
- `SCHEDULE STATUS` — Show all job statuses (requires viewer role)
- `RUN JOB <name>` — Trigger job manually (requires admin role)

**KB Coverage:** admin_operations_overview.md does not mention scheduler commands.

---

## Enrichment Recommendation

**Primary Target:** `06_developer_system/automation_pipeline.md`

Enrich with:
1. Complete job inventory table (all 15 jobs)
2. Cron schedules and timezone
3. Environment overrides table
4. Idempotency mechanism
5. SCHEDULE STATUS and RUN JOB command references
6. fazle_scheduled_jobs audit table

No new article required — `automation_pipeline.md` is the correct home.

**Coverage Score: 0%**
