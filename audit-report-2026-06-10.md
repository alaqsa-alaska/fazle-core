# Fazle Core — Audit Report 2026-06-10
**Version:** v1.2.0 — Continuous Learning Loop + User Context Engine
**Prepared by:** Claude Code (Session)
**System state at audit:** Production Healthy

---

## 1. SESSION SUMMARY

This session implemented Phases 1–6 of the GitHub Models integration roadmap, building a
continuous learning loop on top of the v1.1.0 dual-provider AI foundation.

| Phase | Description | Status |
|---|---|---|
| 1 | `generate_chat_reply()` in `github_models.py` + `llm.py` router + `main.py` import fix | ✅ Done |
| 2 | DB schema (4 new tables), phone normalizer, role classifier | ✅ Done |
| 3 | Memory extractor module, webhook integration, Phase 5 APIs | ✅ Done |
| 4 | Frontend tabs (User Mgmt, Memory Review) | ⏸ Backend ready; HTML/JS pending |
| 5 | `/api/users/*`, `/api/memory/*`, `/api/stats/llm` endpoints | ✅ Done |
| 6 | Scheduler jobs: `daily_memory_review` (09:00) + `rag_rebuild` (18:00) | ✅ Done |

---

## 2. BUGS FIXED

### 2.1 Critical — `llm.py` missing pass-throughs (silent AttributeError)
- **Impact:** After `main.py` switched to `from app import llm as ai` in Phase 1, three functions called via `ai.` were absent from `llm.py`: `check_ollama_health()`, `generate_rag_answer()`.
- **Affected endpoints:** `GET /health` (line 491), `GET /chat/models` (line 2063), `GET /rag/answer` (line 2032).
- **Fix:** Added pass-through functions in `app/llm.py` delegating to `app/ollama`.
- **Verified:** Health endpoint returns `"status": "ok"` post-restart.

### 2.2 Medium — User-supplied schema used SQLite syntax throughout
- All snippets used `?` placeholders, `get_db()` context manager, `INTEGER PRIMARY KEY AUTOINCREMENT`, `TIMESTAMP DEFAULT CURRENT_TIMESTAMP`.
- **Fix:** Converted to asyncpg/PostgreSQL throughout: `$1/$2`, `BIGSERIAL`, `TIMESTAMPTZ DEFAULT NOW()`, direct `fetch_one`/`fetch_all`/`execute` calls.

---

## 3. NEW FILES

| File | Type | Purpose |
|---|---|---|
| `app/llm.py` *(modified)* | Core | `generate_chat_reply`, `check_ollama_health`, `generate_rag_answer` pass-throughs |
| `app/github_models.py` *(modified)* | Core | `generate_structured_response()`, `generate_chat_reply()` |
| `app/main.py` *(modified)* | Core | Memory extraction hook in bridge handler; 8 new API endpoints |
| `modules/scheduler/__init__.py` *(modified)* | Module | 2 new jobs: `daily_memory_review`, `rag_rebuild` |
| `modules/phone_normalizer/__init__.py` | Module | BD phone normalisation |
| `modules/role_classifier/__init__.py` | Module | Per-contact role context + memory |
| `modules/memory_extractor/__init__.py` | Module | GitHub Models structured fact extraction |
| `db/migrations/016_user_profiles_memory.sql` | Migration | 4 new tables + indexes |
| `db/migrations/017_memory_promotion.sql` | Migration | `promoted_to_kb`, `dismissed` on `llm_learning_memory` |

---

## 4. DATABASE CHANGES

### Migration 016 — Applied ✅
```
user_profiles         — phone_canonical UNIQUE, name, role, relationship_type, notes
user_memory           — FK→user_profiles, memory_type, content, confidence, source
role_reply_styles     — per-role tone/length/greeting config
phone_normalization_log — audit log (raw → canonical)
```

### Migration 017 — Applied ✅
```
llm_learning_memory.promoted_to_kb  BOOLEAN DEFAULT FALSE
llm_learning_memory.promoted_at     TIMESTAMPTZ
llm_learning_memory.dismissed       BOOLEAN DEFAULT FALSE
+ partial index idx_llm_memory_pending
```

Both applied via:
```bash
docker exec -i ai-postgres psql "$DB" < db/migrations/016_user_profiles_memory.sql
docker exec -i ai-postgres psql "$DB" < db/migrations/017_memory_promotion.sql
```

---

## 5. NEW API ENDPOINTS

All protected by `X-Internal-Key` header.

```
GET  /api/users/search?q=           Phone or name search
GET  /api/users/{phone}             Full profile + memories
PUT  /api/users/{phone}             Upsert profile (role, name, notes)
POST /api/users/{phone}/memory      Add manual fact to user_memory
GET  /api/memory/pending            LLM replies pending KB promotion (promoted_to_kb=FALSE)
POST /api/memory/{id}/promote       Promote reply → fazle_knowledge_base
POST /api/memory/{id}/dismiss       Remove from review queue
GET  /api/stats/llm                 Provider usage stats from llm_learning_memory
```

---

## 6. SCHEDULER JOBS ADDED

| Job | Cron | Env override |
|---|---|---|
| `daily_memory_review` | `MEMORY_REVIEW_HOUR:00` (default 09:00 Asia/Dhaka) | `MEMORY_REVIEW_HOUR` |
| `rag_rebuild` | `RAG_REBUILD_HOUR:00` (default 18:00 Asia/Dhaka) | `RAG_REBUILD_HOUR` |

`daily_memory_review` logic:
1. Pull distinct `sender_number` from `wbom_whatsapp_messages` (last 24 h, inbound, up to 50 senders)
2. For each sender: fetch last 6 messages, build conversation list
3. Call `memory_extractor.extract_and_save_memory()` → GitHub Models structured JSON → `user_memory`
4. Sleep 4 s between senders (15 RPM free-tier rate limit)

`rag_rebuild` calls `modules.rag.build_index()` — picks up KB entries promoted during the day.

---

## 7. CONTINUOUS LEARNING CYCLE

```
09:00  daily_memory_review  — scan 24h conversations → GitHub Models → user_memory
       Throughout day       — each bridge message → background memory_extractor task
       Admin (any time)     — Memory Review tab → promote good replies to KB
18:00  rag_rebuild          — BM25 index picks up all new KB entries
```

Expected improvement curve:
- Day 1: Generic AI replies
- Day 7: Role-aware replies (role context from user_profiles)
- Day 30: Highly personalised, fact-anchored replies

---

## 8. HEALTH CHECK — POST-RESTART

```json
{
  "status": "ok",
  "probes": {
    "db":              { "status": "ok", "value": 1 },
    "bridge1_db":      { "status": "ok", "mtime_age_s": 86 },
    "bridge2_db":      { "status": "ok", "mtime_age_s": 450 },
    "bridge_poller_b1":{ "status": "ok", "age_s": 0 },
    "bridge_poller_b2":{ "status": "ok", "age_s": 5 },
    "outbound":        { "status": "ok", "pending": 0, "dlq": 0 },
    "disk":            { "status": "ok", "used_pct": 67, "free_gb": 67.5 },
    "mem":             { "status": "ok", "available_mb": 14519 },
    "ollama":          { "status": "ok", "models": ["qwen3:8b", "qwen2.5:3b"] },
    "llm": {
      "primary_provider": "github_models",
      "github_models": { "status": "ok", "model": "openai/gpt-4o-mini" },
      "ollama":         { "status": "ok", "active_model": "qwen2.5:3b" }
    }
  }
}
```

All 10 probes: **OK**. No degraded or critical probes.

---

## 9. PENDING / NOT DONE

| Item | Reason deferred |
|---|---|
| Phase 4 frontend (User Mgmt tab, Memory Review tab) | HTML/JS work; backend APIs are ready |
| `user_memory` unique constraint to prevent duplicate daily facts | Medium priority; get accumulation data first |
| Wire `get_user_context` into `message_router` for live role-aware auto-replies | Requires careful integration into complex router |
| `/v1/chat/completions` OpenAI-shim endpoint | Not started |
| Admin message learning (outbound reply tracking) | Not started |

---

## 10. ACTIVE FEATURE STATUS (Updated)

| Feature | Status | Notes |
|---|---|---|
| WhatsApp auto-reply | ✅ Active | AUTO_REPLY_ENABLED=true, bridges 1+2 |
| GitHub Models AI (primary) | ✅ Active | gpt-4o-mini, 3-token rotation, 15 RPM serialized |
| Ollama AI (fallback) | ✅ Active | qwen2.5:3b, auto-fallback |
| Chat Lab (`/chat/message`) | ✅ Fixed | Was broken (missing pass-throughs) — fixed v1.2.0 |
| LLM learning memory | ✅ Active | `llm_learning_memory` table, promoted_to_kb column added |
| Memory extraction (background) | ✅ Active | Per-message async task via `memory_extractor` |
| Daily memory review (scheduler) | ✅ Active | 09:00 Asia/Dhaka, GitHub Models, 4s rate-limit sleep |
| BM25 rebuild (scheduler) | ✅ Active | 18:00 Asia/Dhaka |
| User profiles + memory tables | ✅ Active | `user_profiles`, `user_memory` — migrations applied |
| Phone normalisation | ✅ Active | `modules/phone_normalizer` |
| Role classifier | ✅ Active | `modules/role_classifier` — `get_user_context()` |
| User Management API | ✅ Active | `/api/users/*` endpoints |
| Memory Review API | ✅ Active | `/api/memory/*` endpoints |
| LLM stats API | ✅ Active | `/api/stats/llm` |
| Draft creation | ✅ Active | DRAFT_CREATION_ENABLED=true |
| Recruitment flow | ✅ Active | GitHub Models backed |
| Payroll dashboard | ✅ Working | fazle.iamazim.com/payroll |
| Escort roster | ✅ Working | fazle.iamazim.com/escort-roster |
| RAG / knowledge base | ✅ Active | BM25 index, daily rebuild scheduled |
| Frontend User Mgmt / Memory Review tabs | ❌ Pending | Backend ready, HTML/JS not done |
| Role-aware WhatsApp replies (live) | ❌ Pending | Modules exist, not wired into message_router yet |

---

*Report generated: 2026-06-10. Previous audit: 2026-06-08.*
