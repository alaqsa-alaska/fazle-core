# Fazle Core

Fazle Core is the production WhatsApp-first operations backend for Al-Aqsa
Security and Logistics Services Ltd. It is a FastAPI service that ingests
messages from WhatsApp bridge databases, bridge webhooks, Meta/Facebook
webhooks, and admin UIs, then routes them into recruitment, payroll, escort,
payment, knowledge-base, memory, and observability workflows.

This README describes the app as it exists in this checkout, not as a clean
target architecture. The system is live, useful, and feature-rich, but several
domains are still transitional and must stay under human review.

| Item | Current value |
|---|---|
| App path | `/home/azim/core` |
| Service | `fazle-core.service` |
| Internal port | `8200` |
| Public route | Nginx proxy, normally `fazle.iamazim.com` |
| Runtime | Python, FastAPI, uvicorn, asyncpg |
| Database | PostgreSQL plus WhatsApp bridge SQLite stores |
| Queue/cache | Redis-backed helpers plus PostgreSQL queues |
| Local AI | Ollama, default model `qwen3:8b` |
| Branch in this checkout | `backup/vps-core-20260612` |
| Last README review | 2026-06-26 |

## Operating Mode

The live `.env` controls whether Fazle Core sends automatically, drafts for
review, or only records inbound messages. Do not infer safety from code alone;
always check `.env` before changing reply behavior.

Important current flags:

| Setting | Purpose |
|---|---|
| `AUTO_REPLY_ENABLED` | Master switch for normal customer auto-replies |
| `RECRUITMENT_AUTOREPLY_ENABLED` | Allows recruitment candidate replies through the restricted recruitment flow |
| `AUTO_REPLY_SOURCES` | Source allow-list for reply/draft processing, usually bridge1, bridge2, and meta |
| `DRAFT_CREATION_ENABLED` | Stores unsafe or suppressed replies for admin review |
| `INTERNAL_NOTIFICATIONS_ENABLED` | Allows internal/admin notifications independently from customer auto-reply |
| `OUTBOUND_ENABLED` | Enables the outbound worker to deliver queued messages |
| `USE_OUTBOUND_QUEUE` | Sends through the durable outbound queue where supported |
| `SOCIAL_AUTO_REPLY_SINGLE_ENGINE` | Lets the social reply daemon own supported reply paths and avoid duplicate legacy processing |
| `OLLAMA_REPLY_DISABLED` | Removes Ollama from customer-facing reply generation when true |

Current code prefers Ollama for generated replies when it is enabled:

```text
Ollama -> Groq -> GitHub Models -> fixed holding reply
```

Intent classification uses:

```text
Ollama -> Groq -> GitHub Models -> unknown
```

Recruitment replies are intentionally narrower than general chat. They use the
approved recruitment source of truth in `resources/ops/` and fixed safe
fallbacks; they should not be treated as a general HR knowledge agent.

## What The App Does

Fazle Core currently handles:

- WhatsApp Bridge 1 and Bridge 2 SQLite polling with deduplication, LID handling,
  media extraction, voice/document processing, and bridge health checks.
- Meta WhatsApp, Messenger, and Facebook comment ingestion through webhook and
  social auto-reply modules.
- Message archiving into `wbom_whatsapp_messages`, identity detection, role
  tagging, contact-role overrides, and reviewed reply memory.
- Recruitment intake and restricted candidate auto-replies.
- Admin commands over WhatsApp, including approval and payment-related flows.
- Draft reply review, edit, approve, reject, block, and send workflows.
- Fazle Payroll Engine transaction ingestion, cash/income tracking, employee
  lookup, normalization review, reconciliation, gap scans, and DLQ/admin tools.
- Escort order, roster, slip extraction, release, payment draft, reconciliation,
  cleanup, backfill, and calculation workflows.
- Knowledge-base upload/search, BM25/RAG indexing, chat lab, learning memory,
  and user profile memory.
- Runtime coordination: service heartbeats, runtime node registry, queue
  arbitration, frontend sync state, realtime event bridge, bridge orchestration,
  and self-healing diagnostics.
- Observability endpoints, Prometheus metrics, scheduled jobs, reports, backups,
  and operational health summaries.

## High-Level Architecture

```text
WhatsApp Bridge 1 SQLite/webhook
WhatsApp Bridge 2 SQLite/webhook
Meta WhatsApp / Messenger / Facebook comments
                |
                v
        FastAPI app/main.py
                |
    identity, dedup, archive, safety gates
                |
    +-----------+--------------+--------------+
    |           |              |              |
recruitment  social daemon  legacy router  admin APIs
    |           |              |              |
restricted   social queues   drafts/send    payroll/escort
reply flow   retry/risk      workflows      dashboards
    +-----------+--------------+--------------+
                |
                v
PostgreSQL, Redis, bridge SQLite stores, Ollama, media processor
```

`app/main.py` is still the composition root and legacy route surface. It starts
the database, bridge pollers, outbound worker, scheduler, Ollama daemon, RAG
build, FPE, social auto-reply backend, runtime gateway, queue arbiter, frontend
sync, bridge orchestration, self-healer, and startup cleanup tasks.

## Main Routes And UIs

Static operator pages:

| Route | Purpose |
|---|---|
| `/dashboard` | Main operations dashboard |
| `/payroll` | Payroll/FPE transactions, employees, review, sync, cash, and income |
| `/escort-roster` | Escort roster, drafts, sync, reconciliation, calculation, export |
| `/drafts` | Draft reply review and approval |
| `/kb` | Knowledge-base management |
| `/chat-lab` or `/open-chat` | Internal chat/RAG testing |
| `/wa-chat` | WhatsApp contact, message, draft, broadcast, and group interface |

Important API groups:

| Group | Examples |
|---|---|
| Health and diagnostics | `/health`, `/health/deep`, `/api/bridges/diagnostics`, `/api/self-heal/diagnostics` |
| Webhooks | `/webhook/meta`, `/webhook/mcp1`, `/webhook/mcp2` |
| Sending | `/send/meta`, `/send/mcp1`, `/send/mcp2`, `/api/wa/send`, `/api/wa/broadcast` |
| Drafts | `/api/drafts`, `/api/drafts/{id}/approve`, `/api/wa/drafts/*` |
| Payroll/FPE | `/fpe/*`, `/payroll/*`, `/transactions/*`, `/payment/*` |
| Escort | `/escort-roster/*`, `/escort-slip/*`, `/escort/release` |
| Knowledge and chat | `/rag/*`, `/api/rag/*`, `/chat/*`, `/admin/kb/*` |
| Users and memory | `/api/users/*`, `/api/memory/*`, `/admin/memory/*` |
| Runtime and queues | `/api/runtime/nodes`, `/api/queue/dead-letters`, `/api/queue/arbiter-metrics` |
| Operations | `/scheduler/*`, `/backup/*`, `/reports/*`, `/observability/*`, `/metrics` |
| Social auto-reply | `/social-auto-reply/*` |

Most admin and mutation endpoints require `X-Internal-Key`. Never print or
commit real keys, tokens, or bridge secrets.

## Repository Layout

```text
app/
  main.py                 FastAPI composition root and legacy route surface
  config.py               Environment-backed settings
  llm.py                  Ollama/Groq/GitHub provider chain
  bridge.py               Bridge send clients and circuit breaker support
  static/                 Dashboard, payroll, roster, drafts, KB, chat UIs

modules/
  bridge_poller/          SQLite pollers, media handling, safe routing gates
  message_router/         Legacy business router and reply/draft decisions
  social_auto_reply/      Meta/social event queue, classifier, risk, retry, sender
  recruitment_ai/         Restricted-source recruitment reply generator
  fazle_payroll_engine/   Payroll/accounting ingestion, employees, reconciliation
  admin_commands/         WhatsApp admin commands and NL operations helpers
  drafts/                 Draft review API
  escort*/                Escort orders, lifecycle, roster, slips, release flows
  payment*/ payroll/      Payment drafts, corrections, payroll workflows
  rag/ knowledge_base/    Knowledge upload, retrieval, and answer generation
  scheduler/ backup/      Scheduled jobs and backup tracking
  observability/          Metrics and error summaries

shared/
  queue.py                Heartbeats and queue helpers
  queue_arbiter.py        Queue lease recovery
  runtime_gateway.py      Runtime node registry
  frontend_sync.py        UI sync state
  bridge_orchestrator.py  Bridge coordination
  self_heal.py            Runtime self-healing checks
  reply_policy.py         Shared reply safety policy
  write_router.py         Emerging canonical write boundary

db/migrations/            Schema migrations
knowledge_base/           Source knowledge documents
resources/                Generated and approved runtime resources
tests/                    Unit, integration, workflow, resilience, E2E, load tests
docs/                     API, architecture, operations, roadmap, launch docs
```

## Data Model Reality

The database is broad and not fully canonicalized. Treat the current model as
transitional.

Known parallel stores include:

| Area | Current reality |
|---|---|
| Employee identity | `wbom_employees`, `fpe_employees`, contacts, roles, and aliases can disagree |
| Escort operations | `wbom_escort_programs` and `escort_roster_entries` are related but not always perfectly projected |
| Payments | WBOM cash tables, FPE cash/income tables, admin transactions, staging rows, and payment drafts overlap |
| Messaging | Bridge SQLite, webhook paths, `wbom_whatsapp_messages`, social inbox events, draft replies, and outbound queues all hold message state |
| Knowledge/memory | File KB, uploaded KB, RAG chunks, reviewed replies, learning memory, and profile memory are separate systems |

New code should use an existing service/module boundary where one exists. Avoid
writing directly to multiple related tables from a new endpoint unless that
write path is deliberately documented and tested.

## Known Problems

The app is operational, but these are real current risks:

1. `app/main.py` remains very large and mixes transport, startup, UI serving,
   legacy business routes, and orchestration.
2. Financial, employee, escort, and message state are split across multiple
   overlapping tables and modules.
3. Some direct bridge send paths and social/legacy paths still need careful
   auditing to guarantee one delivery path, one retry policy, and one DLQ story.
4. Release settlement, payment finalization, roster projection, and WBOM/FPE
   reconciliation should remain human-reviewed until canonical write services
   and stronger idempotency are complete.
5. Historical tests and docs disagree about provider order and safe-mode values;
   always verify `app/config.py`, `app/llm.py`, and live `.env`.
6. Secrets have existed in local environment files and operational notes. Keep
   `.env` at permission `600`, avoid copying secret values into docs/logs, and
   rotate any token that may have been exposed.
7. The complete test suite may require a live or Docker test stack. A green
   `/health` endpoint is not the same thing as a green regression suite.

## Running Locally Or On The VPS

Production is managed by systemd:

```bash
sudo systemctl status fazle-core
sudo systemctl restart fazle-core
sudo journalctl -u fazle-core -n 100 --no-pager
```

Basic health check:

```bash
curl -s http://localhost:8200/health
curl -s http://localhost:8200/health/deep -H "X-Internal-Key: <key>"
```

Local development from this checkout:

```bash
cd /home/azim/core
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r tests/requirements-test.txt
uvicorn app.main:app --host 127.0.0.1 --port 8200 --reload
```

The production VPS may also have a shared virtual environment at
`/home/azim/.venv`. Use the environment that matches the running service before
debugging dependency issues.

## Tests

Common commands:

```bash
cd /home/azim/core
make smoke
make test-unit
make test-integration
make test-e2e
make test
```

The Makefile sets test defaults for API keys, bridge URLs, and test database
configuration. DB, workflow, resilience, and E2E tests may require the Docker
test stack or a prepared local service:

```bash
make test-docker-up
make test-docker
make test-docker-down
```

Use targeted tests for risky changes:

| Change area | Suggested tests |
|---|---|
| Admin commands | `tests/unit/test_admin_commands.py` |
| Draft policy | `tests/unit/test_draft_reply.py`, `tests/unit/test_reply_policy.py` |
| Recruitment | `tests/unit/test_recruitment_ai_restricted.py` |
| Payroll/FPE | `tests/test_fpe_*.py`, `tests/unit/test_accountant_payment_pipeline.py` |
| Escort | `tests/unit/test_escort_module.py`, `tests/unit/test_escort_lifecycle.py`, `tests/workflows/test_escort_payment_flow.py` |
| Runtime coordination | `tests/unit/test_phase13*.py` |
| APIs | `tests/integration/test_api.py`, `tests/e2e/test_dashboard.py` |

## Production Guardrails

1. Do not enable or broaden auto-reply behavior without checking safe-mode flags,
   social single-engine behavior, draft gates, and recent outbound queue state.
2. Keep payroll, payment, employee identity, escort assignment, and release
   settlement actions under human approval until canonical write services are
   complete.
3. Prefer queued outbound delivery for business-critical sends; direct sends
   should be treated as higher risk unless their retry behavior is proven.
4. Keep Meta/social reply behavior single-engine to avoid duplicate responses.
5. Keep `.env` private, permissioned, and out of commits, screenshots, tickets,
   and generated docs.
6. Before release work, run at least a targeted test set plus `/health/deep` and
   relevant queue/observability checks.
7. Update this README after major changes to provider order, safe-mode behavior,
   canonical write paths, or production topology.

## Related Docs

| Doc | Purpose |
|---|---|
| `docs/ARCHITECTURE.md` | Deeper system and data-flow notes |
| `docs/API.md` | HTTP endpoint reference |
| `docs/OPERATIONS.md` | Deployment, restart, backup, and troubleshooting |
| `docs/ROADMAP.md` | Batch/version history and forward work |
| `AUDIT_REPORT.md` | Current-state audit and risk inventory |
| `TESTING_STATUS.md` | Historical test validation notes |
| `payroll-escort-workflow-audit-2026-06-13.md` | Focused payroll/escort workflow review |
