# Master Execution Plan v2 — Organizational Brain Validation & Alignment
**Document ID:** MEP-v2
**Created:** 2026-06-23
**Authority:** Management (Al-Aqsa Security & Logistics Services Ltd.)
**Goal:** Knowledge Base → Organizational Brain → Production AI
**Status:** ACTIVE — Phases 1–3 ready for execution

---

## Purpose of This Document

This plan defines every phase between the current state (~25% KB coverage, ~68% Brain Readiness) and the final target (KB v2 Certified, Coverage ≥ 90%, Production AI fully KB-grounded). It is written for AI agents executing future sessions. Each phase has a verified baseline, specific artifacts, and machine-checkable completion criteria. No phase may be started without reading this document.

---

## Current State Baseline (Verified 2026-06-23)

All facts below were verified by reading actual production code and KB files — not from prior session summaries.

### Infrastructure

| Component | Status | Evidence |
|---|---|---|
| Hybrid RAG | ✅ LIVE | `HYBRID_SEARCH_ENABLED=true` in `.env`; `_hybrid_search()` at line 837, RRF formula at line 792 of `modules/rag/__init__.py` |
| RRF Fusion | ✅ CONFIRMED | `rrf_score = 1/(60 + bm25_rank) + 1/(60 + vector_rank)` — line 792 |
| Qdrant Vector DB | ✅ ACTIVE | Server mode at `172.20.0.2:6333`; collection `fazle_rag_chunks` |
| MiniLM Encoder | ✅ ACTIVE | `paraphrase-multilingual-MiniLM-L12-v2`, 384 dims, LRU cache 2000 entries |
| Prompt Builder | ✅ STRUCTURED | `POLICY_VERSION="structured_v2"`, 6-section format, `shared/reply_policy.py` line 29 |
| Auto Reply | ⚠️ DRAFT MODE | `AUTO_REPLY_ENABLED=false` — all AI replies go to admin review queue |
| Draft Quality Gate | ✅ ACTIVE | `DRAFT_QUALITY_GATE=true` — 4 criteria, documented in `automation_pipeline.md` |
| Scheduler | ✅ ACTIVE | `SCHEDULER_ENABLED=true`, 15 jobs, Asia/Dhaka timezone |
| Outbound Queue | ✅ ACTIVE | `OUTBOUND_ENABLED=true` |

### Knowledge Base

| Metric | Value | Source |
|---|---|---|
| KB version | v1.0 CONTROLLED FREEZE | `management_decisions.md` GOV-01 |
| Weighted module coverage | ~25% | `module_alignment_report.md` (52+ modules audited) |
| Brain readiness score | ~68% CONDITIONAL | `session4_audit_report_2026_06_23.md` Section 5 |
| Conflicts resolved | 6/6 (CR-01–CR-06) | `management_decisions.md` |
| Critical stubs remaining | 3 | `system_prompt.md` (13 lines), `workflow_engine.md` (13 lines), `visibility_rules.md` (12 lines) |
| KB directory structure | Partly duplicated | `02_admin_knowledge` + `02_admin_system`, `03_ai_identity` + `03_developer_system` — both exist |

### Module Coverage Summary (from module_alignment_report.md — Phase 4 Step 5)

| Priority | Modules | Coverage Range | Count |
|---|---|---|---|
| P0 (Critical — now resolved) | rag, payroll | was 5–25% → fixed | 2 |
| P1 (High — blocking Brain Readiness) | message_router, social_auto_reply, reply_policy, escort_roster, recruitment_flow, admin_transactions, queue_arbiter, bridge_orchestrator, runtime_gateway, payroll_logic | 0–40% | 10 |
| P2 (Medium) | escort, payment_workflow, bridge_poller, fazle_payroll_engine, knowledge_base module, drafts, wa_chat_frontend, admin_employees, reports, outbound, employee_verification, contact_sync, ocr_processor, phone_normalizer, self_heal | 0–35% | 15 |
| P3 (Low) | identity_brain, attendance, admin_commands, intent, rbac, reviewed_reply_memory, utility modules (14) | 20–65% | 20+ |

---

## Target State

```
KB v2 Certified
  ├── Coverage ≥ 90% (from ~25% now)
  ├── Brain Readiness ≥ 80% GOOD (from ~68% now)
  ├── Conflicts = 0 (currently 0 — must stay 0)
  ├── Critical Gaps = 0 (currently: 3 stubs + 10 P1 undocumented modules)
  └── PKCA / PKMA / PKVC all pass at v2 level
```

The path has 6 phases. Phases 1–4 are KB-only work (no production code changes). Phase 5 is a validation gate. Phase 6 is conditional production refactoring.

---

## PHASE 1 — Priority Gap Remediation (Wave-3 KB Enrichment)

**Objective:** Bring all P0/P1 modules from current 0–40% coverage to ≥ 80%.
**Owner:** AI Agent (KB-only; no production code changes)
**Management Approval Required:** YES — before starting Phase 1 execution
**Estimated Output:** 7 new/rewritten KB articles

---

### Pre-conditions for Phase 1 Start

An AI agent starting Phase 1 MUST verify:
1. `cat /home/azim/core/.env | grep HYBRID_SEARCH_ENABLED` → must show `true`
2. `wc -l /home/azim/core/knowledge_base/06_developer_system/system_prompt.md` → currently 13 lines (stub to enrich)
3. `wc -l /home/azim/core/knowledge_base/06_developer_system/workflow_engine.md` → currently 13 lines (stub to enrich)
4. Read `management_decisions.md` — confirm Phase 1 authorization exists
5. Read this document fully before writing any KB article

---

### Phase 1 Task List

Each task is a specific KB article. Tasks are ordered by priority within Phase 1.

---

#### TASK 1-A: Enrich `system_prompt.md` (13 lines → full article)

**Source to read:** `shared/reply_policy.py` (full file)
**Current state:** 13-line stub — says "no emoji, formal Bangla" only
**Required content:**
- `POLICY_VERSION = "structured_v2"` — document what this version means
- 6-section prompt structure: Section names, purpose, Bengali headers
  - `## ভূমিকা (Role)` → identity + sender-specific tone
  - `## ব্যবসায়িক নিয়ম (Business Rules)` → prohibitions + output constraints
  - `## কার্যপ্রবাহ (Workflow)` → intent-specific action instruction
  - `## জ্ঞান ও তথ্য (Knowledge)` → KB/RAG context + contact data
  - `## কথোপকথন (Conversation)` → recent history (included only if non-empty)
  - `## প্রশ্ন (User Question)` → inbound message (truncated to 400/500 chars)
- `ROLE_PROMPTS` dict: 7 roles (employee, client, new_lead, admin, vendor, partner, known_contact) — document each role's Bengali tone instruction
- `INTENT_HINTS` dict: 12 intents — document each intent's workflow instruction
- Recruitment-specific builder: `_RECRUITMENT_ROLE`, `_RECRUITMENT_RULES` (10 rules), `_RECRUITMENT_WORKFLOW` (3-step funnel)
- General vs Recruitment path distinction — when each builder is called
- `clean_general_reply()` — what prefixes it strips and why
- History truncation: recruitment path 1200 chars, general path unrestricted
- Source parameter: NEVER changes prompt content — logged only
- Visibility: Internal/Developer only

**Completion criterion:** Article ≥ 60 lines; all 6 sections documented with actual Bengali text; POLICY_VERSION noted; source `shared/reply_policy.py` cited.

---

#### TASK 1-B: Enrich `workflow_engine.md` (13 lines → full article)

**Source to read:** `modules/message_router/__init__.py` (581 lines)
**Current state:** 13-line stub — concept only
**Required content:**
- 15-step routing priority (read from actual code — do NOT reconstruct from memory)
- `_SAFE_AUTOSEND_INTENTS` frozenset: list all 8 intents exactly
- `DRAFT_ALWAYS_ROLES` set: list all roles that always generate drafts
- `REPLY_COOLDOWN = 60` seconds: per-sender anti-spam cooldown
- `_ESCORT_ROLES` frozenset: 4 roles that trigger escort path
- Office-location fast path (Step 12): KB-only reply, no LLM call
- Complaint-phrase guard: specific phrases → forced draft regardless of intent
- Recruitment block for operational roles: recruitment intent + operational role → silent return
- Step 14: reviewed_reply_memory lookup (before LLM, after KB)
- Step 15: RAG enrichment of db_ctx for general fallback (k=3)
- DRAFT_ALWAYS_PHONES: note existence (do NOT list phone numbers in KB — security risk)
- Visibility: Developer/Admin only

**Completion criterion:** Article ≥ 70 lines; all 15 steps documented; exact frozenset/set values from code; REPLY_COOLDOWN noted; `_ESCORT_ROLES` listed.

---

#### TASK 1-C: Complete `visibility_rules.md` (12 lines → full article)

**Sources to read:**
- `modules/rag/__init__.py` — `safe_for_customer` filter logic (build + query time)
- `knowledge_base/ai_access_matrix.md` (root-level file — read and incorporate)
- `03_ai_identity/permission_matrix.md` (current state)
- `shared/reply_policy.py` — which roles access which builders
**Current state:** 12-line stub with no enforcement mechanism
**Required content:**

KB folder access matrix (implement exactly as follows):

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

- `safe_for_customer=True` mechanism: how it works at BM25 build time AND Qdrant query time
- Current enforcement gap: RAG `search()` has no `role` parameter — visibility is binary (safe/unsafe), not role-based
- Article-level visibility flags: which specific articles within `01_employee_knowledge/` are candidate-safe
- Information that must NEVER reach candidates: salary negotiation figures, transport cost tables, FPE financial data, admin phone numbers, developer system internals
- Future enforcement (Phase 3): role parameter in `search()` API

**Completion criterion:** Article ≥ 70 lines; matrix present; current enforcement gap documented; safe_for_customer mechanism explained with code reference; future enhancement path documented.

---

#### TASK 1-D: Create `escort_roster_system.md` (new article)

**Source to read:** `modules/escort_roster/` (all files: `__init__.py`, `db.py`, `calculations.py`, `extractor.py`, `history_sync.py`, `routes.py`) — 3111 total lines
**Current state:** No dedicated article
**Required content:**
- Escort roster purpose and relationship to `wbom_escort_programs` (parent) and `escort_roster_entries` (child)
- Sync flow: `sync_program_to_roster(program_id)` — upsert from programs to roster entries
- Pay calculation: `calculate_pay()` — formula (read from `calculations.py`)
- Conveyance rate table: `_get_conveyance_for_destination()` — exact rates per destination
- Shift rate: `_get_shift_rate()` — how daily rates are retrieved
- Roster entry lifecycle: `sync → recalculate → audit log` entry in `escort_roster_audit_logs`
- Draft entry cleanup pipeline: 3 jobs:
  - `cleanup_draft_entries`: removes incomplete draft entries
  - `expire_stale_drafts(hours=48)`: expires roster drafts after 48h (different table from reply drafts)
  - `cleanup_junk_drafts`: removes orphaned entries
- `history_sync_worker`: backfill purpose and completion criteria
- Remarks field: fault-tolerant JSON parsing (may contain clean JSON, corrupted JSON, or plain text)
- TTL distinction: `escort_roster_entries` draft TTL = 48h; `fazle_draft_replies` TTL = 24h (DRAFT_TTL_HOURS). These are DIFFERENT tables. Documenting this difference resolves the CR-04 documentation requirement.
- Visibility: Developer/Admin only (financial calculation internals)

**Completion criterion:** Article ≥ 80 lines; pay formula from actual code; conveyance table with exact values; 3 cleanup jobs documented; TTL distinction clear.

---

#### TASK 1-E: Create `distributed_architecture.md` (new article)

**Sources to read:**
- `shared/runtime_gateway.py` (614 lines) — NOT a feature-flag registry; it is a distributed node registry with heartbeat and lease management
- `shared/queue_arbiter.py` (814 lines) — PostgreSQL advisory lock + message queue lease system
- `shared/bridge_orchestrator.py` (645 lines) — multi-bridge failover and dedup

**IMPORTANT NOTE FOR AGENT:** `shared/runtime_gateway.py` is a distributed node/heartbeat manager, NOT a feature-flag registry. Read the actual source before writing. Function list from code:
- `register_node()`, `heartbeat()`, `deregister_node()`, `mark_stale_nodes()`, `get_active_nodes()`, `heartbeat_loop()`, `start_gateway()`, `stop_gateway()`

**Required content:**

**Section 1 — Multi-Instance Architecture**
- Fazle runs as multiple app instances (fazle-core, payroll-engine, escort-roster) sharing one PostgreSQL database
- `shared/runtime_gateway.py`: RuntimeNode dataclass; node registration; heartbeat loop interval; stale node detection; claim release when node dies

**Section 2 — Message Queue Arbitration (queue_arbiter.py)**
- Lease system: PostgreSQL advisory lock + `INSERT ON CONFLICT DO NOTHING`
- `LEASE_TTL_S = 120` seconds — lease auto-expires if holder crashes
- Exponential backoff: `2^(attempts-1)` seconds, capped at 3600s
- Dead-letter after MAX_ATTEMPTS
- Recovery sweep: every 60s, reclaims expired leases from crashed workers
- Dedup: completed (message_id, intent) pairs can NEVER be re-leased
- In-memory metrics: processing_latency_ms, retries, lease_conflicts

**Section 3 — Bridge Orchestrator (bridge_orchestrator.py)**
- Bridge authority hierarchy: bridge2 = primary/admin = highest; bridge1 = secondary
- Cross-bridge SHA-256 dedup: same message received on both bridges → processed once
- `HISTORICAL_CUTOFF_S` guard: messages older than cutoff never generate drafts
- Outage buffer: per-bridge send queue with exponential backoff; messages drained on reconnect
- RTT sampling: latency metrics per bridge

**Section 4 — Security Note**
- Admin phone number `8801880446111` appears hardcoded in bridge_orchestrator — this number must NEVER appear in any KB article accessible by RAG (confidential admin identity)
- Key management policy for X-Internal-Key headers: document rotation policy here once confirmed

**Visibility:** Developer only — this article contains infrastructure architecture; NOT for RAG indexing (safe_for_customer=False)

**Completion criterion:** Article ≥ 90 lines; 3 modules covered; LEASE_TTL_S exact; admin phone noted as confidential but number itself NOT written in the article body; bridge hierarchy documented.

---

#### TASK 1-F: Document `recruitment_flow` vs `recruitment_ai` Disambiguation

**Sources to read:**
- `modules/recruitment_flow/__init__.py` (365 lines)
- `modules/recruitment_ai/__init__.py` (253 lines)
- `knowledge_base/05_workflows/recruitment_workflow.md` (current state)

**Current state:** `recruitment_workflow.md` describes only the session funnel. KB does not distinguish the two systems. Engineers/agents reading KB will not know that two separate paths exist.

**Action:** Update `05_workflows/recruitment_workflow.md` (or create a separate disambiguation section) to document:
- System A — `recruitment_flow` (session funnel):
  - Triggered when: `INTAKE_KEYWORDS` detected AND no active session exists
  - 6-step sequence: name → age → area → job_preference → experience → phone_confirm
  - Uses template questions (no LLM)
  - SESSION_TTL = 24 hours
  - `VALID_POSITIONS` dict: 9 canonical job titles
  - `OPERATIONAL_ROLES + OPERATIONAL_INTENTS` blocked from this funnel

- System B — `recruitment_ai` (direct LLM reply):
  - Triggered when: recruitment intent detected in `message_router` AND no active `recruitment_flow` session
  - 3-step: RAG enrichment (k=5) → build structured prompt → LLM → quality gate
  - `_deterministic_fact_reply()` fast-path: bypasses LLM for address/contact/age queries
  - `_FEE_PHRASES` list: 14 phrases that trigger fee-handling branch

- Decision tree: which system handles which message
- The two systems do NOT conflict because `recruitment_flow` runs first in the router

**Completion criterion:** Updated article clearly distinguishes both paths; decision tree present; exact trigger conditions from code; INTAKE_KEYWORDS count (28) noted; VALID_POSITIONS count (9) noted.

---

#### TASK 1-G: Create or update `admin_financial_operations.md` (new article)

**Source to read:** `modules/admin_transactions/__init__.py` (550 lines)
**Current state:** `fpe_overview.md` covers FPE architecture; CRUD transaction rules are absent
**Required content:**
- Smart employee matching: 4-rule resolution algorithm (read exact rules from `resolve_or_create_employee()`):
  - Rule A: exact `employee_id_phone` match
  - Rule B: `payout_phone` match
  - Rule C: fuzzy name match with confidence > 0.95
  - Rule D: auto-create if no match found (non-fatal)
- Soft-delete policy: NEVER hard-delete financial history; `deleted_at` / `deleted_by` timestamps
- Amount change → ledger recalculation trigger (automatic, not manual)
- `employee_id_phone` immutability: identity anchor, cannot be changed after creation
- `X-Internal-Key` header: required for all mutations; document key lifecycle policy
- Visibility: Admin/Developer only — financial transaction data

**Completion criterion:** Article ≥ 50 lines; 4-rule algorithm present with rule labels A/B/C/D; soft-delete documented; immutability rule documented.

---

### Phase 1 Completion Criteria

All 7 tasks complete when:
- [ ] `system_prompt.md`: ≥ 60 lines, all 6 sections, POLICY_VERSION, ROLE_PROMPTS (7), INTENT_HINTS (12)
- [ ] `workflow_engine.md`: ≥ 70 lines, 15 steps, all frozensets, REPLY_COOLDOWN
- [ ] `visibility_rules.md`: ≥ 70 lines, role × folder matrix, safe_for_customer mechanism
- [ ] `escort_roster_system.md`: ≥ 80 lines, pay formula, conveyance table, 3 cleanup jobs, TTL distinction
- [ ] `distributed_architecture.md`: ≥ 90 lines, 3 modules, LEASE_TTL_S, bridge hierarchy
- [ ] `recruitment_workflow.md`: updated with disambiguation section, decision tree, trigger conditions
- [ ] `admin_financial_operations.md`: ≥ 50 lines, 4-rule algorithm, soft-delete, immutability
- [ ] All 7 articles committed and pushed
- [ ] `module_alignment_report.md` supplemental table updated with new coverage %

---

## PHASE 2 — Identity Brain Integration

**Objective:** Unify Role + Permission + Visibility + Business Rules + Knowledge Access + Workflow Access into a single coherent Identity Brain that governs what each sender type can access, trigger, and receive.
**Owner:** AI Agent (KB documentation only in Phase 2; production code changes deferred to Phase 3)
**Management Approval Required:** YES — Phase 2 authorization from management before starting
**Prerequisite:** Phase 1 complete

---

### Current Identity Brain State (Verified)

From `module_alignment_report.md` Module 2 (identity_brain, 65% coverage):

What exists in KB:
- `identity_brain.md` — 11-step identity resolution algorithm documented
- `permission_matrix.md` — 37 admin commands + RBAC documented
- `identity_overview.md` — high-level AI identity overview

What is MISSING from KB (from module reading of `modules/identity_brain/__init__.py`, 393 lines):
- `_CANDIDATE_KEYWORDS` list: 10 Bengali/English terms that trigger candidate classification
- `_ESCORT_CONTENT_RE` pattern: regex that triggers `client_escort_buyer` even for unregistered senders
- Confidence score mapping: how numeric confidence scores map to identity decisions
- `_ROLE_PRIORITY` numeric weights: how conflicting signals are resolved
- Step 10 behavior: candidate keyword detection runs BEFORE DB lookup — override logic unclear in KB

### Phase 2 Task List

---

#### TASK 2-A: Complete `identity_brain.md` Missing 35%

**Source to read:** `modules/identity_brain/__init__.py` (393 lines) — read completely
**Required additions:**
- `_CANDIDATE_KEYWORDS` exact list (10 terms) — copy from code
- `_ESCORT_CONTENT_RE` pattern description (not the raw regex — describe what vessel keywords trigger what role)
- Confidence score thresholds: what score triggers which identity decision
- `_ROLE_PRIORITY` weight table: which signals override which
- Step 10 clarification: keyword detection vs DB lookup — which runs first and which overrides

**Completion criterion:** identity_brain.md covers the full 11 steps AND the 4 missing hidden rules above.

---

#### TASK 2-B: Enrich `visibility_rules.md` with Enforcement Plan (Phase 3 Design)

**Note:** Phase 1 Task 1-C documents the CURRENT visibility enforcement (safe_for_customer binary filter). Phase 2 Task 2-B adds the DESIGN for role-based filtering that will be implemented in Phase 3.

**Required content additions:**
- RAG `search()` API current signature: `await search(q, k=5, min_score=0.0)` — no role parameter
- Proposed Phase 3 signature: `await search(q, k=5, min_score=0.0, role="candidate")` — role-aware filtering
- How role-based filtering would work: KB article metadata includes `allowed_roles` field; RAG filters by this field
- Which KB articles need `allowed_roles` metadata tags before Phase 3 can be implemented
- Required new `safe_for_customer` sub-categories: `candidate_safe`, `employee_safe`, `admin_only`, `developer_only`

**Completion criterion:** visibility_rules.md has a "Phase 3 Design" section with proposed API signature, filtering mechanism, and metadata requirements.

---

#### TASK 2-C: Create `identity_integration.md` (new article)

**Purpose:** Unified reference showing how all 6 identity dimensions interact for each sender type.

**Format:** Decision table per sender type:

For each of: Candidate | Employee | Supervisor | Admin | Developer

| Dimension | Candidate | Employee | Supervisor | Admin |
|---|---|---|---|---|
| Identified by | _CANDIDATE_KEYWORDS or recruitment session | wbom_employees phone | supervisor flag in wbom_employees | ADMIN_NUMBERS or RBAC role |
| KB folders accessible | 01 (limited), 04 (limited), 05 (recruitment only) | 01, 04, 05 | 01, 02 (limited), 04, 05 | all except 06 |
| Workflows can enter | recruitment_flow | attendance, payment_verification | attendance (supervisor path) | all |
| Business rules that apply | BR-25 (age 18–55), fee policy | payroll, attendance deduction, escort duty | supervisor attendance rules | all |
| LLM builder used | build_whatsapp_recruitment_policy() | build_whatsapp_reply_policy() | build_whatsapp_reply_policy() | admin_commands |
| Draft or auto-send | DRAFT (DRAFT_ALWAYS_ROLES) | depends on AUTO_REPLY_ENABLED | DRAFT | direct commands |

**Completion criterion:** Article ≥ 60 lines; decision table present; all 6 dimensions documented for at least 4 sender types; cross-references to identity_brain.md and visibility_rules.md.

---

### Phase 2 Completion Criteria

- [ ] `identity_brain.md`: all 11 steps + 4 hidden rules + confidence scores + _ROLE_PRIORITY
- [ ] `visibility_rules.md`: Phase 3 design section added; proposed API signature documented
- [ ] `identity_integration.md`: new article, ≥ 60 lines, 6-dimension decision table
- [ ] All 3 articles committed and pushed

---

## PHASE 3 — Visibility Engine (Production Code Change)

**Objective:** Enforce role-based KB access in the RAG query path, so candidates cannot receive knowledge intended for admins or developers.
**Owner:** Developer + Management
**Management Approval Required:** YES — explicit production code change authorization required
**Prerequisite:** Phase 2 complete; management authorization for production code change

---

### Current Enforcement Gap (Verified)

From `modules/rag/__init__.py`:
- `safe_for_customer=True` filter exists — enforced at BM25 build AND Qdrant query time
- BUT: `search()` API has no role parameter — it cannot distinguish candidate from employee from admin
- All "safe_for_customer" content is equally accessible to all roles
- Role-based filtering does NOT currently exist

### Phase 3 Production Changes (Subject to Management Approval)

**Change 3-A: KB Article Metadata Tagging**
- Add `allowed_roles` metadata to all KB articles in `fazle_knowledge_base` DB table
- Roles: `candidate`, `employee`, `supervisor`, `admin`, `developer`
- Tag each existing article per the visibility matrix in `visibility_rules.md`

**Change 3-B: RAG `search()` API Extension**
- File: `modules/rag/__init__.py`
- Current: `async def search(q, k=5, min_score=0.0) -> list[dict]`
- Proposed: `async def search(q, k=5, min_score=0.0, role: str = "candidate") -> list[dict]`
- Apply `allowed_roles` filter at both BM25 and Qdrant paths
- Backward compatible: `role="candidate"` is the most restrictive default

**Change 3-C: Call Site Updates**
- `modules/recruitment_ai/__init__.py`: pass `role="candidate"` to `_safe_rag_chunks()`
- `modules/message_router/__init__.py` (Step 15): pass caller's resolved role to `rag.search()`
- `shared/reply_policy.py`: no change (prompt builder does not call RAG directly)

**Change 3-D: Qdrant Payload Update**
- `build_index()`: when upserting points to Qdrant, add `allowed_roles` to payload
- Vector query filter: add `allowed_roles` must-contain filter to Qdrant query

### Phase 3 KB Documentation Requirements

Before any code change, write to `visibility_rules.md`:
- Exact API change (old → new signature)
- Allowed roles list and their definitions
- What happens on role mismatch (search returns empty, no error)
- Rollback: `role="candidate"` default preserves current behavior if flag set wrong

### Phase 3 Completion Criteria

- [ ] Management authorization recorded in `management_decisions.md`
- [ ] `visibility_rules.md` updated with final API design before any code change
- [ ] `fazle_knowledge_base` DB rows tagged with `allowed_roles`
- [ ] `modules/rag/__init__.py` `search()` extended with role parameter
- [ ] All 3 call sites updated
- [ ] All tests pass after change
- [ ] Committed and pushed

---

## PHASE 4 — Knowledge Coverage 90%+

**Objective:** Systematically close P2 and P3 coverage gaps until weighted coverage reaches ≥ 90%.
**Owner:** AI Agent (KB-only)
**Management Approval Required:** YES — Wave-4 authorization
**Prerequisite:** Phase 1 complete; Phase 2 complete

---

### Coverage Gap to Close

Current: ~25% → Target: ≥ 90%
Gap: ~65 percentage points across 52+ modules

### Phase 4 Article Work Plan

**Group A — P2 modules (15 articles)**

| Module | Current Coverage | Target Article | Lines Needed |
|---|---|---|---|
| escort (lifecycle) | 40% | Update `escort_workflow.md` with [RELEASE CONFIRMED] exact text, remarks JSON (8 fields), status transitions | +40 lines |
| bridge_poller | 20% | Create `bridge_poller.md` — ingest pipeline, REPLY_COOLDOWN, LID resolution, complaint-phrase guard, DM-always rule | +80 lines |
| fazle_payroll_engine | 35% | Update `fpe_overview.md` — ai_enhance_parse() (Ollama at confidence <0.7), authorized phone behavior, FPE_CASH_AUTHORIZED_PHONES policy | +60 lines |
| knowledge_base module | 30% | Update `rag_strategy.md` — hardcoded fallback templates (10 entries), joining fee ৳3,500 management approval status, template coupling risk | +40 lines |
| drafts | 5% | Create `draft_management.md` — draft state machine, approve_bypasses_AUTO_REPLY_ENABLED rule, 4 admin actions | +50 lines |
| wa_chat_frontend | 0% | Create `admin_ui.md` — 28 endpoints, SSE real-time stream, group broadcast, cursor pagination | +80 lines |
| admin_employees | 15% | Update `admin_operations_overview.md` — 13 EmployeeCreate fields, mobile immutability, FPE auto-seed, soft deactivation | +40 lines |
| reports | 0% | Create `admin_reports.md` — 6 report builders, 10-min cache, daily digest job | +50 lines |
| outbound | 10% | Update `automation_pipeline.md` — OUTBOUND_ENABLED kill-switch, backoff formula, DLQ threshold, circuit breaker | +30 lines |
| employee_verification | 15% | Update `payment_workflow.md` — 5-step verification funnel, release slip fast-path, fazle_draft_replies dual use | +50 lines |
| contact_sync | 0% | Create `contact_sync.md` — 3-source merge, display_name priority, LID JID handling, BD-only normalization | +50 lines |
| ocr_processor | 35% | Update `ocr_engine.md` — confidence threshold, document types (release slip/bank stmt/bkash screenshot), routing decision | +40 lines |
| phone_normalizer | 45% | Create `06_developer_system/phone_normalizer.md` — canonical format (8801XXXXXXXXX), VALID_OPERATORS set (11–19), hyphen/+880 acceptance | +40 lines |
| self_heal | 0% | Create `system_resilience.md` — 6 monitored conditions, 6 recovery actions, pressure score (0.0–1.0), throttle behavior | +60 lines |
| user_role | 0% | Create `user_role.md` — self-registration flow, role assignment from WhatsApp | +40 lines |

**Group B — Database deep documentation**

| Area | Current State | Action |
|---|---|---|
| 43+ database tables | `database_rules.md` enriched in Wave-2 (515 lines added) — domain assignments done | Verify all 43 tables are present; add DDL-level field descriptions for top-10 tables |
| FPE tables (5 workers, 4 tables, FSM) | `fpe_overview.md` covers architecture (35%) | Add 4 table schemas and FSM state diagram for fpe_transactions |
| Payroll tables | 5% coverage | Add `wbom_payroll_runs` schema, 5-state machine, ALLOWED_TRANSITIONS |
| Recruitment tables | Partial | Add `fazle_recruitment_sessions` schema, SESSION_TTL |

**Group C — Admin commands complete documentation**

| Area | Current State | Action |
|---|---|---|
| NL query categories | `admin_commands` 50% | Document nl_router.py categories + date_parser supported formats |
| Bangla numeral support | Not documented | Document APPROVE ১৬৫ → APPROVE 165 conversion |
| Scheduler commands | Not documented | Document SCHEDULE STATUS output format, RUN JOB list |

**Group D — Social Auto Reply depth**

| Area | Current State | Action |
|---|---|---|
| risk_flagger.py escalation thresholds | 40% | Document exact thresholds |
| state_tracker.py state machine | Not documented | Document transitions |
| payment_issue_handler flow | Not documented | Document comment → payment complaint path |
| Salary figure management approval | FLAGGED as unapproved | Obtain management decision before documenting |

---

### Phase 4 Completion Criteria

- [ ] All 15 P2 module articles written (Group A)
- [ ] Database documentation completed for top 10 tables (Group B)
- [ ] Admin command NL router documented (Group C)
- [ ] Social auto-reply depth complete (Group D) — pending salary figure management decision
- [ ] Weighted KB coverage: ≥ 80% (measured by running PKCA)
- [ ] All articles committed and pushed

---

## PHASE 5 — Production Knowledge Freeze v2 (Validation Gate)

**Objective:** Run PKCA, PKMA, and PKVC validation programs against the enriched KB and confirm all targets are met before declaring KB v2.
**Owner:** AI Agent (read-only validation)
**Management Approval Required:** YES — KB v2 certification authority
**Prerequisite:** Phase 4 complete

### Target Metrics

| Metric | Target | Measurement Method |
|---|---|---|
| Coverage | ≥ 90% | PKCA program: module × KB coverage matrix |
| Maturity | ≥ 4/5 per article | PKMA program: article quality scoring |
| Conflicts | 0 | PKVC program: cross-reference consistency check |
| Critical Gaps | 0 | PKCA: no P0/P1 module below 70% |
| Brain Readiness | ≥ 80% (GOOD) | Readiness score calculation |

### Phase 5 Process

1. AI agent reads ALL KB articles in sequence (not from memory)
2. Runs PKCA: for each production module, score KB coverage 0–100%
3. Runs PKMA: for each KB article, score maturity (Accuracy/Completeness/Clarity/Currency/Consistency)
4. Runs PKVC: cross-reference all factual claims against production code — identify any remaining conflicts
5. Produces PKCA/PKMA/PKVC v2 reports in `07_archived/`
6. If any target is missed → return to Phase 4 and address specific gaps
7. If all targets met → present KB v2 certification proposal to management

### Phase 5 Completion Criteria

- [ ] PKCA v2 report produced: coverage ≥ 90% confirmed
- [ ] PKMA v2 report produced: average maturity ≥ 4/5
- [ ] PKVC v2 report produced: conflicts = 0
- [ ] Brain Readiness score ≥ 80% (GOOD)
- [ ] Management approves KB v2 certification
- [ ] `management_decisions.md` updated with KB v2 certification entry
- [ ] KB v2 governance record created in `07_archived/`

---

## PHASE 6 — Production Refactoring (Conditional)

**Objective:** Align production code with KB v2 where verified mismatches exist and management has authorized specific changes.
**Owner:** Developer (AI Agent assistance only)
**Management Approval Required:** YES — per-module refactoring authorization required
**Prerequisite:** Phase 5 complete; explicit management authorization per module

### Activation Conditions (ALL must be true)

```
1. KB v2 is certified (Phase 5 complete)
2. A specific KB article says X
3. Production code says Y (not X)
4. The divergence causes a real problem (not theoretical)
5. Management explicitly authorizes the specific change
```

If any condition is false: NO CODE CHANGE.

### Refactoring Candidates (from current audit — to be re-verified at Phase 6 start)

The following are candidate mismatches from the module_alignment_report.md. Each must be re-verified at Phase 6 start against the actual production code at that time:

| Module | Potential Mismatch | Verification Required |
|---|---|---|
| identity_brain | KB Step 10 = DB lookup; production may run keyword check first | Re-read `modules/identity_brain/__init__.py` at Phase 6 start |
| recruitment_flow | KB describes session funnel as sole path; production has dual path | Verify dual-path documentation is in KB v2 before considering change |
| knowledge_base module | Hardcoded fallback templates may diverge from KB figures over time | Compare template contents against KB v2 at Phase 6 start |
| payroll_logic | Core formula undocumented — if formula is wrong, refactor needed | Read `modules/payroll_logic/__init__.py` at Phase 6 start |

### Refactoring Prohibited Unless Authorized

- No "cleanup" refactoring without specific KB/production conflict
- No architectural changes without management approval
- No module restructuring without Phase 5 complete
- No refactoring of DORMANT modules (payment_correction, conversation_layer) without activation decision

---

## Agent Execution Protocol (All Phases)

Every AI agent beginning work on any phase MUST:

**Step 1 — Read governance:**
```
Read: /home/azim/core/knowledge_base/00_governance/README.md
Read: /home/azim/core/knowledge_base/00_governance/management_decisions.md
Read: /home/azim/core/knowledge_base/00_governance/master_execution_plan_v2.md (this document)
```

**Step 2 — Verify current state:**
```bash
git log --oneline -5                                    # confirm recent commits
wc -l knowledge_base/06_developer_system/system_prompt.md   # confirm Phase 1 status
cat .env | grep HYBRID_SEARCH_ENABLED                   # confirm RAG live
python3 -m pytest tests/unit/test_reply_policy.py -q    # confirm tests passing
```

**Step 3 — Confirm authorization:**
- Check `management_decisions.md` for the specific phase authorization entry
- If authorization entry not present: STOP, present proposal to management

**Step 4 — Read before writing:**
- For every KB article task: read the SOURCE PRODUCTION FILE first, then read the CURRENT KB article, then write the enriched article
- Never write from memory or session summaries

**Step 5 — KB-only in Phases 1–2:**
- No production code changes in Phases 1 or 2
- Production code changes only in Phases 3 and 6, and only with management authorization

**Step 6 — Commit atomically:**
- Phase 1: one commit per batch of 3–4 articles
- Phase 3: one commit per production file changed (for clean rollback)
- Always push after committing
- Rule: NEVER pull, only push (VPS server rule)

**Step 7 — Update governance trail:**
- After each phase: update `management_decisions.md` with phase completion entry
- After each phase: update `module_alignment_report.md` with new coverage %
- After each phase: update this document's Status field

---

## Progress Tracker

| Phase | Status | Started | Completed | Notes |
|---|---|---|---|---|
| PHASE 1 — Priority Gap Remediation | ⏳ READY | — | — | Awaiting management authorization |
| PHASE 2 — Identity Brain Integration | ⏳ PENDING | — | — | After Phase 1 |
| PHASE 3 — Visibility Engine | ⏳ PENDING | — | — | Requires production code authorization |
| PHASE 4 — Coverage 90%+ | ⏳ PENDING | — | — | After Phase 2 |
| PHASE 5 — Freeze v2 | ⏳ PENDING | — | — | After Phase 4 |
| PHASE 6 — Refactoring (Conditional) | ⏳ PENDING | — | — | Conditional on Phase 5 + management approval |

**Pre-Phase Infrastructure (Confirmed Live):**
- Hybrid RAG (HYBRID_SEARCH_ENABLED=true, RRF live): ✅ 2026-06-22
- Prompt Builder (structured_v2, 6-section): ✅ 2026-06-22
- KB v1.0 Controlled Freeze: ✅ 2026-06-22
- 6 Conflicts resolved (CR-01–CR-06): ✅ 2026-06-23

---

## Quick Reference — What Each Phase Produces

| Phase | Output | KB or Code |
|---|---|---|
| Phase 1 | 7 KB articles (system_prompt, workflow_engine, visibility_rules, escort_roster, distributed_arch, recruitment_disambiguation, admin_financial) | KB only |
| Phase 2 | 3 KB articles (identity_brain complete, visibility enforcement design, identity_integration) | KB only |
| Phase 3 | Role-aware RAG search() + KB article metadata tagging + 3 call site updates | Code + KB |
| Phase 4 | 15+ KB articles (P2 modules) + database schemas + admin command completions | KB only |
| Phase 5 | PKCA/PKMA/PKVC v2 reports + KB v2 certification | Validation only |
| Phase 6 | Targeted production fixes per authorized KB/production mismatches | Code only |

---

*Master Execution Plan v2 | Created 2026-06-23 | Authority: Management*
*Based on verified production code readings — not assumptions or session summaries.*
*Source data: module_alignment_report.md, session4_audit_report_2026_06_23.md, direct code inspection*
*Next action: Present Phase 1 to management for authorization*
