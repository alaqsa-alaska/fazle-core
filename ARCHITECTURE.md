# System Architecture & Integration Contract
**Date:** 2026-06-21 | **Last updated:** 2026-06-21 | **Verified against:** live VPS runtime

> **Source of truth rule:** The VPS is the source of truth for all production state.
> GitHub is a backup mirror only. Never pull from GitHub into the VPS production environment
> without explicit owner approval. The correct flow is: VPS → commit → push to GitHub.

## Status Summary (2026-06-21)

| Item | Status |
|------|--------|
| GitHub Models (`openai/gpt-4.1`) | ✅ Fixed — `ok`, recovered after token rotation + model name correction |
| Meta webhook GET verification | ✅ Confirmed — `https://fazle.iamazim.com/webhook/meta` returns challenge, HTTP 200 |
| Meta webhook re-registration at Meta Developer Portal | ✅ Completed (configured by owner) |
| config.py defaults aligned to runtime | ✅ Applied 2026-06-21 (5 defaults updated) |
| Residual directory reference-check | ✅ Completed — see Section J |
| Groq fallback | ✅ Healthy — `llama-3.1-8b-instant`, 119ms latency |
| fazle-core overall health | ✅ `ok` — all probes green |

---

## A. System Ownership Matrix

| Domain | Owner | Boundary |
|--------|-------|----------|
| WhatsApp operations, payroll, attendance, escort, recruitment, AI replies | **fazle-core** | Primary operational source of truth |
| SMS onboarding, employee app identity, GPS/location, Firebase integration | **LocationWhere** | App-local, outbound sync only |
| Shared operational employee roster (`wbom_employees`) | **fazle-core** | Do not write from outside fazle-core except via documented sync contract |
| Employee sync from onboarding to roster | **Integration contract** | LocationWhere → fazle-core, opt-in, explicit |
| APK distribution for Android apps | **nginx static** | `/var/www/locationwhere.iamazim.com/downloads/` |
| Infrastructure (Postgres, Redis, Ollama, Qdrant, monitoring) | **Shared** | Both apps use shared containers; see Section E |

---

## B. Integration Contract: LocationWhere → fazle-core Employee Sync

**Status:** Active (opt-in by default, controlled by `FAZLE_CORE_SYNC_ENABLED`)

**What it does:**
When a new employee is created via SMS onboarding in LocationWhere, the service calls
`syncEmployeeToFazleCore()` which writes directly into fazle-core's `wbom_employees` table
on the shared Postgres using the same DB credentials.

**Source:** [location_where/backend/src/modules/employee/employee.service.ts:15-115]

**Sync logic:**
- `FAZLE_CORE_SYNC_ENABLED=true` (default in `.env.locationwhere.example:32`)
- Target table: controlled by `FAZLE_CORE_EMPLOYEE_TABLE` (default: `wbom_employees`)
- Fields synced: `employee_mobile`, `employee_name`, `status=ACTIVE`, `joining_date`, `created_at`, `updated_at`
- Schema-aware: checks `information_schema.columns` before writing; skips gracefully if table or columns are missing
- Upsert pattern: SELECT → UPDATE if mobile exists, INSERT if not
- Failure mode: logs warning `fazle_core_sync_failed`, does NOT block LocationWhere onboarding

**Ownership rules:**
- fazle-core owns the `wbom_employees` schema. Any column addition or removal must check LocationWhere sync consumer.
- LocationWhere is a write consumer of `public.wbom_employees`, not the owner.
- The sync crosses Prisma schema boundaries: LocationWhere's Prisma uses `schema=locationwhere` for its own models but writes to `public.wbom_employees` via `$executeRawUnsafe`.
- Do not add NOT NULL columns to `wbom_employees` without updating the sync logic.

**To disable sync:** Set `FAZLE_CORE_SYNC_ENABLED=false` in LocationWhere `.env`. No code change required.

---

## C. Cross-App Data Coupling Map

| Data Object | Primary Owner | Secondary Access | Coupling Type | File |
|-------------|---------------|------------------|---------------|------|
| `wbom_employees` | fazle-core | LocationWhere writes on new SMS onboarding | Direct cross-schema SQL write | [employee.service.ts:36-115] |
| LocationWhere employee records | LocationWhere | None in fazle-core | App-local | [employee.service.ts] |
| Onboarding gateway payload | LocationWhere | Android SMS gateway | HTTP contract | [gateway.routes.ts:59-119] |
| APK download URL | nginx static | Sent in onboarding SMS reply | Static file reference | `.env.locationwhere.example:28` |
| Postgres container | Shared | Both apps, different schemas | Same host, different schema | [.env:6], [.env.locationwhere.example:7] |
| Redis container | Shared | fazle-core: DB 9, LocationWhere: DB 15 | Same host, different DB index | [.env.locationwhere.example:8] |

---

## D. Runtime Config Map

Confirmed live runtime values on 2026-06-21 (from `python3 -c "from app.config import get_settings; ..."`):

| Setting | config.py Default | Live Runtime Value | README Claimed | Match? |
|---------|------------------|-------------------|----------------|--------|
| `ollama_model` | `qwen2.5:3b` | `qwen3:8b` | `qwen2.5:3b` | ❌ Default stale |
| `bridge1_url` | `localhost:8080` | `localhost:8082` | — | ❌ Default stale |
| `bridge2_url` | `localhost:8081` | `localhost:8081` | — | ✅ |
| `auto_reply_sources` | `bridge1,meta` | `bridge1,bridge2,meta` | `bridge1,bridge2` | ❌ README and default both stale |
| `ollama_reply_disabled` | `False` | `False` | `true` | ❌ README stale (corrected 2026-06-21) |
| `draft_creation_enabled` | `False` | `True` | `true` | ❌ Default stale |
| `meta_api_url` | `graph.facebook.com/v22.0` | `graph.facebook.com/v23.0` | — | ❌ Default stale |
| `github_model_name` | `openai/gpt-4o-mini` | `OpenAI GPT-4.1` | — | ⚠️ Runtime value may be wrong format (see Section F) |
| `primary_ai_provider` | `github_models` | `github_models` | `github_models` | ✅ |

**Proposed config.py default alignment (requires owner approval before applying):**

```python
# Proposed diff — do NOT apply without owner approval
# File: /home/azim/core/app/config.py

# Line 23:
-    ollama_model: str = "qwen2.5:3b"
+    ollama_model: str = "qwen3:8b"

# Line 44:
-    bridge1_url: str = "http://localhost:8080"
+    bridge1_url: str = "http://localhost:8082"

# Line 107:
-    draft_creation_enabled: bool = False
+    draft_creation_enabled: bool = True

# Line 103:
-    auto_reply_sources: str = "bridge1,meta"
+    auto_reply_sources: str = "bridge1,bridge2,meta"

# Line 62:
-    meta_api_url: str = "https://graph.facebook.com/v22.0"
+    meta_api_url: str = "https://graph.facebook.com/v23.0"
```

These are environment-overridden in production so the service runs correctly regardless.
Aligning defaults prevents accidental wrong config on a fresh deploy or after `.env` loss.

---

## E. Infrastructure Overlap Map

| Service | Container | fazle-core uses | LocationWhere uses | Isolation |
|---------|-----------|-----------------|-------------------|-----------|
| PostgreSQL | `ai-postgres` at `172.20.x` | `public` schema, all tables | `locationwhere` schema (Prisma) + writes to `public.wbom_employees` | Separate schemas, same user — NOT fully isolated |
| Redis | `ai-redis` at `172.20.x` | DB 9 (app), DB 3 (agent) | DB 15 | Separate DB index — isolated |
| Ollama | `ollama` container | `qwen3:8b`, `nomic-embed-text` | Not used | fazle-core only |
| Qdrant | `qdrant` at `127.0.0.1:6333` | Not yet integrated | Not used | Unused — R9 future task |
| MinIO | `minio` at `127.0.0.1:9000` | Media/document storage | Not used | fazle-core only |
| Prometheus/Loki | Monitoring stack | Scrapes fazle-core metrics | Not configured | fazle-core only |

**Common failure domain:** A Postgres or Redis outage affects both apps simultaneously.

---

## F. GitHub Models Recovery

**Current status (2026-06-21):** `Unauthorized` (was `403 no_access` on 2026-06-19)

**What changed:** GITHUB_TOKEN was rotated. Error changed from `no_access` (wrong scope) to `Unauthorized` (token rejected).

**Two possible causes — check in this order:**

1. **Token format issue.** The token in `.env` may have been saved with extra whitespace, a newline, or is missing the `ghp_` prefix. Check the raw bytes: `wc -c <<< "$GITHUB_TOKEN"` — a classic `ghp_` token is 40 chars + newline = 41 bytes.

2. **Model name format.** Runtime shows `github_model_name = "OpenAI GPT-4.1"` (display name with spaces and capital letters). GitHub Models API requires the API model ID, not the display name. The correct API ID is `openai/gpt-4.1` (lowercase, no spaces). If `GITHUB_MODEL_NAME` in `.env` was set to the human-readable display name, update it to the API ID.

**Required owner action (SSH):**
```bash
# Step 1: verify token has no whitespace
grep "^GITHUB_TOKEN=" /home/azim/core/.env | awk -F= '{print length($2), "chars"}'

# Step 2: verify model name is API format
grep "^GITHUB_MODEL_NAME=" /home/azim/core/.env

# Step 3: if model name wrong, update it:
# Change: GITHUB_MODEL_NAME=OpenAI GPT-4.1
# To:     GITHUB_MODEL_NAME=openai/gpt-4.1

# Step 4: after any .env fix, restart fazle-core:
sudo systemctl restart fazle-core.service

# Step 5: verify recovery:
curl -s http://localhost:8200/health | python3 -c "
import sys, json; d=json.load(sys.stdin)
gm = d['probes']['llm']['github_models']
print('GitHub Models:', gm['status'], gm.get('error',''))
"
```

**Fallback:** Groq (`llama-3.1-8b-instant`) is active and healthy. Service is not degraded in production; only the primary LLM path is affected.

---

## G. Meta Webhook Recovery

**Current status (2026-06-21):** Webhook endpoint working — GET verification confirmed returning challenge. Re-registration at Meta Developer Portal is pending.

**Endpoint confirmed working:**
```bash
curl "https://fazle.iamazim.com/webhook/meta?hub.mode=subscribe&hub.challenge=TEST&hub.verify_token=fazle_core_webhook_2026"
# Returns: TEST  (HTTP 200)
```

**Credentials (from `/home/azim/core/.env` and `app/config.py:838-847`):**

| Field | Value / Variable |
|-------|-----------------|
| Callback URL | `https://fazle.iamazim.com/webhook/meta` |
| Verify Token | Value of `META_VERIFY_TOKEN` in `/home/azim/core/.env` |
| Verify Token env key | `META_VERIFY_TOKEN` |
| App ID | Value of `META_APP_ID` in `.env` |
| Phone Number ID | Value of `META_PHONE_NUMBER_ID` in `.env` |
| WABA ID | Value of `META_WABA_ID` in `.env` |
| Meta API version | `v23.0` (live runtime) |

**Operator instructions (browser — Meta Developer Portal):**
1. Go to: **developers.facebook.com** → Your App → WhatsApp → Configuration
2. Under **Webhook**, click **Edit**
3. **Callback URL:** `https://fazle.iamazim.com/webhook/meta`
4. **Verify Token:** copy exact value of `META_VERIFY_TOKEN` from `/home/azim/core/.env`
5. Click **Verify and Save** — Meta sends a GET; fazle-core returns the challenge
6. Under **Webhook fields**, ensure `messages` is subscribed
7. Confirm by checking logs: `tail -50 /home/azim/core/logs/fazle-core.log | grep meta`

**Note on legacy alias:** nginx also routes `https://fazle.iamazim.com/api/fazle/social/whatsapp/webhook` → same backend handler. Use the canonical `/webhook/meta` URL with Meta.

---

## H. APK Distribution

| App | Filename | Nginx-served path | Public URL |
|-----|----------|-------------------|------------|
| LocationWhere tracking app | `app-debug.apk` | `/var/www/locationwhere.iamazim.com/downloads/app-debug.apk` | `https://locationwhere.iamazim.com/downloads/app-debug.apk` |
| SMS Gateway app | `gateway.apk` | `/var/www/locationwhere.iamazim.com/downloads/gateway.apk` | `https://locationwhere.iamazim.com/downloads/gateway.apk` |

**Nginx source:** `/etc/nginx/sites-enabled/locationwhere.iamazim.com.conf:18-22`
```nginx
location /downloads/ {
    alias /var/www/locationwhere.iamazim.com/downloads/;
}
```

**Current APK:** `app-debug.apk` updated 2026-06-21 (9,215,920 bytes).

**NOT served by:** the LocationWhere Node.js backend process. Downloads are pure static nginx files.
`/home/azim/locationwhere-backend/public/downloads/` is a residual path and is NOT in the nginx config.

---

## I. Active vs Residual LocationWhere Paths

| Path | Status | Contains | Action |
|------|--------|----------|--------|
| `/home/azim/location_where/` | **ACTIVE** | Android app, backend source, PM2 config | Primary repo — do not delete |
| `/home/azim/location_where/backend/` | **ACTIVE** | Node.js backend running under PM2 on port 8310 | PM2 `cwd` target |
| `/home/azim/location_where/backend/ecosystem.locationwhere.config.cjs` | **ACTIVE** | PM2 startup config | Used by PM2 |
| `/home/azim/location_where/admin-dashboard/dist/` | **ACTIVE** | Built SPA served by nginx as root | nginx `root` directive |
| `/var/www/locationwhere.iamazim.com/downloads/` | **ACTIVE** | APK files served by nginx | nginx `alias` directive |
| `/home/azim/locationwhere-backend/` | **RESIDUAL** | `public/downloads/` dir created 2026-06-19 | Not in nginx — do not use for uploads |
| `/home/azim/locationwhere-frontend/` | **RESIDUAL** | `index.html`, `assets/` (2 items, Jun 16) | Old build snapshot |
| `AUDIT_REPORT.md:37` in fazle-core | **STALE REFERENCE** | Lists `/home/azim/locationwhere-backend/` as active | Needs audit note update |

---

## J. Operational Cleanup Checklist
**Reference-check completed 2026-06-21. No files deleted. Classification based on grep across nginx, systemd, crontab, PM2, backup scripts, vps-config-git, and core docs.**

### A. Active runtime references — do not change

| Reference | Location | Why active |
|-----------|----------|------------|
| `location_where/backend/ecosystem.locationwhere.config.cjs` | `/home/azim/location_where/backend/` | PM2 `cwd` target for locationwhere-backend process |
| `/home/azim/location_where/admin-dashboard/dist/` | nginx `root` directive | SPA frontend served by nginx |
| `/var/www/locationwhere.iamazim.com/downloads/` | nginx `alias` for `/downloads/` | APK static file serving |
| `location_where/backend/.env` | PM2 process env | Live production secrets |
| `locationwhere` schema in Postgres | `scripts/cleanup/00-create-backup.sh:175` | Schema dump is correct — LocationWhere data lives here |

### B. Historical documentation only — no action needed

| Reference | File | Notes |
|-----------|------|-------|
| `locationwhere-backend` path as active | `AUDIT_REPORT.md:37, 907` | June 2026 snapshot; old path was active then |
| All `locationwhere-backend` / `locationwhere-frontend` refs | `audit-report-2026-06-07.md` | Historical audit, pre-migration |
| Old path migration narrative | `audit-report-2026-06-10.md` | Documents the move; informational |
| `vps-config-git/scripts/ecosystem.locationwhere.config.cjs` | vps-config-git backup | Config backup copy; not executed directly |

### C. Safe-to-archive candidates — requires owner approval before any action

| Path | What it contains | Why safe to archive | Blocking concern |
|------|-----------------|---------------------|-----------------|
| `/home/azim/locationwhere-backend/` | `public/downloads/` dir only | Not referenced by nginx, PM2, or systemd | `scripts/cleanup/03-phase2-old-backups.sh:38` targets it for deletion — review that script before running |
| `/home/azim/locationwhere-frontend/` | `index.html`, `assets/` (2 items) | Not referenced anywhere active | None found — safe to archive after owner confirms |

### D. Needs owner review before touching

| Item | File | Concern |
|------|------|---------|
| `scripts/cleanup/00-create-backup.sh:104` | Tries to back up `/home/azim/locationwhere-backend/.env` | File no longer exists — backup silently skips it; harmless but stale |
| `scripts/cleanup/03-phase2-old-backups.sh:38` | Targets `/home/azim/locationwhere-backend` for deletion | DO NOT run this script without reviewing full scope first |

### Requires owner approval before any change
- Deleting or archiving any directory
- Modifying cleanup scripts to update stale `.env` path reference
- Changing employee sync behavior (`FAZLE_CORE_SYNC_ENABLED`)
- Modifying nginx configs, PM2 ecosystem file, or systemd services

---

## K. Open Manual Actions

| ID | Action | Owner | Priority | How |
|----|--------|-------|----------|-----|
| R2 | Re-register Meta webhook at Meta Developer Portal | You (browser) | 🔴 High | See Section G above |
| R7 | Fix GitHub Models token/model name | You (SSH + .env edit) | 🟡 Medium | See Section F above |
| R9 | Qdrant RAG integration | Development session | 🔵 Arch | `nomic-embed-text` ready; needs `qdrant-client` pip install + new module |

---

## L. Safe Next Steps (no approval needed)

1. Complete Meta webhook re-registration (5 min, browser)
2. Fix GitHub Models token or model name in `.env`, then restart fazle-core (10 min, SSH)
3. Investigate LocationWhere PM2 74 restarts — check logs: `pm2 logs locationwhere-backend --lines 50`
4. Propose config.py default alignment diff to owner (Section D) — low risk, but needs approval before applying
5. Add audit note to `AUDIT_REPORT.md` marking old path references as stale (documentation only)
