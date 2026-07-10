# Fazle Core — Developer Guide

**Last updated:** 2026-07-09
**Companion doc:** [`docs/MODULE_MAP.md`](MODULE_MAP.md) for module-by-module detail. See `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` for the full architecture/risk/gap audit this guide draws on.

**How to read this:** sections B through J below, matching the lettered structure used when this guide was assembled. Every claim is sourced from a direct code/config/command read at the time of writing — items marked **Not verified** should be re-checked rather than trusted blindly, and this whole file should be treated as a point-in-time snapshot, not a live document.

---

## B — Runtime / Infrastructure

**Main service (systemd):**
- Unit: `fazle-core.service` (`/etc/systemd/system/fazle-core.service`)
- `WorkingDirectory=/home/azim/core`, `ExecStart=/home/azim/.venv/bin/python run.py`, `User=azim`
- `EnvironmentFile=/home/azim/core/.env` plus an optional overlay `-/home/azim/secure-env-backup/runtime-services.env`
- `ExecStartPre=/home/azim/shared/scripts/generate_runtime_services_env.sh` — resolves `DATABASE_URL_TEMPLATE`/`REDIS_URL_TEMPLATE` (`__HOST__` placeholder) into concrete URLs by looking up the `ai-postgres`/`ai-redis` container IPs, writing the result into the overlay env file
- Restart policy: `Restart=always`, `RestartSec=5`
- Logs: `/home/azim/core/logs/fazle-core.log` (stdout), `fazle-core-error.log` (stderr)
- Port: `127.0.0.1:8200` (`app_port` in `app/config.py:111`, consumed by `run.py`)
- **No build/artifact step** — the process runs directly off the git working tree; there is no gate between an edit and it being live on the next restart.

**Public URL / nginx:**
- Site config `/etc/nginx/sites-enabled/fazle.iamazim.com.conf`, upstream `/etc/nginx/upstreams/fazle-api.conf` → `127.0.0.1:8200`
- Public domain `fazle.iamazim.com`, HTTPS via Let's Encrypt
- `/docs` and `/openapi.json` are explicitly blocked (`deny all; return 404;`) in production nginx config
- SSE-friendly long timeouts for `/chat/`, `/agent/`, `/api/wa/stream`

**Database:**
- PostgreSQL via Docker container **`ai-postgres`** (image `pgvector/pgvector:pg17`), bound to `127.0.0.1:5432`
- App connects via `asyncpg` (`app/database.py`, pool 2–10 connections, 30s command timeout)
- Database name: `postgres` (not a database literally named "fazle")
- Current size: ~167 MB

**Docker containers running on this VPS** (13 total; only some are app dependencies):

| Container | Image | App dependency? |
|---|---|---|
| `ollama` | ollama/ollama:latest | **Yes** — local LLM, `127.0.0.1:11434` |
| `ai-postgres` | pgvector/pgvector:pg17 | **Yes** — primary DB, `127.0.0.1:5432` |
| `ai-redis` | redis:8.0.2-alpine | **Yes** — `redis_url`, `127.0.0.1:6379` |
| `qdrant` | qdrant/qdrant:v1.17.0 | **Yes** — RAG vector store, internal-network only |
| `open-webui`, `prometheus`, `node-exporter`, `cadvisor`, `promtail`, `loki`, `grafana`, `minio`, `code-server` | various | No / indirect — general VPS observability/tooling, not app dependencies (minio's app usage **not verified**) |

**External services (`app/config.py`):**
- **Ollama** — default model `phi4-mini:latest`, with tiered overrides (`ollama_business_model=qwen3:8b`, etc.)
- **Groq** (fallback) — `llama-3.1-8b-instant`
- **GitHub Models** (2nd fallback) — `openai/gpt-4o-mini` via `models.github.ai/inference`, up to 3 rotating tokens
- **Meta Graph API** — WhatsApp Cloud API, Messenger, Facebook Page comments, all via one webhook
- **Two self-hosted WhatsApp bridges** — Bridge 1 (HR) on `localhost:8082`, Bridge 2 (OPS) on `localhost:8081`; both are independent Go binaries (`whatsapp-bridge`), **not** managed by Docker or the `fazle-core` systemd unit
- **Qdrant** — collection `fazle_rag_chunks`, falls back to an embedded on-disk client if the server is unreachable
- **Media processor** (OCR/voice) — separate Python process on `127.0.0.1:8090`, exposes `/transcribe` (Whisper), `/ocr` (Tesseract), `/extract`
- **LocationWhere integration** — configured (`127.0.0.1:3000/api/v1`) with a full circuit-breaker/retry/cache setup, but **no listener was found on port 3000** during this audit — **not verified** whether it's currently running

**RAM / disk (observed 2026-07-08/09):**
- `free -h`: 23 GiB total, 16 GiB used, **swap 7 GiB total / 4.8 GiB used** — swap usage under current load is a soft capacity warning
- `df -h /`: 194 GB total, 103 GB used (54%), 91 GB free
- Heaviest containers: `ai-postgres` ~1.4 GB, `ollama` ~650 MB (spikes higher during active inference), `fazle-core.service` itself ~1.7 GB
- Backup growth: ~21–22 MB/day, flat — not a near-term disk concern

**Recommended minimum for smooth production operation:** **16 GB RAM floor / 24 GB recommended** (current provisioning) given the app + Postgres + Ollama + Qdrant + Redis + monitoring-sidecar footprint and the observed swap usage; **100 GB disk minimum / 150–200 GB recommended** for headroom (Docker images/logs/Loki retention are the largest disk consumers, not the app's own data).

---

## C — Frontend Pages

All pages live in `/home/azim/core/app/static/*.html`, served both via dedicated `FileResponse` routes in `app/main.py` and the generic `/static/<name>.html` mount.

### dashboard.html
- **URL:** `/` and `/dashboard` (legacy alias `/dashboard/legacy`)
- **Auth:** login overlay, `X-Internal-Key`, cached in `localStorage['fazle_api_key']`
- **Tabs:** Overview, Drafts, Reports, Users, Audit, Backups, Scheduler, RAG, Observability, Chat Lab
- **APIs:** `GET /admin/overview`, `/admin/drafts`, `/admin/payment-drafts`, `/reports`, `/reports/{name}`, `/admin/users` (GET/POST), `/admin/users/{phone}/apikey`, `/admin/users/{phone}/disable`, `/admin/audit`, `/backup/list`, `/backup/run`, `/scheduler/status`, `/rag/stats`, `/rag/reindex`, `/rag/search`, `/observability/summary`, `/observability/errors`, `/chat/models`, `/chat/message`
- **Buttons:** Refresh, Logout, Run Report, Add User, Issue Key, Disable User, audit filter, Run Backup Now, Rebuild RAG Index, RAG Search, send/clear chat
- **Related tables:** `wbom_contacts`, `wbom_whatsapp_messages`, `fazle_draft_replies`, `fazle_payment_drafts`, `fazle_admins`, `fazle_admin_audit`

### drafts.html
- **URL:** `/drafts`
- **Auth:** login overlay, `X-Internal-Key` in `localStorage['drafts_api_key']`
- **APIs:** `GET /api/drafts`, `/api/drafts/stats`, `POST /api/drafts/{id}/approve|delete|block|edit`
- **Buttons:** Approve, Edit (modal), Delete, Block (with reason)
- **Search:** free-text box filters **client-side only** (JS array filter on `recipient`/`contact_name`/`reply_text`, no backend call); status dropdown does hit the backend (`status` param → `fazle_draft_replies.status`)
- **Related tables:** `fazle_draft_replies`, `fazle_contact_roles` (block action)
- ⚠️ Its backend router (`modules.drafts.routes`) was found silently unmounted by in-flight uncommitted work and was restored 2026-07-09 — confirm a restart has happened since, or this page's approve/edit/delete/block buttons still 404.

### escort-roster.html
- **URL:** `/escort-roster`
- **Auth:** login overlay via `/api/admin/session`, `X-Internal-Key`
- **Tabs:** Roster, Kanban, Slip Queue, Shift Calc, Config, Attendance, Net Salary, Admin (role-gated)
- **APIs:** full `/api/escort-roster/*` surface (summary, list, export, config, escort-clients, sync-all, sync-history, match-slip, drafts, cleanup-*, reconcile, backfill-*, slip-queue, per-program get/patch/delete/recalculate)
- **Search:** "Search vessel, escort, mobile…" → `search` param → `ILIKE` across `mother_vessel`, `lighter_vessel`, `escort_name`, `master_mobile`, `escort_mobile`, `destination` (`modules/escort_roster/db.py:519-525`, verified)
- **Buttons:** Sync, Sync-History, Export CSV, Add Program, per-row detail drawer, Slip Queue confirm/reject, Config save
- **Related tables:** escort roster entries/programs (exact aliasing not fully re-derived here — see `docs/MODULE_MAP.md`'s `escort_roster` entry for the full table list)

### kb.html
- **URL:** `/kb`
- **Auth:** login overlay, `X-Internal-Key` in `localStorage['kb_api_key']`
- **APIs:** `GET /admin/kb/stats`, `POST /admin/kb/upload` (multipart), `GET /admin/kb/list`, `POST /admin/kb/delete`
- **Forms:** file upload (PDF/DOCX/TXT/MD/CSV, max 10MB) with Category/Subcategory/Tags
- **Search:** category filter dropdown only, no free-text search
- **Related table:** `fazle_knowledge_base`

### open-chat.html
- **URL:** `/chat-lab` and `/open-chat`
- **Auth:** `prompt()`-based key entry, `localStorage['fazle_api_key']`
- **APIs:** `GET /chat/models`, `/chat/memory/stats`, `/chat/memory/history`, `POST /chat/message`
- **UI:** 3-panel "AI Operating Console" — memory/quick-prompts panel, conversation composer (file/image/voice attach are **local preview only**, not yet uploaded — "READ ONLY mode" per the UI's own notice), Response/Evidence/Trace/Runtime/Memory tabs
- **Search:** none (chat composer, not a search box)

### wa_chat.html
- **URL:** `/wa-chat` and alias `/dashboard/wa-chat`
- **Auth:** login overlay, `X-Internal-Key`
- **APIs:** full `/api/wa/*` surface — contacts (list/sync/get/patch/delete/block/unblock), messages, send, drafts (approve/reject/edit), settings, broadcast, groups, `/api/wa/stream` (SSE)
- **Search:** "Search contacts…" → `search` param → `ILIKE` on `sender_number`, `display_name` (`modules/wa_chat_frontend/__init__.py:256-257,290-291`, verified)
- **Buttons:** Sync contacts, bulk-select + Broadcast, per-draft Approve/Reject/Edit, contact edit/block/delete, group create/delete/send
- **Related tables:** `fazle_draft_replies`, `fazle_runtime_settings`, `wbom_contacts`, `wbom_whatsapp_messages`
- ⚠️ Same router-mounting caveat as `drafts.html` above (`social_auto_reply` router was also found unmounted and restored 2026-07-09) — this page's control surface depends on that fix taking effect on next restart.

### payroll.html (~4,800 lines, largest page — sampled by tab)
- **URL:** `/payroll` plus deep-link SPA routes `/payroll/overview|transactions|search|employees|addemployee|unmatched|review|sync|manual|operator|cash|income|admin`
- **Auth:** supports both API-key and username/password login (`POST /api/admin/login`); session check returns role flags (`can_edit_delete_transactions`, `is_restricted_office_assistant`, `can_manage_admin`) that gate UI visibility (e.g. Admin tab hidden unless `can_manage_admin`)
- **Tabs (abbreviated — see the search-fields table below for their search behavior):** Overview (stats + recent txns), Transactions (period/date/method/employee filters, Add Payment), Search (read-only employee lookup), Employees (list + Add Employee form), Unmatched (review queue), Review Queue (pending accounting candidates, bulk promote/reject/dismiss), Sync (FPE sync status/trigger), Manual Entry (manual transaction + reversal), Operator (operator-submitted pending approval), Cash (cash transaction search + "WhatsApp Shorthand Staging" sub-table), Income (income entry form + search), Admin (role-gated — add admin user, salary baseline, activity log; also calls the separate `/api/admin/employees`/`/api/admin/transactions` CRUD surface, which uses `wbom_employees`, distinct from `fpe_employees` used elsewhere on this page)
- **Related tables:** `fpe_employees`, `fpe_employee_aliases`, `fpe_cash_transactions`, `fpe_income_transactions`, `wbom_staging_payments`, `wbom_employees` (Admin tab CRUD only)

### ux-preview.html / ux-preview-v2.html
- **Not routed** through `app/main.py` at all — reachable only via the generic static mount. Zero `fetch()` calls, no auth, no DB tables — static before/after design mockups, not a live tool.

### Search Fields Summary Table

| Page | Field / Placeholder | API Param | Backend Column(s) | Verified? |
|---|---|---|---|---|
| dashboard.html | RAG Search — "Ask in Bangla or English…" | `q` (`/rag/search`) | RAG chunk index (not a SQL column) | N/A — not a SQL search |
| dashboard.html | Audit filter — "filter command" | `command` (`/admin/audit`) | `fazle_admin_audit` (exact column) | Not verified |
| drafts.html | "নম্বর / নাম / message…" | *(none — client-side JS filter)* | in-memory `recipient`/`contact_name`/`reply_text` | Verified (client-side only) |
| drafts.html | Status dropdown | `status` | `fazle_draft_replies.status` | Verified |
| escort-roster.html | "Search vessel, escort, mobile…" | `search` | `mother_vessel`, `lighter_vessel`, `escort_name`, `master_mobile`, `escort_mobile`, `destination` (`ILIKE`) | Verified |
| wa_chat.html | "Search contacts…" | `search` | `sender_number`, `display_name` (`ILIKE`) | Verified |
| payroll.html (Employees) | "name, alias, phone, or employee ID…" | `q` | `full_name`, `employee_id_phone`, `primary_phone`, `employee_code`, + `fpe_employee_aliases.alias_value` | Verified |
| payroll.html (Search/Overview/Manual/Operator) | smart-search boxes | `q` (`/employees/suggest`) | fuzzy match over `fpe_employees` + aliases, pg_trgm scoring | Verified |
| payroll.html (Cash) | "name / phone / ref…" | `q` | `employee_name_raw`, `payout_phone`, `txn_ref`, `full_name` (joined), `source_message_text` | Verified |
| payroll.html (Cash) | Sender Phone | `sender_phone` | `payout_phone` (`LIKE`) | Verified |
| payroll.html (Cash, staging) | "name / phone…" | `q` | `extracted_name`, `extracted_mobile`, `employee_name` (joined) | Verified |
| payroll.html (Income) | "name / phone / ref…" | `q` | `employee_name_raw`, `reported_by_phone`, `txn_ref`, `full_name` (joined), `source_message_text` | Verified |
| payroll.html (Income) | Reporter Phone | `reporter_phone` | `reported_by_phone` (`LIKE`) | Verified |
| payroll.html (Admin) | Admin users search | — | not directly traced | Not verified |

---

## E — Finance / Accounts Flow

**End-to-end payment flow.** Two admin-command paths both terminate in the same canonical writer:
1. **Legacy `PAID`/`ADVANCE`:** `admin_commands._cmd_paid()` → `payment_workflow.finalize_payment()` → `fazle_payroll_engine.accounting.create_transaction()` inside a row-locked DB transaction. Draft goes `pending → sent`.
2. **Canonical `APPROVED`:** `admin_commands._cmd_approved()` → `draft_approval.approve_draft()` — pending-state check, 24h TTL expiry check, idempotency check, row lock, `status='approved'`, then `create_canonical_transaction()` → the same `create_transaction()`. Draft ends `status='completed'` with `transaction_id`/`txn_ref` stamped back on.
3. **`DREDIT`/`DREJECT`:** edit/reject a pending draft without ever calling `create_transaction()` — explicitly documented in code as never touching the ledger.

**Draft vs. transaction.** A draft (`fazle_payment_drafts`, status starts `pending`) is created first by `payment_workflow.create_escort_payment_draft()` or `create_advance_request_draft()`. The transition draft → canonical transaction happens **only** when an admin's `PAID`/`ADVANCE`/`APPROVED` command reaches `create_transaction()`. A separate table, `fazle_draft_replies` (non-financial AI-drafted replies), has its own `pending → approved → sent` lifecycle via `APPROVE`/`REJECT`/`EDIT` and never touches money.

**Ledger tables:**
- `fpe_cash_transactions` — immutable, INSERT-only, one row per payment, idempotent via `txn_ref` (sha256), soft-delete columns, corrections via `reverse_transaction()` (counter-rows, original never mutated).
- `fpe_employee_ledger` — running per-employee-per-month totals, updated exclusively by `_upsert_ledger()`, called only when a transaction is `final`.
- `fpe_income_transactions` — deliberately separate from the cash ledger, **no status/state-machine column at all** — a flat immutable INSERT-only table.

**No general ledger / double-entry accounting exists.** `BUSINESS-CONSTITUTION.md` Article I declares `create_transaction()` the one canonical function; Article I §1.2 prohibits direct writes outside it. This confirms a single canonical cash-transaction writer, not a chart-of-accounts/debit-credit model. Zero hits repo-wide for double-entry/chart-of-accounts/trial-balance/bank-reconciliation terminology.

**Owner decision (2026-07-08) — Client Billing System is now in scope.** Every payment module found only pays employees/guards (cost side) — nothing bills the company's own clients (revenue side). The Owner has decided this is now part of **Phase 2b**: client billing, invoice generation, accounts receivable, client payment/outstanding-balance tracking, a link from completed escort service to the resulting invoice, and revenue-side GL postings. **Nothing has been built for this yet** — it's a scope decision, not a delivered feature.

---

## F — Employee Creation and Linkage

**Creation paths:**
1. **Admin frontend** — `POST /api/admin/employees` writes **`wbom_employees`** first, then non-fatally pre-seeds an `fpe_employees` link via `match_or_create_employee()`.
2. **Auto-creation via payroll parsing** — `fazle_payroll_engine.employee.match_or_create_employee()` is FPE's own canonical matcher: exact phone → alias phone → cross-lookup against `wbom_employees` → exact name → fuzzy name (rapidfuzz, threshold 96.0) → auto-create, guarded by an advisory lock. Refuses to auto-create a "phantom" employee if there's no phone evidence and the name match is ambiguous.
3. **Escort confirmation** — two inconsistent paths: `modules.escort`'s own resolver creates/links both `wbom_employees` and `fpe_employees`; but `admin_commands._cmd_escort_confirm()` (the `ESCORTCONFIRM` command) does a direct `INSERT INTO wbom_employees` with **no** `fpe_employees` linking at all.
4. **Frontend "Add Employee" form** — same as (1).

**Canonical-table claim vs. actual code.** `database_rules.md` states `fpe_employees` is now the single canonical table with all creation paths linking to it. **This is only partially true**: the FPE-native matcher genuinely treats it as canonical, but the admin-facing creation path and the `ESCORTCONFIRM` command both still write `wbom_employees` as primary, with `fpe_employees` linking either non-fatal-best-effort or entirely absent. **Not verified** whether this is intentional (KB describing an *intended* end state) or documentation/code drift.

**Phone normalization — a documented 3-way situation, not accidental sprawl.** `PHONE_NORMALIZER_CONVENTION.md` deliberately defines: `modules.phone_normalizer` (canonical 13-digit `8801XXXXXXXXX`), `modules.number_identity` (3-variant helper), and `fazle_payroll_engine.normalizer.normalize_bd_phone()` (an explicitly-documented 11-digit `01XXXXXXXXX` FPE-only exception — which also confusingly self-declares itself "the single source of truth," a naming collision with `phone_normalizer`'s own claim). One genuine inversion exists: `shared/phone.py` imports *upward* from `modules.fazle_payroll_engine.normalizer` while also claiming to be canonical.

**Main inter-linking keys (verified):**
- `fpe_employees.wbom_employee_id` → `wbom_employees.employee_id` (nullable soft-link)
- `fpe_employees.primary_phone` / `employee_id_phone` (11-digit)
- `wbom_employees.employee_mobile` (13-digit), with `bkash_number`/`nagad_number` fallback anchors
- `fpe_employee_aliases (employee_id, alias_type, alias_value)` — multi-phone/multi-name aliases
- `fpe_cash_transactions.employee_id` → `fpe_employees.id` (never `wbom_employees.employee_id` directly)
- `wbom_escort_programs.escort_employee_id` / `escort_roster_entries.escort_employee_id` → `wbom_employees.employee_id` (HR-domain anchor, not FPE)
- `fazle_payment_drafts.employee_id` → resolved to an `fpe_employees.id` at transaction-creation time

**Known risk.** `wbom_employees` is referenced in 29 Python files, `fpe_employees` in 14 — both live simultaneously. This is an already-documented risk (see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 8, Risk #8), not a new finding.

---

## G — Escort / Client / Admin Flow

**Order arrival → parsing.** A client's WhatsApp order routes to `escort.handle_escort_client_message()`, gated by source/sender allow-lists, which calls `parse_escort_message()`. The parser identifies the mother vessel via a heuristic (a vessel line *with* a mobile number is treated as a lighter vessel even if MV-labeled), then tries 4 lighter-parsing strategies in priority order (mobile-grouped → labeled → inline → MV-block fallback), falling through only if the previous strategy finds nothing. `is_minimum_escort_order()` gates that at least one lighter has both a name and a master mobile before anything is persisted.

**Program creation.** `save_escort_programs()` inserts one `wbom_escort_programs` row per valid lighter (`status='draft'`), deduping against existing non-cancelled rows. When the admin sends back a completed draft, `handle_admin_escort_completion()` authenticates it as an "authoritative bridge confirmation," matches it to the pending draft, resolves/auto-creates the escort employee, and flips `status='confirmed'` — the bridge confirmation is treated as authoritative and overwrites draft fields.

**Roster sync.** Immediately after confirmation, `escort_roster.db.sync_program_to_roster()` upserts a matching `escort_roster_entries` row, computing pay via `calculations.calculate_pay()` when both start/end dates are known, and mapping the program's lifecycle status onto `roster_status`.

**Duty completion → payment draft.** `escort_lifecycle.close_program()` is idempotent (a no-op if already `Completed`). The top-level `handle_release_event()` requires admin confirmation, runs in one transaction, backfills `wbom_attendance` (idempotent, `entry_source='escort_release'`), then calls `payment_workflow.create_escort_payment_draft()` — computing `gross_amount = duty_days × 400tk/day` (flat shift-based rate, **fixed 2026-07-10** — was previously `basic_salary/30`, the wrong formula for escort duty) `− food_bill + conveyance − advances` (conveyance **added**, not subtracted), and upserting a `fazle_payment_drafts` row ready for a `PAID`/`APPROVED` command. Also (2026-07-11): on the first admin-approved attendance for a `confirmed`-status program, `escort_lifecycle.mark_program_running_on_attendance()` idempotently flips it to `running`.

**Two known, already-documented pending issues:**
1. `escort_lifecycle` writes `escort_roster_entries` directly via a raw `INSERT ... ON CONFLICT`, bypassing `escort_roster`'s own sync API (see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` Report 3 §7).
2. **Formula decision made 2026-07-10, `compute_run` not yet rewritten.** The escort-duty formula question above is resolved (see the fixed formula above). A *separate* pay formula, `modules/payroll::compute_run`'s monthly rollup (full `basic_salary` + a flat escort-day allowance), is now scoped exclusively to Office Staff (`modules/payroll/eligibility.py::classify_office_staff`, escort employees explicitly excluded) via `compute_all_for_period_office_only` — but `compute_run`'s own internal calculation has **not yet** been rewritten to the new calendar-days/attendance-based formula (`docs/NEXT_IMPLEMENTATION_QUEUE.md`), so `job_daily_payroll` stays guarded behind `settings.office_payroll_authoritative_enabled` (default False) until that rewrite happens and the Owner signs off. Real production evidence, still true: all 703 `wbom_payroll_runs` sit in `status='draft'`; the `PAID <id> <amount> <method>` admin command remains the only path that actually moves money, with a free-typed amount and no cross-check.

**Office Attendance & Payroll Eligibility (new, 2026-07-11).** A separate pipeline from escort attendance: `modules/attendance/crud.py` (manual CRUD, `entry_source='manual_office'`) + `modules/attendance_parser` (supervisor WhatsApp message → draft → admin approval, `entry_source='message_parser'`) feed `modules/payroll/dry_run.py`'s read-only `eligibility_preview`/`payroll_dry_run`/`reconciliation_report`, exposed at `/admin/payroll/{eligibility-preview,dry-run,reconciliation}` (`modules/attendance/routes.py`, admin+/superadmin only). Escort attendance (`whatsapp_escort`, `escort_release`) never counts toward this, regardless of approval. **Currently 0 of 188 employees are eligible** — confirmed correct against real production data, not a bug (184 are Escort-designation; the remaining 4 each fail a different structural gate).

**Client billing connection — does not exist yet.** No code, table, or workflow links a completed escort program to a client-facing invoice. Confirmed Owner-decided Phase 2b scope (see §E above). **Status, 2026-07-09:** the schema (`wbom_billing_records`, `wbom_client_tiers`/`client_statuses`) is now live, and `modules/billing/invoice_numbering.py` provides a working `AQS-YYYY-NNNNNN` number generator — but the actual bill-creation code is blocked: a fresh audit found **no client-facing rate/price field exists anywhere in this schema** (every candidate field — `wbom_escort_programs.total_payment`, `escort_calculation_config.shift_rate` — is the guard's cost, not the client's charge). Building the escort-completion → bill hook needs an Owner decision on where `service_charge` comes from first. Full detail: `docs/PHASE2B_READINESS_REPORT.md`.

---

## H — Message Parser / AI / RAG Flow

**Ingestion sources:** two self-hosted WhatsApp bridges (SQLite polling every 5s, read-only access, DMs always persisted before routing, groups/broadcasts skipped entirely); Meta WhatsApp Cloud API + Messenger + Facebook Page comments (single webhook, `POST /webhook/meta`).

**Identity detection:** `identity_brain.detect_identity()` runs a strict priority cascade (admin → family → accountant → vip_client → client_escort_buyer → repeat_client → vendor → employee → supervisor → candidate → unknown), checking seed rules → employee DB → escort roster → contacts → text-hint keywords, in that order, returning on first match. Never raises — falls back to `unknown`.

**Message routing:** `message_router.process_message()` evaluates in ~15 explicit numbered tiers: family → escort-client (strict order gate) → admin → attendance → intent classification → accountant → recruitment eligibility → escort order → employee (including Sprint-3A Employee Conversation Workflow) → advance/payment request → office-location fast path → knowledge base → reviewed-reply lookup → AI fallback.

**Recruitment reply flow:** `recruitment_flow` → `conversation_layer.generate_recruitment_reply()` → KB → RAG → Ollama LLM → safety filter; returns `None` (no reply) if the result is empty or flagged `restricted`.

**Draft reply flow:** `shared.draft_reply.create_draft_reply()` is the single source of truth for every `fazle_draft_replies` INSERT — acquires a distributed lock, re-checks for a duplicate pending draft *inside* the lock (closing the classic SELECT-then-INSERT race). `modules.draft_quality` is a separate gate applied before saving — strips emoji, rejects known-fallback-string matches, stores rejects with a distinct status so they never reach an admin's queue.

**RAG/KB — two independent paths, no coordination (already-documented finding, not new):** `modules.knowledge_base` does DB-first exact-intent lookup with a hardcoded fallback; `modules.rag` does separate BM25/semantic search. Both independently feed `message_router` — no precedence rule exists between them. Flagged as a Phase-3 (AI console unification) prerequisite in the consolidation report.

**LLM provider fallback chain:** Ollama (local) → Groq → GitHub Models → fixed holding reply. Ollama continues running for intent classification/RAG/learning-memory independent of the customer-reply-disable flag.

**What local Ollama can and cannot do (verified against code):**
- **Cannot:** get unrestricted SQL, direct DB access, or send WhatsApp messages — `ai_console/adapters.py` explicitly declares these as `prohibited_side_effects` per adapter class, and the live orchestration path only instantiates read-only adapter classes.
- **Can (read-only):** query business data only through `modules.ai_readonly_tools`'s named, SELECT-only functions via a separate DB role, capped at 100 rows, with sensitive fields excluded at the view level (NID, bank, token, password).
- **Can write (isolated only):** its own memory store, `modules.ollama_memory` — explicitly documented as the *only* module allowed to write there; production DB is never written by the AI.

**Concrete safe vs. write-capable action list:**
- **Safe/read-only:** `ai_readonly_tools.get_contact_summary/get_payment_summary/get_escort_program_status/get_attendance_summary/get_payroll_run_status/get_recruitment_leads/...`, `fetch_web_page` (GET-only), `ollama_memory.recall_facts/get_recent_questions/...` (reads of its own memory).
- **Write-capable (never exposed to Ollama, human/admin-command-triggered only):** `accounting.create_transaction()`/`reverse_transaction()`, `payment_workflow.finalize_payment()`, `draft_approval.approve_draft()`, employee CRUD, `escort_lifecycle.close_program()`, `admin_commands` money/user-management handlers, `shared.draft_reply.create_draft_reply()` (AI-generated text lands as a *draft*, never a direct send). The only write path Ollama itself owns is confined to `fazle_ollama_memory`.

---

## J — Known Strengths / Weaknesses / Roadmap

**Strengths:**
- The payroll ledger (`fpe_cash_transactions`/`fpe_employee_ledger`) is genuinely production-grade: immutable, idempotent (`txn_ref` sha256 + DB `UNIQUE` constraint), audited, with a documented single-canonical-writer rule (`BUSINESS-CONSTITUTION.md`).
- RBAC is real and enforced consistently across ~12 route modules, not decorative.
- The outbound message queue has genuine production-grade engineering — durable, idempotent, DLQ, circuit-breaker with admin alerting.
- Identity resolution (`identity_brain`) and message routing (`message_router`) are carefully layered, single-choke-point designs that don't duplicate logic elsewhere.
- Backups are real and verified running (18+ consecutive daily dumps as of the 2026-07-08 audit), with a self-checking staleness alert.
- The AI layer's read/write boundary is one of the best-enforced parts of the system — Ollama genuinely cannot reach production write paths or unrestricted SQL.

**Weaknesses (see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` for full evidence on all of these):**
- No CI/build gate between a code edit and it running in production — the service runs directly off the working tree.
- Schema governance is fragmented across 3+ migration mechanisms with real cross-directory collisions; 27 production tables had zero schema source of truth until 2026-07-08's bootstrap migration.
- Two employee-creation paths still write `wbom_employees` as primary despite `fpe_employees` being documented as canonical (§F above).
- `escort_lifecycle` bypasses `escort_roster`'s own sync API, and two independent pay-calculation formulas feed the same table with no resolved precedence (§G above).
- `knowledge_base` (exact-match) and `rag` (semantic) retrieval run independently with no coordination (§H above).
- No general ledger, no client billing/invoicing/accounts-receivable, no dispatch/scheduling conflict-detection — the three biggest gaps against the "complete operating system" goal.
- ~54% of business modules have no dedicated test coverage, including some Critical-risk ones.

**Already fixed (2026-07-08/09, see `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`'s "Current Consolidation State" section for full detail):** leaked credentials scrubbed from docs, RBAC fail-open→fail-closed, drafts/social-auto-reply routers remounted, migration governance documented, 27-table schema bootstrap migration, client-billing scope decided, `AUTO_REPLY_ENABLED` KB documentation reconciled, a test-harness shared-pool bug fixed.

**Remaining critical issues:** production credentials still need rotation (values are known-leaked even though docs are scrubbed); the dual pay-formula issue needs a runtime check before Phase 2a starts; cross-directory migration collisions are documented but not yet resolved.

**Roadmap** (see `docs/NEXT_IMPLEMENTATION_QUEUE.md` for the actionable, prioritized version of this):
- **Phase 1 — Consolidation** (in progress): make the Owner's own `BUSINESS-CONSTITUTION.md` Article VII freeze real — migration consolidation, duplicate-logic cleanup, deployment gate, secret rotation.
- **Phase 2 — Dispatch/scheduling + Accounting/GL + Client Billing:** availability/conflict-detection for guard assignments (Owner-prioritized first); a real chart-of-accounts/debit-credit layer on top of the existing cash ledger; client billing, invoicing, and accounts receivable (Owner-decided scope addition, 2026-07-08).
- **Phase 3 — AI Operating Console unification:** runtime-ingest the curated `knowledge_base/` tree into RAG (today it only indexes `resources/*.txt`); extend `ai_readonly_tools`/`ai_console` with safe read-only tools over the new dispatch/GL data; a single natural-language, evidence-backed query surface in `open-chat.html` across payroll, escort, accounting, and dispatch.
