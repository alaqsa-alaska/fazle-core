---
title: PKVC Report — social_auto_reply_system.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — social_auto_reply_system.md
**Article:** `06_developer_system/social_auto_reply_system.md`
**Wave:** Wave-2A (initial) + Wave-2B (enriched)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Module has 21 Python files | VERIFIED | `find /home/azim/core/modules/social_auto_reply/ -name "*.py" \| wc -l` = 21 |
| 2 | Module creates exactly 8 DB tables | VERIFIED | `grep "CREATE TABLE IF NOT EXISTS" modules/social_auto_reply/__init__.py` = 8 hits: `social_inbox_events`, `social_reply_queue`, `social_sent_log`, `social_retry_queue`, `social_flagged_items`, `social_backlog_state`, `social_rate_limit_state`, `social_thread_state` |
| 3 | `fazle_service_heartbeats` → SYSTEM domain | VERIFIED | conftest.py and Python inline DDL confirmed; written by social daemon (confirmed C-06) |
| 4 | `fazle_bridge_heartbeats` → MESSAGING domain | VERIFIED | FPE migration 009; separate table from `fazle_service_heartbeats` (confirmed C-06) |
| 5 | No auto-reply via bridge1/bridge2 (only messenger/meta/facebook_comment) | VERIFIED | L2: `risk_flagger.py` `can_auto_send()` function; social_auto_reply_system.md platform routing table |
| 6 | 8 tables owned by SOCIAL domain | VERIFIED (Management Override) | All `social_*` tables confirmed in `__init__.py` DDL |

## Pre-Correction Issues Found and Fixed

| # | Original Claim | Correction | Fixed In |
|---|---|---|---|
| C1 | "20-file system" (Module Structure heading) | Corrected to "21 Files"; `service_runner.py` added to table | `social_auto_reply_system.md` line 60, 2026-06-22 |

## Unverified / Legacy Claims

None.

## Certification Decision

**CERTIFIED** — One inaccuracy found and corrected during PKVC (file count 20 → 21). All claims now verified against production.
