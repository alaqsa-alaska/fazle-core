---
title: Management Decisions — Active Register
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# Management Decisions — Active Register
**Version:** 1.0.0
**Last Updated:** 2026-06-22
**Scope:** Active decisions only. Superseded or withdrawn decisions are archived, not listed here.
**Authority:** These decisions take priority over production code where they conflict.

---

## How to Use This Document

These decisions are authoritative. When a decision here conflicts with production code:
- The decision is **policy** (what should be true)
- Production code is **current implementation** (what is currently running)
- Document the gap; do not ignore either reality

---

## Business Rules

| ID | Decision | Domain | Date |
|---|---|---|---|
| BR-25 | Employee age range for active duty = **18–55** (not 18–45) | Employee / Recruitment | 2026-06-22 |

---

## Payroll and Payment Formulas

| ID | Decision | Formula | Domain | Date |
|---|---|---|---|---|
| PAY-01 | Escort payment formula | `12,000 ÷ 30 × duty_days` | Escort / Payroll | 2026-06-22 |
| PAY-02 | Payroll and escort payment formula must be unified | Single source of truth; no separate calculation in escort vs payroll | Escort / Payroll | 2026-06-22 |
| PAY-03 | Mongla transport rate | **৳800 per assignment** (fixed, not variable) | Escort | 2026-06-22 |
| PAY-04 | Food cost | **৳150/day** | Escort | 2026-06-22 |

---

## System Scope Decisions

| ID | Decision | Domain | Date |
|---|---|---|---|
| SCOPE-01 | OCR is strategic. Current scope: escort release slips. Future scope (approved): candidate CV extraction | OCR / Recruitment | 2026-06-22 |
| SCOPE-02 | FPE (Fazle Payroll Engine) and Social Auto Reply are **separate subsystems**. Document separately. No merging. | FPE / SOCIAL | 2026-06-22 |

---

## Entity Ownership Decisions

These decisions resolved contested table domain assignments. They are permanent.

| ID | Table | Domain Assigned | Date |
|---|---|---|---|
| C-01 | `fazle_payment_drafts` | CASH/FPE | 2026-06-22 |
| C-02 | `wbom_staging_payments` | CASH/FPE | 2026-06-22 |
| C-03 | `fazle_reviewed_replies` | MESSAGING | 2026-06-22 |
| C-04 | `user_profiles` | AI | 2026-06-22 |
| C-05 | `fazle_contact_aliases` | IDENTITY | 2026-06-22 |
| C-06 | `fazle_service_heartbeats` → SYSTEM domain; `fazle_bridge_heartbeats` → MESSAGING domain (two separate tables) | SYSTEM / MESSAGING | 2026-06-22 |

---

## Governance Decisions

| ID | Decision | Date |
|---|---|---|
| GOV-01 | Knowledge Base v1.0 is MANAGEMENT APPROVED under Controlled Freeze | 2026-06-22 |
| GOV-02 | Documentation-First Policy is in effect. No production feature may begin without KB Update → Management Approval | 2026-06-22 |
| GOV-03 | Production Freeze remains in effect. No production changes without explicit management authorization | 2026-06-22 |
| GOV-04 | KB-First Reading Policy: every implementation task begins by reading KB, not production code | 2026-06-22 |
| GOV-05 | Session 2 (Knowledge-Driven Application Development) is the active development phase | 2026-06-22 |
| GOV-06 | Knowledge Base v2.0 is MANAGEMENT CERTIFIED (PKCA v2: 67% weighted / P0+P1 100% pass; PKMA v2: 4.88/5.0; PKVC v2: 0 conflicts) | 2026-06-23 |

---

## Pending Verification (Non-Blocking Technical Debt)

These are not decisions — they are unresolved schema questions that require a single psql verification step.

| ID | Question | Risk | Target Version |
|---|---|---|---|
| U-01 | Does `wbom_candidates` exist in current production schema? | ✅ RESOLVED 2026-06-23 — EXISTS. 21 columns. See schema below. |
| U-02 | Does `fpe_transaction_repairs` have a DDL anywhere in production? | ✅ RESOLVED 2026-06-23 — EXISTS. 15 columns. FKs to `fpe_cash_transactions`. See schema below. |
| U-03 | Is `wbom_staging_payments` the same table as `fpe_staging_payments`, or two separate tables? | ✅ RESOLVED 2026-06-23 — `wbom_staging_payments` EXISTS (16 columns). `fpe_staging_payments` does NOT EXIST. They are NOT the same — only one table exists. |

---

## Unresolved Items — Resolution Record (2026-06-23)

Verified via `docker exec ai-postgres psql` on 2026-06-23.

### U-01: `wbom_candidates` — EXISTS ✅

21 columns. UNIQUE phone. 3 FK references from `fazle_recruitment_sessions`, `wbom_candidate_conversations`, `wbom_recruitment_reminders`.

Key columns: `candidate_id`, `phone` (UNIQUE), `full_name`, `age`, `area`, `job_preference`, `experience_years`, `funnel_stage` (CHECK: new/collecting/scored/assigned/contacted/interviewed/hired/rejected/dropped), `collection_step`, `score` (0–100), `score_bucket` (hot/warm/cold), `assigned_recruiter`, `source`.

**KB action:** Add `wbom_candidates` schema to `recruitment_flow_system.md` — this table is the permanent candidate record (distinct from `fazle_recruitment_sessions` which is the temporary intake session).

### U-02: `fpe_transaction_repairs` — EXISTS ✅

15 columns. 3 FKs to `fpe_cash_transactions` (original txn, reversal txn, new txn). Tracks re-attribution of transactions to different employees.

Key columns: `transaction_id` (original), `old_employee_id`, `new_employee_id`, `repair_reason`, `match_method`, `reversal_txn_id`, `new_txn_id`, `review_needed`, `dry_run`, `repaired_by`.

**KB action:** Add `fpe_transaction_repairs` schema to `fpe_overview.md` — documents the repair/re-attribution audit trail.

### U-03: `wbom_staging_payments` vs `fpe_staging_payments` — ONE TABLE ONLY ✅

`wbom_staging_payments` EXISTS (16 columns). `fpe_staging_payments` does NOT EXIST.
These are NOT two separate tables — only `wbom_staging_payments` is in production.

16 columns: `staging_id`, `message_id`, `sender_number`, `extracted_name`, `extracted_mobile`, `amount`, `payment_method`, `transaction_type`, `matched_employee_id`, `name_match_ratio`, `status` (default 'pending'), `approved_by`, `approved_at`, `created_at`, `final_transaction_id`, `idempotency_key`.
FK: `final_transaction_id` → `fpe_cash_transactions.id` (canonical per Owner Directive 2026-06-29; was `wbom_cash_transactions.transaction_id` historically).

**KB action:** Update `database_rules.md` entry for C-02 — table name is `wbom_staging_payments` (not `fpe_staging_payments`). Correct domain assignment stands (CASH/FPE).

---

## Phase 4 Authorization (Session 3 — 2026-06-22)

| ID | Decision | Domain | Date |
|---|---|---|---|
| P4-01 | Phase 4 Step 2 authorized: RAG enrichment wired into recruitment_ai and message_router (general fallback) | AI Runtime | 2026-06-22 |
| P4-02 | Phase 4 Step 3 authorized: structured_v2 prompt format (6-section) replacing flat _BASE prompt | AI Runtime | 2026-06-22 |
| P4-03 | Phase 4 Step 4 authorized: clean_general_reply() artifact stripping on general path output | AI Runtime | 2026-06-22 |
| P4-04 | Phase 4 Step 5 authorized: read-only audit producing module_alignment_report, organizational_brain_gap_report, kb_enrichment_plan_v2 | Governance | 2026-06-22 |

---

## Conflict Resolutions (Session 4 — 2026-06-23)

| ID | Conflict | Decision | Implementation | Date |
|---|---|---|---|---|
| CR-01 | CONFLICT-1: WhatsApp reply draft TTL | **24 hours** (DRAFT_TTL_HOURS=24). KB updated from "48 hours" to "24 hours". Escort roster drafts (48h) are a separate table and are unaffected. | KB: automation_pipeline.md updated | 2026-06-23 |
| CR-02 | CONFLICT-2: Hybrid RAG algorithm | **Production RRF is authoritative**. The 5-signal ranking description in hybrid_search.md was a design draft, never implemented. hybrid_search.md rewritten to document actual RRF implementation. | KB: hybrid_search.md rewritten | 2026-06-23 |
| CR-03 | CONFLICT-3: Age rule on social auto-reply | **Enforce BR-25 (18–55) on all channels including Facebook auto-reply**. AGE_ISSUE_REPLY updated to state policy. AGE_OUT_OF_RANGE_REPLY added for out-of-range ages. Age extraction logic added to intelligent_generator.py and reply_generator.py. | Code: reply_rules.py, intelligent_generator.py, reply_generator.py | 2026-06-23 |
| CR-04 | CONFLICT-4: Dual draft TTL documentation | **Documentation clarification only** — both TTLs are correct for their respective tables. automation_pipeline.md updated to clearly distinguish the two draft systems. | KB: automation_pipeline.md | 2026-06-23 |
| CR-05 | CONFLICT-5 (THREE-WAY): Escort daily rate ৳800/day vs ৳1,200/day vs PAY-01 formula | **PAY-01 formula confirmed: ৳400/day** (12,000 ÷ 30). Both hardcoded constants updated: payroll.DEFAULT_PER_PROGRAM_RATE = 400.0, payment_workflow.DEFAULT_DAILY_RATE = 400. | Code: payroll/__init__.py, payment_workflow/__init__.py | 2026-06-23 |
| CR-06 | CONFLICT-6: GitHub model name in KB (gpt-4o-mini vs gpt-4.1) | **gpt-4.1 is correct** (matches production .env GITHUB_MODEL_NAME=openai/gpt-4.1). automation_pipeline.md updated. | KB: automation_pipeline.md | 2026-06-23 |

---

## Wave-3 KB Enrichment Authorization (Session 5 — 2026-06-23)

| ID | Authorization | Scope | Date |
|---|---|---|---|
| W3-AUTH | Wave-3 Phase 1 KB Enrichment authorized | KB-only — NO production code changes. Covers TASK 1-B (system_prompt.md), TASK 1-D (workflow_engine.md), TASK 1-E (escort_roster_system.md), TASK 1-F (runtime_gateway_flags.md), TASK 1-P2-A (visibility_rules.md) | 2026-06-23 |

---

## Wave-3 Phase 2 Authorization (Session 6 — 2026-06-23)

| ID | Authorization | Scope | Date |
|---|---|---|---|
| W3P2-AUTH | Wave-3 Phase 2 Identity Brain Integration authorized | KB-only — NO production code changes. Covers TASK 2-A (identity_brain.md enrichment), TASK 2-B (visibility_rules.md Phase 3 section — confirmed complete in Phase 1), TASK 2-C (identity_integration.md new article) | 2026-06-23 |

---

## Phase 3 — Visibility Engine Authorization (Session 6 — 2026-06-23)

**Authorization type:** Production code change — per-file authorization required (GOV-03)

| ID | File / Change | Authorization | Date |
|---|---|---|---|
| P3-01 | `modules/rag/__init__.py` | Add `role: str = "candidate"` parameter to `search()`. Apply `allowed_roles` filter at BM25 and Qdrant query time. Backward-compatible default. | 2026-06-23 |
| P3-02 | `modules/recruitment_ai/__init__.py` | Update `_safe_rag_chunks()` call site to pass `role="candidate"` explicitly | 2026-06-23 |
| P3-03 | `modules/message_router/__init__.py` | Update Step 15 `rag.search()` call to pass resolved `role_str` | 2026-06-23 |
| P3-04 | `fazle_knowledge_base` table (DB) | Tag all active KB rows with `allowed_roles` via existing metadata JSON field — no DDL change required | 2026-06-23 |

**Scope:** Role-aware KB filtering in RAG path only. No changes to prompt builders, draft logic, identity brain, or outbound systems.
**Rollback:** Set `role="candidate"` at all call sites → current behavior restored immediately.

---

## Phase 5 Completion + KB v2 Certification (Session 7 — 2026-06-23)

**Authorization type:** Mixed — KB certification (read-only record) + one production code change (GOV-03) + one new KB article

| ID | Authorization | Scope | Date |
|---|---|---|---|
| KB-V2-CERT | KB v2.0 certified (GOV-06) | Read-only governance record. PKCA v2: 67% weighted overall, all P0+P1 modules ≥70%. PKMA v2: 4.88/5.0. PKVC v2: 0 real conflicts. | 2026-06-23 |
| PAY-03-FIX | `modules/escort_lifecycle/__init__.py` | Production code fix: separate Mongla from Faridpur in `_TRANSPORT_RATES`; set Mongla = ৳800 (from ৳700) to match PAY-03 management decision. Faridpur remains at ৳700. | 2026-06-23 |
| OUTBOUND-DOC | `knowledge_base/06_developer_system/outbound_delivery.md` | New KB article for `modules/outbound`. P0 module was at 30% coverage; this article brings it to full coverage. | 2026-06-23 |

---

## Session 8 — Fallback Fixes + Salary Policy (2026-06-23)

**Authorization type:** Production code changes (3 conflict resolutions) + new KB article + DORMANT module analysis

### Conflict Resolutions (Session 8)

| ID | Conflict | Decision | Implementation | Date |
|---|---|---|---|---|
| CR-07 | CONFLICT-7: Age range in `knowledge_base` fallback said "১৮–৪৫" — violates BR-25 (18–55) | **Fix fallback to ১৮–৫৫** (BR-25 was set in Session 4 but fallback was never updated) | Code: `modules/knowledge_base/__init__.py` line 66 | 2026-06-23 |
| CR-08 | CONFLICT-8: Escort post-training salary in fallback said "৳১২,০০০–১৮,০০০" — management policy is ৳১২,০০০–৳১৭,০০০ | **Fix upper bound to ৳১৭,০০০** | Code: `modules/knowledge_base/__init__.py` line 101 | 2026-06-23 |
| CR-09 | CONFLICT-9: Survey Scout job description in fallback said "বেতন: ১০,০০০–১৮,০০০" — upper bound exceeds management policy | **Fix upper bound to ৳১৭,০০০** | Code: `modules/knowledge_base/__init__.py` line 28 | 2026-06-23 |

### Session 8 Authorizations

| ID | Authorization | Scope | Date |
|---|---|---|---|
| SAL-POL | Salary policy management-approved and documented | KB: `04_business_rules/salary_policy.md` created. Groups: (1) Probation/Training ৳১০,০০০–১৫,০০০; (2) Operational/Escort ৳১২,০০০–১৭,০০০; (3) Permanent ৳১৭,০০০–৳২৪,৭০০ | 2026-06-23 |
| DOM-ANALYZE | DORMANT module activation analysis authorized | Read-only analysis. Show activation impact before any code change. `payment_correction` and `conversation_layer` analyzed — see DORMANT Module Status section below | 2026-06-23 |

---

## DORMANT Module Status (Analyzed 2026-06-23)

### payment_correction (`modules/payment_correction/__init__.py`)

**Status:** Fully implemented, zero external callers (audited 2026-06-02)
**Functions:** `reverse_payment()`, `adjust_payment()`, `list_corrections()`
**DB tables required:** `fazle_payment_drafts` ✅, `fpe_cash_transactions` ✅ (canonical per Owner Directive 2026-06-29), `fazle_payment_correction_log` ✅ (migration `008_payment_correction.sql`)

**To activate — changes required:**
1. `modules/admin_commands/__init__.py`: Wire `REVERSE <draft_id> <reason>` → `payment_correction.reverse_payment()`
2. `modules/admin_commands/__init__.py`: Wire `ADJUST <draft_id> <new_amount> <method> [reason]` → `payment_correction.adjust_payment()`
3. Add `REVERSE` and `ADJUST` to `COMMAND_ROLE` dict with required role (recommended: `superadmin` for REVERSE, `admin` for ADJUST)
4. KB: Create `06_developer_system/payment_correction.md` documenting both commands
5. Test: Write unit tests for `reverse_payment()` edge cases (already reversed, non-approved status)

**Risk:** LOW — immutable ledger approach (writes counter-transactions, never deletes). Reversals are audited in `fazle_payment_correction_log`. Rollback: remove commands from `admin_commands` — no data damage.

**Management decision needed:** YES — authorize the two specific admin command wiring files before activation.

---

### conversation_layer (`modules/conversation_layer/__init__.py`)

**Status:** Shadow-only / read-only. "Intentionally not imported by the live message router."
**Functions:** `generate_recruitment_reply_shadow()`, `simulate_current_core_reply()`
**Purpose:** A/B testing framework — compares richer recruitment replies against current deterministic behavior. Never sends messages to users.

**To activate — changes required:**
1. `modules/message_router/__init__.py`: Import and call `generate_recruitment_reply_shadow()` alongside real reply generation (shadow mode — log only, do not send)
2. OR: Replace one recruitment reply path with `generate_recruitment_reply_shadow()` output (full activation — sends to users)
3. Read `modules/conversation_layer/recruitment.py` completely before deciding activation mode

**Risk:** MEDIUM (full activation) / LOW (shadow mode only). Shadow mode is safe — never reaches users. Full activation changes recruitment reply quality and format.

**Management decision needed:** YES — choose activation mode (shadow logging vs full replace) before any code change.

---

---

## Session 9 — conversation_layer Activation + OCR Fix + Phase 6 (2026-06-23)

### Management Decisions (Session 9)

| ID | Decision | Scope | Date |
|---|---|---|---|
| CONV-ACT | `conversation_layer` — **Full Activation** | Recruitment replies now served via `conversation_layer.generate_recruitment_reply()` (KB → RAG → Ollama LLM → safety filter). Replaces `recruitment_ai.generate_recruitment_reply()` in live router. | 2026-06-23 |
| PAY-CORR-NO | `payment_correction` — **NOT activated** | Decision: do not wire REVERSE/ADJUST commands. Module remains DORMANT. | 2026-06-23 |
| OCR-FIX | OCR release slip classification fix | Fix `_classify_slip()` in `modules/ocr_processor/__init__.py`: (1) add release-specific keywords, (2) check release_score BEFORE escort_score so tied release slips are not misclassified as escort slips. | 2026-06-23 |

### Files Authorized for Change (Session 9)

| ID | File | Change | Date |
|---|---|---|---|
| CONV-ACT | `modules/conversation_layer/__init__.py` | Add `generate_recruitment_reply()` full-mode wrapper; update module docstring | 2026-06-23 |
| CONV-ACT | `modules/message_router/__init__.py` | Line 43: change import from `recruitment_ai` to `conversation_layer` | 2026-06-23 |
| OCR-FIX | `modules/ocr_processor/__init__.py` | `_classify_slip()`: expand release_kw (17 terms), swap priority order (release before escort) | 2026-06-23 |

### Phase 6 Verification Results (Session 9)

| Item | Status | Finding |
|---|---|---|
| `recruitment_flow` dual-path vs KB | ⚠️ GAP NOTED | KB says Path 1 uses `intake_message()` (autosend=True), but `message_router` does NOT call `intake_message`. All eligible cases use `generate_recruitment_reply` only. `intake_message()` is fully implemented but unwired. Future activation candidate. |
| `wbom_payroll_runs` DDL | ✅ VERIFIED | 28-column schema confirmed via `\d wbom_payroll_runs`. Matches KB description in `database_rules.md`. status CHECK constraint: draft/reviewed/approved/locked/paid/cancelled. `payout_idempotency_key` UNIQUE (WHERE NOT NULL). |

### Session 9 Escort Release / OCR Root Cause

**Root cause of escort release failures:**
1. `_classify_slip()` had tie-breaking bug: `escort_score == best → escort_slip` checked BEFORE `release_score`. Release slips always contain escort vocabulary (escort name, vessel, lighter), causing them to tie or lose to escort_score and be misclassified as `escort_slip`.
2. `handle_ocr_release_slip()` was never called → no admin draft created → admin could not confirm release.
3. Fix: expand release keywords (17 terms including "food bill", "conveyance", "duty days", "ছাড়পত্র") + swap priority order → release checked first.

**Admin confirmation flow (how it should work post-fix):**
1. Employee sends release slip image via WhatsApp bridge
2. Bridge_poller OCRs image via media-processor (port 8090) — service is running ✅
3. `_classify_slip()` now correctly returns `"release_slip"` → `handle_ocr_release_slip()` called
4. Draft saved to `fazle_draft_replies` with `[RELEASE CONFIRMED]...` text (built by `build_release_draft()`)
5. Admin receives draft in queue → sends `APPROVE <draft_id>` 
6. System sends `[RELEASE CONFIRMED] End Date: ... Escort: ...` to employee's chat (outbound, is_from_me=1)
7. Bridge_poller reads outgoing message → `is_release_confirmation()` → `handle_admin_release_confirmation()` → release finalized

---

## Session 10 — escort_slip_extractor Wiring (2026-06-23)

### Management Authorization (Session 10)

| ID | Decision | Scope | Date |
|---|---|---|---|
| ESX-WIRE | `escort_slip_extractor` — **Wired into bridge_poller** | Replace `ocr_processor.process_image()` with `escort_slip_extractor.extract_escort_slip()` in bridge_poller image OCR path. Release detection: `completion_date is not None` (two-date rule). Admin draft via `handle_ocr_release_slip()` unchanged. | 2026-06-23 |

### Files Authorized for Change (Session 10)

| ID | File | Change | Date |
|---|---|---|---|
| ESX-WIRE | `modules/bridge_poller/__init__.py` | Image STEP 2: replace `_proc_img(file_path, None)` with `extract_escort_slip(file_path, source_label=bridge_name)`. Replace keyword-based `slip_type` check with `completion_date is not None` for release detection. Remove `slip_type == "duplicate"` case (not a concept in escort_slip_extractor). Use `raw_ocr_text` field name. | 2026-06-23 |

### Technical Notes (Session 10)

- `EscortSlipResult` → `handle_ocr_release_slip()` compat dict: `escort_name→employee_name`, `lighter_vessel→vessel`, `completion_date→date`, `release_place→location`, `confidence×100→confidence_score`, `raw_ocr_text→raw_text`
- Unknown document threshold: `doc_type == "unknown_document" OR conf_pct < 10` (replaces `slip_type == "unknown"`)
- Assignment slip path: uses `raw_ocr_text` (was `raw_text`) — field name corrected

---

## Session 11 — Production Full Activation (2026-06-23)

### Management Authorization (Session 11)

User instruction: "start now. execute all. one by one." — blanket authorization for all production activation steps listed below. Each flag authorized individually.

| ID | Decision | Details | Date |
|---|---|---|---|
| S11-01 | `PRIMARY_AI_PROVIDER=groq` | GitHub Models rate-limited (429 Too Many Requests). Switch primary to Groq (llama-3.1-8b-instant, confirmed at 135ms). GitHub Models remains fallback. | 2026-06-23 |
| S11-02 | `AUTO_REPLY_ENABLED=true` | Activate live WhatsApp auto-reply. System will send AI-generated responses to all inbound messages on bridge1, bridge2. `DRAFT_QUALITY_GATE=true` remains — replies go through quality gate before sending. | 2026-06-23 |
| S11-03 | `SOCIAL_AUTO_REPLY_SINGLE_ENGINE=true` | Route inbound bridge messages through social daemon (system_agent port 8300). Health verified before enabling. | 2026-06-23 |
| S11-04 | `AGENT_PROACTIVE_OUTBOUND_ENABLED=true` | Enable proactive outbound messages from system_agent. Requires removal of dry-run mode from fazle-agent.service. | 2026-06-23 |

### Files Authorized for Change (Session 11)

| ID | File | Change |
|---|---|---|
| S11-01 | `.env` | `PRIMARY_AI_PROVIDER=groq` |
| S11-02 | `.env` | `AUTO_REPLY_ENABLED=true` |
| S11-03 | `.env` | `SOCIAL_AUTO_REPLY_SINGLE_ENGINE=true` |
| S11-04 | `.env` | `AGENT_PROACTIVE_OUTBOUND_ENABLED=true` |
| S11-04 | `/etc/systemd/system/fazle-agent.service` | Remove dry-run environment variable if present |

---

## Change Log

| Date | Change | Authorized By |
|---|---|---|
| 2026-06-22 | All entries above created from Session 1 governance decisions | Management |
| 2026-06-23 | Phase 4 authorization entries added (P4-01 through P4-04) | Management |
| 2026-06-23 | Conflict resolutions CR-01 through CR-06 added; all implemented same session | Management |
| 2026-06-23 | Wave-3 Phase 1 KB enrichment authorized (W3-AUTH); execution begins TASK 1-B | Management |
| 2026-06-23 | Wave-3 Phase 2 Identity Brain Integration authorized (W3P2-AUTH); execution begins TASK 2-A | Management |
| 2026-06-23 | Wave-3 Phase 2 COMPLETE — identity_brain.md (307 lines), identity_integration.md (194 lines, new), visibility_rules.md Phase 3 section confirmed; commit 510cd15 | Management |
| 2026-06-23 | Phase 3 COMPLETE — role-aware RAG search() wired; 3 files changed; commit 359812c | Management |
| 2026-06-23 | Wave-4 Phase 4 KB Coverage 90%+ authorized (W4-AUTH); KB-only — NO production code changes | Management |
| 2026-06-23 | Wave-4 COMPLETE — Group A (13 articles: P2-B/C/D/E/F/G/H/I/J/K + bridge_poller/ocr/contact_sync/admin_ops), Group B (payroll_module new, fpe_overview/recruitment_flow_system DDL updates), Group C (admin_commands_detail new), Group D (social_auto_reply exact specs); commits 0947ee7→f419135 | W4-AUTH |
| 2026-06-23 | Phase 5 COMPLETE — PKCA/PKMA/PKVC v2 audit reports written; commit 3ef2c11 | W4-AUTH |
| 2026-06-23 | KB v2.0 CERTIFIED (GOV-06); PAY-03 production fix (Mongla ৳700→৳800); outbound_delivery.md created | Management |
| 2026-06-23 | CR-07/CR-08/CR-09: knowledge_base fallback fixed (age 45→55, escort upper ৳18k→৳17k, survey scout ৳18k→৳17k); salary_policy.md created; DORMANT module impact analyzed | Management |
| 2026-06-23 | Session 9: conversation_layer Full Activation; OCR classification fix (_classify_slip release priority); Phase 6 verifications complete; payment_correction deferred | Management |
| 2026-06-23 | Session 10: escort_slip_extractor wired into bridge_poller (ESX-WIRE); replaces ocr_processor in image path | Management |
| 2026-06-23 | Session 11: Production activation — AUTO_REPLY_ENABLED=true, SOCIAL_AUTO_REPLY_SINGLE_ENGINE=true, AGENT_PROACTIVE_OUTBOUND_ENABLED=true; PRIMARY_AI_PROVIDER=groq (GitHub Models rate-limited); fazle-agent dry-run removed | Management |

---

## Session 12 — Ollama Primary Runtime (2026-06-24)

### Management Authorization (Session 12)

User instruction: "Make Ollama as primary, also think about a ollama_daemon.py for better performance."

| ID | Decision | Details | Date |
|---|---|---|---|
| S12-01 | `PRIMARY_AI_PROVIDER=ollama` | Ollama is the primary runtime AI provider. Fallback order is now Ollama → Groq → GitHub Models. | 2026-06-24 |
| S12-02 | `app/ollama_daemon.py` | Add in-process Ollama daemon helper with shared keep-alive HTTP clients, startup warmup, diagnostics, and clean shutdown. | 2026-06-24 |

### Files Authorized for Change (Session 12)

| ID | File | Change |
|---|---|---|
| S12-01 | `.env` | `PRIMARY_AI_PROVIDER=ollama` |
| S12-01 | `app/config.py` | Default provider changed to Ollama; fallback comments updated. |
| S12-01 | `app/llm.py` | Provider order changed to Ollama → Groq → GitHub Models for classification, replies, recruitment, and Chat Lab. |
| S12-02 | `app/ollama_daemon.py` | New shared-client Ollama helper. |
| S12-02 | `app/ollama.py` | Ollama calls now use shared daemon HTTP client instead of creating a new client per request. |
| S12-02 | `app/main.py` | Startup warmup and shutdown cleanup for Ollama daemon. |

---

## Session 13 — Reply-Gap Fix + 6-Phase Bugfix Plan (2026-07-15)

### Management Authorization (Session 13)

Owner (superadmin) directed a 6-phase fix plan for 5 outstanding audit findings, explicitly
authorized as a management decision without a separate approval round per phase.

| ID | Decision | Details | Date |
|---|---|---|---|
| S13-01 | Social auto-reply draft policy = **Selective**, not blanket | Only senders matching `DRAFT_ALWAYS_ROLES`/`DRAFT_ALWAYS_PHONES`/`DRAFT_ALWAYS_NAMES` (`modules.bridge_poller._is_draft_always()`) get routed to an admin-approval draft. Everyone else gets a real auto-send via `enqueue_reply()` → `social_reply_queue`, matching pre-2026-06-23 behavior. This supersedes the interim blanket-draft-for-everyone behavior briefly shipped the same day in commit `7bbe641` — that was never the intended end state, just a safety-first placeholder while the underlying disabled-send-worker bug was being root-caused. | 2026-07-15 |
| S13-02 | `mark_paid()` must post to canonical `fpe_cash_transactions`, fail closed if unlinked | Payroll payouts now write a `create_transaction()` ledger entry via the `fpe_employees.wbom_employee_id` bridge; if no active FPE employee is linked, the run is not marked paid rather than completing with a missing ledger entry. | 2026-07-15 |
| S13-03 | Settlement formula: prorate office-staff basic salary by attendance | `basic_salary ÷ calendar_days_in_month × approved_duty_days`, wired into `compute_run(..., prorate_basic=True)`, called only from `compute_all_for_period_office_only()` (still gated behind `office_payroll_authoritative_enabled=False` — 0 real eligible employees exist in production as of this date, so the flag stays off pending real-data validation). Escort-inclusive `compute_all_for_period()` is unaffected. | 2026-07-15 |
| S13-04 | WBOM status case-mismatch fix | `modules/fazle_payroll_engine/employee.py`'s WBOM cross-lookup now compares `LOWER(status)` — was a permanent no-op against Title-case `wbom_employees.status` values. | 2026-07-15 |

### Files Authorized for Change (Session 13)

| ID | File | Change |
|---|---|---|
| S13-01 | `modules/social_auto_reply/planner_worker.py` | Added `_is_draft_always_sender()` gate; restored `enqueue_reply()` real-send path for non-draft-always senders. |
| S13-02 | `modules/payroll/__init__.py` | `_transition()`/`mark_paid()` now call new `_post_payroll_ledger_entry()`; fail-closed FPE-link check added. |
| S13-03 | `modules/payroll/__init__.py` | `compute_run()` gained `prorate_basic` param + `_prorated_basic()` helper; `compute_all_for_period_office_only()` passes `prorate_basic=True`. |
| S13-04 | `modules/fazle_payroll_engine/employee.py` | Case-insensitive status comparison in the WBOM cross-lookup query. |

---

## Session 14 — Payroll "View All Transactions" Fix (2026-07-16)

### Management Authorization (Session 14)

Owner (superadmin) approved the read-only audit's proposal ("Approve as-is") for the
Employees → side panel → "View All Transactions" button, which was firing a redundant
unfiltered fetch racing against the filtered one and never clearing the default
current-month period filter, and had no URL/deep-link support at all for an
employee-scoped transactions view.

| ID | Decision | Details | Date |
|---|---|---|---|
| S14-01 | Single-fetch, filter-clean navigation to employee transactions | Button now clears all transaction filters (period/method/from/to/category) before setting `employee_id`, then triggers exactly one `loadTransactions()` call via `navigate()` — removes the prior double-fetch race. | 2026-07-16 |
| S14-02 | Frontend router gains optional employee-ID path segment | `tabFromPath()`/`pathForTab()`/`activateTab()` extended so `/payroll/transactions/{id}` is parsed on load/deep-link/refresh and pre-populates the employee filter; all other tab paths unaffected. | 2026-07-16 |
| S14-03 | Backend route `/payroll/transactions/{employee_id}` | New int-typed FastAPI route serving the same `payroll.html` SPA shell, mirroring the existing `payroll_spa_tab` pattern. Employee **ID** used, not raw name, to avoid display-name collisions. | 2026-07-16 |

### Files Authorized for Change (Session 14)

| ID | File | Change |
|---|---|---|
| S14-01 | `app/static/payroll.html` | Replaced inline onclick on the "View All Transactions" button with a named handler that clears filters and fires a single load. |
| S14-02 | `app/static/payroll.html` | `tabFromPath()`, `pathForTab()`, `activateTab()` updated for optional employee-ID segment on the `transactions` tab. |
| S14-03 | `app/main.py` | New `@app.get("/payroll/transactions/{employee_id}")` route. |
| S14-03 | `docs/DEVELOPER_GUIDE.md` | Route list updated to document the new employee-scoped deep link. |

---

## Session 15 — Employee Edit Salary/Designation/Joining-Date Sync Fix (2026-07-16)

### Management Authorization (Session 15)

Owner (superadmin) reported being unable to update Salary/Designation/Joining Date from
the Employees panel Edit form. Audit found the edits were being written successfully to
`wbom_employees` but never propagated to `fpe_employees` — the table every display surface
(Employees list, Employee Detail panel) actually reads — so saved changes were invisible.
Status already had a working WBOM→FPE sync (`deactivate_employee`/`reactivate_employee`);
Salary/Designation/Joining Date did not. Owner approved ("Approve as-is") extending the
same sync pattern to those three fields.

| ID | Decision | Details | Date |
|---|---|---|---|
| S15-01 | WBOM→FPE sync for designation/basic_salary/joining_date | `update_employee()` (PUT `/api/admin/employees/{id}`) now propagates these three fields to the linked `fpe_employees` row after the `wbom_employees` write, matched by `wbom_employee_id` (the FK — more precise than the phone-match the status sync uses). Best-effort/non-fatal, same pattern as the existing status sync. | 2026-07-16 |
| S15-02 | Fix pre-existing `joining_date` 500 on `PUT /api/admin/employees/{id}` | Found live during S15-01 smoke-testing: `EmployeeUpdate.joining_date` is a plain `str` (per its own docstring) but `wbom_employees.joining_date`/`fpe_employees.joining_date` are `DATE` columns — asyncpg does not auto-cast, so any non-empty `joining_date` in the PUT body 500'd, predating this session entirely. Now parsed via `date.fromisoformat()` before binding (400 on bad format instead of 500). In scope of the same authorized function/endpoint, not a separate change. | 2026-07-16 |

### Files Authorized for Change (Session 15)

| ID | File | Change |
|---|---|---|
| S15-01 | `modules/admin_employees/__init__.py` | `update_employee()` gained a post-write sync step propagating designation/basic_salary/joining_date to `fpe_employees` via `wbom_employee_id`. |
| S15-02 | `modules/admin_employees/__init__.py` | `update_employee()` now parses `joining_date` into a `date` object before the SQL bind, instead of passing the raw string through. |

---

## Session 16 — Escort Sender Validation Audit & Historical Cleanup (2026-07-16)

### Management Authorization (Session 16)

Owner requested an audit of escort-roster S/N provenance (869, 871, 873, 880 and others)
against 4 non-negotiable business rules on sender validation, draft lifecycle, and
source-of-truth. Full discovery + 6-part proposal written to
`knowledge_base/00_governance/escort_sender_validation_audit_2026-07-16.md`. Key finding:
the requested sender-validation gates (Rules 1-2) and 24h hard-delete (Rule 3) were
**already implemented** in commits `41916b0` (2026-06-27) and `82f2b33` (2026-07-05) —
verified via `git log -S` and zero unauthorized-sender/soft-expired rows created since
those dates. The only live gap was 18 historical rows predating the 2026-06-27 gate that
were never retroactively swept. Owner approved the full proposal ("Approve full
proposal") as scoped down in that doc.

| ID | Decision | Details | Date |
|---|---|---|---|
| S16-01 | Delete 18 historical unauthorized-sender `wbom_escort_programs` rows | program_id: 155, 213, 214, 219, 229, 230, 765, 769, 774, 779, 808, 840, 851, 860, 869, 871, 873, 880 — all pre-date the 2026-06-27 sender-validation gate, all verified financially inert (no `escort_employee_id`, zero `fpe_cash_transactions` references). Full row content logged to `escort_roster_audit_logs` (action `manual_unauthorized_sender_cleanup`) before each delete. `940` (`is_historical=true`) and the 5 known-sender expired drafts explicitly NOT touched, per Owner's constraint. | 2026-07-16 |
| S16-02 | Defensive sweep for legacy `status='expired'` rows | `expire_stale_drafts()` in `modules/escort_roster/db.py` extended to also catch any *future* financially-inert `status='expired'` row past TTL, so the "stuck in expired limbo" failure mode (root cause of S16-01's backlog) can't recur even in an edge case. Not required by current evidence — pure defensive addition per Rule 3's "no exceptions" framing. | 2026-07-16 |

### Files Authorized for Change (Session 16)

| ID | File | Change |
|---|---|---|
| S16-01 | *(data only, one-time)* | 18 rows deleted from `wbom_escort_programs` + `escort_roster_entries` via `scripts/cleanup_unauthorized_sender_escort_rows_20260716.py`, audit-logged first. |
| S16-02 | `modules/escort_roster/db.py` | `expire_stale_drafts()` gained a second sweep for legacy `status='expired'` rows past TTL. |
| S16-02 | `knowledge_base/05_workflows/escort_source_of_truth_workflow.md` | Documented the sender-validation gate functions and 24h hard-delete behavior by name. |
| S16-02 | `knowledge_base/06_developer_system/escort_roster_system.md` | Added `expired` row to the program-status → roster-status mapping table. |

---

## Session 17 — Escort Audit Phase 2: Legacy Drafts, S/N 777, Historical & Duplicate Review (2026-07-16)

### Management Authorization (Session 17)

Owner directed Phase 2 cleanup and review, 5 items. Full findings in
`knowledge_base/00_governance/escort_sender_validation_audit_phase2_2026-07-16.md`.
No code changes were needed (S/N 777's parser bug confirmed non-reproducible against
current code), so per Owner's instruction this session is data cleanup + documentation
only.

| ID | Decision | Details | Date |
|---|---|---|---|
| S17-01 | Delete 10 legacy known-sender expired drafts | program_id: 141, 759, 760, 761, 763, 764, 768, 770, 771, 777 — registered sender, pre-2026-06-27, abandoned mid-order, verified financially inert. Audit-logged (`manual_legacy_expired_cleanup`) before delete. | 2026-07-16 |
| S17-02 | S/N 777 reproducibility test — no code change | Raw message recovered from `wbom_whatsapp_messages`, replayed through current `parse_escort_message()` in isolation. The original "label leakage into lighter_vessel" defect does not reproduce; `is_minimum_escort_order()` now correctly rejects the message outright (no mother vessel stated) instead of saving a corrupted row. Folded into S17-01's delete batch. | 2026-07-16 |
| S17-03 | Historical backfill 9-row review — recommendations only, no edits made | 699/702/704/718/727 = keep (blank master_mobile is correct per source text). 703/707 = flagged for admin input (unclaimed phone-like number near lighter name, ambiguous vs Escort Mobile). 734 = flagged for admin review (no MV label in source at all, most ambiguous). 933 = recommend delete as duplicate of 934 (933 is the system's own internal admin-review alert text, accidentally backfilled as if it were a real confirmation) — **not deleted, pending sign-off**. | 2026-07-16 |
| S17-04 | Duplicate review corrected: 6 of 8 pairs are not duplicates | Original detection grouped by mv+lv+master only, missing `program_date` — 58/136, 60/107, 62/93, 900/905, 901/906 all have different program_dates (legitimate separate runs of a recurring vessel/lighter pairing), not duplicates. Only 1/9 and 3/10 (same date, 10 minutes apart, same 2026-04-18 bulk-seed batch) look like genuine accidental duplicates. **No deletions made** for 1/9, 3/10, or 933/934 — all three pending explicit sign-off. | 2026-07-16 |
| S17-05 | master_mobile blank-field rule documented from KB | `escort_business_rules.md`: Master Mobile is a required *draft* field but NOT a required *confirmation* field. Rows created via `_create_confirmed_from_admin()` (`auto_created=true`) can legitimately have a blank master_mobile; rows that went through a real draft first should not. The 189 flagged rows were not individually reclassified — report only, per Owner's instruction. | 2026-07-16 |
| S17-06 | New finding, out of scope, not acted on | 900/901/905/906 all have `destination` containing leaked `"Escort Name: X"` text instead of a place name. Reproducibility unconfirmed (no matching raw message found in `wbom_whatsapp_messages` to replay). Flagged for a possible future follow-up audit; no action taken this session. | 2026-07-16 |

### Files Authorized for Change (Session 17)

| ID | File | Change |
|---|---|---|
| S17-01 | *(data only, one-time)* | 10 rows deleted via `scripts/cleanup_legacy_expired_escort_rows_20260716.py`, audit-logged first. |
| — | *(no code changes this session)* | S/N 777 confirmed non-reproducible; no fix needed. |

---

## Session 18 — Escort Duplicate Cleanup + Issue Tracking (2026-07-16)

### Management Authorization (Session 18)

Owner approved deleting the 3 confirmed duplicate rows identified in Phase 2, declined
to auto-correct the 3 ambiguous historical rows (703, 707, 734), and asked for the
destination-field-leak finding to be tracked as a documented-but-unfixed issue rather
than investigated further right now. Data-only session, no application code touched, no
service restart performed.

| ID | Decision | Details | Date |
|---|---|---|---|
| S18-01 | Delete 3 confirmed duplicate rows | program_id 933 (dup of 934), 1 (dup of 9), 3 (dup of 10). Re-verified zero references across every FK-linked table (`fpe_cash_transactions`, `wbom_cash_transactions`, `wbom_billing_records`, `wbom_cases`, `wbom_whatsapp_messages`, `fazle_payment_drafts`, `escort_order_lighters`) before deleting. `escort_shift_logs` (7 rows each for 1 and 3, no FK) deleted alongside. `escort_roster_entries` for 1/3 had non-zero computed `total`/`net_payable` (৳1,400 / ৳3,000) — flagged explicitly since this differs from every other row removed in this audit; confirmed no actual payout (`fpe_cash_transactions`) existed, and the kept sibling (9/10) carries the identical computed amount, so nothing is lost. Audit-logged (`manual_duplicate_cleanup`) before each delete. | 2026-07-16 |
| S18-02 | 703/707/734 left as-is | Owner directive: do not guess at field ownership when raw text is ambiguous. Review note added to the Phase 2 KB doc instead of an edit. | 2026-07-16 |
| S18-03 | Destination-field leak tracked as a separate open issue | New doc `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md` — evidence (4 known rows), what was checked (could not locate the source admin messages to test reproducibility, unlike S/N 777), and a recommended-next-step list. No fix attempted, no proposal written — explicitly a documentation-only holding doc per Owner's instruction. | 2026-07-16 |

### Files Authorized for Change (Session 18)

| ID | File | Change |
|---|---|---|
| S18-01 | *(data only, one-time)* | 3 rows deleted via `scripts/cleanup_confirmed_duplicate_escort_rows_20260716.py`, audit-logged first. |
| S18-02 | `knowledge_base/00_governance/escort_sender_validation_audit_phase2_2026-07-16.md` | Added review note for 703/707/734; updated §6 deleted-IDs summary. |
| S18-03 | `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md` | New — issue tracking doc, no code change. |
| — | *(no application code changes this session)* | Data-only; no service restart performed. |

---

## Session 19 — Post-Audit Governance Backfill + Implementation Queue (2026-07-17)

### Management Authorization (Session 19)

Owner directed implementation of `docs/AUDIT_REPORT_2026-07-17.md`'s recommended
session plan, one step at a time with a commit and doc update after each. This entry
retroactively records two schema migrations that shipped during the 2026-06-24→2026-07-15
governance-log gap the audit found (§1A) — both were legitimate, Owner-scoped changes,
just never logged here at the time.

| ID | Decision | Details | Date |
|---|---|---|---|
| S19-00a | Retroactive record: Migration 043 (`office_assistant_role`) | Formalizes `office_assistant` as a real `fazle_roles` row (level 20, between `viewer`/10 and `operator`/30): view attendance list/own records, create attendance draft entries only — no review/approve/edit/salary visibility. Replaces the prior ad-hoc name-string check (`_OFFICE_ASSISTANT_NAMES` in `modules/admin_transactions/__init__.py`). Corrected both known office-assistant accounts (`officeassistant01`, `officeassistant02`) from inconsistent prior roles (`operator`/`viewer`) to this one properly-scoped role — a real permission *reduction* for `officeassistant01` (lost stray `operator`-level edit/approve power), which was the intended fix. Shipped in commit `aea5679` (2026-07-11), no governance entry made at the time. | 2026-07-11 (retroactive) |
| S19-00b | Retroactive record: Migration 046 (`fpe_employee_edit_requests`) | New `fpe_employee_edit_requests` table backing `payroll.html`'s Employees-tab edit flow for non-admin roles: admin/superadmin edits still write directly to `fpe_employees`/`wbom_employees` (unchanged), any other role's edit is queued here (`changes` JSONB diff, `pending`/`approved`/`rejected` status) for an admin to approve/reject. Deliberately a new table rather than reusing the Operator-submission pattern's storage (shape mismatch — that one requires a synthetic WhatsApp-message FK row). Named `edit_requests`, not `review_queue`, to avoid colliding with the existing parser-confidence "Review Queue" tab. Shipped in commit `fbf1204` (2026-07-14), no governance entry made at the time. **Compatibility confirmed with Session 15** (`admin_employees/__init__.py` WBOM→FPE sync for designation/basic_salary/joining_date, 2026-07-16): S15's sync sits *after* a direct admin/superadmin write in the same function this migration's approve-handler also writes through — both changes are additive to different branches (direct-edit vs. queued-request-then-approve) of the same underlying update logic, not conflicting. | 2026-07-14 (retroactive) |
| S19-00c | Note: `open-webui` Groq API key still plaintext | Confirmed still open (`/home/azim/docker-compose.yml`, outside this repo) — same class of issue as the already-rotated Postgres password, smaller blast radius. Deferred to its own dedicated rotation task, not bundled into this implementation queue (Owner decision, 2026-07-17). | 2026-07-17 |

### Files Authorized for Change (Session 19, Step 0)

| ID | File | Change |
|---|---|---|
| S19-00a/b/c | `knowledge_base/00_governance/management_decisions.md` | This entry — retroactive documentation only, no code/schema change (migrations 043/046 were already live in production). |

### Management Authorization (Session 19, Step 1)

| ID | Decision | Details | Date |
|---|---|---|---|
| S19-01 | Webhook path gains the poller's legacy-workflow gate; dedup guard changed to fail closed | `docs/AUDIT_REPORT_2026-07-17.md` §1B-iv found the webhook path (`/webhook/mcp1`/`/webhook/mcp2`) had no equivalent to the SQLite poller's `_requires_legacy_workflow()` pre-filter, so an inbound escort/operational message could be classified by the social auto-reply pipeline and never reach booking creation — the exact failure mode that gate was built to prevent on the poller side. `app/main.py::_handle_bridge_event()` now applies the same gate before calling `ingest_social_event()`. Separately, the P15-03 dedup guard's exception handler previously logged at DEBUG and fell through to process the message anyway (fail-open) on any dedup-check error; it now logs at ERROR and skips the message (fail-closed) instead, since a double-processed message is worse than one skipped message the SQLite poller will likely still pick up independently. | 2026-07-17 |

### Files Authorized for Change (Session 19, Step 1)

| ID | File | Change |
|---|---|---|
| S19-01 | `app/main.py` | `_handle_bridge_event()`: added `and not _requires_legacy_workflow(text, {"media_type": media_type})` to the `ingest_social_event()` gating condition (mirrors `modules.bridge_poller._requires_legacy_workflow`, imported locally); dedup-check `except` block changed from log-and-fall-through to log-and-`continue`. |

**Verification status:** function-level only — `_requires_legacy_workflow()` confirmed (via a standalone import test) to correctly classify the exact escort-order message used in `tests/integration/test_webhooks.py::test_escort_order_creates_program` as requiring the legacy workflow, and a plain greeting as not. Full HTTP/integration-level verification (posting to the live `/webhook/mcp1` route, or running the pytest suite against the test DB) was **not possible this session** — no sudo access to restart `fazle-core.service`, and the test DB host (`172.20.0.3`) is unreachable from this session's network. **Pending:** run `scripts/restart_gate.sh --restart` and re-run `pytest tests/integration/test_webhooks.py -k escort_order` in a session with the necessary access before considering this fully verified live. **This restart is also the outstanding prerequisite for Step 1 above** — until it happens, the webhook-gate and fail-closed-dedup fixes exist only in the working tree, not in the running `fazle-core` process.

### Management Authorization (Session 19, Step 2)

| ID | Decision | Details | Date |
|---|---|---|---|
| S19-01a | OPTION A: Document `/webhook/mcp1`/`/webhook/mcp2` always-200 as intentional | Confirmed by code read (`app/main.py::_handle_bridge_event()`): a missing/empty `sender` field on an individual event is logged and skipped (`continue`), never raised — the route can only return 400 (unparseable JSON) or 200, by construction. Owner decision: keep this behavior, to avoid retry-storms from the upstream bridge client on ordinary malformed/incomplete events, rather than add per-event 422 validation. `tests/resilience/test_failure_recovery.py::test_missing_sender_field_returns_422` renamed to `test_missing_sender_field_returns_200` and its assertion updated to `== 200`, with an inline comment documenting the intent — this reflects a real decision, not a stale test being "fixed" back to old behavior. | 2026-07-17 |

**Bridge client script search (requested before this decision):** searched `.env` (`BRIDGE1_URL`/`BRIDGE2_URL` config keys only, no script path), `app/config.py` (only SQLite store paths, `bridge1_db_path` etc. — not the sending client), and the filesystem. Found the actual running bridge process: `whatsapp-bridge.service`/`whatsapp-bridge2.service` (systemd, both `active running`), `ExecStart=/home/azim/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`, a **compiled Go binary** with no `.go` source present in its git working tree (`git ls-files` in `/home/azim/whatsapp-mcp` returns no `.go` or webhook-related files — likely built from source not present on this disk, or from a fork of the public `whatsapp-mcp` project with local unstaged modifications). **Bridge client retry-on-non-200 behavior could not be confirmed** — the binary itself isn't inspectable without decompiling it, which wasn't attempted. Per Owner's instruction, this does not block the Option A decision (Option A doesn't depend on the answer — it's the safe default regardless of the bridge's actual retry behavior).

### Files Authorized for Change (Session 19, Step 2)

| ID | File | Change |
|---|---|---|
| S19-01a | `tests/resilience/test_failure_recovery.py` | `test_missing_sender_field_returns_422` renamed to `test_missing_sender_field_returns_200`; assertion changed from `status_code in (400, 422)` to `== 200`; intent comment added. |

**Verification status:** static/code-read only — confirmed via re-read of `_handle_bridge_event()` (unchanged by Step 1's edits in this respect) that a missing-sender event is always skipped, never raised, so the route's behavior for this payload is unconditionally 200. The test itself was not executed this session (test DB unreachable, same blocker as Step 1). No route code was changed for this step.

### Management Authorization (Session 19, Step 3)

| ID | Decision | Details | Date |
|---|---|---|---|
| S19-02 | Destination-field leak — root cause confirmed and fixed | `docs/AUDIT_REPORT_2026-07-17.md` §1C traced the bug to `_CD_DEST_RE` (`modules/escort/__init__.py`): `\s*` after the colon matches newlines, so a blank `Destination:` line lets the pattern spill into and capture the entire next line ("Escort Name: ..."). Confirmed by a standalone regex test against the real source message (message_id 10678). Fixed by changing `\s*` to `[ \t]*` around the colon and in the lookahead. Blast radius corrected from 4 rows (`escort_destination_field_leak_issue_2026-07-16.md`'s original count) to 6 — program_id 902 and 938 were previously uncounted; 938 came through a second consumer of the same parse output (`_update_program_confirmed`, not `_create_confirmed_from_admin`), confirming the bug wasn't confined to one write path. | 2026-07-17 |
| S19-03 | Data backfill: 6 corrupted `destination` values set to `NULL` | program_id 900, 901, 902, 905, 906, 938 — all had `destination` starting with `"Escort Name:"` (verified via `SELECT` immediately before the fix). `UPDATE` set `destination = NULL` (the correct value, since the admin genuinely left the field blank on all known source messages — a correction, not data loss) and tagged each row's `remarks` with `destination_leak_fix = "fixed_2026-07-17_step3_audit"` for traceability. `UPDATE` affected exactly 6 rows as expected, confirmed via a post-update `SELECT`. | 2026-07-17 |

### Files Authorized for Change (Session 19, Step 3)

| ID | File | Change |
|---|---|---|
| S19-02 | `modules/escort/__init__.py` | `_CD_DEST_RE`: `\s*` → `[ \t]*` around the colon and in the lookahead (one regex, both occurrences). |
| S19-02 | `knowledge_base/00_governance/escort_destination_field_leak_issue_2026-07-16.md` | Status changed from "open, unconfirmed" to "ROOT CAUSE CONFIRMED AND FIXED"; evidence table corrected to 6 rows; root cause, fix, and data-backfill sections added. |
| S19-03 | *(data only)* | `UPDATE wbom_escort_programs SET destination = NULL, remarks = ... WHERE program_id IN (900,901,902,905,906,938) AND destination LIKE 'Escort Name:%'` — 6 rows affected, verified before and after. |

**Verification status:** regex fix verified via 4 standalone tests (blank destination, real destination, blank-with-trailing-spaces, and the exact real message that produced row 938 — all pass) plus `ast`/`py_compile` syntax check. Data backfill verified via `SELECT` before (6/6 still corrupted) and after (6/6 now `NULL` with the fix marker) the `UPDATE`. **Not yet done:** full HTTP/live-service verification (posting a blank-destination admin confirmation through the running app) — pending sudo access to restart `fazle-core.service`, same blocker as Steps 1-2.

---

### Session 19 — Steps 1 and 3 confirmed LIVE (2026-07-17, 03:09:07 CEST restart)

Owner ran `sudo systemctl restart fazle-core` at 2026-07-17 03:09:07 CEST (confirmed via `systemctl status`: `Active: active (running) since Fri 2026-07-17 03:09:07 CEST`, Main PID 4169473). This restart picks up commits `b3fe767` (Step 1: webhook gate + fail-closed dedup) and `7f05de6` (Step 3: `_CD_DEST_RE` fix) — both are now live in the running process, not just the working tree.

**Post-restart health check:** `logs/fazle-core.log` for PID 4169473 shows a clean startup (`Application startup complete`, `Uvicorn running on http://127.0.0.1:8200`) followed only by routine `/health` polls and a benign `Unsupported upgrade request` warning (unrelated WebSocket probe, not a regression). `logs/fazle-core-error.log`'s most recent traceback (`asyncpg.exceptions.DataError`, a stale `toordinal`-shaped error) predates this restart (occurred under an earlier PID before 4169473 started) — not a new regression. (`journalctl -u fazle-core` was not readable without sudo in this session; verification used the log files CLAUDE.md documents instead.)

**Live smoke test — Step 1 (webhook gate) confirmed working end-to-end:**
- Posted an escort-order-shaped message (`"MV TEST VESSEL AUDIT\nlighter: TESTLIGHTER-9\nmaster mobile: 01911112222\nescort lagbe 20/07/2026 Day"`, sender `8801700000199`, a non-whitelisted test number) to `/webhook/mcp1`. Result: **zero rows** in `social_inbox_events` for this sender (confirmed via `SELECT`) — the message never entered the social auto-reply classifier at all. `logs/fazle-core.log` shows it reached `fazle.escort` instead, correctly rejected with `"ignore client order from non-whitelisted sender=8801700000199"` (the pre-existing, unrelated sender-validation gate doing its job) — i.e. the message was routed to the real escort pipeline, not silently classified as social and dropped.
- Control test: posted a plain greeting (`"hello, good morning"`, sender `8801700000288`) to the same route. Result: **one row** in `social_inbox_events` (`classification='greeting'`, `reply_status='pending'`) — confirming the normal social-auto-reply path is unaffected by the new gate.
- No entries in `logs/fazle-core-error.log` during either test's time window.

This is full end-to-end confirmation of the exact failure mode Section 1B-iv described (an escort order silently swallowed by social auto-reply on the webhook path) — the gate now prevents it, live, in production.

**Step 3 (destination-leak fix):** not separately smoke-tested with a live blank-destination admin-confirmation message this session (would require simulating an authoritative bridge confirmation from a real admin number, out of scope for a quick smoke test) — verification for this step remains the 4 standalone regex tests plus the completed data backfill recorded above. The regex fix is confirmed loaded in the running process (same restart, same commit range).

**Both Step 1 and Step 3 are now considered fully live**, not just committed to the working tree.

### Session 19, Step 4 — chat_history sync: staged files verified, install still pending manual sudo

**STATUS: NOT INSTALLED. `chat-sync.service` does not exist yet — nothing is running.** The steps below are pre-install verification only. This entry stays open until a follow-up entry records the actual `systemctl enable --now chat-sync.service` and its confirmed `active (running)` status.

**Column cross-check (requested detail):** the live `chat_history` table has 14 columns; `sync.py`'s `INSERT_SQL` writes 12 of them. The 2 not written are `id` (`NOT NULL`, but `DEFAULT nextval('chat_history_id_seq'::regclass)` — auto-generated PK) and `created_at` (nullable, `DEFAULT now()`) — both safe to omit (case (a): live-table-only columns with defaults, not missing-from-INSERT columns that the table requires). Every column that is `NOT NULL` with **no** default (`thread_id`, `direction`, `source_table`, `source_id`, `sent_at`) is present in `sync.py`'s column list. No INSERT-time error risk; no fix needed to `sync.py`.

Re-verified `~/chat_sync_staging/` (`sync.py`, `chat-sync.service`) against the live schema before recommending install:
- `sync.py`'s `INSERT_SQL` column list (`thread_id, direction, sender_phone, receiver_phone, message_body, message_type, bridge, purpose, status, source_table, source_id, sent_at`) matches the live `chat_history` table exactly (12 of its 14 columns — `id` and `created_at` correctly omitted as auto-populated).
- The `ON CONFLICT (source_table, source_id) DO NOTHING` clause is backed by a real constraint: `chat_history_source_table_source_id_key UNIQUE (source_table, source_id)`, confirmed present.
- `chat-sync.service`: `ExecStart=/home/azim/.venv/bin/python /opt/chat_sync/sync.py` — interpreter path confirmed to exist and resolve correctly (`/home/azim/.venv/bin/python → python3.11`). `Restart=always`/`RestartSec=10` present (a stronger policy than the originally-suggested `on-failure`, no change needed). No fixes required to either staged file.
- No schema drift since the files were staged 2026-07-15.

**Not installed this session** — requires interactive `sudo` (password prompt), which this session cannot supply. Exact install commands (for the Owner to run) and rollback commands are recorded in `docs/AUDIT_REPORT_2026-07-17.md` Part 2 Step 4 and were re-confirmed unchanged during this re-verification.

### CORRECTION (2026-07-17, same session): the above "NOT INSTALLED" status was wrong — the service was already running

Everything above this line was written based on Session 13's note ("blocked on sudo, not deployed") and this session's own file-level re-verification, **without first checking whether `chat-sync.service` already existed as a systemd unit.** That check was skipped — a real gap in this session's own Step 4 process, not just stale prior information.

When the Owner ran the install commands, `systemctl status chat-sync.service` revealed the service was **already `active (running)` since `Wed 2026-07-15 23:28:28 CEST`** — roughly 20 minutes after Session 13 staged the files (`~/chat_sync_staging/`, mtime 2026-07-15 23:11) and about 1.5 hours before Session 14 began the same night. **Someone or something installed and started this service in that window, entirely outside of any recorded Claude Code session** — no commit, no `management_decisions.md` entry, nothing. This is the same class of gap already found in Step 0 (undocumented migrations 043/046) — a third instance of a real production change happening with zero governance trail.

**Verified healthy over its full ~2-day undocumented run:**
- `chat_history` contains 9,156 rows (8,437 from `wbom_whatsapp_messages`, 719 from `fazle_outbound_queue`), spanning 2026-07-15 08:15:39 UTC → 2026-07-17 01:13:55 UTC (query run by the assistant) — continuous, real, still growing.
- `journalctl -u chat-sync.service -n 50 --no-pager` (run by the Owner, since the assistant has no sudo/journal-group access this session): every line from `chat_sync started, poll_interval=20s` (2026-07-15 23:28:28) through `synced 1 rows...` (2026-07-17 03:13:50) is `INFO`-level — **zero errors, zero warnings**, across the entire run.
- The final logged sync (`2026-07-17 03:13:50, synced 1 rows`) is the assistant's own Step 1 escort-gate smoke-test message (sent 03:13:38 CEST the same session) — confirming the full pipeline (webhook → `wbom_whatsapp_messages` → `chat_sync` → `chat_history`) is working end-to-end, live, right now.

The Owner's `sudo cp`/`systemctl enable --now` commands run this session did not restart anything (systemd does not restart an already-active unit on `--now`) — they overwrote `/opt/chat_sync/sync.py` with byte-identical content (already confirmed matching the staged version) and re-enabled the unit for boot, both no-ops on the already-correct running state.

**Corrected status: chat_history sync IS installed, IS running, and has been healthy for ~2 days. No further action needed for Step 4.**

### Files Authorized for Change (Session 19, Step 4)

| ID | File | Change |
|---|---|---|
| S19-04 | *(repo: none)* | No repo files changed. **System state (outside this repo, discovered already in place):** `/opt/chat_sync/sync.py`, `/etc/systemd/system/chat-sync.service` — both were already live since 2026-07-15 23:28:28, from an undocumented install predating this session; the Owner's commands this session (`sudo cp`, `systemctl enable --now`) overwrote them with byte-identical content, a no-op on the already-correct running state. |

### Session 19, Step 5 — Dual-write concurrency diagnostic (no code changed)

Documented all three writers to `escort_roster_entries` in
`knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md`:
(1) `escort_lifecycle`'s direct atomic `INSERT...ON CONFLICT` (safe, already
shipped); (2) `escort_roster.sync_program_to_roster()` — check-then-act
(SELECT then branch UPDATE/INSERT), the function the reverted 2026-07-13
refactor tried to route Writer 1 through, which is what surfaced the hang;
(3) `escort_roster.create_roster_entry()` — a previously-undiscussed third
writer (manual admin "add roster entry" tool) with its own two separate
check-then-act races (program_id collision, duplicate-entry bypass),
unrelated to the release-event path and not part of the 2026-07-13 refactor
attempt at all.

Ran `tests/workflows/test_escort_payment_flow.py::TestRaceConditions::test_concurrent_release_events`
against current (reverted) code: **passed, no hang** — expected, since this
test currently exercises only the already-safe Writer 1 path. Reproducing
the actual 2026-07-13 hang would require temporarily reintroducing the
reverted refactor, which this diagnosis-only step was not authorized to do.
No `pg_locks` snapshot captured (no hang window existed to observe). Test DB
connectivity note: unreachable via a raw TCP probe from this session's shell,
but reachable by `pytest` itself with `TEST_DB_PASSWORD` set correctly —
recorded in the KB doc for future sessions.

**No code changed** in `modules/escort_lifecycle/__init__.py` or
`modules/escort_roster/db.py` this step, per instruction.

### Files Authorized for Change (Session 19, Step 5)

| ID | File | Change |
|---|---|---|
| S19-05 | `knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md` | New — three-writer comparison table, test-run result, recommended next step for an actual fix session. |

### Session 19, Step 6 — Hardcoded admin bridge numbers replaced with settings references

`docs/AUDIT_REPORT_2026-07-17.md` §1D found the literal `"8801880446111"` (bridge2/OPS admin number) hardcoded in 5+ production files instead of reading `settings.admin_bridge2_number`, plus its bridge1 counterpart `"8801958122300"` hardcoded alongside it in several of the same files. Existing `Settings` fields already covered both (`app/config.py:72` `bridge2_number`, `:165` `admin_bridge2_number`, `:164` `admin_bridge1_number`) — no new config needed, no permission required.

**7 files fixed, one at a time, each read/edited/syntax-checked/value-confirmed before moving to the next:**
1. `shared/bridge_orchestrator.py` — `ADMIN_SELF_NUMBER`/`ADMIN_SELF_JID` module constants now derive from `get_settings().admin_bridge2_number`.
2. `app/main.py` — `_AGENT_FORWARD_PHONES` now `set(settings.admin_number_list)` instead of a hardcoded 2-element set.
3. `modules/fazle_payroll_engine/diagnostics.py` — stale-bridge admin alert now resolves `get_settings().admin_bridge2_number` via a local import (matching this file's existing local-import style).
4. `modules/escort_roster/routes.py` — `/backfill-sqlite` route now passes `_settings.bridge2_number` (an already-resolved `_settings` object existed at this call site).
5. `modules/fazle_payroll_engine/historical_sync.py` — `_BRIDGE_CONFIGS`' `source_number` fields now derive from `_settings.admin_bridge1_number`/`admin_bridge2_number`.
6. `modules/fazle_payroll_engine/replay.py` — `_do_replay()`'s `source_number` now resolves `get_settings().admin_bridge1_number` via a local import.
7. `modules/contact_sync/__init__.py` — `BRIDGE_SOURCES`' `number` fields now derive from `_settings.admin_bridge1_number`/`admin_bridge2_number`.

**8th fix, found by the final verification grep, not in the original audit list:** `app/main.py`'s Facebook-comment recruitment auto-reply had a hardcoded `wa.me/8801958122300` in the Bangla reply text sent to commenters — same drift risk (a number rotation would silently send users to a dead link). Fixed to `wa.me/{settings.admin_bridge1_number}`.

**`app/critical_numbers.py` — investigated per request, nuanced finding, not modified.** The *module* is live (imported by `modules/number_identity/__init__.py`, which is itself in the live import graph via `message_router`/`escort`/`message_archive`/`reviewed_reply_memory`/`shared/phone.py`) — but only for its generic `normalize_phone_880()` helper. The `CRITICAL_NUMBERS`/`CRITICAL_NUMBERS_RAW` hardcoded 20-number list itself is **dead in the live app process** — its only consumers are `recover_critical_numbers.py` (a standalone one-off script, not part of the running service) and the already-archived `archive/deprecated/20260602_gap_detector/__init__.py`. The real live "is this critical" check has moved to `number_identity.is_critical_phone()`, which reads `settings.critical_phone_set` (`.env`-driven) instead. No `# DEAD FILE` comment added — that would misdescribe a file that's genuinely still imported; flagged here for the Owner's own call on whether the unused list is worth removing separately. Also: this file's 20 numbers are unrelated to `8801880446111`/`8801958122300` — never actually in Step 6's scope.

**Final verification:** `grep -rn "8801880446111\|8801958122300" modules/ app/ shared/ --include="*.py"` returns only comment/docstring lines (top-of-file/function docstrings, inline `#` comments, one usage-example docstring in `shared/env.py`) — zero remaining functional literals.

### Files Authorized for Change (Session 19, Step 6)

| ID | File | Change |
|---|---|---|
| S19-06 | `shared/bridge_orchestrator.py` | `ADMIN_SELF_NUMBER`/`ADMIN_SELF_JID` derived from settings instead of hardcoded. |
| S19-06 | `app/main.py` | `_AGENT_FORWARD_PHONES` derived from `settings.admin_number_list`; FB-comment reply text uses `settings.admin_bridge1_number`. |
| S19-06 | `modules/fazle_payroll_engine/diagnostics.py` | Stale-bridge alert's `bridge_number`/`recipient_jid` derived from settings. |
| S19-06 | `modules/escort_roster/routes.py` | `/backfill-sqlite`'s `bridge_number` derived from settings. |
| S19-06 | `modules/fazle_payroll_engine/historical_sync.py` | `_BRIDGE_CONFIGS`' `source_number` fields derived from settings. |
| S19-06 | `modules/fazle_payroll_engine/replay.py` | `_do_replay()`'s `source_number` derived from settings. |
| S19-06 | `modules/contact_sync/__init__.py` | `BRIDGE_SOURCES`' `number` fields derived from settings. |

**Verification status:** all 7 touched files pass `py_compile`; each settings-derived value confirmed via a standalone import test to resolve to the exact same literal it replaced (no behavior change, only the source of truth changed). Final grep confirms zero remaining functional hardcoded literals.

**Confirmed LIVE (2026-07-17, 04:04:09 CEST restart, Main PID 73731):** Owner restarted `fazle-core.service`; `logs/fazle-core-error.log` shows a clean `Started server process [73731]` → `Application startup complete` → `Uvicorn running` sequence with no exceptions. This is meaningful, not just an absence-of-errors check: three of the seven files (`modules/contact_sync/__init__.py`, `modules/fazle_payroll_engine/historical_sync.py`, `shared/bridge_orchestrator.py`) build their settings-derived values at **module import time** — any error in those specific fixes would have thrown during Python's import phase and crashed startup entirely, not merely logged a warning. A clean startup is direct, positive evidence those three fixes are correct, not absence-of-proof. The pre-existing `TypeError: expected str, got int` / `asyncpg.exceptions.DataError` tracebacks visible in the log are all timestamped 2026-07-16 (a full day before this restart) — an unrelated, pre-existing bug in a different query path, not a regression from this step. Only new log entry post-restart is a benign HuggingFace read-timeout warning (RAG's embedding-model version check, unrelated to this step).

### Session 19, Step 6B — Rate limiter mark_sent() int→str type mismatch fixed

Found during Step 6's own log review (not part of the original audit list): `modules/social_auto_reply/rate_limiter.py::mark_sent()`'s query casts its second parameter with `$2::text`, which tells asyncpg to expect a Python `str` — but the code passed `delay` (a raw `int` from `random.randint(...)`) directly. Confirmed as the cause of 5+ recurring `TypeError: expected str, got int` / `asyncpg.exceptions.DataError` failures across the prior ~22 hours of logs, all caught by `daemon_worker.py`'s outer `except Exception` (logged, 30s fallback sleep, loop continues) — meaning `social_rate_limit_state.next_allowed_at`/`sent_count_window` were never actually persisted; the DB-backed cross-restart rate limit had been silently non-functional.

**Fix:** `str(delay)` for the query parameter only; the function's own `return delay` stays a raw `int` (the caller does `asyncio.sleep(delay)`, which requires a number, not a string). Re-read the full function first to confirm no other `$N` placeholder in the query needed the same fix — only `$2` is cast to `::text`; the `ON CONFLICT DO UPDATE` block introduces no additional placeholders (only references `EXCLUDED.*` and the table's own columns).

**Verified:** `py_compile` clean. Type-fix verified via an isolated check (`str(delay)` confirmed to produce a `str`) rather than actually invoking `mark_sent()`, to avoid writing to `social_rate_limit_state` from a throwaway test run.

### Files Authorized for Change (Session 19, Step 6B)

| ID | File | Change |
|---|---|---|
| S19-6B | `modules/social_auto_reply/rate_limiter.py` | `mark_sent()`: `delay` → `str(delay)` as the `$2::text` query parameter. One-line fix. |

### Session 19 — Known open item: Employee Edit 500 (log and defer, Owner decision)

PUT /api/admin/employees/143 returned 500 at 2026-07-16 20:48:46 — single occurrence, no traceback found in available log history. Pydantic type validation rules out a simple str/int mismatch. Cannot diagnose from logs alone. Next step when reproducing: check logs immediately after triggering the same edit action live to capture the traceback.

**Owner decision (2026-07-17): log and defer.** Not investigated further this session. Filed as a standalone LOW-priority item in `docs/NEXT_IMPLEMENTATION_QUEUE.md`'s "Immediate Next" section — not assigned to any Tier or Phase, since it's a single occurrence with no confirmed pattern, not a scoped project of work.

### Session 19 — Bridge1 cursor: confirmed correct, no action

Bridge1 cursor advance mechanism confirmed correct 2026-07-17 — `fetched=0` over 10h overnight period is expected behavior when no inbound messages arrive, not a bug. Advance-only-if-newer guard (`if new_cursor > cursor: cursor = new_cursor; await _set_cursor(...)`, `modules/bridge_poller/__init__.py:1140-1143`) working as designed. Owner will do a manual sanity check by texting the HR number directly; no code change made or needed.

### Session 19 — Incident: Step 7 caused a brief production outage (self-inflicted, same-session hotfix)

**What happened:** commit `c203bba` (Step 7) removed `modules/escort_roster/routes.py::_normalize_client_phone()`, believing all its call sites were confined to that file. `modules/client_billing/profiles.py:27` imported it directly (`from modules.escort_roster.routes import _escort_client_phone_list, _normalize_client_phone`) — a cross-module import of a "private" underscore-prefixed function that was never checked for before deletion. The pre-removal search only looked for call sites *inside* `escort_roster/routes.py`, not a whole-repo search for external importers of the function being deleted.

**Impact:** Owner's restart attempt (`sudo systemctl restart fazle-core`) failed — `ImportError: cannot import name '_normalize_client_phone' from 'modules.escort_roster.routes'`, `Main PID ... (code=exited, status=1/FAILURE)`, systemd auto-restart crash-loop. Service was down from the failed restart until the hotfix (commit `6e595f2`) was deployed and the Owner restarted again — real, if brief, production downtime, self-inflicted.

**Fix:** `6e595f2` — `client_billing/profiles.py::_validate_contact_mobile()` now calls `to_880(normalize_bd_phone(raw))` directly instead of the removed function, with an explicit `None` check preserving the original two-stage 400 behavior (invalid format vs. valid-but-unauthorized).

**Process gap, for future sessions:** before deleting or renaming any function — even one that looks file-local or is underscore-prefixed — search the *whole repository* for importers (`grep -rn "from <module> import.*<name>"` or `grep -rn "\b<name>\b"` across `modules/`, `app/`, `shared/`, `tests/`), not just call sites within the same file. Underscore-prefixed names in this codebase are a naming convention, not an enforced privacy boundary — cross-module imports of "private" functions exist (confirmed here, and `_escort_client_phone_list` itself is cross-imported by two files too, which happened to survive only because that function wasn't the one removed). `py_compile` does not catch this class of error — it only validates syntax within a single file, not cross-module import resolution. The `import app.main` full-chain test (matching uvicorn's actual load path) used to verify the hotfix should be standard practice before any restart request going forward, not just for large multi-file changes.

### Session 19 — Session closed (2026-07-17)

Session 19 complete — Steps 0-8 implemented, tested, and committed. Service healthy. Open items carried to next session: dual-write hang fix, history_sync blocking call, employee edit 500 investigation, Step 9 credential rotation.

All Session 19 commits (`5ad65f3` through `e458158`, 19 commits) plus 65 earlier unpushed commits from prior sessions — 84 total — pushed to `origin/main` this session (`46acd44..e458158`, clean fast-forward, confirmed `origin/main` == local `HEAD`).

---

### Management Authorization (Session 20, Step 1)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-01 | `history_sync.py`'s blocking SQLite read dispatched off the event loop | `docs/NEXT_IMPLEMENTATION_QUEUE.md` "Immediate Next" #9 / "Technical Debt": `modules/escort_roster/history_sync.py::sync_from_bridge_sqlite()` ran a raw `sqlite3.connect()`/`execute()`/`close()` sequence directly inside its `async def` body with no executor/`to_thread()` dispatch — found (not fixed) in Session 19 Step 8 as the same bug class already fixed in `modules/bridge_poller/__init__.py` (commit `a5d3871`). Backs the admin-triggered `/backfill-sqlite` route (`escort_roster/routes.py:536`), lower frequency than the continuous poller loop but still blocks the whole app's event loop for every other concurrent request while it runs. Fixed using the identical pattern: extracted the connect/execute/close sequence into a new sync helper, `_fetch_backfill_messages(db_path, jid_patterns)`, dispatched via `asyncio.to_thread()`. Same query, same params, same 5.0s timeout, same outer `try/except` and error handling (`stats["errors"]` append + early return on failure) — only the execution now happens off the event loop. No business logic changed, matching the session's explicit scope. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 1)

| ID | File | Change |
|---|---|---|
| S20-01 | `modules/escort_roster/history_sync.py` | Added `import asyncio` at module top. Extracted the `sqlite3.connect()/execute()/close()` block from `sync_from_bridge_sqlite()` into a new module-level sync helper `_fetch_backfill_messages(db_path, jid_patterns)`. `sync_from_bridge_sqlite()` now calls `await asyncio.to_thread(_fetch_backfill_messages, db_path, jid_patterns)` instead of running the SQLite calls inline. |

**Verification:** `py_compile` clean. Targeted tests green — `tests/unit/test_stage2_stability_fixes.py` (4/4, includes `test_history_sync_duplicate_message_counts_reused_group`, the one existing test that exercises `history_sync.py`), plus a broader sweep of `tests/unit/test_bridge_poller_legacy_gate.py`, `tests/unit/test_escort_roster_routes.py`, and related escort/bridge unit tests (31 passed, 6 xfailed as baseline, 1 pre-existing unrelated failure in `test_escort_source_of_truth_refactor.py` confirmed present identically on unmodified `main` via `git stash`, not caused by this change). Committed as `e3c1f58`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 pre-restart checks PASS, pre/post health snapshots both `ok`), `curl localhost:8200/health` returned `"status":"ok"` with all probes healthy post-restart, and `logs/fazle-core.log` shows clean `Application startup complete` with no import/exception errors traceable to this change (the one `ImportError` present in the error log is a stale entry from a prior, unrelated Session 19 incident, not from this restart).

---

### Management Authorization (Session 20, Step 2 — Group B1)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-02 | `payroll.html::clientBillingFetch()` migrated to `shared-auth-fetch.js` | Owner-approved audit (`docs/NEXT_IMPLEMENTATION_QUEUE.md` "URL / Route-Based Functions Needing Attention" #2, frontend shared-fetch migration, 8-page scope) identified `payroll.html` as the largest/highest-risk of the 8 pages still on hand-rolled fetch code. Owner scoped this step to `clientBillingFetch()` only (Group B1 of a larger, explicitly staged plan — `attendanceFetch()` is Group B2, not yet done; `api()`/`adminApi()` explicitly deferred, see below). Exact diff shown to Owner and approved before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group B1)

| ID | File | Change |
|---|---|---|
| S20-02 | `app/static/payroll.html` | Added `<script src="/static/shared-auth-fetch.js"></script>` before the page's existing inline `<script>` (same placement pattern as `escort-roster.html`). Reimplemented `clientBillingFetch(path, opts = {})` to call `FazleAuthFetch.fetch('/admin' + path, API_KEY, opts)` and `FazleAuthFetch.readError(r)` on non-ok, instead of its own inline header-building/error-parsing block. Function name, signature, route prefix (`/admin`), and all 9 call sites unchanged. |

**Behavior change (Owner pre-approved as the only allowed change):** error-message text only. Old code only ever extracted a string-shaped `detail` field, falling back to `r.statusText` for anything else (including FastAPI's array-shaped 422 validation-error `detail`, which it silently dropped). `FazleAuthFetch.readError()` additionally handles the array case and falls back to raw response text (not just `statusText`) when the body isn't JSON. Happy path (header set built, success path returning `r.json()`) is byte-for-byte identical — confirmed by diff review with the Owner before implementation.

**Verification:** `git diff` reviewed — confirmed only the two intended hunks (script tag + `clientBillingFetch` body), no unrelated changes. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed; `node -c shared-auth-fetch.js` passed. No existing pytest suite covers this static frontend file (confirmed via repo-wide grep for `payroll.html`/`clientBillingFetch`/`shared-auth-fetch` in `tests/`, zero hits) — `scripts/restart_gate.sh`'s "Targeted tests" check (807 collected) still ran clean as part of the standard gate. All 9 `clientBillingFetch()` call sites already pass pre-stringified `body: JSON.stringify(...)` via `opts`, which `FazleAuthFetch.fetch()` passes through unmodified — no call-site changes needed. Committed as `d76a7ff`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, and both `/static/payroll.html` and `/static/shared-auth-fetch.js` served `200`.

**Not done, deferred per Owner scope:** `attendanceFetch()` (Group B2), `api()`/`adminApi()` (explicitly deferred — see `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 2 addition), the 10 ad-hoc `fetch()` calls (Group C), `resolveUserAccess()`/`doLogin()` (permanently out of scope per Owner decision).

---

### Management Authorization (Session 20, Step 2 — Group B2)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-03 | `payroll.html::attendanceFetch()` migrated to `shared-auth-fetch.js` | Same pattern as Group B1 (S20-02) — `attendanceFetch()` was structurally identical to the pre-migration `clientBillingFetch()` (same inline header-building/error-parsing block, same `/admin` route prefix). Owner approved the exact diff before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group B2)

| ID | File | Change |
|---|---|---|
| S20-03 | `app/static/payroll.html` | Reimplemented `attendanceFetch(path, opts = {})` to call `FazleAuthFetch.fetch('/admin' + path, API_KEY, opts)` and `FazleAuthFetch.readError(r)` on non-ok, instead of its own inline header-building/error-parsing block. Function name, signature, route prefix (`/admin`), and all 10 call sites unchanged. No new `<script>` tag needed — already added in Group B1 (S20-02). |

**Behavior change (same class as B1, Owner pre-approved):** error-message text only — `readError()` additionally handles FastAPI's array-shaped 422 `detail` and falls back to raw response text instead of just `statusText`. Happy path unchanged.

**Verification:** `git diff` reviewed — confirmed the single intended hunk, no unrelated changes. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed. All 10 `attendanceFetch()` call sites checked — pass either no `opts` or pre-stringified `body: JSON.stringify(...)`, both compatible with `FazleAuthFetch.fetch()`'s pass-through; no call-site changes needed. Committed as `2cab0f2`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, `/static/payroll.html` served `200`.

**Group B (both wrappers) now complete.** Remaining for `payroll.html`: Group C (10 ad-hoc `fetch()` calls, not yet started), `api()`/`adminApi()` deferred per Tier 2 note above.

---

### Management Authorization (Session 20, Step 2 — Group C1)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-04 | 3 employee ad-hoc `fetch()` calls migrated to `FazleAuthFetch` | First of 4 planned sub-groups (Group C, 10 ad-hoc calls total, split by feature area: C1 Employees, C2 Transactions, C3 Escort, C4 Income). Owner reviewed and approved the exact diff for all 3 calls before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group C1)

| ID | File | Change |
|---|---|---|
| S20-04 | `app/static/payroll.html` | `submitNewEmployeeFromTab()` (create employee, POST `/api/admin/employees`): raw `fetch()` + manual `r.json()`-then-check replaced with `FazleAuthFetch.postJson(...)`. `deactivateEmployee()` (2 calls): the by-phone lookup (GET) now uses `FazleAuthFetch.fetch()` as a raw-Response primitive, preserving its existing manual `if (!wr.ok)` check and curated "No WBOM record found for this employee" toast unchanged; the deactivate call (PATCH) replaced with `FazleAuthFetch.patchJson(...)`. |

**Behavior changes (all pre-reviewed and approved by Owner, no happy-path change on any of the 3):**
- Create (POST) and deactivate (PATCH): error-message text improves (same class as Group B), **and** fixes a real pre-existing bug — both old call sites did `const d = await r.json()` *before* checking `r.ok`, so a non-JSON error response body would have thrown an unreadable parse error instead of a clean message. `postJson`/`patchJson` read text first and only attempt JSON parsing for the `detail` field, falling back gracefully.
- Lookup-by-phone (GET): functionally a no-op — the only observable difference is an added `Content-Type: application/json` header on this GET request (previously sent only `X-Internal-Key`), consistent with the same unconditional-Content-Type pattern already used and approved in the Group B wrappers. The curated error toast and early-return control flow are byte-for-byte unchanged.

**Verification:** `git diff` reviewed — confirmed exactly the 2 intended hunks (create + deactivate/lookup), no unrelated changes; the other 7 ad-hoc calls (C2/C3/C4) and the 2 bootstrap calls (`resolveUserAccess`/`doLogin`) and 2 deferred wrappers (`api`/`adminApi`) untouched — confirmed via `grep -n "fetch("` before/after showing only the 3 expected lines changed. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed. Committed as `6feffd1`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, `/static/payroll.html` served `200`.

**Remaining for Group C:** C2 (Transactions, 4 calls), C3 (Escort, 2 calls), C4 (Income, 1 call) — not yet started, plan to be presented and approved per sub-group before each is touched.

---

### Management Authorization (Session 20, Step 2 — Group C2)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-05 | 4 transaction ad-hoc `fetch()` calls migrated to `FazleAuthFetch` | Second of 4 Group C sub-groups. Owner reviewed and approved the exact diff for all 4 calls before implementation, including an explicit instruction to preserve `_fetchTxnSuggest()`'s pre-existing no-`res.ok`-check / silent-failure behavior unchanged rather than fix it in this session. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group C2)

| ID | File | Change |
|---|---|---|
| S20-05 | `app/static/payroll.html` | `_fetchTxnSuggest()` (typeahead, GET `/api/fpe/employees/suggest`): raw `fetch()` swapped for `FazleAuthFetch.fetch()` only — the missing `res.ok` check and the silent-catch behavior were deliberately left exactly as-is, per Owner instruction (documented, not fixed). `saveNewPayment()` (POST `/api/admin/transactions`) and `saveEditTxn()` (PUT `/api/admin/transactions/{id}`): raw `fetch()` + manual `r.json()`-then-check replaced with `FazleAuthFetch.postJson(...)`/`putJson(...)`. `confirmDeleteTxn()` (DELETE `/api/admin/transactions/{id}`): raw `fetch()` replaced with `FazleAuthFetch.fetch(..., {method:'DELETE'})` + `FazleAuthFetch.readError(res)` on non-ok — `shared-auth-fetch.js` was **not** modified to add a `deleteJson` helper, per Owner's standing rule against touching that file without a fatal-issue report first. |

**Behavior changes (all pre-reviewed and approved by Owner, no happy-path change on any of the 4):**
- Typeahead (GET): zero behavior change — same incidental `Content-Type` header addition as prior GET conversions in this session; the pre-existing no-`ok`-check / silent-failure quirk is explicitly preserved, not fixed, per Owner instruction.
- Add payment (POST) and edit transaction (PUT): same class of change as Group C1's create/deactivate calls — error-message text improves (`readError()` handles array-shaped 422 `detail` and non-JSON bodies) and fixes the parse-before-check bug (old code called `res.json()` before checking `res.ok`).
- Delete transaction (DELETE): same error-message-improvement class, implemented manually (fetch + explicit `if (!res.ok) throw new Error(await readError(res))` + `res.json()`) since no `deleteJson` helper exists — this mirrors the exact internal shape of the already-approved `clientBillingFetch`/`attendanceFetch` wrappers, so no new pattern was introduced.

**Verification:** `git diff` reviewed — confirmed exactly the 4 intended hunks, no unrelated changes; `grep -n "fetch("` before/after confirms only these 4 lines changed, with the 2 remaining C3/C4 ad-hoc calls, the 2 bootstrap calls, and the 2 deferred wrappers untouched. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed. Committed as `cc6f96d`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, `/static/payroll.html` served `200`.

**Remaining for Group C:** C3 (Escort, 2 calls), C4 (Income, 1 call) — not yet started.

---

### Management Authorization (Session 20, Step 2 — Group C3)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-06 | 2 escort change-request ad-hoc `fetch()` calls migrated to `FazleAuthFetch` | Third of 4 Group C sub-groups. Owner reviewed and approved the exact diff for both calls before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group C3)

| ID | File | Change |
|---|---|---|
| S20-06 | `app/static/payroll.html` | `rvApproveOneClick()` (POST `/api/escort-roster/change-requests/{id}/approve`) and `rvDismissOneClick()` (POST `/api/escort-roster/change-requests/{id}/reject?note=...`): raw `fetch().then(async r => {...})` chains replaced with `FazleAuthFetch.postJson(path, API_KEY)` (no body). The `?note=` query string on the reject call is preserved verbatim. |

**Behavior change (Owner pre-approved, cosmetic only):** both calls discard the parsed response body and always show a hardcoded toast on success, unchanged before/after. Only the error-path message wording changes — old code used raw `r.text()` (never parsed the FastAPI `{detail: ...}` JSON shape), falling back to `'HTTP ' + r.status` on an empty body; `readError()` parses `detail` when present and falls back to `` `${status} ${statusText}` `` (e.g. `"404 Not Found"`) instead of `"HTTP 404"` on a genuinely empty body — a cosmetic fallback-format difference the Owner explicitly accepted with no action needed.

**Verification:** `git diff` reviewed — confirmed exactly the 2 intended hunks, both hardcoded `showToast(...)` calls immediately following each migrated line left untouched (happy path preserved). `grep -n "fetch("` before/after confirms only these 2 lines changed; the C4 income-delete call, the 2 bootstrap calls, and the 2 deferred wrappers untouched. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed. Committed as `b738006`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, `/static/payroll.html` served `200`.

**Remaining for Group C:** C4 (Income, 1 call) — not yet started. Once complete, Group C (all 10 ad-hoc calls) will be fully migrated; only `api()`/`adminApi()` (deferred, Tier 2) and `resolveUserAccess()`/`doLogin()` (permanently out of scope) will remain on `payroll.html`.

---

### Management Authorization (Session 20, Step 2 — Group C4)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-07 | 1 income ad-hoc `fetch()` call migrated to `FazleAuthFetch` — Group C now fully complete | Fourth and final Group C sub-group. Owner reviewed and approved the exact diff before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 2 — Group C4)

| ID | File | Change |
|---|---|---|
| S20-07 | `app/static/payroll.html` | `deleteIncomeTxn()` (DELETE `/api/admin/income/{id}?deleted_by=admin_manual`): raw `fetch()` replaced with `FazleAuthFetch.fetch(..., {method:'DELETE'})` + `FazleAuthFetch.readError(res)` on non-ok — same shape as the Group C2 transaction-delete conversion. `?deleted_by=admin_manual` query string and the `const data = await res.json()` success-path line preserved exactly, per Owner instruction, even though `data` is never read (the toast is hardcoded). |

**Behavior change (same class as C2's delete-transaction call, Owner pre-approved):** error-message text improves (`readError()` handles array-shaped `detail` and non-JSON bodies vs. the old string-only `data.detail || res.statusText`) and fixes the parse-before-check bug (old code called `res.json()` before checking `res.ok`). Happy path unchanged.

**Verification:** `git diff` reviewed — confirmed the single intended hunk, no unrelated changes. `grep -n "fetch("` before/after confirms the only remaining raw `fetch()` calls in the file are the 2 permanently-out-of-scope bootstrap calls (`resolveUserAccess`, `doLogin`) and the 2 deferred wrappers (`api()`, `adminApi()`) — all 10 Group C ad-hoc calls now use `FazleAuthFetch`. `node -e "new Function(...)"` syntax check on the extracted inline `<script>` block passed. Committed as `8512acd`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, `/static/payroll.html` served `200`.

### Session 20, Step 2 — payroll.html shared-fetch migration complete (Groups B1, B2, C1, C2, C3, C4)

All 6 Owner-approved sub-groups landed, each its own commit, each restart-verified live:

| Sub-group | What | Commit |
|---|---|---|
| B1 | `clientBillingFetch()` wrapper | `d76a7ff` |
| B2 | `attendanceFetch()` wrapper | `2cab0f2` |
| C1 | Employee ad-hoc calls (create, lookup-by-phone, deactivate) | `6feffd1` |
| C2 | Transaction ad-hoc calls (typeahead, add, edit, delete) | `cc6f96d` |
| C3 | Escort ad-hoc calls (approve, reject) | `b738006` |
| C4 | Income ad-hoc call (delete) | `8512acd` |

12 of `payroll.html`'s original 16 `fetch()` calls now go through `shared-auth-fetch.js` (2 wrapper functions + 10 ad-hoc calls). The remaining 4 are explicitly out of scope: `api()`/`adminApi()` deferred to Tier 2 (dual-prefix routing and auto-serialize behavior need a design decision, not a mechanical swap — see `docs/NEXT_IMPLEMENTATION_QUEUE.md`), and `resolveUserAccess()`/`doLogin()` permanently excluded (bootstrap/session logic, structurally different from the general authenticated-fetch pattern this migration targets).

---

### Session 20, Step 3 — Finance-First Link/Sync Integrity Audit + Phase 0/1 (2026-07-17)

New scope opened this session: a read-only audit of unlinked/out-of-sync row creation between `wbom_*` and `fpe_*` employee/finance tables, requested separately from the `payroll.html` migration work above. Full audit report (ownership matrix, writer inventory, mismatch report, top-3 root-cause paths, minimal fix plan, direct answer on FPE auto-create from finance flows) delivered in-conversation, not yet filed as its own KB doc — cross-reference this entry if that changes.

**Owner scope lock for this sub-effort (explicit, in writing):** NOT a rename/merge project. Excluded unless separately approved: `wbom_*`→`fpe_*` global renames, `wbom_employees`↔`fpe_employees` merge, `employee_id_phone` uniqueness changes, escort dual-write concurrency work, `accounting.py` semantics changes, `admin_transactions` bypass cleanup. Session goal locked to exactly 3 items: (1) stop unknown/untrusted-sender auto-create, (2) preserve the trusted admin→accountant finance path, (3) add a read-only unresolved/unlinked FPE employee report.

**A separate, adjacent proposal from the Owner** (rename all `wbom_*` tables to `fpe_*`, lock `employee_id_phone` as the unique FK anchor, dedup same-phone/mismatched-name rows by transaction-count) was reviewed for feasibility only, not implemented. Key findings, backed by live read-only queries against `ai-postgres`: `wbom_employees` (216 rows) is FK-anchored by 8 dependent tables, `fpe_employees` (493 rows) by 9 — a rename-only op is safe for non-colliding tables but the `wbom_employees`/`fpe_employees` case is a genuine schema merge (17 total dependent-table FK remap), not a rename. 253 of 493 `fpe_employees` rows (51%) already have `wbom_employee_id IS NULL`. `primary_phone` already carries a real partial `UNIQUE` index; `employee_id_phone` does not and has 39 phone values shared across 2+ employee rows with mismatched names — live evidence for the dedup idea, but the `employee_id_phone`-as-unique-key proposal directly contradicts `CANONICAL_BUSINESS_RULES.md`'s own already-Owner-ratified "Naming Rule" (phone-shaped columns are deliberately not unified) — flagged as an unresolved conflict per that document's own Conflict Resolution Rule, not resolved unilaterally. **None of this rename/merge/uniqueness work was implemented or approved this session** — parked pending an explicit future Owner decision.

### Management Authorization (Session 20, Step 3 — Phase 0)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-08 | Requirements lock for the 3-item finance-integrity fix, read-only | Traced exact live call paths for 4 flows (trusted admin→accountant finance, operator/admin manual-entry frontend, escort allowlist, unknown-sender auto-create) against current code, not assumption. Findings: trusted finance path is **PARTIAL** (accountant-only gate is correct, but the required "employee mobile/id first, then payout number" two-step search isn't actually implemented — `parse_admin_cash_shorthand()` collapses both into one `lookup_mobile` before `match_employee()` ever runs, so an explicit ID-mobile miss never falls back to a payout-number retry); operator manual-entry is a pre-existing **VIOLATION** of the now-stated Owner Rule B (`COMMAND_ROLE["edit"] = "operator"` means operator and admin get byte-identical treatment on `POST /api/admin/transactions`, zero review-queue branch exists) — explicitly ruled **out of scope this session**, documented only; escort allowlist path **MATCHES** (double-gated: source-trust + `.env ESCORT_CLIENT_PHONES`, at the actual write function not just role classification — one footnote: `_is_allowed_escort_client()` fails open if that env var is ever emptied, not exercised live today); unknown-sender path **VIOLATES** — confirmed as the Phase 1 target. | 2026-07-17 |

**Files Authorized for Change (Phase 0):** none — read-only requirements-gathering only.

### Management Authorization (Session 20, Step 3 — Phase 1)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-09 | Disable unknown-sender auto-create in `_seed_unknown_employee_identity()` | Function previously created a real `wbom_employees` row (and indirectly seeded a linked `fpe_employees` row) whenever `identity_brain.detect_identity()` returned `role="unknown"` **and** the inbound message shape matched `_OPERATIONAL_AUTO_CREATE_INTENTS` (salary_query/payment_due/attendance/leave/escort_duty/slip_submission) or one of several intent-regex checks (advance request, release intent, escort order, etc.) — reachable from literally any phone number, since by construction every sender reaching this function had already failed every trust check the system has (not admin, not accountant, not escort-client, not an existing employee/contact match). This is exactly the "random/unapproved number" auto-create Owner Rule D prohibits. **Disabled**, not deleted: the entire function body replaced with a documented `return identity, False`. `_OPERATIONAL_AUTO_CREATE_INTENTS` is now unused outside this function — flagged in the docstring, intentionally kept (not removed) per Owner's standing "document dead code, don't clean it up inline" rule. | 2026-07-17 |

**Files Authorized for Change (Phase 1):**

| ID | File | Change |
|---|---|---|
| S20-09 | `modules/message_router/__init__.py` | `_seed_unknown_employee_identity()`: entire function body (including a trailing tail block past the point shown in the Phase 1 proposal — discovered and corrected during implementation, see Verification below) replaced with a docstring explaining the disable rationale + `return identity, False`. Function signature and its one call site (`process_message()`) unchanged. `_OPERATIONAL_AUTO_CREATE_INTENTS` (module-level constant) left in place, now unused. |
| S20-09 | `tests/unit/test_message_router_identity_seed.py` | Rewrote the one test covering this function. Old test asserted the disabled behavior (`seeded is True`, DB rows created) — now asserts the guard is an unconditional no-op: same input that used to trigger creation (unknown role + advance-request-shaped text) now returns the identity dict unchanged with `seeded is False` and zero calls to `fetch_one`/`fetch_val`/`execute`. |

**Trusted paths confirmed unaffected, by construction, not just by testing:** admin (`identity_brain._check_admin_settings`), accountant (`fazle_contact_roles` seed rule), and escort-client (`fazle_contact_roles`/`wbom_contacts` seed) are all resolved inside `detect_identity()` **before** `_seed_unknown_employee_identity()` is ever called — none of those three paths, nor `modules/payment_ingest` (admin→accountant finance) nor `modules/escort::handle_escort_client_message()` (escort allowlist), call this function at all. No code in those modules was touched.

**Verification:** implementation caught and fixed its own gap mid-flight — the Phase 1 proposal shown to the Owner for approval was based on a truncated read of the function (missed a ~19-line tail block past what was displayed); applying the approved diff left that tail as orphaned dead code referencing now-undefined variables. Caught immediately via `git diff` review before committing, corrected to remove the full original function body as originally intended (matching the Owner's own explicit checklist item: "the entire function body" replaced). `py_compile` clean. Targeted test suite: 1 test directly covering this function (updated, passes); broader sweep (`-k "message_router or identity or escort or accountant"`, 161 passed) found 2 pre-existing failures (`test_escort_module.py::test_creates_escort_program_and_draft`, `test_escort_source_of_truth_refactor.py::test_draft_updated_by_bridge_confirmation`) and 39 pre-existing schema-setup errors (`password authentication failed for user "postgres"`, the same test-DB-connectivity gap seen throughout this session) — all confirmed present identically on unmodified `main` via `git stash` isolation, none caused by this change. Committed as `47018f7`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`.

**Not done, deliberately out of scope this session:** Phase 2 (trusted finance path — the search-order PARTIAL finding above, not yet fixed), Phase 3 (read-only unresolved/unlinked FPE report), the rename/merge proposal, escort dual-write concurrency, `accounting.py`, `admin_transactions` bypass cleanup, and the operator-manual-entry review-queue gap (documented, explicitly deferred).

### Management Authorization (Session 20, Step 3 — Phase 2)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-10 | Add `payout_mobile` fallback search to `_match_or_create_instruction_employee()` | Re-verified Phase 0's finding with exact current code, live DB queries (`fazle_contact_roles` seed rows, `.env` comparison), and a full trace of `parse_admin_cash_shorthand()` → `_match_or_create_instruction_employee()` → `match_employee()`. **Confirmed real gap:** `parse_admin_cash_shorthand()` collapses `employee_id_mobile`/`payout_mobile` into one `lookup_mobile` value (line 464) before `match_employee()` ever runs, so an explicit ID-mobile that misses never falls back to a second search against the payout number — even when the payout number belongs to an already-registered employee, causing a duplicate `wbom_employees`/`fpe_employees` row instead of a match. Owner approved the exact 3-line fix before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 3 — Phase 2)

| ID | File | Change |
|---|---|---|
| S20-10 | `modules/payment_ingest/__init__.py` | `_match_or_create_instruction_employee()`: added `if not eid and payout_mobile and payout_mobile != lookup_mobile: eid, _ratio, _mtype = await match_employee(payout_mobile, name)` immediately after the first `match_employee()` call. Two lines. `match_employee()` itself, `modules/payment_ingest/fpe_bridge.py`, and `modules/fazle_payroll_engine/accounting.py` were read for verification but not modified. |

**Explicitly preserved (verified, not just asserted):** auto-create still allowed if both searches miss (fall-through logic below `if eid:` byte-identical to before); no reply to the accountant (no code in the reply path touched); no review-queue routing added (none introduced); transaction completion and ledger write confirmed unaffected — traced `payment_event_from_whatsapp()` (used by this path) hardcodes `transaction_status=TransactionStatus.final`, and `resolve_fpe_employee_for_wbom_employee()`'s return type is `int` (never `None`), so `accounting.py::create_transaction()`'s ledger-upsert condition (`req.employee_id and req.transaction_status.value == "final"`) is always satisfied on this path — both `fpe_cash_transactions` and `fpe_employee_ledger` are written every time, confirmed by code trace, not changed by this fix.

**Two items explicitly deferred, not bundled into this fix:**
- `resolution_status` metadata staleness on `resolve_fpe_employee_for_wbom_employee()`'s step-3 INSERT (defaults to `'unresolved'` even though `wbom_employee_id` is set in the same statement) — noted for Phase 3's report-query design only (a naive `resolution_status='unresolved' OR wbom_employee_id IS NULL` filter would over-flag already-linked rows), not fixed here.
- `POST /admin/contact-roles`'s unvalidated free-text `role` field (an admin could assign `role=accountant` to a second phone without touching `.env`, conflicting with `CANONICAL_BUSINESS_RULES.md` §Role) — added to `docs/NEXT_IMPLEMENTATION_QUEUE.md` Technical Debt as its own item, not fixed here (touches RBAC/admin tooling, outside this session's locked scope).

**Verification:** `git diff` reviewed — confirmed exactly the 2-line addition, no other file touched. `py_compile` clean. No test asserts single-search-only behavior (searched for direct/indirect test coverage of this function; the one test that exercises it end-to-end, `test_accountant_payment_pipeline.py::TestIngestPaymentSms::test_admin_accountant_instruction_finalizes_direct_transaction`, uses distinct ID-mobile/payout-mobile values against an empty test DB, so both the old and new code paths miss identically and fall through to auto-create — its assertions are unaffected). Full targeted sweep (`tests/unit/test_accountant_payment_pipeline.py`, 53 passed / 4 xpassed / 10 errors) confirmed identical before/after via `git stash` isolation — the 10 errors are the same pre-existing `test_db_pool`/Postgres-auth environmental limitation seen throughout this session, not caused by this change. Committed as `38dbedd`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`.

**Not done, deliberately out of scope this session:** Phase 3 (read-only unresolved/unlinked FPE report), the rename/merge proposal, escort dual-write concurrency, `accounting.py` changes, `admin_transactions` bypass cleanup, the operator-manual-entry review-queue gap, the `resolution_status` metadata staleness, and the `contact-roles` role-field validation gap (last two now tracked in `NEXT_IMPLEMENTATION_QUEUE.md`).

### Management Authorization (Session 20, Step 3 — Phase 3)

| ID | Decision | Details | Date |
|---|---|---|---|
| S20-11 | Add `unresolved_fpe_employees` read-only report builder | New builder in `modules/reports/__init__.py`, registered in the existing `_BUILDERS` dict pattern — no new route needed (served automatically via the existing generic `GET /reports/{name}` dispatcher, `app/main.py:2416`, untouched). Investigated `wbom-create-link.html`/`wbom-link-review.html` before writing anything: both currently use a **hardcoded static `CANDIDATES` JavaScript array**, not live data — no report-data hook exists in either file, confirmed via inspection, so neither was touched (per Owner's hard rule). Owner approved the exact function body and registry line before implementation. | 2026-07-17 |

### Files Authorized for Change (Session 20, Step 3 — Phase 3)

| ID | File | Change |
|---|---|---|
| S20-11 | `modules/reports/__init__.py` | Added `_b_unresolved_fpe_employees()` (one `fetch_all()` SELECT, zero writes) and one line in `_BUILDERS`: `"unresolved_fpe_employees": _b_unresolved_fpe_employees`. Purely additive — 105 lines added, 0 removed, no other function touched. |

**What the report covers:** `fpe_employees` rows where `resolution_status = 'unresolved' OR wbom_employee_id IS NULL`, split into 3 buckets that are never conflated:
- `no_wbom_match` — no phone match found in `wbom_employees` at all. Feeds `wbom-create-link.html`'s candidate shape (`fpe_id`, name, phone, salary, joining_date, created_source, txn_count) — needs a human-provided designation, never guessed.
- `wbom_match_unlinked` — a `wbom_employees` phone match exists (checked against `employee_mobile`/`bkash_number`/`nagad_number`, last-10-digit normalized, same fields already treated as authoritative WBOM phone anchors elsewhere in this codebase) but `wbom_employee_id` isn't set. Feeds `wbom-link-review.html`'s candidate shape, including a `name_mismatch` boolean flag.
- `linked_but_mislabeled` — **separated per the Phase 2 finding (S20-10):** `wbom_employee_id IS NOT NULL` (i.e. genuinely, correctly linked) but `resolution_status` still reads `'unresolved'` because `resolve_fpe_employee_for_wbom_employee()`'s creating INSERT never explicitly sets that column, so it takes the schema default. These rows need **no action** — reporting them in the same bucket as the two above would have wrongly told a human reviewer to "fix" something that isn't broken, exactly the mistake the Phase 2 audit flagged as a risk for this report's design.

**Confirmed LIVE, production read** (not a test-DB run): `GET /reports/unresolved_fpe_employees` returned HTTP 200 with:
```json
{"no_wbom_match_count": 172, "wbom_match_unlinked_count": 81, "linked_but_mislabeled_count": 153, "total_reviewed": 406}
```
`172 + 81 = 253` — matches the Phase 2 audit's live finding ("253 of 493 `fpe_employees` rows had `wbom_employee_id IS NULL`") exactly, the first time that number has been reproduced end-to-end through the new report rather than a one-off manual query. Spot-checked one row per bucket for shape correctness (`no_wbom_match` row: name defaulted to the phone number itself, a real WhatsApp-auto-create artifact; `wbom_match_unlinked` row: `fpe_name="Unknown"` vs `wbom_match_name="Rabbi"`, `name_mismatch: true`, correctly caught; `linked_but_mislabeled` row: `wbom_employee_id=695` set, correctly excluded from the two actionable buckets). `?fmt=csv` also verified working.

**Exact routes:**
- `GET /reports/unresolved_fpe_employees` (JSON)
- `GET /reports/unresolved_fpe_employees?fmt=csv`
- `GET /reports/unresolved_fpe_employees?fmt=text`

**Verification:** `git diff` reviewed — confirmed purely additive (105 insertions, 0 deletions), only `modules/reports/__init__.py` touched. `py_compile` clean. Zero write calls (`execute`/`fetch_val`) in the new function — verified via grep, one `fetch_all()` SELECT only. No test file references `modules.reports` at all (repo-wide grep, zero hits) — no test needed updating. Full `tests/unit` sweep: 542 passed, 4 xpassed, 14 xfailed, 4 failed, 242 errors — all 4 failures and all 242 errors confirmed pre-existing via `git stash` isolation (same `password authentication failed for user "postgres"` test-DB-connectivity limitation seen throughout this session; the 2 `test_drafts_router_mounted.py` failures specifically re-verified via stash for this entry, not previously checked this session). Committed as `b0c0057`. **Confirmed LIVE:** `scripts/restart_gate.sh --restart` ran clean (all 7 checks PASS, pre/post health `ok`), `curl localhost:8200/health` returned `"status":"ok"`, and the report itself confirmed working against production data as shown above.

**Discovered, documented, not fixed (per hard rule):** `.gitignore` line 71 has a bare `reports/` pattern that unintentionally also matches `modules/reports/` (likely intended only for a top-level report-output directory). Did not cause any harm this session since `modules/reports/__init__.py` was already tracked from before, but any *new* file added under `modules/reports/` in the future would need `git add -f`. Not fixed — flagging only.

**Not done, deliberately out of scope:** wiring this report's live output into `wbom-create-link.html`/`wbom-link-review.html` (both still run on their original static `CANDIDATES` snapshot) — a separate, not-yet-scoped task, tracked in `NEXT_IMPLEMENTATION_QUEUE.md`.

### Session 20, Step 3 — Finance-First Link/Sync Integrity work complete (Phases 0-3)

| Phase | What | Code commit | Docs commit |
|---|---|---|---|
| 0 | Requirements lock — 4-flow call-path trace, MATCHES/PARTIAL/VIOLATES | — (read-only) | `dfc67f1` |
| 1 | Disable unknown-sender auto-create (`_seed_unknown_employee_identity`) | `47018f7` | `dfc67f1` |
| 2 | `payout_mobile` fallback search (`_match_or_create_instruction_employee`) | `38dbedd` | `80de8e3` |
| 3 | `unresolved_fpe_employees` read-only report builder | `b0c0057` | (this entry) |


### Session 21, Phase 1B — Contact-Role API Whitelist (2026-07-17)

New scope this session: closed the `POST /admin/contact-roles` free-text `role` gap
flagged as Technical Debt in Session 20 Step 3 Phase 2 (S20-10's writeup) and in
`docs/NEXT_IMPLEMENTATION_QUEUE.md`. Preceded by a Phase 0 baseline revalidation that
found the queue file's own "Technical Debt" section was stale on a separate item
(`modules/escort_roster/history_sync.py:779` was already fixed by Session 20 Step 1,
commit `e3c1f58` — the file's "What shipped" section said so, but its Technical Debt
section, written during Session 19 before the fix, was never updated) — corrected as
an isolated documentation fix, no code change needed for that item.

**Call-path verification performed before writing the proposal:** confirmed
`modules/identity_brain/__init__.py::_lookup_seed()` reads `fazle_contact_roles.role`
unfiltered and returns it as `identity_role`, consumed directly by
`modules/message_router/__init__.py:330,349` for WhatsApp trust decisions (e.g.
`role_str == "admin"` bypasses the escort-review-draft safety net) — a real trust
surface, distinct from RBAC command permission. Confirmed the two places money/RBAC
actually move — `message_router.py:253`'s `accountant_phone` check and
`admin_commands`'s `rbac.check_permission()` — read `.env`/`fazle_admins` directly and
never touch this table, so blast radius is message-routing trust, not fund movement or
command execution. Confirmed `shared/auth_deps.require_api_key` accepts the master
`internal_api_key` **or any active admin's personal API key** — i.e. any active
admin-tier account, not just Owner/superadmin, could reach this endpoint.

**Approved policy (Owner, in writing, before implementation):**

| ID | Decision | Details | Date |
|---|---|---|---|
| S21-01 | Restrict `/admin/contact-roles` to a closed contact-role enum | Allowed: `vip_client`, `client_escort_buyer`, `repeat_client`, `vendor`, `supervisor`, `candidate`. Forbidden outright (422), even if the phone exists in `.env`: `admin`, `accountant`, `employee`, `family`, `blocked` — no conditional `.env` cross-check, by explicit Owner choice (simpler, can't drift out of sync with `.env`). Outer whitespace stripped before validation; case variants (`Admin`, `Vip_Client`) rejected, not normalized. `PUT` with `role` omitted preserves the stored value unconditionally (existing `COALESCE` behavior), including on rows already carrying a forbidden legacy value. | 2026-07-17 |

**Existing-role audit (read-only, run before finalizing the enum, via the same
DB-resolution mechanism `fazle-core.service`'s systemd unit itself uses —
`/home/azim/core/.env` + `/home/azim/secure-env-backup/runtime-services.env` —
no credentials logged):**

| role | total | active | inactive |
|---|---:|---:|---:|
| accountant | 4 | 2 | 2 |
| client_escort_buyer | 7 | 3 | 4 |
| employee | 5 | 5 | 0 |
| family | 8 | 8 | 0 |
| repeat_client | 8 | 8 | 0 |
| supervisor | 5 | 5 | 0 |
| vendor | 6 | 6 | 0 |
| vip_client | 2 | 2 | 0 |

No `admin` rows, no null/blank roles. `accountant`/`employee`/`family` rows already
exist and are actively relied upon by `identity_brain` — none created via this HTTP
endpoint (grepped for internal callers of `modules/contact_roles`: none found outside
the module itself and test files), so locking down `POST`/explicit-`role` `PUT` for
these values doesn't break any in-app provisioning path. These rows remain untouched,
readable, and updatable (for non-`role` fields, or via direct DB access) — not migrated
or deactivated, per the approved existing-data policy.

**Files Authorized for Change:**

| ID | File | Change |
|---|---|---|
| S21-01 | `modules/contact_roles/routes.py` | Added `AssignableContactRole(str, Enum)` (6 members) and a shared whitespace-stripping `field_validator("role", mode="before")` on both `ContactRoleBody.role` (now required, enum-typed) and `ContactRoleUpdate.role` (now `Optional[AssignableContactRole]`, unchanged default `None`/preserve-on-omit semantics). Both DB-write call sites and the one log line updated to pass `.value` explicitly (avoids `str,Enum.__str__` surprises in logs/params). No schema change, no RBAC change, no change to `identity_brain`/`message_router`. |
| S21-01 | `tests/unit/test_contact_roles.py` (new) | POST allow-matrix (all 6 enum values), POST reject-matrix (`admin`/`accountant`/`employee`/`family`/`blocked`/`client`/`unknown`/case variants/blank), whitespace-strip-then-validate behavior (`" vendor "` accepted, `" Vendor "` rejected), PUT allow/reject/omit-preserves-existing (including a seeded legacy `family` row updated via a non-`role` field, confirming the mandatory legacy-compatibility case), 404 on unknown phone, auth-gate regression (missing/invalid/valid key unchanged). |

**Verification:** `py_compile` clean on both files. Interactive Pydantic-model check
(no test DB needed) confirmed all POST/PUT allow/reject/whitespace/case cases behave
exactly as specified before the test file was even run. `pytest tests/unit/test_contact_roles.py`
and `tests/unit/test_shared_auth_deps.py` (a pre-existing file exercising the same
endpoint's auth gate) both error at fixture setup with
`password authentication failed for user "postgres"` — **confirmed pre-existing and
environmental, not caused by this change**, via `git stash` isolation: the identical
15/15 errors reproduce on unmodified `main` with zero code changes, matching the same
`TEST_DB_PASSWORD`-not-set limitation this repo's own prior sessions have hit and
documented repeatedly. `tests/unit/test_identity_brain.py` (mocks the DB, doesn't need
`test_db_pool`) ran clean — 17 passed — confirming no regression in the module that
actually consumes `fazle_contact_roles.role`, which this change doesn't touch.
Full DB-backed integration verification (the POST/PUT matrix in the new test file)
could not be executed end-to-end in this session for the same environmental reason and
is flagged as outstanding — see "Remaining Risks" in the session report.

**Not done, deliberately out of scope this session:** conditional `.env` cross-check
for `admin`/`accountant` (Owner chose reject-outright instead), any data migration/
backfill of the existing forbidden-role rows, any change to `identity_brain` or
`message_router`'s consumption of this table, deploy/push/restart (local commit only,
pending Owner review), Phase 1C (`fpe_employees.resolution_status`) or any later phase.

**Commit:** `aee44bc` (`fix(contact-roles): restrict API-assigned identity roles`) —
local only, not pushed, not deployed, no restart performed.

### Session 21, Reconciliation — Escort-Client Identity Convergence Audit (2026-07-17)

Follow-up requested after the Owner clarified the intended business architecture:
`client_escort_buyer` is the one canonical escort-client identity, meant to be
reachable via three convergent sources — `ESCORT_CLIENT_PHONES` (`.env` fallback),
contact-book name containing "escort" (the originally-intended detection method), and
a stored `fazle_contact_roles` row. Audited whether Phase 1B's already-committed code
(`aee44bc`, `bcb5a53`) was compatible with this clarification, and whether the three
sources actually converge in current code.

**Phase 1B diff re-audit (9-point checklist, all confirmed):** `client_escort_buyer`
remains allowed; functional roles remain rejected; POST/PUT validation unchanged from
the prior session; PUT omission still preserves legacy stored roles (including
pre-existing `family`/`employee`/`accountant` rows); no `message_router`, identity
priority, `.env`, or database-row changes; the added documentation never mischaracterizes
`ESCORT_CLIENT_PHONES` vs `client_escort_buyer` as unrelated (it simply never discussed
that relationship — `client_escort_buyer` was already correctly in the allowed set).
**Conclusion: Phase 1B's code is unchanged and correct as committed (Outcome A) — no
correction needed.**

**Full identity-source trace performed against live code** (`modules/identity_brain/__init__.py`,
`modules/message_router/__init__.py`, `modules/escort/__init__.py`, `shared/env.py`,
`app/main.py`, `modules/bridge_poller/__init__.py`):

| Source | Implemented | Function | Canonical result reaches identity_brain | Webhook | Poller |
|---|---|---|---|---|---|
| A — `ESCORT_CLIENT_PHONES` | Partial — order-parsing gate only, not an identity source | `modules/escort/__init__.py::_is_allowed_escort_client()` (used) vs. `shared/env.py::EscortEnv.is_escort_client()` (dead code, unused — a 2nd divergent implementation) | No — runs *after* identity is already decided | Yes (shared function) | Yes (shared function) |
| B — contact-name contains "escort" | **Not implemented at all** | None exists; `identity_brain._lookup_contact()`/`_map_relation()` match `wbom_contacts.relation` (a separate, 99.9%-`'unknown'` field), never `display_name` | No | N/A | N/A |
| C — `fazle_contact_roles.role` | Yes | `identity_brain._lookup_seed()`, Step 2 | Yes — directly, highest non-admin/family priority | Yes | Yes |

**Convergence verdict: NOT converged.** Source C is the only reliable identity-assignment
path. Source A never assigns identity — it only gates whether an *already-classified*
escort-role sender's message reaches the order parser. Source B was never built,
contradicting the Owner's stated original intent; `wbom_contacts.relation` sits at
~0.1% population, making even the implemented relation-string-match path effectively
dead in practice.

**Two additional risks found during the trace, not previously fully documented:**
1. **Conditional risk (was a footnote in S20-08, now fully traced):** `identity_brain`
   Step 6's text-hint fallback assigns `role="repeat_client"` to *any never-before-seen
   sender* whose message contains escort keywords (`_ESCORT_CONTENT_RE`); `repeat_client`
   is a member of `message_router._ESCORT_ROLES`, so this sender reaches
   `handle_escort_client_message()` gated only by `_is_allowed_escort_client()` — which
   returns `True` for *any* phone the instant `ESCORT_CLIENT_PHONES` is emptied ("open
   mode", per its own `.env` comment and `shared/env.py`'s docstring). Not exercised
   live today (3 phones currently configured) but a live landmine if the list is ever
   cleared — the same "random/unapproved number" risk class the Session 20 finance-integrity
   audit already flagged and fixed for `_seed_unknown_employee_identity`, here unfixed
   for escort-order creation.
2. **Webhook/poller LID parity gap (newly found):** `app/main.py::_handle_bridge_event()`
   only strips `@s.whatsapp.net`/`+` from the sender string — it never resolves an
   `@lid`-suffixed sender to a real phone number. `modules/bridge_poller/__init__.py::_load_lid_map()`
   does this resolution (via the bridge's own `whatsmeow_lid_map` SQLite table) before
   calling `process_message()`. Net effect: a LID-only sender is always classified
   `identity_role="unknown"` on the webhook path, regardless of which of the three
   escort-identity sources would otherwise have matched — a real parity gap, same
   family as (but distinct from) the Session 19 `_requires_legacy_workflow()` fix.

**`ESCORT_CLIENT_PHONES` empty-mode semantics (S21 §9) — Interpretation confirmed by
code, not assumption:** matches **Interpretation A/C combined**, not B. An empty list
does not itself make every sender `client_escort_buyer` (Interpretation B is false —
identity assignment doesn't come from this setting at all). It disables the
allowlist *gate* (Interpretation C) on order-parsing eligibility, while identity is
still decided upstream by Source C or the Step 6 fallback. The practical effect is
close to the Owner's concern in spirit even though the mechanism differs from what
was assumed: an ordinary, unverified sender can still end up in `_ESCORT_ROLES` (via
Step 6, not via becoming `client_escort_buyer`) and, in open mode, would not be
blocked from creating a real booking.

**Decision: Outcome C — routing convergence defect found.** Not bundled into Phase
1B (which stays exactly as committed). A full Phase 1B.2 proposal was produced (see
below) and **implementation was not started** — this is a design decision requiring
Owner input (does the fix add real contact-name-substring matching for Source B?
does it change the Step 6/`_ESCORT_ROLES`/empty-list interaction? does it fix LID
handling on the webhook path?), not a mechanical bug fix.

**`FPE_SYNC_CHAT_JIDS`/`FPE_HSYNC_INTERVAL_S` independence confirmed:** both consumed
exclusively by `modules/fazle_payroll_engine` (`historical_sync.py`, `ingestion.py`,
`workers.py`) for payroll-engine historical-sync chat selection and polling interval —
zero coupling to contact roles, RBAC, or escort identity. Untouched, as required.

**DB-backed test verification completed for real this session** (previously blocked
by `TEST_DB_PASSWORD` not being set in-session): spun up the repo's own disposable
`docker-compose.test.yml` `postgres-test` service (a fully separate container/volume
from production, trivial checked-in dev password, port 5433) — the "isolated
temporary test database" path the prior report flagged as untried. Found and worked
around a **pre-existing, separate test-schema drift** (migration
`029_bootstrap_core_tables_schema_record.sql`'s `fazle_contact_roles` snapshot is
missing `sub_role`/`is_active`/`language_pref`/`metadata` and has `id integer` where
production now has `id uuid` — production was altered after migration 029 was
captured, with no migration recording it; filed in `NEXT_IMPLEMENTATION_QUEUE.md`
Technical Debt, not fixed) via a temporary, uncommitted `ALTER TABLE` shim in
`tests/unit/conftest.py`, reverted via `git checkout` immediately after the run
completed — confirmed via `git diff`/`git status` that no trace of it remains.
Also found and fixed for real (commit `393781f`): `name`/`sub_role`/`notes` are
`Optional[str]=None` in `ContactRoleBody` but `NOT NULL` in the live schema with no
`COALESCE`-to-default in the `INSERT`, raising a raw 500 instead of using the
column's own default — filed in `NEXT_IMPLEMENTATION_QUEUE.md` Technical Debt (not
fixed, out of Phase 1B's scope), test payloads updated to supply all three explicitly.

**Full results:** `tests/unit/test_contact_roles.py` (30), `tests/unit/test_shared_auth_deps.py`
(15), `tests/unit/test_identity_brain.py` (17) — **62 passed, 0 failed, 0 errors.**
`tests/unit/test_message_router_identity_seed.py`, `test_escort_module.py`,
`test_escort_lifecycle.py`, `test_escort_roster_routes.py`,
`test_escort_source_of_truth_refactor.py` also run: 2 failures
(`test_escort_module.py::test_creates_escort_program_and_draft`,
`test_escort_source_of_truth_refactor.py::test_draft_updated_by_bridge_confirmation`)
— **both exactly match the 2 pre-existing failures already on record from Session 20's
own verification sweep** (same test names, same root cause already confirmed via
`git stash` isolation that session); this session's changes never touch
`modules/escort`/`modules/escort_lifecycle`/`modules/message_router` at all (read-only
trace only), so no new isolation check was needed.

**Files touched this session:** `tests/unit/test_contact_roles.py` (payload fix,
commit `393781f`), `docs/NEXT_IMPLEMENTATION_QUEUE.md` (3 new Technical Debt items:
contact-roles NOT NULL gap, test-schema drift instance, escort-identity-convergence
gap — doc commit to follow), this governance entry. `tests/unit/conftest.py`'s
temporary shim reverted, leaves no diff. Disposable `postgres-test` container and
volume fully torn down (`docker compose -f docker-compose.test.yml down -v`).

**Deployment recommendation for Phase 1B itself: safe to deploy as committed**
(`aee44bc`, `bcb5a53`, `393781f`) — the role-whitelist change is independently correct
and now genuinely verified end-to-end. **Not safe to consider the wider escort-client
routing workflow "fixed" or "converged"** — that remains open pending the Phase 1B.2
proposal below and an explicit Owner decision.

**Commits this session:** `393781f` (test fix, code), doc commit for
`NEXT_IMPLEMENTATION_QUEUE.md` to follow this entry. Still local only — nothing
pushed, no restart performed.

---

## Phase 1B.2 — Escort-Client Identity and Routing Convergence Proposal (NOT IMPLEMENTED — awaiting Owner decision)

**1. Current production behavior:** see the identity-source trace and convergence
table above. In short: only `fazle_contact_roles.role='client_escort_buyer'` reliably
assigns the canonical identity; `ESCORT_CLIENT_PHONES` only gates order-parsing after
the fact; contact-name-based detection doesn't exist; an empty `ESCORT_CLIENT_PHONES`
combined with `identity_brain`'s Step 6 text-hint fallback can let an unverified,
never-before-seen sender reach the order parser; the webhook path never resolves LIDs.

**2. Relevant KB and Owner decisions:** `CANONICAL_BUSINESS_RULES.md`'s `.env`-authority
Structural Rule (functional roles only — escort-client identity is explicitly NOT a
functional-authority role, so this doesn't conflict with it); this session's Owner
clarification (§1.1–1.4 of the reconciliation request) establishing `client_escort_buyer`
as the single canonical identity with three intended convergent sources; the Session 20
finance-integrity audit's precedent for disabling unverified-sender auto-create
(`_seed_unknown_employee_identity`) — the same risk class applies here for escort orders.

**3. Exact gap:** no single function decides "is this sender a `client_escort_buyer`" by
consulting all three intended sources with a defined priority; the three mechanisms
don't share a code path, a priority order, or evidence/source metadata; Source B doesn't
exist; Source A/Step-6-fallback interaction is an unaudited live risk contingent on
`.env` state; webhook/poller diverge on LID handling.

**4. Smallest proposed implementation (design sketch, not yet approved):** one canonical
resolver — conceptually `resolve_escort_client_identity(sender_phone, sender_jid,
resolved_contact_name, stored_contact_role, settings)` — called from `identity_brain`
in place of (or ahead of) today's separate Step 2/Step 4/Step 6 partial matches,
checking in a defined priority order: (a) stored `fazle_contact_roles` row (highest
trust, unchanged from today), (b) `ESCORT_CLIENT_PHONES` explicit match (elevated from
"parser-stage gate" to "actual identity source"), (c) contact-name "escort" prefix/word
match (new — the Owner's originally-intended source, needs a precise, false-positive-aware
matcher), each result carrying explicit source/evidence metadata for audit logging.
Separately: decide whether Step 6's keyword-only fallback should still be allowed to
land a never-verified sender in `_ESCORT_ROLES` at all when none of A/B/C matched, and
whether `_is_allowed_escort_client()`'s "open mode" should remain fail-open when the
`.env` list is empty. Separately: port `_load_lid_map()`-based resolution (or equivalent)
into the webhook path for parity with the poller.

**5. Risk assessment:** touches the sender-identity resolution used by every WhatsApp
message, not just escort — high blast radius if implemented carelessly. The empty-list
fail-open question and the Step 6 fallback question each have real behavior
implications for live traffic (today's 3-phone list currently masks both). LID-handling
parity touches the webhook ingestion path directly.

**6. Verification plan:** would need new tests for each of the 12 audit questions in
the original request (phone/JID/LID normalization, each source in isolation and in
combination, webhook/poller parity, auto-reply suppression, parser routing, complete/
incomplete/non-order message handling, dedup, audit logging) before any deployment —
none of this exists as test coverage today.

**7. Rollback strategy:** would depend on the chosen design; a single new resolver
function called from one call site is revertable via `git revert` with no schema
change if no new table/column is introduced.

**Owner decisions needed before implementation:** should Source B be built as literal
name-substring matching (with what false-positive guard)? should `ESCORT_CLIENT_PHONES`
become an actual identity source or stay a parsing-stage gate? should Step 6's
unverified-sender fallback be disabled or restricted (mirroring the
`_seed_unknown_employee_identity` precedent)? should empty-list "open mode" be
changed to fail closed? is webhook LID resolution in scope for this same effort or a
separate item?

**Not implemented. No code, schema, or config changed for this proposal.**

### Session 21, Deployment — Phase 1B Pushed, Deployed, Production-Certified (2026-07-17)

Owner authorized push + deploy of the 4 already-reviewed Phase 1B/reconciliation
commits (`aee44bc`, `bcb5a53`, `393781f`, `ba1ac40`). Pushed (fast-forward, no
force), `HEAD` == `origin/main` confirmed at `ba1ac40`. Restarted via
`scripts/restart_gate.sh --restart` (Owner ran it directly): all 7 pre-checks
PASS (working-tree clean, 0 secrets, xfail count unchanged at 13, targeted
tests 100%, migration inventory clean, `.env` flags unchanged, backup 18h
old), pre-restart health `ok`, post-restart health `ok` within 6s. New PID
confirmed (`788177` → `1515204`), clean startup log (no import/Pydantic
errors), no new ERROR/Exception log lines after the restart timestamp
(20:56:47 CEST) — all remaining log noise predates the restart (known
Ollama timeout/rate-limit class, unrelated).

**Live production smoke tests, all 7 passed**, using a disposable phone
(`8801799990001`, confirmed via read-only query to collide with nothing in
`fazle_contact_roles`/`wbom_contacts`/`wbom_employees`/`fpe_employees`, and
not in `ESCORT_CLIENT_PHONES`/`ACCOUNTANT_PHONE`/admin/FPE-authorized lists):
`candidate` create (200), whitespace `" vendor "` → stored `vendor` (200),
`Vendor` → 422 with a clear enum message, role unchanged; `admin` → 422;
`accountant` → 422, role still unchanged; `client_escort_buyer` → 200
(confirms the canonical escort-client identity remains assignable); PUT
omitting `role` while updating `priority` → role preserved; unauthenticated
GET (missing key, invalid key) → both 403. Disposable row deleted via the
API's own `DELETE` endpoint afterward, confirmed empty (`{"items":[],"count":0}`).
No other production row touched.

**Phase 1B: CERTIFIED.** All required conditions met — pushed, restarted,
health OK, allowed/forbidden/case/omission behavior all verified live (not
just in the isolated test DB), `client_escort_buyer` confirmed still
assignable, disposable record cleaned up, zero new runtime errors, zero
unintended data changes.

**Not certified — deliberately out of scope:** the wider escort-client
identity/routing convergence question (Phase 1B.2). Phase 1B only certifies
the API-boundary role whitelist; it does not certify or claim to fix the
Source A/B/C convergence gap, the empty-list conditional risk, or the
webhook/poller LID parity gap documented in the Phase 1B.2 proposal above.

**Rollback (not needed, recorded for completeness):** `git revert ba1ac40
393781f bcb5a53 aee44bc && git push origin main && scripts/restart_gate.sh
--restart`. No database rollback required — no schema/data change shipped.

---

## Phase 1B.2 Escort Client Identity and Routing Convergence Proposal (FINALIZED per Owner decision — NOT IMPLEMENTED)

**Status: proposal only. No code, schema, or config changed. Awaiting explicit
separate Owner approval before any implementation, per the Owner's own
instruction that this session covers deployment + proposal, not Phase 1B.2 code.**

### 1. Current Behavior

- **Source A — `ESCORT_CLIENT_PHONES`:** `modules/escort/__init__.py::_is_allowed_escort_client()`
  (lines 276-281), called only from `handle_escort_client_message()` (line ~1757) —
  a post-identity order-parsing gate, not an identity source. A second, unused,
  divergent implementation exists at `shared/env.py::EscortEnv.is_escort_client()`
  (dead code, zero callers).
- **Source B — stored role:** `identity_brain._lookup_seed()` (`modules/identity_brain/__init__.py:198-210`),
  Step 2 (line 101) — the only fully-implemented, reliable source.
- **Source C — contact name:** does not exist. `identity_brain._lookup_contact()`
  (line 320) / `_map_relation()` (line 337), Step 4 (line 152), match
  `wbom_contacts.relation` — a structured field, 99.9% `'unknown'` — never
  `display_name`.
- **Step 6 fallback** (line 168): keyword-only (`_ESCORT_CONTENT_RE`) classification
  of a never-before-seen sender to `role="repeat_client"`, which
  `message_router._ESCORT_ROLES` (line 77) treats identically to `client_escort_buyer`
  for routing purposes.
- **Webhook behavior:** `app/main.py::_handle_bridge_event()` (line 1534) strips only
  `@s.whatsapp.net`/`+`; never resolves `@lid`.
- **Poller behavior:** `modules/bridge_poller/__init__.py::_load_lid_map()`
  (lines 241-252) resolves LID → phone via the bridge's own `whatsmeow_lid_map`
  SQLite table before calling `process_message()`.
- **LID behavior:** a LID-only sender is `unknown` on webhook, correctly resolved
  on poller — a real parity gap.
- **Empty-list behavior:** confirmed = allowlist gate disabled, not automatic
  `client_escort_buyer` promotion (Interpretation A/C, not B) — see Session 21
  reconciliation entry above for the full trace.

### 2. Owner Decisions (now authoritative, recorded verbatim in substance)

1. **Canonical identity:** `client_escort_buyer` is the one canonical identity;
   A/B/C must converge to it, not remain three independent concepts.
2. **Priority order:** (1) `.env` explicit phone match, (2) stored
   `fazle_contact_roles.role`, (3) contact-name "escort" marker, (4) existing
   lower-confidence fallback. A higher-priority explicit match is never
   overwritten by a lower one. The resolver must record which source fired.
3. **Contact-name matching:** case-insensitive, Unicode-safe, whitespace/punctuation-
   tolerant, word/token-boundary match on "escort" — not a bare substring match
   (must reject e.g. a name that merely contains "escort" mid-word with no
   token boundary). Evidence recorded with the matched name appropriately
   masked/limited in logs.
4. **Empty `ESCORT_CLIENT_PHONES`:** disables the phone-allowlist gate only;
   stored-role and contact-name detection keep working; the Step 6 keyword-only
   fallback must **not** be treated as a trusted, persistent identity source.
5. **Unverified message-content fallback:** Step 6 becomes a *routing signal for
   review only* — labeled `unverified_escort_order_source` (or an equivalent
   existing review-status vocabulary), never silently promoted to a persisted
   `client_escort_buyer` row, never auto-confirmed, never granted any
   financial/admin/RBAC weight.
6. **Webhook/poller parity:** both must resolve a given sender to the same
   canonical identity; LID resolution must use the existing safe mapping
   (never invent a phone number, never collapse distinct LIDs, retain original
   JID as evidence, fall through to "unverified" rather than misclassify on
   resolution failure); `.env` phone matching only after a normalized phone is
   actually obtained.

### 3. Exact Gaps

- No contact-name classifier exists (Source B entirely missing).
- `ESCORT_CLIENT_PHONES` used only as a parser-stage gate, not wired into identity
  resolution at all.
- `fazle_contact_roles.role` is the only reliable identity source today — a single
  point of failure for the "three converging sources" design the Owner intends.
- `shared/env.py::EscortEnv.is_escort_client()` is dead, duplicate code (divergent
  from the one actually used in `modules/escort/__init__.py`).
- Webhook/poller LID mismatch — webhook path has zero LID resolution.
- Verified (A/B/C) vs. unverified (Step 6 keyword-only) trust levels are conflated
  today — both land in the same `_ESCORT_ROLES` set with no distinguishing flag.

### 4. Proposed Architecture

- **Canonical resolver location: `modules/identity_brain`.** Not `modules/escort/__init__.py`
  — `identity_brain.detect_identity()` is already the single shared entry point both
  webhook (`app/main.py::_handle_bridge_event()`) and poller
  (`modules/bridge_poller/__init__.py`, via `process_message()`) call before any
  role-specific handling. Centralizing there gives webhook/poller parity "for
  free" for sources A/B/C, rather than requiring each ingestion path to
  separately re-implement escort-client detection.
- **Input contract:** sender phone (raw, pre-normalization), original JID (may
  carry `@lid`), resolved contact-book name (if any), current `fazle_contact_roles`
  row (if any), `Settings` (for `.env` lists).
- **Result contract** (conceptual — exact type to match this module's existing
  `_build()`/`_build_contact()`/`_unknown()` pattern rather than introducing a
  new unrelated shape): `is_escort_client: bool`, `canonical_role: Optional[str]`,
  `is_verified: bool`, `source: Optional[str]` (`"env_phone" | "stored_contact_role"
  | "contact_name_marker" | "unverified_message_content" | None`),
  `normalized_phone`, `original_jid`, `resolved_contact_name`, `evidence: dict`.
- **Verified/unverified distinction:** a new boolean (or equivalent) threaded
  through the existing `identity_role`/`identity_source`/`identity_priority`
  dict fields `detect_identity()` already returns, so `message_router` can
  branch on it without a second parallel lookup.
- **Integration points:** `identity_brain.detect_identity()` (add the new
  priority-ordered check before/alongside today's Step 2/4/6), `message_router`
  (branch on `is_verified` for the two routing-contract paths in §14 below),
  `modules/escort/__init__.py::handle_escort_client_message()` (receives the
  verified/unverified flag instead of re-deriving trust itself).

### 5. File-by-File Change Plan

| File | Proposed change | Reason |
|---|---|---|
| `modules/identity_brain/__init__.py` | New canonical escort-client resolution step, priority-ordered per Decision 2; new contact-name "escort" token matcher; Step 6's escort-content branch reclassified as unverified, not a trusted role | Centralizes A/B/C, gives webhook/poller parity, implements Decisions 1-3, 5 |
| `modules/escort/__init__.py` | `_is_allowed_escort_client()` role narrowed to a true parser-stage gate only, consuming the new resolver's `is_verified`/`source` instead of independently re-deriving trust; `handle_escort_client_message()` branches per §14's two-path contract | Decision 4, 5 — separates verified/unverified handling |
| `shared/env.py` | Remove or clearly deprecate the dead, divergent `is_escort_client()` | Closes the duplicate-implementation gap (§3) |
| `app/main.py::_handle_bridge_event()` | Add LID resolution (reuse the poller's existing `whatsmeow_lid_map`-based approach or a shared helper) before calling `detect_identity()` | Decision 6 — webhook/poller parity |
| `modules/message_router/__init__.py` | Branch on the new `is_verified` flag for auto-reply suppression / admin-draft routing per §14's contract | Implements the verified/unverified routing contract |

### 6. Routing Decision Table

| Identity source | Verified | Generic reply | Parser | Admin draft | Persist role |
|---|---:|---|---|---|---|
| A — `.env` phone match | Yes | Suppressed | Evaluated | Yes | No change (source of truth is `.env`, not a persisted row) |
| B (proposed) — stored `client_escort_buyer` | Yes | Suppressed | Evaluated | Yes | Already persisted |
| C (proposed) — contact-name marker | Yes | Suppressed | Evaluated | Yes | Optional — could seed a `fazle_contact_roles` row for future speed, needs Owner input |
| Step 6 — keyword-only, unverified | **No** | Suppressed (for that message only) | Evaluated, safely | Yes, labeled `unverified_escort_order_source` | **Never** |

### 7. Webhook/Poller Parity

- Shared normalization: both paths call the same `identity_brain.detect_identity()`
  once the resolver above lands — no separate re-implementation.
- LID mapping: webhook gains the same `whatsmeow_lid_map`-based resolution the
  poller already has (exact mechanism TBD at implementation time — reuse vs.
  extract to a shared helper is an implementation-time call, not a design
  decision that changes behavior).
- Failure behavior: unresolved LID → proceed as unverified, never fabricate a
  phone number, never collapse distinct LIDs into one identity.
- Original JID retention: kept as evidence in the result contract regardless of
  resolution success.

### 8. Empty-List Behavior

- **Current:** disables the phone-allowlist parser gate only (confirmed, not
  assumed — see Session 21 reconciliation trace).
- **Proposed:** unchanged for the gate itself; the resolver's Decision 4/5
  changes apply regardless of list state — an empty list changes nothing about
  whether Step 6 output is trusted (it never should be, list state or not).
- **Compatibility impact:** none expected on already-verified senders (A/B/C);
  today's "open mode lets an unverified sender's order through the parser" stays
  true, but the order/sender would now be correctly labeled unverified rather
  than silently riding on `repeat_client`'s undifferentiated trust level.

### 9. Data and Schema Impact

- **Schema required:** No, for the minimum viable version (verified/unverified
  can travel as a dict field, same pattern as existing `identity_*` fields).
  Optionally, if Source C (contact-name match) is meant to *persist* a
  `fazle_contact_roles` row for future speed (open question in §6), that would
  be an additive `INSERT` through the already-existing, already-whitelisted
  Phase 1B endpoint/logic — no new column needed either way.
- **Existing row changes:** none required.
- **Backfill:** none required.
- **Audit metadata:** the proposed `evidence`/`source` fields serve this
  directly; where an admin-review draft is created for an unverified sender, the
  draft's own audit trail should carry the `unverified_escort_order_source` label.

### 10. Risks

- False positive identity: contact-name token matching, done carelessly, could
  misclassify (mitigated by Decision 3's token-boundary requirement).
- False negative identity: over-tightening the name matcher could miss real
  escort clients whose contact names are formatted unexpectedly.
- Order loss: any change to the Step 6 path must preserve "create an admin-review
  draft" behavior — must not regress to silently dropping unverified orders.
- Auto-reply leakage: must confirm suppression still fires for the unverified
  path exactly as it does today for `repeat_client`.
- Duplicate drafts: the existing dedup mechanism must be confirmed compatible
  with the new verified/unverified split.
- LID mismatch: webhook LID resolution must be proven against real bridge data,
  not just unit-mocked, before certification.
- Legacy behavior change: today's 3-phone `ESCORT_CLIENT_PHONES` list masks
  most of this — real-world behavior change is largest for empty-list and
  never-before-seen-sender scenarios, which need deliberate test coverage since
  they're rare in current traffic.

### 11. Tests

Unit: resolver priority order (A beats B beats C beats Step 6, each in isolation
and layered); contact-name matcher (true positives from §Decision 3's examples,
true negatives against bare-substring false positives). Integration: full
webhook and full poller round-trips for a phone-only sender, a LID-only sender,
a contact-name-only sender, a stored-role-only sender, and a no-match sender.
LID: resolved vs. unresolved LID behavior on both paths. Open mode vs. closed
mode: identical resolver behavior for A/B/C, differing only in whether the
phone-list *gate* (not identity) applies. Complete order / incomplete order /
non-order message: existing parser behavior preserved for verified senders;
unverified-path equivalents newly tested. Duplicate message: existing dedup
behavior confirmed unchanged.

### 12. Rollout

Feature flag recommended given the sender-identity blast radius (affects every
inbound WhatsApp message, not just escort-shaped ones) — e.g. gate the new
resolver behind a settings flag defaulting to today's behavior, flip only after
a shadow-comparison period. Shadow comparison: log what the new resolver would
have decided alongside today's actual decision for some window before cutover.
Logging: source/evidence on every resolution, verified/unverified explicitly
visible in logs for the first weeks. Smoke tests: same shape as Phase 1B's
(disposable phone/LID, read-only collision check first). Monitoring: watch
`unverified_escort_order_source`-labeled draft volume for a spike (would
indicate the empty-list/Step-6 risk is live) or a drop-to-zero (would indicate
over-suppression).

### 13. Rollback

Code rollback: revert the resolver commit(s); since integration points are
additive branches (`is_verified` check), a revert restores today's undifferentiated
behavior exactly. Config rollback: flip the proposed feature flag off. Schema
rollback: not applicable (no schema change in the minimum viable version).
Data cleanup: not applicable unless Source C is given persistence (§9's open
question), in which case any auto-seeded rows would need their own reviewable
cleanup path — flagged for the eventual detailed implementation proposal, not
decided here.

### 14. Owner Approval Gate

**Exact files this would touch (not yet approved for editing):**
`modules/identity_brain/__init__.py`, `modules/escort/__init__.py`, `shared/env.py`,
`app/main.py`, `modules/message_router/__init__.py`.

**Exact behavior changes (not yet approved):** contact-name "escort" token
matching as a new identity source; `ESCORT_CLIENT_PHONES` elevated from
parser-gate-only to an actual identity source; Step 6's keyword fallback
downgraded from full `_ESCORT_ROLES` trust to a labeled "unverified" path;
webhook gains LID resolution.

**Explicitly excluded from this proposal, still open:** whether Source C
should *persist* a `fazle_contact_roles` row (§9); the feature-flag/shadow-rollout
mechanics (implementation-time detail, not a design decision); any change to
RBAC, `.env`, or financial-authority code (none proposed, none needed).

**Implementation performed: No.** This entire section is a proposal only,
produced per Owner authorization to prepare (not implement) the Phase 1B.2
decision package. Awaiting explicit separate Owner approval to begin coding.

### Session 21, Phase 1B.2 — Implementation, Deployment, Production Certification (2026-07-17)

Owner explicitly approved implementing the ratified Phase 1B.2 proposal above.
Implemented, tested, pushed, deployed, and certified in this session.

**Implementation (4 commits):**

| Commit | File(s) | Summary |
|---|---|---|
| `7313613` | `modules/identity_brain/__init__.py`, `shared/env.py` | New Step 3.7 (env_phone) and Step 4 name-marker check, both additive, priority-ordered per Owner Decision 2; new `resolve_escort_client_identity()` canonical resolver; dead `shared/env.py::is_escort_client()` documented as superseded, not deleted |
| `45d23fa` | `modules/message_router/__init__.py`, `modules/escort/__init__.py` | `_is_verified_escort_identity()` helper; both `handle_escort_client_message()` call sites now pass `is_verified`; unverified admin drafts labeled; `save_escort_programs()` records `source_verified` in remarks; verified senders no longer blocked by the phone-only allowlist gate (a real gap this fix closes) |
| `24051fe` | `app/main.py` | Webhook `@lid` resolution via the poller's own `_load_lid_map()`, resolved per-bridge from existing settings; unresolved LIDs get the poller's `"unresolved:<lid>"` sentinel, never a fabricated phone |
| `f8a1d75` | `tests/unit/test_escort_client_identity_convergence.py` (new) | 39 tests |

**Design interpretation decisions made during implementation** (each individually
justified, not arbitrary):
- Source A (env_phone) and Source B (contact-name marker) checks are inserted
  additively, only reached when the existing seed-rule/employee/cash/attendance/
  escort-roster checks all missed — guarantees zero regression risk to any
  existing admin/family/accountant/employee/vip_client/vendor/supervisor
  classification, at the cost of env_phone not literally overriding an existing
  employee match (judged the safer reading of "priority among escort-client
  sources" vs. "global identity priority," given `.env`'s escort-client list is
  operationally disjoint from employee/guard phone numbers by construction).
- The Step 6 keyword-only fallback's actual `role`/`source` values
  (`repeat_client`/`text_hint`) were left byte-identical — `repeat_client` has
  other legitimate, unrelated uses (message_router KB/AI routing,
  `DRAFT_ALWAYS_ROLES`) that a role-string change would have broken. The
  verified/unverified distinction is carried by `identity_source == "text_hint"`
  instead, recognized explicitly by both `resolve_escort_client_identity()` and
  `message_router._is_verified_escort_identity()`.
- Contact-name matching checked before the legacy `wbom_contacts.relation`-based
  classification (which is ~0.1% populated in production and mostly dead in
  practice) — an edge case where a contact's `relation` says e.g. `"family"`
  but its `display_name` also contains "escort" would now classify as
  `client_escort_buyer`; judged acceptable given `relation`'s near-total
  non-population, flagged here for the record rather than silently decided.
- The phone-allowlist gate inside `handle_escort_client_message()` was narrowed
  to apply only to the **unverified** path — unchanged for that path,
  removed for the verified path (a real fix, not scope creep: a sender
  verified via stored role or contact-name marker was previously rejected by a
  gate that only ever checked `.env` membership).
- Incomplete-order handling, dedup mechanics, and the parser's own
  minimum-field policy were deliberately left untouched, per the proposal's
  own explicit scope limits.

**Tests:** 39 new (verified sources + false-positive rejections, source
priority, phone-form normalization equivalence, empty-allowlist semantics,
canonical resolver contract, authority isolation, message_router helper,
escort-module unverified labeling and the fixed verified-gate, webhook/poller
LID parity using a real disposable Postgres) — all pass. One test-hygiene
issue found and fixed during verification (not a defect in the implementation
itself): the LID webhook tests initially attempted a real network call via
the AI-reply fallback path for an "unknown" classified message; fixed by
adding the existing `mock_all_services` fixture.

Broader regression sweep (`test_identity_brain.py`, `test_message_router_identity_seed.py`,
`test_escort_module.py`, `test_escort_lifecycle.py`, `test_escort_roster_routes.py`,
`test_escort_source_of_truth_refactor.py`, `test_shared_auth_deps.py`,
`tests/integration/test_webhooks.py`, plus the new file): **1 pre-existing
failure** (`test_escort_source_of_truth_refactor.py::test_draft_updated_by_bridge_confirmation`,
`_is_authoritative_bridge_confirmation()` — an unrelated code path this
session never touched, matches a failure already on record from a prior
session's own git-stash-isolation check). Notably,
`test_escort_module.py::test_creates_escort_program_and_draft` — the *other*
previously-documented pre-existing failure — passed in every run this
session; no causal mechanism was identified connecting it to this diff, and
it is flagged here as an observation, not claimed as a fix, since test-order
sensitivity was independently confirmed elsewhere this session (the LID test
hang, resolved by adding `mock_all_services`).

Verified against the same isolated disposable test Postgres container
(`docker-compose.test.yml`'s `postgres-test` service) used for Phase 1B,
torn down cleanly afterward (`docker compose down -v`, confirmed removed).

**Git:** pushed (`7313613`, `45d23fa`, `24051fe`, `f8a1d75`), `HEAD` ==
`origin/main` confirmed at `f8a1d75`.

**Deployment:** `scripts/restart_gate.sh --restart` — all 7 pre-checks PASS
(xfail count unchanged at 13, targeted tests 100%, migrations clean, `.env`
flags unchanged), health `ok` before and after (PID `1598304`→`1598344`),
clean startup log (no import/Pydantic errors), zero new errors observed in
logs after restart during a live-traffic observation window (bridge poller
actively running, RAG rebuild completed normally).

**Live production smoke tests — deliberately scoped down, documented reasoning:**
Per this task's own instruction ("do not message real customers unless the
Owner separately authorizes it"), synthetic webhook payloads were **not**
injected into the live production ingestion pipeline. Unlike Phase 1B's
contact-role smoke test (a cleanly reversible, disposable row in a low-stakes
lookup table), triggering `_handle_bridge_event()` live risks a real outbound
send via the bridge's own send API if a reply is generated, and unsigned
webhook payloads are currently accepted in this environment (`enforcement
disabled`, a pre-existing, separately-tracked item) — a materially different
risk class from Phase 1B's smoke test. Live-traffic proof for the specific
escort-order-creation path (verified/unverified order creation, admin draft
delivery, dedup under real bridge traffic) is **not yet independently
confirmed against production** and is flagged as the one residual risk
below. Primary evidence for this deployment is the automated suite above,
which exercises the identical webhook HTTP handler (including the new LID
resolution code) against a real Postgres via `ASGITransport`, with
bridge/Ollama network calls mocked — a faithful stand-in for the code path
itself, though not literally the production process.

**Phase 1B.2: CERTIFIED** for the identity-convergence and routing-labeling
logic itself (all automated test evidence, clean deploy, zero new errors).
**Residual risk, explicitly flagged, not blocking:** the escort-order
creation/admin-draft/dedup path has not been exercised by a live production
webhook call this session, by deliberate safety choice — recommend observing
production logs for the next real inbound escort-order message (webhook or
poller) to confirm end-to-end behavior, or a separately-authorized live test
with an explicitly Owner-approved disposable scenario if faster confirmation
is wanted.

**Rollback (not needed, recorded for completeness):**
```
git revert f8a1d75 24051fe 45d23fa 7313613
git push origin main
scripts/restart_gate.sh --restart
curl -fsS http://127.0.0.1:8200/health
```
No database rollback required — no schema/data change shipped (the
`source_verified` remarks field is additive-only, on a JSON-shaped column
that already tolerates new keys).

**Not implemented, deliberately out of scope:** the incomplete-order handling
redesign, any feature-flag/shadow-rollout mechanism, Source C (contact-name
marker) row persistence, Phase 1C, Dispatch, GL, AI Console, frontend
consolidation, or any RBAC/financial-authority change.

### Session 22 — Security Incident: Test Postgres Public Exposure (2026-07-17)

While resuming Phase 1B.2 Stage A live-verification work (see below), the
disposable `docker-compose.test.yml` `postgres-test` container was found
**actively targeted by a real PostgreSQL RCE exploit attempt** within
minutes of startup — twice in the same session. Full evidence, timeline,
and decoded payload:
`knowledge_base/00_governance/incidents/2026-07-17_test_postgres_public_exposure.md`.

**Summary:** `docker-compose.test.yml` published `postgres-test` on
`"5433:5432"` (Docker's default publishes all host interfaces when no
`127.0.0.1:` prefix is given) with a hardcoded `POSTGRES_PASSWORD: postgres`.
This VPS has a public IP (`5.189.131.48`); an internet scanner found and hit
the port, authenticated with the trivial password, and attempted a classic
`CREATE FUNCTION system(cstring) ... LANGUAGE 'c'` privilege-escalation
technique to fetch and run a Kinsing-family cryptomining dropper from
`196.251.121.185/bot`. **The exploit failed** — the image is
`postgres:15-alpine` (musl libc), and the attack hardcoded glibc paths that
don't exist on it, so the malicious UDF never loaded and the payload never
ran.

**Immediate containment:** container killed, then
`docker compose -f docker-compose.test.yml down -v --remove-orphans` —
confirmed no container, volume, or network remained.

**Host compromise checks (all clean):** no suspicious processes (kinsing/
kdevtmpfsi/miner names), no executables in `/tmp`, `/var/tmp`, `/dev/shm`,
no cron/systemd persistence added in the relevant window, no new users, no
`authorized_keys` tampering found, no active or historical connections to
the attacker IP, login history shows only recognized source IPs, docker
daemon logs show nothing anomalous.

**Production Postgres (`ai-postgres`) checks (all clean):** confirmed still
bound `127.0.0.1:5432` only (never exposed by this incident); read-only
inspection found no unknown/malicious functions (all `lanname='c'` functions
are legitimate `pgvector`/`pg_trgm` extension internals plus standard
built-ins), no unexpected roles (`postgres`, `fazle_ai_reader`,
`ollama_memory_owner` plus standard `pg_*` roles only, no altered login/
superuser flags), no unexpected databases (`postgres`, `fazle_ollama_memory`,
`fazle_test` — the last one is this repo's own pre-existing, legitimate
same-server test-database convention, unrelated to today's incident and the
separate disposable-container `postgres-test` service), no unexpected
extensions.

**Remediation, committed:**
- `599dacd` — `postgres-test` and `redis-test` both rebound to `127.0.0.1`
  only; `POSTGRES_PASSWORD`/`TEST_DATABASE_URL` now require `TEST_DB_PASSWORD`
  from the environment (fails loud, no hardcoded fallback) instead of the
  trivial checked-in `postgres` password. Verified via `ss -lntp` (listener
  shows `127.0.0.1:5433`, not `0.0.0.0`/`*`) and a direct connection attempt
  against the host's own public IP (correctly refused).
- `111c957` — full incident evidence record committed.
- Compose-file sweep (`grep` across `/home/azim` for `5432`-shaped exposed
  ports): no other exposed database found — `ai-postgres`
  (`127.0.0.1:5432`) and `vps-monorepo-staging/docker-compose.yml`
  (`127.0.0.1:5432:5432`) were already correctly bound.

**Separate, lower-severity housekeeping finding, not part of this incident's
remediation (flagged, not fixed):** a stale, already-rotated production
Postgres password (confirmed via safe comparison against the current live
credential — **not** a match, i.e. genuinely dead) was found sitting in
plaintext in `/home/azim/.bash_history` and
`/home/azim/ESCORT-ROSTER-PRODUCTION-PLAN-2026-07-02.md` (a loose file at
the home-directory root, **not** inside any git repository — confirmed via
`git rev-parse --is-inside-work-tree` failing at `/home/azim`). Dead
credential, not in git, low risk — but poor hygiene worth cleaning up
(`history -c`/`.bash_history` edit, and redacting or deleting the old
planning doc) whenever convenient. Not touched this session — editing a
user's shell history or personal files without being asked is out of scope
for an incident-response pass.

**Safe to resume Stage A: Yes.** **Safe to begin Phase 1C: Yes** (production
Postgres integrity confirmed, host confirmed clean, test Postgres confirmed
no longer publicly reachable, trivial password removed).

### Phase 1B.2 Stage A — Live End-to-End Escort Order Verification (2026-07-17)

Completed after the security remediation above, using **Method 3** (the
task's own sanctioned fallback: production code — `identity_brain`,
`message_router`, `modules.escort`, `app.main`'s real webhook handler —
exercised end-to-end against a real, now-properly-secured disposable
Postgres, with only the external bridge send and Ollama/Groq calls mocked).
Method 1 (observe a genuine order) wasn't practical synchronously; Method 2
(Owner-controlled real phone) wasn't available in this session. No synthetic
payload was ever sent to the live production webhook, since unsigned
webhook payloads are currently accepted there (a separate, already-tracked
item) and a live send risk was judged unacceptable for a routine
verification pass.

**4 tests, all pass** (`tests/unit/test_stage_a_live_escort_verification.py`,
commit `f57def2`):
1. Verified sender (`env_phone` source) + complete order → identity
   correctly resolves `client_escort_buyer`; exactly one
   `wbom_escort_programs` row created, `status='draft'` (never
   auto-confirmed — confirmed structurally, the INSERT hardcodes `'draft'`);
   `remarks.source_verified = true`; exactly one admin notification enqueued
   into `fazle_outbound_queue`, correctly unlabeled; zero entries addressed
   to the client's own phone (no client-facing reply).
2. Same `message_id` replayed → zero duplicates anywhere (message, program,
   or admin notification) — confirms the webhook-level
   `processed_bridge_messages` dedup guard short-circuits before any further
   processing.
3. Unverified, never-before-seen sender + order-shaped text → identity
   resolves `repeat_client`/`text_hint` (never `client_escort_buyer`); the
   draft program's `remarks.source_verified = false`; the admin notification
   is prefixed `"⚠️ UNVERIFIED SENDER"`; no `fazle_contact_roles` row is
   auto-created from message content alone; no client-facing reply.
4. No attendance, `fpe_cash_transactions`, or elevated `fazle_contact_roles`
   role created as a side effect of an escort order.

**Real production log trace from the first (pre-incident) run already
confirmed this working correctly** before the security interruption —
e.g. `[identity] 8801766600001 → client_escort_buyer (env_phone)`,
`[escort] ... verified=True`, `[OUTBOUND_ENQUEUE] ... purpose=internal-notification`,
and for the unverified case `[identity] ... → repeat_client (text_hint, escort content)`
with the admin body starting `'⚠️ UNVERIFIED SENDER — order-like message...'`.
The only issues found were in the *test's own* SQL (a wrong column name,
`wbom_whatsapp_messages.id` instead of the real `message_id`) and two
over-fitted parser-output assertions — not the Phase 1B.2 implementation
itself, which needed no changes.

**Discovered, not part of Phase 1B.2's scope, logged only:**
`escort_roster_audit_logs` relation does not exist in the test schema
(`[audit] log_audit failed`, caught and logged, non-fatal) — another
instance of the test/production schema-drift class already tracked in
`NEXT_IMPLEMENTATION_QUEUE.md` Technical Debt, not a new category.

**Stage A result: PASS. Safe to begin Phase 1C: Yes.**

### Session 23, Stage 0 — Stale Credential Residue Cleanup (2026-07-17)

Follow-up to the test-Postgres exposure incident above: cleaned the stale,
already-rotated (confirmed dead — does not match the live production
credential, verified by comparison earlier this session without printing
either value) production Postgres password out of plaintext residue.
**No production credential was changed; no service was restarted.**

**Originally-reported locations, both confirmed and cleaned:**
- `/home/azim/.bash_history` (mode `0600`, owner `azim`, unchanged) — 3
  matching history lines removed (not just redacted — a shell history entry
  is a full command, so the whole line was dropped), 1997 unrelated lines
  preserved untouched. Backed up first to
  `/home/azim/.bash_history.pre-redaction-2026-07-17.bak` (mode `0600`).
  Verified zero remaining matches. **Caveat, not fully closeable from
  outside a session:** 3 other interactive bash sessions (`pts/0`, `pts/1`,
  `pts/2`, all started 2026-07-17, i.e. after the credential was last
  plausibly typed) are currently open; if any of their in-memory history
  buffers still held the old command they could re-append it to
  `.bash_history` on exit — not independently verifiable without reaching
  into those sessions, which this session did not do. Low residual risk
  given their start times.
- `/home/azim/ESCORT-ROSTER-PRODUCTION-PLAN-2026-07-02.md` (mode `0664`,
  owner `azim`, unchanged, **not inside any git repository** — confirmed
  via `git rev-parse --is-inside-work-tree` failing at `/home/azim`) — the
  one credential-bearing line's password redacted in place to
  `[REDACTED — credential rotated]`; all other content, line count (514),
  and permissions preserved exactly.

**Wider scan (`/home/azim`, `/home/azim/core`, excluding `.git`,
`node_modules`, `.venv`, `__pycache__`) found the same stale value in
several additional categories — file paths and counts only, never the
secret or matching line content:**

| Category | Count | Action |
|---|---:|---|
| `/home/azim/scripts/auto_maintain.py` (live cron script, `0 2 * * *`) | 1 | **Cleaned this session** — see below |
| `/home/azim/backups/*` (2 backup archives: `al-aqsa-docker-compose.yml`, `agent.env`) | 9 total matches | **Not modified** — explicitly out of scope per the stop-before-archives/backups rule; reported for Owner decision |
| `/home/azim/.vscode-server/.../User/History/*` (VS Code local file-history snapshots) | 46 | **Not modified** — editor-internal backup mechanism, same class as "unknown application data" |
| `/home/azim/.codex/sessions/*.jsonl` (OpenAI Codex CLI session transcripts) | 5 | **Not modified** — another tool's own session store |
| `/home/azim/.claude/file-history/*`, `/home/azim/.claude/projects/-home-azim/*.jsonl` (Claude Code's own session/project transcripts, **including this session's own conversation log and a cached tool-output file from this session's own earlier wider-scan command**) | 37 | **Not modified** — this is the running harness's own conversation/audit trail; editing or deleting it was judged inappropriate and potentially disruptive to the active session, not a decision this pass should make unilaterally |
| GitHub Copilot Chat transcripts (found nested under `.vscode-server`, counted within that category above) | — | **Not modified** — another tool's own data store |

**`auto_maintain.py` finding detail:** the stale value was hardcoded as the
default argument of an `os.environ.get('DATABASE_URL', ...)` call — a full
Postgres connection string (user `postgres`, host `172.20.0.3`, port 5432,
db `postgres`) with the stale password embedded — reached only if
`DATABASE_URL` isn't resolvable from `.env`/
`DATABASE_URL_TEMPLATE` first. Whether this fallback path is ever actually
exercised by the nightly cron run wasn't independently traced this session
(out of Stage 0's scope); either way, hardcoding a real-shaped credential as
a fallback is bad practice regardless of whether it's live. Replaced with an
obviously-non-functional placeholder
(`ROTATED-CREDENTIAL-REMOVED-SET-DATABASE_URL-ENV-VAR`) — preserves the
exact same behavior as before (both the old stale password and the new
placeholder fail to authenticate identically if this branch is ever hit;
no functional regression, since this path was never going to work with the
already-rotated password anyway). Verified `python3 -m py_compile` clean,
permissions (`rwxrwxr-x`, owner `azim`) and line count preserved except the
one substituted line.

**Not committed to git:** `.bash_history`, the loose planning `.md` file,
and `auto_maintain.py` are all outside `/home/azim/core` and/or not
git-tracked — nothing to commit for these. Only this documentation update
(inside the repo) is committed.

**Verification:** all three targeted files (`~/.bash_history`, the planning
doc, `auto_maintain.py`) independently re-confirmed at zero remaining
matches after cleanup. No password was printed in any command output,
report, or this record — all verification used match counts and line
numbers only.

**Stage 0 result: complete for all directly-actionable, in-scope locations.**
Backups, editor/AI-tool-internal history stores, and this session's own
transcript are flagged for Owner awareness but intentionally left
unmodified, per the explicit stop-before-archives/unknown-application-data
rule.

### Session 23, Stage 1 — Phase 1C: FPE Employee Resolution Status Consistency (2026-07-17)

**Canonical invariant, validated against live production data and code
before implementation (not assumed):**

```
resolution_status = 'unresolved' (schema default — "not yet classified in
any dimension") AND wbom_employee_id IS NOT NULL
→ resolution_status = 'resolved', set atomically with the link.
```

**Confirmed legitimate exceptions, never touched by this fix:**
- `resolution_status IN ('duplicate', 'canonical', 'inactive')` — a
  *separate* dimension owned by `modules.fazle_payroll_engine.normalization`'s
  dedup workflow (`link_duplicate()`, `mark_inactive()`). Zero live rows in
  this state today, but the fix's predicate (`WHERE resolution_status =
  'unresolved'`, never `<> 'resolved'`) protects it regardless, since these
  functions are live, reachable code, not dead.
- `resolution_status = 'resolved' AND wbom_employee_id IS NULL` — confirmed
  live: all 6 such rows are `created_source = 'admin_manual_create'`,
  i.e. deliberately WBOM-less FPE-only employees created via
  `fazle_payroll_engine/employee.py`'s admin-manual-create path, which
  hardcodes `resolution_status='resolved'` in the same INSERT regardless of
  WBOM linkage. Not a bug — a different, valid meaning of "resolved."
- `resolution_status = 'auto_created'` (12 rows, all unlinked) — a third,
  distinct status, out of this fix's scope either way (none are linked).

**All writers audited** (grep for every `fpe_employees.wbom_employee_id`
assignment repo-wide, not just the originally-cited function):

| # | File:location | Prior behavior | Fix |
|---|---|---|---|
| 1 | `payment_ingest/fpe_bridge.py` — existing-row phone-link UPDATE | Set link only | Conditional `resolution_status` added |
| 2 | `payment_ingest/fpe_bridge.py` — new-row `INSERT ... ON CONFLICT DO UPDATE` | Relied on schema default `'unresolved'`, no ON CONFLICT status handling | `resolution_status='resolved'` on INSERT; conditional CASE on the ON CONFLICT branch |
| 3 | `admin_employees/__init__.py::_seed_fpe()` | Set link only | Conditional `resolution_status` added |
| 4 | `admin_employees/__init__.py` — `POST /api/admin/employees/link-fpe` | Set link only | Conditional `resolution_status` added |
| 5 | `admin_employees/__init__.py` — `POST /api/admin/employees/create-and-link` | Set link only | Conditional `resolution_status` added |
| 6 | `fazle_payroll_engine/employee.py` — "runtime repair" link UPDATE (`match_or_create_employee`) | Set link only | Conditional `resolution_status` added |
| 7 | `fazle_payroll_engine/employee.py` — `whatsapp_auto_create` post-insert WBOM-link UPDATE | Set link only; row's own INSERT moments earlier hardcoded `'unresolved'` | Conditional `resolution_status` added — **the dominant real-world contributor, 152/153 of the live affected rows** |

**Already-correct writers, confirmed by inspection, untouched:**
`modules/escort/__init__.py`'s program-confirmation INSERT (already sets
`resolution_status='resolved'` inline) and
`fazle_payroll_engine/employee.py`'s `admin_manual_create` INSERT (already
sets `'resolved'` inline, by design, independent of WBOM linkage — this is
the source of the 6 legitimate resolved-but-unlinked rows above).

**Fix pattern, identical everywhere:**
```sql
resolution_status = CASE WHEN resolution_status = 'unresolved'
                          THEN 'resolved' ELSE resolution_status END
```
set in the same statement as `wbom_employee_id`. Deliberately conditional,
not unconditional — protects the dedup-workflow states even though none
currently exist, and costs nothing when they don't.

**Read-only production audit (before any write):**

| resolution_status | total | linked | unlinked |
|---|---:|---:|---:|
| `auto_created` | 12 | 0 | 12 |
| `resolved` | 93 | 87 | 6 |
| `unresolved` | 388 | 153 | 235 |

Linked-but-not-resolved: **153**. Resolved-but-unlinked: **6** (all
`admin_manual_create`, confirmed legitimate, not touched). Broken WBOM
references (`wbom_employee_id` pointing at a non-existent
`wbom_employees` row): **0**. `created_source` breakdown of the 153:
`whatsapp_auto_create` ×152, `payment_ingest_bridge` ×1 — exactly matching
writers #7 and #1 above, confirming root cause.

**Tests:** 13 new (`tests/unit/test_fpe_employee_resolution_status.py`,
commit `c2671fb`) against the isolated disposable test DB — all pass.
Broader regression sweep (existing employee/payment_ingest/reports/
shared-auth-deps tests): 92 passed, 16 failed — all 16 are the same
pre-existing, already-documented `fazle_contact_roles` test-schema drift
(missing `sub_role` column in the migration-built schema), unrelated to
this change.

**Historical backfill — executed, not deferred.** All 6 safety conditions
proven before running: every affected row's `wbom_employee_id` references
a real `wbom_employees` row (0 broken links); no linked-but-unresolved
state is legitimate (100% of the 153 map to the two confirmed-defective
writers, none to the dedup workflow or any other deliberate state);
original statuses are homogeneous and known (`'unresolved'` for all 153);
affected IDs captured before the write
(`knowledge_base/00_governance/phase1c_backfill_audit_20260717.json`,
commit `20c3778` — IDs + `wbom_employee_id` + original status +
`created_source` only, no personal data); predicate unambiguous
(`wbom_employee_id IS NOT NULL AND resolution_status = 'unresolved'`);
expected count (153) re-verified immediately before the transaction,
matched exactly, no drift from the earlier audit.

Executed in a single transaction with in-transaction pre/post assertions:
pre-count 153 (asserted), `UPDATE 153`, post-count of remaining
unresolved-but-linked rows 0 (asserted), resolved-but-unlinked inverse
check still exactly 6 (asserted unchanged — confirms the legitimate
exception rows were not touched). Transaction committed.

**Post-backfill verification (read-only, live):**

| Status | Total | Linked | Unlinked |
|---|---:|---:|---:|
| `auto_created` | 12 | 0 | 12 |
| `resolved` | 246 | 240 | 6 |
| `unresolved` | 235 | 0 | 235 |

Zero linked rows remain `unresolved`; the 6 legitimate
`admin_manual_create` resolved-but-unlinked rows are unchanged; zero
broken WBOM references. `modules.reports._b_unresolved_fpe_employees`'s
live output confirms end-to-end: `linked_but_mislabeled_count` **153 → 0**;
`no_wbom_match_count` (172) and `wbom_match_unlinked_count` (81) unchanged
— exactly matching the original Session 20 Phase 3 audit numbers,
confirming those two buckets were never disturbed.

**Not implemented, deliberately out of scope:** any change to employee
identity architecture, table merger, phone-column renaming, payroll
eligibility/formulas, employee deduplication rules, or the unresolved-
review UI integration (`wbom-create-link.html`/`wbom-link-review.html`
still run on their original static `CANDIDATES` snapshot, unrelated to
this fix).

**Git:** `55a2d79` (code fix), `c2671fb` (tests), `20c3778` (backfill audit
artifact) — pushed alongside this documentation commit.

**Deployment:** restart gate run, health confirmed before/after, logs
reviewed for new errors — see the final report for this session for exact
PID/health/log detail.

**Phase 1C: CERTIFIED.** Both the future-write fix and the historical
correction are live and verified.

### Session 24, Phase 1D — `fazle_contact_roles` Test-Schema Drift (2026-07-18)

**Drift table (production vs. previous test schema):**

| Element | Production (migration 029) | Previous test schema | Impact |
|---|---|---|---|
| `id` | `uuid DEFAULT gen_random_uuid()` | `SERIAL` (integer) | Type mismatch, not itself test-breaking |
| `sub_role` | `character varying(30) NOT NULL DEFAULT ''` | **missing** | `UndefinedColumnError` on any INSERT/UPDATE touching it (Phase 1B's whitelist code) |
| `language_pref` | `character varying(10) NOT NULL DEFAULT ''` | **missing** | Same class |
| `is_active` | `boolean DEFAULT true` | **missing** | Same class |
| `metadata` | `jsonb DEFAULT '{}'` | **missing** | Same class |
| `label` | **does not exist** | present (`TEXT`) | 4 test files seeded via this nonexistent-in-production column |
| `name` | `character varying(200) NOT NULL DEFAULT ''` | present, nullable | Minor nullability drift, not independently test-breaking |

**Root cause — not simply "incomplete," but permanently shadowed:**
`tests/conftest.py`'s own pre-migration `_BASE_SCHEMA_SQL` creates
`fazle_contact_roles` with a stale, hand-written shape (comment: "migration
004 ALTERs it, but we pre-add the columns so they survive"). Migration
029's own `CREATE TABLE IF NOT EXISTS public.fazle_contact_roles (...)` —
verified, already 100% correct, matches live production column-for-column
— runs later in the sequence but silently no-ops, because `IF NOT EXISTS`
sees the table already created by conftest's stub. **Migration 029 itself
was never the problem** — the test fixture was shadowing it.

**Canonical-source decision:** per the preferred order (run governed
migrations → reuse canonical builder → update test fixture to match →
avoid duplicated definitions), corrected `_BASE_SCHEMA_SQL`'s stub to
match migration 029 exactly (including both `UNIQUE` constraints migration
004/029 both rely on) rather than writing a new migration or a separate
schema builder — the stub has to keep existing (migration 004 requires the
table to pre-exist for its `ALTER TABLE`/seed `INSERT`), it just needed to
stop being wrong.

**Necessary consequence, not scope creep:** 3 other test files
(`test_webhooks.py`, `test_full_workflows.py`, `test_escort_payment_flow.py`)
plus conftest's own `seed_accountant` fixture seeded rows via the
now-removed `label` column — confirmed via repo-wide grep that `label`
exists in no production code path (`modules/contact_roles/routes.py`'s
INSERT/UPDATE column lists never include it). Renamed to `name` (the real
column serving the same "human-readable label" purpose) in all 4 locations
— required for these tests to keep passing after the schema fix, not
unrelated cleanup.

**No production schema or data changed.** No production restart required
(test-only change).

**Verification:**
- The exact 16 previously-failing tests: **45/45 pass**
  (`test_contact_roles.py` + `test_shared_auth_deps.py`)
- `test_webhooks.py` (directly modified): **10/10 pass**
- `test_identity_brain.py` + `test_fpe_employee_resolution_status.py`
  (unaffected by this change): **30/30 pass**
- `test_full_workflows.py` (directly modified): the one test exercising
  this fixture (`test_full_escort_lifecycle`) correctly resolved identity
  via the seeded `name`-column row (`client_escort_buyer (seed, conf=0,
  pri=50)` in the log) before failing later for an **unrelated,
  pre-existing** reason — a hardcoded release date now stale relative to
  "today." The file's other 5 failures are unrelated pre-existing
  function-signature drift (`compute_run()`, `save_attendance()`,
  `finalize_payment()` all called with outdated argument lists) — a
  separate technical-debt cluster, not touched.
- `test_escort_payment_flow.py` (directly modified): **could not be
  verified this session** — hangs in this environment independent of this
  change (killed after 100s with `--timeout=15` per-test still not
  interrupting it), matching the same "incompletely-mocked external call"
  pattern already found and fixed once this session (the Stage A LID
  tests needed `mock_all_services`) — flagged as a known, separate,
  pre-existing risk in this file, not fixed here.

**Test-infrastructure security re-confirmed:** disposable Postgres bound
`127.0.0.1:5433` only throughout, random `TEST_DB_PASSWORD`, containers/
volumes torn down after use.

**Phase 1D: schema drift precisely identified and corrected. The 16
originally-reported failures are resolved. Two adjacent files
(`test_full_workflows.py`'s non-fixture failures, `test_escort_payment_flow.py`'s
hang) are explicitly NOT part of this fix and remain open, unrelated
technical debt.**

**Git:** `9fea840`. Pushed. No restart performed (test-only).

### Session 24, Phase 1E — Live Unresolved-Employee Report Integration (2026-07-18)

**API contract confirmed via live read:** `GET /reports/unresolved_fpe_employees`
(same `X-Internal-Key` auth both pages already use), flat `rows` array (no
pagination — 253 rows total, safe for direct frontend use at this size),
per-row fields: `fpe_id`, `fpe_name`, `resolution_status`, `phone`, `salary`,
`joining_date`, `created_source`, `designation`, `txn_count`, `bucket`
(`no_wbom_match` | `wbom_match_unlinked` | `linked_but_mislabeled`), plus
bucket-specific `wbom_employee_id`/`wbom_match_id`/`wbom_match_name`/
`name_mismatch`. `summary` gives the 4 counts directly.

**Recounted before implementation (matches Phase 1C's post-backfill numbers
exactly, no drift):** `no_wbom_match=172`, `wbom_match_unlinked=81`,
`linked_but_mislabeled=0`, `total_reviewed=253`.

**Implementation:** both pages now call the live endpoint on boot and filter
client-side by `bucket`, replacing the hardcoded snapshots (180 rows /
33 rows) from when they were built. `wbom-link-review.html` also gained an
always-visible (even at 0) `linked_but_mislabeled` section — read-only,
future-regression detection per the explicit requirement not to remove
support for an empty category. Existing action endpoints
(`/api/admin/employees/create-and-link`, `/api/admin/employees/link-fpe`),
confirm dialogs, and the shared API-key login flow are unchanged. Added:
loading/network-error/403-unauthorized/empty states on both pages; explicit
`409`/`404` stale-row handling on `wbom-link-review.html`'s `linkPair()`
(`wbom-create-link.html` already had `409` handling, confirmed unchanged).
No new shared JS utility introduced — `shared-auth-fetch.js`'s semantics
(different auth/base-URL conventions) didn't cleanly fit these two
already-self-contained, differently-authenticated tools; extending it was
judged unnecessary risk for a 2-page, low-traffic admin surface.

**Static-snapshot removal:** `wbom-link-review.html`'s 33-row array deleted
outright (zero use for local demo). `wbom-create-link.html`'s 180-row array
kept as an explicitly-named, provably-unreferenced `_DEMO_FIXTURE_CANDIDATES`
constant (verified via grep: zero references outside its own declaration)
rather than deleted, per the "move to an explicit fixture, don't compete
with live data" instruction.

**Verification (no real browser available in this environment — see
"Remaining Risks" below):**
- Both pages confirmed serving `HTTP 200` from the running production
  static-file mount after the edit (static files are read live from disk,
  no restart needed for this class of change — confirmed empirically, not
  assumed).
- Diff-scanned both files for any dynamic string interpolation bypassing
  the existing `esc()` HTML-escaping helper — zero found; every new dynamic
  field goes through `esc()`.
- Node `--check` syntax-validated both files' extracted `<script>` blocks —
  clean.
- **End-to-end data-flow validation:** fed the real, live report response
  through both pages' exact client-side mapping/filter logic in a Node
  script. Result: `no_wbom_match` → 172 mapped candidates, `wbom_match_unlinked`
  → 81, `linked_but_mislabeled` → 0, zero rows with missing required fields
  in either mapping, and `172 + 81 + 0 = 253` — exactly matching
  `total_reviewed`, confirming complete, non-overlapping bucket coverage
  across both pages with no gap and no double-counting.

**Git:** `fe5b342`. No restart performed (static-file-only change,
confirmed unnecessary via the live 200 checks above).

**Not implemented, deliberately out of scope:** any change to
`create-and-link`/`link-fpe`'s backend logic beyond what Phase 1C already
touched (both endpoints' stale-state 409 handling was already correct,
confirmed by reading the code, not assumed); merging the two pages into
one (the current architecture cleanly supports the existing two-purpose
split); a shared frontend loader utility (judged unnecessary for 2
self-contained pages); pagination on the report endpoint (253 rows is
small enough for direct use, confirmed via the live read).

**Remaining risk, explicitly flagged:** no live browser click-through was
performed in this session (no browser automation tooling available in this
environment) — verification relied on syntax checking, XSS-safety diff
scanning, and feeding real production data through the exact client-side
logic in Node, which validates the data flow and mapping correctness but
not actual DOM rendering, CSS layout, or interactive click behavior.
Recommend a manual browser pass (both pages, both action buttons, at least
one real disposable link action) before treating this as fully certified
for interactive use, even though the underlying logic is verified correct.

---

## Phase 1E.1 — Real Browser Verification and UI Certification (2026-07-18)

**Objective:** Complete the missing browser-level certification for
`wbom-create-link.html` / `wbom-link-review.html`, flagged as an open risk
at the end of Phase 1E.

**Method:** `playwright` + `pytest-playwright` were already installed
(`pip list` confirmed) with Chromium available under
`~/.cache/ms-playwright` — used real headless Chromium against the live
production server, not a simulation. Read-only checks ran directly against
production data. All state-mutating checks (link/create actions, 409/404
handling, confirm/cancel dialogs, XSS payloads) used Playwright's
`page.route()` to intercept the specific `fetch()` calls with synthetic
data — the real backend was never sent a mutating request, so this needed
no disposable test-DB app instance and touched zero real unresolved-employee
records (a stronger safety posture than write-then-cleanup against a
disposable DB, since no write occurs at all).

**Defects found and fixed:**
1. `wbom-link-review.html` had no horizontal-scroll container around its
   table — on a 390px mobile viewport the whole page overflowed and
   squished unreadably instead of scrolling within its own container.
   Fixed: `#tableHost{overflow-x:auto}` + `table{min-width:600px}`.
2. Neither page had a dedicated refresh control in its normal (non-error)
   load state — only in error/empty fallback states. Added a `↻ Refresh`
   button to both pages' progress bar, wired to the existing
   `loadCandidates()`.

**Verified correct (no fix needed):** auth flow (wrong key rejected, no
`#app` reveal); live counts match API exactly (81/172, matching the
already-reported 253 total); XSS escaping on both pages, including a
targeted attribute-breakout payload
(`Evil" onmouseover="window.__xss_fired=true" x="`) against
`wbom-create-link.html`'s `<input value=...>` field — confirmed the
browser treats the whole string as inert text (`window.__xss_fired`
never set, input count stayed exactly 3); Bangla/Unicode name rendering
(a real production row, fpe_id 316, contains a Bangla name — rendered
correctly, screenshotted); a real 37-character English name (fpe_id 122)
did not break card layout; `linked_but_mislabeled` renders correctly at
its current real count of 0 (`<details open>`, "None" message); cancel on
the link-confirmation dialog fires zero network requests; a successful
link marks the row done and shows "✓ Linked"; a 409 (stale/already-linked)
response is caught and the row is removed with a clear message, never
silently overwritten.

**New permanent test file:** `tests/e2e/test_employee_review.py` (19
tests, `pytest.mark.e2e`, sync Playwright API — matches the existing
`tests/e2e/test_dashboard.py` convention to avoid the documented
asyncio-event-loop conflict with `pytest-asyncio`). Verified passing
against production: `TEST_APP_URL=http://localhost:8200
TEST_API_KEY=<redacted> pytest tests/e2e/test_employee_review.py -m e2e
--timeout=60 -v` → 19 passed.

**Git:** `03f851a`, pushed. No restart needed (static-file-only change,
served live from disk; confirmed via direct Playwright checks against the
running server both before and after the edit).

**Certification:** Phase 1E.1 CLOSED. The "no live browser interaction
pass yet" risk flagged at the end of Phase 1E is resolved.

---

## Phase 1G — Repair Workflow-Test Drift (2026-07-18)

**Objective:** Fix the 6 known failures in `tests/workflows/test_full_workflows.py`.

**Root-cause table:**

| Test | Failure | Current production contract | Action |
|---|---|---|---|
| `test_full_escort_lifecycle` | `ValueError: release end date/shift is before program start` | The order text's date hint (`"06/05/2026"`) is stored in `remarks.date_hint` but never parsed into `wbom_escort_programs.start_date` (confirmed empirically: stays NULL). `_handle_release_event_tx()` falls back to `program_date`, which defaults to the row's real insertion date — i.e. "today" whenever the test actually runs. The old hardcoded `End Date: 2026-05-05` was therefore never a once-correct date that went stale; it could never have passed on any date. | Updated test: release end date computed as `date.today() + timedelta(days=4)`, shift `N` — verified via direct invocation of `count_shifts()`/`shifts_to_days()` to yield exactly 10 shifts = 5.0 days, matching the test's own `day_count==5.0` assertion. No fixed-date dependency. |
| `test_advance_request_to_transaction` | `assert draft is not None` (no `fazle_payment_drafts` row created) | `modules.employee_conversation` now runs advance requests as a strict 4-turn state machine (trigger → amount → payout → explicit `"confirm"`); a single combined "trigger + amount" message only satisfies the trigger step, documented & covered by `tests/unit/test_sprint3a_employee_conversation.py::Test4VerificationCompleteDraftCreated`. | Updated test to send the same 4-message sequence through the real `/webhook/mcp1` HTTP path. |
| `test_advance_deducted_in_payroll` | `TypeError: compute_run() missing period_year` | `compute_run(employee_id, period_year: int, period_month: int, computed_by, ...)` — confirmed against all 3 live production call sites (`modules/admin_commands`, `modules/payroll` x2, `app/main.py`). | Updated test to pass `period_year=2026, period_month=5` as separate ints. **Also uncovered a real production bug — see below.** |
| `test_full_payroll_state_machine` | same `compute_run()` `TypeError` | same | Same signature fix. Also added a `wbom_employee_id`-linked `fpe_employees` row (`mark_paid()` fails closed without one, 2026-07-15 fix per `knowledge_base/06_developer_system/payroll_module.md`) — the test previously never reached that step. |
| `test_guard_reports_present_admin_approves` | `TypeError: save_attendance() missing attendance_date` | `attendance_date` is a required, no-default param — confirmed via the real production caller (`modules/attendance/__init__.py:406`) and sibling passing tests in `tests/unit/test_attendance.py`. | Added `attendance_date=date.today()`. |
| `test_reverse_payment` | `TypeError: finalize_payment() got unexpected kwarg 'amount'` | `finalize_payment(draft_id, approved_amount, method)` — no `admin_phone` param. `reverse_payment(draft_id, admin_phone, reason='')` — now keyed by the payment draft, not a raw transaction id; looks up the canonical transaction via `fazle_payment_drafts.transaction_id`. | Rewrote both calls to the current contracts and rewrote both assertions against `fpe_cash_transactions` (see below). |

**Separate, systemic finding uncovered during investigation:** the whole
file still asserted against the legacy `wbom_cash_transactions` table (5
occurrences) instead of the canonical `fpe_cash_transactions` (Owner
Directive 2026-06-29, `.claude/CLAUDE.md`) — predates that migration.
Fixed all 5: escort-payment PAID step, advance-approval, advance-seeding,
and both sides of the reversal check. Two required matching on
`employee_phone` rather than `employee_id`, since
`fpe_cash_transactions.employee_id` FKs to `fpe_employees(id)` — a
distinct ID space from `wbom_employees.employee_id` (confirmed via
`\d fpe_cash_transactions` on the disposable test DB).

**CONFIRMED REAL PRODUCTION BUG, NOT FIXED (payroll formula work
explicitly out of scope this session):** `compute_run()`'s advances query
(`modules/payroll/__init__.py` ~line 152) filters
`fpe_cash_transactions WHERE employee_id=$1` using the **WBOM**
`employee_id` passed into `compute_run`, but that column's FK target is
`fpe_employees(id)` — confirmed via schema inspection
(`fpe_cash_transactions_employee_id_fkey FOREIGN KEY (employee_id)
REFERENCES fpe_employees(id)`). `mark_paid()` in the *same file* resolves
this correctly, first looking up `fpe_employees WHERE wbom_employee_id=$1`
before using its `id` (see `_transition()`'s fail-closed check and
`_post_payroll_ledger_entry()`) — the advances query does not do this
resolution step. **Practical impact: real advances are silently never
deducted from payroll unless `fpe_employees.id` happens to numerically
coincide with the WBOM `employee_id`**, which is not guaranteed and not
the general case (they are independent `SERIAL` sequences).
`test_advance_deducted_in_payroll` was rewritten to seed through a real,
correctly `wbom_employee_id`-linked `fpe_employees` row, with a decoy row
inserted first specifically to rule out an ID-coincidence false pass in a
fresh, truncated test DB (both sequences start at 1) — confirmed this
matters: without the decoy the test passed coincidentally; with it, it
correctly and reproducibly fails with `total_advances=0.0` instead of
`1000.0`. Left intentionally failing with a clear docstring/assertion
message pointing to this record, per "do not update tests merely to force
green results." **Recommend a dedicated Phase 1G.1 (or similar) proposal
to fix `compute_run()`'s advances query to resolve through
`fpe_employees.wbom_employee_id`, matching `mark_paid()`'s existing
pattern — small, well-scoped, but real-money-affecting, so needs its own
Owner-approved proposal rather than a silent fix here.**

**Tests:** `tests/workflows/test_full_workflows.py` → 6/6 (5 pass, 1
intentional documented-bug failure). Broader regression:
`tests/unit/{admin_commands,attendance,attendance_crud,escort_lifecycle,
payment_workflow,payroll,payroll_dry_run,scheduler_payroll_guard,
sprint3a_employee_conversation}.py` — exit code 0 (all green).

**Git:** `1489bbd`, pushed. Test-only change, no restart needed/performed.

---

## Phase 1H — Diagnose `test_escort_payment_flow.py` Hang (2026-07-18)

**Objective:** Determine why `tests/workflows/test_escort_payment_flow.py`
hangs.

**Finding: the hang does not reproduce.** Ran the full file twice, back to
back, against the disposable test Postgres, with `pytest-timeout` active
(`--timeout=30`, matching both `pytest.ini` and `tests/pytest.ini`'s
configured value):
- Run 1: `11 failed, 48 passed in 275.95s (0:04:35)`
- Run 2: `11 failed, 48 passed in 224.93s (0:03:44)`

Both runs produced the *identical* set of 11 failures (see below), no
flakiness, no test hit the 30s per-test timeout ceiling. `pg_stat_activity`
and `pg_locks` were snapshotted mid-run-2: one `active` connection doing a
normal between-test `TRUNCATE`, zero ungranted (waiting) locks — no lock
contention anywhere in the run.

**Most plausible root cause (documented elsewhere in this same session,
not newly hypothesized here):** earlier in this multi-phase session, a
malformed multi-line background-bash invocation (a `> file 2>&1`
redirection followed by `echo`/`tail` on subsequent lines) got flattened
by `eval` into a single line, causing `pytest` to receive bogus extra
positional arguments — which manifested as an apparent hang (pytest
sitting waiting on invalid collection args) rather than a clean error.
Given (a) this is a documented, confirmed artifact from this exact
session's own tooling, (b) two clean, correctly-invoked runs just now
completed deterministically in under 5 minutes each with no lock
contention and no test approaching its timeout ceiling, and (c) none of
the 11 real failures below involve blocking calls, deadlocks, or timeouts
— the "hang" was very likely an invocation artifact, not a product or
test defect.

**Outcome: A (test-only), no code fix required.** No fixture, mock,
cleanup, or production change needed for the hang itself. Documenting the
correct invocation here to prevent recurrence:
```bash
export TEST_DATABASE_URL="postgresql://postgres:${TEST_DB_PASSWORD}@127.0.0.1:5433/fazle_test"
pytest tests/workflows/test_escort_payment_flow.py -v --timeout=30
```
— always as a single-line command when run via a background/non-interactive
shell; never split a redirected command across multiple lines in that
context.

**The 11 real (non-hang) failures, found and reproduced cleanly, are
explicitly OUT OF SCOPE for this phase** (Phase 1H's mandate is the hang
only) and are recorded here for a future dedicated pass:
- `test_step3_escort_active_visible_via_api` — `GET /admin/escort-programs`
  returns 404; no such route exists anywhere in the current codebase
  (route removed/renamed, never replaced under that path).
- `test_step8_cash_transaction_inserted`,
  `test_cash_transaction_links_to_program`,
  `test_duplicate_payment_finalize_not_allowed` — same legacy
  `wbom_cash_transactions` vs. canonical `fpe_cash_transactions` drift
  fixed in Phase 1G, present in this file too, not yet fixed here.
- `test_payroll_run_items_are_complete`, `test_advance_deducted_in_payroll`,
  `test_payroll_transitions_write_approval_log`,
  `test_payroll_transition_api_full_chain` — same confirmed
  `compute_run()` advances-resolution bug and `mark_paid()` fail-closed
  requirement documented in Phase 1G, present in this file's fixtures too.
- `test_two_employees_independent_payrolls` — likely a downstream
  consequence of the same advances/program-linkage issues above, not
  independently root-caused this session.
- `test_duplicate_release_webhook_idempotent`, `test_escort_release_api_full_flow`
  — `POST /escort/release` now returns 409 "Direct release finalization is
  disabled; send an admin [RELEASE CONFIRMED] message" — a deliberate,
  already-documented 2026-06-19 hardening (`modules/admin_commands/__init__.py`),
  not a bug; these two tests simply predate that change.

**Certification:** Phase 1H CLOSED. Hang not reproducible; root cause
identified as an invocation artifact from earlier in this session, not a
product defect. `test_escort_payment_flow.py`'s 11 unrelated failures are
explicitly carried forward as a separate, documented backlog item — not
silently fixed here, and not blocking Phase 1I (none of them are
concurrency-related; the file's own `TestRaceConditions` class — the
closest thing to a Phase 1I precursor — passed cleanly in both runs).


---

## Phase 1I — Escort-Roster Dual-Write Concurrency Resolution (2026-07-18)

**Objective:** Resolve the open concurrency risk involving multiple writers to
`escort_roster_entries`, per
`knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md`.

**Prerequisite re-trace confirmed all documented line references still valid**
against current code: Writer 1 (`modules/escort_lifecycle/__init__.py:359-388`,
atomic `INSERT ... ON CONFLICT (program_id) DO UPDATE`), Writer 2
(`modules/escort_roster/db.py:259` check-then-act SELECT, confirmed
non-transactional — no `conn.transaction()` anywhere in the function), Writer 3
(`modules/escort_roster/db.py::create_roster_entry()` — separate races, out of
scope, matching the diagnostic doc's own framing).

### Reproduction (built before any code change, per the mandatory prerequisite)

Two standalone, git-uncommitted diagnostic tests called the real, unmodified
`sync_program_to_roster()` directly — no shipped code was changed to build
these reproductions:

**1. Check-then-act race** — 10 concurrent calls for the same `program_id`,
no enclosing transaction:
```
REPRO RESULTS: ok=6 timeout=0 error=4
  ERROR: duplicate key value violates unique constraint
         "escort_roster_entries_program_id_key" (Key (program_id)=(1) already exists.)
  [× 4]
FINAL ROW COUNT for program_id=1: 1
```
Confirmed real, fast (not a hang) — 4/10 concurrent calls hit a genuine
UniqueViolationError from the unguarded check-then-act.

**2. Nested-acquire deadlock** — the actual root cause of the "second,
un-root-caused issue underneath" that made the 2026-07-13 refactor attempt
hang even after its check-then-act race was believed fixed. Called
`sync_program_to_roster()` from *inside* an already-held
`pool.acquire() → conn.transaction()` block (exactly matching
`escort_lifecycle.handle_release_event()`'s own wrapper, and exactly what the
reverted refactor did by routing `escort_lifecycle` through this function),
against the same `test_db_pool` (`max_size=3`):
```
POOL: min_size=1 max_size=3
NESTED REPRO: overall_timeout=True ok=0 timeout=0 error=0
```
Genuine ~25-30s hang, reproduced deterministically. Root cause:
`sync_program_to_roster()` never accepted/threaded a `conn` parameter — every
internal `fetch_one()`/`execute()`/`fetch_val()` call independently acquired
a *fresh* connection from the shared pool via `db_conn()`. Under N concurrent
outer transactions each holding 1 of `max_size` connections, the nested
acquire has nothing left to grant — total pool deadlock. `pg_stat_activity`/
`pg_locks` were polled throughout (0 blocked-lock rows — this is pure
connection-pool exhaustion, not a row/table lock).

**A genuinely useful side-finding while building the reproduction:** the
disposable test DB was missing `escort_calculation_config` and
`escort_roster_audit_logs` entirely — both exist in production (confirmed
`escort_calculation_config` live via `GET /api/escort-roster/config`, 30+
seeded destination rows returned) but are defined only in
`migrations/escort_roster_schema.sql`, a separate file never scanned by
`tests/conftest.py`'s `db/migrations/`-only migration runner. This gap meant
*no test in this repository* had ever exercised
`sync_program_to_roster()`'s real pay-calculation branch
(`if start_date and end_date:`) — only its legacy `total_payment` fallback.
Fixed by adding both tables (verbatim column shapes) to
`tests/conftest.py::_BASE_SCHEMA_SQL`.

### Fix

Single change to `modules/escort_roster/db.py::sync_program_to_roster()`:
1. Replaced the SELECT-then-branch with one atomic
   `INSERT ... ON CONFLICT (program_id) DO UPDATE ... RETURNING *, (xmax = 0) AS _inserted`
   — the `xmax = 0` idiom distinguishes insert vs. update for audit-log
   labeling without a second query.
2. Added an optional `conn=None` parameter, threaded through every internal
   DB call (`_get_conveyance_for_destination`, `_get_shift_rate`,
   `_write_shift_log`, `log_audit`, and the upsert itself) — when a caller
   passes its own held connection, this function composes into that
   caller's transaction instead of acquiring a new one; when called
   standalone (all of its current real callers), behavior is unchanged.

**Post-fix re-verification of both reproductions:**
```
# Standalone race, post-fix:
REPRO RESULTS: ok=10 timeout=0 error=0
FINAL ROW COUNT for program_id=1: 1

# Nested-acquire, post-fix (now correctly threading conn=outer_conn):
POOL: min_size=1 max_size=3
NESTED REPRO: overall_timeout=False ok=10 timeout=0 error=0
```
Both fully resolved. The nested-acquire case now completes in ~2.6s versus
the pre-fix ~25-30s hang.

### Decision: Option A (canonical writer unchanged), Writer 2 hardened, no unification

**A material finding that overrides the prompt's own "likely preferred"
default (one shared atomic upsert used by both writers):** Writer 1's
financial fields (`salary`, `conveyance`, `food_bill`, `net_payable`, `total`)
are sourced from the actual `fazle_payment_drafts` row created earlier in the
same release-confirmation flow (`d.gross_amount`, `d.conveyance`,
`d.food_bill`, `d.advance_deduction`, `d.expected_amount`). Writer 2
independently *recomputes* these same fields via its own `calculate_pay()`
call against `escort_calculation_config`'s conveyance/shift-rate table, and
has **no `advance_deduction` concept at all**. Routing Writer 1 through
Writer 2 — even now that both concurrency bugs are fixed — would silently
change which numbers land in `escort_roster_entries` for every real release,
diverging from the actual payment draft/transaction figures. That is an
unauthorized production financial-calculation change, explicitly out of
scope this session ("Do not implement... payroll formula changes").

**Decision:** Writer 1 (`escort_lifecycle`) is **unchanged** and remains the
sole, canonical, financially-authoritative writer for the release-event
path — Option A from the prompt's menu ("Retain atomic lifecycle upsert").
Writer 2 (`sync_program_to_roster()`) is hardened (atomic + conn-safe) for
its **own three other real callers**
(`modules/escort/__init__.py:1363,1373`,
`modules/escort_roster/history_sync.py:726,903`,
`modules/escort_roster/routes.py:730` admin resync API), where both bugs
were already live, unaddressed, and unrelated to the payment-draft-sourcing
concern (those callers don't have a competing payment-draft source of
truth). The "two independent, duplicated write paths" (the Tier-2 cleanup
item the queue doc names as "the actual defect") therefore remains
duplicated by design, now that a concrete, evidence-based reason exists to
keep them separate — recorded here rather than silently left ambiguous.
Writer 3 (`create_roster_entry()`) is untouched, per the original
diagnostic's own scoping — separate bug class (ID-collision,
duplicate-bypass), lower urgency, admin manual-entry tool only.

### Tests

`tests/unit/test_escort_roster_concurrency.py` (new, 6 tests): 10-concurrent
same-program (no error, 1 row), 10-concurrent different-programs (no
cross-contamination), nested-conn 10-way (no deadlock), release-path +
admin-sync running concurrently for the same program (the literal "release +
sync_program_to_roster" scenario from the required test matrix — both
writers, unchanged Writer 1 alongside fixed Writer 2, produce exactly 1
roster row with no hang), idempotent `calc_version` increment on repeat
calls, and transactional rollback composability (an outer transaction
rollback correctly rolls back the roster write when `conn` is threaded
through — proves the fix is genuinely transactional, not just passing an
inert object). All 6 pass.

`tests/unit/test_escort_roster_routes.py::TestEscortRosterSyncStatus` updated
— was asserting the *old* implementation's exact 3-call
SELECT/UPDATE-with-`$18` SQL shape; updated to match the new single
INSERT-with-RETURNING shape while preserving its original intent
(`status='confirmed'` must map to `roster_status='confirmed'`).

**Full regression, zero new failures:**
`tests/unit/{escort_roster_routes,escort_lifecycle,escort_roster_concurrency}.py`
+ `tests/workflows/test_full_workflows.py` → only the already-documented
Phase 1G `compute_run()` advances bug fails (unrelated, pre-existing,
intentional). `tests/workflows/test_escort_payment_flow.py` full run →
identical 11/59 pre-existing failures from Phase 1H (route removal, legacy
`wbom_cash_transactions` drift, the same `compute_run()` bug, one
deliberately-disabled endpoint), confirmed byte-for-byte identical test
names and error messages before and after this change — zero new
regressions anywhere touched by this fix.

### Rollback

Pure application-code change, no schema migration (the `UNIQUE(program_id)`
constraint the `ON CONFLICT` clause targets already existed). `git revert
2d712f9` fully restores the prior (safe-but-duplicated, check-then-act)
behavior with no data-shape implications either direction.

### Production risk

🟢 Low. The changed function's *current* production callers
(`modules/escort/__init__.py`, `modules/escort_roster/history_sync.py`,
`modules/escort_roster/routes.py`) are all called standalone today (no
enclosing transaction), so the `conn=None` default preserves their exact
prior behavior byte-for-byte except for the now-atomic upsert (strictly
safer under concurrency than before, with identical steady-state output for
non-concurrent calls — confirmed via the idempotency test).
`modules/escort_lifecycle` (the financially-authoritative release path) is
completely untouched by this commit.

**Git:** `2d712f9`, pushed. **Restart:** not required for this change's own
correctness (pure Python, no schema/env change) but performed anyway per
this phase's deployment-safety requirement — see the deployment record
below.


---

## Payroll Advance Deduction Employee-ID Resolution Fix (2026-07-18)

**Objective:** Correct `compute_run()` so advance deductions resolve through the
correct FPE employee ID, per the Owner-authorized narrow scope (identity
resolution only — no formula redesign, no unrelated payment-flow cleanup).

### Confirmed defect

`modules/payroll/__init__.py::compute_run()`'s advance-deduction query filtered
`fpe_cash_transactions.employee_id` directly against the WBOM `employee_id`
parameter passed into `compute_run`. Schema inspection confirms
`fpe_cash_transactions_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES
fpe_employees(id)` — a different, independently-sequenced ID space from
`wbom_employees.employee_id`. `_transition()`'s "paid" fail-closed check
elsewhere in the same file already resolves this correctly via
`fpe_employees WHERE wbom_employee_id=$1 AND status='active'`; the advances
query never did.

### Production data audit (read-only, before any code change)

- `GET /api/fpe/transactions?txn_category=advance`: **194 advance transactions
  all-time**, ৳153,408 total, spanning 2026-01-22 to 2026-07-09, across ~70
  distinct FPE employee IDs.
- Sampled 15 real advance-bearing employees via `GET /api/fpe/employees/{id}`:
  **15/15 (100%) had `id != wbom_employee_id`** when linked at all; 6/15 (40%)
  had `wbom_employee_id = NULL` (no link — correctly resolves to $0 under both
  old and new code; a separate, unrelated unresolved-linkage gap, not this
  fix's concern).
- `GET /payroll/runs?period=2026-05|06|07`: **534 sampled runs, 100%
  `status='draft'`, 0% with `total_programs>0`, 0% with `total_advances>0`**
  — consistent with `CANONICAL_BUSINESS_RULES.md`'s already-documented fact
  (703 rows stuck in draft as of 2026-07-09): **this pipeline has never paid
  anyone.** Confirmed: **zero realized financial exposure** — the bug's
  impact was entirely latent/forward-looking, never historical, since no run
  has ever left `draft`.

### Same-bug-class functions found, NOT fixed (explicitly out of this fix's scope)

- **`modules/payment_workflow/__init__.py::create_escort_payment_draft()`
  — HIGH severity, urgent follow-up recommended.** Per
  `CANONICAL_BUSINESS_RULES.md`: "the function that actually computes every
  real escort payment draft on release." Its advance subtraction
  (`net_payable = gross_amount − food_bill + conveyance − advances`) has the
  *identical* WBOM-id-against-`fpe_cash_transactions` bug, but unlike
  `compute_run()` this is a **live, real-money path** — guards may be
  currently overpaid on every release because their prior advances aren't
  being deducted.
- `modules/payment_workflow/__init__.py::create_advance_request_draft()` —
  same bug in a "paid this month" figure shown to the approving admin, but
  display-only (doesn't gate or cap the transaction) — lower urgency.
- `modules/payroll_logic/__init__.py::get_payroll_summary()` — 3 queries
  (this month's payments, last payment, all-time total) feeding
  customer-facing WhatsApp "কত টাকা পেয়েছি" replies — same bug, misleads
  employees about their own payment history.
- `modules/admin_employees/__init__.py::get_employee()` — cosmetic
  `txn_count` in an admin detail panel, same bug, low stakes.
- `modules/social_auto_reply/employee_lookup.py::get_payment_history()` /
  `get_total_paid()` — same bug present but **dead code, zero callers
  anywhere in the codebase**, no live impact.

**These are recorded here for a separate, Owner-approved follow-up — not
fixed in this pass, per the explicit "no unrelated payment-flow cleanup"
scope boundary.** `create_escort_payment_draft()` is the clear priority given
it's live money moving today.

### Fix

Single change to `compute_run()`: resolve the linked, active `fpe_employees`
row via `wbom_employee_id` first (`SELECT id FROM fpe_employees WHERE
wbom_employee_id=$1 AND status='active'`, the exact pattern already used by
`_transition()`), then query `fpe_cash_transactions` by the resolved id. No
FPE link → advances = 0, the run still computes successfully (not fail
closed — a missing link is a data-completeness fact, not an error condition,
per Owner-defined policy for this fix). Everything else in the function is
byte-for-byte unchanged: signature, period bounds, program-days aggregation,
`prorate_basic` logic, other-deductions hook, status/date filters on the
advances query itself, output schema, and audit-log writes.

### Tests

`tests/unit/test_payroll_advance_identity.py` (new, 9 tests): cross-system ID
mapping (the key regression test — WBOM id deliberately desynchronized from
FPE id via a decoy-row technique, matching the empirically-confirmed real
production pattern), coincidental-match safety net, another employee's
advance never leaking in, no-FPE-link → zero deduction (run still succeeds),
inactive-link treated as no-link, multiple-advance correct summing, status
filtering (`final` counted, `pending`/`reversed` excluded — confirmed the
existing filter was already correct, only the ID was wrong), period
boundaries (inclusive start/end via `BETWEEN`, exclusive outside), and
idempotency (repeat calls return the same run, no duplicate rows).

Fixed two pre-existing tests whose fixtures never actually exercised
cross-system resolution (both passed only via `id`/`wbom_employee_id`
coincidence in a fresh truncated test DB — a "silently masked by ID
coincidence" pattern, the same class of test-hygiene issue found and fixed
in Phase 1G):
- `tests/unit/test_payroll.py::test_advances_deducted_from_net` — its
  `seed_fpe_employee` fixture sets `canonical_employee_id`, not
  `wbom_employee_id` (the real bridge column) — a pre-existing fixture gap,
  first noted in Phase 1G's investigation but not touched then since it
  wasn't blocking anything at the time. Fixed locally in this one test via
  an explicit `UPDATE`, not by modifying the shared fixture (used by 5 other
  test files for unrelated purposes — changing its behavior globally was
  judged out of scope and riskier than a one-line local fix).
- `tests/workflows/test_full_workflows.py::test_advance_deducted_in_payroll`
  — was already correctly seeded in Phase 1G specifically to *reveal* this
  bug (deliberately not coincidentally matched); now genuinely passes
  instead of being an intentionally-documented failure.

**Full regression, zero new failures:**
`tests/unit/test_payroll.py` 12/12. `tests/workflows/test_full_workflows.py`
6/6 (the previously-documented failure is now a genuine pass — no
independently-explained failures remain in this file). Full
`tests/workflows/test_escort_payment_flow.py` run (clean, solo — a
concurrent-run collision against the shared disposable test DB on the first
attempt produced spurious schema-corruption errors, re-ran cleanly): the
same **identical 11/59 pre-existing failures**, confirmed by name and error
message, before and after this change. None of the 11 are advance-identity
issues — 3 are legacy `wbom_cash_transactions`-table drift (this file's own
`test_advance_deducted_in_payroll` and `test_payroll_run_items_are_complete`
still seed via the legacy table, unrelated to the ID-resolution bug just
fixed and correctly left untouched per "do not rewrite unrelated
legacy-table tests"), 1 is unrelated test-employee salary-data design
(`test_two_employees_independent_payrolls`), the rest are route removal,
missing-FPE-link-for-`mark_paid`, or deliberately-disabled-endpoint issues
already documented in Phase 1H.

### Historical correction decision

**No correction performed or required.** Zero payroll runs have ever left
`draft` status (confirmed above) — there is no paid or approved run
containing an incorrect deduction to fix. Existing draft runs will simply
compute correctly the next time anyone explicitly recomputes them (this fix
doesn't retroactively touch existing rows — `compute_run()`'s own idempotency
returns the existing draft as-is when one is already present for that
employee/period, per its documented behavior).

### Production verification

Used a real, previously-uncomputed employee/period combination (wbom
employee 151 / FPE employee 177 "Nazmul", period 2026-01, which genuinely had
two real advance transactions totaling ৳3,500 and no prior payroll run) —
not fake data, per the explicit "do not create fake payroll transactions"
constraint. `POST /payroll/compute` created a new, real, correctly-computed
draft run (`run_id=714`): `"total_advances": 3500.0`, exactly matching the
real underlying transactions (before this fix, this would have returned
`0.0`, since the query would have searched
`fpe_cash_transactions WHERE employee_id=151`, but the real rows are keyed by
`employee_id=177`). A second identical call correctly hit the idempotency
path (`"already_exists": true`, same `run_id=714`, no duplicate). Health
`ok` before and after; no new errors in logs; this new draft run is left in
place as a legitimate, now-correct record (not cleaned up — it reflects real
underlying data, unlike the disposable-test-DB material which was always
synthetic).

### Git and deployment

Commit `c2c4256` (fix + tests combined — tightly coupled, both landed
together per the explicit "two commits are acceptable" allowance), pushed.
`restart_gate.sh --restart`: all 9 checks passed, health `ok` before and
after, PID before 1783977 → PID after 2848578.

### Rollback

`git revert c2c4256`, push, `restart_gate.sh --restart`. No schema change
(nothing to roll back at the DB level). No production-data rollback needed —
`run_id=714` is a correct draft record, not something requiring reversal;
if the Owner nonetheless wants it removed, `cancel_run(714, ...)` is the
existing, safe, already-approved transition for that.

### Remaining risks

- `create_escort_payment_draft()`'s identical, live-money-affecting bug is
  the clear next priority (see "same-bug-class functions" above) — needs its
  own Owner-approved proposal.
- `get_payroll_summary()`'s bug means employees asking WhatsApp "কত টাকা
  পেয়েছি" may be told incorrect payment history today — customer-facing,
  worth prioritizing alongside the above.
- The 40% of sampled advance-bearing employees with no `wbom_employee_id`
  link at all is a separate, unresolved-employee-linkage gap (distinct bug
  class from this fix) — those employees' advances will correctly show as
  $0 under the now-fixed query, but that $0 is only "correct" in the sense
  of "no crash/no wrong number," not in the sense of "the real advance was
  accounted for." A dedicated employee-linkage cleanup would close this.
- The 11 pre-existing `test_escort_payment_flow.py` failures remain open,
  unrelated, already documented in Phase 1H.


---

## Phase 2A — Complete Remaining Money Correctness (2026-07-18)

**Objective:** search the whole repository for the same WBOM-employee_id-used-
directly-against-fpe_cash_transactions bug fixed in `compute_run()`, and fix
every live-money and customer-visible instance.

### Audit table

| Function | Correct before | Bug | Live money | Customer visible | Action |
|---|---|---|---|---|---|
| `modules/payroll/__init__.py::compute_run()` | No | Yes | No (dormant pipeline, see prior entry) | No | Fixed earlier this session |
| `modules/payment_workflow/__init__.py::create_escort_payment_draft()` | No | Yes | **Yes — computes every real escort payment draft on release** | Indirectly (guard's payout amount) | **Fixed** |
| `modules/payment_workflow/__init__.py::create_advance_request_draft()` | No | Yes | No (display-only, doesn't gate the transaction) | Admin-facing only | **Fixed** |
| `modules/payroll_logic/__init__.py::get_payroll_summary()` | No | Yes | No (read-only reply) | **Yes — WhatsApp "কত টাকা পেয়েছি" replies** | **Fixed** (also exposed 2 separate pre-existing KeyError/wrong-column bugs, fixed in the same change) |
| `modules/admin_employees/__init__.py::get_employee()` | No | Yes | No (cosmetic count) | Admin-facing only | **Fixed** |
| `modules/social_auto_reply/employee_lookup.py::get_payment_history()`/`get_total_paid()` | No | Yes | No — **dead code, zero callers anywhere** (confirmed via repo-wide search) | No | Not fixed — no live impact, not worth the risk of touching unused code |

### Fix

Same pattern as `compute_run()`: resolve `SELECT id FROM fpe_employees WHERE
wbom_employee_id=$1 AND status='active'` first, use the resolved id in the
`fpe_cash_transactions` query, no link → the safe default for that function's
own contract (zero deduction / zero payment history / draft still created,
never fail closed). No formula changes, no filter changes beyond the
employee-id parameter itself.

**Bonus fix surfaced by this work:** `get_payroll_summary()`'s `last_txn`
row access used `last_txn["transaction_date"]` (real column: `txn_date`) and
`last_txn.get("payment_method")` (real column: `payout_method`) — both
silently never triggered before because `last_txn` was always `None` under
the old buggy query. Fixing the identity bug would have turned this into a
500 (`KeyError`) for every newly-resolving employee if left alone — fixed in
the same commit since it's a direct, unavoidable consequence, not scope
creep.

### Tests

`tests/unit/test_money_correctness_identity.py` (new, 7 tests). Fixed 2
existing `tests/unit/test_payment_workflow.py` tests whose `seed_fpe_employee`
fixtures never set `wbom_employee_id` (same masking pattern as before).
Full regression: `test_payment_workflow.py` 23/23,
`test_full_workflows.py`/`test_payroll.py`/`test_payroll_advance_identity.py`
all green, `test_escort_payment_flow.py` identical 11/59 pre-existing
failures, zero new regressions.

### Production verification

`create_escort_payment_draft()`'s identity-resolution SQL is byte-for-byte
identical to the pattern already live-verified in `compute_run()` (see the
prior "Payroll Advance Deduction Employee-ID Resolution Fix" entry:
`total_advances: 3500.0` for a real employee). Deliberately **did not**
trigger a live call to `create_escort_payment_draft()` for verification,
since unlike `compute_run()`'s draft-run artifact (an expected, normal
record), a payment draft appears in an admin's approval queue and creating
one outside a real release event would be a confusing, avoidable side
effect for a human reviewer. Relying instead on: identical SQL pattern
already proven live, full new unit coverage, and zero regressions across
the entire test surface. Health `ok` before/after; the one error log entry
found post-restart (`admin_employees/__init__.py:348, update_employee`) is
confirmed stale (line 4676 of 5718 in the error log, well before this
deployment) and in a function never touched by this change.

**Git:** `69897d7`, pushed. Restart gate: 9/9 checks passed.


---

## Phase 2B — Writer 3 (`create_roster_entry()`) Concurrency Resolution (2026-07-18)

**Objective:** resolve the last unfixed escort-roster dual-write concurrency
risk, per the diagnostic's own scoping (`escort_dualwrite_concurrency_debug_2026-07-17.md`:
"Writer 3 was not part of the 2026-07-13 refactor attempt at all and has its
own, separate race conditions that any future consolidation effort needs to
account for independently").

### Re-trace

Confirmed both documented races still present, current code:
`modules/escort_roster/db.py::create_roster_entry()` (line 793 → shifted
slightly from the doc's "797" after intervening edits, same logic) — (1)
`max_pid = SELECT GREATEST(MAX(...))+1` then a separate `INSERT`; (2)
`_find_duplicate_roster_entry()` check then a separate `INSERT`, with no DB
constraint backing the vessel+lighter+date(+escort) uniqueness rule.

### Reproduction (before any code change)

Two diagnostic tests, git-uncommitted, calling the real unmodified function:
- **Program_id collision:** 10 concurrent calls with distinct vessel data
  (duplicate check short-circuited via `start_date=None`) → **9/10 hit a live
  `UniqueViolationError`** on the identical computed `program_id=900001`.
  Confirmed real, not a hang.
- **Duplicate bypass:** could not be cleanly isolated from the program_id
  race in this reproduction shape (both races share the same "10 calls
  computing the same max_pid" setup, so the ID-uniqueness constraint
  coincidentally limited successful inserts to 1 regardless of the
  duplicate-check race) — the structural TOCTOU gap in
  `_find_duplicate_roster_entry()` is confirmed by code inspection (no DB
  constraint backs it) even though this specific reproduction shape
  couldn't isolate it independently. Not pursued further since the chosen
  fix (full serialization) eliminates both races by construction regardless
  of the exact interaction.

**Side-finding while building the reproduction:** `tests/conftest.py`'s
`escort_roster_entries` stub was missing the `notes` column (present in the
canonical schema and confirmed live in production via
`GET /api/escort-roster/{id}`) — an omission from this session's own Phase
1I stub transcription. Fixed.

### Fix

Wrapped the whole `create_roster_entry()` body (max_pid generation,
duplicate check, INSERT, audit log, final SELECT) in one transaction
serialized by `pg_advisory_xact_lock(hashtext('escort_roster.create_roster_entry'))`
— Option D (advisory lock) from the established decision menu. Smallest
change: no schema change, no new unique constraint, no restructuring of the
duplicate-check business logic itself. `create_roster_entry()` is a
low-frequency, admin-only manual "add roster entry" tool — full
serialization has no meaningful throughput cost and is a clean, low-risk
trade-off. Threaded `conn` through `_find_duplicate_roster_entry()`
(previously had no `conn` parameter at all) and the other internal calls
(`_get_conveyance_for_destination`, `_get_shift_rate`, `execute`,
`log_audit`, the final `fetch_one`) so every statement in the sequence
shares the same connection/lock scope.

**Post-fix re-verification of the same reproduction:** 10/10 concurrent
distinct-vessel calls now succeed with 10 distinct `program_id`s (0 errors,
down from 9/10). 10/10 concurrent identical-vessel calls now correctly
produce exactly 1 row, with the other 9 cleanly blocked by the existing
`ValueError` (not silently duplicated).

### Tests

`tests/unit/test_roster_writer3_concurrency.py` (new, 4 tests): 10-concurrent
distinct-vessel no-collision, 10-concurrent identical-vessel exactly-one-row,
sequential no-deadlock/no-hang, and rollback-leaves-no-partial-row (proves
the sequence is genuinely one transaction, not just passing a `conn` object
inertly). **No existing test anywhere in the repository previously exercised
`create_roster_entry()` at all** (confirmed via repo-wide search) — this also
closes a real test-coverage gap, not just a concurrency bug.

**Full regression, zero new failures:**
`tests/unit/{escort_roster_routes,escort_lifecycle,escort_roster_concurrency,
roster_writer3_concurrency}.py` 45/45 combined.
`tests/workflows/test_escort_payment_flow.py`: identical 11/59 pre-existing
failures, confirmed byte-for-byte, before and after this change.

### Production verification

Created two clearly-marked synthetic rows via `POST /api/escort-roster`
(`mother_vessel: "PHASE2B TEST VERIFY"` / `"PHASE2B DUPE TEST"`, `notes`
explicitly stating "safe to delete"): first confirmed a normal create
succeeds (`program_id=957`); second confirmed the duplicate-block path works
live (`program_id=958` created, then an identical repeat correctly returned
`409 Duplicate roster blocked`). Both rows deleted via
`DELETE /api/escort-roster/{program_id}` immediately after — confirmed
gone (`404`) — since unlike the payroll-run verification in Phase 2A/earlier
(a legitimate artifact built on real underlying data), these were fully
synthetic admin-created rows with no real underlying business data, so
cleanup was both possible and appropriate. Health `ok` before/after, no new
errors.

**Git:** `5bab26a`, pushed. Restart gate: 9/9 checks passed.

### Rollback

`git revert 5bab26a`. No schema change (advisory locks are session/
transaction-scoped, nothing persisted). No production-data rollback needed
(verification rows already cleaned up).

**All three escort-roster writers are now resolved:** Writer 1
(`escort_lifecycle`, unchanged, canonical) — Writer 2
(`sync_program_to_roster`, Phase 1I) — Writer 3 (`create_roster_entry`,
this phase).


---

## Phase 2C — Escort Payment Workflow Failure Classification (2026-07-18)

**Objective:** classify all 11 pre-existing `test_escort_payment_flow.py`
failures; fix only genuine production bugs or stale tests whose production
contract has intentionally changed; leave documented-historical ones alone.

| Test | Root Cause | Production Bug | Test Drift | Legacy | Action |
|---|---|---|---|---|---|
| `test_step3_escort_active_visible_via_api` | `GET /admin/escort-programs` route removed, no replacement | No | No | Yes | Not fixed |
| `test_step8_cash_transaction_inserted` | Legacy `wbom_cash_transactions` | No | No | Yes | Not fixed |
| `test_cash_transaction_links_to_program` | Legacy `wbom_cash_transactions` | No | No | Yes | Not fixed |
| `test_payroll_run_items_are_complete` | Legacy `wbom_cash_transactions` | No | No | Yes | Not fixed |
| `test_advance_deducted_in_payroll` (this file) | Legacy `wbom_cash_transactions` | No | No | Yes | Not fixed |
| `test_duplicate_release_webhook_idempotent` | `POST /escort/release` intentionally hardened 2026-06-19 | No | No | Yes | Not fixed |
| `test_duplicate_payment_finalize_not_allowed` | Legacy `wbom_cash_transactions` count check | No | No | Yes | Not fixed |
| `test_escort_release_api_full_flow` | Same disabled-endpoint hardening | No | No | Yes | Not fixed |
| `test_payroll_transitions_write_approval_log` | Missing linked active `fpe_employees` row (2026-07-15 `mark_paid()` fail-closed fix) | No | **Yes** | No | **Fixed** |
| `test_two_employees_independent_payrolls` | Unrelated test-data flaw: emp2's `basic_salary` exceeded emp1's basic+program total | No | **Yes** | No | **Fixed** |
| `test_payroll_transition_api_full_chain` | Same missing-FPE-link issue | No | **Yes** | No | **Fixed** |

**Result:** 3/11 fixed. Suite now 51 passed / 8 failed (was 48/11). The
remaining 8 are the same, already-documented (Phase 1H) legacy-table and
disabled-endpoint issues — none are production bugs, none are blocking.

**Git:** `7b9fe41`, pushed. Test-only change — no restart required or
performed.


---

## Phase 2D — `payroll.html` Wrapper Migration (2026-07-18)

**Objective:** complete the deferred migration of `payroll.html`'s `api()`/
`adminApi()` wrappers onto `shared-auth-fetch.js`, previously deferred
because "dual-prefix routing and auto-serialize behavior have no equivalent
in shared-auth-fetch.js and need a design decision, not a mechanical swap."

### Audit — behavioral differences found

| Aspect | `api()` | `adminApi()` | `shared-auth-fetch.js` |
|---|---|---|---|
| URL prefix | Fixed `/api/fpe` | Dual: `/users`\*/`/audit`\* → `/admin`, else `/api/admin` | None (caller passes full path) |
| Body serialization | Never (caller must pre-stringify) | Conditional (only if not already a string) | `sendJson()`: always unconditional |
| Cache control | None | `Cache-Control`/`Pragma: no-cache` + `cache:'no-store'` | None built-in, but passthrough via `opts` |
| Error extraction | Raw, unparsed response text | `JSON.parse` → `.detail \|\| .message`, no array-of-msg handling | `readError()`: handles both string `detail` and array-of-`{msg}` FastAPI validation shape |

**Confirmed via a full call-site audit (48 `api()` calls, 11 `adminApi()`
calls):** every `api()` call site that passes a body pre-stringifies it
manually. Delegating to `FazleAuthFetch.sendJson()` (unconditional
`JSON.stringify`) would have double-encoded these bodies — a real breaking
bug a mechanical swap would have introduced.

### Decision: no extension needed to `shared-auth-fetch.js`

Both wrappers now delegate to the existing **low-level** primitives
(`FazleAuthFetch.fetch()` + `readError()`), not the higher-level
`getJson`/`postJson` (which force serialization). This preserves each
wrapper's own external contract exactly — **zero call sites needed to
change** — while gaining the shared header-building and, as an intentional
improvement, correct FastAPI error-shape parsing (previously `api()` showed
raw unparsed JSON text as the error message; `adminApi()` didn't handle the
validation-error array shape at all).

### Verification

Since this repo serves static files directly from the working tree (no
build step, no restart needed), the edit was live the instant it was saved.
Verified via Playwright directly against the running production server
(`page.evaluate()` calling `api()`/`adminApi()` in the real browser
context, matching the Phase 1E.1-established technique): `api()`'s
`GET /health`, a `GET` with query params, and the 404 error path (confirmed
`"Employee not found"`, not raw JSON — proving the improved error
extraction works); `adminApi()`'s `/api/admin` branch and both `/users` and
`/audit` special-case `/admin` branches. All 6 checks passed, zero
unexpected console errors.

New `tests/e2e/test_payroll_wrappers.py` (6 tests) makes this permanent.

**Git:** `0c8afb5`, pushed. No restart required or performed (static-file
change, confirmed live via direct checks before and after commit).

