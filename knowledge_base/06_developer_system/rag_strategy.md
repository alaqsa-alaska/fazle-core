---
title: RAG Strategy — Fazle AI Platform
owner: Fazle Core Admin
status: active
last_verified: 2026-06-24
runtime_index: true
---

# RAG Strategy — Fazle AI Platform

**Article Type:** Developer System Reference
**Visibility:** Developer / Admin
**Source Module:** `modules/rag/__init__.py`
**Production Status:** ACTIVE — index built on startup and rebuilt daily
**Wave:** Wave-2B | 2026-06-22
**Traceability:** PKMA Report 18 (DEV-07 — enriched from Level 1)

---

## Visibility and AI Exposure Rules

**Visibility:** Developer
**AI Exposure:** BM25 parameters, chunk sizes, excluded directories, safety patterns, and RAG pipeline internals must NEVER be exposed via AI responses. The existence of a RAG knowledge base may be mentioned to admin only.

---

## Purpose

The RAG (Retrieval-Augmented Generation) layer provides context-relevant knowledge retrieval for the LLM reply generation pipeline. It runs entirely in-process (no external embedding service) using BM25 keyword search with a Unicode-aware tokenizer. The design is offline-first: the index is rebuilt from local files and the database, with no network calls during query.

**RAG answers:** "Given an inbound message, what knowledge should be injected as context into the LLM prompt?"

---

## Data Sources Indexed

Two sources are merged into a single in-memory index:

| Source | Location | Format |
|---|---|---|
| Plain-text knowledge files | `resources/*.txt` | Chunked by character size |
| Active KB rows from database | `fazle_knowledge_base` WHERE `is_active=true` | key + reply_text concatenated |

---

## Configuration Parameters

All parameters are production-verified from `modules/rag/__init__.py`.

| Parameter | Value | Env Override | Notes |
|---|---|---|---|
| Chunk size | 320 characters | `RAG_CHUNK_SIZE` | Per chunk, before overlap |
| Chunk overlap | 60 characters | `RAG_CHUNK_OVERLAP` | Shared characters between adjacent chunks |
| Min token length | 2 characters | Hardcoded | Tokens shorter than 2 chars are discarded |
| BM25 k1 | 1.5 | Hardcoded | Term frequency saturation parameter |
| BM25 b | 0.75 | Hardcoded | Length normalization parameter |
| Rebuild schedule | 18:00 daily | `RAG_REBUILD_HOUR` | Via scheduler `rag_rebuild` job |
| Resources directory | `fazle-core/resources/` | Hardcoded | Root of plain-text knowledge files |

**BM25 scoring formula:**
```
score = IDF(q_term) × (tf × (k1+1)) / (tf + k1 × (1 - b + b × doc_len/avg_len))
```
where `k1=1.5`, `b=0.75`, and IDF uses standard log-ratio formula.

---

## Tokenizer

**Algorithm:** Unicode-aware regex split — bilingual (Bangla + English)
**Pattern:** `re.compile(r"[A-Za-z0-9ঀ-৿]+", re.UNICODE)` applied via `findall()` on `text.lower()`
- Single character class covering ASCII alphanumeric (`A-Za-z0-9`) and Bangla Unicode block (`ঀ–৿`, equivalent to `ঀ-৿`) — functionally matches Bangla and English tokens separately since mixed-script tokens do not occur in practice
- Lowercases all ASCII tokens
- Filters tokens shorter than `MIN_TOKEN_LEN = 2`
- Filters tokens present in `_STOP_WORDS` (large frozenset of Bangla conjunctions, particles, pronouns, postpositions + English function words)

**Stop words include (partial list):**
- Bangla conjunctions/particles: এবং, বা, কিন্তু, তবে, যদি, তাহলে, তখন, কারণ, তাই, আর
- Bangla pronouns: আমি, আমার, আপনি, আপনার, সে, তার, তারা
- Bangla postpositions: থেকে, দিয়ে, উপর, নিচে, ভেতরে, জন্য, কাছে, সাথে
- English function words: the, a, an, and, or, but, in, on, at, to, for, of, is, are, was, were...

**Source Function:** `_tokenize(text)` in `modules/rag/__init__.py` — line 147: `return [t for t in toks if len(t) >= MIN_TOKEN_LEN and t not in _STOP_WORDS]`

---

## Excluded Directories

The following **11 directories** under `resources/` are NEVER indexed, even if they contain `.txt` files:

| Directory | Reason |
|---|---|
| `_internal_archived` | Archived internal content |
| `_internal` | Internal-only files |
| `prompts` | LLM prompt templates — not customer-safe |
| `debug` | Debug output |
| `tests` | Test data |
| `drafts` | Draft content |
| `internal` | Internal content |
| `ai` | AI training content |
| `training` | Training data |
| `examples` | Example files |
| `temp` | Temporary files |

**Additional implicit rule (PKVC-corrected 2026-06-22):** Any directory whose name begins with `_` is also silently excluded even if not listed above. Source: `modules/rag/__init__.py` line 202: `if name in _EXCLUDED_DIRS or name.startswith("_")`.

**Excluded filename keywords** (case-insensitive substring match — file is blocked if filename contains any of these):

`analysis`, `prompt`, `intent`, `debug`, `test`, `sample`, `chain`, `reasoning`, `internal`, `archived`, `system_context`

**Excluded file patterns** (blocks non-authoritative backup files):

`.bak`, `.backup`, `.old`, `.tmp`

**Source Constants:** `_EXCLUDED_DIRS`, `_EXCLUDED_NAME_KEYWORDS`, `_EXCLUDED_FILE_PATTERNS`

---

## Chunk-Level Safety Filter

Before any chunk is added to the index, it is tested against chunk-level unsafe patterns. A chunk containing ANY of these strings is silently excluded from the index.

**Total patterns: 32** (original 16 + PATCH 4 additions + PATCH 5 additions)

| Pattern | PATCH | Risk Prevented |
|---|---|---|
| `এআই-এর বিশ্লেষণ` | Original | Internal AI analysis exposed |
| `এআই-এর ইনটেন্ট` | Original | Internal intent analysis exposed |
| `\| :--- \|` | Original | Internal table format (markdown analysis table) |
| `chain_of_thought` | Original | LLM chain-of-thought leaked |
| `Intent)` | Original | Intent classification artifact |
| `প্রার্থীর মেসেজ` | Original | Candidate message template |
| `প্রার্থীর সম্ভাব্য প্রশ্ন` | Original | Candidate question template |
| `Semantic Analysis` | Original | Internal semantic analysis |
| `Tokenization` | Original | Internal NLP terminology |
| `RAG pipeline` | Original | Internal RAG reference |
| `LLM pipeline` | Original | Internal LLM reference |
| `prompt template` | Original | Prompt template content |
| `OCR raw` | Original | Raw OCR output |
| `বিশ্লেষণ (Intent)` | Original | Intent analysis in Bangla |
| `reasoning_trace` | Original | LLM reasoning artifact |
| `AI ব্যবহারের জন্য বিশেষ নির্দেশিকা` | Original | Internal AI instruction marker |
| `এআই ব্যবহারের নির্দেশিকা` | PATCH 4 | Additional internal instruction marker |
| `CASE A —` | PATCH 4 | Internal case analysis pattern |
| `CASE B —` | PATCH 4 | Internal case analysis pattern |
| `এআই-এর ইনটেন্ট অনুধাবন` | PATCH 4 | Intent comprehension annotation |
| `এআই-এর প্রতিক্রিয়া (Action)` | PATCH 4 | Action classification annotation |
| `AI সিস্টেমের বিশেষত্ব` | PATCH 4 | AI system characteristic marker |
| `AI-এর একটি বিশেষ অটো-রিপ্লাই` | PATCH 4 | Auto-reply system instruction |
| `অটো-রিপ্লাই সিস্টেমের জন্য` | PATCH 4 | Auto-reply system instruction |
| `এআই উত্তর` | PATCH 5 | Inline AI answer annotation found in employee policy file |
| `AI উত্তর` | PATCH 5 | Inline AI answer annotation |
| `উত্তর — প্রার্থী` | PATCH 5 | Candidate-targeted answer annotation |
| `উত্তর — কর্মচারী` | PATCH 5 | Employee-targeted answer annotation |
| `The AI manages` | PATCH 5 | English AI instruction text |
| `that the AI tracks` | PATCH 5 | English AI instruction text |
| `the AI tracks` | PATCH 5 | English AI instruction text |
| `AI manages internal` | PATCH 5 | English AI instruction text |

**Source Constant:** `_CHUNK_UNSAFE_PATTERNS` in `modules/rag/__init__.py`

**Behavior:** Unsafe chunks are logged (`[RAG_CHUNK_PURGED]`) and silently excluded. They never appear in search results and are never sent to customers.

---

## Public API

| Function | Purpose | Notes |
|---|---|---|
| `await build_index()` | (Re)build the full in-memory index | Clears existing index; re-reads all sources |
| `await ensure_index()` | Build if not yet built | Safe to call on every request; no-op if index exists |
| `await search(q, k=5, min_score=0.0)` | Return top-k matching chunks | Returns list of dicts with chunk text + score |
| `await answer(q, k=3, min_score=1.0)` | Return {answer, citations} or None | Returns None if no chunk meets min_score |
| `await stats()` | Return diagnostics dict | Includes chunk_count, last_build_time, chunk_size, chunk_overlap |
| `await rebuild_index()` | Force full rebuild with wipe | Used by `rag_rebuild` scheduler job |
| `await recent_searches()` | Return last 50 RAG search audit records | Ring buffer; for incident debugging only — Developer visibility |

**Source Module:** `modules/rag/__init__.py`

---

## Rebuild Schedule

The `rag_rebuild` scheduler job runs at **18:00 daily** (`RAG_REBUILD_HOUR` env). It calls `rebuild_index()` which:
1. Acquires an exclusive rebuild lock (prevents concurrent rebuilds)
2. Wipes the existing index
3. Re-reads all `resources/*.txt` files (excluding blocked dirs/files/patterns)
4. Re-reads all active `fazle_knowledge_base` rows
5. Chunks, tokenizes, filters for safety
6. Rebuilds BM25 index in-memory

**Duration:** < 1 second for small corpus (< 10 MB) per design constraint.

---

## Integration with LLM Pipeline

RAG results are injected as context into the LLM prompt before reply generation:

1. `ensure_index()` is called at startup
2. For every inbound message: `answer(q=message_text, k=3, min_score=1.0)` is called
3. If answer is found: answer text + citations are injected into the LLM system prompt context
4. LLM generates reply using the RAG context
5. Generated reply goes through draft quality gate before saving/sending

**Key constraint:** `min_score=1.0` for `answer()` — only results above this threshold are used. Lower-scoring results are silently discarded rather than injected as possibly-wrong context.

---

## Corpus Safety Guarantee

The RAG system has 3 defense layers that prevent internal content from reaching customers:

1. **File-level exclusion** — blocked directories, filename keywords, file patterns
2. **Chunk-level exclusion** — 32 unsafe content patterns (original 16 + PATCH 4 + PATCH 5)
3. **Draft quality gate** — post-generation bad-pattern filter in `modules/draft_quality`

This means even if a knowledge file is accidentally placed in the resources directory with internal content, it will be filtered at ingestion (chunk safety) and at output (draft quality gate) before reaching the customer.

---

## Related Articles

- `06_developer_system/automation_pipeline.md` — rag_rebuild scheduler job + LLM pipeline
- `06_developer_system/database_rules.md` — Domain 9 (AI) — `fazle_knowledge_base` table
- `hybrid_search.md` — Extended search strategy documentation

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Wave-2B: Full enrichment from production code read. Previous stub (16 lines) replaced. | KSP Wave-2B |
| 2026-06-22 | RAG-001 (Management Approved): DOC-01 log label `[RAG_CHUNK_UNSAFE]` → `[RAG_CHUNK_PURGED]`; DOC-02 added `recent_searches()` to Public API table; DOC-03 corrected tokenizer regex to single-character-class form matching production; DOC-04 corrected Corpus Safety Guarantee pattern count 16 → 32. No business rules changed. | Session 2 Architect |
