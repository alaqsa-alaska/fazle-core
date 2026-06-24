---
title: PKCA Report 16: Existing Article Enrichment Plan
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 16: Existing Article Enrichment Plan

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## Enrichment Policy

Per PKCA program rules: **Enrich existing articles first. Only recommend a new article when knowledge cannot logically fit anywhere existing.**

Each entry below specifies what to add, from which module, and the priority.

---

## 06_developer_system/ Enrichment Targets

### `automation_pipeline.md` — HIGH IMPACT

Current state: Describes pipeline stages broadly. No concrete details.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| LLM Provider Chain (Reply): GitHub→Groq→Ollama with exact model names and Groq rate limits | `app/llm.py` | P1 |
| LLM Provider Chain (Intent): Groq→GitHub→Ollama (explain WHY order differs from reply) | `app/llm.py` | P1 |
| LLM fallback holding message — exact Bangla text | `app/llm.py._FALLBACK_REPLY` | P1 |
| Outbound queue state machine: pending→sending→sent/failed→dlq | `modules/outbound` | P1 |
| DLQ behavior and DLQ alert interval | `modules/outbound`, `modules/scheduler` | P1 |
| Circuit breaker: CLOSED/OPEN pattern | `app/bridge.py` | P2 |
| Complete 15-job scheduler table (name, schedule, env override, purpose) | `modules/scheduler` | P1 |
| Scheduler timezone: Asia/Dhaka; SCHEDULER_TIMEZONE env override | `modules/scheduler` | P2 |
| Draft quality gate: 4 rejection criteria, BAD_PATTERNS, MAX_DRAFT_LEN=4000 | `modules/draft_quality` | P2 |
| Draft state machine: pending→approved→sent; rejected; rejected_quality; rejected_fallback; expired | `modules/drafts` + `modules/draft_quality` | P2 |
| Reply templates rotation: _rotate() per-sender counter, 6 template categories | `modules/reply_templates` | P2 |
| Memory extractor: fire-and-forget, user_profiles, user_memory, should_update_kb | `modules/memory_extractor` | P2 |

---

### `security_rules.md` — HIGH IMPACT

Current state: Documents X-Internal-Key auth and API key creation. Missing all runtime protections.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| Loop detection: 3 replies/120s → pause 600s; Redis primary, in-memory fallback | `bridge_poller` | P1 |
| Keyword flood: same keyword >3 in 5min → 15min block | `bridge_poller` | P1 |
| Prompt injection protection: 18 blocked patterns → logged to outbound_safety_incidents | `bridge_poller._PROMPT_INJECTION_PATTERNS` | P1 |
| Outbound poison filter: 16 internal strings blocked from outbound | `bridge_poller._OUTBOUND_POISON` | P1 |
| Admin command dedup: SHA1(text+phone), 30s TTL, 256 entries | `admin_commands._dedup_seen` | P2 |
| API key storage: SHA-256 hash (never plaintext) | `modules/rbac.hash_api_key` | P2 |
| Group/broadcast skip: @g.us, newsletters, status@broadcast silently skipped | `bridge_poller` | P2 |
| RAG chunk safety: 30+ internal marker patterns purged from index | `modules/rag._CHUNK_UNSAFE_PATTERNS` | P2 |

---

### `database_rules.md` — HIGH IMPACT

Current state: Abstract principles only. No table names.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| Core table inventory (43 tables across 10 categories) | All modules | P2 |
| Phone multi-variant lookup: 01X, 880X, +880X always tried | `modules/phone_normalizer` | P2 |
| Soft-delete: wbom_employees uses status='Inactive', never DELETE | `modules/admin_employees` | P2 |
| Idempotency patterns: payment draft keys, outbound keys, message hash | Multiple | P2 |
| Advisory locks: concurrent payment writes use Postgres advisory locks | `modules/payment_workflow` | P2 |
| Attendance dedup: UNIQUE(employee_id, attendance_date) ON CONFLICT UPDATE | `modules/attendance` | P2 |
| Contact sync: canonical 8801XXXXXXXXXX, best-name=longest, ON CONFLICT DO UPDATE | `modules/contact_sync` | P2 |

---

### `identity_brain.md` — HIGH IMPACT

Current state: Describes identity resolution generally. Missing algorithm steps and evidence sources.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| 11-step resolution algorithm with step-by-step order | `modules/identity_brain` | P1 |
| 8 evidence sources with table names | `modules/identity_brain` | P2 |
| Confidence scoring: 1.0 (direct seed), 0.95 (primary phone), 0.7 (secondary), 0.5 (contacts only) | `modules/identity_brain` | P2 |
| Bangla system prompt injection per role | `modules/role_classifier` | P2 |
| blocked role behavior: hard silent-skip, no log, no draft | `message_router` | P1 |

---

### `developer_notes.md` — MEDIUM IMPACT

Current state: Various developer notes. Missing config flags and runtime switches.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| AI config flags: OLLAMA_REPLY_DISABLED, AI_SAFE_MODE, AUTO_REPLY_ENABLED, RECRUITMENT_AUTOREPLY_ENABLED | `app/config.py` | P1 |
| Bridge ports: bridge1=8082 (HR), bridge2=8081 (OPS) | `app/config.py` | P2 |
| wa_chat_frontend: 25 REST endpoints + SSE stream at /api/wa/stream | `modules/wa_chat_frontend` | P2 |
| Backup rotation: 14 daily + 8 weekly, SHA-256 hash, fazle_db_backups table | `modules/backup` | P2 |

---

### `rag_strategy.md` — MEDIUM IMPACT

Current state: Mentions BM25+semantic and chunking guidance. Missing technical params.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| BM25 parameters: k1=1.5, b=0.75 | `modules/rag` | P2 |
| Chunk size: 320 chars, overlap: 60 chars, min token: 2 chars | `modules/rag` | P2 |
| Bilingual tokenizer: [A-Za-z0-9ঀ-৿]+ | `modules/rag._TOKEN_RE` | P2 |
| Excluded dirs (11): \_internal\_archived, tests, etc. | `modules/rag._EXCLUDED_DIRS` | P2 |
| Excluded filenames (11): analysis, prompt, intent, debug, etc. | `modules/rag._EXCLUDED_NAME_KEYWORDS` | P2 |
| Index rebuild: daily at 18:00 by rag_rebuild job; sources: resources/*.txt + fazle_knowledge_base | `modules/rag` | P2 |
| Audit ring buffer: last 50 queries in-memory (not persisted) | `modules/rag` | P3 |

---

### `role_permissions.md` — MEDIUM IMPACT

Current state: Lists roles but not the ordered hierarchy or gate behaviors.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| RBAC hierarchy: viewer < operator < accountant < admin < superadmin | `modules/rbac.COMMAND_ROLE` | P1 |
| Bootstrap creation: ADMIN_NUMBERS from .env auto-created as superadmin on first message | `modules/rbac.ensure_bootstrap_admins` | P1 |
| Role gate table: draft-always, silent-skip, auto-reply, recruiting-blocked per role | `modules/bridge_poller` | P1 |

---

### `ocr_engine.md` — MEDIUM IMPACT

Current state: Describes OCR outputs. Missing EscortSlipResult structure.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| EscortSlipResult TypedDict: all 18 fields | `modules/escort_slip_extractor` | P2 |
| Document type detection: printed_template_slip vs handwritten_blank_slip vs mixed_form | `modules/escort_slip_extractor.detect_document_type` | P2 |
| REQUIRED_FIELDS list: 6 mandatory fields | `modules/escort_slip_extractor` | P2 |
| Label blacklist: 35+ values that can never be field values | `modules/escort_slip_extractor` | P2 |

---

## 04_business_rules/ Enrichment Targets

### `ai_response_rules.md` — CRITICAL

Current state: General AI response guidelines. Missing all operational gate rules.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| Silent-skip trigger conditions: 11 display-name tokens + accountant phone + blocked role | `message_router._should_silent_skip` | P1 |
| 9 safe auto-send intents (complete list) | `message_router._SAFE_AUTOSEND_INTENTS` | P1 |
| advance_request excluded from auto-send (explain why) | `message_router` comment | P1 |
| Draft-always roles: accountant, client_escort_buyer, vip_client, repeat_client | `bridge_poller._is_draft_always` | P1 |
| Complaint phrases that force draft (11 phrases) | `bridge_poller._COMPLAINT_PHRASES` | P1 |
| Advance request phrases that force draft (5 phrases) | `bridge_poller._ADVANCE_REQUEST_PHRASES` | P1 |
| office_location bypasses AI entirely → KB-only fast path | `message_router` step 12 | P2 |
| Automated reply suffix: exact Bangla text | `app/bridge._AUTOMATED_SUFFIX` | P1 |
| Polite holding fallback message | `app/llm._FALLBACK_REPLY` | P1 |

---

### `escort_business_rules.md` — HIGH IMPACT

Current state: Has escort business rules. Missing hardcoded financial rules.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| Transport rate table: full BDT values by destination | `escort_lifecycle._TRANSPORT_RATES` | P1 |
| Food calculation: 150 BDT/day, time exceptions (before 10AM = no food, after 3PM = no food) | `escort_lifecycle._calc_duty_days` | P1 |
| Duty days >90 → SUSPICIOUS flag in draft | `escort_lifecycle.build_release_draft` | P2 |
| Release date validation rules | `escort_lifecycle._validate_release_date` | P2 |

---

### `recruitment_business_rules.md` — HIGH IMPACT + CONFLICT

Current state: Has recruitment rules. Missing scoring, session TTL, valid positions.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| **CONFLICT BR-25**: Age range → AWAITING MANAGEMENT DECISION | `recruitment_flow._parse_age` | P1-CONFLICT |
| 9 valid positions (full list) | `recruitment_flow.VALID_POSITIONS` | P1 |
| Scoring algorithm: experience pts + position pts + completeness pts | `recruitment_flow._compute_score` | P1 |
| Session TTL: 24 hours | `recruitment_flow.SESSION_TTL` | P1 |
| Deterministic fast-replies: fee/contact/office/age answered without LLM | `modules/recruitment_ai` | P1 |
| Safe fallback message text (Bangla) | `modules/recruitment_ai` | P1 |

---

### `payment_business_rules.md` — MEDIUM IMPACT

Current state: Has payment rules. Missing advance trigger phrases and draft TTL.

**Add:**
| Section to Add | Source | Priority |
|---|---|---|
| 18 advance trigger keywords (including emergency/medical/family crisis) | `payment_workflow.ADVANCE_KEYWORDS` | P1 |
| Advance request force-draft: 5 Bangla phrases | `bridge_poller._ADVANCE_REQUEST_PHRASES` | P1 |
| Payment draft TTL: 24h expiry via combined_draft_cleanup job | `modules/scheduler` | P1 |

---

## 05_workflows/ Enrichment Targets

### `salary_workflow.md`

**Add:** Payroll 6-state machine diagram + ALLOWED_TRANSITIONS table + PAYROLL command syntax.

### `escort_workflow.md`

**Add:** Escort program state machine: draft→confirmed→Assigned→Running→Completed; Cancelled. Parser formats.

### `payment_workflow.md`

**Add:** Payment draft states (pending/sent/rejected/expired). Employee verification 5-step sequence.

### `attendance_workflow.md`

**Add:** Attendance draft state machine. Date format examples. Shift detection patterns.

### `recruitment_workflow.md`

**Add:** Recruitment session state machine (7 steps + scored/expired). Session TTL.

### `release_slip_workflow.md`

**Add:** Release confirmation parser fields (6 extracted fields). Release date validation.

---

## 02_admin_knowledge/ Enrichment Targets

### `admin_operations_overview.md`

**Add:** Complete command reference with syntax, role, action for all 37 commands. PAYROLL, BACKUP, SCHEDULE commands are entirely missing.

### `admin_role_management.md`

**Add:** USER ADD/ROLE/REMOVE/LIST/APIKEY syntax. Bootstrap admin creation from ADMIN_NUMBERS env.

---

## 03_ai_identity/ Enrichment Targets

### `identity_overview.md`

**Add:** 11-step resolution algorithm with step order. Secondary evidence sources. Confidence scoring.

### `permission_matrix.md`

**Add:** Role behavioral gate table (draft-always, silent-skip, auto-reply blocked per role).

---

## Enrichment Summary

| Folder | Articles to Enrich | Items to Add |
|---|---|---|
| 06_developer_system/ | 8 articles | ~60 items |
| 04_business_rules/ | 4 articles | ~25 items |
| 05_workflows/ | 6 articles | ~18 items |
| 02_admin_knowledge/ | 2 articles | ~12 items |
| 03_ai_identity/ | 2 articles | ~10 items |
| **Total** | **22 articles** | **~125 items** |

**Articles needing enrichment: 22 of 65 (34%)**
**Articles fully adequate (no changes needed): 43 of 65 (66%)**
