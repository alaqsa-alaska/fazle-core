# Master Execution Plan v2 — Organizational Brain Validation & Alignment
**Document ID:** MEP-v2
**Created:** 2026-06-23
**Last Updated:** 2026-06-23 (post-Session 4 rewrite)
**Authority:** Management — Al-Aqsa Security & Logistics Services Ltd.
**Goal:** Knowledge Base → Organizational Brain → Production AI
**Status:** ACTIVE — Phase 1 authorized pending management sign-off; Phases 2–6 queued

---

## How to Use This Document

This is the canonical execution roadmap for every future AI agent session. Before starting any work:

1. Read this document completely
2. Read `management_decisions.md` — confirm phase authorization exists
3. Read `kb_enrichment_plan_v2.md` — task templates and detailed content specs
4. Read `organizational_brain_gap_report.md` — gap categories and business impact scores
5. Run the pre-conditions check in the target phase
6. Only then begin execution

**Cross-reference authority:** When this document conflicts with `kb_enrichment_plan_v2.md`, this document takes precedence for phase sequencing. `kb_enrichment_plan_v2.md` takes precedence for article content templates.

---

## Current State Baseline (Verified 2026-06-23 — Post-Session 4)

All facts below verified by direct code and file reading — not from session memory.

### Production Infrastructure

| Component | Status | Evidence |
|---|---|---|
| Hybrid RAG | ✅ LIVE | `HYBRID_SEARCH_ENABLED=true` in `.env`; `_hybrid_search()` at line 837 of `modules/rag/__init__.py`; RRF formula at line 792 |
| RRF Fusion | ✅ CONFIRMED | `rrf_score = 1/(60 + rank_bm25) + 1/(60 + rank_vector)` — live production formula |
| Qdrant Vector DB | ✅ ACTIVE | Server mode at `172.20.0.2:6333`; collection `fazle_rag_chunks`; 251 indexed points |
| MiniLM Encoder | ✅ ACTIVE | `paraphrase-multilingual-MiniLM-L12-v2`, 384 dims, LRU cache 2000 entries, 27 seed queries |
| Prompt Builder | ✅ STRUCTURED | `POLICY_VERSION="structured_v2"`, 6-section format (`shared/reply_policy.py` line 29) |
| Auto Reply | ⚠️ DRAFT MODE | `AUTO_REPLY_ENABLED=false` — all AI replies go to admin queue before sending |
| Draft Quality Gate | ✅ ACTIVE | `DRAFT_QUALITY_GATE=true`; 4 criteria; documented in `automation_pipeline.md` (Wave-2B) |
| Scheduler | ✅ ACTIVE | `SCHEDULER_ENABLED=true`; 15 jobs; `SCHEDULER_TIMEZONE=Asia/Dhaka` |
| Outbound Queue | ✅ ACTIVE | `OUTBOUND_ENABLED=true` |

### Knowledge Base State

| Metric | Value | Source |
|---|---|---|
| KB version | v1.0 CONTROLLED FREEZE | `management_decisions.md` GOV-01 |
| Weighted module coverage | ~25% | `module_alignment_report.md` (52+ modules audited, post-Session 4) |
| Brain readiness score | **68% CONDITIONAL** | `session4_audit_report_2026_06_23.md` Section 5 (post-conflict-resolution) |
| Active conflicts | **0** | All 6 resolved: CR-01–CR-06 (`management_decisions.md`) |
| P1 stub articles remaining | **3** | `system_prompt.md` (13 lines), `workflow_engine.md` (13 lines), `visibility_rules.md` (12 lines) |
| P1 new articles missing | **2** | `escort_roster_system.md` (0 lines), `runtime_gateway_flags.md` (0 lines) |
| Last commit | `483ddd2` | 49 files, Session 4 conflict resolutions + Wave-2 enrichment |

### What Is Already Done (Do Not Re-Do)

| Item | Done In | Status |
|---|---|---|
| Hybrid RAG live (HYBRID_SEARCH_ENABLED=true) | Session 2 (Phase 1) | ✅ COMPLETE |
| Structured prompt builder structured_v2 | Session 3 (Phase 4 Step 3) | ✅ COMPLETE |
| Phase 4 production code (5 files) | Session 3 | ✅ COMMITTED `4c2b6f2` |
| Wave-2 KB enrichment (23 articles + 2 new) | Session 3 + Session 4 | ✅ COMMITTED `4c2b6f2` |
| `hybrid_search.md` full rewrite (P1-A) | Session 4 CR-02 | ✅ 122 lines, certified 2026-06-23 |
| Draft quality gate documentation | Wave-2B in `automation_pipeline.md` | ✅ 262 lines total (gate section = lines 169–208) |
| CONFLICT-1: Draft TTL = 24h | Session 4 CR-01 | ✅ `automation_pipeline.md` updated |
| CONFLICT-2: RRF is authoritative | Session 4 CR-02 | ✅ `hybrid_search.md` rewritten |
| CONFLICT-3: BR-25 on all channels | Session 4 CR-03 | ✅ Code + KB updated |
| CONFLICT-4: Dual draft TTL documented | Session 4 CR-04 | ✅ `automation_pipeline.md` updated |
| CONFLICT-5: Escort rate ৳400/day | Session 4 CR-05 | ✅ `payroll/__init__.py` + `payment_workflow/__init__.py` updated |
| CONFLICT-6: gpt-4.1 model name | Session 4 CR-06 | ✅ `automation_pipeline.md` updated |
| Governance trail (management_decisions.md) | Session 4 | ✅ CR-01–CR-06 documented |
| `module_alignment_report.md` | Phase 4 Step 5 + Extension 2026-06-23 | ✅ 52+ modules audited |
| 14/14 `test_reply_policy.py` tests | Session 4 | ✅ Passing |

### Module Coverage Summary

From `module_alignment_report.md` — Phase 4 Step 5 Extended Audit (2026-06-23):

| Priority | Modules | Coverage | Count | Action |
|---|---|---|---|---|
| P0 — RESOLVED | rag (Hybrid RAG), payroll | was 5–25% → fixed by Session 4 | 2 | ✅ Done |
| P1 — BLOCKING | system_prompt, workflow_engine, escort_roster, runtime_gateway, payroll_logic, recruitment_flow, admin_transactions, queue_arbiter, bridge_orchestrator | 0–40% | 9 | Phase 1 |
| P2 — HIGH | escort, payment_workflow, bridge_poller, fazle_payroll_engine, drafts, wa_chat_frontend, admin_employees, reports, outbound, employee_verification, contact_sync, ocr_processor, phone_normalizer, self_heal, visibility_rules | 0–40% | 15 | Phase 4 |
| P3 — MEDIUM | identity_brain (65%), attendance (50%), admin_commands (50%), rbac (60%), intent (20%), reviewed_reply_memory (5%), utility modules (14) | 5–65% | 20+ | Phase 4 |

---

## Target State

```
KB v2 Certified
  ├── Coverage        ≥ 90%  (from ~25% now)
  ├── Brain Readiness ≥ 80%  (GOOD — from 68% now)
  ├── Conflicts       = 0    (currently 0 — preserve)
  ├── Critical Gaps   = 0    (currently: 3 stubs + 9 P1 modules undocumented)
  └── PKCA / PKMA / PKVC v2 all pass
```

---

## Phase Map

| Phase | Name | Type | Owner | Prerequisite |
|---|---|---|---|---|
| **PHASE 1** | Wave-3 P1 KB Enrichment | KB only | AI Agent | Management authorization |
| **PHASE 2** | Identity Brain Integration | KB only | AI Agent | Phase 1 complete |
| **PHASE 3** | Visibility Engine | Code + KB | Developer + Management | Phase 2 complete + code approval |
| **PHASE 4** | Coverage 90%+ (P2/P3) | KB only | AI Agent | Phase 1 complete |
| **PHASE 5** | Knowledge Freeze v2 (Validation) | Read-only audit | AI Agent | Phase 4 complete |
| **PHASE 6** | Conditional Refactoring | Code only | Developer | Phase 5 certified + per-module approval |

---

## PHASE 1 — Wave-3 P1 KB Enrichment

**Objective:** Enrich all P1 stub articles and create all P1 missing articles. Bring the 9 highest-risk modules from 0–40% KB coverage to ≥ 80%.
**Type:** KB-only — NO production code changes in this phase
**Owner:** AI Agent
**Management Approval Required:** YES — Wave-3 authorization entry in `management_decisions.md` before starting
**Estimated Output:** 5 articles enriched/created; ~450 new KB lines

---

### Pre-conditions Check (Run Before Starting Phase 1)

```bash
# 1. Confirm infrastructure is live
grep HYBRID_SEARCH_ENABLED /home/azim/core/.env      # must be: true
grep POLICY_VERSION /home/azim/core/shared/reply_policy.py  # must be: structured_v2

# 2. Confirm stub sizes (these should be enriched)
wc -l /home/azim/core/knowledge_base/06_developer_system/system_prompt.md    # expect: 13
wc -l /home/azim/core/knowledge_base/06_developer_system/workflow_engine.md   # expect: 13
wc -l /home/azim/core/knowledge_base/06_developer_system/visibility_rules.md  # expect: 12

# 3. Confirm missing articles (these should not exist yet)
ls /home/azim/core/knowledge_base/06_developer_system/escort_roster_system.md  # expect: not found
ls /home/azim/core/knowledge_base/06_developer_system/runtime_gateway_flags.md # expect: not found

# 4. Confirm authorization
grep "Wave-3" /home/azim/core/knowledge_base/00_governance/management_decisions.md  # must exist

# 5. Confirm tests still pass
cd /home/azim/core && python3 -m pytest tests/unit/test_reply_policy.py -q   # expect: 14 passed
```

---

### Phase 1 Task List

Tasks are ordered by `kb_enrichment_plan_v2.md` priority. **P1-A is already DONE.** Start from P1-B.

---

#### TASK 1-B: Enrich `system_prompt.md` (13 lines → full article)

**Enrichment Plan ref:** P1-B in `kb_enrichment_plan_v2.md`
**Source to read FIRST:** `shared/reply_policy.py` (full file — read completely before writing)
**Current state:** 13-line stub. Says only "no emoji, formal Bangla." Phase 4 structured_v2 is entirely undocumented.

**Required content** (read from actual source, do not reconstruct from memory):
- `POLICY_VERSION = "structured_v2"` — what version means, when it changes
- Two builders: `build_whatsapp_recruitment_policy()` vs `build_whatsapp_reply_policy()` — when each is called
- 6-section format: Bengali headers, purpose of each section, when a section is suppressed (empty history → no Conversation section)
  - `## ভূমিকা (Role)` — identity + role-specific tone
  - `## ব্যবসায়িক নিয়ম (Business Rules)` — non-negotiable output constraints
  - `## কার্যপ্রবাহ (Workflow)` — intent-specific action instruction
  - `## জ্ঞান ও তথ্য (Knowledge)` — KB/RAG context + contact data
  - `## কথোপকথন (Conversation)` — history, truncated at 1200 chars
  - `## প্রশ্ন (User Question)` — inbound message, truncated at 400 (general) / 500 (recruitment) chars
- `ROLE_PROMPTS` dict: list all 7 roles with their Bengali tone instruction (read from file)
- `INTENT_HINTS` dict: list all 12 intent categories (read from file)
- Recruitment builder internals: `_RECRUITMENT_ROLE`, `_RECRUITMENT_RULES` (10 rules), `_RECRUITMENT_WORKFLOW` (3-step funnel)
- `clean_general_reply()`: what artifact prefixes it strips, when it is called (message_router step 15 only), what happens if stripping produces empty string
- Source parameter rule: NEVER changes prompt content — logged only
- Visibility: Developer/Internal only — do NOT set safe_for_customer=True

**Completion criterion:** ≥ 60 lines; all 6 sections documented with Bengali header text from actual code; POLICY_VERSION noted; both builders explained; ROLE_PROMPTS count (7) and INTENT_HINTS count (12) confirmed from code.

---

#### TASK 1-D: Enrich `workflow_engine.md` (13 lines → full article)

**Enrichment Plan ref:** P1-D in `kb_enrichment_plan_v2.md`
**Source to read FIRST:** `modules/message_router/__init__.py` (581 lines — read completely)
**Current state:** 13-line stub. Mentions "routing" only.

**Required content** (all values must be read from code — do NOT guess):
- Full 15-step routing priority table: step number, trigger condition, action, destination module
- `_SAFE_AUTOSEND_INTENTS` frozenset: list all intents exactly as they appear in code
- `DRAFT_ALWAYS_ROLES` set: list all roles that always generate drafts
- `REPLY_COOLDOWN = 60` seconds: per-sender anti-spam; what happens when triggered (silent drop)
- `_ESCORT_ROLES` frozenset: 4 roles that trigger escort handling path
- `_SILENT_SKIP_NAME_TOKENS`: list of name tokens that trigger silent skip (do not expose values — note count only)
- Office-location fast path (Step 12): KB-only reply, no LLM call, what triggers it
- Complaint-phrase guard: how specific phrases force draft regardless of intent or role
- Recruitment block for operational roles: recruitment intent + operational role → silent return (not an error)
- Step 14: `reviewed_reply_memory` lookup — where it sits in the chain (before LLM, after KB)
- Step 15: RAG enrichment (`rag.search(text, k=3)`) enriches `db_ctx` for general AI fallback
- `DRAFT_ALWAYS_PHONES`: note existence and that phone list must NOT appear in KB (security)
- Visibility: Developer/Admin only

**Completion criterion:** ≥ 70 lines; all 15 steps present in table form; exact frozenset members listed (read from code); REPLY_COOLDOWN = 60 documented; office-location fast path and complaint-phrase guard both documented.

---

#### TASK 1-E: Create `escort_roster_system.md` (new article)

**Enrichment Plan ref:** P1-E in `kb_enrichment_plan_v2.md`
**Sources to read FIRST:** All files in `modules/escort_roster/` — `__init__.py`, `db.py`, `calculations.py`, `extractor.py`, `history_sync.py`, `routes.py` (3111 lines total)
**Current state:** Zero coverage. Largest undocumented module. Pay calculation entirely in code.

**Required content:**
- Module purpose: sync escort programs from `wbom_escort_programs` (parent) into `escort_roster_entries` (child)
- `sync_program_to_roster(program_id)`: upsert flow, what fields are synced, idempotency
- Pay calculation: read `calculate_pay()` from `calculations.py` — document actual formula (do not invent)
- `_get_shift_rate()`: how daily shift rates are resolved
- Conveyance rate table: read `_get_conveyance_for_destination()` from `calculations.py` — list exact rates per destination (Mongla, Dhaka, Faridpur, etc.)
- Roster entry lifecycle: sync → recalculate → closed; what triggers each state
- `escort_roster_audit_logs` table: that it exists, what it records
- Draft entry cleanup pipeline — 3 jobs:
  - `cleanup_draft_entries()`: removes incomplete entries
  - `expire_stale_drafts(hours=48)`: expires roster draft entries after 48h
  - `cleanup_junk_drafts()`: removes orphaned entries
- `history_sync_worker`: backfill purpose
- Remarks field: fault-tolerant JSON parsing (may be clean JSON, corrupted JSON, or plain text)
- **CRITICAL TTL distinction** (resolves CR-04 documentation requirement):
  - `escort_roster_entries` draft TTL = **48 hours** (hardcoded in `db.py`)
  - `fazle_draft_replies` TTL = **24 hours** (DRAFT_TTL_HOURS in `.env`)
  - These are DIFFERENT tables. The 48h and 24h values govern DIFFERENT objects.
- Visibility: Developer/Admin only (financial calculation internals)

**Completion criterion:** ≥ 80 lines; pay formula from actual `calculations.py`; conveyance table with exact destination-rate pairs; all 3 cleanup jobs documented; TTL distinction explicit.

---

#### TASK 1-F: Create `runtime_gateway_flags.md` (new article)

**Enrichment Plan ref:** P1-F in `kb_enrichment_plan_v2.md`
**Sources to read FIRST:**
- `shared/runtime_gateway.py` (614 lines)
- `/home/azim/core/.env` (all feature flags)

**IMPORTANT CORRECTION from enrichment plan:**
The enrichment plan says "All feature flags in `shared/runtime_gateway.py`." This is incorrect. `shared/runtime_gateway.py` is a **distributed node registry and heartbeat manager**, NOT a feature-flag registry. Functions: `register_node()`, `heartbeat()`, `deregister_node()`, `mark_stale_nodes()`, `get_active_nodes()`, `heartbeat_loop()`, `start_gateway()`, `stop_gateway()`.

The actual feature flags live in **`.env`** and are read by individual modules. This article should document:

**Section 1 — Kill-Switch Registry** (from `.env` and module code)

| Flag | Default | Module That Reads It | Effect When Disabled |
|---|---|---|---|
| `HYBRID_SEARCH_ENABLED` | false | `modules/rag` | Falls back to BM25-only; Qdrant not contacted |
| `AUTO_REPLY_ENABLED` | false | `modules/message_router` | All replies go to draft queue; nothing auto-sent |
| `DRAFT_QUALITY_GATE` | true | `modules/draft_quality` | Disables all 4 quality checks; all replies pass to queue |
| `OUTBOUND_ENABLED` | true | `modules/outbound` | Stops all outbound WhatsApp sends |
| `SCHEDULER_ENABLED` | true | `modules/scheduler` | Stops all 15 scheduled jobs |
| `REVIEWED_REPLY_MEMORY_ENABLED` | (read from code) | `modules/reviewed_reply_memory` | Disables cached-reply lookup |
| `SOCIAL_AUTO_REPLY_SINGLE_ENGINE` | false | `modules/social_auto_reply` | (read effect from code) |
| `OLLAMA_REPLY_DISABLED` | false | `app/llm.py` | Skips Ollama as LLM fallback |
| `AGENT_PROACTIVE_OUTBOUND_ENABLED` | false | (read from code) | (read effect) |

For each flag: read the exact module that consumes it; document what the system does when the flag is turned off.

**Section 2 — Runtime Node System** (from `shared/runtime_gateway.py`)
- Multi-instance architecture: fazle-core, payroll-engine, escort-roster each register as a RuntimeNode
- `RuntimeNode` dataclass fields (read from code)
- Heartbeat interval and stale-node detection threshold
- What happens when a node dies mid-processing (lease release, stale-node sweep)
- `start_gateway()` / `stop_gateway()` lifecycle

**Section 3 — Safe Toggle Procedures**
- Which flags can be toggled without restart (via DB or runtime)
- Which flags require a process restart to take effect
- Emergency disable sequence for common incidents (auto-reply storm, outbound queue overflow)

**Visibility:** Developer/Admin only — kill-switch documentation

**Completion criterion:** ≥ 80 lines; all env flags listed with module reference; RuntimeNode dataclass documented; safe toggle guidance present; enrichment plan correction noted (gateway.py is not a flag registry).

---

#### TASK 1-P2-A: Enrich `visibility_rules.md` (12 lines → full article)

**Enrichment Plan ref:** P2-A in `kb_enrichment_plan_v2.md` (elevated to Phase 1 because it blocks Phase 2 and Phase 3)
**Sources to read FIRST:**
- `modules/rag/__init__.py` — `safe_for_customer` filter at BM25 build and Qdrant query time
- `knowledge_base/ai_access_matrix.md` (root-level file outside KB folders — incorporate its content)
- `knowledge_base/03_ai_identity/permission_matrix.md`
**Current state:** 12-line stub. No enforcement mechanism documented.

**Required content:**
- 3-level visibility model (from `organizational_brain_gap_report.md` Gap Category):
  - **Public (safe_for_customer=True):** Recruitment info, office address, general job policies
  - **Internal:** Salary ranges, daily rates, joining fee, form fee — employees and admin only
  - **Restricted:** FPE financial data, DRAFT_ALWAYS_PHONES, authorized phone lists, admin API outputs, developer system internals — never exposed to candidates or external contacts
- KB folder × role access matrix:

| KB Folder | Candidate | Employee | Supervisor | Admin | Developer |
|---|---|---|---|---|---|
| `01_employee_knowledge/` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `02_admin_knowledge/` | ❌ | ❌ | ⚠️ limited | ✅ | ✅ |
| `02_admin_system/` | ❌ | ❌ | ❌ | ✅ | ✅ |
| `03_ai_identity/` | ❌ | ❌ | ❌ | ✅ | ✅ |
| `03_developer_system/` | ❌ | ❌ | ❌ | ✅ | ✅ |
| `04_business_rules/` | ✅ limited | ✅ | ✅ | ✅ | ✅ |
| `05_workflows/` | ✅ recruitment only | ✅ | ✅ | ✅ | ✅ |
| `06_developer_system/` | ❌ | ❌ | ❌ | ❌ | ✅ |
| `07_archived/` | ❌ | ❌ | ❌ | ❌ | ✅ |

- Current enforcement: `safe_for_customer=True` filter — binary (safe/unsafe), not role-aware
  - Applied at BM25 index build time (unsafe chunks excluded from index)
  - Applied at Qdrant query time (payload filter on every vector search)
  - Current `search()` API has NO role parameter — all "safe" content is equally accessible to all roles
- Phase 3 design (what needs to be built): role-aware RAG filtering (see Phase 3)
- What must NEVER reach candidates: transport cost tables, escort conveyance rates, FPE data, admin phone numbers

**Completion criterion:** ≥ 70 lines; 3-level visibility model; role × folder matrix; `safe_for_customer` mechanism with code reference; current enforcement gap documented; Phase 3 design noted.

---

### Phase 1 Completion Criteria

All 5 tasks complete when:
- [ ] `system_prompt.md`: ≥ 60 lines, all 6 sections, POLICY_VERSION, ROLE_PROMPTS (7), INTENT_HINTS (12)
- [ ] `workflow_engine.md`: ≥ 70 lines, 15 steps in table, all frozensets from code, REPLY_COOLDOWN = 60, office-location fast path
- [ ] `escort_roster_system.md`: ≥ 80 lines (new file), pay formula from code, conveyance table, 3 cleanup jobs, TTL distinction
- [ ] `runtime_gateway_flags.md`: ≥ 80 lines (new file), all env flags with module ref, RuntimeNode dataclass
- [ ] `visibility_rules.md`: ≥ 70 lines, 3-level model, role × folder matrix, safe_for_customer mechanism
- [ ] All articles committed (one commit per batch of 2–3 articles)
- [ ] Pushed to `origin/backup/vps-core-20260612`
- [ ] `module_alignment_report.md` supplemental table updated with new coverage %
- [ ] `management_decisions.md` updated with Phase 1 completion entry

**Coverage impact of Phase 1:** ~25% → ~42% (estimated; measured by running mini-PKCA after completion)

---

## PHASE 2 — Identity Brain Integration

**Objective:** Complete the Identity Brain documentation so that Role + Permission + Visibility + Business Rules + Knowledge Access + Workflow Access are documented as a unified system — not as separate isolated articles.
**Type:** KB-only — NO production code changes
**Owner:** AI Agent
**Management Approval Required:** YES — Phase 2 authorization entry in `management_decisions.md`
**Prerequisite:** Phase 1 complete

---

### Current Identity Brain State

From `module_alignment_report.md` Module 2 (`identity_brain`, 65% — best-covered module):

**Exists in KB:**
- `identity_brain.md` (212 lines): 11-step algorithm documented, phone variant lookup documented
- `permission_matrix.md` (89 lines): 37 admin commands + RBAC documented
- `identity_overview.md`: high-level overview

**Missing from KB** (from reading `modules/identity_brain/__init__.py`, 393 lines):
- `_CANDIDATE_KEYWORDS` list: 10 Bengali/English terms that trigger candidate classification (not in KB)
- `_ESCORT_CONTENT_RE` pattern: regex that triggers `client_escort_buyer` for vessel keywords — description not in KB
- Confidence score mapping: numeric confidence thresholds not documented
- `_ROLE_PRIORITY` numeric weights: how conflicting signals are resolved — not in KB
- Step 10 override logic: candidate keyword detection runs before DB lookup in some conditions — unclear in KB

---

### Phase 2 Task List

---

#### TASK 2-A: Complete `identity_brain.md` Missing 35%

**Source to read:** `modules/identity_brain/__init__.py` (393 lines) — read completely
**Required additions:**
- `_CANDIDATE_KEYWORDS`: exact list (10 terms) — copy from code (Bengali + English)
- `_ESCORT_CONTENT_RE`: describe what vessel keywords trigger `client_escort_buyer` (describe — do not expose raw regex)
- Confidence score thresholds: read the score-to-role mapping from code
- `_ROLE_PRIORITY` weight table: which signals override which (numeric values from code)
- Step 10 clarification: when does keyword detection override DB lookup?

**Completion criterion:** `identity_brain.md` covers all 11 steps + 4 hidden rules; confidence score table present; _ROLE_PRIORITY documented.

---

#### TASK 2-B: Add Phase 3 Design Section to `visibility_rules.md`

*(Only after Phase 1 Task 1-P2-A is complete)*

**Required additions to visibility_rules.md:**
- Current `search()` API: `async def search(q, k=5, min_score=0.0)` — no role parameter
- Proposed Phase 3 signature: `async def search(q, k=5, min_score=0.0, role: str = "candidate")`
- How role-based filtering will work: KB article metadata gets `allowed_roles` field; RAG filters at BM25 and Qdrant level
- Which KB articles need `allowed_roles` metadata tags before Phase 3 implementation
- Role categories: `candidate`, `employee`, `supervisor`, `admin`, `developer`
- Rollback: `role="candidate"` default (most restrictive) preserves current behavior

**Completion criterion:** `visibility_rules.md` has a "Phase 3 Design" section with proposed API signature and metadata tagging plan.

---

#### TASK 2-C: Create `identity_integration.md` (new article)

**Purpose:** Single-page unified reference showing how all 6 identity dimensions interact per sender type.

**Required content:**

Decision table for each sender type (Candidate, Employee, Supervisor, Admin, Developer):

| Dimension | Candidate | Employee | Supervisor | Admin |
|---|---|---|---|---|
| Identified by | `_CANDIDATE_KEYWORDS` or recruitment session | `wbom_employees` phone match | supervisor flag in `wbom_employees` | `ADMIN_NUMBERS` env or RBAC role |
| KB folders accessible | 01 (limited), 04 (limited), 05 (recruitment only) | 01, 04, 05 | 01, 02 (limited), 04, 05 | all except 06 |
| Workflows can enter | `recruitment_flow` (6-step funnel) | attendance, payment_verification | attendance (supervisor path) | all admin commands |
| Business rules that apply | BR-25 (age 18–55), fee policy | payroll formula, attendance deduction | supervisor attendance rules | all |
| Prompt builder used | `build_whatsapp_recruitment_policy()` | `build_whatsapp_reply_policy()` | `build_whatsapp_reply_policy()` | `admin_commands` handler |
| Reply disposition | DRAFT (DRAFT_ALWAYS_ROLES includes candidate) | depends on `AUTO_REPLY_ENABLED` | DRAFT | direct command response |

Cross-references: `identity_brain.md`, `visibility_rules.md`, `permission_matrix.md`

**Completion criterion:** ≥ 60 lines; full decision table; all 6 dimensions for 4+ sender types; cross-references present.

---

### Phase 2 Completion Criteria

- [ ] `identity_brain.md`: all 11 steps + _CANDIDATE_KEYWORDS + confidence scores + _ROLE_PRIORITY
- [ ] `visibility_rules.md`: Phase 3 Design section added
- [ ] `identity_integration.md`: new article, ≥ 60 lines, 6-dimension decision table
- [ ] All committed and pushed
- [ ] `module_alignment_report.md` updated

---

## PHASE 3 — Visibility Engine (Production Code Change)

**Objective:** Enforce role-based KB access in the RAG query path. A candidate must not receive knowledge intended for employees or admins.
**Type:** Production code change + KB documentation
**Owner:** Developer + Management
**Management Approval Required:** YES — explicit production code change authorization per-file in `management_decisions.md`
**Prerequisite:** Phase 2 complete; management code-change authorization

---

### Current Enforcement Gap (Verified from Code)

From `modules/rag/__init__.py`:
- `safe_for_customer=True` filter: enforced at BM25 build AND Qdrant query time ✅
- BUT: `search()` signature is `async def search(q, k=5, min_score=0.0)` — no `role` parameter
- All "safe_for_customer" content is equally accessible to every caller
- Role-based filtering does NOT exist today

### Phase 3 Changes (Subject to Per-File Authorization)

**Change 3-A — KB Article Metadata Tagging (Database)**
- Add `allowed_roles` column to `fazle_knowledge_base` table (or use existing metadata JSON field)
- Tag every active KB row per the visibility matrix in `visibility_rules.md`
- Roles: `candidate`, `employee`, `supervisor`, `admin`, `developer`

**Change 3-B — RAG `search()` API Extension**
- File: `modules/rag/__init__.py`
- Change: `async def search(q, k=5, min_score=0.0, role: str = "candidate") -> list[dict]`
- Apply `allowed_roles` contains-role filter at both BM25 path and Qdrant payload filter
- Backward-compatible: `role="candidate"` is the most restrictive default — existing callers that don't pass role get candidate-level access

**Change 3-C — Call Site Updates**
- `modules/recruitment_ai/__init__.py`: `_safe_rag_chunks(text, k=5, role="candidate")`
- `modules/message_router/__init__.py` Step 15: pass resolved caller role to `rag.search()`
- No change to `shared/reply_policy.py` (does not call RAG)

**Change 3-D — Qdrant Payload Update**
- `build_index()`: add `allowed_roles` list to each point payload on upsert
- Vector query: add `allowed_roles must contain role` to Qdrant filter condition

### Phase 3 KB Requirement (Before Code Change)

Before ANY code change, `visibility_rules.md` must document:
- Final API signature (from Phase 2 Task 2-B design)
- What happens on role mismatch (returns empty list — no error to caller)
- Rollback: set `role="candidate"` at all call sites to restore current behavior

### Phase 3 Completion Criteria

- [ ] Management code-change authorization recorded per-file in `management_decisions.md`
- [ ] `visibility_rules.md` finalized with exact API change before code edit
- [ ] `fazle_knowledge_base` DB rows tagged with `allowed_roles`
- [ ] `modules/rag/__init__.py` `search()` extended with role parameter
- [ ] All 2 call sites updated with correct role values
- [ ] All unit tests pass after change
- [ ] Committed and pushed

---

## PHASE 4 — Knowledge Coverage 90%+

**Objective:** Systematically close P2 and P3 coverage gaps across all 52+ modules until weighted coverage reaches ≥ 90%.
**Type:** KB-only
**Owner:** AI Agent
**Management Approval Required:** YES — Wave-4 authorization
**Prerequisite:** Phase 1 complete (Phase 2 preferred but not blocking)

---

### Coverage Path

Current: ~25% → After Phase 1: ~42% → Target: ≥ 90%
Gap to close in Phase 4: ~48 percentage points across 52+ modules

---

### Phase 4 Article Work Plan

All tasks reference `kb_enrichment_plan_v2.md` for detailed content templates. Reference that document — do not duplicate.

**Group A — P2 Modules (15 new/updated articles)**

| Plan Ref | Module | Current Coverage | Article Target | Key Content |
|---|---|---|---|---|
| P2-B | reviewed_reply_memory | 5% | CREATE `06_developer_system/reviewed_reply_memory.md` | Match scope (phone + intent + role), TTL, REVIEWED_REPLY_MEMORY_ENABLED kill-switch, lookup position (step 14, before LLM) |
| P2-C | scheduler (all jobs) | 30% | UPDATE `automation_pipeline.md` | Exact cron schedules for all 15 jobs (not just 8), SCHEDULER_ENABLED, RUN JOB command list |
| P2-D | Financial constants | — | UPDATE `payment_business_rules.md` | Document management-approved rates only; note pending items as "authorization pending" |
| P2-E | escort lifecycle | 40% | UPDATE `escort_workflow.md` | `[RELEASE CONFIRMED]` exact text, remarks JSON (8 fields), status transitions (draft → confirmed → released) |
| P2-F | recruitment_ai detail | 40% | CREATE `06_developer_system/recruitment_ai_detail.md` | `_deterministic_fact_reply()`, section scoring algorithm, `_safe_rag_chunks()` k=5, `enforce_recruitment_reply_policy()`, `_FEE_PHRASES` (16 phrases) |
| P2-G | wa_chat_frontend | 0% | CREATE `06_developer_system/admin_ui.md` | 28 endpoints, SSE stream events, cursor pagination, group broadcast, per-number block |
| P2-H | admin_transactions | 10% | CREATE `02_admin_knowledge/admin_transactions_rules.md` | 4-rule employee matching (A/B/C/D), soft-delete policy, amount-change → ledger recalculation, `employee_id_phone` immutability |
| P2-I | queue_arbiter + self_heal + bridge_orchestrator | 0–10% | CREATE `06_developer_system/distributed_architecture.md` | Lease system (LEASE_TTL_S=120), multi-instance architecture, 6 self-heal conditions, bridge authority hierarchy (bridge2 highest), HISTORICAL_CUTOFF_S |
| P2-J | phone_normalizer | 45% | CREATE `06_developer_system/phone_normalizer.md` | Canonical 13-digit format, VALID_OPERATORS set, accepted input forms, non-BD returns None, LID skip |
| P2-K | recruitment_flow vs recruitment_ai | 30% | CREATE `06_developer_system/recruitment_flow_system.md` | Two-path disambiguation, INTAKE_KEYWORDS trigger, 6-step COLLECTION_STEPS, SESSION_TTL=24h, OPERATIONAL_ROLES exclusion |
| — | bridge_poller | 20% | CREATE `06_developer_system/bridge_poller.md` | Ingest pipeline, REPLY_COOLDOWN=60s, LID resolution, dedup table, adaptive backoff, DM-always rule, complaint-phrase guard |
| — | ocr_processor | 35% | UPDATE `ocr_engine.md` | Confidence threshold, document types (release slip/bank stmt/bkash screenshot), OCR routing decision |
| — | contact_sync | 0% | CREATE `06_developer_system/contact_sync.md` | 3-source merge, display_name priority rule, LID JID handling, BD-only normalization |
| — | self_heal | 0% | Incorporated in `distributed_architecture.md` (P2-I above) | — |
| — | admin_employees | 15% | UPDATE `admin_operations_overview.md` | 13 EmployeeCreate fields, mobile immutability, FPE auto-seed on create, soft deactivation |

**Group B — Database Deep Documentation**

| Area | Current | Action |
|---|---|---|
| 43+ tables | `database_rules.md` (503 lines) — domain assignments complete | Verify all 43 tables present; add DDL-level field descriptions for top 10 tables by access frequency |
| FPE tables (4 tables, FSM) | `fpe_overview.md` (483 lines) — architecture at 35% | Add: `fpe_transactions` FSM state diagram, `fpe_employee_ledger` schema, 5-worker roles |
| Payroll tables | 5% | Add: `wbom_payroll_runs` schema, 5-state machine, ALLOWED_TRANSITIONS dict |
| Recruitment tables | Partial | Add: `fazle_recruitment_sessions` schema, SESSION_TTL=24h |

**Group C — Admin Command Completions (P3-A through P3-G)**

Reference `kb_enrichment_plan_v2.md` P3 section for full list. Key items:
- NL query categories (nl_router.py) and date parser supported formats
- Bangla numeral support in commands (APPROVE ১৬৫ → APPROVE 165)
- Scheduler SCHEDULE STATUS command output format
- RBAC: SHA-256 key storage, `fazle_admin_audit` table, superadmin bootstrap behavior

**Group D — Social Auto Reply Depth**

| Area | Status |
|---|---|
| `risk_flagger.py` escalation thresholds | Read from code; document exact thresholds |
| `state_tracker.py` transitions | Read from code; document state machine |
| `payment_issue_handler` comment → payment complaint path | Read from code |
| Salary figures (৳10,000–৳18,000, ৳17,000, ৳24,700) | **BLOCKED** — require management approval before KB inclusion |

---

### Phase 4 Completion Criteria

- [ ] All Group A articles written (15 new/updated)
- [ ] Group B: top 10 tables with DDL-level docs; FPE FSM; payroll state machine
- [ ] Group C: NL query categories, Bangla numeral support
- [ ] Group D: escalation thresholds documented (salary figures pending management approval)
- [ ] Weighted KB coverage ≥ 80% (run mini-PKCA to measure)
- [ ] All committed and pushed

---

## PHASE 5 — Production Knowledge Freeze v2 (Validation Gate)

**Objective:** Run PKCA, PKMA, and PKVC validation programs against the fully-enriched KB. Confirm all v2 targets are met before certifying KB v2.
**Type:** Read-only audit — no KB edits, no code changes
**Owner:** AI Agent
**Management Approval Required:** YES — KB v2 certification authority
**Prerequisite:** Phase 4 complete

---

### Target Metrics

| Metric | Target | How to Measure |
|---|---|---|
| Coverage | ≥ 90% | PKCA: for each module, score KB coverage; weighted average |
| Maturity | ≥ 4/5 average per article | PKMA: Accuracy + Completeness + Clarity + Currency + Consistency |
| Conflicts | 0 | PKVC: cross-reference all factual claims against production code |
| Critical Gaps | 0 | PKCA: no P0 or P1 module below 70% coverage |
| Brain Readiness | ≥ 80% (GOOD) | Score calculation per `organizational_brain_gap_report.md` method |

### Phase 5 Process

1. AI agent reads ALL KB articles sequentially (not from memory — fresh reads)
2. PKCA: for each production module, score KB coverage 0–100%; produce coverage table
3. PKMA: for each KB article, score 5 maturity dimensions; average per article
4. PKVC: cross-reference every factual claim (rates, formulas, constants, sequences) against live production code — flag any divergence
5. Produce PKCA/PKMA/PKVC v2 reports in `07_archived/pkXX_reports_20260623/`
6. If any target missed → return to Phase 4 for specific gap closure
7. All targets met → present KB v2 certification proposal to management
8. Management approves → update `management_decisions.md` with KB v2 certification entry

### Phase 5 Completion Criteria

- [ ] PKCA v2 report: coverage ≥ 90% per weighted module audit
- [ ] PKMA v2 report: average maturity ≥ 4/5 across all articles
- [ ] PKVC v2 report: zero conflicts between KB facts and production code
- [ ] Brain Readiness ≥ 80% GOOD
- [ ] Management certification approval in `management_decisions.md`
- [ ] KB v2 governance record created in `07_archived/`

---

## PHASE 6 — Production Refactoring (Conditional)

**Objective:** Where KB v2 and production code are verified to diverge AND the divergence causes a real problem AND management has authorized the specific fix — align the code to the KB.
**Type:** Production code only
**Owner:** Developer (AI Agent assistance)
**Management Approval Required:** YES — per-module authorization (not a blanket refactoring approval)
**Prerequisite:** Phase 5 certified; management authorization per specific change

---

### Activation Gate (ALL conditions must be true)

```
Condition A: KB v2 is certified (Phase 5 complete)
Condition B: A specific KB article states X as the authoritative behavior
Condition C: Production code does Y (not X) — verified by reading code at Phase 6 start
Condition D: The divergence causes a real operational or financial problem
Condition E: Management explicitly authorizes this specific file change
```

If any condition is false: **NO CODE CHANGE.**

### Refactoring Candidates (Re-verify at Phase 6 Start)

These are potential mismatches identified in prior audits. Each must be re-verified against actual production code at Phase 6 start — code may have changed.

| Module | Potential Mismatch | Re-verify Command |
|---|---|---|
| `identity_brain` | KB Step 10 = DB lookup; production may run keyword check first in some conditions | `grep -n "_CANDIDATE_KEYWORDS\|step.*10" modules/identity_brain/__init__.py` |
| `recruitment_flow` | KB describes session funnel as sole recruitment path; production has dual path (funnel + LLM FAQ) | `grep -n "recruitment_ai\|recruitment_flow" modules/message_router/__init__.py` |
| `knowledge_base` module | Hardcoded fallback templates (৳3,500 joining fee, etc.) may diverge from KB v2 authoritative values | `grep -n "FALLBACK\|৳" modules/knowledge_base/__init__.py` |
| `payroll_logic` | Core computation formula undocumented — if formula is wrong vs KB v2, refactor needed | `cat modules/payroll_logic/__init__.py` |

### Refactoring Prohibited Without Authorization

- No "cleanup" refactoring without specific documented KB/production conflict
- No architectural changes without management approval
- No module restructuring without Phase 5 complete
- DORMANT modules (`payment_correction`, `conversation_layer`): no changes without explicit activation decision

---

## Agent Execution Protocol (Every Session, Every Phase)

**Step 1 — Read governance (mandatory):**
```
Read: /home/azim/core/knowledge_base/00_governance/README.md
Read: /home/azim/core/knowledge_base/00_governance/management_decisions.md
Read: /home/azim/core/knowledge_base/00_governance/master_execution_plan_v2.md  ← this doc
```

**Step 2 — Verify current state:**
```bash
cd /home/azim/core
git log --oneline -3                                                    # recent commits
grep HYBRID_SEARCH_ENABLED .env                                        # RAG live check
grep POLICY_VERSION shared/reply_policy.py                             # prompt builder check
wc -l knowledge_base/06_developer_system/system_prompt.md             # Phase 1 status
python3 -m pytest tests/unit/test_reply_policy.py -q                  # test health
```

**Step 3 — Confirm phase authorization:**
- Find the authorization entry in `management_decisions.md` for the phase you are about to execute
- If authorization entry is absent: STOP — present phase proposal to management; do not proceed

**Step 4 — Read before writing (no exceptions):**
- For every KB task: read the SOURCE PRODUCTION FILE first, then read the CURRENT KB article
- Never write from session summary or memory
- Cite the source file and line numbers in the article

**Step 5 — KB-only in Phases 1, 2, 4:**
- Zero production code changes in Phases 1, 2, 4
- Production changes only in Phases 3 and 6, each requiring file-level authorization

**Step 6 — Commit and push:**
- Phase 1: commit per batch of 2–3 articles; push after each commit
- Phase 3: one commit per production file changed (for clean rollback traceability)
- Rule: NEVER pull — only push (VPS server; working directly on production branch)

**Step 7 — Update governance trail after each phase:**
- Update `management_decisions.md`: add phase completion entry
- Update `module_alignment_report.md`: update coverage % for all modules touched
- Update this document's Progress Tracker table

---

## Progress Tracker

| Phase | Status | Target Start | Completed | Notes |
|---|---|---|---|---|
| **Infrastructure** | ✅ LIVE | 2026-06-22 | 2026-06-22 | Hybrid RAG + structured_v2 + Phase 4 code |
| **Conflicts** | ✅ RESOLVED | 2026-06-23 | 2026-06-23 | CR-01–CR-06; all committed `4c2b6f2` |
| **PHASE 1** — Wave-3 P1 KB | ✅ COMPLETE | 2026-06-23 | 2026-06-23 | 5 articles, 1,492 lines; commits 43d8eec–81818ab |
| **PHASE 2** — Identity Brain | ✅ COMPLETE | 2026-06-23 | 2026-06-23 | identity_brain.md (307), identity_integration.md (194 new); commit 510cd15 |
| **PHASE 3** — Visibility Engine | ⏳ PENDING | — | — | Requires production code authorization |
| **PHASE 4** — Coverage 90%+ | ⏳ PENDING | — | — | Can begin after Phase 1 |
| **PHASE 5** — Freeze v2 | ⏳ PENDING | — | — | After Phase 4 |
| **PHASE 6** — Refactoring | ⏳ CONDITIONAL | — | — | Phase 5 + per-module approval |

---

## Quick Reference — Phase Deliverables

| Phase | Articles / Changes | KB or Code | Coverage Impact |
|---|---|---|---|
| Phase 1 | 5 articles (system_prompt, workflow_engine, escort_roster, runtime_gateway_flags, visibility_rules) | KB only | ~25% → ~42% |
| Phase 2 | 3 articles (identity_brain complete, visibility_rules Phase 3 design, identity_integration) | KB only | ~42% → ~48% |
| Phase 3 | RAG search() role parameter + KB metadata tagging + 2 call sites | Code + KB | — (quality, not coverage) |
| Phase 4 | 15+ new/updated articles + DB schemas + admin command docs | KB only | ~48% → ~90% |
| Phase 5 | PKCA/PKMA/PKVC v2 reports + KB v2 certification | Audit only | Gate: ≥ 90% |
| Phase 6 | Targeted code fixes per authorized KB/production mismatches | Code only | — |

---

## Source Documents

| Document | Purpose | Read When |
|---|---|---|
| `00_governance/kb_enrichment_plan_v2.md` | Article content templates, P0–P3 task list, Wave-3 delivery sequence | Before every Phase 1 and Phase 4 task |
| `00_governance/organizational_brain_gap_report.md` | Gap categories, business impact ranking, brain readiness score method | Before Phase 4 prioritization; before Phase 5 audit |
| `00_governance/module_alignment_report.md` | Module-by-module coverage scores, hidden rules, conflict list | Before writing any KB article (check what already exists) |
| `00_governance/management_decisions.md` | Authoritative decisions, conflict resolutions, phase authorizations | Step 3 of every session |
| `00_governance/session4_audit_report_2026_06_23.md` | Full before/after record of Session 4 | When verifying what was done before starting |

---

*Master Execution Plan v2 | Rewritten 2026-06-23 (post-Session 4)*
*All facts verified from production code and KB files — not from session memory.*
*Source data: module_alignment_report.md, kb_enrichment_plan_v2.md, organizational_brain_gap_report.md, direct code inspection*
*Next action: Management authorize Wave-3 (Phase 1) → AI Agent executes TASK 1-B*
