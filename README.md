# Fazle Core

Fazle Core is the WhatsApp-first operations backend for Al-Aqsa Security &
Logistics Services Ltd. It combines customer and recruitment messaging,
employee/payroll operations, escort-service workflows, OCR/media processing,
admin review queues, scheduled jobs, and observability in one FastAPI service.

**Repository:** `git@github.com:arshiyaazim/studious-system.git`
**Live service:** `fazle-core.service` on port `8200`
**Current branch:** `backup/vps-core-20260612`
**Last verified:** 2026-06-13

## Current Operating Mode

The application is healthy, but it is intentionally not running as a general
customer auto-reply system.

| Setting | Live value | Effect |
|---|---|---|
| `AUTO_REPLY_ENABLED` | `false` | General replies and internal `admin_note` delivery are suppressed |
| `RECRUITMENT_AUTOREPLY_ENABLED` | `true` | Recruitment replies may bypass global safe mode |
| `AUTO_REPLY_SOURCES` | `bridge1,bridge2` | Meta inbound is persisted but not auto-replied |
| `DRAFT_CREATION_ENABLED` | `true` | Suppressed/unsafe replies are stored for review |
| `OUTBOUND_ENABLED` | `false` | Generic outbound worker is disabled |
| `PRIMARY_AI_PROVIDER` | `github_models` | General and Chat Lab replies start with GitHub Models |
| `OLLAMA_REPLY_DISABLED` | `true` | Ollama is not used for customer-facing reply fallback |

Do not describe this deployment as fully autonomous. Recruitment is the only
enabled auto-reply exception, and financial/operational actions still require
human review.

## Live Status

Verified on 2026-06-13:

- `fazle-core`, `nginx`, and `fail2ban` are active.
- `/health` returns HTTP 200 and reports PostgreSQL, bridges, outbound queue,
  disk, memory, Ollama, GitHub Models, and Groq as healthy.
- Bridge 1 and Bridge 2 heartbeats are current.
- Meta Cloud API heartbeat is healthy; it was about two hours old at audit time.
- 14 Docker infrastructure containers are running.
- Disk usage is about 45%; approximately 10 GiB RAM is available.
- Swap use is about 125 MiB of 3 GiB.
- `location-where` is online, but currently shows repeated `401` requests,
  9 lifetime restarts, and a sampled CPU value of 200%.

See [audit-report-2026-06-10.md](audit-report-2026-06-10.md) for the full
current-state review.

## Architecture

```text
WhatsApp Bridge 1 / Bridge 2 / Meta Cloud API
                    |
          webhook + bridge pollers
                    |
        normalize phone and identify role
                    |
             message_router
       +------------+-------------+
       |            |             |
 recruitment   operations     general reply
       |       payroll/escort       |
       |            |               |
 restricted AI   review/action   GitHub -> Groq
       |            |          -> holding message
       +------------+---------------+
                    |
       direct bridge send or draft queue

PostgreSQL: operational, accounting, memory, audit, queue data
Redis: queues/cache/dedup support
Ollama: local intent/RAG/learning workloads
Media processor: OCR and document field extraction
Prometheus/Grafana/Loki/OpenTelemetry: observability
```

`app/main.py` is the composition root and currently remains a large monolith.
It mounts routers, starts pollers/workers/scheduler/RAG, serves the admin pages,
and exposes health, webhook, operations, RAG, memory, and observability APIs.

## AI and Reply Workflow

### Intent classification

Free-form intent classification uses:

```text
Groq llama-3.1-8b-instant
  -> GitHub Models openai/gpt-4o-mini
  -> local Ollama qwen2.5:3b
  -> unknown
```

Deterministic rules still run in several modules. The bridge safety gate and
the router can therefore disagree about intent.

### Recruitment replies

Recruitment replies use:

```text
Groq -> GitHub Models -> fixed safe fallback
```

The only approved recruitment source is:

```text
resources/ops/recruitment_source_of_truth.txt
```

`modules/recruitment_ai` deliberately ignores DB knowledge, conversation
history, contact context, and other resource files. Fee questions receive a
fixed policy response without a specific amount.

### General replies

General replies currently use:

```text
GitHub Models -> Groq -> polite holding message
```

Ollama reply generation is disabled for customer-facing general messages.

### Sending and review

Bridge 1 and Bridge 2 replies pass through prompt-injection checks, financial
draft gates, risk/draft rules, loop detection, poison/length guards, and safe
mode checks. Admin-approved drafts can be enqueued for delivery even when
general auto-reply is disabled.

Important limitation: direct bridge auto-sends do not yet have a durable
retry/DLQ path, and internal operational `admin_note` notifications currently
depend on `AUTO_REPLY_ENABLED`.

## Main Business Domains

| Domain | Main modules | Current state |
|---|---|---|
| WhatsApp ingestion | `bridge_poller`, `message_router`, `message_archive` | Working; direct-send reliability needs improvement |
| Recruitment | `recruitment_ai`, `recruitment_flow`, `role_classifier` | Restricted-source auto-reply enabled |
| Employee operations | `admin_employees`, `employee_verification`, `attendance` | Functional but employee identities are split |
| Payroll/accounting | `fazle_payroll_engine`, `payroll`, `payment_*`, `admin_transactions` | Feature-rich; WBOM/FPE write paths are fragmented |
| Escort operations | `escort`, `escort_lifecycle`, `escort_roster`, `escort_slip_extractor` | Order/roster UI exists; release/payment paths have schema defects |
| Knowledge/memory | `rag`, `knowledge_base`, `memory_extractor`, `reviewed_reply_memory` | BM25 and learning tables active; Qdrant unused |
| Operations | `scheduler`, `backup`, `reports`, `observability`, `rbac` | Running; tests and incident noise require work |

## Data Model Reality

The live database has 229 application tables, including 208 in `public`.
Several domains currently have parallel stores:

| Store | Purpose | Live rows |
|---|---|---:|
| `wbom_employees` | Operational employee records | 171 |
| `fpe_employees` | Payroll-engine identities | 351 |
| `wbom_escort_programs` | Operational escort programs | 319 |
| `escort_roster_entries` | Roster UI projection | 140 |
| `fazle_payment_drafts` | Payment approval queue | 47 |
| `agent.incidents` | Guardian incidents | 8,812 |

Only 98 FPE employees are linked to WBOM, and 179 escort programs are absent
from the roster projection. Treat the current model as transitional: do not
write directly to multiple related tables from new code.

## Frontends

| Route | Purpose |
|---|---|
| `/dashboard` | Operations/admin dashboard |
| `/payroll` | FPE payroll, transactions, employees, review, and sync |
| `/escort-roster` | Escort roster, filters, details, sync, and calculation |
| `/drafts` | Reply review and approval |
| `/kb` | Knowledge-base management |

Missing high-value operational pages include escort-order review, assignment
availability, advance verification, release settlement approval, payment-draft
reconciliation, and WBOM/FPE reconciliation.

## Important API Groups

- Health and diagnostics: `/health`, `/health/deep`, `/api/bridges/diagnostics`
- Webhooks: `/webhook/meta`, `/webhook/mcp1`, `/webhook/mcp2`
- Sending: `/send/meta`, `/send/mcp1`, `/send/mcp2`
- Payroll/payment: `/payroll/*`, `/payment/*`, mounted FPE/admin routers
- Escort: `/escort/release`, `/escort-slip/*`, mounted roster router
- Scheduler/backup/reports: `/scheduler/*`, `/backup/*`, `/reports/*`
- RAG/chat/memory: `/rag/*`, `/chat/*`, `/api/users/*`, `/api/memory/*`
- Observability: `/metrics`, `/observability/*`

Most administrative endpoints require the internal API key. Never commit or
print that key.

## Repository Layout

```text
app/
  main.py                 FastAPI composition root and legacy route surface
  config.py               Environment-backed settings
  llm.py                  Unified AI provider routing
  static/                 Dashboard, payroll, roster, drafts, and KB pages

modules/
  bridge_poller/          Bridge polling, safety gates, direct send
  message_router/         Business routing
  recruitment_ai/         Restricted-source recruitment generation
  fazle_payroll_engine/   Immutable WhatsApp accounting pipeline
  escort*/                Escort order, lifecycle, roster, OCR
  payment*/ payroll/      Payment and payroll workflows
  rag/ knowledge_base/    Retrieval and KB
  scheduler/ backup/      Scheduled operations

shared/                   Cross-domain policies and infrastructure helpers
db/migrations/            Core schema migrations
resources/ops/            Approved operational policy resources
scripts/                  Maintenance, recovery, and batch verification tools
tests/                    Unit, integration, DB, workflow, resilience, and E2E tests
```

## Running and Verification

Production is managed by systemd:

```bash
sudo systemctl status fazle-core
sudo systemctl restart fazle-core
curl -s http://localhost:8200/health | python3 -m json.tool
sudo journalctl -u fazle-core -n 100 --no-pager
```

Use the repository virtual environment at `/home/azim/.venv`.

```bash
cd /home/azim/core
/home/azim/.venv/bin/pytest -q
```

The complete suite does not currently pass:

- Full collection fails when `DATABASE_URL` is absent for script-style tests.
- Unit execution includes DB-dependent setup failures in restricted/no-test-DB
  environments.
- Confirmed non-environment failures include stale backup tests, missing
  `context_memory`, write-router API drift, parser model/test drift, and an OCR
  timeout.

Do not use a green health endpoint as a substitute for a green regression suite.

## Production Guardrails

1. Keep `.env` permission at `600`; never commit secrets.
2. Keep financial, employee identity, assignment, and release actions under
   human approval until canonical write paths exist.
3. Do not enable Meta auto-reply until its path has the same safety and durable
   delivery controls as Bridge 1/2.
4. Separate internal operations notifications from customer auto-reply before
   relying on WhatsApp-driven escort/payment workflows.
5. Fix release schema mismatches before enabling automated settlement.
6. Reconcile WBOM/FPE employees and roster projection before treating payroll
   or escort totals as authoritative.

## Priority Work

1. Fix release/payment schema mismatches and make release settlement atomic.
2. Introduce durable outbound retry/DLQ for every channel.
3. Separate internal notification enablement from customer auto-reply.
4. Define canonical employee, escort, and financial write services.
5. Reconcile employee identities, payment records, and roster projection.
6. Restore a deterministic, isolated, passing test suite.
7. Diagnose `location-where` repeated `401` traffic and current high CPU.

Detailed findings and the remediation roadmap are maintained in
[audit-report-2026-06-10.md](audit-report-2026-06-10.md). The focused payroll
and escort review is in
[payroll-escort-workflow-audit-2026-06-13.md](payroll-escort-workflow-audit-2026-06-13.md).
