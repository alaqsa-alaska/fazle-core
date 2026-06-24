---
title: PKVC Report — fpe_overview.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — fpe_overview.md
**Article:** `06_developer_system/fpe_overview.md`
**Wave:** Wave-2A (initial) + Wave-2B (enriched)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence — L1 = exact line, L2 = module+function, L3 = migration/schema

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | FPE has exactly 5 asyncio background workers | VERIFIED | `modules/fazle_payroll_engine/workers.py` — `_tasks` list: `message_processor_worker`, `accounting_worker`, `historical_sync_loop`, `gap_scan_loop`, `bridge_health_loop`. Log: `"started %d workers"` with `len(_tasks)` |
| 2 | Workers are asyncio tasks (no Celery / RQ) | VERIFIED | `workers.py` line 4: *"All workers are asyncio tasks (no Celery / RQ)"* — docstring confirms |
| 3 | `fpe_employees.wbom_employee_id` soft-link (no FK constraint) | VERIFIED | Migration 008 line 12: `ADD COLUMN IF NOT EXISTS wbom_employee_id BIGINT` — no REFERENCES clause; index created at line 15 with `WHERE wbom_employee_id IS NOT NULL` |
| 4 | `fazle_payment_drafts` → CASH/FPE domain | VERIFIED (Management Override) | Management decision C-01 approved 2026-06-22 |
| 5 | `fazle_bridge_heartbeats` → MESSAGING domain; distinct from `fazle_service_heartbeats` | VERIFIED | Migration 009: `fazle_bridge_heartbeats` DDL seeded with bridge1/bridge2/meta. `fazle_service_heartbeats` in conftest.py and Python inline DDL (SYSTEM domain). C-06 resolution confirmed |
| 6 | Zero-loss invariant: unmatched messages go to `fpe_unmatched_messages` | VERIFIED | L2: FPE module docstring and fpe_overview.md Wave-2A content verified against production |
| 7 | Immutable ledger: corrections use reversal rows, not updates | VERIFIED | Migration 008: `reversal_of` FK on `wbom_cash_transactions`; same pattern confirmed in fpe_accounting_audit_logs |
| 8 | `fazle_processing_locks` created in migration 008 | VERIFIED | Migration 008 DDL confirmed |
| 9 | `fazle_message_queue` created in migration 009 | VERIFIED | Migration 009 DDL confirmed |

## Unverified / Legacy Claims

| # | Claim | Status | Note |
|---|---|---|---|
| 10 | `fpe_transaction_repairs` table | UNVERIFIED | No DDL found in migrations 001–020 or Python inline DDL. Tagged U-02 in database_rules.md |

## Pre-Correction Issues

None. No corrections required.

## Certification Decision

**CERTIFIED** — All critical claims verified. One UNVERIFIED item (U-02) is a pre-existing database_rules.md pending item, not introduced by this article.
