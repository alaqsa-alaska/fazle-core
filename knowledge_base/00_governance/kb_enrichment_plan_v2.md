---
title: KB Enrichment Plan v2
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# KB Enrichment Plan v2
**Program:** Wave-3 Knowledge Base Enrichment
**Date:** 2026-06-22
**Prerequisite:** Management approval before any KB article is modified (per PKMA v1.0 freeze policy)
**Baseline:** KB v1.0, Wave-2 complete, Phase 4 complete (code-side)
**Target:** Brain Readiness Score 80% (GOOD) by end of Wave-3

---

## How to Use This Plan

Each item is:
1. Assigned a PRIORITY level (P0 = block all other work, P1 = must do Wave-3, P2 = should do Wave-3, P3 = backlog)
2. Classified as ENRICH (existing stub article needs content) or CREATE (new article needed)
3. Given an estimated author effort (Small < 2h, Medium 2–4h, Large > 4h)
4. Mapped to the gap it resolves from `organizational_brain_gap_report.md`

**Conflict items (CONFLICT-1, 2, 3, 4, 5) require a management decision before any KB edit. CONFLICT-5 is a financial critical — management authorization required immediately.**

---

## P0 — BLOCK: Resolve Conflicts (Management Decision Required)

### CONFLICT-1: Draft TTL — 24h vs 48h
**Action:** Schedule management meeting. Confirm authoritative draft TTL.
- If **24h** (match production): Update `automation_pipeline.md` line "Expire old pending drafts after 48 hours" → "after 24 hours (DRAFT_TTL_HOURS=24)".
- If **48h** (match KB): Update `.env` `DRAFT_TTL_HOURS=48` and restart bridge_poller.
**Owner:** Management
**Effort:** Small (decision) + Small (KB edit after decision)

### CONFLICT-2: Hybrid RAG Algorithm Conflict
**Action:** Lock hybrid_search.md for rewrite (see PRIORITY-1 below). Remove the "5-signal ranking" description which is factually incorrect. Replace with actual RRF implementation.
**Owner:** Developer + KB Author
**Prerequisite:** Management approval to overwrite existing hybrid_search.md content

### CONFLICT-3: Age Rule (BR-25 vs Facebook Auto-Reply)
**Action:** Management to decide: enforce BR-25 (18–55) in social_auto_reply?
- If **yes**: Update `social_auto_reply/reply_rules.py` to add age restriction check, and update `social_auto_reply_system.md`.
- If **no** (allow all ages on Facebook): Document the channel exception in `social_auto_reply_system.md` and `recruitment_business_rules.md`.
**Owner:** Management
**Effort:** Small (decision) + Small (code change or KB note)

### CONFLICT-4: Dual Draft TTL Mechanisms (Clarification)
**Action:** Update `automation_pipeline.md` to distinguish the two draft systems:
- WhatsApp reply drafts (`fazle_draft_replies`): governed by `DRAFT_TTL_HOURS=24` (.env)
- Escort roster draft entries (`escort_roster_entries`): governed by `expire_stale_drafts(hours=48)` in `escort_roster/db.py`
**No code change required** — this is a documentation clarification.
**Owner:** KB Author
**Effort:** Small

### CONFLICT-5: Escort Daily Rate ৳800 vs ৳1,200 (FINANCIAL CRITICAL — Management Authorization Required)
**Action:** Management MUST decide the authoritative escort daily pay rate:
- `modules/payroll/__init__.py: DEFAULT_PER_PROGRAM_RATE = 800.0`
- `modules/payment_workflow/__init__.py: DEFAULT_DAILY_RATE = 1200.0`
- If **৳800**: Update `payment_workflow/__init__.py` constant and all KB references.
- If **৳1,200**: Update `payroll/__init__.py` constant and all KB references.
- If **DB-driven** (no hardcoded default): Remove both constants, add DB lookup.
**Owner:** Management (rate decision) + Developer (constant update) + KB Author (KB update)
**Effort:** Small (decision) + Small (code edit) + Small (KB edit)
**NOTE: This is a financial integrity issue. Do not defer beyond Wave-3 start.**

---

## PRIORITY-1 — Must Complete Wave-3

### P1-A: Enrich `hybrid_search.md` (STUB → Full Article)
**Type:** ENRICH
**Current state:** 17 lines, describes wrong algorithm
**Target:** Full technical reference for Hybrid RAG (all production-accurate details)
**Effort:** Medium

**Content to add:**

```
# Hybrid Search System

## Overview
Fazle uses Hybrid RAG: BM25 keyword search + Qdrant vector search, fused via
Reciprocal Rank Fusion (RRF).

## Environment Control
HYBRID_SEARCH_ENABLED=true (in .env) activates hybrid mode.
When false: BM25-only mode (in-process, no external dependency).

## Components

### BM25 Index
- Unicode tokenizer regex: [A-Za-z0-9ঀ-৿]+
- Chunk size: 320 tokens / 60 token overlap
- BM25 params: k1=1.5, b=0.75
- Index size: 251 documents (87 file + 164 KB, as of Wave-2)
- All chunks have safe_for_customer=True (unsafe content filtered at build time)

### Vector Search — Qdrant
- Server: Docker qdrant/qdrant:v1.17.0 at 172.20.0.2:6333
- Collection: fazle_rag_chunks
- Points: 251 (same corpus as BM25)
- Payload filter: safe_for_customer=True applied at query time

### MiniLM Encoder
- Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
- Dimensions: 384
- Supports: Bangla, English, Banglish
- LRU embedding cache: max 2000 entries, SHA1 key, thread-safe
- Executor: 1 dedicated thread (CPU-bound isolation)
- Warm-up: 27 seed queries on first build

### RRF Fusion Algorithm
scores are combined using Reciprocal Rank Fusion:
  rrf_score = (1 / (60 + rank_bm25)) + (1 / (60 + rank_vector))
where rank is 1-indexed position in each result list.
Higher score = more relevant. Results sorted descending by rrf_score.

## Fallback Behavior
If Qdrant is unavailable (server down, connection refused):
- System automatically falls back to BM25-only mode
- No error raised to callers; logged at WARNING level

## Phase 4 Integration (2026-06-22)
- Recruitment path: _safe_rag_chunks(text, k=5) enriches kb_context before LLM
- General fallback path (message_router step 15): _rag_search(text, k=3) enriches db_ctx
- Source tracing: rag_sources= field logged per message in recruitment_ai
```

---

### P1-B: Enrich `system_prompt.md` (STUB → Full Article)
**Type:** ENRICH
**Current state:** 13 lines
**Target:** Full documentation of prompt architecture, both builders, Phase 4 structured_v2 format
**Effort:** Medium

**Content to add:**

```
# System Prompt Architecture

## Policy Version
POLICY_VERSION = "structured_v2" (set in shared/reply_policy.py, Phase 4 Step 3)

## Two Prompt Builders
1. build_whatsapp_recruitment_policy() — for recruitment path
2. build_whatsapp_reply_policy() — for all other paths (general fallback)

## 6-Section Structured Format (structured_v2)
Both builders produce prompts in this exact section order:

  ## ভূমিকা (Role)
  Who Fazle is, what language to use, what persona to maintain.

  ## ব্যবসায়িক নিয়ম (Business Rules)
  Non-negotiable constraints: don't invent numbers, don't disclose restricted info,
  max reply length, no emoji, no markdown.

  ## কার্যপ্রবাহ (Workflow)          ← Only if intent_hint / recruitment steps available
  How to handle this conversation type step by step.

  ## জ্ঞান ও তথ্য (Knowledge)       ← Only if db_context or kb_context is present
  Facts retrieved from KB, RAG chunks, or contact context.
  Source: build_recruitment_source_context() + _safe_rag_chunks() for recruitment;
          get_contact_context() + _rag_search() for general path.

  ## কথোপকথন (Conversation)          ← Only if history is non-empty
  Last 1200 chars of conversation history.

  ## প্রশ্ন (User Question)
  The user's actual message (truncated to 500 chars for recruitment, 400 for general).

  [REPLY INSTRUCTION LINE]
  Recruitment: "Reply only the WhatsApp message text:"
  General:     "জবাব (বাংলা, সর্বোচ্চ ৩-৪ বাক্য):"

Sections with no content are suppressed (not included in the prompt).

## clean_general_reply()
Post-processing step on general path LLM output.
Strips artifact prefixes: "জবাব:", "উত্তর:", "Answer:", "Reply:".
Removes ``` fences.
Removes echoed ## section headers.
Falls back to original reply if cleaning produces empty string.
Applied in message_router step 15 AFTER generate_reply().

## Role Prompts (ROLE_PROMPTS dict)
7 production roles have custom identity hints injected into ## ভূমিকা:
guard, escort, employee, accountant, admin, candidate, (default)
These override the generic _GENERAL_IDENTITY string.

## Intent Hints (INTENT_HINTS dict)
12 intent categories have workflow instructions injected into ## কার্যপ্রবাহ:
attendance, escort_duty, salary_query, advance_request, complaint, recruitment, ...
When intent is detected, the matching hint is inserted as the Workflow section.
```

---

### P1-C: Create `draft_quality_gate.md` (New Article)
**Type:** CREATE
**Target path:** `06_developer_system/draft_quality_gate.md`
**Effort:** Small

**Content to create:**

```
# Draft Quality Gate

## Purpose
Before any AI-generated reply is saved as a pending draft for admin review,
it passes through a 4-criteria quality gate. Replies failing the gate are
saved with status='rejected_quality' and logged for review.

## Environment Control
DRAFT_QUALITY_GATE=true enables the gate (default in production).
DRAFT_QUALITY_GATE=false disables all checks (emergency kill-switch).

## 4 Rejection Criteria
1. empty — reply is None, empty string, or whitespace only
2. llm_fallback — reply is exactly one of the fallback strings returned when the
   LLM call fails (e.g., "দুঃখিত, এখন উত্তর দিতে পারছি না।")
3. bad_pattern — reply contains developer/system artifacts:
   file://, /home/azim, Traceback, ```, <|, /scripts/, /venv/
4. too_long — reply exceeds 4000 characters

## What Passes
- Numbered-list replies [1] ... [2] ... are explicitly allowed.
- Single-sentence safe fallbacks from enforce_recruitment_reply_policy() pass.
- Short factual deterministic replies pass.

## State Transition
draft created → check_draft_quality() → PASS: status='pending' (admin review queue)
                                       → FAIL: status='rejected_quality' (logged, not shown to admin)
```

---

### P1-D: Enrich `workflow_engine.md` (STUB → Full Article)
**Type:** ENRICH
**Current state:** 13 lines
**Effort:** Large

**Content summary (to expand):**
Full 15-step message routing table with: step number, condition, action, destination.
Silent-skip logic (11 _SILENT_SKIP_NAME_TOKENS). Complaint-phrase guard. DRAFT_ALWAYS_ROLES set. Office location fast path (Step 12 — KB reply, no LLM). Reviewed reply memory lookup (Step 14). AI fallback RAG enrichment (Step 15). REPLY_COOLDOWN = 60s.

### P1-E: Create `escort_roster_system.md` (New Article — HIGHEST PRIORITY new item)
**Type:** CREATE
**Target path:** `06_developer_system/escort_roster_system.md`
**Effort:** Large (3111-line module, most complex undocumented module)

**Content to cover:**
- `sync_program_to_roster()` — upsert flow from `wbom_escort_programs` → `escort_roster_entries`
- Pay calculation: `calculate_pay()`, `parse_date_shift()`, conveyance rate table
- Draft lifecycle: three cleanup jobs (`expire_stale_drafts(hours=48)`, `cleanup_draft_entries()`, `cleanup_junk_drafts()`)
- Roster states: draft → confirmed → closed
- Distinction: escort roster drafts (48h) ≠ WhatsApp reply drafts (24h) — resolves CONFLICT-4 ambiguity
- `escort_roster_audit_logs` table structure
- After CONFLICT-5 is resolved: include authoritative pay rate

### P1-F: Create `runtime_gateway_flags.md` (New Article)
**Type:** CREATE
**Target path:** `06_developer_system/runtime_gateway_flags.md`
**Effort:** Medium

**Content to cover:**
- All feature flags in `shared/runtime_gateway.py` (614 lines of live configuration)
- Kill-switch registry with: flag name, env var, default, effect when disabled
- Safe toggle procedure (which flags require restart vs live-reload)
- Emergency disable sequence for common incident types

**Priority justification:** Undocumented kill-switches are an incident-response blocker. Engineers cannot disable features during incidents without source-code spelunking.

---

## PRIORITY-2 — Should Complete Wave-3

### P2-A: Enrich `visibility_rules.md` (STUB → Full Article)
**Type:** ENRICH
**Current state:** 12 lines
**Effort:** Medium

**Content to add:**
3-level visibility model:
- **Public (safe_for_customer=True):** Recruitment info, office address, general policies
- **Internal:** Salary amounts (guard ৳17,000/৳24,700), daily rate (৳1,200), joining fee, form fee — visible to employees/admin only
- **Restricted:** FPE financial transactions, DRAFT_ALWAYS_PHONES, authorized phone lists, admin command outputs — never exposed to candidates or external contacts

Role → visibility matrix (7 roles × 3 levels).
Enforcement mechanism: safe_for_customer filter in RAG index, RBAC in admin_commands, visibility check in social_auto_reply.

---

### P2-B: Document `reviewed_reply_memory`
**Type:** CREATE
**Target path:** `06_developer_system/reviewed_reply_memory.md`
**Effort:** Small

**Content summary:** Admin approves or edits a draft → reply saved as a "reviewed reply" → future messages from same phone with same intent + role retrieve the cached reply (skip LLM). TTL, match scope (phone + intent + role hierarchy), kill-switch REVIEWED_REPLY_MEMORY_ENABLED.

---

### P2-C: Enrich `automation_pipeline.md` — Scheduler Jobs
**Type:** ENRICH (add section to existing article)
**Effort:** Small

**Content to add:**
Exact cron schedule for all 8 scheduled jobs:
| Job | Schedule |
|---|---|
| daily_payroll_compute | 02:00 Asia/Dhaka daily |
| dlq_alert | every 15 minutes |
| health_summary | every 6 hours |
| stale_escort_reminder | 09:00 daily |
| payment_reconciliation | every 1 hour |
| backup_staleness_alert | 03:00 daily |
| daily_memory_review | 09:00 daily |
| rag_rebuild | 18:00 daily |

SCHEDULER_ENABLED kill-switch. SCHEDULER_TIMEZONE=Asia/Dhaka override. `RUN JOB <name>` manual trigger commands.
**Also fix CONFLICT-1:** Update draft TTL after management decision.

---

### P2-D: Document Hardcoded Financial Constants (Management Authorization Required)
**Type:** ENRICH (add to `payment_business_rules.md`)
**Effort:** Small (after management approval)

Management must approve each value before KB inclusion:
- `DEFAULT_DAILY_RATE = ৳1,200/day` for escort duty pay calculation
- Joining fee `৳3,500` (in knowledge_base module _FALLBACK templates)
- Form fee `৳330` (same)

Until approved: add a note in each relevant article: "Authoritative value pending management confirmation."

---

### P2-E: Enrich `escort_workflow.md` — Exact Trigger Rules
**Type:** ENRICH
**Effort:** Small

**Content to add:**
- `[RELEASE CONFIRMED]` — exact text string required to complete an escort release. No variations accepted.
- `remarks` JSON structure (8 fields): sender_phone, source_bridge, escort_name, escort_mobile, capacity, importer, cargo_type, timestamp.
- Status transition: draft → confirmed → released (3 states, what triggers each).

---

### P2-F: Create `recruitment_ai_detail.md` (New Article)
**Type:** CREATE
**Target path:** `06_developer_system/recruitment_ai_detail.md`
**Effort:** Medium

**Content to cover:**
- `_deterministic_fact_reply()` — contact/address/age bypass LLM (3 fast paths)
- `build_recruitment_source_context()` — section scoring algorithm (overlap + title overlap × 4 + fee bonus + contact bonus), top-4 selection, [:4500] truncation
- `_safe_rag_chunks()` — Phase 4 RAG enrichment, source tracing, k=5, never raises
- `enforce_recruitment_reply_policy()` — number guard (reply numbers ⊆ source numbers), place hallucination guard (inflection suffix stripping)
- `_FEE_PHRASES` — 16 fee detection phrases
- `_QUESTION_HINTS` — 12 follow-up detection phrases

### P2-G: Create `admin_ui.md` (New Article — from Extension Audit)
**Type:** CREATE
**Target path:** `06_developer_system/admin_ui.md`
**Effort:** Medium

**Content to cover:**
- All 28 REST endpoints under `/api/wa/` and `/api/admin/` from `modules/wa_chat_frontend`
- SSE real-time stream (`/api/wa/stream`) — event types: `new_message`, `new_draft`
- Cursor pagination for messages (not offset pagination)
- Group broadcast: individual sends per member (not WA group send)
- Per-number auto-reply block: endpoint behavior and persistence
- Auth: X-Internal-Key header requirement

### P2-H: Create `admin_transactions_rules.md` (New Article — from Extension Audit)
**Type:** CREATE
**Target path:** `02_admin_knowledge/admin_transactions_rules.md`
**Effort:** Small

**Content to cover:**
- NEVER hard-delete financial records (soft delete via deleted_at + deleted_by columns)
- Smart employee matching — 4-rule algorithm:
  - Rule A: exact `employee_id_phone` match
  - Rule B: `payout_phone` match
  - Rule C: fuzzy name match (confidence ≥ 95%)
  - Rule D: auto-create FPE employee record
- Amount or period edit triggers automatic ledger recalculation
- `employee_id_phone` is immutable after creation (identity anchor)
- X-Internal-Key authentication required for all mutations

### P2-I: Enrich `automation_pipeline.md` — Queue Arbitration + Self-Heal + Bridge Failover
**Type:** ENRICH
**Target path:** `06_developer_system/automation_pipeline.md`
**Effort:** Medium

**Content to add:**
- `shared/queue_arbiter.py`: lease system, LEASE_TTL_S=120, multi-instance architecture (fazle-core + payroll-engine + escort-roster share queue), DLQ after MAX_ATTEMPTS, deduplication guarantee for completed leases
- `shared/self_heal.py`: 6 monitored conditions, 6 recovery actions, pressure score [0.0–1.0], throttle behavior
- `shared/bridge_orchestrator.py`: bridge authority hierarchy (bridge2 highest), failover sequence, HISTORICAL_CUTOFF_S message age guard, outage buffer + replay
- Dual draft system clarification (CONFLICT-4 resolution): escort roster drafts (48h, `escort_roster_entries`) vs reply drafts (24h, `fazle_draft_replies`)

### P2-J: Create `phone_normalizer.md` in KB Directory (from Extension Audit)
**Type:** CREATE
**Target path:** `06_developer_system/phone_normalizer.md`
**Effort:** Small

**Source:** Content already exists at `core/PHONE_NORMALIZER_CONVENTION.md` (root-level). This file is OUTSIDE the `knowledge_base/` directory and is not indexed by RAG.

**Content to include:**
- Canonical format: 13-digit `8801XXXXXXXXX`
- VALID_OPERATORS set: {11, 12, 13, 14, 15, 16, 17, 18, 19}
- Accepted input formats: `+8801`, `8801`, `01`, `1`, hyphenated (01812-345678)
- Non-BD numbers return None silently
- LID JIDs (@lid suffix) are not processed

### P2-K: Create `recruitment_flow_system.md` (from Extension Audit)
**Type:** CREATE
**Target path:** `06_developer_system/recruitment_flow_system.md`
**Effort:** Small

**Content to cover:**
- Two-path architecture clarification: when does `recruitment_flow` (6-step funnel) activate vs `recruitment_ai` (LLM FAQ)?
- Trigger: INTAKE_KEYWORDS set activates funnel; all other recruitment queries go to LLM
- COLLECTION_STEPS 6-step sequence and STEP_QUESTIONS per step
- SESSION_TTL = 24h (same as reply draft TTL — coincidence, governed by different code)
- Operational role exclusion: OPERATIONAL_ROLES frozenset excluded from funnel
- VALID_POSITIONS: management to decide whether to publish (may constitute a commitment to open positions)

**Note:** Resolves the two-system conflict currently creating ambiguity for KB readers.

---

## PRIORITY-3 — Backlog (Wave-4)

| Item | Type | Effort | Description |
|---|---|---|---|
| P3-A: bridge_poller operational rules | ENRICH `automation_pipeline.md` | Small | REPLY_COOLDOWN 60s, LID resolution, dedup table, adaptive backoff |
| P3-B: intent keyword lists | CREATE `intent_keywords.md` | Medium | Full INTENT_KEYWORDS dict, 13 categories, fuzzy threshold |
| P3-C: knowledge_base module fallbacks | ENRICH `rag_strategy.md` | Small | _FALLBACK template audit, hardcoded salary/fee management approval |
| P3-D: admin NL query categories | ENRICH `admin_operations_overview.md` | Small | nl_router categories, date parser formats, Bangla numeral support |
| P3-E: attendance detection keywords | ENRICH `attendance_workflow.md` | Small | _PRESENT_KEYWORDS (12), _ABSENT_KEYWORDS (8), location regex |
| P3-F: FPE normalization rules | ENRICH `fpe_overview.md` | Small | ai_enhance_parse() (Ollama recovery at confidence < 0.7), authorized phone behavior |
| P3-G: identity_brain text_hint criteria | ENRICH `identity_brain.md` | Small | _CANDIDATE_KEYWORDS list, confidence score mapping |
| P3-H: contact_sync merge rules | CREATE `06_developer_system/contact_sync.md` | Small | 3-source merge, display_name priority, LID handling |
| P3-I: reply_templates catalogue | CREATE `06_developer_system/reply_templates.md` | Small | Template intents and rotation rule (no Bengali text needed) |
| P3-J: reports catalogue | ENRICH `02_admin_knowledge/admin_operations_overview.md` | Small | 6 report types, cache TTL (600s), daily digest schedule 09:00 |
| P3-K: outbound queue rules | ENRICH `automation_pipeline.md` | Small | OUTBOUND_ENABLED kill-switch, backoff cap, DLQ threshold |
| P3-L: drafts approve-override rule | ENRICH `automation_pipeline.md` | Small | Admin approval always sends regardless of AUTO_REPLY_ENABLED |
| P3-M: rbac security notes | ENRICH `role_permissions.md` | Small | SHA-256 key storage, audit log table, superadmin bootstrap behavior |

---

## Wave-3 Delivery Sequence

Wave-3 should proceed in this order to avoid working on articles with open conflicts:

1. **Resolve CONFLICT-5** (management decision — financial rate) → BLOCKS payroll documentation
2. **Resolve CONFLICT-1, 2, 3** (management decisions) → unblocks all P1 edits
3. **Resolve CONFLICT-4** (documentation-only clarification, no management decision needed)
4. **P1-C** (draft_quality_gate.md) → quick win, zero conflict risk
5. **P1-E** (escort_roster_system.md) → largest module, highest audit risk
6. **P1-A** (hybrid_search.md) → highest readiness impact
7. **P1-B** (system_prompt.md) → Phase 4 documentation
8. **P1-F** (runtime_gateway_flags.md) → operational kill-switches
9. **P1-D** (workflow_engine.md) → largest effort, most used by operations
10. **P2-A through P2-K** → in any order
11. **P3-A through P3-M** → Wave-4 if Wave-3 capacity exceeded

---

## Wave-3 Target Score

| Dimension | Current (Extended) | Wave-3 Target | How |
|---|---|---|---|
| Onboarding | 60% | 75% | escort_roster_system, recruitment_flow_system, workflow_engine, draft_quality |
| Incident Response | 55% | 78% | Conflict resolution (esp. CONFLICT-5), runtime_gateway_flags, bridge_orchestrator, hybrid_search |
| AI Training | 67% | 85% | hybrid_search (RRF), system_prompt (structured_v2), visibility_rules |
| Audit & Compliance | 52% | 73% | draft_quality gate, CONFLICT-5 rate resolution, admin_transactions_rules, RBAC security notes |
| Staff Turnover | 62% | 79% | reviewed_reply_memory, escort_workflow, workflow_engine, admin_ui |

**Wave-3 Target Weighted Score: 80% (GOOD)**

---

*KB Enrichment Plan v2 | Phase 4 Step 5 (Extended 2026-06-23) | Requires Management Approval*
