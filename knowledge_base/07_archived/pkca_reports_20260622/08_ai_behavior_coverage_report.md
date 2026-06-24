---
title: PKCA Report 08: AI Behavior Coverage Report
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKCA Report 08: AI Behavior Coverage Report

**Date:** 2026-06-22
**Mode:** Read-Only Analysis

---

## AI Provider Chain — 0% Covered

### Reply Generation Chain (customer-facing WhatsApp)

```
GitHub Models (primary) — openai/gpt-4o-mini
  └─ If fails or rate-limited →
Groq (secondary) — llama-3.1-8b-instant (14,400 req/day, 30 RPM)
  └─ If fails →
Ollama (local) — qwen3:8b
  └─ If OLLAMA_REPLY_DISABLED=true OR fails →
Polite holding message: "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"
```

**KB Coverage:** 0% — No article documents the provider chain.

### Intent Classification Chain (different from reply chain)

```
Groq (PRIMARY for intent) — llama-3.1-8b-instant
  └─ If fails or returns 'unknown' →
GitHub Models — openai/gpt-4o-mini
  └─ If fails →
Ollama — qwen3:8b
```

**KB Coverage:** 0% — The intent chain order (Groq first for intent, GitHub first for replies) is not documented anywhere.

### Key Distinction Not in KB

Intent classification and reply generation use **different provider priority orders**. This is a non-obvious behavioral rule that must be documented.

---

## OLLAMA_REPLY_DISABLED Flag — 0% Covered

When `OLLAMA_REPLY_DISABLED=true`:
- Ollama is NEVER used for customer-facing WhatsApp replies
- Ollama still runs for: intent classification, RAG index rebuild, learning memory extraction
- If GitHub + Groq both fail → polite holding message returned (no crash)

**KB Coverage:** 0%

---

## LLM Learning Memory — 0% Covered

Every successful LLM reply is saved to `llm_learning_memory` table:
- Fields: provider, model, trigger_text, intent, role, source, context_used, reply_text, is_fallback
- Used for future model evaluation and improvement
- **KB Coverage:** 0%

---

## Memory Extractor — 0% Covered

**Module:** `modules/memory_extractor`

Runs as fire-and-forget task after each message exchange:
- Sends conversation to GitHub Models with structured extraction prompt
- Extracts: name, role_hint, important_facts (work_info/personal_info/pending_matter)
- Persists to `user_profiles` + `user_memory` tables
- Optional flag: `should_update_kb` → promote fact to `fazle_knowledge_base`
- Strips JSON markdown fences from LLM response before parse

**KB Coverage:** 0%

---

## Role Classifier — 0% Covered

**Module:** `modules/role_classifier`

Provides per-contact role context injected into LLM system prompts:
- Reads `user_profiles` + `user_memory` tables
- Returns Bangla system prompt addition per role:
  - vip_client: "এই ব্যক্তি একজন VIP ক্লায়েন্ট..."
  - employee: "এই ব্যক্তি একজন কর্মচারী..."
  - candidate: "এই ব্যক্তি একজন চাকরিপ্রার্থী..."
  - unknown: "এই ব্যক্তি অপরিচিত..."
- ROLE_PRIORITY dict determines system prompt tone

**KB Coverage:** 0%

---

## Recruitment AI Module — 0% Covered

**Module:** `modules/recruitment_ai`

Restricted recruitment AI brain:
- Deterministic fact replies for fee/contact/office/age questions (no LLM)
- Safe fallback: "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"
- Reads from `resources/ops/recruitment_source_of_truth.txt`
- _looks_like_fee_question() detects 12 fee-related phrases
- _looks_like_contact_question() detects contact number queries
- Age limit fast-reply: "সাধারণ বয়সসীমা ১৮–৫৫ বছর।"
- Office location fast-reply
- General LLM fallback with scoped context

**KB Coverage:** 0%

---

## Reply Templates Module — 0% Covered

**Module:** `modules/reply_templates`

Provides rotating pre-approved Bengali templates per intent:
- _rotate() maintains per-sender counter (resets on restart)
- Template categories: recruitment (normal), recruitment (frustrated), greeting (normal), salary (normal)
- Frustration variant: switches to office-visit guidance
- Vendor/incident/emergency/followup templates
- Purpose: prevent "robotic" feel from identical consecutive replies

**KB Coverage:** 0%

---

## Reviewed Reply Memory — 5% Covered

**Module:** `modules/reviewed_reply_memory` (365 lines)

Admin-curated reply learning system:
- Admin approves a draft → the approved reply is stored as a memory
- Future similar messages → memory is looked up before calling LLM
- Match scope hierarchy: intent_role_phone → intent_role → intent (most to least specific)
- _eligible_draft_type: attendance, payment, gap_action are EXCLUDED
- _has_unsafe_content: blocks admin commands, credential patterns from being learned
- Requires REVIEWED_REPLY_MEMORY_ENABLED=true in config
- KB Coverage: `06_developer_system/workflow_engine.md` briefly mentions "Reviewed reply memory" in state list

**KB Coverage:** 5%

---

## AI Safety Gate — 0% Covered

**Source:** `app/config.py` + `modules/bridge_poller` + `modules/message_router`

When `AI_SAFE_MODE=true`:
- Long replies become drafts
- Low-confidence auto-replies become drafts
- Uncertain-intent auto-replies become drafts

Auto-Reply Global Gates:
- `AUTO_REPLY_ENABLED=false` → no customer replies (except recruitment)
- `RECRUITMENT_AUTOREPLY_ENABLED=true` → recruitment still replies when global auto-reply is off
- `AUTO_REPLY_SOURCES` → comma-separated list of allowed source names

**KB Coverage:** 0%

---

## Outbound Automated Reply Suffix — 0% Covered

**Source:** `app/bridge._AUTOMATED_SUFFIX`

Every auto-generated WhatsApp reply has this suffix appended:
```
─────────────────
🤖 Automated Reply System
এই বার্তাটি স্বয়ংক্রিয়ভাবে তৈরি হয়েছে। ভুল হতে পারে।
```

The system checks `_AUTOMATED_SUFFIX_ANCHOR` before appending to prevent double-append on retries.

**KB Coverage:** 0%

---

## RAG Engine — 15% Covered

**Module:** `modules/rag` (484 lines)

**Production Behavior:**
- Algorithm: BM25 (k1=1.5, b=0.75) — offline, no embedding service
- Bilingual tokenizer: `[A-Za-z0-9ঀ-৿]+`
- Chunk size: 320 characters, overlap: 60 characters
- Minimum token length: 2 characters
- Index rebuilt daily at 18:00 by scheduler job `rag_rebuild`
- Index sources: `core/resources/*.txt` + active rows from `fazle_knowledge_base` table
- Safety filters (3 layers):
  1. Directory exclusion (11 dir patterns)
  2. Filename keyword exclusion (11 filename patterns)
  3. Chunk-level safety (30+ internal marker patterns) — unsafe chunks purged entirely
- Stop words: 80+ Bangla + English function words removed
  - Note: লোক, পদে, নেওয়া, হচ্ছে were REMOVED from stop words because they appear in valid recruitment queries
- Audit ring buffer: last 50 queries in-memory (not persisted)

**KB Coverage:** `rag_strategy.md` (15%) mentions BM25+semantic and chunking guidance. `hybrid_search.md` describes ranking but not the actual BM25 algorithm or chunk parameters.

**Missing from KB:** BM25 params (k1, b), chunk size, overlap, 3-layer safety filter details, stop word rationale, ring buffer, excluded dirs/filenames.

**Enrichment Target:** `06_developer_system/rag_strategy.md` — enrich with technical parameters.

---

## AI Coverage Summary

| AI Component | KB Coverage |
|---|---|
| Reply generation chain (GitHub→Groq→Ollama) | 0% |
| Intent classification chain (Groq→GitHub→Ollama) | 0% |
| OLLAMA_REPLY_DISABLED behavior | 0% |
| LLM learning memory | 0% |
| Memory extractor | 0% |
| Role classifier (per-contact tone) | 0% |
| Recruitment AI brain | 0% |
| Reply templates (rotating) | 0% |
| Reviewed reply memory | 5% |
| AI safety gate / auto-reply gates | 0% |
| Automated reply suffix | 0% |
| RAG engine (BM25 + safety) | 15% |

**Average AI Behavior Coverage: 1.7%**
