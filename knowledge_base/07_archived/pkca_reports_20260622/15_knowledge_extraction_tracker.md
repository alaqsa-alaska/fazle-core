---
title: PKCA Report 15: Knowledge Extraction Tracker
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 15: Knowledge Extraction Tracker

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Extraction Unit Methodology

A "Knowledge Unit" (KU) is one distinct, documentable fact about how the production system behaves. One function, one config value, one algorithm step, one table relationship = one KU.

---

## Module-Level Extraction Tracker

| Module | File | Total KUs | Documented KUs | Missing KUs | Coverage % |
|---|---|---|---|---|---|
| **app/config.py** | 50+ settings | 50 | 4 | 46 | 8% |
| **app/llm.py** | Provider chains, fallback, memory | 12 | 0 | 12 | 0% |
| **app/bridge.py** | BridgeClient, CircuitBreaker, suffix | 10 | 0 | 10 | 0% |
| **app/bridge_poller** | 15-step routing, all gates | 30 | 2 | 28 | 7% |
| **app/message_router** | 15-step routing, silent-skip, intents | 20 | 2 | 18 | 10% |
| **app/admin_commands** | 37 commands, dedup, audit | 45 | 4 | 41 | 9% |
| **app/rbac** | 5 roles, hierarchy, bootstrap, API keys | 8 | 1 | 7 | 12% |
| **modules/accountant_summary** | 13 labels, format detection, no-write rule | 15 | 0 | 15 | 0% |
| **modules/admin_employees** | CRUD, soft-delete, FPE pre-seed | 12 | 2 | 10 | 17% |
| **modules/admin_transactions** | 4 matching rules, AdminTxnCreate | 14 | 2 | 12 | 14% |
| **modules/attendance** | Draft, approve, state machine | 10 | 4 | 6 | 40% |
| **modules/attendance_parser** | Date formats, shift detect, heuristic | 8 | 2 | 6 | 25% |
| **modules/backup** | pg_dump, 14+8 rotation, SHA256, table | 8 | 0 | 8 | 0% |
| **modules/contact_sync** | Canonical, best-name, incremental+full | 8 | 0 | 8 | 0% |
| **modules/conversation_layer** | Shadow-only (excluded by design) | N/A | N/A | N/A | N/A |
| **modules/draft_quality** | 4 criteria, BAD_PATTERNS, MAX_LEN, env flag | 8 | 0 | 8 | 0% |
| **modules/employee_verification** | 5 steps, session table, identity mismatch | 10 | 1 | 9 | 10% |
| **modules/escort** | 4 parsers, EscortOrder TypedDict, formats | 20 | 4 | 16 | 20% |
| **modules/escort_lifecycle** | Release parser, transport rates, food rule, validation | 15 | 2 | 13 | 13% |
| **modules/escort_roster** | sync_program, sync_all, recalculate, get_summary | 6 | 1 | 5 | 17% |
| **modules/escort_slip_extractor** | EscortSlipResult, doc types, blacklist, signatures | 18 | 0 | 18 | 0% |
| **modules/fazle_payroll_engine** | 5 workers, FPE SM, MessageType, 4 tables, routes | 20 | 0 | 20 | 0% |
| **modules/identity_brain** | 11 roles, 8 sources, algorithm, confidence | 20 | 6 | 14 | 30% |
| **modules/media_normalization** | OCR candidate detection, placeholder text | 6 | 1 | 5 | 17% |
| **modules/memory_extractor** | Fact extraction, user_profiles, user_memory, should_update_kb | 8 | 0 | 8 | 0% |
| **modules/message_archive** | save_message, canonical_phone, message_hash, critical_contact | 6 | 0 | 6 | 0% |
| **modules/observability** | inc/gauge/observe, Prometheus /metrics | 6 | 0 | 6 | 0% |
| **modules/outbound** | Queue states, DLQ, backoff, circuit breaker, multi-channel | 15 | 0 | 15 | 0% |
| **modules/payment** | Thin re-export, no logic | 1 | 1 | 0 | 100% |
| **modules/payment_correction** | DORMANT: 0 callers, 3 functions | 3 | 0 | 3 | 0% |
| **modules/payment_ingest** | SMS parser, cash shorthand parser | 8 | 1 | 7 | 12% |
| **modules/payment_workflow** | Draft creation, advance keywords, daily rate formula | 15 | 4 | 11 | 27% |
| **modules/payroll** | State machine, ALLOWED_TRANSITIONS, idempotency, compute | 12 | 0 | 12 | 0% |
| **modules/payroll_logic** | PayrollSummary TypedDict, under_review, duty_count_30d | 5 | 1 | 4 | 20% |
| **modules/rag** | BM25, chunk, tokenizer, safety filters, stop words | 18 | 3 | 15 | 17% |
| **modules/recruitment_ai** | Deterministic fast-replies, safe fallback, source_of_truth | 10 | 0 | 10 | 0% |
| **modules/recruitment_flow** | 7-step session, scoring, age, positions, TTL, INTAKE_KEYWORDS | 15 | 5 | 10 | 33% |
| **modules/reply_templates** | Rotation, categories, frustration variant | 8 | 0 | 8 | 0% |
| **modules/reports** | 10-min cache, audit log, report builders, 6 report types | 10 | 1 | 9 | 10% |
| **modules/reviewed_reply_memory** | Match hierarchy, exclusions, safety gate | 12 | 1 | 11 | 8% |
| **modules/role_classifier** | ROLE_PRIORITY, Bangla prompts, user_profiles lookup | 8 | 0 | 8 | 0% |
| **modules/scheduler** | 15 jobs, schedules, timezone, env overrides, idempotency | 25 | 0 | 25 | 0% |
| **modules/social_auto_reply** | 20-file system, reply_rules.py content, all intent replies | 25 | 1 | 24 | 4% |
| **modules/user_role** | UserRole TypedDict, 11-digit normalize, confidence | 5 | 1 | 4 | 20% |
| **modules/wa_chat_frontend** | 25 endpoints, SSE, auth variants, capabilities | 18 | 0 | 18 | 0% |

---

## Extraction Summary Totals

| Metric | Value |
|---|---|
| Total modules analyzed | 44 |
| Total knowledge units identified | ~618 |
| Total knowledge units documented | ~57 |
| Total knowledge units missing | ~561 |
| Overall extraction coverage | **~9%** |

---

## Modules with 0% Documentation (Highest Priority)

1. `modules/scheduler` — 25 KUs, 15 jobs, all undocumented
2. `modules/fazle_payroll_engine` — 20 KUs, 5 workers, entirely undocumented
3. `modules/outbound` — 15 KUs, queue system entirely undocumented
4. `app/llm.py` — 12 KUs, dual provider chains undocumented
5. `modules/payroll` — 12 KUs, state machine entirely undocumented
6. `modules/escort_slip_extractor` — 18 KUs, entire module undocumented
7. `modules/wa_chat_frontend` — 18 KUs, entire admin dashboard undocumented
8. `modules/role_classifier` — 8 KUs, Bangla prompt injection undocumented
9. `modules/memory_extractor` — 8 KUs, fact extraction pipeline undocumented
10. `modules/draft_quality` — 8 KUs, rejection criteria entirely undocumented

---

## Modules with Best Partial Coverage (Enrichment First)

| Module | Coverage | Best Existing Article |
|---|---|---|
| `modules/attendance` | 40% | `05_workflows/attendance_workflow.md` |
| `modules/recruitment_flow` | 33% | `04_business_rules/recruitment_business_rules.md` |
| `modules/identity_brain` | 30% | `03_ai_identity/identity_overview.md` |
| `modules/payment_workflow` | 27% | `05_workflows/payment_workflow.md` |
| `modules/attendance_parser` | 25% | `05_workflows/attendance_workflow.md` |
| `modules/escort` | 20% | `05_workflows/escort_workflow.md` |
