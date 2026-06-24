---
title: PKMA Report 18 — Developer System Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 18 — Developer System Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of every article in the `06_developer_system/` knowledge base folder. Developer system knowledge is mature when configuration, architecture, security rules, integrations, deployment, and observability are documented, production-verified, and management-approved.

---

## Developer System Article Inventory

The `06_developer_system/` folder contains 16 articles:

| Article | Wave-1 Enriched | Estimated Coverage Post-Wave-1 |
|---|---|---|
| automation_pipeline.md | Yes | ~65% |
| security_rules.md | Yes | ~70% |
| identity_brain.md | Yes | ~60% |
| developer_notes.md | Yes | ~55% |
| role_permissions.md | Yes | ~65% |
| database_rules.md | No | ~3% |
| rag_strategy.md | No | ~8% |
| conversation_parser.md | No | ~10% |
| ocr_engine.md | Partial | ~30% |
| parser_engine.md | No | ~5% |
| event_pipeline.md | No | ~5% |
| hybrid_search.md | No | ~5% |
| system_prompt.md | No | ~5% |
| visibility_rules.md | No | ~5% |
| workflow_engine.md | No | ~5% |
| message_routing.md | No | ~15% |

---

## Per-Article Maturity Assessment

---

## DEV-01 — automation_pipeline.md

**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| LLM Reply Chain | Yes (GitHub→Groq→Ollama) |
| LLM Intent Chain | Yes (Groq→GitHub→Ollama) |
| All 15 Scheduler Jobs | Yes (complete table) |
| Outbound Queue State Machine | Yes (5 states) |
| DLQ Alert Behavior | Yes |
| Automated Reply Suffix | Yes |
| Idempotency | Yes |
| Missing | Draft quality gate (4 criteria), circuit breaker pattern, memory extractor fire-and-forget |
| Production Verified | Yes (Wave-1) |
| Management Decision | None for pipeline architecture as a whole |

**Gap to Level 3:** No management decision approving the pipeline architecture (LLM chains, queue, scheduler combination).

---

## DEV-02 — security_rules.md

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Loop Detection | Yes (3/120s → 600s pause; Redis+memory) |
| Keyword Flood | Yes (>3 in 5min → 15min block) |
| Prompt Injection | Yes (18 patterns → outbound_safety_incidents) |
| Outbound Poison Filter | Yes (16 strings) |
| Reply Cooldown | Yes (60s) |
| Group/Broadcast Skip | Yes (@g.us at SQL level) |
| RAG Chunk Safety | Yes (30+ patterns purged) |
| API Key Storage | Yes (SHA-256) |
| Admin Command Dedup | Yes (SHA1/30s TTL/256 entries) |
| Missing | Exact prompt injection pattern list (security sensitive) |
| Production Verified | Yes (Wave-1) |
| Management Decision | HK-13 (security rules), HK-44 (cooldown) |

**Best-documented developer system article. Strongest governance.**

---

## DEV-03 — identity_brain.md

**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| 11-Step Algorithm | Yes (full table) |
| 8 Evidence Sources | Yes (with table names) |
| Confidence Scoring | Yes (1.0/0.95/0.7/0.5/0.0) |
| Phone Normalization | Yes (3 variants) |
| Secondary Evidence | Yes (4 types) |
| Missing | Role-level Bangla prompt injection (role_classifier) |
| Production Verified | Yes (Wave-1) |
| Management Decision | HK-02 (blocked pre-check), HK-09 (gate behaviors) — but not for the algorithm itself |

---

## DEV-04 — developer_notes.md

**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| AI Runtime Flags | Yes (9 flags with values and effect) |
| Bridge Ports | Yes (8082 HR, 8081 OPS) |
| Backup System | Yes (14d/8w, SHA-256, fazle_db_backups) |
| Missing | wa_chat_frontend 25 REST endpoints, SSE stream, /metrics observability endpoint, contact sync |
| Production Verified | Yes (Wave-1) |
| Management Decision | None for configuration flags |

---

## DEV-05 — role_permissions.md

**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| RBAC 5-Level Hierarchy | Yes (viewer < operator < accountant < admin < superadmin) |
| Bootstrap Admin Creation | Yes (ADMIN_NUMBERS env → superadmin on first message) |
| Role Gate Behaviors Matrix | Yes (12 roles × 4 behaviors) |
| Command-Role Mapping | Yes (examples per level) |
| Missing | Full COMMAND_ROLE dict (only examples, not exhaustive list) |
| Production Verified | Yes (Wave-1) |
| Management Decision | HK-09 (draft-always), HK-40 (RBAC approved), HK-41 (bootstrap) |

---

## DEV-06 — database_rules.md

**Maturity: Level 1 (Documented — abstract)**

| Dimension | Status |
|---|---|
| Table Inventory | 0 of 43 tables |
| Schema | None |
| Idempotency | Not consolidated here (scattered in workflow articles) |
| Advisory Locks | Mentioned abstractly |
| Soft-Delete | Not mentioned |
| Production Verified | No |
| Wave-2 Priority | P2 (highest priority among P2 items) |

**Lowest-maturity article in the developer system. Critical gap.**

---

## DEV-07 — rag_strategy.md

**Maturity: Level 1 (Documented — abstract)**

| Dimension | Status |
|---|---|
| Algorithm Mentioned | BM25 (mentioned) |
| Parameters | k1=1.5, b=0.75 — NOT documented |
| Chunk Size | 320/60 — NOT documented |
| Tokenizer | NOT documented |
| Excluded Dirs/Files | NOT documented |
| Production Verified | No |
| Wave-2 Priority | P2 |

---

## DEV-08 — conversation_parser.md

**Maturity: Level 1 (Documented — stub)**

| Dimension | Status |
|---|---|
| Intent Classification | Brief mention |
| 15 Parsers | Not consolidated here |
| Production Verified | No |
| Wave-2 Priority | P2 |

---

## DEV-09 — ocr_engine.md

**Maturity: Level 1 (Documented — partial via Wave-1)**

| Dimension | Status |
|---|---|
| Document Types | 4 documented (via release_slip_workflow.md Wave-1) |
| Required Fields | 6 documented |
| Full TypedDict | 18 fields total — 12 missing in KB |
| Image Requirements | 1KB–8MB, JPG/JPEG/PNG/WEBP |
| Label Blacklist | 35+ strings — NOT in KB |
| Signature Detection | 3 types — NOT in KB |
| Confidence Scoring | NOT in KB |
| Production Verified | Partial (Wave-1 put OCR details in release_slip_workflow.md, not ocr_engine.md) |
| Wave-2 Priority | P2 |

**Note:** OCR details are scattered — Wave-1 put them in the workflow article rather than the engine article. This needs consolidation in Wave-2.

---

## DEV-10 — parser_engine.md

**Maturity: Level 1 (Documented — stub)**

| Dimension | Status |
|---|---|
| 15 Parsers | Not consolidated here |
| Regex Constants | Not documented |
| Production Verified | No |
| Wave-2 Priority | P2 |

---

## DEV-11 through DEV-16 (Unenriched Articles)

| Article | Estimated Level | Key Gaps |
|---|---|---|
| event_pipeline.md | Level 1 | Event bus behavior, webhook receipt, retry logic |
| hybrid_search.md | Level 1 | BM25 + keyword hybrid weights |
| system_prompt.md | Level 1 | Prompt templates per role not documented |
| visibility_rules.md | Level 1 | Which messages visible to which roles |
| workflow_engine.md | Level 1 | Generic workflow orchestration pattern |
| message_routing.md | Level 1 | 15-step routing table exists in PKCA but not in KB article |

---

## Developer System Maturity Summary

| Article | Level | Missing for Next Level |
|---|---|---|
| automation_pipeline.md | 2 | Management approval for pipeline architecture |
| security_rules.md | 3 | PKVC post-Wave-1 |
| identity_brain.md | 2 | Management approval for algorithm |
| developer_notes.md | 2 | wa_chat_frontend, metrics endpoint |
| role_permissions.md | 3 | Full COMMAND_ROLE dict |
| database_rules.md | 1 | 43 table inventory (Wave-2) |
| rag_strategy.md | 1 | BM25 technical params (Wave-2) |
| conversation_parser.md | 1 | Parser consolidation (Wave-2) |
| ocr_engine.md | 1 | Full EscortSlipResult TypedDict (Wave-2) |
| parser_engine.md | 1 | 15 parsers consolidated (Wave-2) |
| event_pipeline.md | 1 | Event bus behavior |
| hybrid_search.md | 1 | Search weights |
| system_prompt.md | 1 | Role-specific prompts |
| visibility_rules.md | 1 | Message visibility rules |
| workflow_engine.md | 1 | Orchestration pattern |
| message_routing.md | 1 | 15-step routing documented in PKCA but not here |

**Developer System Domain Average: 1.75 / 5.0**
**Level 3: 2 / 16**
**Level 2: 3 / 16**
**Level 1: 11 / 16**
**Level 0: 0 / 16**

---

## Developer System Domain Verdict

**Domain Maturity: Level 2 (Production Verified)**

The 5 Wave-1-enriched articles bring this domain to Level 2 overall, with `security_rules.md` and `role_permissions.md` standing out at Level 3. The remaining 11 unenriched articles are at Level 1 (documented stubs). The database and RAG articles are the most critical gaps for Wave-2.

**Key governance insight:** Two articles reached Level 3 because they have specific management decisions (HK-13, HK-44, HK-40, HK-41) — not because they are the most comprehensive. The path to Level 3 for the remaining articles is often a management ratification decision rather than more documentation.

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
