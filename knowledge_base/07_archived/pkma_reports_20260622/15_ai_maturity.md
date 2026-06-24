---
title: PKMA Report 15 — AI Behavior Maturity
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKMA Report 15 — AI Behavior Maturity

**Program:** PKMA v1.0
**Date:** 2026-06-22
**Mode:** READ-ONLY

---

## Purpose

Assess the maturity of every AI behavior component in the Fazle AI Platform. AI behavior is mature when the LLM chain, intent classification, RAG retrieval, prompt construction, fallback behavior, safety gates, and reply rules are documented, production-verified, and management-approved.

---

## AI Component Inventory (12 components)

---

## AI-01 — Reply Generation LLM Chain

**KB Article:** `06_developer_system/automation_pipeline.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Provider Order | GitHub Models → Groq → Ollama (documented Wave-1) |
| Source | `app/llm.generate_reply()` |
| Fallback Holding Message | "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।" (documented) |
| Retry Logic | Each provider attempted in order; no re-attempt within same provider |
| Model Names | NOT in KB (which specific GitHub Models, Groq model name, Ollama model tag) |
| Timeout | NOT in KB |
| Production Verified | Yes (Wave-1) |
| Management Decision | None for LLM provider order |

**Gap to Level 3:** No management decision approving the LLM provider priority order. If a provider fails persistently, there is no documented escalation path.

---

## AI-02 — Intent Classification LLM Chain

**KB Article:** `06_developer_system/automation_pipeline.md`
**Maturity: Level 2 (Production Verified)**

| Dimension | Status |
|---|---|
| Provider Order | Groq → GitHub → Ollama (different from reply chain — documented Wave-1) |
| Source | `modules/intent.classify()` |
| Key Difference | Groq-first for intent (speed-optimized); GitHub-first for reply (quality-optimized) |
| Intent Categories | 9 safe auto-send + multiple non-auto-send intents — partially documented |
| Deterministic Pre-check | Yes — keyword patterns before LLM (documented ai_response_rules.md) |
| Model Names | NOT in KB |
| Production Verified | Yes (Wave-1) |
| Management Decision | None for intent chain provider order |

**Gap to Level 3:** No management decision approving intent chain configuration. The asymmetry between reply chain and intent chain (different provider orders) is documented but not ratified.

---

## AI-03 — Automated Reply Suffix

**KB Article:** `04_business_rules/ai_response_rules.md`, `06_developer_system/automation_pipeline.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Content | "This is an automated reply. For human assistance, contact [admin]." (paraphrased) |
| Source | `app/bridge.py._AUTOMATED_SUFFIX` |
| Applied To | All automated outbound messages |
| Exceptions | Reviewed replies (human-approved) — NOT exempt from suffix |
| Documented in KB | Yes (Wave-1, two articles) |
| Production Verified | Yes |
| Management Decision | HK-04 (automated suffix requirement) |

**Note:** Full suffix text not reproduced in KB (contains the exact admin phone number — privacy consideration). Referenced by constant name only.

---

## AI-04 — Silent-Skip Logic

**KB Article:** `04_business_rules/ai_response_rules.md`, `03_ai_identity/permission_matrix.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Conditions | 3: display-name token match (11 tokens), ACCOUNTANT_PHONE env match, role = blocked |
| Source | `app/message_router._should_silent_skip()` |
| Behavior | Message received, no reply, no draft created — silent discard |
| Display-Name Tokens | Documented (11 tokens listed in ai_response_rules.md) |
| Production Verified | Yes (Wave-1) |
| Management Decision | HK-01 (token list), HK-02 (blocked role) |

**Solid Level 3 component — best-documented AI behavior in the system.**

---

## AI-05 — Draft Quality Gate

**KB Article:** `06_developer_system/automation_pipeline.md` (partial mention only)
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| Source | `modules/draft_quality` |
| 4 Criteria | Length check, language detection, profanity filter, format validation |
| States It Produces | `rejected_quality`, `rejected_fallback` — NOT in KB |
| Threshold Values | NOT in KB |
| Admin Visibility | Admin sees rejection reason? — NOT in KB |
| Production Verified | No |
| Management Decision | None for quality gate behavior |

**Risk:** High — quality gate silently rejects drafts; admins do not know what "rejected_quality" means without documentation.

---

## AI-06 — office_location Fast Path

**KB Article:** `04_business_rules/ai_response_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Behavior | office_location intent bypasses LLM entirely; serves answer from KB RAG only |
| Source | `app/message_router._handle_office_location()` |
| Rationale | Deterministic answer from knowledge; LLM adds no value |
| Documented in KB | Yes (Wave-1) |
| Production Verified | Yes |
| Management Decision | HK-04 (classified as safe auto-send; fast path approved implicitly) |

---

## AI-07 — RAG Retrieval Engine

**KB Article:** `06_developer_system/rag_strategy.md` (abstract only)
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| Algorithm | BM25 |
| Parameters | k1=1.5, b=0.75 — NOT in KB (P2 Wave-2 target) |
| Chunk Size | 320 chars / 60 overlap — NOT in KB |
| Tokenizer | `[A-Za-z0-9ঀ-৿]+` bilingual regex — NOT in KB |
| Excluded Directories | 11 dirs excluded from indexing — NOT in KB |
| Excluded Filename Patterns | 11 patterns excluded — NOT in KB |
| Chunk Safety Filter | 30+ patterns purged from RAG chunks (documented security_rules.md) |
| Rebuild Schedule | 18:00 daily (documented in automation_pipeline.md scheduler table) |
| Production Verified | No (params not verified against kb articles) |
| Management Decision | None for RAG configuration |

**Risk:** High — RAG is the primary knowledge retrieval mechanism; its parameters determine answer quality. Entirely undocumented technically.

---

## AI-08 — Recruitment AI Brain

**KB Article:** `04_business_rules/recruitment_business_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Behavior | Deterministic classifier for recruitment questions (4 categories) |
| Source | `modules/recruitment_ai._looks_like_fee_question()`, etc. |
| Categories | fee_inquiry, contact_request, office_location, age_eligibility |
| Fallback | Safe Bangla fallback message when no category matches |
| Source of Truth | `resources/ops/recruitment_source_of_truth.txt` |
| Documented in KB | Yes (Wave-1 — 4 categories + fallback) |
| Production Verified | Yes |
| Management Decision | BR-25 (age eligibility), HK-36 (valid positions) |

---

## AI-09 — Reviewed Reply Memory

**KB Article:** `06_developer_system/developer_notes.md` (flag mention only)
**Maturity: Level 1 (Documented)**

| Dimension | Status |
|---|---|
| Config Flag | `REVIEWED_REPLY_MEMORY_ENABLED` — documented (developer_notes.md) |
| Behavior | Stores admin-reviewed replies as learned examples for future LLM prompting |
| Exclusions | NOT documented — which reply types are excluded from memory |
| Storage | NOT documented — which table/column |
| Retention | NOT documented |
| Production Verified | No |
| Management Decision | None |

**Risk:** Medium — uncontrolled learning system; exclusions not documented.

---

## AI-10 — Reply Cooldown Gate

**KB Article:** `06_developer_system/security_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Duration | 60 seconds between auto-replies to same sender |
| Source | `app/bridge_poller.REPLY_COOLDOWN` |
| Storage | Redis (with memory fallback) |
| Documented in KB | Yes (Wave-1) |
| Production Verified | Yes |
| Management Decision | HK-44 (cooldown approved) |

---

## AI-11 — Prompt Injection Protection

**KB Article:** `06_developer_system/security_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Patterns | 18 prompt injection patterns — documented by count (Wave-1) |
| Source | `app/bridge_poller._PROMPT_INJECTION_PATTERNS` |
| On Detection | Message blocked; logged to `outbound_safety_incidents` |
| Exact Pattern List | NOT reproduced in KB (security sensitivity) |
| Production Verified | Yes |
| Management Decision | HK-13 (security rules approved) |

---

## AI-12 — Outbound Poison Filter

**KB Article:** `06_developer_system/security_rules.md`
**Maturity: Level 3 (Management Approved)**

| Dimension | Status |
|---|---|
| Strings | 16 outbound poison strings — documented by count (Wave-1) |
| Source | `app/bridge_poller._OUTBOUND_POISON` |
| On Detection | Outbound message blocked before send |
| Exact List | NOT reproduced in KB (security sensitivity) |
| Production Verified | Yes |
| Management Decision | HK-13 (security rules approved) |

---

## AI Component Maturity Summary

| Component | Level | Risk |
|---|---|---|
| AI-01: Reply LLM Chain | 2 | Medium |
| AI-02: Intent Chain | 2 | Medium |
| AI-03: Reply Suffix | 3 | Low |
| AI-04: Silent-Skip | 3 | Low |
| AI-05: Draft Quality Gate | 1 | High |
| AI-06: office_location Fast Path | 3 | Low |
| AI-07: RAG Retrieval | 1 | High |
| AI-08: Recruitment AI Brain | 3 | Low |
| AI-09: Reviewed Reply Memory | 1 | Medium |
| AI-10: Reply Cooldown | 3 | Low |
| AI-11: Prompt Injection Protection | 3 | Low |
| AI-12: Outbound Poison Filter | 3 | Low |

**AI Behavior Domain Average: 2.5 / 5.0**
**Level 3 count: 7 / 12**
**Level 1 count: 3 (Draft Quality Gate, RAG, Reviewed Reply Memory)**

---

## AI Domain Verdict

**Domain Maturity: Level 2 (Production Verified)**

The safety and routing components (silent-skip, cooldown, suffix, injection/poison filters) are all at Level 3 — well-governed. The LLM chains themselves are only Level 2 (no management approval for provider order). The RAG engine and Draft Quality Gate are at Level 1, both critical gaps.

**Fastest path to Level 3:**
1. Management approves reply chain provider order (GitHub→Groq→Ollama) — 1 decision
2. Management approves intent chain provider order (Groq→GitHub→Ollama) — 1 decision
3. Document Draft Quality Gate behavior (4 criteria + rejection states) — KB update

---

*PKMA v1.0 | READ-ONLY | 2026-06-22*
