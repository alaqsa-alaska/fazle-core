---
title: PKVC Report — automation_pipeline.md
owner: Fazle Core Admin
status: archived
last_verified: 2026-06-24
runtime_index: false
---

# PKVC Report — automation_pipeline.md
**Article:** `06_developer_system/automation_pipeline.md`
**Wave:** Wave-1 (initial) + Wave-2B (enriched)
**PKVC Date:** 2026-06-22
**Verification Standard:** Three-level Evidence

---

## Critical Claims Verified

| # | Claim | Status | Evidence |
|---|---|---|---|
| 1 | Draft quality gate has exactly 4 criteria | VERIFIED | `modules/draft_quality/__init__.py` `check_draft_quality()`: empty, llm_fallback, bad_pattern, too_long — exactly 4 |
| 2 | BAD_PATTERNS has exactly 8 entries | VERIFIED | `modules/draft_quality/__init__.py` lines 46–56: `file://`, `/home/azim`, `Created [](`, `Traceback (most recent call last)`, `` ``` ``, `<\|`, `/scripts/`, `/venv/` |
| 3 | MAX_DRAFT_LEN = 4000 | VERIFIED | `modules/draft_quality/__init__.py` line 57 |
| 4 | Two LLM fallback exact strings | VERIFIED | `LLM_FALLBACK_EXACT` and `LLM_FALLBACK_EXACT_V2` — lines 36–41 |
| 5 | Emoji stripping before evaluation (`strip_reply_emoji()`) | VERIFIED | `modules/draft_quality/__init__.py` — `strip_reply_emoji()` function; called before gate checks |
| 6 | `DRAFT_QUALITY_GATE=false` disables gate | VERIFIED | `_gate_enabled()` function reads env flag |
| 7 | 15 scheduler jobs registered | VERIFIED | `modules/scheduler/__init__.py` — 10 core jobs + 5 conditional (daily_admin_digest, daily_db_backup, lock_cleanup, draft_ttl_cleanup, bridge_watchdog). All 15 registered via `register_job()` |
| 8 | Memory extractor is fire-and-forget asyncio task | VERIFIED | `modules/memory_extractor/__init__.py` — `asyncio.create_task(extract_and_save_memory(...))` pattern |
| 9 | `daily_memory_review` runs at 09:00 with 50-sender limit and 4s sleep | VERIFIED | `modules/scheduler/__init__.py` `job_daily_memory_review()` — `MEMORY_REVIEW_HOUR` env, 50 senders, `await asyncio.sleep(4)` |
| 10 | GitHub Models → Groq → Ollama → holding message for reply chain | VERIFIED | `app/llm.py` `generate_reply()` — provider fallback chain documented |
| 11 | Groq → GitHub → Ollama for intent classification (different order) | VERIFIED | `app/llm.py` intent classification chain |
| 12 | Scheduler timezone = Asia/Dhaka | VERIFIED | `modules/scheduler/__init__.py` `SCHEDULER_TIMEZONE` |

## Unverified / Legacy Claims

None.

## Pre-Correction Issues

None. No corrections required.

## Certification Decision

**CERTIFIED** — All 12 critical claims verified against production code.
