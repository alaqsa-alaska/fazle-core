# Fazle Core

> **New to this repo, or an AI agent picking up cold?** Read [`SYSTEM_ATLAS.md`](SYSTEM_ATLAS.md) first — it's the single canonical entry point and document map. This README covers the same ground in narrative form.

Fazle Core is the production WhatsApp-first operations backend for Al-Aqsa
Security and Logistics Services Ltd. It is a FastAPI service that ingests
messages from WhatsApp bridge databases, bridge webhooks, Meta/Facebook
webhooks, and admin UIs, then routes them into recruitment, payroll, escort,
payment, knowledge-base, memory, and observability workflows.

This README describes the app as it exists in this checkout, not as a clean
target architecture. The system is live, useful, and feature-rich, but several
domains are still transitional and must stay under human review.

## Product Overview

**What it is:** a bilingual (Bangla/English) WhatsApp-first operations platform
that runs Al-Aqsa Security & Logistics Services Ltd's day-to-day business —
recruitment, employee/guard payroll, escort (vessel security) service
dispatch and duty tracking, client and employee communication, and a growing
AI-driven console over that data. It is not a generic chatbot framework; every
workflow is specific to this company's business.

**Who uses it:**
- **The Owner/Admin** — runs the business through WhatsApp admin commands and
  the web dashboards (`/dashboard`, `/payroll`, `/escort-roster`), approving
  payments, managing employees, and reviewing AI-drafted replies.
- **HR/office assistants** — restricted "operator" role access to the payroll
  dashboard for income/cash entry (see `HR_OFFICER_SETUP.md`).
- **The accountant** — receives payment instructions via WhatsApp and
  confirms/executes them; some flows treat accountant replies as
  notification-only, not a data-entry path (see `BUSINESS-CONSTITUTION.md`
  Article I).
- **Employees/guards** — message in for attendance, salary/advance requests,
  and duty-related communication; receive AI-drafted or auto-sent replies
  depending on role and safe-mode configuration.
- **Recruitment candidates** — message in and go through an automated intake
  funnel with a restricted AI reply flow.
- **Corporate escort/vessel clients** — send vessel escort orders via
  WhatsApp; receive drafted (never auto-sent) confirmations.

**Main business domains:** recruitment, payroll/cash disbursement (single-entry
ledger, not yet full accounting — see `docs/DEVELOPER_GUIDE.md` §E), escort
(vessel security) order intake/dispatch/duty tracking, WhatsApp/Meta
communication and reply automation, an internal knowledge base with
RAG-backed AI answers, and (in progress) an AI Operating Console for
natural-language queries over business data.

**Problems it solves:** replaces what would otherwise be entirely manual
WhatsApp-based coordination (an admin manually reading and replying to every
message) with AI-assisted, human-supervised automation — while keeping every
financial action behind an explicit approval step and an immutable audit
trail. It is explicitly **not yet** a complete business operating system: it
has no client billing/invoicing, no guard dispatch conflict-checking, and no
double-entry accounting — see `docs/DEVELOPER_GUIDE.md` §J and
`SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` for the full gap analysis and
roadmap toward that goal.

**For full developer documentation** (runtime/infra detail, every frontend
page's APIs/forms/search fields, module-by-module risk map, finance/employee/
escort/AI code-path tracing, and known strengths/weaknesses/roadmap), see
[`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md) and
[`docs/MODULE_MAP.md`](docs/MODULE_MAP.md).

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
| Branch in this checkout | `main` |
| Last README review | 2026-07-13 |

### Ports & Key Services

| Service | Port | Notes |
|---|---|---|
| `fazle-core.service` (this app) | `8200` internal, proxied publicly via Nginx | `APP_PORT` in `.env` |
| `fazle-agent.service` | `8300`, `127.0.0.1` only | Separate systemd service, `/home/azim/agent` |
| Media processor (OCR/ASR sidecar) | `8090`, `127.0.0.1` only | `/home/azim/shared/media/media-processor/server.py`, not part of this repo |
| PostgreSQL (`ai-postgres` container) | `5432` | Shared with `fazle-agent` and `open-webui`; resolved via `DATABASE_URL_TEMPLATE` + `generate_runtime_services_env.sh` |
| Qdrant (vector search) | `6333`/`6334` | `fazle_rag_chunks` collection |
| Redis | `6379` | `ai-redis` container |
| Ollama (local LLM) | `11434` | Models: `phi4-mini` (default), `qwen3:8b`, plus cloud-proxied `kimi`/`glm`/`minimax` models |

### RBAC Role Hierarchy

Source of truth is the live `fazle_roles` table, not a hardcoded Python dict:

| Level | Role | Scope |
|---|---|---|
| 10 | `viewer` | Read-only: status/list/report/backup-status |
| 20 | `office_assistant` | View attendance list/own records; create attendance draft entries only — no review/approve/edit/salary |
| 30 | `operator` | Approve/reject drafts, escort confirm, mark paid |
| 50 | `accountant` | Payroll + payment ingest + cash reconciliation |
| 70 | `admin` | Backup now, scheduler run, user management |
| 100 | `superadmin` | Full control including role assignment + delete |

All 48 `fazle_payroll_engine` API endpoints are individually tiered by this hierarchy (audited 2026-07-11), not gated by a blanket "any admin key" check.

### Auth Mechanism

- **Header:** `X-Internal-Key: <key>` on every admin/mutation endpoint — either the master `INTERNAL_API_KEY` (`.env`, rotated 2026-07-12) or an active admin's individually-issued key (`fazle_admins`, checked via `modules.rbac.get_admin_by_api_key()`).
- **Frontend storage:** each dashboard page stores its key in `localStorage` under its own key name — **4 different names across 7 pages, not unified**: `fazle_api_key` (`dashboard.html`, `open-chat.html`, `wa_chat.html`), `fpe_api_key` (`payroll.html`, `escort-roster.html`), `drafts_api_key` (`drafts.html`), `kb_api_key` (`kb.html`). See `app/static/shared-auth-fetch.js` (added 2026-07-13) for the one page (`escort-roster.html`) migrated to a shared fetch helper so far — it kept its own `fpe_api_key` name/variable, only the header-building/error-parsing logic moved.
- There is no separate username/password login — API key only.

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

## Deterministic Hardening Update (2026-07-05)

- Escort duplicate prevention is now enforced at three layers:
   - Escort confirmation code path dedupe in `modules/escort`
   - Roster/admin audit + cleanup endpoints in `modules/escort_roster`
   - DB schema unique index migration `db/migrations/025_escort_confirmed_exact_unique.sql`
- Escort roster edit/delete now supports operator pending approval flow with
  migration `db/migrations/026_escort_roster_change_requests.sql`.
- Startup and periodic maintenance now run exact confirmed duplicate cleanup
   (no AI-dependent step required).

## Deterministic Hardening Update (2026-07-06)

- Payroll false-positive transaction guardrails were strengthened in the FPE path:
   - Historical sync now honors explicit target JID lists instead of broad bridge-wide ingestion.
   - Ingestion enqueue now requires authorized payroll sender/source conditions.
   - Parser worker prefilter blocks review-only/approval-marker traffic from payment parsing.
   - Parser human-name validation rejects approval/review artifacts as candidate names.
- Ollama power-path controls were wired to config-backed safe bounds:
   - Task-tier model defaults are now resolved via `modules/ai_console/model_router.py` and settings.
   - Inference/chat semaphores and generation options in `app/ollama.py` are config-bounded.
   - Shared Ollama daemon HTTP pool limits in `app/ollama_daemon.py` are config-driven.
- Recruitment routing was unified to a single canonical decision path:
   - `modules/recruitment_flow.route_recruitment_message(...)` now owns eligibility + reply routing.
   - `modules/message_router` uses canonical recruitment routing for candidate/new-lead/unknown paths.
   - `modules/conversation_layer/recruitment.py` keeps shadow naming only as a backward-compatible alias.
- Chat deterministic DB-contract tests were migrated and stabilized:
   - `tests/unit/test_chat_exact_db_tools.py` now asserts current `admin_chat_lab` orchestrator contract.
   - Test-only stubs suppress background persistence task teardown noise for this file.

Focused verification evidence:

- `pytest -q tests/unit/test_chat_exact_db_tools.py`
   - Result: 4 passed
- `pytest -q tests/unit/test_chat_exact_db_tools.py tests/unit/test_chat_message_smoke.py tests/unit/test_phase8a_chat_integration.py tests/unit/test_phase8c_structured_evidence.py tests/unit/test_recruitment_ai_restricted.py tests/unit/test_message_router_identity_seed.py -ra`
   - Result: 27 passed, no async teardown warnings in this run

## Deterministic Hardening Update (2026-07-13)

- **Deployment restart gate is now implemented**, not just designed —
  `scripts/restart_gate.sh` runs all 9 checks from `docs/DEPLOYMENT_RESTART_GATE.md`
  (working-tree cleanliness, secret scan, no-new-xfail, targeted tests, migration
  inventory/path enforcement, `.env` critical-flag ack, backup freshness,
  pre/post-restart health). Manual script, not wired into `ExecStartPre` (Owner
  decision) — run `scripts/restart_gate.sh --restart` before every production
  restart instead of a bare `systemctl restart`.
- **Production Postgres password rotated** (2026-07-12) after the new secret
  scanner found the real superuser password hardcoded in `Makefile` and
  `tests/conftest.py` (the `Makefile` copy had been on GitHub since ~2026-05-30).
  All 3 real consumers (`fazle-core.service`, `fazle-agent.service`, the
  `open-webui` container) updated; test fixtures now require `TEST_DB_PASSWORD`.
- **Client billing is now feature-complete** (Phase 2B Commit D): payment
  recording, outstanding-balance view, mark-paid action, audit log
  (`wbom_billing_payment_log`), an AI read-only tool, and a dashboard summary.
  Bills, once generated, can now be tracked through to payment.
- **RAG rebuild no longer holds the search lock for 15-36 minutes.** The Qdrant
  embedding phase moved outside `_IDX.lock` (guards itself with a separate lock
  instead), and unchanged chunks are skipped via a content-hash comparison
  instead of always re-encoding the full 8,878-chunk corpus.
- **e2e/non-e2e test isolation is mechanically enforced**, not just documented —
  a `tests/conftest.py` collection-time guard hard-fails (exit 4) any pytest
  invocation that mixes `tests/e2e/` with anything else.
- RBAC's plain "valid key or not" check (`_require_api_key`) — previously
  copy-pasted across 5 route files — now lives in one place, `shared/auth_deps.py`.

## AI Operating Console V1 — Stages A-C (2026-07-19)

Owner-authorized voice/mobile stabilization of `open-chat.html`, run in parallel with
(not blocked on) the Phase 1/2/3 roadmap in `docs/NEXT_IMPLEMENTATION_QUEUE.md`.

- **`POST /chat/voice`** (new) — multipart voice upload → transcription. Auth-gated the
  same way as `/chat/message` (`require_api_key`). Accepts `audio` (webm/ogg/oga/wav/
  m4a/mp3, 15MB max, ~2 min max), `language` (`auto`/`bn`/`en`, UI hint only — the
  underlying transcribe call does not take a language parameter, so this does not steer
  recognition), `duration_ms` (client-measured, informational). Response:
  `{transcript, language, confidence, confident, duration_ms, editable, warning}`.
  Reuses `modules.voice_processor.process_voice()` — the same engine WhatsApp voice
  notes use — with an additive `skip_intent=True` flag so this route never invokes the
  intent-classification LLM call itself (default `skip_intent=False` preserves existing
  WhatsApp behavior exactly). Audio is written to `resources/uploads/voice/` for the
  duration of transcription only and always deleted in a `finally` block.
- **The transcript is never auto-sent.** It fills an editable review box in the
  composer; the user must explicitly click "Use Transcript" then "Send" — this goes
  through the normal, unmodified `/chat/message` path.
- `/chat/message`'s old `[voice: ...]` placeholder-rejection branch is removed (it had
  a latent `NameError` bug and is obsolete now that voice produces real transcripts).
  `/chat/message`'s contract, auth, model selection, and provider routing are otherwise
  unchanged.
- Frontend (`app/static/open-chat.html`): explicit voice state machine (idle →
  recording → uploading_audio → transcribing → transcript_ready → error) guards
  against concurrent recordings, duplicate sends, and a stale transcript from a
  superseded recording. "Attach File"/"Attach Image" are disabled with a "coming in a
  later stage" label rather than offering non-functional local-only preview. Mobile:
  `100dvh` viewport (keyboard-safe), 44px minimum touch targets under 980px width.
- **Not done this pass** (deferred, matches the Owner's Stage A-C-only scope): file/
  image upload and OCR into chat, business read-only tool registry expansion, response
  streaming, any new conversation-persistence schema, General Ledger tools, any AI
  write capability. See `docs/NEXT_IMPLEMENTATION_QUEUE.md` → "AI Operating Console V1"
  for the full stage-by-stage record and test inventory.

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

## Troubleshooting Employee Edit HTTP 500

Employee edit is a production-critical write path (`wbom_employees`, bridged to
`fpe_employees`) — follow this process rather than guessing at a fix. Do not
change any employee-edit code until step 7 has produced a real traceback.

1. **Record the employee ID** (`wbom_employees.employee_id`, and `fpe_employees.id`
   if known) from the failing request.
2. **Preserve the submitted frontend payload** — copy the exact JSON body from
   DevTools/Network before retrying anything; a retry can mask what caused the
   original failure.
3. **Record timestamp (UTC) and the acting admin user** (from the RBAC session /
   API key used) so the server-side trace can be located precisely.
4. **Inspect the browser Network tab response** — capture the raw HTTP 500 body;
   FastAPI error responses often include enough detail to skip several steps
   below.
5. **Inspect `fazle-core.service` logs around the timestamp**:
   ```
   sudo journalctl -u fazle-core.service --since "<ts-5min>" --until "<ts+2min>"
   tail -n 200 logs/fazle-core-error.log
   ```
6. **Capture the full Python traceback** — do not summarize it; paste it
   verbatim into the incident/session note.
7. **Identify the SQL/constraint error**, if any — look for `asyncpg` exception
   text (`UniqueViolationError`, `ForeignKeyViolationError`,
   `CheckViolationError`, etc.) inside the traceback.
8. **Compare the request schema with the live table** — `wbom_employees` and
   `fpe_employees` are two separate, bridged identity systems (see
   `.claude/CLAUDE.md`); check both, and check
   `knowledge_base/06_developer_system/database_rules.md` Domain 2 (EMPLOYEE)
   for the current canonical columns rather than assuming the schema matches
   an older doc or `tests/conftest.py`'s stub — that stub has drifted from
   production before.
9. **Reproduce using a safe test employee** — never reproduce against a real
   employee record; create a disposable test row (or use an existing
   designated test employee if one exists) and repeat the exact payload from
   step 2.
10. **Classify the failure**: frontend (bad payload shape), validation
    (rejected before DB), DB (constraint/schema mismatch), authentication/RBAC
    (role below required threshold — see `fazle_roles` hierarchy), or
    transient (DB connection/pool exhaustion, timeout).
11. **Add a regression test before fixing** — a failing test that reproduces
    the traceback, under the appropriate suite in `tests/` (see `Makefile`
    targets for `test-unit` / `test-integration`).
12. **Verify the fix through both the API and the browser** — a passing test
    is necessary but not sufficient; confirm the actual edit form saves
    correctly end-to-end.
13. **Document the rollback** — note the commit hash, and confirm
    `scripts/restart_gate.sh --check-only` passes before any restart; use
    `scripts/restart_gate.sh --restart` (never a bare `systemctl restart`) if
    a restart is required to pick up the fix.

**Do not invent a fix without a reproduced traceback.** If steps 1-9 don't
produce one (e.g. the failure is intermittent), document what was observed
and escalate rather than guessing at a change to a production-critical path.

## Related Docs

| Doc | Purpose |
|---|---|
| `docs/DEVELOPER_GUIDE.md` | Runtime/infra detail, frontend page APIs/forms/search fields, finance/employee/escort/AI code-flow tracing, strengths/weaknesses/roadmap (2026-07-09) |
| `docs/MODULE_MAP.md` | Module-by-module purpose/APIs/tables/dependencies/risk level for 26 core backend modules (2026-07-09) |
| `docs/NEXT_IMPLEMENTATION_QUEUE.md` | Prioritized queue of what to work on next, Tier 0 through Phase 3. As of 2026-07-13: P0/P1/P2 and 4/5 Tier 2 items done; only Phase 2 (dispatch engine, GL) and Phase 3 (AI console unification) remain, both deliberately not started — large, undesigned business systems needing an Owner scope conversation first |
| `docs/PRODUCTION_CERTIFICATION_v1.0.md` | Phase 1.5 evidence-based production certification (2026-07-09) — is this actually ready for Phase 2? |
| `docs/PHASE2_ENTRY_GATE.md` | The GO/NO-GO checklist for Phase 2a (dispatch) and Phase 2b (billing/GL) |
| `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` | Second-highest-priority doc after the Constitution — the single source of truth for business rules, canonical naming (e.g. which of 7 phone-column names means what), and known formula/status conflicts |
| `knowledge_base/06_developer_system/writer_authority.md` | All payroll/roster/payment writer functions — destination table, trigger, transaction/locking, financial authority, audit, idempotency (2026-07-19) |
| `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md` | Full 10-report architecture/risk/gap audit and consolidation roadmap |
| `docs/ARCHITECTURE.md` | Deeper system and data-flow notes |
| `docs/API.md` | HTTP endpoint reference |
| `docs/OPERATIONS.md` | Deployment, restart, backup, and troubleshooting |
| `docs/ROADMAP.md` | Batch/version history and forward work |
| `AUDIT_REPORT.md` | Current-state audit and risk inventory (⚠️ stale, see file header) |
| `TESTING_STATUS.md` | Historical test validation notes (⚠️ stale, see file header) |
| `payroll-escort-workflow-audit-2026-06-13.md` | Focused payroll/escort workflow review |

## Phone Number Normalization (2026-07-01)

All Bangladeshi phone number normalization uses a **single source of truth**:

```python
from modules.fazle_payroll_engine.normalizer import normalize_bd_phone
```

**Canonical storage format:** `01XXXXXXXXX` (11 digits, starts with `01`)

**Cross-domain helpers:**

| Function | Purpose |
|----------|---------|
| `normalize_bd_phone(raw)` | Any input → `01XXXXXXXXX` or `None` |
| `to_880(canonical_11)` | `01XXXXXXXXX` → `8801XXXXXXXXX` (for WBOM/RBAC) |
| `from_880(canonical_13)` | `8801XXXXXXXXX` → `01XXXXXXXXX` (for FPE) |
| `phone_key9(phone)` | Last 9 digits — cross-domain match key |
| `phones_match(a, b)` | Compare two phones by last 9 digits |

**DB storage by domain:**

| Domain | Format | Tables |
|--------|--------|--------|
| FPE (payroll) | `01XXXXXXXXX` | `fpe_employees`, `fpe_cash_transactions`, `fpe_income_transactions` |
| WBOM (operations) | `8801XXXXXXXXX` | `wbom_employees`, `wbom_contacts` |
| RBAC (auth) | `8801XXXXXXXXX` | `user_profiles` |

**Cross-domain lookups** must match on `phone_key9()` (last 9 digits), not exact string equality.

See: `knowledge_base/06_developer_system/phone_normalization_rules.md`
