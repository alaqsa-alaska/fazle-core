---
title: PKCA v3 — Production Knowledge Coverage Audit
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA v3 — Production Knowledge Coverage Audit
**Program:** Production Knowledge Coverage Audit (PKCA)
**Version:** v3 (Session 10 post-enrichment delta)
**Date:** 2026-06-23
**Baseline:** PKCA v2 2026-06-23 — 67% weighted overall
**Mode:** Post-implementation update — reflects Session 10 KB articles and ESX-WIRE production change
**Authorized under:** W4-AUTH + Session 10 ESX-WIRE authorization

---

## Session 10 Changes Summary

| Change | Type | Impact |
|---|---|---|
| `knowledge_base/06_developer_system/intent_classifier.md` | NEW KB article | P2 `intent` 28% → 85% |
| `knowledge_base/06_developer_system/accountant_summary.md` | NEW KB article | P3 `accountant_summary` 3% → 85% |
| `knowledge_base/06_developer_system/attendance_parser.md` | NEW KB article | P2 `attendance_parser` 62% → 78% |
| `modules/bridge_poller/__init__.py` ESX-WIRE | Production code | Step 2 OCR → `escort_slip_extractor`; `bridge_poller.md` NOT yet updated → freshness gap |
| `knowledge_base/00_governance/management_decisions.md` | Updated | Session 10 ESX-WIRE authorization + field mapping documented |
| `scripts/rebuild_qdrant.py` | New tool | Standalone rebuild; Qdrant rebuilt 2026-06-23 — 251 points in `fazle_rag_chunks` |

---

## Audit Method

Coverage % = (knowledge actually documented in KB) / (total knowledge that should be documented)

**Priority tiers:**
- **P0 (Core pipeline):** Must be ≥ 70% or Phase 5 fails
- **P1 (Key subsystems):** Should be ≥ 70%
- **P2 (Support/utilities):** Target ≥ 50%
- **P3 (Low priority):** Document when capacity allows

**Coverage dimensions scored:**
1. Purpose/scope documented
2. Key constants/config values present
3. Data flow / algorithm steps present
4. Table schemas / DDL present
5. Hidden rules / edge cases present
6. Cross-references accurate

---

## Module Coverage Matrix — P0 (Core Pipeline)

| Module | KB Article(s) | v2 % | v3 % | Δ | P0 Pass? |
|---|---|---|---|---|---|
| `message_router` | `workflow_engine.md` + `reviewed_reply_memory.md` | 75 | 75 | — | ✅ |
| `bridge_poller` | `bridge_poller.md` (Wave-4) | 90 | 85 | -5 | ✅ ⚠️ stale |
| `identity_brain` | `identity_brain.md` + `identity_integration.md` | 80 | 80 | — | ✅ |
| `escort_lifecycle` | `escort_workflow.md` + `escort_roster_system.md` | 82 | 82 | — | ✅ |
| `payroll` | `payroll_module.md` | 85 | 85 | — | ✅ |
| `fazle_payroll_engine` | `fpe_overview.md` | 82 | 82 | — | ✅ |
| `outbound` | `automation_pipeline.md` (partial) | 30 | 30 | — | ❌ gap |

**⚠️ bridge_poller note:** ESX-WIRE change (Session 10) replaced `ocr_processor.process_image()` with `escort_slip_extractor.extract_escort_slip()` in Step 2 of the image path. The `bridge_poller.md` KB article has NOT yet been updated to reflect this change — it still describes the old `ocr_processor` Step 2 flow. Coverage reduced by 5% to account for the stale Step 2 description. **Action required:** Update `bridge_poller.md` Step 2 section.

**P0 Average:** 74% (v2: 75%) — slight decrease due to bridge_poller freshness gap
**P0 Pass Rate:** 6/7 (86%) — `outbound` still below threshold

---

## Module Coverage Matrix — P1 (Key Subsystems)

| Module | KB Article(s) | v2 % | v3 % | Δ | P1 Pass? |
|---|---|---|---|---|---|
| `recruitment_flow` | `recruitment_flow_system.md` | 82 | 82 | — | ✅ |
| `recruitment_ai` | `recruitment_ai_detail.md` | 87 | 87 | — | ✅ |
| `social_auto_reply` | `social_auto_reply_system.md` | 87 | 87 | — | ✅ |
| `admin_commands` | `admin_operations_overview.md` + `admin_commands_detail.md` | 87 | 87 | — | ✅ |
| `scheduler` | `automation_pipeline.md` | 92 | 92 | — | ✅ |
| `wa_chat_frontend` | `admin_ui.md` | 87 | 87 | — | ✅ |
| `contact_sync` | `contact_sync.md` | 87 | 87 | — | ✅ |
| `distributed_architecture` | `distributed_architecture.md` | 87 | 87 | — | ✅ |
| `rbac` | `admin_commands_detail.md` | 82 | 82 | — | ✅ |
| `rag` | `rag_strategy.md` + `hybrid_search.md` | 78 | 78 | — | ✅ |
| `payment_workflow` | `payment_business_rules.md` | 72 | 72 | — | ✅ |

**P1 Average:** 84% (unchanged)
**P1 Pass Rate:** 11/11 (100%)

---

## Module Coverage Matrix — P2 (Support/Utilities)

| Module | KB Article(s) | v2 % | v3 % | Δ |
|---|---|---|---|---|
| `phone_normalizer` | `phone_normalizer.md` | 87 | 87 | — |
| `reviewed_reply_memory` | `reviewed_reply_memory.md` | 87 | 87 | — |
| `ocr_processor` | `ocr_engine.md` | 90 | 90 | — |
| `admin_employees` | `admin_operations_overview.md` | 82 | 82 | — |
| `admin_transactions` | `admin_transactions_rules.md` | 87 | 87 | — |
| `attendance` + `attendance_parser` | `attendance_workflow.md` + `attendance_parser.md` (NEW v3) | 62 | 78 | **+16** |
| `intent` (classifier) | `intent_classifier.md` (NEW v3) | 28 | 85 | **+57** |
| `escort_slip_extractor` | `ocr_engine.md` (partial) + `management_decisions.md` ESX-WIRE | 35 | 70 | **+35** |
| `image_hash` | `ocr_engine.md` (duplicate detection section) | 42 | 42 | — |
| `drafts` / `draft_quality` | `automation_pipeline.md` | 32 | 32 | — |
| `contact_roles` | `identity_brain.md` + `identity_integration.md` | 62 | 62 | — |
| `number_identity` | `identity_integration.md` + `phone_normalizer.md` | 52 | 52 | — |
| `rag` module internals | `rag_strategy.md` + `hybrid_search.md` | 78 | 78 | — |
| `escort_roster` | `escort_roster_system.md` | 75 | 75 | — |
| `backup` | `admin_operations_overview.md` | 28 | 28 | — |
| `reports` | `admin_operations_overview.md` | 32 | 32 | — |
| `user_role` | `identity_brain.md` (partial) | 18 | 18 | — |
| `observability` | `distributed_architecture.md` | 15 | 15 | — |

**P2 Average: 61%** (v2: 57%, +4%)

**escort_slip_extractor note:** ESX-WIRE architectural use now documented in `management_decisions.md` (field mapping, TWO-DATE rule, completion_date detector, compat dict). Dedicated KB article still missing — remaining gap is EscortSlipResult DDL, full 3-pass OCR algorithm, and `escort_slip_extractions` table schema.

---

## Module Coverage Matrix — P3 (Low Priority / Not Yet Scoped)

| Module | v2 % | v3 % | Δ | Note |
|---|---|---|---|---|
| `voice_processor` | 0 | 0 | — | Out of scope — not active per production |
| `kb_upload` | 2 | 2 | — | Mentioned in rag_strategy.md |
| `payment_correction` | 5 | 5 | — | Referenced in fpe_overview.md reversal section |
| `payment_ingest` | 5 | 5 | — | Referenced in fpe_overview.md |
| `conversation_layer` | 3 | 3 | — | Not documented (Session 9 activation not documented in KB) |
| `accountant_summary` | 3 | 85 | **+82** | `accountant_summary.md` NEW v3 |
| `media_normalization` | 5 | 5 | — | Mentioned in bridge_poller.md |
| `memory_extractor` | 0 | 0 | — | Not documented |
| `message_archive` | 10 | 10 | — | Referenced in bridge_poller.md cursor section |
| `payroll_logic` | 12 | 12 | — | Partially in payroll_module.md compute logic |
| `employee_verification` | 8 | 8 | — | Referenced in identity_integration.md |
| `reply_templates` | 22 | 22 | — | Covered in system_prompt.md + social_auto_reply |
| `role_classifier` | 15 | 15 | — | In identity_brain.md |
| `draft_quality` | 8 | 8 | — | In automation_pipeline.md |
| `conversation_parser` | 20 | 20 | — | `conversation_parser.md` exists in 06_dev |

**P3 Average: 13%** (v2: 7%, +6%)

---

## Workflow Coverage (Updated)

| Workflow | v2 % | v3 % | Δ | Notes |
|---|---|---|---|---|
| WF-01: Message routing (15 steps) | 78 | 78 | — | |
| WF-02: Escort order → assignment | 82 | 82 | — | |
| WF-03: Release + payment (slip → draft) | 78 | 82 | **+4** | ESX-WIRE: completion_date structural detector documented |
| WF-04: Attendance reporting | 62 | 72 | **+10** | attendance_parser.md adds parse → draft → approve flow |
| WF-05: Recruitment funnel | 85 | 85 | — | |
| WF-06: Admin command processing | 85 | 85 | — | |
| WF-07: Payroll compute → approval | 85 | 85 | — | |
| WF-08: Employee payment verification | 72 | 72 | — | |
| WF-09: Outbound message delivery | 35 | 35 | — | |
| WF-10: Contact synchronization | 87 | 87 | — | |
| **Average** | **75** | **76** | **+1** | |

---

## Business Rule Coverage (Unchanged)

| Domain | Rules | v2 % | v3 % | Notes |
|---|---|---|---|---|
| Attendance | 6 | 85 | 85 | — |
| Recruitment | 8 | 82 | 82 | — |
| Payment | 12 | 78 | 78 | — |
| Escort | 14 | 82 | 82 | ESX-WIRE TWO-DATE rule in management_decisions.md (not yet in separate biz rule article) |
| Identity / Routing | 10 | 72 | 72 | — |
| AI / System | 8 | 68 | 68 | — |
| **Average** | 58 | **78** | **78** | — |

---

## State Machine Coverage (Unchanged)

| State Machine | v2 % | v3 % |
|---|---|---|
| SM-01: FPE message processing | 87 | 87 |
| SM-02: Escort program lifecycle | 82 | 82 |
| SM-03: Payment draft | 85 | 85 |
| SM-04: Payroll run | 92 | 92 |
| SM-05: Queue arbiter lease | 87 | 87 |
| SM-06: Bridge health | 85 | 85 |
| SM-07: Draft reply | 72 | 72 |
| SM-08: Social reply queue | 85 | 85 |
| SM-09: Recruitment session | 82 | 82 |
| SM-10: Self-healer pressure | 87 | 87 |
| **Average** | **84** | **84** | |

---

## Hidden Rule Coverage (Updated)

| Category | # Rules | v2 % | v3 % | Δ | Notes |
|---|---|---|---|---|---|
| Advance request force-draft | 5+18 | 87 | 87 | — | |
| Admin command dedup (30s TTL, SHA-1) | 1 | 90 | 90 | — | |
| Bridge DEDUP_TTL_S=120, HISTORICAL_CUTOFF_S=300 | 2 | 87 | 87 | — | |
| REPLY_COOLDOWN=60s | 1 | 90 | 90 | — | |
| _SILENT_SKIP_NAME_TOKENS (8 tokens) | 1 | 80 | 80 | — | Values security-omitted |
| DRAFT_ALWAYS_PHONES (15 phones) | 1 | 95 | 95 | — | Values security-omitted |
| OCR confidence threshold (<40 → warning) | 1 | 90 | 90 | — | |
| Mongla transport gap (code=৳700 vs mgmt=৳800) | 1 | 100 | 100 | — | |
| BR-25 age 18–55 enforcement across channels | 1 | 95 | 95 | — | |
| TWO-DATE rule: completion_date = release detector | 1 | 0 | 85 | **+85** | management_decisions.md Session 10 |
| reviewed_reply_memory UNSAFE_DRAFT_TYPES (3) | 1 | 87 | 87 | — | |
| **Average** | | **90** | **91** | **+1** | |

---

## Consolidated Score Card

| Dimension | v2 Score | v3 Score | Δ | Target | Met? |
|---|---|---|---|---|---|
| P0 module coverage (avg) | 75% | 74% | -1% | ≥ 70% | ✅ ⚠️ |
| P1 module coverage (avg) | 84% | 84% | — | ≥ 70% | ✅ |
| P2 module coverage (avg) | 57% | 61% | +4% | ≥ 50% | ✅ |
| P3 module coverage (avg) | 7% | 13% | +6% | best effort | — |
| Workflow coverage (avg) | 75% | 76% | +1% | ≥ 70% | ✅ |
| Business rule coverage (avg) | 78% | 78% | — | ≥ 70% | ✅ |
| State machine coverage (avg) | 84% | 84% | — | ≥ 70% | ✅ |
| Hidden rule coverage (avg) | 90% | 91% | +1% | ≥ 65% | ✅ |
| P0 pass rate (≥70% each) | 86% (6/7) | 86% (6/7) | — | 100% | ⚠️ |

**Weighted overall (P0×0.30, P1×0.35, P2×0.25, P3×0.10):**
= 0.30×74 + 0.35×84 + 0.25×61 + 0.10×13 = 22.2 + 29.4 + 15.25 + 1.3 = **68%**

(v2 was 67% — net +1%)

---

## Open Gaps Requiring Action

### 1. `bridge_poller.md` — Step 2 OCR Stale (P0 ⚠️)
- **Problem:** ESX-WIRE replaced `ocr_processor.process_image()` with `escort_slip_extractor.extract_escort_slip()` in the image path Step 2. The KB article still describes the old flow.
- **Impact:** Developer reading `bridge_poller.md` gets wrong behavior for Step 2.
- **Action:** Update `bridge_poller.md` — replace Step 2 section with ESX-WIRE flow (completion_date detector, compat dict, EscortSlipResult fields).

### 2. `outbound` Module (P0 — 30%)
- **Problem:** Final delivery path (WhatsApp send gate, bridge selection, retry, safety incidents) not documented.
- **Action:** Create `06_developer_system/outbound_delivery.md`.

### 3. `escort_slip_extractor` Dedicated Article Missing (P2 — 70%)
- **Problem:** Coverage comes from `ocr_engine.md` partial + `management_decisions.md`. No standalone article documents the 3-pass OCR pipeline, `EscortSlipResult` TypedDict DDL, or `escort_slip_extractions` table schema.
- **Action:** Create `06_developer_system/escort_slip_extractor.md`.

---

## Coverage Trend

| Wave | Date | Overall | P0 Avg | P1 Avg | P2 Avg | P3 Avg |
|---|---|---|---|---|---|---|
| v1 (pre-enrichment) | 2026-06-22 | 14% | 25% | 15% | 9% | 0% |
| v2 (post Wave-3/4) | 2026-06-23 | 67% | 75% | 84% | 57% | 7% |
| v3 (Session 10) | 2026-06-23 | 68% | 74% | 84% | 61% | 13% |
| Target (Phase 5 cert) | — | ≥ 90% | ≥ 70% all | ≥ 70% all | ≥ 50% | best effort |

---

## Certification Recommendation

**Status: CONDITIONAL — same as v2; Session 10 gains are incremental**

**Improvement from v2:**
- P2 average: 57% → 61% (intent, attendance_parser, escort_slip_extractor improved)
- P3 average: 7% → 13% (accountant_summary fully documented)
- TWO-DATE hidden rule now documented (0% → 85%)
- Weighted overall: 67% → 68%

**Remaining blockers to 90% target:**
1. `outbound` module (P0, 30%) — largest single gap
2. `bridge_poller.md` freshness gap (ESX-WIRE Step 2 stale)
3. P2 utilities still below 70% average (61%)
4. P3 modules remain largely undocumented (13%)

**Path to 90%:** Requires `outbound_delivery.md` + `escort_slip_extractor.md` + `bridge_poller.md` update + continued P2/P3 documentation.
