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
FK: `final_transaction_id` → `wbom_cash_transactions.transaction_id`.

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
**DB tables required:** `fazle_payment_drafts` ✅, `wbom_cash_transactions` ✅, `fazle_payment_correction_log` ✅ (migration `008_payment_correction.sql`)

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
