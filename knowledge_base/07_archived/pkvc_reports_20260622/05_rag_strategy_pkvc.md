---
title: PKVC Report — rag_strategy.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — rag_strategy.md
**Article:** `06_developer_system/rag_strategy.md`
**Wave:** Wave-2B (full replacement of 16-line stub)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | BM25 k1 = 1.5 | VERIFIED | `modules/rag/__init__.py` line 47: `_K1 = 1.5` |
| 2 | BM25 b = 0.75 | VERIFIED | `modules/rag/__init__.py` line 48: `_B = 0.75` |
| 3 | CHUNK_SIZE = 320 | VERIFIED | `modules/rag/__init__.py` line 42: `CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "320"))` |
| 4 | CHUNK_OVERLAP = 60 | VERIFIED | `modules/rag/__init__.py` line 43: `CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "60"))` |
| 5 | MIN_TOKEN_LEN = 2 | VERIFIED | `modules/rag/__init__.py` line 44 |
| 6 | Tokenizer regex: `re.findall(r'[ঀ-৿]+\|[a-zA-Z0-9]+', text.lower())` | VERIFIED | `modules/rag/__init__.py` `_TOKEN_RE` constant |
| 7 | Stop words filter in `_tokenize()` | VERIFIED | Line 148: `return [t for t in toks if len(t) >= MIN_TOKEN_LEN and t not in _STOP_WORDS]` — `_STOP_WORDS` is large frozenset (Bangla + English) |
| 8 | 11 excluded directories in `_EXCLUDED_DIRS` frozenset | VERIFIED | `modules/rag/__init__.py` lines 52–56: exact 11 dirs confirmed |
| 9 | Implicit `_` prefix directory exclusion | VERIFIED | Line 202: `if name in _EXCLUDED_DIRS or name.startswith("_")` |
| 10 | 11 excluded filename keywords | VERIFIED | `_EXCLUDED_NAME_KEYWORDS` tuple lines 57–66: 11 keywords including `system_context` |
| 11 | 4 excluded file patterns | VERIFIED | `_EXCLUDED_FILE_PATTERNS`: `.bak`, `.backup`, `.old`, `.tmp` |
| 12 | 32 chunk-level unsafe patterns (PATCH 1–5) | VERIFIED | Full `_CHUNK_UNSAFE_PATTERNS` tuple read: 16 original + 8 PATCH 4 + 8 PATCH 5 = 32 total |
| 13 | RAG rebuild at 18:00 daily (`rag_rebuild` job) | VERIFIED | `modules/scheduler/__init__.py` `job_rag_rebuild()` with `RAG_REBUILD_HOUR` env |
| 14 | Two data sources: `resources/*.txt` + `fazle_knowledge_base` WHERE `is_active=true` | VERIFIED | `modules/rag/__init__.py` `build_index()` function |

## Pre-Correction Issues Found and Fixed

| # | Original Claim | Correction | Fixed In |
|---|---|---|---|
| C1 | "10 excluded directories" (config table, header) | Corrected to "11 directories" + added `_` prefix implicit rule | `rag_strategy.md` Excluded Directories section, 2026-06-22 |
| C2 | "16 chunk-level unsafe patterns" | Corrected to 32 patterns; PATCH 4 (8 patterns) and PATCH 5 (8 patterns) added to table | `rag_strategy.md` Chunk-Level Safety Filter section, 2026-06-22 |
| C3 | `_tokenize()` — stop words filter undocumented | Added `_STOP_WORDS` filter with partial stop word list | `rag_strategy.md` Tokenizer section, 2026-06-22 |

## Unverified / Legacy Claims

None.

## Certification Decision

**CERTIFIED** — 3 inaccuracies found (counts and undocumented stop words). All corrected during PKVC. All 14 claims now verified against production.
