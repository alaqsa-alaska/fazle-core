---
title: PKMA Report 14 — Scheduler Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 14 — Scheduler Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of the APScheduler job system. A scheduler job is mature when its name, trigger, schedule, actor, production evidence, behavior on failure, and operational controls are documented, production-verified, and management-approved.

---

## Scheduler Architecture

| Component | Detail |
|---|---|
| Framework | APScheduler (AsyncIOScheduler) |
| Source | `modules/scheduler/__init__.py` (691 lines) |
| Total Jobs | 15 (all confirmed ACTIVE — verified by PKCA scheduler read) |
| Timezone | Asia/Dhaka |
| Job Store | SQLAlchemyJobStore (`fazle_scheduled_jobs` table) |
| Env Override | `SCHEDULER_ENABLED=false` disables all jobs |
| Admin Controls | SCHEDULE STATUS (list), RUN JOB <name> (manual trigger) |
| KB Article | `06_developer_system/automation_pipeline.md` (Wave-1 scheduler section) |

---

## Per-Job Maturity Assessment

---

### J-01: daily_payroll_compute

| Dimension | Status |
|---|---|
| Schedule | 02:00 daily |
| Function | `modules/payroll.run_daily_compute()` |
| Behavior | Auto-creates draft payroll entries for active employees |
| Failure Path | Logged; payroll not created; admin can trigger via PAYROLL START |
| Documented in KB | Yes (Wave-1 scheduler table) |
| Production Verified | Yes — confirmed ACTIVE in `start_scheduler()` |
| Management Decision | CON-01 (formula); no explicit scheduler decision |
| Risk | Medium — financial; silent failure possible |

**Maturity: Level 2 (Production Verified)**
**Gap to Level 3:** No explicit management decision approving the 02:00 auto-compute schedule.

---

### J-02: dlq_alert

| Dimension | Status |
|---|---|
| Schedule | Every 15 minutes |
| Function | `modules/outbound.alert_dlq_messages()` |
| Behavior | Notifies admin if any outbound messages in DLQ |
| Failure Path | Silent (alert itself may fail) |
| Documented in KB | Yes (Wave-1 outbound queue section) |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Medium — DLQ failures can mean undelivered messages |

**Maturity: Level 2 (Production Verified)**

---

### J-03: health_summary

| Dimension | Status |
|---|---|
| Schedule | Every 6 hours |
| Function | `modules/health.send_health_summary()` |
| Behavior | Sends platform health report to superadmin |
| Documented in KB | Yes (table row only) |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Low |

**Maturity: Level 2 (Production Verified)**

---

### J-04: agent_incident_summary

| Dimension | Status |
|---|---|
| Schedule | Every 6 hours |
| Function | `modules/agent.summarize_incidents()` |
| Behavior | Summarizes AI/agent incidents for review |
| Documented in KB | Yes (table row only) |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Low |

**Maturity: Level 2 (Production Verified)**

---

### J-05: stale_escort_reminder

| Dimension | Status |
|---|---|
| Schedule | 09:00 daily |
| Function | `modules/escort_lifecycle.send_stale_reminders()` |
| Behavior | Sends reminder for programs Active/Assigned >30 days (ESCORT_STALE_DAYS env) |
| Dedup | `fazle_reminders_sent` table prevents repeat alerts per program |
| Documented in KB | Yes (escort_workflow.md stale alert section — Wave-1) |
| Production Verified | Yes |
| Management Decision | None explicitly for stale threshold; HK-23 adjacent |
| Risk | Low — operational alerting |

**Maturity: Level 2 (Production Verified)**
**Note:** ESCORT_STALE_DAYS env var allows override without code change; not documented in developer_notes.md.

---

### J-06: payment_reconciliation

| Dimension | Status |
|---|---|
| Schedule | Every hour |
| Function | `modules/payment_reconciliation.run_reconciliation()` |
| Behavior | Matches outbound payment records to bKash/Nagad SMS confirmations using mobile-tail-11; max 50 records per run; logs to `fazle_reconciliation_log` |
| Documented in KB | Yes (payment_workflow.md reconciliation section — Wave-1) |
| Production Verified | Yes |
| Management Decision | None formal |
| Risk | High — financial reconciliation; silent errors = unreconciled payments |

**Maturity: Level 2 (Production Verified)**

---

### J-07: backup_staleness_alert

| Dimension | Status |
|---|---|
| Schedule | 03:00 daily |
| Function | `modules/backup.check_backup_staleness()` |
| Behavior | Alerts superadmin if no DB backup in expected window; reads `fazle_db_backups` |
| Documented in KB | Yes (developer_notes.md backup system section — Wave-1) |
| Production Verified | Yes |
| Management Decision | None formal |
| Risk | Medium — data protection alerting |

**Maturity: Level 2 (Production Verified)**

---

### J-08: combined_draft_cleanup

| Dimension | Status |
|---|---|
| Schedule | Every hour |
| Function | `modules/drafts.cleanup_expired_drafts()` |
| Behavior | Expires pending payment drafts and attendance drafts past 24h TTL |
| Documented in KB | Yes (payment_workflow.md, attendance_workflow.md TTL references — Wave-1) |
| Production Verified | Yes |
| Management Decision | HK-33 (24h session TTL for recruitment, analogous to draft TTL); no explicit draft TTL decision |
| Risk | High — if cleanup fails, stale drafts persist and may be actioned |

**Maturity: Level 2 (Production Verified)**

---

### J-09: daily_memory_review

| Dimension | Status |
|---|---|
| Schedule | 09:00 daily |
| Function | `modules/memory_extractor.run_daily_review()` |
| Behavior | Reviews conversation history; extracts learned knowledge; stores in knowledge memory |
| Documented in KB | Table row only; memory extractor P2 in Wave-2 |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Medium — AI learning system; uncontrolled extraction not documented |

**Maturity: Level 2 (Production Verified)**

---

### J-10: rag_rebuild

| Dimension | Status |
|---|---|
| Schedule | 18:00 daily |
| Function | `modules/rag.rebuild_index()` |
| Behavior | Rebuilds BM25 RAG index from KB articles |
| Documented in KB | Table row only; BM25 params (k1=1.5, b=0.75, chunk 320/60) not documented |
| Production Verified | Yes |
| Management Decision | None formal for rebuild schedule or index parameters |
| Risk | High — if rebuild fails, RAG serves stale index |

**Maturity: Level 2 (Production Verified)**

---

### J-11: lock_cleanup

| Dimension | Status |
|---|---|
| Schedule | Every 5 minutes |
| Function | `modules/locks.cleanup_stale_locks()` |
| Behavior | Releases advisory locks held beyond timeout threshold |
| Documented in KB | Table row only |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Low — infrastructure; lock leaks are rare |

**Maturity: Level 2 (Production Verified)**

---

### J-12: draft_ttl_cleanup

| Dimension | Status |
|---|---|
| Schedule | Every 30 minutes |
| Function | `modules/drafts.cleanup_draft_ttl()` |
| Behavior | Secondary cleanup for draft TTLs; supplements combined_draft_cleanup |
| Documented in KB | Table row only |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Medium — overlaps with J-08; combined behavior not documented |

**Maturity: Level 2 (Production Verified)**
**Note:** Relationship between J-08 and J-12 (combined_draft_cleanup vs draft_ttl_cleanup) is undocumented in KB; potential for confusion.

---

### J-13: bridge_watchdog

| Dimension | Status |
|---|---|
| Schedule | Every 5 minutes |
| Function | `modules/bridge_health.watchdog_check()` |
| Behavior | Checks HR bridge (8082) and OPS bridge (8081) health; restarts dead bridge processes |
| Documented in KB | Table row + bridge ports in developer_notes.md — Wave-1 |
| Production Verified | Yes |
| Management Decision | None |
| Risk | High — if watchdog fails, bridges go down silently |

**Maturity: Level 2 (Production Verified)**

---

### J-14: daily_admin_digest

| Dimension | Status |
|---|---|
| Schedule | 08:00 daily |
| Function | `modules/digest.send_admin_digest()` |
| Behavior | Sends daily operational summary to admin/superadmin |
| Documented in KB | Table row only; digest format not documented |
| Production Verified | Yes |
| Management Decision | None |
| Risk | Low |

**Maturity: Level 2 (Production Verified)**

---

### J-15: daily_db_backup

| Dimension | Status |
|---|---|
| Schedule | 02:30 daily |
| Function | `modules/backup.run_daily_backup()` |
| Behavior | Creates DB backup; rotates (14 daily, 8 weekly); SHA-256 integrity check; records in `fazle_db_backups` |
| Documented in KB | Yes (developer_notes.md backup system section — Wave-1) |
| Production Verified | Yes |
| Management Decision | None formal for backup retention policy |
| Risk | High — data protection; retention policy not formally approved |

**Maturity: Level 2 (Production Verified)**

---

## Scheduler Maturity Summary

| Job | Level | Risk |
|---|---|---|
| J-01: daily_payroll_compute | 2 | Medium |
| J-02: dlq_alert | 2 | Medium |
| J-03: health_summary | 2 | Low |
| J-04: agent_incident_summary | 2 | Low |
| J-05: stale_escort_reminder | 2 | Low |
| J-06: payment_reconciliation | 2 | High |
| J-07: backup_staleness_alert | 2 | Medium |
| J-08: combined_draft_cleanup | 2 | High |
| J-09: daily_memory_review | 2 | Medium |
| J-10: rag_rebuild | 2 | High |
| J-11: lock_cleanup | 2 | Low |
| J-12: draft_ttl_cleanup | 2 | Medium |
| J-13: bridge_watchdog | 2 | High |
| J-14: daily_admin_digest | 2 | Low |
| J-15: daily_db_backup | 2 | High |

**All 15 jobs: Level 2 (Production Verified)**
**Scheduler Domain Average: 2.0 / 5.0**

---

## Gap Analysis: Level 2 → Level 3

The entire scheduler system is at Level 2. To reach Level 3, the following management decisions are needed:

| Decision Required | Priority | Affects |
|---|---|---|
| Approve daily_payroll_compute at 02:00 | High | J-01 (financial auto-compute) |
| Approve backup retention policy (14d/8w) | High | J-15 (data protection) |
| Approve payment reconciliation frequency (hourly) | High | J-06 (financial) |
| Approve draft TTL cleanup redundancy (J-08 vs J-12) | Medium | J-08, J-12 |
| Approve bridge watchdog restart behavior | Medium | J-13 |

**Fastest path to Level 3:** Management formally ratify the 5 job behaviors above. This would move Scheduler from Level 2 → Level 3 with no KB changes needed.

---

## Notable Production Detail

The entire scheduler system shares a single `SCHEDULER_ENABLED` flag. There is no per-job enable/disable — all 15 jobs are enabled or disabled together. This is a governance gap: a single flag can disable critical jobs (payroll, backup, reconciliation) alongside low-risk jobs (digest, health summary). Not documented in KB.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
