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

