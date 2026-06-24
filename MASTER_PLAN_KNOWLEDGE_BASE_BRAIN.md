# Fazle Core Master Plan — Knowledge Base as the System Brain

**Created:** 2026-06-24  
**Location:** `/home/azim/core/MASTER_PLAN_KNOWLEDGE_BASE_BRAIN.md`  
**Primary repo:** `git@github.com:arshiyaazim/fazle-core.git`  
**Primary branch:** `backup/vps-core-20260612`  
**Runtime source of truth:** VPS `/home/azim/core`  
**Safety status:** Planning document only. No production code, DB schema, bridge DB, or service config was changed while creating this file.

---

## 1. Executive Goal

এই project-এর মূল লক্ষ্য হলো `fazle-core`-কে এমন একটি operations brain বানানো যেখানে:

- `knowledge_base/` হবে পুরো business, system, workflow, permission, database, and AI behavior-এর authoritative brain.
- Ollama/local model এই brain থেকে answer, analysis, and read-only operational insight দিতে পারবে.
- Existing app modules, code paths, database tables, frontend pages, ports, and assistant apps এই brain-এর সাথে traceable হবে.
- যেসব code/module/document বাস্তব system-এর সাথে মেলে না, unused, duplicate, stale, or risky, সেগুলো আগে identify, তারপর archive/deprecate/delete করা হবে.
- WhatsApp bridge 1 and 2 receive/send capability, bridge SQLite stores, and production DB tables/columns কোনোভাবেই ক্ষতিগ্রস্ত করা যাবে না.

**Non-negotiable production constraints:**

- `whatsapp1/store/messages.db`, `whatsapp1/store/whatsapp.db`, `whatsapp2/store/messages.db`, `whatsapp2/store/whatsapp.db` are read-only integration sources for Fazle Core. Do not write, migrate, vacuum, or alter them.
- Existing production DB tables/columns must not be changed for cleanup/refactor unless there is a separate migration plan, backup, dry-run, and owner approval.
- WhatsApp bridge send/receive must remain operational during every phase.
- Meta WhatsApp Cloud API and Facebook Page tokens must never be printed in logs or committed files.
- Any delete operation must have dependency evidence, tests, backup/archive, and explicit owner approval.

---

## 2. Current System Snapshot

### 2.1 Core runtime

| Component | Runtime | Current role |
|---|---|---|
| Fazle Core | FastAPI, port `8200`, systemd `fazle-core.service` | Main orchestrator, dashboard, webhooks, routing, RAG, payroll, OCR, drafts |
| WhatsApp Bridge 1 | `whatsapp-bridge.service`, port `8082` | WhatsApp QR bridge, HR/admin number |
| WhatsApp Bridge 2 | `whatsapp-bridge2.service`, port `8081` | WhatsApp QR bridge, OPS/admin authority |
| WhatsApp Bridge 3 | `whatsapp-bridge3.service` | Installed/running, not primary in current core config |
| Ollama | port `11434`, model `qwen3:8b` | Local LLM fallback/classifier/RAG chat support |
| Groq | external API | Current primary provider in health check |
| GitHub Models | external API | Configured provider/fallback |
| PostgreSQL | port `5432` | Main production DB |
| Redis | port `6379` | Runtime/cache/queue support |
| Media Processor | port `8090` | OCR/STT/PDF processing support |
| LocationWhere backend | PM2, port `8310` | Location/SMS gateway companion backend |
| SMSGateway Android app | Android project under `/home/azim/smsgateway` | Phone-side SMS receive/send bridge to LocationWhere backend |

### 2.2 Public route map

| Domain | Public route | Backend |
|---|---|---|
| `https://fazle.iamazim.com/` | Dashboard and app pages | `127.0.0.1:8200` |
| `https://fazle.iamazim.com/webhook/meta` | Meta WhatsApp/Facebook webhook | `127.0.0.1:8200/webhook/meta` |
| `https://fazle.iamazim.com/api/fazle/...` | Fazle API prefix | `127.0.0.1:8200/...` |
| `https://fazle.iamazim.com/api/wa/...` | WhatsApp chat frontend API | `127.0.0.1:8200/api/wa/...` |
| `https://fazle.iamazim.com/api/wa/stream` | SSE stream | `127.0.0.1:8200/api/wa/stream` |
| `https://locationwhere.iamazim.com/` | LocationWhere admin SPA | static `/home/azim/location_where/admin-dashboard/dist` |
| `https://locationwhere.iamazim.com/api/...` | LocationWhere backend API | `127.0.0.1:8310/api/...` |
| `https://locationwhere.iamazim.com/downloads/...` | APK/static downloads | `/var/www/locationwhere.iamazim.com/downloads` |

### 2.3 Main frontend pages in Fazle Core

| Local file | Route | Purpose |
|---|---|---|
| `app/static/dashboard.html` | `/`, `/dashboard` | Operational overview |
| `app/static/payroll.html` | `/payroll`, `/payroll/{tab}` | Payroll/FPE dashboard |
| `app/static/escort-roster.html` | `/escort-roster` | Escort program/roster operations |
| `app/static/drafts.html` | `/drafts` | Draft approvals |
| `app/static/kb.html` | `/kb` | KB upload/list/stats UI |
| `app/static/wa_chat.html` | `/wa-chat`, `/dashboard/wa-chat` | WhatsApp-style admin chat |
| `app/static/open-chat.html` | likely chat/test page | AI/open chat UI |

---

## 3. App Folder Tree and Ownership

High-level tree:

```text
/home/azim/core
├── app/                    # FastAPI app, config, DB, bridge clients, LLM providers, static pages
├── modules/                # Business and integration modules
├── shared/                 # Runtime gateway, queue, realtime, write router, common policy
├── knowledge_base/         # Intended canonical system brain
├── resources/              # Current runtime RAG file corpus
├── db/migrations/          # Main schema migrations
├── migrations/             # Additional standalone schema files
├── tests/                  # Unit/integration/e2e/load/resilience tests
├── scripts/                # Maintenance, RAG rebuild, test helpers
├── store/qdrant/           # Local vector store path when embedded Qdrant is used
├── docs/                   # Older docs
└── app/static/             # HTML dashboards
```

Important companion folders outside core:

```text
/home/azim/whatsapp1         # Bridge 1 runtime store
/home/azim/whatsapp2         # Bridge 2 runtime store
/home/azim/whatsapp-mcp      # WhatsApp bridge binary/runtime
/home/azim/location_where    # LocationWhere Android + backend + admin dashboard
/home/azim/smsgateway        # Android SMS gateway app
/home/azim/agent             # System agent
/home/azim/fazle-payroll-engine # External/older FPE package copy
```

---

## 4. Major Features and Subsystems

### 4.1 Messaging and routing

**Goal:** Accept inbound messages from Meta WhatsApp, Facebook Messenger/comments, and WhatsApp bridges; save them; classify; reply/draft/escalate safely.

Key modules:

- `app/main.py`
  - `/webhook/meta`
  - `/webhook/mcp1`
  - `/webhook/mcp2`
  - `/send/meta`, `/send/mcp1`, `/send/mcp2`
  - health/admin/dashboard routes
- `modules/bridge_poller`
  - SQLite read-only polling from bridge DBs
  - LID to phone resolution
  - inbound dedup
  - outgoing `[RELEASE CONFIRMED]` scan
  - image OCR path for escort slips
- `modules/message_router`
  - identity-aware message routing
  - safe auto-send vs draft logic
  - recruitment/attendance/payment/admin command dispatch
- `modules/outbound`
  - persistent outbound queue
  - retry/DLQ
  - bridge circuit breaker
  - multi-channel send
- `modules/social_auto_reply`
  - social inbox events
  - Facebook/Messenger/Meta reply queues
  - salary/recruitment/social flows
- `modules/wa_chat_frontend`
  - WhatsApp-like admin UI API
  - contact list, messages, drafts, groups, settings, SSE

Sub-features:

- Bridge 1/2 health diagnostics
- Reply cooldown
- Prompt-injection and poison filtering
- Draft quality gate
- Draft approval and reviewed reply memory
- Social daemon single-engine routing
- Admin manual override

### 4.2 Knowledge and AI

Current reality:

- `knowledge_base/` is the intended human/developer/system brain.
- Runtime RAG currently indexes:
  - `resources/*.txt`
  - active rows in `fazle_knowledge_base`
- Runtime RAG does **not** automatically index every markdown file under `knowledge_base/`.

Key modules:

- `modules/rag`
  - BM25 search
  - optional hybrid Qdrant search
  - role-aware category filtering scaffold
  - `build_index`, `search`, `answer`, `stats`, `rebuild_index`
- `modules/knowledge_base`
  - direct DB KB lookup
  - recruitment reply lookup
- `modules/kb_upload`
  - upload TXT/MD/PDF/DOCX/CSV
  - extract/chunk
  - insert into `fazle_knowledge_base`
  - save text copy under `resources`
  - schedule RAG rebuild
- `app/llm.py`
  - provider chain
  - memory logging
- `app/ollama.py`
  - local Ollama generation/classification
  - serialized requests
  - prompt policy via `shared.reply_policy`

Sub-features to complete:

- Canonical `knowledge_base/` ingestion into runtime RAG
- KB versioning and certification
- Traceability from every module/function/table to KB article
- Ollama read-only database access layer
- Ollama-owned memory database
- Admin UI to inspect KB coverage and stale mappings

### 4.3 Recruitment

Key modules:

- `modules/recruitment_flow`
- `modules/recruitment_ai`
- `modules/conversation_layer`
- `modules/intent`
- `modules/message_router`
- `modules/social_auto_reply`

Sub-features:

- Candidate intake flow
- Age validation
- Role/source safety gates
- Facebook comment redirect/recruitment reply
- Conversation memory and safe recruitment responses

### 4.4 Payroll, payment, cash, and accountant workflows

Key modules:

- `modules/fazle_payroll_engine`
- `modules/payment_workflow`
- `modules/payment_correction`
- `modules/payment_ingest`
- `modules/payroll`
- `modules/payroll_logic`
- `modules/accountant_summary`
- `modules/admin_transactions`
- `modules/admin_commands`

Sub-features:

- Payment SMS parsing
- Cash/Income command parsing
- FPE immutable processing state
- Payment drafts
- PAID/ADVANCE/REVERSE/ADJUST commands
- Payroll compute/submit/approve/lock/paid/cancel
- Accountant summary
- Reconciliation and gap scans

### 4.5 Escort, attendance, OCR, and roster

Key modules:

- `modules/escort`
- `modules/escort_lifecycle`
- `modules/escort_roster`
- `modules/escort_slip_extractor`
- `modules/ocr_processor`
- `modules/attendance`
- `modules/attendance_parser`

Sub-features:

- Client order parsing
- Escort assignment and confirmation
- Release slip OCR
- TWO-DATE release detection
- Release confirmation by admin outbound message
- Attendance creation
- Escort payment draft creation
- Stale escort reminder

### 4.6 Identity, permissions, contacts

Key modules:

- `modules/identity_brain`
- `modules/role_classifier`
- `modules/rbac`
- `modules/contact_roles`
- `modules/contact_sync`
- `modules/number_identity`
- `modules/phone_normalizer`
- `modules/user_role`

Sub-features:

- Phone normalization
- Contact sync from bridge messages
- Role and risk classification
- Admin/user API keys
- Runtime auto-reply settings
- Draft-always role/name/phone gates

### 4.7 Operations and reliability

Key modules:

- `modules/scheduler`
- `modules/backup`
- `modules/observability`
- `shared/runtime_gateway`
- `shared/queue_arbiter`
- `shared/bridge_orchestrator`
- `shared/self_heal`
- `shared/frontend_sync`
- `shared/realtime`

Sub-features:

- Daily DB backup
- Backup staleness alert
- Bridge watchdog
- RAG rebuild schedule
- DLQ alert
- Health summary
- Runtime node registry
- Queue arbitration
- Self-healing diagnostics
- Metrics and error summary

---

## 5. What Has Already Been Achieved

Current confirmed achievements:

- Fazle Core service is running and health endpoint reports ok.
- PostgreSQL, Redis, Media Processor, Ollama, Groq, GitHub Models are connected.
- WhatsApp bridge 1 and bridge 2 are running, receiving messages, and send-control is allowed.
- Bridge pollers are fresh and healthy.
- Outbound queue is enabled and currently has no pending/DLQ items.
- Meta WhatsApp Cloud API number is connected with GREEN quality.
- Facebook Page identity resolves and token is valid, although subscription verification needs a Page-access-token-specific check.
- LocationWhere backend is online and SMS gateway API contract works with configured secret.
- `knowledge_base/` has a mature role-centric structure and many developer system articles.
- Important post-audit articles exist:
  - `06_developer_system/bridge_poller.md`
  - `06_developer_system/outbound_delivery.md`
  - `06_developer_system/escort_slip_extractor.md`
  - `06_developer_system/rag_strategy.md`
  - `06_developer_system/database_rules.md`
  - `06_developer_system/fpe_overview.md`
  - `06_developer_system/social_auto_reply_system.md`
- RAG runtime has 251 docs indexed at last check: 87 file chunks and 164 DB chunks.
- Frontend dashboard routes exist for dashboard, payroll, escort roster, drafts, KB, and WhatsApp chat.

---

## 6. Main Gaps

### G1. `knowledge_base/` is not yet the runtime brain

Problem:

- The master KB folder is documented but runtime RAG indexes `resources/*.txt` and `fazle_knowledge_base`, not the full `knowledge_base/**/*.md`.

Required outcome:

- Every approved KB article should become queryable by the AI through a controlled ingestion pipeline.
- Runtime RAG must know source path, article type, visibility, role, revision, and certification status.

### G2. Ollama cannot read operational DBs safely

Problem:

- Ollama is only an LLM server. It does not directly read payment, message, contact, escort, attendance, payroll, or recruitment DB tables.
- Letting a model directly access production DB credentials would be unsafe.

Required outcome:

- Build a read-only tool/API layer that exposes approved SQL views or read-only query endpoints.
- Ollama/agent may read only through that layer.
- No write, update, delete, migration, or DDL access to production DB.

### G3. Ollama needs its own writable memory DB

Problem:

- Existing `llm_learning_memory`, `llm_conversation_log`, `user_memory` exist in production DB, but the user wants a separate model-owned DB.

Required outcome:

- Create a separate Ollama memory database/schema where the AI may create/alter/drop its own memory tables.
- This DB must not be the production Fazle DB.
- Production DB is read-only to the AI; Ollama memory DB is writable by the AI service only.

### G4. KB/code mismatch needs systematic cleanup

Problem:

- Some modules are active, some partial, some duplicate, some stale.
- Some docs report gaps that have since been filled.
- Some legacy folders duplicate canonical folders.

Required outcome:

- Build a module-to-KB traceability matrix.
- Mark each module as active, dormant, duplicate, deprecated, unsafe, or candidate-delete.
- Do not delete until tests and owner approval pass.

### G5. Facebook Page connection is partly verified but webhook subscription is not fully proven

Problem:

- Page identity and token scopes are valid.
- `/subscribed_apps` check failed because the token behaved as a system-user token, not accepted as Page token for that endpoint.

Required outcome:

- Verify webhook app subscription with a correct Page access token or Business app-level endpoint.
- Do not change subscriptions without explicit owner approval.

### G6. Environment shell compatibility

Problem:

- `.env` is valid for Python dotenv/Pydantic, but shell `source .env` can fail on unquoted values with spaces.

Required outcome:

- Any shell script must use a robust dotenv parser or quote known problematic values.
- Do not rely on `source .env` unless the file is shell-safe.

---

## 7. Target Architecture

### 7.1 Knowledge Brain Architecture

```text
knowledge_base/**/*.md
        ↓
KB linter + metadata validator
        ↓
Canonical KB manifest
        ↓
Ingestion pipeline
        ├── fazle_knowledge_base rows
        ├── resources/generated_kb/*.txt
        └── vector/BM25 index
        ↓
RAG / Hybrid Search
        ↓
LLM prompt context
        ↓
Draft quality gate + role visibility gate
        ↓
Reply / draft / admin answer
```

### 7.2 Ollama Read-Only Operational Insight Architecture

```text
Ollama / Agent
   ↓ natural language question
Read-only query planner
   ↓
Allowed query catalog / SQL views only
   ↓
Production DB read-only role
   ↓
Safe summarized result
   ↓
Ollama answer with citations/table names/timestamps
```

Rules:

- AI never receives production DB superuser or app write credentials.
- AI never executes arbitrary SQL against production.
- AI can call named tools like:
  - `get_contact_summary(phone)`
  - `get_recent_messages(phone, limit)`
  - `get_payment_summary(employee_id/month)`
  - `get_escort_program_status(program_id/phone)`
  - `get_attendance_summary(employee_id/month)`
  - `get_payroll_run_status(month)`
- Each tool maps to reviewed SELECT-only SQL.

### 7.3 Ollama Memory DB Architecture

Recommended implementation:

- Database: `fazle_ollama_memory`
- Role: `ollama_memory_owner`
- Permission:
  - Full DDL/DML only on `fazle_ollama_memory`
  - No permission on production `fazle` database
- Optional read-only role for production:
  - `fazle_ai_reader`
  - SELECT only on approved views, not raw tables initially

Suggested initial memory tables:

```sql
ai_memory_facts(
  id bigserial primary key,
  subject_type text not null,
  subject_key text not null,
  fact_type text not null,
  fact_text text not null,
  confidence numeric(4,2) default 0.70,
  source_ref text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

ai_memory_questions(
  id bigserial primary key,
  question text not null,
  normalized_question text,
  answer_summary text,
  source_refs jsonb default '[]',
  created_at timestamptz default now()
);

ai_memory_tasks(
  id bigserial primary key,
  task_name text not null,
  status text not null default 'open',
  notes text,
  source_refs jsonb default '[]',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

ai_kb_embeddings_manifest(
  id bigserial primary key,
  kb_path text not null,
  kb_hash text not null,
  indexed_at timestamptz default now(),
  chunk_count int default 0,
  status text default 'indexed'
);
```

The AI may add/alter/drop tables inside its own memory DB after approval of this architecture, but must never alter production DB.

---

## 8. Detailed Implementation Plan

### Phase 0 — Freeze and Baseline

Objective: Capture current state before any refactor.

Tasks:

1. Confirm branch and remote:
   - `git -C /home/azim/core status --short --branch`
   - expected branch: `backup/vps-core-20260612`
2. Export safe inventories:
   - modules list
   - routes list
   - env key presence without values
   - systemd/PM2 service list
   - nginx route map
   - RAG stats
   - DB table inventory
3. Create a dated audit folder:
   - `reports/kb_brain_baseline_YYYYMMDD/`
4. Run read-only health checks:
   - `/health`
   - `/admin/overview`
   - `/api/bridges/diagnostics`
   - `/rag/stats`
   - LocationWhere `/health`
   - SMS gateway `/api/v1/gateway/status`
5. Take backups:
   - Fazle PostgreSQL dump
   - current `knowledge_base/`
   - current `resources/`
   - current `.env` copied to secure backup location only, never committed

Exit criteria:

- Baseline report exists.
- Backups verified.
- No write operations done except report files.

### Phase 1 — Knowledge Base Structural Audit

Objective: Ensure KB is clean, canonical, non-duplicative, and role-aware.

Tasks:

1. Build KB inventory:
   - path
   - title
   - article type
   - visibility
   - source module/table
   - last updated date
   - certification status
2. Identify duplicate folders:
   - `03_developer_system` vs `06_developer_system`
   - `02_admin_system` vs `02_admin_knowledge`
   - archived reports vs active canonical docs
3. Define canonical folder policy:
   - `00_governance`
   - `01_employee_knowledge`
   - `02_admin_knowledge`
   - `03_ai_identity`
   - `04_business_rules`
   - `05_workflows`
   - `06_developer_system`
   - `07_archived`
4. Add required front matter to every active KB file:

```yaml
---
kb_id: DEV-BRIDGE-POLLER
title: Bridge Poller
status: active
visibility: developer
source_modules:
  - modules/bridge_poller/__init__.py
source_tables:
  - bridge_poller_cursor
  - processed_bridge_messages
runtime_index: true
last_verified: 2026-06-24
---
```

5. Mark non-runtime docs:
   - audit reports
   - historical reports
   - old completion reports
   - board-ready summaries
6. Create `knowledge_base/00_governance/kb_manifest.json` or `.yaml`.

Exit criteria:

- Every active KB file has metadata.
- Every archived/report file is marked `runtime_index: false`.
- Duplicate folders have a consolidation plan.

### Phase 2 — KB Completeness and Quality Review

Objective: Determine whether KB fully represents the app.

Tasks:

1. Compare active modules against KB articles.
2. Compare DB tables/views against `database_rules.md`.
3. Compare frontend pages and API routes against developer docs.
4. Compare scheduler jobs against automation docs.
5. Compare nginx/public routes against operations docs.
6. Compare env flags against `runtime_gateway_flags.md`.
7. Compare tests against documented workflows.
8. Produce gap matrix:

| Gap ID | Domain | Missing/incorrect KB | Production evidence | Risk | Fix file |
|---|---|---|---|---|---|

Important domains to verify:

- Bridge receive/send
- Meta WhatsApp
- Facebook Page/Messenger/comments
- Recruitment
- Attendance
- Escort order/release/payment
- FPE/payroll/accountant
- Admin commands
- Draft approval
- Contact roles and identity
- RAG and KB upload
- Backup/scheduler/observability
- LocationWhere/SMSGateway integration

Exit criteria:

- New coverage score exists.
- All P0/P1 systems have KB coverage above 90%.
- Stale reports are archived or clearly superseded.

### Phase 3 — Runtime KB Ingestion

Objective: Make `knowledge_base/` the actual runtime brain.

Recommended approach:

- Do not directly index every file blindly.
- Add a controlled ingestion script:
  - `scripts/sync_knowledge_base_to_runtime.py`

Required behavior:

1. Read only active KB files with `runtime_index: true`.
2. Skip:
   - `07_archived`
   - old reports
   - files with `visibility: developer` unless admin/developer search mode
   - files marked `runtime_index: false`
3. Chunk content with metadata.
4. Insert/update rows in `fazle_knowledge_base` using stable keys:
   - `kb:{kb_id}:chunk:{n}`
5. Save generated text snapshots to:
   - `resources/generated_kb/`
6. Trigger RAG rebuild.
7. Write manifest:
   - `reports/kb_sync_manifest_YYYYMMDD.json`

Proposed DB columns if existing table supports them:

- Use existing columns first: `category`, `subcategory`, `key`, `value`, `reply_text`, `tags`, `is_active`.
- Do not add columns in this phase unless a migration plan is approved.

Exit criteria:

- `/rag/stats` shows KB-derived chunks.
- Search queries can cite KB path/key.
- Employee/candidate-safe answers do not expose developer-only content.

### Phase 4 — Read-Only Operational Data Access for AI

Objective: Let Ollama answer operational questions using production data without write access.

Tasks:

1. Create PostgreSQL role:
   - `fazle_ai_reader`
2. Create approved views or functions:
   - `ai_read_contacts`
   - `ai_read_recent_messages`
   - `ai_read_payment_summary`
   - `ai_read_escort_programs`
   - `ai_read_attendance_summary`
   - `ai_read_payroll_runs`
   - `ai_read_recruitment_leads`
3. Grant SELECT only:
   - no INSERT
   - no UPDATE
   - no DELETE
   - no TRUNCATE
   - no CREATE
   - no ALTER
   - no DROP
4. Build API/tool layer:
   - `modules/ai_readonly_tools`
   - endpoints under `/api/ai/read/...` with internal API auth
5. Add query audit table if allowed:
   - if production table changes are not allowed, log to Ollama memory DB instead.
6. Add row limits and privacy filters:
   - default limit 20
   - max limit 100
   - mask sensitive fields unless admin role
   - no raw token/env/secret exposure

Exit criteria:

- Ollama can answer "এই নাম্বারের শেষ ১০ মেসেজ দেখাও" via read-only tool.
- Ollama can answer payment/escort/payroll status questions.
- Attempted write query fails.

### Phase 5 — Ollama Memory DB

Objective: Give Ollama an independent writable memory.

Tasks:

1. Create separate DB:
   - `fazle_ollama_memory`
2. Create owner role:
   - `ollama_memory_owner`
3. Store credentials outside repo:
   - systemd env or secure env backup only
4. Implement module:
   - `modules/ollama_memory`
5. APIs:
   - `remember_fact(subject_type, subject_key, fact_type, fact_text, source_ref)`
   - `recall_facts(subject_type, subject_key)`
   - `record_question(question, answer, source_refs)`
   - `list_memory_stats()`
6. Add memory policy KB article:
   - `knowledge_base/06_developer_system/ollama_memory.md`
7. Add privacy/retention policy:
   - which facts may be stored
   - who can delete memory
   - how to export memory

Exit criteria:

- AI can write only to `fazle_ollama_memory`.
- AI cannot write to production DB.
- Memory can be backed up and restored separately.

### Phase 6 — Module and Code Alignment

Objective: Find modules/code that do not match the KB and decide fix/archive/delete.

Tasks:

1. Build module inventory:

| Module | Imported by | Runtime active? | KB article | Tests | Status |
|---|---|---|---|---|---|

2. Status labels:
   - `active`
   - `active-underdocumented`
   - `dormant`
   - `duplicate`
   - `legacy`
   - `unsafe`
   - `delete-candidate`
3. Use evidence:
   - imports
   - route inclusion
   - scheduler calls
   - tests
   - logs
   - DB tables used
4. For mismatch, choose:
   - update KB
   - update code
   - mark deprecated
   - move to `archive/deprecated`
   - delete after approval
5. Required delete checklist:
   - not imported
   - not referenced by routes/scheduler/tests/scripts
   - no active DB table depends on it
   - no KB article marks it active
   - test suite passes after removal
   - owner approves exact file list

Exit criteria:

- `reports/module_alignment_matrix.md` exists.
- No module is undocumented.
- Delete candidates are listed but not removed until approved.

### Phase 7 — Frontend and Admin UX

Objective: Give admins visibility into KB brain, model memory, and read-only operational answers.

Required UI additions:

1. `/kb`
   - KB coverage dashboard
   - active vs archived article count
   - ingestion status
   - stale article warnings
   - sync/rebuild button
2. `/chat` or `/open-chat`
   - Ask operational questions
   - Show citations:
     - KB path
     - DB view/tool name
     - timestamp
   - Show read-only badge
3. `/observability`
   - RAG stats
   - Ollama memory stats
   - AI read-only query count
4. `/wa-chat`
   - Keep current send/receive and draft controls unchanged
   - Add KB-cited suggested replies only after safety review

Frontend constraints:

- Do not make developer-only KB visible to employees/candidates.
- Do not show secrets.
- Do not expose raw unrestricted SQL.
- Keep WhatsApp bridge send controls stable.

Exit criteria:

- Admin can see whether the model is answering from KB, DB view, or memory.
- Admin can trigger KB sync/reindex safely.

### Phase 8 — Tests and Certification

Objective: Prevent regressions.

Required tests:

- KB parser metadata tests
- KB ingestion tests
- RAG role visibility tests
- AI read-only DB permission tests
- Ollama memory write/read tests
- No-production-write tests
- Bridge send/receive smoke tests
- Meta webhook verification tests
- LocationWhere SMS gateway contract tests
- Dashboard route smoke tests

Commands to run before merge:

```bash
pytest tests/unit
pytest tests/integration
pytest tests/db
pytest tests/workflows
```

If full suite is too slow, minimum gate:

```bash
pytest tests/unit/test_phase13d_bridge_orchestrator.py
pytest tests/unit/test_phase13a_gateway.py
pytest tests/unit/test_draft_reply.py
pytest tests/unit/test_payment_workflow.py
pytest tests/db/test_db_consistency.py
```

Exit criteria:

- KB certification report says P0/P1 above 90%.
- No WhatsApp bridge regression.
- No DB schema drift unless explicitly approved.

---

## 9. Canonical KB Coverage Targets

Target scores:

| Area | Target |
|---|---|
| P0 modules | 95% |
| P1 modules | 90% |
| Business rules | 95% |
| Workflows | 95% |
| DB behavior | 95% |
| Frontend/API routes | 90% |
| Env/runtime flags | 95% |
| Assistant integrations | 90% |
| Tests mapped to workflows | 85% |

P0 modules:

- `app/main.py`
- `modules/message_router`
- `modules/bridge_poller`
- `modules/outbound`
- `modules/rag`
- `modules/knowledge_base`
- `modules/identity_brain`
- `modules/payment_workflow`
- `modules/fazle_payroll_engine`
- `modules/escort_lifecycle`
- `modules/social_auto_reply`
- `modules/wa_chat_frontend`

---

## 10. Database Access Policy

### Production DB

Allowed for AI:

- SELECT through approved views/tools.
- Aggregated summaries.
- Limited recent records.
- Masked personally sensitive values unless admin asks and is authorized.

Forbidden for AI:

- INSERT/UPDATE/DELETE/TRUNCATE on production.
- CREATE/ALTER/DROP on production.
- Reading env/secrets/tokens.
- Reading raw bridge SQLite DB directly unless through approved read-only service.
- Modifying WhatsApp bridge DB.

### Ollama Memory DB

Allowed for AI:

- CREATE/ALTER/DROP tables in its own DB only, if the memory role owns that DB.
- INSERT/UPDATE/DELETE its memory rows.
- Build its own indexes.
- Store summaries, facts, question history, and KB embedding manifests.

Forbidden:

- Copying secret values.
- Storing raw access tokens.
- Storing unnecessary sensitive personal info without policy.

---

## 11. Recommended New Files

Create these files during implementation:

```text
knowledge_base/06_developer_system/ollama_memory.md
knowledge_base/06_developer_system/ai_readonly_data_access.md
knowledge_base/06_developer_system/kb_runtime_ingestion.md
knowledge_base/00_governance/kb_manifest.yaml
knowledge_base/00_governance/kb_certification_report_YYYYMMDD.md
scripts/audit_kb_structure.py
scripts/sync_knowledge_base_to_runtime.py
scripts/audit_module_kb_alignment.py
modules/ai_readonly_tools/__init__.py
modules/ollama_memory/__init__.py
tests/unit/test_kb_manifest.py
tests/unit/test_ai_readonly_tools.py
tests/unit/test_ollama_memory.py
```

---

## 12. Implementation Guardrails for Any Agent

Before editing code:

1. Read this file.
2. Read `knowledge_base/README.md`.
3. Read relevant `knowledge_base/06_developer_system/*.md`.
4. Confirm current branch.
5. Run health checks.
6. Make a backup if touching DB-related logic.

Before deleting anything:

1. Produce exact file list.
2. Show import/reference evidence.
3. Run tests with the file still present.
4. Move to archive first if uncertain.
5. Run tests after archive/remove.
6. Get owner approval for final deletion.

Never do these without explicit approval:

- `git reset --hard`
- altering production DB tables/columns
- deleting bridge stores
- restarting WhatsApp bridges during business hours
- changing Meta/Facebook webhook subscriptions
- printing tokens/secrets

---

## 13. Suggested Development Order

Recommended order for the next agent:

1. Create KB manifest and metadata validator.
2. Run KB structural audit and produce a new coverage report.
3. Implement `sync_knowledge_base_to_runtime.py`.
4. Sync only a small safe subset first, then rebuild RAG.
5. Add read-only DB views/tool layer.
6. Add Ollama memory DB.
7. Add admin UI status panels.
8. Build module-to-KB alignment matrix.
9. Identify delete candidates, but do not delete yet.
10. Run certification and tests.

---

## 14. Definition of Done

This plan is complete when:

- `knowledge_base/` is the canonical and runtime-ingested brain.
- RAG answers cite KB article paths and DB read-only tools.
- Ollama has a separate writable memory DB.
- Ollama can read operational production data only through approved read-only views/tools.
- Every active module has a KB article and test mapping.
- Stale/duplicate modules are either fixed, archived, or approved for deletion.
- Frontend pages show KB/RAG/model health clearly.
- WhatsApp bridge 1 and 2 continue to send and receive messages.
- No production DB schema or bridge DB data is changed accidentally.

---

## 15. Current Open Decisions for Owner

1. Should legacy `03_developer_system/` be archived after its content is merged into `06_developer_system/`?
2. Should `knowledge_base/07_archived` be excluded from all runtime AI indexing permanently? Recommended: yes.
3. Should Ollama memory DB be a separate PostgreSQL database or a separate SQLite/Qdrant bundle? Recommended: separate PostgreSQL database `fazle_ollama_memory`.
4. Should production read-only AI access start with SQL views or Python API tools? Recommended: Python API tools over approved SQL views.
5. Should Facebook Page webhook subscription be changed if current app subscription is missing? Recommended: verify first, mutate only after owner approval.

