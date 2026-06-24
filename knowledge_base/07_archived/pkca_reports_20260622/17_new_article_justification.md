---
title: PKCA Report 17: New Article Justification
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 17: New Article Justification

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Justification Policy

Per PKCA program rules: **Only recommend a new article when knowledge cannot logically fit in any existing article.**

New articles must pass this test:
1. The knowledge domain is distinct enough that it does not belong in any existing article
2. It will be referenced as a standalone concept in multiple contexts
3. It has sufficient scope to warrant a separate article (>5 knowledge units)

---

## NEW ARTICLE 1: `06_developer_system/fpe_overview.md`

**Justification:**
The Fazle Payroll Engine (FPE) is an entirely separate background system from the core payroll module. It has its own 5 workers, separate database tables (fpe_* prefix), separate API routes (/api/fpe/*), and its own processing state machine. This is not a sub-section of any existing article:
- `salary_workflow.md` covers the core payroll module state machine, not FPE
- `automation_pipeline.md` covers the main app pipeline, not FPE's 5 workers
- `database_rules.md` can hold fpe_* table names, but the FPE processing architecture requires its own article

**Content to include:**
1. FPE purpose: background financial intelligence engine reading accountant WhatsApp
2. 5 worker threads: message_processor_worker, accounting_worker, historical_sync_loop, gap_scan_loop, bridge_health_loop
3. FPE processing state machine: pending→parsing→parsed→accounting→done/failed/skipped
4. MessageType enum: payment, balance_summary, cash_command, income_command, escort_payment, other
5. TxnCategory enum
6. ProcessingStatus enum
7. FPE database tables: fpe_wa_messages, fpe_message_processing_state, fpe_cash_transactions, fpe_employees
8. FPE API routes: /api/fpe/* endpoints
9. Key distinction: fpe_cash_transactions is separate from wbom_cash_transactions
10. FPE does NOT write to wbom_cash_transactions (only accountant_summary module is involved in that path)

**Visibility:** DEVELOPER
**Priority:** P2

---

## NEW ARTICLE 2: `06_developer_system/scheduler_jobs.md`

**Justification:**
The 15 scheduled jobs deserve their own reference article because:
- `automation_pipeline.md` describes the pipeline flow; embedding 15 jobs with individual schedules, env overrides, and idempotency rules would make it too long and hard to navigate
- Scheduler jobs are frequently referenced by admins for troubleshooting (SCHEDULE STATUS command)
- It is a distinct operational reference that operators and devs both need independently

However, this is a borderline case. An alternative is a detailed § Scheduler table in `automation_pipeline.md`. If the enrichment plan is executed and the scheduler section becomes too long, it can be split into a standalone file later.

**Recommendation:** Enrich `automation_pipeline.md` first (per enrichment plan). Create this article only if the scheduler section exceeds 60 lines.

**Conditional Priority:** P3 (only create if enrichment creates size issue)

---

## NEW ARTICLE 3: `06_developer_system/social_auto_reply_system.md`

**Justification:**
The social auto-reply system is a 20-file sub-system:
- `__init__.py`, `backlog_processor.py`, `classifier.py`, `comment_handler.py`, `conversation_history.py`, `daemon_worker.py`, `employee_lookup.py`, `intelligent_generator.py`, `message_deduplicator.py`, `payment_issue_handler.py`, `planner_worker.py`, `rate_limiter.py`, `reply_generator.py`, `reply_rules.py`, `retry_queue.py`, `risk_flagger.py`, `routes.py`, `salary_flow.py`, `send_queue.py`, `service_runner.py`, `state_tracker.py`

This is Facebook/Messenger/Meta WhatsApp auto-reply — a completely different channel than the main WhatsApp pipeline. No existing article covers:
- Facebook comment handler
- Messenger auto-reply
- Social rate limiter
- Risk flagging
- Planner vs daemon worker distinction

This cannot logically be appended to `automation_pipeline.md` (which covers the WhatsApp bridge pipeline) without creating a misleading conflation of two separate systems.

**Content to include:**
1. Social channels covered: Facebook comment, Messenger, Meta WhatsApp
2. 20-file architecture overview
3. Daemon vs planner worker distinction
4. Reply rules categories (WELCOME_REPLY, LOCATION_REPLY, JOB_DETAILS_REPLY, SALARY_REPLY, etc.)
5. Rate limiter behavior
6. Risk flagger: what triggers risk flag
7. Retry queue and backlog processor
8. Social auto-reply is separate from the main WhatsApp bridge system

**Visibility:** DEVELOPER
**Priority:** P2

---

## Articles Considered and Rejected

| Proposed Article | Reason Rejected (Fits Existing) |
|---|---|
| `outbound_queue.md` | Fits in `automation_pipeline.md` § Outbound Queue |
| `llm_chains.md` | Fits in `automation_pipeline.md` § LLM Providers |
| `admin_command_reference.md` | Fits in `admin_operations_overview.md` § Commands |
| `phone_normalization.md` | Fits in `database_rules.md` § Phone Lookup |
| `identity_algorithm.md` | Fits in `identity_brain.md` § Resolution Algorithm |
| `draft_system.md` | Fits in `automation_pipeline.md` § Draft System |
| `rag_parameters.md` | Fits in `rag_strategy.md` § Technical Parameters |
| `escort_state_machine.md` | Fits in `escort_workflow.md` § State Machine |
| `payroll_state_machine.md` | Fits in `salary_workflow.md` § State Machine |

---

## Summary

| New Article | Visibility | Priority | Rationale |
|---|---|---|---|
| `fpe_overview.md` | DEVELOPER | P2 | Separate background engine; no existing home |
| `scheduler_jobs.md` | DEVELOPER | P3 (conditional) | Only if enrichment plan creates size issue |
| `social_auto_reply_system.md` | DEVELOPER | P2 | Separate channel system; 20 files cannot fit in existing articles |

**Total new articles proposed: 2 definite + 1 conditional**

**Contrast with enrichment plan:** 22 existing articles need enrichment vs 2 new articles justified. This follows the PKCA program rule to enrich first.
