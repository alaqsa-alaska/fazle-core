# Fazle Core

**Repository:** <https://github.com/arshiyaazim/fazle-core>
**Branch:** `feat/bridge1-2-conversation-continuity-20260902`
**Commit:** `104df66c18504a7e946b8f6208d5045d5d3d9e66`
**Documentation date:** 2026-10-03

Fazle Core is a Python/FastAPI operations backend for a WhatsApp-first security,
recruitment, payroll, and vessel-escort business. It integrates WhatsApp and
Meta message channels with operator dashboards, PostgreSQL-backed workflows,
and AI-assisted search and reply drafting. It is company-specific software,
not a general chatbot platform.

This repository contains the application and its integrations. It does **not**
bundle or install production databases, WhatsApp bridge binaries, AI gateways,
model weights, or other separately operated services.

---

## 1. Project introduction

### What the application does

- Ingests messages from external WhatsApp bridges and Meta webhooks, resolves
  sender identity, and routes work to recruitment, escort, payment, attendance,
  and admin workflows.
- Supports the Fazle Payroll Engine (FPE), employee lookup, payment and income
  tracking, reconciliation, and review queues. `fpe_cash_transactions` is the
  canonical cash ledger; `wbom_cash_transactions` is legacy and must not receive
  new writes.
- Provides escort intake, roster and dispatch-related views, slip review,
  payment drafts, and client-billing profile/payment-tracking workflows.
- Serves operator dashboards, reply and payment-draft review, knowledge-base
  management, and the AI chat console.
- Provides retrieval (BM25/RAG), read-oriented business tools, scheduled jobs,
  backups, health checks, and observability endpoints.

The application supports pieces of these business domains; it is not a
double-entry general ledger or a complete ERP. Financial and outbound actions
must continue to follow the code's authorization and review paths.

### Current messaging architecture

```
WhatsApp (Meta)
    │
    ├── webhook (Meta HTTPS) ──► fazle-core :8200
    └── bridge polling ──► Bridge1/Bridge2/Bridge3 SQLite stores
            │
            ▼
    message_router.process_message()
            │
            ├── identity/role resolution (identity_brain, identity_resolver)
            ├── context/history lookup
            ├── domain routing
            │       ├── admin_commands
            │       ├── escort / escort_lifecycle
            │       ├── recruitment_flow
            │       ├── payment_workflow
            │       ├── attendance
            │       └── rag (KB search)
            │
            ├── AI/provider fallback
            │       ├── OmniRoute (primary)
            │       ├── Groq (disabled)
            │       ├── local Ollama / Hermes-family model
            │       └── Kimi (disabled)
            │
            └── outbound (draft/approved/sent flow)
                    ├── DRAFT_CREATION_ENABLED = true
                    ├── OUTBOUND_ENABLED = false (SAFE MODE)
                    └── queue + retry + DLQ
```

### Production safety status

In the current working tree (`/home/azim/core/.env`):
- `OUTBOUND_ENABLED=false` — no real customer WhatsApp sends are issued.
- `AUTO_REPLY_ENABLED=false` — customer-facing auto-reply is suppressed.
- `RECRUITMENT_AUTOREPLY_ENABLED=false` — the recruitment candidate-intake
  bypass is also off.
- `PAYMENT_RELAY_ENABLED=true` — the owner→accountant payment relay is active
  and **FROZEN** (see Section 15).

Drafts are still created for designated roles (`DRAFT_CREATION_ENABLED=true`);
they require explicit admin `APPROVE` before any send occurs.

---

## 2. Repository history and feature summary

### Integration history

| Date / commit range | Summary |
|---|---|
| 2026-06-13 | Atomic release and outbound queue (`018_atomic_release_and_outbound.sql`). |
| 2026-06-29 | FPE canonical cash ledger established; `wbom_cash_transactions` deprecated. |
| 2026-07-09 | Client billing profiles (`034_client_billing_profiles.sql`) applied. |
| 2026-08-05 | Reply chain reordered to OmniRoute → Groq → Ollama/Hermes → Kimi. GitHub Models removed. |
| 2026-08-12 | Hermes customer-dispatch Phase 1 (identity-scoped context, no MCP tools). |
| 2026-08-13 | Bridge1 reliability hardening; conversation continuity for Bridge1/2. |
| 2026-08-31 | Production system status document records live/shadow/off/deferred states. |
| 2026-09-02 | Bridge3 repair; Bridge1/2 conversation continuity deployed. |
| 2026-10-02 | Identity provenance followup (`a861c99`); provider-disable reconciliation (`104df66`). |

### Identity brain, resolver, and provenance

- `modules/identity_brain/` — `detect_identity()` is the canonical role/trust
  resolution engine.
- `modules/identity_resolver.py` — single shared inbound-identity helper; all
  entry points (bridge poller, webhooks, MCP/Hermes read tools) flow through it
  and receive one authoritative `identity_phone`.
- `modules/group_identity.py` — reads group metadata from the bridge SQLite
  `chats` table; mirrors into `wbom_group_identities`. **Not wired into the
  poll loop.** Messages remain read/index-only until a separate analysis
  workflow explicitly consumes them.
- Provenance tracking: inbound processing and outbound queue metadata carry
  sender/group/account/reply provenance. `modules/conversation_memory/` tracks
  `duplicate_replay` exclusion actions.

### Employee and recruitment conversation

- `modules/recruitment_flow/`, `modules/recruitment_ai/`,
  `modules/recruitment_detector/`, `modules/recruitment_turn_decision/` —
  candidate intake, session lifecycle, AI-assisted reply.
- Recruitment fact authority is governed by
  `knowledge_base/00_governance/recruitment_fact_authority.json`.
- Conflicting/outdated recruitment facts are archived to
  `knowledge_base/07_archived/recruitment_fact_conflicts.json`.
- Approved salary facts remain answerable; unresolved facts fail closed.

### Knowledge base and authoritative/archived separation

- `knowledge_base/00_governance/` — policy, authority matrix, manifest.
- `knowledge_base/01_employee_knowledge/` — employee/candidate-safe knowledge.
- `knowledge_base/02_admin_knowledge/` — admin/HR/operations knowledge.
- `knowledge_base/03_ai_identity/` — role mapping and permission matrix.
- `knowledge_base/04_business_rules/` — decision-engine rules.
- `knowledge_base/05_workflows/` — operational workflows.
- `knowledge_base/06_developer_system/` — RAG, parser, OCR, event pipeline.
- `knowledge_base/07_archived/` — legacy source rewrites, conflict archives.

### Vessel register read-only context

- `modules/vessel_register_context/` reads `wbom_escort_programs` records for
  operational context. It does not call the Site, write records, enqueue
  WhatsApp messages, post a ledger transaction, or treat a draft preview as
  approved/sent. Ambiguous vessel matches are omitted.

### Admin relay, message replay, and payment/attendance context

- `modules/admin_commands/` — parses and executes admin WhatsApp commands
  (`APPROVE`, `REJECT`, `EDIT`, `PAID`, `ADVANCE`, `STATUS`, `DRAFTS`, etc.).
  `APPROVE`/`PAID`/`ESCORTCONFIRM` deliver real WhatsApp messages via the
  bridge.
- `modules/payment_relay/` — deterministic, LLM-free owner→accountant
  payment-message relay. **FROZEN.** Only activates for bridge2, sender
  `8801XXXXXXX`, destination `8801XXXXXXX`.
- `modules/payment_relay_accounting/` — books `fpe_cash_transactions` on
  outbound send success. **FROZEN.**
- `modules/admin_directives/router.py` — admin directive CRUD and revocation.
- `modules/outbound/__init__.py` — persistent outbound queue with idempotency
  dedup, retry with exponential backoff, and DLQ. Captures authorization
  snapshots at enqueue.

### Group discovery and safe persistence

- `modules/group_identity.py` — discovers groups from the bridge SQLite `chats`
  table and mirrors JIDs into `wbom_group_identities`. Names are used only to
  recognize already-known groups. **Not wired into the poll loop.**

### Feature status summary

| Feature | Status |
|---|---|
| OmniRoute primary provider | Active |
| Groq fallback | Disabled (`GROQ_API_KEY=` empty) |
| Kimi fallback | Disabled (`KIMI_MODEL_NAME` set but not enabled) |
| MiniMax | Removed (absent from `.env.example` and code) |
| 9Router | Development-only (`NINE_ROUTER_SINGLE_UPSTREAM_GUARANTEED=false`) |
| Group AI analysis/summary | Deferred — messages remain read/index-only |
| Payment relay | **FROZEN** — active but locked |
| RAG hybrid search | Configurable (`HYBRID_SEARCH_ENABLED` toggle) |
| Conversation memory | Disabled (`CONVERSATION_MEMORY_ENABLED=false`) |
| Reviewed reply memory | Active (`REVIEWED_REPLY_MEMORY_ENABLED=true`) |
| Bridge media acquisition | Disabled (`BRIDGE_MEDIA_ACQUISITION_ENABLED=false`) |
| Hermes customer dispatch | Feature-flagged, not enabled by default |
| Agent proactive outbound | Off (`AGENT_PROACTIVE_OUTBOUND_ENABLED=false`) |

---

## 3. Repository structure

```
fazle-core/
├── app/                     # FastAPI application entry and shared clients
│   ├── main.py              # HTTP transport, webhooks, send APIs, HTML routes
│   ├── llm.py               # Unified LLM provider chain (route order OmniRoute → Groq → Ollama → Kimi; Groq/Kimi disabled)
│   ├── ollama.py            # Local Ollama client
│   ├── ollama_daemon.py     # Ollama daemon helpers (live model inspection)
│   ├── omniroute_provider.py # OmniRoute OpenAI-compatible client
│   ├── groq_provider.py     # Groq direct client
│   ├── nine_router_provider.py # 9Router bounded physical-model adapter (dev-only)
│   ├── unified_routing.py    # Phase 3B route registry and executor
│   ├── config.py            # Settings (Pydantic, env vars)
│   ├── database.py          # asyncpg pool and query helpers
│   ├── bridge.py            # Bridge1/Bridge2/Bridge3 send clients
│   ├── logging_setup.py     # Structured logging
│   └── static/              # Served HTML pages (FastAPI FileResponse)
│       ├── dashboard.html
│       ├── payroll.html
│       ├── escort-roster.html
│       ├── drafts.html
│       ├── kb.html
│       ├── chat-lab.html      (open-chat)
│       ├── wa_chat.html
│       ├── dispatch.html
│       ├── unmatched-confirmations.html
│       ├── wbom-create-link.html
│       ├── wbom-link-review.html
│       └── ...
├── modules/                 # Business logic modules (~86 submodules)
│   ├── message_router/      # Single source of truth for inbound routing
│   ├── identity_brain/      # Role/trust resolution engine
│   ├── identity_resolver.py # Canonical phone-based inbound identity resolver
│   ├── group_identity.py    # Bridge group chat discovery (read/index-only)
│   ├── admin_commands/      # Admin WhatsApp command parser and executor
│   ├── admin_directives/    # Admin directive CRUD + revocation
│   ├── outbound/            # Persistent outbound queue, retry, DLQ
│   ├── outbound_authorization/ # Authorization snapshot capture and revalidation
│   ├── payment_workflow/    # Escort payment draft and advance request
│   ├── payment_relay/       # Owner→Accountant relay (FROZEN)
│   ├── payment_relay_accounting/ # Relay accounting integration (FROZEN)
│   ├── payment_ingest/      # SMS/cash instruction parsing and FPE staging
│   ├── payroll/             # Monthly payroll batch engine
│   ├── fazle_payroll_engine/ # Canonical FPE subsystem (cash ledger + workers)
│   ├── escort/              # Escort order parsing and slip finalization
│   ├── escort_roster/       # Roster projection, dashboard, reconciliation
│   ├── escort_lifecycle/    # Escort lifecycle state machine
│   ├── recruitment_flow/    # Candidate intake and session management
│   ├── recruitment_ai/      # Recruitment AI assistance
│   ├── rag/                 # RAG/hybrid search (Qdrant + BM25)
│   ├── knowledge_base/      # DB-backed KB lookup + hardcoded fallback
│   ├── intent/              # Intent classifier
│   ├── hermes_tasks/        # Hermes admin task routing
│   ├── hermes_dispatch.py   # Hermes customer-dispatch helper (Phase 1)
│   ├── ai_route_registry.py # Admin route registry (Phase 3B)
│   ├── draft_approval/      # Draft approval workflow
│   ├── drafts/              # Draft CRUD API
│   ├── rbac/                # Role-based access control
│   ├── bridge_poller/       # Bridge1/2/3 SQLite pollers
│   ├── vessel_register_context/ # Vessel register read-only context
│   ├── location_adapter/    # LocationWhere integration (circuit breaker)
│   ├── media_normalization/ # Media/OCR/voice normalization
│   ├── voice_processor/     # Voice transcription pipeline
│   ├── ocr_processor/       # OCR pipeline
│   ├── scheduler/           # APScheduler (8 jobs)
│   ├── backup/              # Backup rotate/run
│   ├── health_monitor/      # System health probes
│   ├── observability/       # Prometheus metrics + error summary
│   ├── reports/             # Report generation
│   └── ...
├── shared/                  # Cross-cutting helpers
│   ├── reply_policy.py      # Reply policy + provider context package
│   ├── draft_reply.py       # Draft reply helpers
│   ├── queue.py             # Queue primitives
│   ├── queue_arbiter.py     # Queue arbitration
│   ├── identity_map.py      # Identity mapping helpers
│   ├── phone.py             # Phone normalization
│   ├── locks.py             # Distributed lock helpers
│   ├── auth_deps.py         # Auth dependency helpers
│   └── ...
├── config/                  # Configuration files
│   └── phase3_ai_routes.yaml # AI route registry (OmniRoute/Groq/Ollama/Kimi)
├── knowledge_base/          # Authoritative and archived knowledge
│   ├── 00_governance/       # Policy, authority matrix, manifest
│   ├── 01_employee_knowledge/
│   ├── 02_admin_knowledge/
│   ├── 02_admin_system/
│   ├── 03_ai_identity/
│   ├── 04_business_rules/
│   ├── 05_workflows/
│   ├── 06_developer_system/
│   └── 07_archived/         # Legacy and conflict archives
├── db/                      # Database migrations and migration runner
│   ├── migrations/          # Ordered .sql migration files
│   ├── migrate.py           # Migration runner (applies unapplied .sql files)
│   └── MIGRATION_INVENTORY.md
├── docs/                    # Project documentation
│   ├── README.md            # Documentation index
│   ├── ARCHITECTURE.md      # System diagram and data flow
│   ├── MODULE_MAP.md        # Module-by-module responsibility map
│   ├── OPERATIONS.md        # Production operations runbook
│   ├── DEPLOYMENT_RESTART_GATE.md # Pre-restart safety gate (manual script)
│   ├── DEVELOPER_GUIDE.md   # Runtime, frontend, testing details
│   ├── API.md               # HTTP endpoint reference
│   └── ...
├── tests/                   # Test suites
│   ├── unit/                # Fast unit tests (no I/O)
│   ├── integration/         # API integration tests (requires test DB)
│   ├── db/                  # Database consistency tests
│   ├── workflows/           # Workflow simulation tests
│   ├── resilience/          # Failure recovery tests
│   ├── e2e/                 # Playwright browser tests
│   ├── load/                # Locust load tests
│   ├── mocks/               # Shared test mocks
│   ├── fixtures/            # Test data
│   └── requirements-test.txt
├── scripts/                 # Operational and CI scripts
│   ├── run_ci.sh            # CI gate
│   ├── secret_scan.sh       # Secret pattern scan
│   ├── restart_gate.sh      # Pre-restart safety checks
│   ├── restart_gate_readiness.py # Restart readiness logic (unit-tested)
│   ├── create_hermes_mcp_admin.py # One-time Hermes admin identity helper
│   └── ...
├── resources/               # Static resource files (KB source .txt, CSV templates)
├── store/                   # Local data stores
│   └── qdrant/              # Embedded Qdrant fallback data
├── logs/                    # Application logs
├── requirements.txt         # Runtime dependencies
├── run.py                   # Uvicorn entrypoint
├── makefile                 # Test targets
├── pytest.ini               # Root pytest config
├── docker-compose.test.yml  # Test Docker stack
├── .env.example             # Environment template (safe defaults)
├── fazle-core.service       # systemd unit
├── ARCHITECTURE.md          # Supplementary technical reference
├── BUSINESS-CONSTITUTION.md # Financial/architectural invariants
├── BACKUP.md                # Backup procedures
├── RECOVERY.md              # Disaster recovery procedures
├── SYSTEM_ATLAS.md          # Canonical document map
└── README.md                # This file
```

Each module under `modules/` is responsible for one domain: message routing,
identity, payroll, escort, recruitment, AI/RAG, outbound, reporting, etc.
The exact module list and risk ratings are in `docs/MODULE_MAP.md`.

---

## 4. Installation guide

### A. VPS installation

#### Prerequisites

- Linux VPS (Ubuntu/Debian recommended).
- Python 3.10+ (3.11 recommended).
- PostgreSQL 15+ (the app uses `asyncpg`, no ORM).
- Redis (queue/runtime helpers).
- Optional: Qdrant (vector store for hybrid RAG), Ollama, OmniRoute,
  Hermes runner, WhatsApp bridges, media processor.
- Nginx terminating public TLS; Fazle Core bound to `127.0.0.1:8200`.
- Git, pip, virtualenv.

#### Steps

```bash
# 1. Clone as the unprivileged azim user
sudo -u azim git clone https://github.com/arshiyaazim/fazle-core.git /home/azim/core
cd /home/azim/core

# 2. Create virtualenv
sudo -u azim python3 -m venv /home/azim/.venv
sudo -u azim /home/azim/.venv/bin/pip install --upgrade pip
sudo -u azim /home/azim/.venv/bin/pip install -r requirements.txt

# 3. Environment file
sudo -u azim cp /home/azim/core/.env.example /home/azim/core/.env
sudo chmod 600 /home/azim/core/.env
# Edit .env with real DATABASE_URL, REDIS_URL, INTERNAL_API_KEY, etc.
# Do NOT set OUTBOUND_ENABLED=true until verified.

# 4. Migrations
sudo -u azim /home/azim/.venv/bin/python /home/azim/core/db/migrate.py --dry-run
sudo -u azim /home/azim/.venv/bin/python /home/azim/core/db/migrate.py

# 5. systemd service
sudo cp /home/azim/core/fazle-core.service /etc/systemd/system/fazle-core.service
sudo systemctl daemon-reload
sudo systemctl enable --now fazle-core.service

# 6. Health verification
curl -fsS http://127.0.0.1:8200/health
```

#### Log inspection

```bash
sudo journalctl -u fazle-core -n 200 -f
tail -f /home/azim/core/logs/fazle-core.log
tail -f /home/azim/core/logs/fazle-core-error.log
```

#### Rollback and backup

- Ad-hoc backup: `curl -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/backup/run`
- Snapshots land in `/home/azim/backups/` (rotated by scheduler).
- Code rollback: `git revert` or `git checkout <previous-sha>` followed by
  `sudo systemctl restart fazle-core.service`.
- Database rollback: use `psql` with a prior dump. See `RECOVERY.md`.
- **Never run production migrations or test commands against the production
  database.**

### B. Local desktop/development installation

#### Supported Python

- Python 3.10 or 3.11 (matches production runtime).

#### Steps (Linux/macOS/WSL)

```bash
git clone https://github.com/arshiyaazim/fazle-core.git
cd fazle-core
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r tests/requirements-test.txt
python db/migrate.py --dry-run
python db/migrate.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8200 --reload
```

#### Steps (Windows PowerShell)

```powershell
git clone https://github.com/arshiyaazim/fazle-core.git
Set-Location fazle-core
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
Copy-Item .env.example .env
python -m pip install -r requirements.txt
python -m pip install -r tests/requirements-test.txt
python db\migrate.py --dry-run
python db\migrate.py
python -m uvicorn app.main:app --host 127.0.0.1 --port 8200 --reload
```

#### Isolated test database

```bash
# Dedicated test database required — do not reuse dev/production DB.
# Set a local-only password; never reuse a production credential here.
export TEST_DB_PASSWORD='<local-test-password>'
export TEST_DATABASE_URL="postgresql://postgres:${TEST_DB_PASSWORD}@127.0.0.1:5432/fazle_test"
make test-unit
make test-integration
```

#### Local Ollama

```bash
ollama serve
ollama pull hermes3:3b
ollama list
```

Keep `AUTO_REPLY_ENABLED=false` and `OUTBOUND_ENABLED=false` for local
development unless a test explicitly requires outbound behavior.

**Production database or production `.env` must never be used for test
commands. Always use an isolated test database.**

---

## 5. External dependency installation and connection

### WhatsApp MCP / WhatsApp bridge

| Item | Detail |
|---|---|
| Purpose | Maintains WhatsApp Web sessions; writes messages into local SQLite stores. Core polls these stores via `modules/bridge_poller`. |
| Install | Separate repository/binaries. Not vendored here. Install and run independently. |
| Expected ports | Bridge1 default `http://127.0.0.1:8082`, Bridge2 default `http://127.0.0.1:8081`, Bridge3 default `http://127.0.0.1:8083` (configured in `.env` as `BRIDGE1_URL`..`BRIDGE3_URL`). |
| Env vars | `BRIDGE1_URL`, `BRIDGE1_NUMBER`, `BRIDGE1_DB_PATH`, `BRIDGE1_WHATSAPP_DB_PATH` (and equivalents for bridge2/bridge3). |
| Auth | Bridge-level authentication is configured on the bridge service itself, not in Core. Core uses `BRIDGE_WEBHOOK_SECRET` for webhook signature verification when `WEBHOOK_SIGNATURE_ENFORCEMENT=true`. |
| Health check | `curl -fsS http://127.0.0.1:8081/health` (adjust per bridge). |
| Failure behavior | Core logs bridge unavailability; inbound messages queue in SQLite and are polled on recovery. Outbound falls back to other bridges only if configured. |
| Restart needed? | No — Core polls at 5s intervals. Bridge restarts are transparent once the bridge is back up. |

### Hermes runner

| Item | Detail |
|---|---|
| Purpose | Separate admin AI relay and Hermes customer-dispatch helper. Phase 1 scope: Hermes receives pre-fetched identity-scoped context and returns text; no MCP tools. |
| Install | Separate repository/service. Not vendored here. |
| Expected URL | `http://127.0.0.1:8093` (`HERMES_RUNNER_URL`). |
| Env vars | `HERMES_RUNNER_URL`, `HERMES_RUNNER_SECRET`, `HERMES_RUNNER_CUSTOMER_SECRET`. |
| Auth | Two distinct Core-to-Hermes secrets: `HERMES_RUNNER_SECRET` (admin grant) and `HERMES_RUNNER_CUSTOMER_SECRET` (customer dispatch). Configure privately after the runner is protected on loopback or behind an authenticated reverse proxy. |
| Health check | `curl -fsS http://127.0.0.1:8093/health` |
| Failure behavior | Hermes calls fail closed to the local Ollama chain. Admin `HERMES` relay returns an error notification. |
| Restart needed? | No — Core calls Hermes per-request. Hermes restarts are transparent. |
| Setup script | `scripts/create_hermes_mcp_admin.py` — one-time Core identity helper. |

### OmniRoute

| Item | Detail |
|---|---|
| Purpose | Primary AI gateway (OpenAI-compatible). First step in the reply chain. |
| Install | Separate gateway service. Not vendored here. |
| Expected URL | `http://127.0.0.1:20128/v1` (`OMNIROUTE_URL`). |
| Env vars | `OMNIROUTE_URL`, `OMNIROUTE_API_KEY`, `OMNIROUTE_MODEL_NAME`. |
| Auth | `OMNIROUTE_API_KEY` — configure privately. |
| Health check | `curl -fsS http://127.0.0.1:20128/v1/models` |
| Failure behavior | Falls through to Groq (if enabled), then local Ollama, then Kimi (if enabled). |
| Restart needed? | No — Core calls OmniRoute per-request. |

### 9Router

| Item | Detail |
|---|---|
| Purpose | Development-only bounded physical-model routing adapter. |
| Install | Separate gateway. Not vendored here. |
| Expected URL | `http://127.0.0.1:20129/v1` (`NINE_ROUTER_URL`). |
| Env vars | `NINE_ROUTER_URL`, `NINE_ROUTER_PRIVATE_URL`, `NINE_ROUTER_DATA_PATH`, `NINE_ROUTER_VERSION`, `NINE_ROUTER_CREDENTIAL_REF`, `NINE_ROUTER_SINGLE_UPSTREAM_GUARANTEED`. |
| Auth | `NINE_ROUTER_CREDENTIAL_REF` resolves a credential from runtime settings. Configure privately. |
| Health check | `curl -fsS http://127.0.0.1:20129/v1/models` |
| Failure behavior | Not part of the normal reply chain. If invoked, failures return errors to the caller. |
| Restart needed? | No — not in the production reply chain. |
| Limitation | `NINE_ROUTER_SINGLE_UPSTREAM_GUARANTEED=false` — single-upstream guarantee is unverified. Route is dev-only. |

### Ollama

| Item | Detail |
|---|---|
| Purpose | Local LLM runtime. Default local fallback in the reply chain. |
| Install | Official Ollama installer. `ollama serve` or systemd service. |
| Expected URL | `http://127.0.0.1:11434` (`OLLAMA_URL`). |
| Env vars | `OLLAMA_URL`, `OLLAMA_MODEL`, `OLLAMA_RUNTIME_MODEL`, `OLLAMA_BUSINESS_MODEL`, `OLLAMA_MEMORY_MODEL`, `OLLAMA_GENERAL_MODEL`, `OLLAMA_DEEP_REASONING_MODEL`, `OLLAMA_REPLY_DISABLED`, `OLLAMA_INFER_CONCURRENCY`, `OLLAMA_CHAT_CONCURRENCY`, `OLLAMA_HTTP_MAX_CONNECTIONS`, `OLLAMA_HTTP_MAX_KEEPALIVE_CONNECTIONS`, `OLLAMA_NUM_CTX_DEFAULT`, `OLLAMA_NUM_CTX_CLASSIFY`, `OLLAMA_NUM_PREDICT_REPLY`, `OLLAMA_NUM_PREDICT_RECRUITMENT`. |
| Auth | None (local runtime). Keep port `11434` on loopback. |
| Health check | `curl -fsS http://127.0.0.1:11434/api/tags` |
| Failure behavior | Falls through to next provider in chain (Groq or Kimi). If all fail, fallback text is returned. |
| Restart needed? | No — Core calls Ollama per-request. Ollama restarts are transparent. |
| Models | Default `hermes3:3b`. Resolver prefers smallest Hermes-family tag (`hermes3:3b`, `hermes3`, `hermes3:latest`, `hermes`, `hermes:latest`). |

### Media processor

| Item | Detail |
|---|---|
| Purpose | OCR, voice transcription, and document extraction for inbound media. |
| Install | Separate Python process. Not vendored here. |
| Expected URL | `http://127.0.0.1:8090` (`MEDIA_PROCESSOR_URL`). |
| Env vars | `MEDIA_PROCESSOR_URL`, `BRIDGE_MEDIA_ACQUISITION_ENABLED`, `BRIDGE_MEDIA_DOWNLOAD_TIMEOUT_S`. |
| Auth | Not configured in Core. Bridge/media processor authentication is external. |
| Health check | `curl -fsS http://127.0.0.1:8090/health` (verify externally). |
| Failure behavior | Media remains `media_unprocessed` if processor is unreachable or disabled. |
| Restart needed? | No — Core calls per-media-item. |

### PostgreSQL

| Item | Detail |
|---|---|
| Purpose | Primary application database. |
| Install | PostgreSQL 15+ (Docker or native). |
| Expected connection | `DATABASE_URL=postgresql://user:pass@host:5432/dbname` |
| Env vars | `DATABASE_URL`. Test: `TEST_DATABASE_URL`, `TEST_DB_PASSWORD`. |
| Auth | Database username/password. Configure in `.env` or secure overlay. |
| Health check | `pg_isready -h 127.0.0.1 -p 5432` |
| Failure behavior | Core fails to start or returns `503` when DB pool is exhausted. |
| Restart needed? | Yes — Core requires a live DB connection at startup. |

### Redis

| Item | Detail |
|---|---|
| Purpose | Supporting service for queue/runtime helpers. |
| Install | Redis server or Docker container. |
| Expected URL | `redis://127.0.0.1:6379/9` (`REDIS_URL`). |
| Env vars | `REDIS_URL`. |
| Auth | Optional Redis AUTH (configure in `REDIS_URL` if needed). |
| Health check | `redis-cli -h 127.0.0.1 -p 6379 ping` |
| Failure behavior | Queue/runtime features that depend on Redis degrade or fail. |
| Restart needed? | No — Core connects lazily. |

### Qdrant

| Item | Detail |
|---|---|
| Purpose | Optional vector store for hybrid RAG. |
| Install | Qdrant server or Docker. |
| Expected URL | `QDRANT_HOST` + `QDRANT_PORT` (default `6333`). |
| Env vars | `QDRANT_HOST`, `QDRANT_PORT`. |
| Auth | Qdrant API key (configure externally if Qdrant is secured). |
| Health check | `curl -fsS http://127.0.0.1:6333/healthz` |
| Failure behavior | Falls back to embedded on-disk Qdrant client or BM25-only search. |
| Restart needed? | No — Core connects lazily. |

### LocationWhere

| Item | Detail |
|---|---|
| Purpose | Employee location and device status integration. |
| Install | Separate service. |
| Expected URL | `http://127.0.0.1:3000/api/v1` (`LOCATIONWHERE_BASE_URL`). |
| Env vars | `LOCATIONWHERE_BASE_URL`, `LOCATIONWHERE_JWT_TOKEN`, `LOCATIONWHERE_EMPLOYEE_LOCATION_ENDPOINT`, `LOCATIONWHERE_DEVICE_STATUS_ENDPOINT`, `LOCATIONWHERE_HEALTH_ENDPOINT`, `LOCATION_ADAPTER_TIMEOUT_S`, `LOCATION_ADAPTER_RETRIES`, `LOCATION_ADAPTER_CACHE_TTL_S`, `LOCATION_ADAPTER_CIRCUIT_MAX_FAILURES`, `LOCATION_ADAPTER_CIRCUIT_WINDOW_S`, `LOCATION_ADAPTER_CIRCUIT_OPEN_S`. |
| Auth | `LOCATIONWHERE_JWT_TOKEN` — configure privately. |
| Health check | `curl -fsS http://127.0.0.1:3000/api/v1/gateway/status` |
| Failure behavior | Circuit breaker opens after configured failures; cached data served until circuit closes. |
| Restart needed? | No — Core calls per-request with circuit breaker. |

### Meta WhatsApp Cloud API

| Item | Detail |
|---|---|
| Purpose | Webhook and messaging API for Meta-hosted WhatsApp Business. |
| Install | Meta developer account; phone number ID and app credentials. |
| Expected URL | `https://graph.facebook.com/v23.0` (`META_API_URL`). |
| Env vars | `META_PHONE_NUMBER_ID`, `META_API_TOKEN`, `META_API_URL`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `FB_PAGE_ACCESS_TOKEN`, `FB_PAGE_ID`. |
| Auth | `META_API_TOKEN` and `META_APP_SECRET` — configure privately in Meta developer console and `.env`. |
| Health check | Webhook verification: `GET /webhook/meta?hub.mode=subscribe&hub.verify_token=...&hub.challenge=...` |
| Failure behavior | Webhook returns error; message delivery retries via Meta's retry policy. |
| Restart needed? | No — Core handles webhooks per-request. |

---

## 6. Provider routing and fallback

### Current implementation

The reply chain is defined in `app/llm.py`:

```
OmniRoute → Ollama/Hermes → safe fallback
```

**Effective provider chain:** OmniRoute is the primary provider. If OmniRoute
is unavailable, the chain falls through to local Ollama (preferring a
Hermes-family model). Groq and Kimi are currently disabled; MiniMax has been
removed. If every step fails, `app/llm.py` returns per-caller fallback text.

The full registered route table in `config/phase3_ai_routes.yaml` includes:

1. **OmniRoute** — first step, and the only enabled remote provider. Direct
   provider at `OMNIROUTE_URL`. The resolver queries `/v1/models` to discover
   the real model name, then calls `/v1/chat/completions`.
   `OMNIROUTE_API_KEY` is sent as `Authorization: Bearer`. Route config in
   `config/phase3_ai_routes.yaml` (`core-omniroute-default`, priority 10,
   max_attempts 2).

2. **Groq** — second step. Direct provider at `https://api.groq.com/openai/v1`.
   `GROQ_API_KEY` is sent as `Authorization: Bearer`. Currently disabled
   (`GROQ_FALLBACK_ENABLED=false`, key empty). Route config
   `core-groq-default` (priority 20, enabled: false).

3. **Local Ollama / Hermes-family model** — third step. Core queries
   `OLLAMA_URL/api/tags`, prefers the smallest Hermes-family model
   (`hermes3:3b`, `hermes3`, `hermes3:latest`, `hermes`, `hermes:latest`),
   cached for 5 minutes. Falls back to `OLLAMA_MODEL` if none found.
   Skipped entirely when `OLLAMA_REPLY_DISABLED=true`. Route config
   `core-ollama-local` (priority 30, enabled: true).

4. **Kimi** — fourth step. Reached through the same Ollama client using
   `KIMI_MODEL_NAME` (default `kimi-k2.6:cloud`, Ollama's cloud-model proxy).
   Not a separate HTTP provider. Currently disabled (route config
   `core-kimi-ollama-proxy`, enabled: false).

### Hermes customer dispatch

- `modules/hermes_dispatch.py` is a separate, narrower path. It hands the
  AI-generated portion of specific reply categories (Employee informational,
  Recruitment, Route B, Tier-15 fallback) to Hermes via `HERMES_RUNNER_URL`
  `/run`. Hermes receives pre-fetched context as prompt text; it does not
  query databases itself. Phase 1 scope excludes MCP tools.

### 9Router

- `app/nine_router_provider.py` — bounded physical-model routing adapter.
  Development-only. Requires an explicit physical model ID (aliases rejected).
  Not part of the normal reply chain. `NINE_ROUTER_SINGLE_UPSTREAM_GUARANTEED=false`
  — single-upstream guarantee is unverified.

### Failure and fallback behavior

If every provider step fails, `app/llm.py` returns per-caller fallback text
(`_FALLBACK_REPLY` for conversational, `RECRUITMENT_FALLBACK_TEXT` for
recruitment). Callers can call `get_last_attempts()` for the full
per-provider attempt trace.

`PRIMARY_AI_PROVIDER` is accepted for compatibility but is **not** read by
`generate_reply()`/`generate_recruitment_reply()`; it is retained for
`check_health()` reporting only.

### Disabled providers

| Provider | Status | Reason |
|---|---|---|
| MiniMax | Removed | Absent from `.env.example` and code. |
| Groq | Disabled | Key empty; `GROQ_FALLBACK_ENABLED=false`. |
| Kimi | Disabled | Route disabled; not enabled by default. |
| GitHub Models | Removed | Confirmed dead endpoint (2026-08-05). |

### Provider model ID naming convention

- OmniRoute: gateway alias (e.g. `auto`) resolved by the gateway.
- Ollama: physical model tags (e.g. `hermes3:3b`).
- 9Router: explicit physical model ID only; aliases are rejected with an error.
- Kimi: Ollama cloud-model proxy tag (e.g. `kimi-k2.6:cloud`).

### Provider configuration change and restart

Provider configuration changes in `.env` (URLs, keys, model names) require a
service restart to take effect because the running process keeps its
pre-change environment. The `PRIMARY_AI_PROVIDER` setting does not override
the reply chain at runtime; changing it without updating the actual provider
chain in `app/llm.py` has no effect on reply generation.

### Free/rate-limited provider risk

- Free/rate-limited providers (e.g. Groq free tier, GitHub Models free tier)
  can exhaust credits or hit rate limits. The reply chain degrades to the next
  available provider. Core does not buffer or queue AI requests; a total
  failure returns fallback text immediately.

---

## 7. API key and subscription guide

Documented variable names only. **No actual values, masked values, prefixes,
token lengths, or credential fingerprints are recorded in this README.**

| Variable | Purpose | Required? | Used by | Where to configure privately | Rotation/revocation |
|---|---|---|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Yes | Core app | `.env` or secure overlay | Change password in Postgres; update `.env`; restart. |
| `REDIS_URL` | Redis connection string | Yes | Core app | `.env` or secure overlay | Update `.env`; restart. |
| `INTERNAL_API_KEY` | Privileged single API key (legacy; kept alongside per-admin keys) | Yes | Core app | `.env` or secure overlay | Generate new random key; update `.env`; restart. Issue per-admin keys via `/admin/users/{phone}/apikey`. |
| `OMNIROUTE_API_KEY` | OmniRoute gateway authentication | Yes (for OmniRoute path) | `app/omniroute_provider.py` | `.env` or secure overlay | Rotate in OmniRoute admin; update `.env`; restart. |
| `GROQ_API_KEY` | Groq provider authentication | No (disabled) | `app/groq_provider.py` | `.env` or secure overlay | Rotate in Groq console; update `.env`; restart. |
| `GITHUB_TOKEN` / `GITHUB_TOKEN_2` / `GITHUB_TOKEN_3` | GitHub Models rotating tokens | No (dormant legacy) | `app/github_models.py` | `.env` or secure overlay | Rotate in GitHub settings; update `.env`; restart. |
| `HERMES_RUNNER_SECRET` | Core→Hermes admin grant secret | Yes (for Hermes admin relay) | `modules/hermes_tasks/`, `modules/admin_hermes_action_grant.py` | `.env` or secure overlay | Regenerate in Hermes runner config; update `.env`; restart. |
| `HERMES_RUNNER_CUSTOMER_SECRET` | Core→Hermes customer dispatch secret | Yes (for Hermes dispatch) | `modules/hermes_dispatch.py` | `.env` or secure overlay | Regenerate in Hermes runner config; update `.env`; restart. |
| `FAZLE_CORE_READ_KEY` | Read-only HTTPS contract for Vessel Register Site `/api/core` proxy | Yes (for Site integration) | Vessel-register read-only GET routes | `.env` or secure overlay | Generate new key; update `.env`; restart. Not accepted by mutation/send/admin routes. |
| `BRIDGE_WEBHOOK_SECRET` | Bridge webhook signature secret | Optional | `app/main.py` webhook verification | `.env` or secure overlay | Update bridge and `.env` simultaneously. |
| `META_API_TOKEN` | Meta WhatsApp Cloud API token | Yes (for Meta webhook path) | `app/main.py` Meta send | Meta developer console + `.env` | Regenerate in Meta developer console; update `.env`; restart. |
| `META_APP_SECRET` | Meta app secret for webhook signature | Yes (for Meta webhook) | `app/main.py` webhook verification | Meta developer console + `.env` | Regenerate in Meta developer console; update `.env`; restart. |
| `META_VERIFY_TOKEN` | Meta webhook verification token | Yes | `app/main.py` webhook verification | `.env` or secure overlay | Update `.env`; update Meta webhook config. |
| `QDRANT_HOST` / `QDRANT_PORT` | Qdrant connection | Yes (for hybrid RAG) | `modules/rag/` | `.env` or secure overlay | Update `.env`; restart. |
| `LOCATIONWHERE_JWT_TOKEN` | LocationWhere API authentication | Yes (for location integration) | `modules/location_adapter/` | `.env` or secure overlay | Rotate in LocationWhere admin; update `.env`; restart. |
| `AI_POLICY_RUNNER_BEARER` | AI policy runner bearer token | Optional | Policy runner endpoints | `.env` or secure overlay | Rotate bearer; update `.env`; restart. |
| `AI_POLICY_OBSERVER_BEARER` | AI policy observer bearer token | Optional | Policy observer endpoints | `.env` or secure overlay | Rotate bearer; update `.env`; restart. |

### General rotation steps

1. Generate a new secret/token in the external service (or locally for
   internal keys).
2. Update the corresponding variable in `.env` (or the secure overlay).
3. Restart `fazle-core.service`.
4. Verify health: `curl -fsS http://127.0.0.1:8200/health`.
5. Revoke the old token in the external service.

### Private storage recommendations

- `.env` is never committed. Keep it mode `0600`.
- Use the secure overlay (`/home/azim/secure-env-backup/runtime-services.env`)
  for production secrets when the VPS supports it.
- Back up `.env` to `/home/azim/secure-env-backup/` after approved changes.
- Do not copy credentials into `.env.example`.

---

## 8. Frontend and admin page guide

All pages are served by `app/main.py` as `FileResponse` from `app/static/`.
Auth uses a login overlay backed by `X-Internal-Key` (cached in
`localStorage`) or admin username/password (database-backed, hashed).

| URL | Page | Purpose | Required role | Action | Notes |
|---|---|---|---|---|---|
| `/` `/dashboard` `/dashboard/legacy` | Main dashboard | System overview, drafts, reports, users, audit, backups, scheduler, RAG, observability, chat lab | Authenticated admin | Read + mutation (add user, issue key, disable user, run backup, reindex RAG, send chat) | Shell page. |
| `/payroll` | Payroll/FPE | FPE transactions, employees, ledger, unmatched, manual entries | Authenticated admin | Read + mutation | Entry to FPE subsystem. |
| `/payroll/overview` | Payroll overview | FPE overview | Authenticated admin | Read | |
| `/payroll/transactions` | FPE transactions | Transaction list | Authenticated admin | Read | |
| `/payroll/search` | FPE search | Employee/transaction search | Authenticated admin | Read | |
| `/payroll/employees` | FPE employees | Employee master | Authenticated admin | Read + mutation | |
| `/payroll/addemployee` | Add employee | Employee creation | Authenticated admin | Mutation | |
| `/payroll/unmatched` | Unmatched messages | Unmatched WhatsApp messages | Authenticated admin | Read + mutation | |
| `/payroll/review` | Review queue | Pending review items | Authenticated admin | Read + mutation | |
| `/payroll/sync` | Sync | Sync operations | Authenticated admin | Mutation | |
| `/payroll/manual` | Manual entry | Manual transaction entry | Authenticated admin | Mutation | |
| `/payroll/operator` | Operator | Operator-specific views | Authenticated admin | Read | |
| `/payroll/cash` | Cash transactions | Cash ledger | Authenticated admin | Read | |
| `/payroll/income` | Income transactions | Income ledger | Authenticated admin | Read + mutation | |
| `/payroll/admin` | FPE admin | Admin-level FPE operations | Authenticated admin | Read + mutation | |
| `/escort-roster` | Escort roster | Roster, kanban, slip queue, shift calc, config, attendance, net salary, admin | Authenticated admin | Read + mutation | |
| `/unmatched-confirmations` | Unmatched confirmations | Confirm/reject unmatched escort items | Authenticated admin | Mutation | |
| `/drafts` | Reply drafts | Approve, edit, delete, block drafts | Authenticated admin | Mutation | |
| `/kb` | Knowledge base | Upload, list, delete KB files; category/tag management | Authenticated admin | Mutation | |
| `/admin/knowledge/recruitment` | Recruitment KB | Recruitment-specific knowledge management | Authenticated admin | Mutation | |
| `/chat-lab` `/open-chat` | AI chat console | Chat with AI models; memory stats/history | Authenticated admin | Read + mutation (send message, upload, voice) | |
| `/wa-chat` `/dashboard/wa-chat` | WhatsApp workspace | WhatsApp message workspace | Authenticated admin | Read | |
| `/dispatch` | Dispatch | Dispatch views | Authenticated admin | Read + mutation | |
| `/wbom-create-link` | WBOM create link | Link creation interface | Authenticated admin | Mutation | Source-verified page. |
| `/wbom-link-review` | WBOM link review | Link review interface | Authenticated admin | Read + mutation | Source-verified page. |

### Admin API endpoints (no dedicated HTML page)

| URL | Purpose | Required command | Action |
|---|---|---|---|
| `/admin/overview` | Aggregate system overview | `require_api_key` | Read |
| `/admin/safe-mode` | Current safe-mode state | `require_api_key` | Read |
| `/admin/drafts` | All drafts | `require_api_key` | Read |
| `/admin/payment-drafts` | Payment drafts | `require_api_key` | Read |
| `/admin/approvals` | Approval queue | `require_api_key` | Read |
| `/admin/recruitment` | Recruitment data | `require_api_key` | Read |
| `/admin/memory/reviewed-replies` | Reviewed reply memory | `require_api_key` | Read |
| `/admin/users` | User CRUD + role management | `user_list`/`user_add`/`user_role`/`user_remove`/`user_apikey` | Read + mutation |
| `/admin/audit` | Admin audit log | `user_list` | Read |
| `/admin/directives` | Admin directives | `admin_directive_read`/`admin_directive_write` | Read + mutation |
| `/admin/send-whatsapp` | Send WhatsApp message | `send_whatsapp_message` | Mutation |
| `/reports` | Available reports | `require_api_key` | Read |
| `/reports/{name}` | Specific report | `require_api_key` | Read |
| `/backup/status` `/backup/list` `/backup/run` `/backup/rotate` | Backup operations | `require_api_key` | Read + mutation |
| `/scheduler/status` `/scheduler/run/{job_name}` | Scheduler control | `require_api_key` / `schedule_run` | Read + mutation |
| `/rag/stats` `/rag/search` `/rag/answer` `/rag/reindex` | RAG operations | `require_api_key` | Read + mutation |
| `/health` `/health/deep` `/health/c1b` | Health probes | `/health` public; others require `X-Internal-Key` | Read |

### Security recommendation

Change the admin username/password after first login via the authenticated
admin user-management flow (`/admin/users` or Payroll/Admin page). Password
and login-token hashes live in `fazle_admins`. Do not add admin credentials
to `.env.example` or commit them. OmniRoute, Ollama, Hermes, and WhatsApp
MCP/bridge UIs are separate applications; configure authentication for each
independently and do not expose their raw ports publicly.

---

## 9. Messaging workflow

### Non-technical overview

1. A WhatsApp message arrives via Meta webhook or one of the three bridges.
2. Core stores the native message ID and resolves the sender's phone number
   to an `identity_phone`.
3. The message is routed by intent and sender role (employee, client,
   candidate, admin, unknown, etc.) to the appropriate workflow module.
4. If AI assistance is needed, the effective provider chain
   (OmniRoute → Ollama/Hermes → safe fallback) generates or polishes a reply.
   Groq and Kimi are registered but disabled, and MiniMax is removed.
5. For designated roles, a draft is created for admin review. Approved drafts
   are sent via the outbound queue.
6. Every step writes audit records and observability counters.

### Technical flow

```
Inbound (bridge webhook or poller)
    │
    ▼
identity_resolver.resolve_inbound_identity()
    │   Input: sender_number, canonical_phone, LID, JID, push-name, etc.
    │   Output: identity_phone (normalized mobile), trusted_identity_name,
    │           employee_id, contact_id, role, trust_level
    ▼
phone_normalizer.normalize_phone()
    │   E.164 normalization; last-10 variants
    ▼
message_router.process_message()
    │
    ├── intent.classify() → intent label
    ├── identity_brain.detect_identity() → role + trust
    ├── get_recent_history(phone) → conversation context
    ├── knowledge_base lookup (authoritative + archived separation)
    │
    ├── Domain routing
    │       ├── admin_commands → APPORVE/REJECT/PAID/STATUS etc.
    │       ├── escort → parse_escort_message() → draft/finalize
    │       ├── recruitment_flow → session/turn decision
    │       ├── payment_workflow → escort payment draft / advance request
    │       ├── attendance → attendance query/parser
    │       └── rag → knowledge retrieval
    │
    ├── AI provider chain (app.llm.generate_reply / generate_recruitment_reply)
    │       ├── OmniRoute (primary)
    │       ├── Groq (disabled)
    │       ├── Ollama (Hermes-family model preference)
    │       └── Kimi (disabled)
    │       Fallback text on total failure.
    │
    ├── Draft creation (DRAFT_CREATION_ENABLED=true)
    │       └── fazle_draft_replies (status=pending)
    │
    └── Outbound queue (OUTBOUND_ENABLED=true)
            ├── enqueue with idempotency dedup
            ├── sweep_once → bridge.send()
            ├── retry with exponential backoff
            └── DLQ at max_attempts
```

### Key invariants

- **Native message ID preservation:** The original WhatsApp message ID is
  stored in `wbom_whatsapp_messages` (or bridge SQLite) and referenced by
  replies and audit rows.
- **Bridge/account instance:** Each bridge has its own URL, number, label,
  and SQLite store. Messages are attributed to their source bridge.
- **Chat ID vs sender/participant ID:** Group messages carry a group JID;
  the sender's phone is resolved separately. `modules/group_identity.py`
  mirrors group metadata but does not alter routing.
- **Phone normalization:** `modules/phone_normalizer.py` normalizes to E.164;
  `identity_resolver.py` uses normalized `identity_phone` for all lookups.
- **Employee/client/candidate/unknown identity lookup:**
  - `modules/identity_brain/` is the canonical role/trust engine.
  - `modules/identity_resolver.py` resolves `employee_id`, `contact_id`,
    `role`, and `trust_level` from the normalized phone.
  - Unknown numbers are routed to `unknown` intent; they are **not** treated
    as applicants, clients, or employees by default.
- **Context/history lookup:** `message_router.get_recent_history()` and
  `get_contact_context()` retrieve recent bidirectional history.
- **Domain routing:** `process_message()` returns `(reply_text, admin_notification)`;
  actual send happens downstream.
- **Reply draft vs approved/sent message:** Drafts are created for designated
  roles even when `AUTO_REPLY_ENABLED=false`. Sending requires explicit admin
  `APPROVE`.
- **Reply provenance:** Inbound processing and outbound queue metadata carry
  sender/group/account/reply provenance. `modules/conversation_memory/` tracks
  `duplicate_replay` exclusion.
- **Original message ↔ reply relationship:** Reply drafts reference the
  original message ID. Outbound queue items carry provenance fields.
- **Replay duplicate prevention:** `modules/outbound/__init__.py` uses
  idempotency_key dedup. `modules/escort/` prevents exact-duplicate confirmations
  via DB unique index. `modules/conversation_memory/` classifies
  `duplicate_replay` exclusions.

### Important caveat

**Unknown numbers cannot be pre-identified as applicants, clients, or
employees.** They must go through the normal identity resolution flow and are
treated as `unknown` until explicitly classified.

---

## 10. Identity update guide

### Which module/table/config to use

- **Employee identity:** `fpe_employees` (canonical) and `wbom_employees`
  (legacy, still used by some paths). Edit via `modules/admin_employees` API
  (`PUT /api/admin/employees/{emp_id}`) or directly via admin transaction API.
  `modules/fazle_payroll_engine/employee.py` provides `match_or_create_employee`.
- **Role/trust:** `modules/identity_brain/` — `detect_identity()` computes
  role, trust level, and notes. Not directly exposed as an HTTP endpoint.
- **Payout phone:** `fpe_employee_payout_destinations` (separate from
  employee ID). Edit via FPE employee endpoints or admin transaction API.
- **Group participant vs group chat:** `modules/group_identity.py` mirrors
  group JIDs from the bridge SQLite `chats` table into `wbom_group_identities`.
  Group names are used only to recognize already-known groups; JIDs are never
  hand-invented.
- **Accountant alias:** `ACCOUNTANT_PHONE` in `.env` designates the accountant
  phone. `modules/payment_relay/` uses `RELAY_DEST_NUMBER` internally for the
  frozen relay path.

### Identity change audit/review

- All employee edits are logged via `modules/admin_employees` and
  `modules/admin_transactions`.
- Identity changes that affect payroll or payment paths must be reviewed
  against the FPE transaction ledger (`fpe_cash_transactions`).
- `modules/identity_role_admin/` exposes role-persistence administration.

### Important rule

**A name or payout phone change alone is not sufficient for identity
assignment.** Phone normalization (`identity_phone`) is used for **lookup and
authorization**; it does not by itself establish identity. Identity assignment
requires full resolution through `modules/identity_brain/` and
`modules/identity_resolver.py`, which combine phone, role, trust level, and
context. Names are supporting/display evidence only. The owner-authoritative
principle (from `modules/identity_resolver.py`) is: "For WhatsApp identity,
the normalized WhatsApp mobile number is the primary identity key."

---

## 11. Admin messaging and AI agent operations

### Admin messaging conversation

- Admins can trigger operations via WhatsApp commands (`APPROVE`, `REJECT`,
  `EDIT`, `PAID`, `ADVANCE`, `STATUS`, `DRAFTS`, etc.) parsed by
  `modules/admin_commands/`.
- Admin can send arbitrary WhatsApp messages via `/admin/send-whatsapp`
  (requires `send_whatsapp_message` command permission).
- Admin directives (persistent instructions) are managed via `/admin/directives`
  (create, revoke). Requires `admin_directive_write` permission.
- Scheduled jobs can be triggered manually via `/scheduler/run/{job_name}`
  (requires `schedule_run` command permission).

### Custom admin instruction processing

- `modules/admin_directives/router.py` — `is_admin_ai_command(text)` detects
  admin AI requests and routes them through the admin AI engine
  (`docs/ADMIN_AI_ENGINE.md`).
- `modules/hermes_tasks/` — Hermes admin task routing.
- `modules/hermes_dispatch.py` — Hermes customer-dispatch for specific reply
  categories (Phase 1 scope).

### Report/audit generation

- `/reports` and `/reports/{name}` — available reports.
- `/admin/audit` — admin audit log.
- `/observability/summary` and `/observability/errors` — error summary and
  recent errors.
- Scheduler jobs generate periodic reports (e.g., daily gap report).

### Approval/send/enqueue/ledger write safety boundaries

- **Draft approval:** `APPROVE <id>` marks a draft as sent and forwards via
  the original bridge. Only designated roles can auto-send; others create
  drafts even when `AUTO_REPLY_ENABLED=true`.
- **Outbound queue:** `modules/outbound/__init__.py` enqueues with
  idempotency dedup, retries with exponential backoff, and moves to DLQ at
  `max_attempts`.
- **Authorization snapshot:** `modules/outbound_authorization/` captures
  authorization evidence at enqueue and revalidates on sweep.
- **Ledger write:** `fpe_cash_transactions` is the canonical cash ledger.
  Only `modules/fazle_payroll_engine/accounting.py` should create rows there.
  `modules/payment_ingest.py` and `modules/payment_workflow.py` write through
  FPE's canonical creator.

### Read-only vs explicit approval required

- Read-only: `/admin/overview`, `/admin/drafts`, `/admin/payment-drafts`,
  `/admin/approvals`, `/admin/audit`, `/reports`, `/observability/*`,
  `/health/*`, `/api/whatsapp/messages/query`, `/api/identity/resolve`,
  `/api/rag/stats`, `/api/rag/search`.
- Requires explicit admin approval or command permission:
  `/admin/send-whatsapp`, `/admin/directives` (write), `/admin/users` (mutation),
  `/backup/run`, `/scheduler/run/{job_name}`, `/admin/transactions` (mutation),
  `/payment/escort-draft`, `/payment/ingest`, `/payment/advance-draft`.

---

## 12. Knowledge base policy

### Authoritative knowledge register

- `knowledge_base/00_governance/` — policy, authority matrix, manifest,
  management decisions.
- `knowledge_base/01_employee_knowledge/` — employee/candidate-safe knowledge.
- `knowledge_base/02_admin_knowledge/` — admin/HR/operations knowledge.
- `knowledge_base/03_ai_identity/` — role mapping and permission matrix.
- `knowledge_base/04_business_rules/` — decision-engine rules.
- `knowledge_base/05_workflows/` — operational workflows.
- Runtime DB table: `fazle_knowledge_base` (admin-editable via `/admin/kb/upload`).

### Archived/historical/conflicting facts

- `knowledge_base/07_archived/` — legacy source rewrites, conflict archives,
  `recruitment_fact_conflicts.json`.
- `knowledge_base/module_registry.yaml` — runtime index.

### Candidate-facing answerability

- `modules/knowledge_base/__init__.py` provides instant auto-replies for
  common intents without LLM overhead.
- Recruitment fact authority is governed by
  `knowledge_base/00_governance/recruitment_fact_authority.json`.
- Approved salary facts remain answerable; unresolved facts fail closed to
  the safe fallback: "এই বিষয়ে নিশ্চিত তথ্যের জন্য অফিসে যোগাযোগ করুন।"

### Unresolved facts fail-closed behavior

When the knowledge base cannot provide an authoritative answer (no DB entry,
no hardcoded fallback, or conflicting archived facts), the system returns the
recruitment safe fallback text. It does not invent or guess.

### Knowledge base update process

- Admin uploads via `/admin/kb/upload` (multipart, PDF/DOCX/TXT/MD/CSV,
  max 10MB).
- Category/Subcategory/Tags are required.
- `sync_knowledge_base_to_runtime.py` and scheduler jobs refresh the runtime
  index.
- `rebuild_qdrant.py` reindexes vector store after KB edits.

### Archival data rule

Archived data in `knowledge_base/07_archived/` and
`knowledge_base/07_archived/recruitment_fact_conflicts.json` is never used
as current authoritative answer. It is retained for traceability and conflict
resolution reference only.

---

## 13. Operations guide

### Health check

```bash
curl -fsS http://127.0.0.1:8200/health
curl -s -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/health/deep | jq
```

### Service status

```bash
sudo systemctl status fazle-core.service
sudo systemctl is-active fazle-core.service
```

### Logs

```bash
sudo journalctl -u fazle-core -n 200 -f
tail -f /home/azim/core/logs/fazle-core.log
tail -f /home/azim/core/logs/fazle-core-error.log
```

### Safe restart

Run the pre-restart safety gate before any production restart:

```bash
scripts/restart_gate.sh --check-only   # checks 1–7
scripts/restart_gate.sh --restart      # checks 1–9, restarts if PASS
```

The gate checks:
1. Working-tree cleanliness or approved dirty-file manifest.
2. No leaked secret patterns in git-tracked files.
3. No new `pytest.mark.xfail` increase.
4. Targeted tests pass.
5. No untracked migration files without an inventory entry.
5b. Migrations actually applied to the live DB.
6. `.env` critical flags acknowledged.
7. DB backup freshness.
8. Service health before restart.
9. Service health after restart (distinguishes process failure, startup
   exception, and ambient probe degradation).

See `docs/DEPLOYMENT_RESTART_GATE.md` for the full design.

### Backup

```bash
curl -X POST -H "X-Internal-Key: $KEY" http://127.0.0.1:8200/backup/run
# Snapshots land in /home/azim/backups/ (rotated by scheduler)
```

### Rollback

- Code rollback: `git revert` or `git checkout <previous-sha>`; restart service.
- Database rollback: restore from a prior dump. See `RECOVERY.md`.
- `.env` rollback: restore from `/home/azim/secure-env-backup/`.

### Queue pause/resume

```bash
# Pause outbound sweep (development/emergency only — production requires owner approval)
export USE_OUTBOUND_QUEUE=false
# Resume
export USE_OUTBOUND_QUEUE=true
sudo systemctl restart fazle-core.service
```

### Outbound enable checklist

Before setting `OUTBOUND_ENABLED=true`:
- [ ] `git diff`/`git status` clean or approved manifest.
- [ ] All targeted tests pass.
- [ ] Secrets verified privately (no leaked credentials in `.env` or tracked files).
- [ ] Service health returns `200`.
- [ ] Provider synthetic test pass (OmniRoute → Ollama fallback verified).
- [ ] Hermes synthetic test pass (if Hermes dispatch is enabled).
- [ ] `OUTBOUND_ENABLED=false` state verified in running env.
- [ ] Outbound queue reviewed (no stale or unauthorized items).
- [ ] Owner approval obtained.
- Then enable and restart.

### Production migration prohibition

Never run migrations or test commands against the production database. Always
use an isolated test database for development/testing.

### Synthetic test before real WhatsApp test

Before enabling live WhatsApp sends, run a synthetic customer `POST /run`
test (no WhatsApp send) to verify the full routing and AI chain. See
`docs/OPERATIONS.md` and the Hermes integration docs for procedures.

---

## 14. Development, testing, and cleanup

### Test environment setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r tests/requirements-test.txt
```

### Isolated database requirement

All tests require `TEST_DATABASE_URL` pointing to a dedicated test database.
The test Docker stack (`docker-compose.test.yml`) provides `postgres-test`,
`redis-test`, `mock-bridge1`, `mock-bridge2`, and `mock-ollama`.

```bash
make test-docker-up
make test-docker
make test-docker-down
```

### Pytest commands

```bash
make test-unit          # Fast unit suite
make test-integration   # Integration + DB + workflow tests
make test-resilience    # Failure recovery tests
make test-e2e           # Playwright browser tests
make test-load          # Locust load tests
make smoke              # Quick smoke check (no coverage)
```

### Key test files

- `tests/unit/test_llm_provider_order.py` — provider chain order.
- `tests/unit/test_phase_c_provenance.py` — identity/reply provenance.
- `tests/unit/test_group_identity_sync.py` — group identity sync.
- `tests/unit/test_identity_brain.py` — identity brain.
- `tests/unit/test_phase2_identity_resolver.py` — phone-based resolver.
- `tests/unit/test_recruitment_ai_restricted.py` — recruitment AI scope.
- `tests/unit/test_restart_gate_readiness.py` — restart gate logic.
- `tests/unit/test_blocker_a_b_relay_accounting.py` — payment relay accounting.
- `tests/unit/test_admin_commands.py` — admin command parsing.
- `tests/unit/test_rbac.py` — role-based access control.
- `tests/unit/test_drafts_router_mounted.py` — draft router mount check.

### No-secret scan

```bash
bash scripts/secret_scan.sh
```

### Static checks

```bash
bash scripts/run_ci.sh
```

### Unused file/module cleanup procedure

Before removing any file or module:
1. Search for all imports and references (`grep -rn "modules.foo"`).
2. Back up the file/module (copy to `archive/` or git stash).
3. Run targeted tests that cover the dependency surface.
4. Review with `git diff` and confirm no unintended removals.
5. Do not delete production data or `.env` entries without explicit owner approval.

### Git worktree/stash recovery

```bash
git worktree list
git stash list
git stash pop <stash-name>
git checkout <previous-sha>
```

### Production dirty changes overwrite prohibition

The production instance runs directly off the working tree (`WorkingDirectory=/home/azim/core`). Any uncommitted change is live on the next restart. Never overwrite the production working tree without:
- Backing up current state (`git stash` or copy).
- Running the restart gate.
- Obtaining owner approval.

---

## 15. Current known limitations

| Limitation | Source |
|---|---|
| Group AI analysis/summary deferred | `modules/group_identity.py`: "Messages remain read/index-only until a separate analysis workflow explicitly consumes them." Not wired into the poll loop. |
| 9Router single-upstream guarantee unverified | `app/nine_router_provider.py` and `config/phase3_ai_routes.yaml`: `NINE_ROUTER_SINGLE_UPSTREAM_GUARANTEED=false`. Dev-only. |
| Provider fallback chain does not queue AI requests | `app/llm.py`: total failure returns fallback text immediately. |
| Test environment requires isolated PostgreSQL | `tests/pytest.ini`, `docker-compose.test.yml`. Production DB must never be used for tests. |
| External Site proxy not verified running | `docs/DEVELOPER_GUIDE.md`: LocationWhere integration configured but no listener verified on port 3000 during last audit. |
| Schema ambiguity — dual employee tables | `wbom_employees` and `fpe_employees` coexist. Some paths write to `wbom_employees` as primary. See `docs/MODULE_MAP.md` and `docs/DEVELOPER_GUIDE.md` §F. |
| Manual restart required for `.env` changes | Core process keeps pre-change environment until restarted. |
| Outbound disabled by default | `OUTBOUND_ENABLED=false` in `.env.example`. Live enable requires owner approval and verification. |
| Webhook signature enforcement off (bridge1) | `WEBHOOK_SIGNATURE_ENFORCEMENT=false`. Unsigned bridge1 webhooks accepted with audit log. |
| RAG recurring degradation | Qdrant availability recurs daily at 18:00 per production status doc. |
| Payment relay FROZEN | `PAYMENT_RELAY_ENABLED=true` but path is intentionally isolated and must not be modified without review. |

---

## Before Production Activation checklist

Complete all items before enabling live WhatsApp messaging or changing
production safety flags:

- [ ] `git diff`/`git status` clean or approved dirty-file manifest.
- [ ] `scripts/restart_gate.sh --check-only` passes.
- [ ] All targeted tests pass (`make test-unit` minimum).
- [ ] No secret patterns in tracked files (`scripts/secret_scan.sh`).
- [ ] Migration inventory matches `db/migrations/*.sql`.
- [ ] Migrations applied to live DB (`SELECT name FROM schema_migrations`).
- [ ] Secrets verified privately (no leaked credentials).
- [ ] Service health returns `200` (`curl -fsS http://127.0.0.1:8200/health`).
- [ ] Provider synthetic test pass (OmniRoute → Ollama fallback verified).
- [ ] Hermes synthetic test pass (if Hermes dispatch is enabled).
- [ ] `OUTBOUND_ENABLED=false` verified in running env.
- [ ] Outbound queue reviewed (no stale or unauthorized items).
- [ ] Owner approval obtained.
- [ ] Backup fresh (`/home/azim/backups/fazle/`).
- [ ] `AUTO_REPLY_ENABLED` and `RECRUITMENT_AUTOREPLY_ENABLED` intentionally set.
- [ ] `WEBHOOK_SIGNATURE_ENFORCEMENT` and `META_WEBHOOK_SIGNATURE_ENFORCEMENT` reviewed.
- [ ] Live messaging enable (`OUTBOUND_ENABLED=true`) only after all above pass.
