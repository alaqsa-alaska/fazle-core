---
title: PKMA v2 — Production Knowledge Maturity Audit
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA v2 — Production Knowledge Maturity Audit
**Program:** Production Knowledge Maturity Audit (PKMA)
**Version:** v2 (Wave-4 post-enrichment)
**Date:** 2026-06-23
**Mode:** Read-Only Audit — no KB changes
**Authorized under:** W4-AUTH

---

## Maturity Dimensions (5-point scale each)

| Dimension | Definition |
|---|---|
| **Accuracy (AC)** | Facts match production code; no stale constants, wrong counts, or superseded behavior |
| **Completeness (CO)** | All major behaviors, constants, tables, and edge cases covered |
| **Clarity (CL)** | A developer could use this article to understand the module without reading source code |
| **Currency (CU)** | Freshness — was it read from source code recently? Are there open "unresolved" items? |
| **Consistency (CS)** | Cross-references are accurate; no contradictions with other KB articles |

**Score scale:** 5 = Excellent, 4 = Good, 3 = Adequate, 2 = Partial, 1 = Inadequate, 0 = Missing

**Target for certification:** Average ≥ 4.0 per article across all Wave-4 articles

---

## Wave-4 New Articles — Maturity Scores

### 1. `reviewed_reply_memory.md`
**Source read:** `modules/reviewed_reply_memory/__init__.py` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 3-attempt cascade, UNSAFE_DRAFT_TYPES, UNSAFE_PREFIXES (8), UNSAFE_SUBSTRINGS (8) — all verified from source |
| Completeness (CO) | 4 | Covers table schema, cascade algorithm, tie-breaking, kill switch. Minor gap: no DDL-level column list for `fazle_reviewed_replies` |
| Clarity (CL) | 5 | Cascade table is very clear; Step 14 position documented |
| Currency (CU) | 5 | Read from source 2026-06-23; no stale entries |
| Consistency (CS) | 5 | Cross-references to workflow_engine.md (Step 14) are accurate |
| **Average** | **4.8** | |

---

### 2. `recruitment_flow_system.md`
**Source read:** `modules/recruitment_flow/__init__.py` + `db/migrations/003b_recruitment_sessions_fix.sql` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | SESSION_TTL=24h, INTAKE_KEYWORDS ~23, COLLECTION_STEPS 6 steps, DDL from 003b (corrected schema) |
| Completeness (CO) | 5 | Two-path architecture, scoring, VALID_POSITIONS, BR-25 enforcement, full DDL |
| Clarity (CL) | 5 | Two-path table and funnel flow are immediately comprehensible |
| Currency (CU) | 5 | Read 2026-06-23; migration 003b correction documented |
| Consistency (CS) | 5 | Matches recruitment_ai_detail.md (LLM path) and identity_brain.md (candidate routing) |
| **Average** | **5.0** | |

---

### 3. `phone_normalizer.md`
**Source read:** `modules/phone_normalizer/__init__.py` + `PHONE_NORMALIZER_CONVENTION.md` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 13-digit canonical, VALID_OPERATORS 9 codes, 3 input formats, 3-normalizer disambiguation table |
| Completeness (CO) | 4 | Good. Minor gap: regex pattern for VALID_OPERATORS not shown |
| Clarity (CL) | 5 | 3-normalizer disambiguation table is very clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Correctly distinguishes modules.phone_normalizer vs number_identity vs FPE normalizer |
| **Average** | **4.8** | |

---

### 4. `recruitment_ai_detail.md`
**Source read:** `modules/recruitment_ai/__init__.py` (258 lines, 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | _SAFE_FALLBACK, _OFFICE_REPLY, _CONTACT_REPLY, _AGE_REPLY, _FEE_PHRASES (15), _QUESTION_HINTS (12) all verified |
| Completeness (CO) | 4 | Deterministic fast-path documented. Gap: build_recruitment_source_context() token overlap algorithm not fully detailed |
| Clarity (CL) | 5 | Deterministic vs LLM path distinction is very clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Contact context (`del contact_context`) noted; cross-refs to recruitment_flow_system.md accurate |
| **Average** | **4.8** | |

---

### 5. `admin_transactions_rules.md`
**Source read:** `modules/admin_transactions/__init__.py` (551 lines, 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 4-rule employee matching (A/B/C/D), pg_trgm ≥0.95, FPE 11-digit normalizer, soft-delete pattern |
| Completeness (CO) | 5 | REST routes, auth, optimistic locking, ledger reversal, audit log — all documented |
| Clarity (CL) | 5 | Rule A/B/C/D table is excellent; escalation order is explicit |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Correctly notes FPE 11-digit (not canonical 13-digit) for this module |
| **Average** | **5.0** | |

---

### 6. `contact_sync.md`
**Source read:** `modules/contact_sync/__init__.py` (357 lines, 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 3 sources, exact DB paths, _best_name() algorithm, "longer name wins" merge strategy |
| Completeness (CO) | 4 | 3 sync modes documented. Minor gap: per-message upsert field mapping not fully listed |
| Clarity (CL) | 5 | 3-source table is clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | LID JIDs → None consistent with bridge_poller.md LID resolution |
| **Average** | **4.8** | |

---

### 7. `admin_ui.md`
**Source read:** `modules/wa_chat_frontend/__init__.py` (821 lines, 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 23 endpoints (corrected from session summary's "28"), auth mechanism, SSE poll=3s all verified |
| Completeness (CO) | 4 | All 5 endpoint groups documented. Minor gap: broadcast rate limits not documented |
| Clarity (CL) | 5 | Endpoint table with path+purpose is clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Settings keys match runtime_gateway_flags.md; approve flow consistent with outbound module behavior |
| **Average** | **4.8** | |

---

### 8. `distributed_architecture.md`
**Source read:** `shared/queue_arbiter.py` + `shared/bridge_orchestrator.py` + `shared/self_heal.py` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | LEASE_TTL_S=120, PANIC_THRESHOLD=0.85, PANIC_CLEAR=0.60, 6-signal weights all verified |
| Completeness (CO) | 5 | All 3 modules (queue_arbiter, orchestrator, self_heal) with constants, APIs, kill switches |
| Clarity (CL) | 5 | Lease lifecycle table and pressure score table are very clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Kill switches (QUEUE_ARBITER_ENABLED, SELF_HEAL_ENABLED) cross-ref to runtime_gateway_flags.md |
| **Average** | **5.0** | |

---

### 9. `bridge_poller.md`
**Source read:** `modules/bridge_poller/__init__.py` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | BRIDGE_POLL_MIN_S=1.0, MAX_S=30.0, BACKOFF=1.5×, REPLY_COOLDOWN=60s, ingest policy v1.0.2 |
| Completeness (CO) | 5 | SQL filter, cursor management, LID resolution, dedup tables, OCR eligibility, outgoing scan |
| Clarity (CL) | 5 | Pipeline flow diagram and SQL filter are clear |
| Currency (CU) | 5 | Read 2026-06-23 |
| Consistency (CS) | 5 | Ingest policy v1.0.2 consistent with fpe_overview.md and database_rules.md |
| **Average** | **5.0** | |

---

### 10. `payroll_module.md` (NEW)
**Source read:** `modules/payroll/__init__.py` + `tests/conftest.py` DDL (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | ALLOWED_TRANSITIONS exact Python dict, 22-column DDL schema, DEFAULT_PER_PROGRAM_RATE=400.0 |
| Completeness (CO) | 5 | State machine diagram, all 3 tables (runs, run_items, approval_log), compute_run() algorithm |
| Clarity (CL) | 5 | ASCII state diagram is excellent; column table is well-organized |
| Currency (CU) | 5 | Read 2026-06-23 from conftest.py DDL (note: primary source; production migration may differ) |
| Consistency (CS) | 4 | Minor: DDL sourced from conftest.py (test fixture) not production migration; note added |
| **Average** | **4.8** | |

---

### 11. `admin_commands_detail.md` (NEW)
**Source read:** `modules/admin_commands/__init__.py` + `nl_router.py` + `date_parser.py` + `modules/rbac/__init__.py` (2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | SHA-1 dedup, SHA-256 API keys, COMMAND_ROLE dict (all entries), _BN_DIGITS translation |
| Completeness (CO) | 5 | 6 sections: Bangla digits, dedup, NL router, date parser, RBAC, multi-ID commands |
| Clarity (CL) | 5 | Date parser format table is exhaustive and well-organized |
| Currency (CU) | 5 | Read 2026-06-23 — NL router v1.1.0 "Phase 1.1 validation slice" noted |
| Consistency (CS) | 5 | COMMAND_ROLE dict consistent with admin_operations_overview.md (Group 1–8 commands) |
| **Average** | **5.0** | |

---

## Wave-4 Updated Articles — Maturity Scores

### 12. `ocr_engine.md` (updated Wave-4)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | `_compute_confidence()` actual algorithm with all conditions; low confidence threshold <40 |
| Completeness (CO) | 5 | Image pipeline, doc pipeline, slip types, doc types, confidence, duplicate detection, pre-filter |
| Clarity (CL) | 5 | Pipeline flow diagrams are excellent |
| Currency (CU) | 5 | Wave-4 update corrected confidence description from abstract to actual algorithm |
| Consistency (CS) | 5 | escort_lifecycle confidence check (conf<40) consistent with ocr_engine threshold |
| **Average** | **5.0** | |

---

### 13. `admin_operations_overview.md` (updated Wave-4)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 11+3 fields, mobile immutability, FPE auto-seed, soft deactivation |
| Completeness (CO) | 4 | 37 commands + employee API well-covered. Gap: per-command error response codes not documented |
| Clarity (CL) | 5 | Command table with syntax+role+action is very readable |
| Currency (CU) | 5 | Wave-4 update adds employee management section |
| Consistency (CS) | 5 | Mobile immutability consistent with admin_transactions_rules.md FPE normalizer note |
| **Average** | **4.8** | |

---

### 14. `payment_business_rules.md` (updated Wave-4 P2-D)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | PAY-01 ৳400/day verified in both constants; PAY-04 ৳150/day verified; PAY-03 gap explicitly documented |
| Completeness (CO) | 5 | PAY-01 through PAY-04, CR-05, full transport rate table, code gap noted |
| Clarity (CL) | 5 | "Full Rate Reference" summary table is excellent |
| Currency (CU) | 5 | Wave-4 update; code updated 2026-05-29 vs management decision 2026-06-22 discrepancy documented |
| Consistency (CS) | 5 | PAY-03 gap (code=৳700 vs management=৳800) is honest — not hidden |
| **Average** | **5.0** | |

---

### 15. `escort_workflow.md` (updated Wave-4)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 8 parsed fields from RELEASE CONFIRMED verified; remarks JSON 8 fields verified |
| Completeness (CO) | 4 | RELEASE CONFIRMED section complete. Gap: no coverage of ESCORTLIST/ESCORTDETAIL response format |
| Clarity (CL) | 5 | [RELEASE CONFIRMED] template with field extraction table is clear |
| Currency (CU) | 5 | Wave-4 update |
| Consistency (CS) | 5 | bridge_poller.md outgoing scan consistent; escort_lifecycle match confirmed |
| **Average** | **4.8** | |

---

### 16. `fpe_overview.md` (updated Wave-4 — fpe_employee_ledger DDL)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 10-column ledger DDL from 001_fpe_schema.sql; UNIQUE constraint correct |
| Completeness (CO) | 4 | Ledger DDL now complete. Minor gap: closing_balance calculation formula not explicitly documented in article |
| Clarity (CL) | 5 | New schema table fits cleanly into existing article |
| Currency (CU) | 5 | Wave-4 read 2026-06-23 |
| Consistency (CS) | 5 | accounting_worker behavior consistent with accounting_audit_logs section |
| **Average** | **4.8** | |

---

### 17. `recruitment_flow_system.md` (updated Wave-4 — DDL schema)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | Full DDL from 003b (corrected migration); migration 003 error noted |
| Completeness (CO) | 5 | 17 columns + 3 indexes |
| Clarity (CL) | 5 | Migration history note is very valuable |
| Currency (CU) | 5 | Wave-4 update |
| Consistency (CS) | 5 | SESSION_TTL=24h consistent with constants section; funnel_stage values match state_tracker references |
| **Average** | **5.0** | |

---

### 18. `social_auto_reply_system.md` (updated Wave-4 section)
| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | Exact frozensets (16/15/3), media_flag logic, state_tracker kill switch, payment two-path all verified from source |
| Completeness (CO) | 4 | Wave-4 additions complete. Overall article gap: salary_flow.py not documented |
| Clarity (CL) | 5 | Frozensets as code blocks are excellent |
| Currency (CU) | 5 | Wave-4 update |
| Consistency (CS) | 5 | RISKY_INTENTS cross-checks with intent list in article body |
| **Average** | **4.8** | |

---

## Summary Score Card

| Article | AC | CO | CL | CU | CS | **Avg** |
|---|---|---|---|---|---|---|
| reviewed_reply_memory.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| recruitment_flow_system.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| phone_normalizer.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| recruitment_ai_detail.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| admin_transactions_rules.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| contact_sync.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| admin_ui.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| distributed_architecture.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| bridge_poller.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| payroll_module.md | 5 | 5 | 5 | 5 | 4 | **4.8** |
| admin_commands_detail.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| ocr_engine.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| admin_operations_overview.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| payment_business_rules.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| escort_workflow.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| fpe_overview.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| social_auto_reply_system.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| **Average** | **5.0** | **4.5** | **5.0** | **5.0** | **4.9** | **4.88** |

---

## PKMA v2 Verdict

**Average maturity score: 4.88 / 5.0**

**Target: ≥ 4.0 — ACHIEVED ✅**

All 18 Wave-4 articles score ≥ 4.8. The weakest dimension is Completeness (4.5), driven by minor gaps in: broadcast rate limits (admin_ui), token overlap algorithm detail (recruitment_ai), DDL source from conftest.py vs production migration (payroll_module), and salary_flow.py undocumented (social_auto_reply).

**No article scores below the 4.0 threshold.**
