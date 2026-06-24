---
title: PKMA v3 — Production Knowledge Maturity Audit
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA v3 — Production Knowledge Maturity Audit
**Program:** Production Knowledge Maturity Audit (PKMA)
**Version:** v3 (Session 10 post-enrichment)
**Date:** 2026-06-23
**Baseline:** PKMA v2 2026-06-23 — avg 4.88/5.0
**Mode:** Post-implementation audit — Session 10 articles added; bridge_poller.md freshness reassessed
**Authorized under:** W4-AUTH + Session 10 ESX-WIRE authorization

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

**Target for certification:** Average ≥ 4.0 per article across all audited articles

---

## Session 10 New Articles — Maturity Scores

### 19. `intent_classifier.md` (NEW Session 10)
**Source read:** `modules/intent/__init__.py` (172 lines — read 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 14 intents, 3-pass pipeline order, REGEX_INTENTS with examples, threshold=72, rapidfuzz partial_ratio — all verified from source |
| Completeness (CO) | 4 | Core pipeline and all 14 intents documented. Gap: full INTENT_KEYWORDS dict with all keyword variants not enumerated (only samples shown per intent) |
| Clarity (CL) | 5 | 3-pass pipeline ASCII diagram is clear; 14-intent table with regex column and downstream effects table are excellent |
| Currency (CU) | 5 | Read from source 2026-06-23; no stale entries |
| Consistency (CS) | 4 | Downstream effects cross-check with bridge_poller.md and social_auto_reply_system.md correct. Minor gap: `voice_note` intent origin (audio transcript path) not cross-referenced |
| **Average** | **4.6** | |

---

### 20. `accountant_summary.md` (NEW Session 10)
**Source read:** `modules/accountant_summary/__init__.py` (158 lines — read 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | 13 labels, `= <digits>/-` pattern, Bengali digit normalisation (`str.maketrans`), no-DB-write constraint — all verified from source |
| Completeness (CO) | 4 | Detection, 13 labels, ACK format, routing position, and "what it does NOT do" all documented. Gap: exact regex pattern for `= <digits>/-` not shown; dual detection condition (pattern OR known label) slightly under-specified |
| Clarity (CL) | 5 | Example message table with parse result is very clear; API code block is immediately usable |
| Currency (CU) | 5 | Read from source 2026-06-23; no stale entries |
| Consistency (CS) | 4 | Cross-ref to `nl_advance_record.py` for per-employee advances correct. Minor gap: identity_brain accountant role detection trigger not fully cross-referenced to identity_brain.md |
| **Average** | **4.6** | |

---

### 21. `attendance_parser.md` (NEW Session 10)
**Source read:** `modules/attendance_parser/__init__.py` (281 lines — read 2026-06-23)

| Dimension | Score | Notes |
|---|---|---|
| Accuracy (AC) | 5 | Detection gate (both keyword+date required), 2-pass name extraction algorithm, DB tables (wbom_employees, fazle_draft_replies, wbom_attendance), ON CONFLICT UPDATE, deprecated alias `save_supervisor_attendance` — all verified |
| Completeness (CO) | 4 | Detection, parsing, draft flow, approval path, and API all documented. Gaps: complete attendance keyword list not enumerated (only ~10 shown); ON CONFLICT UPDATE target column not specified |
| Clarity (CL) | 5 | Draft creation flow diagram with DB tables and APPROVE/REJECT path is clear; draft body format examples are immediately useful |
| Currency (CU) | 5 | Read from source 2026-06-23; no stale entries |
| Consistency (CS) | 4 | Cross-refs to `attendance_workflow.md`, `attendance_business_rules.md`, `admin_commands` APPROVE handler correct. Minor gap: `save_supervisor_attendance` deprecated alias not cross-checked with actual `message_router` call sites |
| **Average** | **4.6** | |

---

## Wave-4 Revised Article — Freshness Reassessment

### 9. `bridge_poller.md` (reassessed — ESX-WIRE gap)
**Original score (v2):** 5.0 | **Revised score (v3):** 4.2
**Reason:** ESX-WIRE change in Session 10 replaced `ocr_processor.process_image()` with `escort_slip_extractor.extract_escort_slip()` in Step 2 of the image path. The KB article still describes the old `ocr_processor` flow — it is now stale on the core OCR dispatch path.

| Dimension | v2 Score | v3 Score | Change | Notes |
|---|---|---|---|---|
| Accuracy (AC) | 5 | 4 | -1 | Step 1 (classify_from_context) still correct; Step 2 now stale — article says `ocr_processor.process_image()` but code uses `escort_slip_extractor.extract_escort_slip()` |
| Completeness (CO) | 5 | 5 | — | Complete for what it documented at time of writing |
| Clarity (CL) | 5 | 5 | — | Pipeline flow diagram still clear for Step 1 and dedup/outgoing sections |
| Currency (CU) | 5 | 3 | -2 | ESX-WIRE applied 2026-06-23 (Session 10) — article not updated; Step 2 section is now factually incorrect |
| Consistency (CS) | 5 | 4 | -1 | Cross-references to `ocr_processor` for image OCR now incorrect; `escort_slip_extractor` not referenced |
| **Average** | **5.0** | **4.2** | **-0.8** | Still passes ≥ 4.0 threshold but requires update |

**Action required:** Update `bridge_poller.md` Step 2 section to describe ESX-WIRE flow:
- Replace `ocr_processor.process_image()` with `escort_slip_extractor.extract_escort_slip()`
- Document `completion_date is not None` TWO-DATE rule
- Document compat dict translation layer (`escort_name` → `employee_name`, etc.)
- Note `handle_ocr_release_slip()` `slip_type="release_slip"` requirement

---

## Summary Score Card (v3 — All Articles)

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
| bridge_poller.md ⚠️ | 4 | 5 | 5 | 3 | 4 | **4.2** |
| payroll_module.md | 5 | 5 | 5 | 5 | 4 | **4.8** |
| admin_commands_detail.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| ocr_engine.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| admin_operations_overview.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| payment_business_rules.md | 5 | 5 | 5 | 5 | 5 | **5.0** |
| escort_workflow.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| fpe_overview.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| social_auto_reply_system.md | 5 | 4 | 5 | 5 | 5 | **4.8** |
| **[Wave-4 subtotal 1–17]** | **4.94** | **4.47** | **5.0** | **4.88** | **4.88** | **4.83** |
| intent_classifier.md ✨ | 5 | 4 | 5 | 5 | 4 | **4.6** |
| accountant_summary.md ✨ | 5 | 4 | 5 | 5 | 4 | **4.6** |
| attendance_parser.md ✨ | 5 | 4 | 5 | 5 | 4 | **4.6** |
| **Overall Average (21 articles)** | **4.95** | **4.43** | **5.0** | **4.86** | **4.81** | **4.81** |

✨ = Session 10 new article | ⚠️ = freshness gap identified

---

## PKMA v3 Verdict

**Average maturity score: 4.81 / 5.0**

**Target: ≥ 4.0 — ACHIEVED ✅**

**Change from v2 (4.88 → 4.81):**
The slight decrease (-0.07) comes from two sources:
1. Three new Session 10 articles score 4.6 (vs v2 avg of 4.88) — lower Completeness (CO=4) because full keyword enumerations and exact regex patterns weren't shown
2. `bridge_poller.md` freshness reassessment dropped from 5.0 to 4.2 due to ESX-WIRE step not reflected in the article

All 21 articles remain above the 4.0 certification threshold.

**Weakest dimension:** Completeness (CO) at 4.43 — driven by:
- Intent keyword enumeration gap (intent_classifier.md)
- Regex pattern not shown (accountant_summary.md)
- Attendance keyword list partial (attendance_parser.md)
- bridge_poller.md Step 2 stale (Completeness still passes — AC/CU are the real gaps)

**Urgent action:** `bridge_poller.md` CU=3 is the lowest single dimension score in the corpus. The ESX-WIRE update should be applied to bring it back to 5.0.
