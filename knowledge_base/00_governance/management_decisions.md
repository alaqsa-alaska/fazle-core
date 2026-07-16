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
