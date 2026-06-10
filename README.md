# Fazle Core — Single Source of Truth

**Last Updated:** 2026-06-10
**Status:** Production Healthy — v1.2.0 — Continuous Learning Loop + User Context Engine deployed (2026-06-10)

This README is the single authoritative reference for the Fazle Core production stack.
All other documentation, archived notes, and prior READMEs must be considered stale.

---

## Current Production Architecture

### Main Application

| Item | Value |
|---|---|
| App path | `/home/azim/core` |
| Framework | FastAPI |
| Entry point | `run.py → app.main:app` |
| Systemd unit | `fazle-core.service` |
| Local port | `8200` |
| Health endpoint | `http://localhost:8200/health` |
| Environment file | `/home/azim/core/.env` (single source for all runtime flags) |

> The old path `/home/azim/fazle-core` may still appear in archived files. The active service uses `/home/azim/core`.

### WhatsApp Bridges

Bridges run as **systemd services** (not Docker containers).

| Bridge | Label | Number | Systemd Unit | Port | SQLite Store |
|---|---|---|---|---|---|
| bridge1 | HR | `8801958122300` | `whatsapp-bridge.service` | `8082` | `/home/azim/whatsapp1/store/messages.db` |
| bridge2 | OPS | `8801880446111` | `whatsapp-bridge2.service` | `8081` | `/home/azim/whatsapp2/store/messages.db` |
| meta | Meta Cloud API | — | webhook at `/webhook/meta` | — | — |

**Bridge binary location:** `/home/azim/whatsapp-mcp/whatsapp-bridge/whatsapp-bridge`
**bridge1 working directory:** `/home/azim/whatsapp-mcp/whatsapp-bridge`
**bridge2 working directory:** `/home/azim/whatsapp2`

Active auto-reply sources: `bridge1,bridge2` (set via `AUTO_REPLY_SOURCES` in `.env`).
Meta ingestion is present but not in `AUTO_REPLY_SOURCES` — Meta messages are received but do not trigger auto-reply.

### AI Layer — Dual Provider (GitHub Models + Ollama)

**Primary:** GitHub Models (cloud, gpt-4o-mini) — **Active**
**Fallback:** Ollama (local, qwen2.5:3b) — automatic if GitHub Models fails

#### GitHub Models (Primary)

| Item | Value |
|---|---|
| Provider | GitHub Models OpenAI-compatible API |
| Endpoint | `https://models.github.ai/inference` |
| Model | `openai/gpt-4o-mini` (set via `GITHUB_MODEL_NAME` in `.env`) |
| Token rotation | `GITHUB_TOKEN`, `GITHUB_TOKEN_2`, `GITHUB_TOKEN_3` (round-robin) |
| Rate limit | 15 RPM / 1000 TPM (free tier) — semaphore-serialized |
| Client module | `app/github_models.py` |
| Learning memory | `llm_learning_memory` DB table (every reply stored) |

**To switch primary AI provider to Ollama**, update `.env`:
```env
PRIMARY_AI_PROVIDER=ollama
```

#### Ollama (Fallback / Secondary)

| Item | Value |
|---|---|
| Container name | `ollama` |
| Docker network | `ai-network` |
| Static IP | `172.22.0.7` |
| Port (internal) | `11434` |
| Compose file | `/home/azim/ai-call-platform/ai-infra/docker-compose.yaml` |
| Active model | `qwen2.5:3b` (set via `OLLAMA_MODEL` in `.env`) |
| Available models | `qwen2.5:3b` (1.9 GB), `qwen3:8b` |
| `OLLAMA_URL` in `.env` | `http://172.22.0.7:11434` |

> The host shell does not have `ollama` in PATH. Use `docker exec ollama ollama list` to inspect models.
> Host port binding (127.0.0.1:11434) is not functional; fazle-core reaches Ollama via Docker network IP directly.

**To pull a new model** (ai-network is isolated, needs temporary internet bridge):
```bash
docker network connect al-aqsa_openwebui-bridge ollama
curl -N -X POST http://172.22.0.7:11434/api/pull -H "Content-Type: application/json" -d '{"model":"<name>","stream":true}'
docker network disconnect al-aqsa_openwebui-bridge ollama
```

---

## Ports & URLs — Complete Reference

| Component | Address | Type | Notes |
|---|---|---|---|
| fazle-core | `http://localhost:8200` | Main API | Health at `/health` |
| bridge1 (HR outbound) | `http://localhost:8082` | Bridge send | `BRIDGE1_URL` in `.env` |
| bridge2 (OPS outbound) | `http://localhost:8081` | Bridge send | `BRIDGE2_URL` in `.env` |
| Ollama | `http://172.22.0.7:11434` | AI model | `OLLAMA_URL` in `.env` |
| Open WebUI | `172.22.0.2:8080` → `chat.iamazim.com` | Private AI chat | Single user (owner only) |
| System Agent | `http://localhost:8300` (if running) | Internal | `fazle-agent.service` |
| Media Processor | `http://localhost:8090` | STT/OCR/PDF | `media-processor.service` |
| VS Code Server | `http://localhost:8443` → `vscode.iamazim.com` | Dev IDE | `code-server` container |

## Active Public Hostnames (Nginx)

| Hostname | Routing | Status |
|---|---|---|
| `fazle.iamazim.com` | → `127.0.0.1:8200` | Active |
| `api.iamazim.com` | → `127.0.0.1:8200` (via upstream conf) | Active |
| `iamazim.com` | → static `/var/www/iamazim.com/` (Al-Aqsa company website) | Active ✅ Fixed 2026-06-04 |
| `www.iamazim.com` | Redirect → `iamazim.com` | Active alias |
| `chat.iamazim.com` | → `172.22.0.2:8080` (Open WebUI) | Active |
| `vscode.iamazim.com` | → `127.0.0.1:8443` | Active |

`livekit.iamazim.com` exists in `sites-available` but is **not enabled** — not counted as active.

> **Pending cleanup:** 10 stale `.bak` files in `/etc/nginx/sites-available/` require manual `sudo rm` from a terminal session (not blocking, not symlinked, not in `sites-enabled`).

---

## Active Modules Inside fazle-core

| Module | Mount / Path | Purpose |
|---|---|---|
| `bridge_poller` | `modules/bridge_poller/` | Polls SQLite stores for inbound messages |
| `social_auto_reply` | `modules/social_auto_reply/` | Legacy reply pipeline (`SOCIAL_AUTO_REPLY_SINGLE_ENGINE=false`) |
| `message_router` | internal | Routes by intent and sender identity |
| `outbound` | `modules/outbound/` | Queue-based send with DLQ (`OUTBOUND_ENABLED=false`) |
| `recruitment_ai` | `modules/recruitment_ai/` | qwen-backed recruitment replies |
| `knowledge_base` | `modules/knowledge_base/` | Hardcoded + DB-backed knowledge |
| `payroll` | `/payroll` | Fazle Payroll Engine |
| `escort_roster` | `/escort-roster` | Escort roster management |
| `contact_sync` | internal | Employee/contact identity management |
| `fazle_agent` | `fazle-agent.service` | System agent (dry-run mode) |
| `phone_normalizer` | `modules/phone_normalizer/` | BD phone → 8801XXXXXXXXX canonical form *(v1.2.0)* |
| `role_classifier` | `modules/role_classifier/` | Per-contact role context + memory loader *(v1.2.0)* |
| `memory_extractor` | `modules/memory_extractor/` | GitHub Models structured fact extraction → `user_memory` *(v1.2.0)* |

---

## Auto-Reply Pipeline

Inbound message flow:

1. Inbound message arrives from bridge SQLite or Meta webhook
2. Phone normalisation → `8801XXXXXXXXX` canonical form
3. Identity detection (admin / employee / candidate / unknown)
4. Intent classification
5. Business routing (recruitment, payroll, escort, office, general)
6. Knowledge base lookup → reviewed reply memory → Ollama generation → fallback
7. Draft gate (finance, advance, release-slip → always draft)
8. Send via bridge client or save as draft
9. **Background (async):** `memory_extractor` sends conversation to GitHub Models → structured facts saved to `user_memory` *(v1.2.0)*

### Unified LLM Interface (`app/llm.py`)

All AI reply calls go through `app/llm.py` which:
1. Tries GitHub Models first (if `PRIMARY_AI_PROVIDER=github_models`)
2. Falls back to Ollama automatically on any error
3. Stores every reply in `llm_learning_memory` table for Ollama training

`message_router`, `recruitment_ai`, and `main.py` import this as `from app import llm as ai`.

Exposed functions (v1.2.0): `generate_reply`, `generate_recruitment_reply`, `generate_chat_reply`, `generate_rag_answer`, `classify_intent_llm`, `check_ollama_health`, `check_health`.

### Recruitment Reply Brain

- Uses GitHub Models gpt-4o-mini (primary) → Ollama qwen2.5:3b (fallback)
- Approved facts from `fazle_knowledge_base` rows in `recruitment` and `vessel_duty`
- Conversation memory from read-only `wbom_whatsapp_messages` history
- Module: `modules/recruitment_ai`
- Answers only from approved knowledge — no hallucinated facts
- Still passes outbound safety gate before send

### Safety Protections (Always Enforced)

- Advance money requests → always draft/admin review
- Financial complaints (salary not received, payment dispute) → always draft/admin review
- Release slip / escort slip calculations → draft-only, require admin approval
- Contacts in `DRAFT_ALWAYS_ROLES`, `DRAFT_ALWAYS_NAMES`, `DRAFT_ALWAYS_PHONES` → always draft
- Contacts with `admin_review_only` risk level → all AI replies drafted

---

## Knowledge Base Status

**Migration 014 applied: 2026-05-31**

| Tier | Category | Count |
|---|---|---|
| DB (`fazle_knowledge_base`) | recruitment | 66 rows |
| DB (`fazle_knowledge_base`) | vessel_duty | 3 rows |
| DB (`fazle_knowledge_base`) | client | 2 rows |
| DB (`fazle_knowledge_base`) | business | 6 rows |
| DB (`fazle_knowledge_base`) | conversation | 80 rows |
| DB (`fazle_knowledge_base`) | personal | 11 rows |
| RAG file | `resources/al_aqsa_career_hr_intents.txt` | 64 intent blocks |
| Hardcoded fallback | `knowledge_base/__init__.py` | ~25 entries |

Topics covered: all 9 job positions (duties, salary, career path), vessel/escort duty, salary packages, payment methods (bKash/Nagad/bank), training, probation, leave, discipline, resignation, provident fund, insurance, conveyance, salary advance policy, client services, marketing contact.

---

## Current Critical Configuration Flags

These are the verified live values in `/home/azim/core/.env` as of 2026-06-10:

```
AUTO_REPLY_ENABLED=true
AUTO_REPLY_SOURCES=bridge1,bridge2
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_URL=http://172.22.0.7:11434
BRIDGE1_URL=http://localhost:8082
BRIDGE2_URL=http://localhost:8081
RECRUITMENT_AUTOREPLY_ENABLED=true
SOCIAL_AUTO_REPLY_SINGLE_ENGINE=false
OUTBOUND_ENABLED=false
USE_OUTBOUND_QUEUE=true
DRAFT_QUALITY_GATE=true
AI_SAFE_MODE=false
REVIEWED_REPLY_MEMORY_ENABLED=true
SCHEDULER_ENABLED=true
GAP_SCAN_ENABLED=true
PRIMARY_AI_PROVIDER=github_models
GITHUB_MODEL_NAME=openai/gpt-4o-mini
DRAFT_CREATION_ENABLED=true
```

**New in v1.1.0 (2026-06-10):**
- `PRIMARY_AI_PROVIDER=github_models` — GitHub Models gpt-4o-mini is now the primary AI
- `DRAFT_CREATION_ENABLED=true` — Drafts are created for admin review
- `GITHUB_TOKEN` / `GITHUB_TOKEN_2` / `GITHUB_TOKEN_3` — round-robin token rotation

**New in v1.2.0 (2026-06-10) — optional, have defaults:**
- `MEMORY_REVIEW_HOUR=9` — hour (Asia/Dhaka) for daily memory review job
- `RAG_REBUILD_HOUR=18` — hour (Asia/Dhaka) for evening BM25 index rebuild

---

## Docker Infrastructure

All AI infrastructure containers run from `/home/azim/ai-call-platform/ai-infra/docker-compose.yaml`.

| Container | Status | IP / Port |
|---|---|---|
| `ollama` | healthy | `172.22.0.7:11434` |
| `ai-postgres` | healthy (5 days) | `db-network` |
| `ai-redis` | healthy (5 days) | `db-network` |
| `qdrant` | healthy (5 days) | internal |
| `minio` | healthy (5 days) | internal |
| `open-webui` | healthy | `172.22.0.2:8080` |
| `grafana` | healthy (5 days) | internal |
| `prometheus` | healthy (5 days) | internal |
| `loki` | healthy (5 days) | internal |
| `code-server` | healthy (5 days) | `127.0.0.1:8443` |
| `fazle-otel-collector` | running (5 days) | internal |

**VPS Resources (verified 2026-05-31):**
- RAM: 23 GiB total, ~16 GiB available
- Disk: 194 GB total, ~110 GB free (44% used)
- Docker images: 21.89 GB total
- Docker volumes: 16.24 GB total

---

## Bridge Store Paths — Canonical Reference

The `bridge_poller` reads from these hardcoded paths (`modules/bridge_poller/__init__.py:53-60`):

| Bridge | Canonical store path |
|---|---|
| bridge1 (HR) | `/home/azim/whatsapp1/store/messages.db` |
| bridge2 (OPS) | `/home/azim/whatsapp2/store/messages.db` |

**Stale/legacy directories** (do not use as primary path):
- `/home/azim/bridges/bridge1/store/` — stale, last updated 2026-05-27. Remove after 2026-06-07.
- `/home/azim/bridges/bridge2/store/messages.db` — hardlink to `/home/azim/whatsapp2/store/messages.db` (same inode). Not stale but redundant.
- `/home/azim/bridges/bridge3/store/` — for future bridge3 if activated.

---

## Supporting Services (Intentionally Inactive — Decision 2026-05-31)

### fazle-recruitment-bridge3

- **Unit:** `/etc/systemd/system/fazle-recruitment-bridge3.service`
- **Code:** `/home/azim/external_recruitment_agent/bridge3_agent.py`
- **Purpose:** Isolated recruitment companion for the +8801958122322 WhatsApp bridge (Bridge Three). Reads Bridge Three SQLite, mirrors rows into Fazle DB, replies only on confident recruitment intent.
- **Status: INACTIVE — intentional**
- **Reason:** Requires `whatsapp-bridge3.service` and a configured `.env`. `fazle-core` `recruitment_ai` handles all current recruitment replies. Do not activate without configuring bridge3 and validating in dry-run mode first.

### fazle-recruitment-agent

- **Unit:** `/etc/systemd/system/fazle-recruitment-agent.service`
- **Code:** `/home/azim/external_recruitment_agent/agent.py`
- **Status: INACTIVE — intentional**
- **Reason:** `fazle-core` `recruitment_ai` is sufficient. Activating risks double-replies to candidates. Requires `.env` with `DATABASE_URL`, `INTERNAL_API_KEY`, `ADMIN_PHONE`. Set `RECRUITMENT_AGENT_SEND_ENABLED=false` for dry-run before enabling live send.

### fazle-recruitment-bot-v3

- **Unit:** `/etc/systemd/system/fazle-recruitment-bot-v3.service`
- **Code:** `/home/azim/external_recruitment_bot_v3/bot.py`
- **Status: INACTIVE — intentional**
- **Reason:** Superseded by `fazle-core` `recruitment_ai` module. Same double-reply risk as above.

---

## Fazle Diagnostic Agent

- **Location:** `/home/azim/fazle-diagnostic-agent`
- **Purpose:** Read-only diagnostics for VPS, apps, code, routes, DB inspection
- **Constraint:** Must remain separate. Must not modify the main app, databases, bridges, or other services.

```bash
cd /home/azim/fazle-diagnostic-agent
./venv/bin/python agent.py --full --target core
./venv/bin/python agent.py --area code --target core --json
./venv/bin/python agent.py --full --target vps
```

---

## Future Upgrade Plan (Prioritised)

### High Priority — within 7 days

- [ ] Remove 10 stale nginx `.bak` files from `/etc/nginx/sites-available/` (requires `sudo rm` from terminal).
- [ ] Monitor `llm_fallback_total` counter for 48 hours after Ollama fix. If elevated, investigate model load time.
- [x] ~~Remove stale `/home/azim/bridges/bridge1/store/` after 2026-06-07 confirmation.~~ — done 2026-06-09
- [ ] Dashboard: add User Management tab, Memory Review tab (Phase 4 frontend — HTML/JS).

### Medium Priority — within 30 days

- [ ] Re-evaluate `DRAFT_CREATION_ENABLED`. Consider enabling with Grafana/Prometheus alert on draft queue growth.
- [ ] Load test `qwen3:14b` under simulated message burst. Create a rollback procedure before switching `OLLAMA_MODEL`.
- [ ] Add Grafana alert for: bridge polling lag > 5 minutes, Ollama (unhealthy) state, outbound DLQ > 0.
- [ ] Fix host port binding for Ollama (`127.0.0.1:11434`) — currently null; container only reachable via docker network IP.
- [ ] Add unique constraint on `user_memory (phone_canonical, memory_type, content)` to prevent duplicate fact accumulation from daily memory review re-runs.
- [ ] Integrate `get_user_context` + `build_context_for_llm` into `modules/message_router` for role-aware WhatsApp replies.

### Long-term Upgrades

- [ ] Deploy React frontend from `/home/azim/frontend/` via nginx (built but not publicly exposed).
- [ ] Implement a message recovery queue for silent-loss cases when `DRAFT_CREATION_ENABLED=false` and send fails.
- [ ] Consider moving bridge processes to Docker containers with correct volume mounts for operational consistency.
- [ ] Evaluate LiveKit voice stack reactivation only when RAM usage is consistently under 12 GiB under normal load.

---

## Safety Rules

- Do not store secrets in this README.
- Do not expose `.env` secret values — only flag names are safe here.
- `/home/azim/core/.env` is the single production env source for `fazle-core`. Do not use any other file.
- Use read-only DB credentials for all audits and diagnostics.
- Keep advance requests, financial complaints, and release-slip calculations in draft/admin-review flow — never auto-send.
- Do not switch to `qwen3:14b` without load testing and a documented rollback procedure.
- Do not start external recruitment agents without dry-run validation and explicit owner approval.

---

## Quick Verification Commands

```bash
# Core services
sudo systemctl status fazle-core whatsapp-bridge whatsapp-bridge2 fazle-agent --no-pager

# Docker AI layer
docker ps --filter name=ollama --format "{{.Names}} {{.Status}}"
docker ps --format "{{.Names}} {{.Status}}" | grep -E "postgres|redis|webui"

# Health endpoint (full JSON)
curl -s http://localhost:8200/health | python3 -m json.tool

# Ollama models
curl -s http://172.22.0.7:11434/api/tags | python3 -c 'import sys,json; print([m["name"] for m in json.load(sys.stdin)["models"]])'

# Bridge store freshness
ls -lh /home/azim/whatsapp1/store/messages.db /home/azim/whatsapp2/store/messages.db

# Key config flags
grep -E "AUTO_REPLY|OLLAMA|BRIDGE|DRAFT|OUTBOUND" /home/azim/core/.env

# Nginx
sudo nginx -t
```

---

## Database Tables — v1.2.0 Additions

| Table | Migration | Purpose |
|---|---|---|
| `user_profiles` | 016 | Per-contact profile: canonical phone, name, role, relationship_type, notes |
| `user_memory` | 016 | Structured facts extracted per contact (work_info, personal_info, pending_matter…) |
| `role_reply_styles` | 016 | Per-role tone/length/template config (for future use) |
| `phone_normalization_log` | 016 | Audit log of raw→canonical phone conversions |
| `llm_learning_memory.promoted_to_kb` | 017 | Boolean — marks reviewed replies promoted to KB |
| `llm_learning_memory.dismissed` | 017 | Boolean — hides dismissed replies from Memory Review queue |

Apply with:
```bash
export DB="postgresql://postgres:<pass>@172.20.0.3:5432/postgres"
docker exec -i ai-postgres psql "$DB" < db/migrations/016_user_profiles_memory.sql
docker exec -i ai-postgres psql "$DB" < db/migrations/017_memory_promotion.sql
```

---

## Scheduled Jobs — Complete Reference

| Job | Schedule | Purpose |
|---|---|---|
| `daily_payroll_compute` | 02:00 | Auto-compute payroll for current month |
| `daily_admin_digest` | 08:00 | Send daily summary to admin |
| `daily_memory_review` | 09:00 | GitHub Models extracts facts from last 24h conversations → `user_memory` |
| `stale_escort_reminder` | 09:00 | Alert admin for escort programs Active >30 days |
| `rag_rebuild` | 18:00 | Rebuild BM25 RAG index (picks up new KB entries from the day) |
| `backup_staleness_alert` | 03:00 | Alert if newest backup is older than 48h |
| `dlq_alert` | every 15 min | Alert if outbound DLQ > 0 |
| `payment_reconciliation` | every 1h | Re-match unresolved staging payments |
| `combined_draft_cleanup` | every 1h | Expire stale escort + payment drafts (>24h) |

Override hours via `.env`: `MEMORY_REVIEW_HOUR=9`, `RAG_REBUILD_HOUR=18`.

---

## New API Endpoints — v1.2.0

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/users/search?q=` | Search user_profiles by phone or name |
| GET | `/api/users/{phone}` | Full profile + memories for a contact |
| PUT | `/api/users/{phone}` | Upsert contact profile (role, name, notes) |
| POST | `/api/users/{phone}/memory` | Manually add a fact to user_memory |
| GET | `/api/memory/pending` | LLM replies awaiting KB promotion review |
| POST | `/api/memory/{id}/promote` | Promote an LLM reply to fazle_knowledge_base |
| POST | `/api/memory/{id}/dismiss` | Dismiss a reply from the review queue |
| GET | `/api/stats/llm` | GitHub Models + Ollama usage stats |

All require `X-Internal-Key` header.

---

## CHANGES — 2026-06-10 (v1.2.0)

### v1.2.0 — Continuous Learning Loop + User Context Engine

**Bug fixed (critical — carried from v1.1.0):**
- `app/llm.py` — `check_ollama_health()` and `generate_rag_answer()` were missing after `main.py` switched to `from app import llm as ai`. Both functions now present as pass-throughs. Without this fix the `/health`, `/chat/models`, and `/rag/answer` endpoints would throw `AttributeError`.

**New modules:**
- `modules/phone_normalizer/` — Bangladeshi phone number normalisation (`+880…`, `0…`, `1…` → `8801XXXXXXXXX`)
- `modules/role_classifier/` — Per-contact role context loader from `user_profiles` + `user_memory`
- `modules/memory_extractor/` — GitHub Models structured JSON extraction from conversations → `user_memory`

**Updated files:**
- `app/github_models.py` — added `generate_structured_response()` (raw text, for JSON extraction) and `generate_chat_reply()` (Chat Lab)
- `app/llm.py` — added `generate_chat_reply()` router, `check_ollama_health()`, `generate_rag_answer()` pass-throughs
- `app/main.py` — background memory extraction in bridge event handler; Phase 5 User Management + Memory Review + LLM Stats APIs
- `modules/scheduler/__init__.py` — `daily_memory_review` (09:00) and `rag_rebuild` (18:00) jobs registered

**New migrations:**
- `016_user_profiles_memory.sql` — `user_profiles`, `user_memory`, `role_reply_styles`, `phone_normalization_log` ✅ Applied
- `017_memory_promotion.sql` — `promoted_to_kb`, `promoted_at`, `dismissed` on `llm_learning_memory` ✅ Applied

**Health check after restart (2026-06-10):**
```json
{
  "status": "ok",
  "probes": {
    "db": { "status": "ok" },
    "ollama": { "status": "ok", "models": ["qwen3:8b", "qwen2.5:3b"] },
    "llm": {
      "primary_provider": "github_models",
      "github_models": { "status": "ok", "model": "openai/gpt-4o-mini" },
      "ollama": { "status": "ok", "active_model": "qwen2.5:3b" }
    }
  }
}
```

---

## Backup Location

Most recent pre-regen backup: `/home/azim/backups/fazle-backup-20260531-2150`

Contents: `.env`, `config.py`, systemd units, docker compose files, nginx configs, logs snapshot, DB schema dump, knowledge base data dump.
