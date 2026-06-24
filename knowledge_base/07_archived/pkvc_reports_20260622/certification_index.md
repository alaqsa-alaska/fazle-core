---
title: PKVC Certification Index — Wave-2A + Wave-2B
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Certification Index — Wave-2A + Wave-2B
**Program:** KSP Wave-2B Final Certification Run
**Date:** 2026-06-22
**Scope:** Wave-2A articles (2) + Wave-2B articles (6) = 8 articles total
**Authority:** Final PKVC Re-run under Development Freeze

---

## Article Certification Status

| # | Article | Wave | Claims Verified | Pre-Corrections | Result | Report |
|---|---|---|---|---|---|---|
| 1 | `fpe_overview.md` | 2A + 2B | 9 | 0 | **CERTIFIED** | [01_fpe_overview_pkvc.md](01_fpe_overview_pkvc.md) |
| 2 | `social_auto_reply_system.md` | 2A + 2B | 6 | 1 (file count 20→21) | **CERTIFIED** | [02_social_auto_reply_pkvc.md](02_social_auto_reply_pkvc.md) |
| 3 | `database_rules.md` | 2B | 14 | 0 | **CERTIFIED** | [03_database_rules_pkvc.md](03_database_rules_pkvc.md) |
| 4 | `automation_pipeline.md` | 1 + 2B | 12 | 0 | **CERTIFIED** | [04_automation_pipeline_pkvc.md](04_automation_pipeline_pkvc.md) |
| 5 | `rag_strategy.md` | 2B | 14 | 3 (dir count, pattern count, stop words) | **CERTIFIED** | [05_rag_strategy_pkvc.md](05_rag_strategy_pkvc.md) |
| 6 | `ocr_engine.md` | 2B | 13 | 0 | **CERTIFIED** | [06_ocr_engine_pkvc.md](06_ocr_engine_pkvc.md) |
| 7 | `developer_notes.md` | 1 + 2B | 17 | 0 | **CERTIFIED** | [07_developer_notes_pkvc.md](07_developer_notes_pkvc.md) |
| 8 | `identity_brain.md` | 1 + 2B | 11 | 1 (role count 14→15) | **CERTIFIED** | [08_identity_brain_pkvc.md](08_identity_brain_pkvc.md) |

**Total:** 8 articles | 96 claims verified | 5 pre-corrections applied | **8/8 CERTIFIED**

---

## Pre-Corrections Summary

All corrections were applied to KB articles only. No production files modified.

| # | Article | Inaccuracy | Correction Applied |
|---|---|---|---|
| 1 | `rag_strategy.md` | "10 excluded directories" | Corrected to 11; added implicit `_` prefix rule |
| 2 | `rag_strategy.md` | "16 chunk-level unsafe patterns" | Corrected to 32; PATCH 4 + PATCH 5 documented |
| 3 | `rag_strategy.md` | Stop words filter undocumented | `_STOP_WORDS` frozenset documented in Tokenizer section |
| 4 | `social_auto_reply_system.md` | "20-file system" | Corrected to 21; `service_runner.py` added to table |
| 5 | `identity_brain.md` | "14 roles" in revision history | Corrected to 15 roles; PKVC correction note added |

---

## Claim Status Distribution

| Status | Count |
|---|---|
| VERIFIED | 78 |
| VERIFIED (Management Override) | 13 |
| UNVERIFIED (Pre-existing pending items) | 5 |
| LEGACY | 0 |

**Pre-existing UNVERIFIED items** (all from database_rules.md — not blocking):
- U-01: `wbom_candidates` table existence in production
- U-02: `fpe_transaction_repairs` table DDL not found
- U-03: `wbom_staging_payments` vs `fpe_staging_payments` naming ambiguity
- (2 additional minor items noted in identity_brain.md report — clarifications, not errors)

---

## Scope Boundary

Articles NOT in this PKVC scope (Wave-1 only, not enriched in Wave-2A/2B):

| Article | Status |
|---|---|
| `conversation_parser.md` | Stub — out of scope |
| `event_pipeline.md` | Stub — out of scope |
| `hybrid_search.md` | Stub — out of scope |
| `parser_engine.md` | Stub — out of scope |
| `role_permissions.md` | Partial — out of scope |
| `security_rules.md` | Wave-1 — out of scope |
| `system_prompt.md` | Stub — out of scope |
| `visibility_rules.md` | Stub — out of scope |
| `workflow_engine.md` | Stub — out of scope |
