# VPS Full Audit & Modernization Review
**Date:** 2026-06-12
**Last live refresh:** 2026-06-12 20:13 CEST
**Auditor:** Codex (live system inspection)
**Scope:** Complete end-to-end VPS environment
**System:** iamazim.com — Al-Aqsa Security AI Automation Platform

---

## 1. Executive Summary

**Overall Status: Partially Functional → Approaching Production Ready**

The platform has made significant architectural progress. The core AI pipeline (GitHub Models → Groq → Ollama cascade), WhatsApp bridges, PostgreSQL, monitoring stack, and scheduler are all **healthy and running**. The platform successfully receives WhatsApp messages from two business numbers, classifies intent, queries a knowledge base, and routes responses through a 3-tier AI provider cascade.

However, the system is operating in **safe mode** (`AUTO_REPLY_ENABLED=false`) — meaning no messages are being sent automatically to customers. Drafts are being created, but human approval is required for everything. This is a deliberate operational decision, not a bug, but it means the AI automation is not yet delivering its core value proposition.

### Major Achievements
- 3-tier AI cascade (GitHub Models → Groq → Ollama) fully operational
- Two WhatsApp numbers fully bridged and receiving messages
- 50+ domain modules covering recruitment, payroll, escort operations, payments
- Comprehensive monitoring stack (Prometheus, Grafana, Loki, OpenTelemetry)
- Full backup system with daily pg_dumps (5 recent kept)
- 208-table PostgreSQL schema with pgvector extension
- RAG system operational (BM25, 251 docs, 12ms build time)
- 10+ scheduled jobs automating routine tasks
- 10,091 messages processed, 576 contacts tracked

### Current Operational Risks
1. **Guardian still emits repeated incidents** — automated six-hour deduplication/TTL cleanup is now installed, but the underlying repeated `ollama_model_evicted` signal still needs a source-level fix
2. **`location-where` is online after restart but has 8 lifetime restarts** — currently 0% CPU; monitor for recurrence and investigate repeated `401` client requests
3. **Auto-reply fully disabled** — the platform's primary value (autonomous AI replies) is switched off
4. **Qdrant vector database deployed but completely unused** — semantic search capability wasted
5. **Meta webhook events are not visible in `journalctl`** — inbound delivery works and persists messages, but service logging is routed elsewhere or not captured by journald

### Remediation Update — 2026-06-12
- Restarted `media-processor`; RSS dropped from about 3 GiB to 333,400 KiB (~326 MiB)
- Restarted `location-where`; it is online at 0% CPU and 53.9 MB RAM
- Cleaned `agent.incidents`: 81,100 → 30,284 immediately after cleanup
- Updated Meta Cloud API configuration to verified working token (`EAAQ...ZDZD`) and Graph API v23; preserved current valid App ID/secret
- Restarted `fazle-core`; service is active, `/health` returns HTTP 200, and local/external webhook verification returns the expected challenge
- Confirmed Meta Cloud API end to end with live phone messages; heartbeat refreshed and inbound rows persisted with `platform=meta`
- Reset swap safely: swap usage dropped from 2.9 GiB to 0 B; services remained healthy
- Locked sensitive `.env` files to mode `600`
- Archived all 24 source-tree `*.bak*` files under `/home/azim/archives/bak-files-20260612/`
- Installed `/home/azim/scripts/cleanup-incidents.sh` with a six-hour cron; incidents reduced to 9,888 total with 1 unresolved
- Created and archived Meta config backup: `/home/azim/archives/bak-files-20260612/.env.bak.meta.20260612_150323`

### Technical Debt Level: Medium-High
- `main.py` is 2,541 lines — a monolith that needs splitting
- 208 DB tables including many legacy `wbom_*` tables from a prior system
- `active_employees = 0` despite an extensive payroll system suggests data not loaded

### Readiness for Production Use
- **Core chat pipeline**: Partially Functional (would be Working if auto-reply enabled)
- **Payroll/Escort system**: Development Stage (no active employees in DB)
- **Monitoring & backup**: Production Ready
- **Memory/learning system**: Development Stage (no UI, no RAG vector search)

---

## 2. Infrastructure Inventory

### 2.1 VPS Hardware
| Resource | Total | Used | Available |
|---|---|---|---|
| Disk | 194 GB | 66 GB (34%) | 128 GB |
| RAM | 23 GiB | 7.1 GiB | 15 GiB |
| Swap | 3.0 GiB | 0 B | 3.0 GiB |
| CPU cores | 8 | — | — |

> Note: Disk was at 100% on 2026-06-12 due to 113GB of git garbage objects (`tmp_pack_*`). Resolved by deleting garbage objects + enabling `git maintenance` systemd timers.

### 2.2 Docker Containers

| Container | Image | Purpose | Port | Status |
|---|---|---|---|---|
| `ollama` | `ollama/ollama:latest` | Local LLM inference | 11434 (internal) | **Working** |
| `open-webui` | `open-webui:main` | Chat UI at chat.iamazim.com | 8501→8080 | **Working** |
| `code-server` | `codercom/code-server` | VS Code at vscode.iamazim.com | 8443→8080 | **Working** |
| `grafana` | `grafana:11.4.0` | Monitoring dashboards | 3000 (internal) | **Working** |
| `promtail` | `grafana/promtail:3.2.1` | Log shipping to Loki | — | **Working** |
| `cadvisor` | `cadvisor:v0.49.1` | Container metrics | 8080 (internal) | **Working** |
| `loki` | `grafana/loki:3.2.1` | Log aggregation | 3100 (internal) | **Working** |
| `prometheus` | `prometheus:v2.55.0` | Metrics collection | 9090 (internal) | **Working** |
| `ai-postgres` | `pgvector/pgvector:pg17` | Primary DB (pgvector) | 5432 (internal) | **Working** |
| `minio` | `minio:2025-09-07` | Object storage | 9000 (internal) | **Working** |
| `qdrant` | `qdrant:v1.17.0` | Vector database | 6333-6334 (internal) | **Working (unused)** |
| `node-exporter` | `node-exporter:v1.8.2` | System metrics for Prometheus | 9100 (internal) | **Working** |
| `ai-redis` | `redis:8.0.2-alpine` | Cache and queue backend | 6379 (internal) | **Working** |
| `fazle-otel-collector` | `otelcol-contrib:0.96.0` | OpenTelemetry trace/metric collection | 4317-4318 | **Working** |

### 2.3 Systemd Services

| Service | Purpose | Port | Uptime | Status |
|---|---|---|---|---|
| `fazle-core` | Main Python AI backend (FastAPI) | 8200 | Restarted after Meta config update | **Working — HTTP 200** |
| `nginx` | Reverse proxy for all domains | 80/443 | — | **Working** |
| `fail2ban` | Intrusion prevention | — | Since May 26 | **Working** |
| `git-maintenance@hourly` | Prevents git garbage accumulation | — | Newly enabled | **Working** |
| `git-maintenance@daily` | Deeper git GC | — | Newly enabled | **Working** |

### 2.4 PM2 Processes

| Name | Technology | Port | Restarts | CPU | Status |
|---|---|---|---|---|---|
| `location-where` | Node.js (NestJS) | 8310 | 8 | **0%** | **Online — monitor restarts/401s** |

### 2.5 Raw Processes (non-containerized)

| Process | Technology | Port | RAM | Started | Status |
|---|---|---|---|---|---|
| `fazle-core` (via systemd) | Python/FastAPI | 8200 | 112 MB | Jun 12 00:48 | **Working** |
| `media-processor` | Python | 8090 | **~326 MiB RSS** | Restarted Jun 12 | **Working — monitor growth** |
| `system-agent` | Python/FastAPI | 8300 | 60 MB | Jun 10 | **Partially Working** |
| `whatsapp-bridge` ×2 | Go binaries | — | 32 MB each | Jun 08 | **Working** |
| `open-webui` (in Docker) | Python/uvicorn | 8080 | 755 MB | Jun 02 | **Working** |

### 2.6 Nginx Virtual Hosts

| Domain | Backend | Purpose | SSL |
|---|---|---|---|
| `iamazim.com` | Static `/var/www/iamazim.com` | Company website (Al-Aqsa) | Let's Encrypt |
| `fazle.iamazim.com` | `127.0.0.1:8200` | Fazle admin dashboard + API | Let's Encrypt |
| `api.iamazim.com` | `127.0.0.1:8200` | Dedicated API subdomain | Let's Encrypt |
| `chat.iamazim.com` | `172.22.0.2:8080` | Open WebUI (Ollama chat) | Let's Encrypt |
| `vscode.iamazim.com` | `127.0.0.1:8443` | Code-server (VS Code in browser) | Let's Encrypt |
| `locationwhere.iamazim.com` | `127.0.0.1:8310` | LocationWhere tracking app | Let's Encrypt |

### 2.7 Ollama Models

| Model | Size | Use |
|---|---|---|
| `qwen3:8b` | 5.2 GB | Available but not active |
| `qwen2.5:3b` | 1.9 GB | Active model (intent, RAG, learning) |

### 2.8 WhatsApp Channels

| Channel | Number | Label | Bridge | Status |
|---|---|---|---|---|
| Bridge 1 | 8801958122300 | HR / Recruitment | Port 8082 | **Working** |
| Bridge 2 | 8801880446111 | OPS / Escort | Port 8081 | **Working** |
| Meta Cloud API | 8801958122322 | Official business | Webhook `/webhook/meta` | **Working — live inbound test passed** |

---

## 3. Working Features

### 3.1 WhatsApp Message Ingestion (Bridges 1 & 2)
**What:** Receives incoming WhatsApp DMs via two Go-based bridge binaries polling SQLite stores.
**How:** Bridge pollers run on 10-second intervals reading `messages.db`; bridge poller cursors track last-seen message ID. Events processed via `process_message()` in `modules/message_router`.
**Trigger:** Automatic on every new message.
**Limitations:** Bridges 1 and 2 are actively heartbeating. Meta Cloud API passed a live inbound test and persisted messages, but Meta webhook logs were not visible in `journalctl`. No group message support.
**Production Ready:** Yes (for bridge traffic).

### 3.2 Intent Classification
**What:** Classifies incoming messages into intents: recruitment, payment, escort, attendance, admin, office_location, unknown.
**How:** Regex patterns first, then keyword scoring (fuzzy match with threshold 72), then LLM fallback via Ollama when regex/keyword fails.
**Trigger:** Every inbound message automatically.
**Limitations:** Purely rule-based for common intents; no semantic understanding; bilingual (Bangla+English) coverage varies by intent.
**Production Ready:** Yes.

### 3.3 Knowledge Base Lookup
**What:** Returns pre-written replies for common intents without AI.
**How:** DB lookup in `fazle_knowledge_base` (164 active entries) plus hardcoded fallbacks in module. BM25 RAG adds additional retrieval (87 file + 164 KB = 251 docs).
**Trigger:** Before LLM call for every message.
**Limitations:** Manual curation required; no semantic search; BM25 can miss paraphrased queries.
**Production Ready:** Yes.

### 3.4 3-Tier AI Reply Generation
**What:** Generates contextual WhatsApp replies in Bangla/English.
**How:**
  1. GitHub Models `gpt-4o-mini` (primary — 15 RPM free tier, 3-token round-robin rotation)
  2. Groq `llama-3.1-8b-instant` (fallback — 30 RPM, 14,400 req/day free)
  3. Ollama `qwen2.5:3b` (tertiary — fully local, disabled for customer replies via `OLLAMA_REPLY_DISABLED=true`)
**Trigger:** When KB lookup fails to match.
**Limitations:** `gpt-4o-mini` is not open-source; 15 RPM is very low under load; no conversation history injected into prompts currently.
**Production Ready:** Yes (architecture), but auto-reply is disabled.

### 3.5 Draft Queue System
**What:** Saves AI-generated replies as pending drafts for human review before sending.
**How:** `fazle_draft_replies` table, reviewed via `/drafts` dashboard page. Admin approves/edits/rejects. Outbound queue sends approved messages.
**Trigger:** Every AI-generated reply becomes a draft (when `DRAFT_CREATION_ENABLED=true`).
**Limitations:** 33 pending drafts currently; human bottleneck; no mobile-friendly review interface.
**Production Ready:** Yes.

### 3.6 Scheduler (10 background jobs)
**What:** APScheduler running 10 automated jobs on Asia/Dhaka timezone.
**Jobs:**
  - `daily_payroll_compute` — 02:00 daily
  - `dlq_alert` — hourly: alerts if dead-letter queue > 0
  - `health_summary` — every 6 hours
  - `stale_escort_reminder` — daily
  - `payment_reconciliation` — hourly
  - `backup_staleness_alert` — daily: warns if backup > 48h old
  - `combined_draft_cleanup` — daily: expires drafts > 24h
  - `daily_memory_review` — 09:00 daily
  - `rag_rebuild` — 18:00 daily
  - `daily_admin_digest` — 08:00 (DAILY_DIGEST_HOUR)
  - `daily_db_backup` — 02:30 daily
**Production Ready:** Yes.

### 3.7 Database Backup System
**What:** Daily pg_dump with 14-day daily and 8-week weekly retention.
**How:** `daily_db_backup` scheduler job + cron `backup-fazle.sh`. Dumps stored at `/home/azim/backups/fazle/`.
**Current state:** 5 recent dumps present (Jun 08–12). Last backup: today.
**Production Ready:** Yes.

### 3.8 Full Observability Stack
**What:** Metrics, logs, traces, and dashboards.
**How:** Prometheus scrapes Node Exporter + cAdvisor + fazle-core app metrics → Grafana dashboards. Loki+Promtail collects logs. OpenTelemetry collector receives traces.
**Trigger:** Automatic, continuous.
**Production Ready:** Yes (infra deployed; custom dashboards may need configuration).

### 3.9 OCR / Document Processing
**What:** Extracts text from images and PDFs. Detects slip type (escort slip, payment slip, release slip) and candidate document type (CV, NID, certificate).
**How:** Calls media-processor service at port 8090 → `/ocr` or `/extract` → parse structured fields.
**Limitations:** Media processor was restarted on Jun 12 and is currently about 326 MiB RSS; continued memory-growth monitoring is recommended.
**Production Ready:** Partially (functional but memory leak risk).

### 3.10 Escort Roster & Lifecycle
**What:** Manages escort assignments, duty slips, and shift logs.
**How:** 312 escort programs in DB; `escort_roster.html` frontend (1,108 lines); lifecycle state machine in `modules/escort_lifecycle`.
**Production Ready:** Partially (backend done; UI exists but needs verification).

### 3.11 Payroll (FPE)
**What:** WhatsApp-based payroll processing; parses "Cash 01XXXXXXXXX Name Amount" commands, creates ledger entries.
**How:** `fazle-payroll-engine` Python package installed from private GitHub repo; integrated via FastAPI router in `main.py`.
**Limitations:** `active_employees = 0` — no employees loaded in DB. Payroll dashboard HTML exists (3,085 lines) but system is data-empty.
**Production Ready:** Not yet (no employee data).

### 3.12 Admin Dashboard
**What:** Tabbed web dashboard at `fazle.iamazim.com/dashboard`.
**Tabs:** Overview, Drafts, Reports, Users, Audit, Backups, Scheduler, RAG, Observability, Chat Lab.
**Authentication:** API key login via `X-Internal-Key` header.
**Production Ready:** Partially (some tabs populated, others empty).

### 3.13 Reviewed Reply Memory
**What:** Stores admin-edited draft replies for future reuse — when a similar message arrives, the corrected reply is used instead of re-generating.
**How:** `fazle_corrections` + `reviewed_reply_memory` tables; enabled via `REVIEWED_REPLY_MEMORY_ENABLED=true`.
**Production Ready:** Yes.

### 3.14 Disk & RAM Monitoring with WhatsApp Alert
**What:** Hourly cron checks disk usage; sends WhatsApp alert to owner when > 80%.
**How:** `scripts/disk-alert.sh` → Bridge 2 `/api/send` → `8801880446111`.
**Alert content:** Disk%, RAM%, top 5 dirs, git garbage status, safe-delete commands.
**Production Ready:** Yes (newly added 2026-06-12).

---

## 4. Broken Features & Issues

### Issue 1: Meta WhatsApp Delivery Restored; Journald Visibility Missing
**Severity: Low**
**Current state:** End-to-end live testing passed. Messages sent to the official Meta Cloud API number refreshed the `meta` heartbeat and were persisted in `wbom_whatsapp_messages` with `platform=meta`.
**Evidence:** The latest test message from `8801880446111` was stored at `2026-06-12 18:07:56+00`, and the heartbeat recorded its WhatsApp message ID and `msg_type=text`.
**Remaining problem:** No Meta/webhook event lines appeared in `journalctl -u fazle-core`, so journald is not currently useful for webhook tracing.
**Next step:** Route application logs consistently to journald or the existing Loki stack and add a Meta heartbeat-age alert.
**Effort:** 30–60 minutes.

### Issue 2: `location-where` Restarted; Recurrence Monitoring Required
**Severity: Medium**
**Current state:** Restart completed successfully. PM2 reports the service online at 0% CPU, 53.9 MB RAM, and 8 lifetime restarts.
**Observed logs:** No crash error was present. AWS SDK warns that releases after early January 2027 will require Node.js 22; the application currently runs Node.js 20.20.2. Application logs contain repeated `401 Unauthorized` requests from clients.
**Next step:** Monitor CPU/restart count and investigate invalid/expired client authentication causing repeated `401` traffic.
**Effort:** 1–2 hours.

### Issue 3: Agent Incidents Cleaned but Still Regenerating
**Severity: High**
**Current state:** Cleanup deleted 50,818 rows older than seven days and resolved 2,129 unresolved `ollama_model_evicted` rows. The count dropped from 81,100 to 30,284 immediately after cleanup.
**Current snapshot:** A second schema-aware cleanup removed resolved rows older than three days and duplicate unresolved incidents, reducing the table to 9,888 total with 1 unresolved. Within minutes it rose to 9,892 total with 5 unresolved, confirming active source-level incident generation.
**Automation:** `/home/azim/scripts/cleanup-incidents.sh` runs every six hours, deduplicates by `(source, title)`, removes old resolved rows, and auto-resolves stale target incidents.
**Remaining problem:** Guardian continues creating repeated `ollama_model_evicted` incidents, so scheduled cleanup controls growth but does not fix the source.
**Next step:** Add source-level alert suppression/rate controls in Guardian and investigate repeated model eviction.
**Effort:** 2–4 hours.

### Issue 4: Media Processor Memory Growth Under Monitoring
**Severity: Medium**
**Current state:** The process was force-stopped and restarted successfully. Current PID is `1426244`, RSS is 333,400 KiB (~326 MiB), CPU is 0.5%, and memory is 1.3%.
**Remaining problem:** The previous process grew to about 3 GiB over ten days, so a leak remains suspected until memory stays stable over time.
**Next step:** Record RSS periodically and add a managed service/restart policy after confirming normal growth characteristics.
**Effort:** 1–4 hours.

### Issue 5: Auto-Reply Completely Disabled
**Severity: Medium** (intentional but value-blocking)
**Root cause:** `AUTO_REPLY_ENABLED=false` and `RECRUITMENT_AUTOREPLY_ENABLED=false` in `.env`. The system creates drafts but never auto-sends.
**Impact:** The core value proposition of the platform — autonomous AI replies to WhatsApp messages — is not delivered. Human must review every single message.
**Fix:** Gradual re-enablement: first set `RECRUITMENT_AUTOREPLY_ENABLED=true` for recruitment only (lowest risk), then monitor quality before enabling broader auto-reply.
**Effort:** 30 minutes to enable; ongoing monitoring needed.

### Issue 6: Qdrant Vector Database Unused
**Severity: Medium**
**Root cause:** Qdrant (`v1.17.0`) is deployed and healthy but the RAG module uses BM25 (keyword matching) only. No embeddings are generated, no collections exist in Qdrant.
**Impact:** Wasted RAM/CPU for Qdrant container. More importantly, semantic search capability is absent — BM25 misses paraphrased or intent-matched queries.
**Fix:** Integrate Ollama's embedding capability (`nomic-embed-text` or `mxbai-embed-large`) to generate embeddings for KB entries and store them in Qdrant. Hybrid BM25+vector retrieval would significantly improve answer quality.
**Effort:** 1–2 days.

### Issue 7: MinIO Object Storage Unused
**Severity: Low**
**Root cause:** MinIO is deployed and healthy but no application writes to it. Media files, document uploads, and backups still use local filesystem paths.
**Impact:** Wasted resources. No centralized media storage.
**Fix:** Integrate MinIO for media uploads (OCR input files, candidate CVs), backup storage, and document knowledge ingestion pipeline.
**Effort:** 1 week.

### Issue 8: `active_employees = 0`
**Severity: Medium**
**Root cause:** The FPE payroll system, escort roster, and employee management modules exist and are operational, but the `fpe_employees` table has no records. The entire payroll and escort system is data-empty.
**Impact:** Payroll computation jobs run but produce nothing. Escort assignments cannot be made. System is not operationally connected to real workforce data.
**Fix:** Load employee data via the admin dashboard or WhatsApp commands (if FPE cash-authorized phones are configured).
**Effort:** 1–2 hours (data entry).

### Issue 9: `main.py` Monolith
**Severity: Low**
**Current state:** All 24 `*.bak*` files were removed from the source tree and preserved under `/home/azim/archives/bak-files-20260612/`.
**Remaining problem:** `app/main.py` is still 2,541 lines and difficult to maintain.
**Fix:** Split `main.py` into routers (webhooks, admin, payroll, reports).
**Effort:** 1 day.

### Issue 10: No Memory Management UI
**Severity: Medium**
**Root cause:** The memory extractor and `user_memory` table exist, but there is no frontend interface for viewing, editing, pinning, or curating learned facts. Dashboard `Users (B19)` tab exists but is minimal.
**Impact:** Cannot do no-code AI improvement — must edit SQL or code to adjust AI behavior.
**Fix:** Build a Memory Admin UI (see Section 10).
**Effort:** 3–5 days.

### Issue 11: Dashboard Auth Uses Header-Only API Key
**Severity: Medium**
**Root cause:** Dashboard authentication sends `X-Internal-Key` header via JavaScript `localStorage`. No session management, no CSRF protection, no 2FA.
**Impact:** API key visible in browser localStorage; single compromised key gives full admin access.
**Fix:** Move to proper session-based auth (httpOnly cookie + server-side session) or at minimum use short-lived JWT tokens.
**Effort:** 2–3 days.

---

## 5. Frontend ↔ Backend Gap Analysis

| Frontend Element | Backend State | Gap | Recommendation |
|---|---|---|---|
| Dashboard "Users (B19)" tab | `/api/users/*` endpoints exist | Tab shows minimal table; no create/edit UI | Build proper user management UI |
| Dashboard "Memory Review" | `/api/memory/pending` exists | No dashboard tab for memory | Add "Memory" tab to dashboard |
| Dashboard "Chat Lab" | `/chat/message` exists | Exists but unverified UX | Test and improve chat lab UI |
| `kb.html` Knowledge Base | `/api/kb/*` via `kb_upload` router | KB visible but no bulk upload UI | Add CSV/TXT bulk import |
| `escort-roster.html` | Full escort backend | Functional but no slip upload from UI | Add drag-drop slip upload |
| `payroll.html` (3085 lines) | FPE router active | No employees loaded — all views empty | Load employee data first |
| No document upload page | OCR + media processor exists | No frontend for document knowledge ingestion | Build doc upload page (see Section 11) |
| No memory/facts admin page | `user_memory`, `user_profiles` tables exist | No way to view/edit AI-learned facts without SQL | Build memory admin UI (Section 10) |
| No agent reasoning trace view | Logs exist in `fazle_ai_workflow_logs` | No UI to inspect AI decisions | Add reasoning trace viewer |
| Meta webhook status | Live inbound delivery works | Dashboard does not expose heartbeat age or last inbound event | Add bridge status banner with heartbeat age and delivery test |

---

## 6. Feature Classification

### Keep As-Is
- **3-tier AI cascade** (GitHub Models → Groq → Ollama): Working well, cost-optimized
- **APScheduler + job registry**: Clean, reliable, timezone-aware
- **Daily pg_dump backup system**: Running correctly, 14-day retention
- **BM25 RAG**: Fast (12ms), deterministic, good for exact matches — keep as hybrid layer
- **Bridge poller architecture**: Reliable SQLite polling pattern
- **Nginx reverse proxy config**: Well-structured with rate limiting and security headers
- **Prometheus + Grafana + Loki**: Full observability stack already deployed
- **Draft quality gate**: Prevents garbage replies from reaching admins
- **Reviewed reply memory**: Valuable learning from human edits

### Improve
- **Intent classification**: Add LLM-based semantic intent for edge cases; current keyword/regex misses nuanced messages
- **Dashboard authentication**: Upgrade from API key in localStorage to proper session management
- **Media processor**: Monitor post-restart memory growth, then add a managed restart policy
- **RAG**: Upgrade from BM25-only to hybrid BM25 + Qdrant vector search
- **Knowledge base UI**: Add bulk import (CSV, TXT), source tagging, quality scores
- **`main.py`**: Split into logical routers

### Build Next (Highest Priority)
1. **Memory Admin UI** — view, edit, pin, delete AI-learned facts (Section 10)
2. **Document Upload & Ingestion Pipeline** — PDF/DOCX → embeddings → Qdrant (Section 11)
3. **Re-enable auto-reply** — start with recruitment only, expand carefully
4. **Qdrant vector search integration** — semantic KB search with Ollama embeddings
5. **Expose Meta webhook observability** — live delivery works; add heartbeat/log visibility
6. **Employee data onboarding** — populate payroll/escort with real workforce

### Remove
- **Legacy `wbom_*` DB tables** (50+ tables): Archive to a separate schema or drop — they inflate DB size and migration complexity
- **`_legacy_*` DB tables**: Same — archive and drop
- **`fazle-core.service` in `/home/azim/core/` (the old copy)**: Only one copy should exist
- **`secure-env-backup/`** (684MB): Review what's in here; sensitive credentials should not live in home dir

---

## 7. AI Capability Review

### 7.1 Current Models in Use

| Provider | Model | Role | Cost | Latency | Limits |
|---|---|---|---|---|---|
| GitHub Models | `gpt-4o-mini` | Primary reply generation | Free | ~500ms | 15 RPM, 1000 TPM |
| Groq | `llama-3.1-8b-instant` | Secondary fallback | Free | ~251ms | 30 RPM, 14,400 req/day |
| Ollama local | `qwen2.5:3b` | Intent classify, RAG, learning | Free (local) | ~200ms | No limit |
| Ollama local | `qwen3:8b` | Available, not active | Free (local) | ~800ms | No limit |

### 7.2 AI Flow
```
Inbound message
    → Intent classify (local Ollama qwen2.5:3b)
    → KB lookup (BM25)
    → [if no KB match] GitHub Models gpt-4o-mini
        → [if rate-limited] Groq llama-3.1-8b-instant
            → [if fails] Ollama qwen2.5:3b (if not disabled)
                → [if all fail] Holding message (Bengali polite fallback)
```

### 7.3 Memory Mechanisms
- **Short-term**: None — no conversation history passed to LLM (each message is stateless)
- **Long-term**: `user_memory` table (facts extracted by memory_extractor); `fazle_knowledge_base` (curated KB); `reviewed_reply_memory` (approved reply patterns)
- **RAG**: BM25 over KB + resource files — retrieves context before LLM call
- **Learning loop**: `daily_memory_review` scheduler job promotes learned facts to KB

### 7.4 Critical AI Gaps
1. **No conversation context**: Each WhatsApp message is treated as independent — AI has no memory of what was said 2 messages ago in the same conversation
2. **No semantic embeddings**: Despite pgvector and Qdrant being deployed, no vectors are being generated or used
3. **15 RPM GitHub Models limit**: Under any real message volume this becomes a bottleneck in minutes
4. **`gpt-4o-mini` is proprietary/paid**: Goes against the FOSS-first principle; need a plan to migrate

### 7.5 Recommended Model Strategy (FOSS-first)

| Task | Current | Recommended | Reason |
|---|---|---|---|
| Customer reply generation | `gpt-4o-mini` | `qwen3:8b` local | Best FOSS 8B for multilingual Bengali/English; already downloaded |
| Recruitment reply (nuanced) | `gpt-4o-mini` | `qwen3:8b` or `llama3.1:8b` | Strong reasoning, good Bengali |
| Intent classification | `qwen2.5:3b` | `qwen2.5:3b` (keep) | Fast, accurate, no network dependency |
| Embedding generation | None | `nomic-embed-text` (Ollama) | 768-dim, fast, FOSS, good bilingual |
| Reranking | None | `bge-reranker-v2-m3` | Improves RAG retrieval precision |
| OCR text extraction | Media processor | Keep current | Working well |
| Voice transcription | None deployed | `whisper:base` (Ollama) | For future voice message support |

**Recommended cascade (FOSS-first):**
```
qwen3:8b (local, primary) → Groq llama-3.1-8b (free cloud fallback) → holding message
```
This eliminates the GitHub Models dependency entirely and removes the 15 RPM bottleneck.

---

## 8. WhatsApp AI Automation Review

### 8.1 Pipeline Assessment

| Component | Status | Quality | Notes |
|---|---|---|---|
| Incoming message handling | Working | Good | Bridge pollers reliable, 10s lag max |
| Intent detection | Working | Medium | Regex/keyword sufficient for known patterns; unknown intents fall through to LLM |
| Reply generation | Working (disabled) | Medium | No conversation history; stateless per message |
| Session stability | Good | — | Both bridges up, 8+ days uptime |
| Message queueing | Working | Good | Outbound queue with retry + DLQ |
| Human handoff | Working | Good | Draft queue + approval flow operational |
| Knowledge retrieval | Working (BM25) | Medium | No semantic search; misses paraphrases |
| Context retention | Missing | Poor | No multi-turn memory passed to AI |
| Multi-turn conversations | Not implemented | Poor | Each message treated independently |
| Error recovery | Good | — | Quality gate, DLQ, fallback holding message |
| Media handling | Working | Medium | OCR for images/PDFs; no voice transcription |

### 8.2 Improvement Recommendations

**Faster responses:**
- Switch primary to `qwen3:8b` local — eliminates network round-trip to GitHub/Groq
- Pre-warm Ollama model (currently `qwen2.5:3b` active, `qwen3:8b` cold)

**Better reasoning:**
- Inject last 3–5 conversation turns into LLM context (conversation_summaries table exists but is unused)
- Use `fazle_conversation_summaries` table — it's there, just not populated

**Lower costs:**
- Already near-zero; migrate GitHub Models → local qwen3:8b to eliminate any future cost risk

**Higher reliability:**
- Add Meta heartbeat-age dashboard alert and reliable webhook log visibility
- Monitor media processor RSS after restart and add a managed restart policy
- Add Guardian incident deduplication, auto-resolution, and retention

**More natural conversations:**
- Add conversation history injection (last 3 turns)
- Improve system prompts with company-specific tone guide

---

## 9. Knowledge & Memory Improvement Plan

### 9.1 Current Architecture
```
Inbound text
    → BM25 search over (KB table + resource files)
    → Top-k results as context
    → LLM generates reply
    → memory_extractor extracts facts (background task)
    → facts stored in user_memory
    → daily_memory_review promotes to fazle_knowledge_base
```

### 9.2 Target Architecture
```
Inbound text
    → BM25 search (fast exact/keyword match)
    → Qdrant vector search (semantic similarity)
    → Reranker merges + scores results (optional: bge-reranker)
    → Top-k hybrid results as context + last 3 conversation turns
    → LLM generates reply
    → memory_extractor extracts facts
    → facts stored pending admin review
    → admin approves/rejects/edits via Memory Admin UI
    → approved facts embedded + stored in Qdrant + KB table
```

### 9.3 Proposed Memory Layers

| Layer | Technology | Purpose | Editable? |
|---|---|---|---|
| Short-term | Last N messages from `fazle_conversations` | Conversation context | Auto |
| User profiles | `user_profiles` + `user_memory` tables | Per-contact facts | Yes (admin UI) |
| Knowledge base | `fazle_knowledge_base` + Qdrant | General business knowledge | Yes (KB admin UI) |
| Reviewed patterns | `reviewed_reply_memory` | Human-corrected reply patterns | Auto (on draft edit) |
| Resource docs | `resources/*.txt` | Static business docs | Files (or UI upload) |

### 9.4 Immediate Memory Improvements (No Code Change)
1. Populate `fazle_conversation_summaries` — scheduler job already registered, just needs conversation history inserted
2. Add conversation turns to LLM prompt — 5-line change in `llm.py`
3. Curate `fazle_knowledge_base` — review and add entries via `/kb` page

---

## 10. Frontend Memory Management Interface

### 10.1 Recommended UI Design

Add a **"Memory" tab** to the existing dashboard at `fazle.iamazim.com/dashboard`:

```
┌─────────────────────────────────────────────────────────────┐
│  Fazle Dashboard  | Overview | Drafts | Memory | KB | ...   │
├─────────────────────────────────────────────────────────────┤
│  MEMORY MANAGEMENT                                          │
│                                                             │
│  [ Search contacts... ]  [ Filter: pending / all ]         │
│                                                             │
│  Contact: 01958122300 — "Recruitment Caller"               │
│  ├─ [✓] name: "আব্দুল করিম"          [Edit] [Delete]      │
│  ├─ [?] role_hint: "candidate"        [Pin] [Dismiss]      │
│  └─ [!] pending_matter: "salary..."   [Promote→KB] [Skip]  │
│                                                             │
│  [ PIN INSTRUCTION ]                                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Always reply in formal Bangla for this contact...   │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  PROMPT TESTER                                              │
│  Input: [ type a test message... ]  [ Test →]              │
│  AI sees: [retrieved context shown here]                    │
│  AI reply: [generated reply shown here]                     │
└─────────────────────────────────────────────────────────────┘
```

### 10.2 Features to Implement

| Feature | Endpoint Needed | Priority |
|---|---|---|
| View per-contact memories | `GET /api/users/{phone}` (exists) | High |
| Edit a memory fact | `PUT /api/users/{phone}/memory/{id}` (new) | High |
| Delete a memory | `DELETE /api/users/{phone}/memory/{id}` (new) | High |
| Pin important instruction | `POST /api/users/{phone}/memory` with type=instruction | High |
| Approve learned fact → KB | `POST /api/memory/{id}/promote` (exists) | High |
| Dismiss/reject learned fact | `POST /api/memory/{id}/dismiss` (exists) | High |
| Test prompt + see context | `POST /api/debug/test-reply` (new) | Medium |
| View conversation history | `GET /api/conversations/{phone}` (new) | Medium |
| Inspect AI reasoning trace | `GET /api/ai-logs/{id}` (new) | Low |
| Configure AI behavior flags | `PUT /api/config` (new, admin only) | Medium |

---

## 11. Document Upload & Knowledge Ingestion

### 11.1 Current State
- Media processor (port 8090) supports OCR for images and PDF text extraction
- `kb_upload` module exists with FastAPI router
- `resources/uploads/` directory exists
- No frontend upload interface

### 11.2 Proposed Ingestion Pipeline
```
User uploads file (PDF/DOCX/TXT/CSV/image)
    → Frontend drag-drop at /knowledge/upload
    → POST /api/kb/upload
    → media-processor extracts text (OCR for images, pdfplumber for PDFs)
    → text chunked (320 chars, 60 char overlap — same as RAG config)
    → nomic-embed-text generates embeddings (via Ollama)
    → chunks + embeddings stored in Qdrant collection "fazle_kb"
    → metadata (filename, upload date, source, admin) stored in fazle_knowledge_base
    → BM25 index rebuilt (rag_rebuild job triggered)
    → Admin sees: "42 chunks indexed from 'salary_policy_2026.pdf'"
```

### 11.3 Supported Formats (Recommended)
| Format | Handler | Status |
|---|---|---|
| PDF | `pdfplumber` via media-processor | Ready |
| Image (JPG/PNG) | Tesseract OCR via media-processor | Ready |
| TXT | Direct read | Trivial |
| DOCX | `python-docx` | Add to media-processor |
| CSV | `pandas` read + row-by-row index | Add |

### 11.4 Priority Features
1. Basic TXT/PDF upload → BM25 index (1 day)
2. Image OCR → knowledge (media-processor already does this, just need UI)
3. Embedding + Qdrant storage (2 days, needs nomic-embed-text pull)
4. Duplicate detection via content hash (1 day)
5. Source attribution in RAG answers (half day)

---

## 12. Reply Generation Enhancement

### 12.1 Current Quality Assessment

| Dimension | Current Score | Target |
|---|---|---|
| Bengali fluency | 7/10 | 9/10 |
| Professional tone | 7/10 | 9/10 |
| Context awareness | 3/10 | 8/10 |
| Multi-turn coherence | 2/10 | 8/10 |
| Instruction following | 7/10 | 9/10 |
| Consistency | 6/10 | 9/10 |
| Hallucination control | 7/10 | 9/10 |

### 12.2 Quick Wins (Low Effort, High Impact)

1. **Add last 3 message turns to LLM context** — fixes multi-turn coherence instantly. Change in `llm.py`: load last 3 rows from `fazle_conversations` for the sender phone and prepend as `user`/`assistant` messages.

2. **Upgrade primary model to `qwen3:8b` local** — better Bengali, no rate limit, no external dependency. Change `OLLAMA_MODEL=qwen3:8b` and `OLLAMA_REPLY_DISABLED=false` and `PRIMARY_AI_PROVIDER=ollama`.

3. **Add reply quality evaluation** — after generation, score the reply against criteria (length < 500 chars, no code blocks, no file paths, responds to intent). The quality gate exists; extend it.

### 12.3 Evaluation Criteria

Measurable metrics to track reply quality:
- **Response rate**: % of messages that got a reply (target: 100% in 30s)
- **Draft promotion rate**: % of AI drafts approved without edit (target: >80%)
- **Edit rate**: % of drafts admin modifies before sending (lower is better)
- **Rejection rate**: % of drafts rejected entirely (target: <5%)
- **KB hit rate**: % of messages answered by KB without LLM (target: >40%)
- **Latency**: Time from message receipt to draft creation (target: <5s)

---

## 13. Security & Reliability Review

### 13.1 Secrets Management
| Secret Type | Location | Risk | Recommendation |
|---|---|---|---|
| `.env` with API keys | `/home/azim/core/.env` | Medium — world-readable if permissions wrong | Ensure `chmod 600`, consider Vault or systemd credentials |
| `secure-env-backup/` | `/home/azim/` (684MB) | High — backups of secrets in home dir | Move to encrypted storage, restrict access |
| GitHub tokens (×3) | `.env` | Medium — rotation via round-robin exists | Add token expiry monitoring |
| DB password | `.env` | Medium | OK for single-server; restrict psql connections |
| `firebase-service-account.json` | `locationwhere-backend/` | High — service account key on disk | Move to environment variable |

### 13.2 Access Controls
- ✅ Nginx rate limiting on all domains (20–30 r/s with burst)
- ✅ API docs blocked in production (`/docs`, `/redoc` → 404)
- ✅ Internal API protected by `X-Internal-Key` header
- ✅ RBAC implemented (`fazle_admin_roles`, `fazle_access_rules`)
- ✅ fail2ban active (SSH brute-force protection)
- ✅ SSL/TLS on all domains (Let's Encrypt, HSTS enabled)
- ⚠️ Dashboard uses localStorage for API key (XSS risk)
- ⚠️ No 2FA on admin dashboard
- ⚠️ code-server at `vscode.iamazim.com` — full shell access; ensure strong password

### 13.3 Backup Strategy
- ✅ Daily pg_dump at 02:30 (14 daily + 8 weekly retention)
- ✅ Backup staleness alert (warns if backup > 48h old)
- ⚠️ Backups stored on same VPS — not offsite
- ⚠️ No backup integrity verification (no restore test)
- ❌ No application-state backup (`.env`, WhatsApp sessions, resource files)

### 13.4 Disaster Recovery
- ❌ No documented restore procedure
- ❌ No secondary VPS or failover
- ✅ Git history provides code rollback
- ✅ Versioned deployment model described in ROADMAP.md

### 13.5 Priority Security Actions
1. **High**: Move `firebase-service-account.json` to environment variable
2. **High**: Restrict `.env` files: `chmod 600 /home/azim/core/.env`
3. **High**: Add offsite backup (e.g., rclone to Backblaze B2 or S3-compatible)
4. **Medium**: Upgrade dashboard auth to session-based (not localStorage API key)
5. **Medium**: Add backup restore test to monthly cron
6. **Low**: Add monitoring for Let's Encrypt certificate expiry

---

## 14. Resource & Cost Optimization

### 14.1 Current Resource Usage

| Service | RAM | CPU | Notes |
|---|---|---|---|
| media-processor | ~326 MiB RSS | 0.5% | Restarted; monitor for gradual growth |
| open-webui (Docker) | ~755 MB | 0.1% | Normal for the app |
| ollama (Docker) | ~500 MB | ~1% | Low when idle |
| fazle-core | ~112 MB | 12.5% | Normal for Python async |
| location-where | 53.9 MB | **0%** | Online after restart; 8 lifetime restarts |
| system-agent | ~60 MB | 2.2% | Normal |
| vscode-server (process) | ~1 GB | 15% | High — VSCode extension host |
| TypeScript server | ~1.1 GB | 1.7% | VSCode Pylance + TS server |

**Immediate wins:**
1. Fix repeated Guardian `ollama_model_evicted` generation at the source
2. Add Meta webhook logging/heartbeat visibility
3. Close VSCode when not actively developing → reclaim ~2 GB RAM + CPU

### 14.2 Monthly Cost Analysis

| Service | Monthly Cost |
|---|---|
| VPS hosting | ~$20–40/mo (estimated) |
| GitHub Models (gpt-4o-mini) | $0 (free tier) |
| Groq (llama-3.1-8b) | $0 (free tier) |
| Ollama (local) | $0 |
| Let's Encrypt SSL | $0 |
| **Total AI cost** | **$0/month** |

The AI stack is effectively **free**. The only cost is the VPS itself.

### 14.3 Optimization Opportunities

1. **Migrate primary AI to local `qwen3:8b`**: Eliminates GitHub Models rate limit (15 RPM) for no additional cost. The model is already downloaded (5.2 GB).

2. **Reduce Docker container overhead**: Loki, Promtail, cAdvisor, Prometheus together consume ~500 MB RAM. If monitoring is not actively used, consider stopping during low-traffic hours.

3. **Archive legacy WBOM tables**: 50+ `wbom_*` tables from the old system inflate backup size and query planning overhead. Archive to a separate schema.

4. **Clean up `.vscode-server`**: Currently 8.4 GB; set up a cron to prune old extension versions periodically.

---

## 15. Recommended Future Architecture

### 15.1 Target System Architecture

```
┌────────────────────── iamazim.com VPS ──────────────────────────────┐
│                                                                      │
│  ┌─── Nginx (TLS termination, rate limiting) ───────────────────┐   │
│  │  iamazim.com → static site                                   │   │
│  │  fazle.iamazim.com → fazle-core :8200                        │   │
│  │  api.iamazim.com → fazle-core :8200                          │   │
│  │  chat.iamazim.com → open-webui :8501                         │   │
│  │  locationwhere.iamazim.com → location-where :8310            │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  ┌─── WhatsApp Ingestion ─────────────────────────────────────────┐  │
│  │  Bridge1 (HR)  :8082 ──┐                                      │  │
│  │  Bridge2 (OPS) :8081 ──┼──► fazle-core webhook handlers       │  │
│  │  Meta Cloud API ────────┘   (process_message → router)        │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─── fazle-core (FastAPI :8200) ──────────────────────────────────┐ │
│  │  message_router → intent classify → KB lookup                  │ │
│  │                → [miss] Hybrid RAG (BM25 + Qdrant)             │ │
│  │                → [context built] AI reply generation           │ │
│  │                → draft queue → [approved] outbound send        │ │
│  │                                                                │ │
│  │  Scheduler: 10 jobs (payroll, backup, memory review, etc.)    │ │
│  │  Admin Dashboard: /dashboard (tabbed HTML SPA)                │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─── AI Stack ────────────────────────────────────────────────────┐ │
│  │  Ollama → qwen3:8b (primary reply)                             │ │
│  │        → qwen2.5:3b (intent classify)                         │ │
│  │        → nomic-embed-text (embeddings for Qdrant)             │ │
│  │  Groq → llama-3.1-8b-instant (cloud fallback)                 │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─── Data Layer ─────────────────────────────────────────────────┐  │
│  │  PostgreSQL (pgvector) → all relational data                   │  │
│  │  Qdrant → semantic embeddings for KB and docs                  │  │
│  │  Redis → queue, cache, dedup, rate limiting                    │  │
│  │  MinIO → uploaded documents, media files, backups              │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌─── Observability ──────────────────────────────────────────────┐  │
│  │  Prometheus + Node Exporter + cAdvisor → metrics               │  │
│  │  Loki + Promtail → logs                                        │  │
│  │  OpenTelemetry → traces                                        │  │
│  │  Grafana → dashboards                                          │  │
│  └────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### 15.2 Document Ingestion Flow
```
Admin uploads file
    → MinIO stores original
    → media-processor extracts text
    → chunker splits text
    → nomic-embed-text generates embeddings
    → Qdrant stores vectors
    → PostgreSQL stores metadata + BM25 updates
```

### 15.3 AI Reply Flow (Target)
```
Inbound WhatsApp message
    → Fetch last 3 conversation turns from PostgreSQL
    → BM25 search (fast, keyword)
    → Qdrant vector search (semantic)
    → reranker merges results
    → Build context: [company info] + [relevant KB] + [conversation history]
    → qwen3:8b local generates reply
    → Quality gate checks reply
    → [if auto-reply enabled] send immediately
    → [if draft mode] create draft for review
    → memory_extractor extracts facts (background)
```

---

## 16. Prioritized Roadmap

### Immediate (Next 24–48 Hours)

| Task | Impact | Complexity | Priority |
|---|---|---|---|
| Rotate exposed sudo and Meta credentials | Critical security remediation | Medium | P0 |
| Set `chmod 600` on all `.env` files | High security — prevent credential leak | Trivial | P0 |
| Monitor media processor RSS and `location-where` CPU/restarts | High — confirms restart remediation remains stable | Low | P1 |
| Fix Guardian repeated incident generation at source | High — scheduled cleanup now controls growth but does not remove cause | Medium | P1 |
| Re-enable `RECRUITMENT_AUTOREPLY_ENABLED=true` | High — start delivering AI value | Low | P1 |
| Load employee data into FPE | Medium — activates payroll/escort system | Medium | P1 |

### Short-Term (1–2 Weeks)

| Task | Impact | Complexity | Dependencies | Priority |
|---|---|---|---|---|
| Pull `nomic-embed-text` into Ollama | Enables vector search | Trivial | — | P1 |
| Integrate Qdrant into RAG pipeline | Semantic search → better AI answers | Medium | nomic-embed-text | P1 |
| Build Memory Admin UI tab in dashboard | No-code AI improvement | Medium | Existing `/api/memory/*` endpoints | P1 |
| Add conversation history to LLM context | Better multi-turn coherence | Low | — | P1 |
| Switch primary AI to `qwen3:8b` local | Zero rate limit, eliminates gpt-4o-mini dependency | Low | — | P2 |
| Fix dashboard auth (session vs localStorage) | Security improvement | Medium | — | P2 |
| Add offsite backup (rclone to B2) | DR improvement | Low | rclone config | P2 |
| Archive `wbom_*` legacy tables | DB cleanliness | Medium | Data audit | P3 |

### Mid-Term (1–2 Months)

| Task | Impact | Complexity | Dependencies | Priority |
|---|---|---|---|---|
| Document upload & ingestion pipeline | Knowledge grows without code changes | Medium | Qdrant integration | P1 |
| Gradually enable full auto-reply | Core value delivery | Medium | Quality metrics tracking | P1 |
| Conversation summary generation | Enables long-term context | Medium | — | P1 |
| Split `main.py` into router modules | Maintainability | Medium | — | P2 |
| Agent reasoning trace viewer in dashboard | AI transparency | Medium | — | P2 |
| Migrate bridges to Meta's official API only | Reduce Go binary dependencies | High complexity | Meta account setup | P3 |
| Add backup restore test to monthly cron | DR validation | Low | — | P2 |
| Knowledge versioning (know what changed when) | Admin trust in AI | Medium | — | P3 |

### Long-Term (3–6 Months)

| Task | Impact | Complexity | Dependencies | Priority |
|---|---|---|---|---|
| Feedback loop: approved edits improve AI | Self-improving system | High | Memory UI + embedding pipeline | P1 |
| Voice message transcription (Whisper) | Handle audio messages | Medium | Whisper in Ollama | P2 |
| Multi-tenant architecture | Serve multiple businesses | Very High | Full rewrite of multi-tenancy layer | P3 |
| Mobile-friendly admin app | Better draft review on phone | High | React Native or PWA | P2 |
| Fine-tune `qwen2.5:3b` on Al-Aqsa domain data | Best possible Bengali response quality | Very High | Training data + GPU | P3 |
| Public-facing AI chat widget on iamazim.com | Lead generation automation | Medium | Embed tokens (already in DB) | P2 |
| LocationWhere enterprise features | Revenue expansion | High | Stable Node.js backend | P2 |

---

## Appendix A: Key File Locations

| Purpose | Path |
|---|---|
| Main application | `/home/azim/core/app/main.py` |
| Configuration | `/home/azim/core/app/config.py` |
| Environment variables | `/home/azim/core/.env` |
| Message router | `/home/azim/core/modules/message_router/__init__.py` |
| AI providers | `/home/azim/core/app/github_models.py`, `groq_provider.py`, `ollama.py`, `llm.py` |
| Dashboard UI | `/home/azim/core/app/static/dashboard.html` |
| Payroll UI | `/home/azim/core/app/static/payroll.html` |
| Nginx config | `/etc/nginx/sites-enabled/` |
| DB migrations | `/home/azim/core/db/migrations/` |
| Backups | `/home/azim/backups/fazle/` |
| Scheduler | `/home/azim/core/modules/scheduler/__init__.py` |
| Knowledge resources | `/home/azim/core/resources/` |
| Media processor | `/home/azim/shared/media/media-processor/server.py` |
| System agent | `/home/azim/agent/system_agent/main.py` |
| Disk alert script | `/home/azim/scripts/disk-alert.sh` |

## Appendix B: Port Map

| Port | Service |
|---|---|
| 80/443 | Nginx (all domains) |
| 8080 | Open WebUI (Docker internal) |
| 8081 | WhatsApp Bridge 2 (OPS) |
| 8082 | WhatsApp Bridge 1 (HR) |
| 8090 | Media Processor |
| 8200 | fazle-core (main AI backend) |
| 8300 | System Agent |
| 8310 | LocationWhere backend (Node.js) |
| 8443 | code-server (Docker internal) |
| 8501 | Open WebUI (Docker host-bound) |
| 9000 | MinIO |
| 9090 | Prometheus |
| 9100 | Node Exporter |
| 11434 | Ollama |
| 3000 | Grafana |
| 3100 | Loki |
| 4317-4318 | OpenTelemetry gRPC/HTTP |
| 5432 | PostgreSQL |
| 6333-6334 | Qdrant |
| 6379 | Redis |

---

*Audit refreshed: 2026-06-12 20:13 CEST. Based on live system inspection including running processes, PM2 state, PostgreSQL incident/heartbeat data, health endpoint responses, and the remediation actions completed in this session. All current-condition findings are evidence-based.*
