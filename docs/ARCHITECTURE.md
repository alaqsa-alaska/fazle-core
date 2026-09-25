# Architecture

## High-level diagram

```
                    ┌──────────────────────────────────────┐
                    │         WhatsApp (Meta)              │
                    └────┬───────────────┬─────────────────┘
                         │               │
              webhook    │               │  bridge polling
                         ▼               ▼
                ┌────────────┐   ┌────────────┐   ┌────────────┐
                │  Meta WH   │   │ Bridge1    │   │ Bridge2    │
                │  (HTTPS)   │   │ HR :8080   │   │ OPS :8081  │
                └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
                      │                │                │
                      └────────┬───────┴────────┬───────┘
                               ▼                ▼
                      ┌────────────────────────────────────┐
                      │    fazle-core (FastAPI :8200)      │
                      │  ┌──────────────────────────────┐  │
                      │  │ message_router (intent dispatch)│
                      │  │ ├─ admin_commands               │
                      │  │ ├─ escort / escort_lifecycle    │
                      │  │ ├─ recruitment_flow             │
                      │  │ ├─ payment_workflow             │
                      │  │ ├─ rag (KB search)              │
                      │  │ └─ outbound (queue + retry)     │
                      │  └──────────────────────────────┘  │
                      │  ┌──────────────────────────────┐  │
                      │  │ scheduler (apscheduler, 8 jobs)│ │
                      │  │ rbac · backup · reports         │
                      │  │ observability (metrics + logs)  │
                      │  └──────────────────────────────┘  │
                      └─────┬──────────────────┬───────────┘
                            │                  │
                            ▼                  ▼
                    ┌──────────────┐   ┌──────────────┐
                    │ PostgreSQL15 │   │   Ollama     │
                    │  (asyncpg)   │   │ qwen2.5:1.5b │
                    └──────────────┘   └──────────────┘
```

## Components

### fazle-core (this repo)
FastAPI app on `127.0.0.1:8200`, run by `systemd` unit
`fazle-core.service` as user `azim`. Single process, async I/O, in-memory
metrics. Bind is loopback-only — public traffic terminates at nginx.

### Bridges
Two Go binaries (`whatsapp-mcp`, `whatsapp2`) that maintain WhatsApp Web
sessions and write messages into local SQLite stores. fazle-core polls
both stores every 5s via `modules/bridge_poller`.

### PostgreSQL
Primary data store (`asyncpg`). Migrations live in `db/migrations/*.sql`,
applied in order on first boot. Schemas cover: employees, drafts,
payments, payroll runs, attendance, KB, recruitment sessions, RBAC,
audit, error log, escort lifecycle.

### Ollama
Local LLM for intent classification and reply drafting. Model:
`hermes3:3b` for the configured local conversational/document path.
Fazle-core uses `app/ollama.py` with health check and the existing
`app/llm.py` installed-tag resolver; it degrades gracefully if Ollama is down.
The resolver prefers an installed Hermes-family tag and otherwise uses the
configured local model. `phi4-mini:latest` is verified installed but is not
activated as an automatic fallback. `qwen3:8b` is not installed and is no
longer an active local default or task-tier setting.

### Scheduler
APScheduler (`Asia/Dhaka`) runs 8 jobs: outbound queue drain, KB refresh,
backup rotate, RAG reindex, attendance import, error-log truncate, etc.
See `modules/scheduler.py`.

## Data flow — inbound message

1. Bridge poller picks up a new SQLite row.
2. `message_router.process_message(msg)` is called.
3. Intent classifier (`modules/intent`) labels it.
4. Routes to a workflow module (escort / recruitment / admin_commands).
5. Workflow may insert a **draft** in `fazle_drafts` (pending approval).
6. Admin sends `APPROVE 123` via WhatsApp → admin_commands marks the
   draft sent and forwards via the original bridge.
7. Every step writes to `fazle_audit` and increments observability counters.

## Hermes intelligence and context (Step 1)

The effective conversational path is:

`modules.bridge_poller.process_bridge_inbound` → `modules.message_router.process_message` → identity/role routing and conversation history → `modules.recruitment_flow.route_recruitment_message` when applicable → existing Recruitment session/turn decision and RAG/context retrieval → `shared.reply_policy.build_provider_context_package` → the shared reply policy consumed by `modules.hermes_dispatch` and the existing provider paths → policy/fact-check boundary → reply/outbound handling and persistence.

`ProviderContextPackage` is provider-neutral and is a structured view over existing context; it is not a second memory store or RAG system. It carries:

- the current message, source/channel, intent, identity, role, and relationship evidence;
- recent relevant bidirectional history and prior-answer evidence;
- retrieved knowledge as evidence blocks with `source`, `authority`, `status`, and `conflicts` metadata;
- explicit omissions and uncertainty when evidence is absent or incomplete.

The three layers remain separate:

- **Agent/skill instructions** describe how Hermes should reason, preserve continuity, use evidence, handle uncertainty, and converse naturally.
- **Business knowledge** is company fact supplied by policy/document/RAG sources; it is not permanently embedded in the agent instructions.
- **Conversation memory/context** contains turns, topic-relevant history, time/identity/relationship context, and application/session state; it is not business-policy authority.

The current legacy Recruitment source is explicitly represented at source level as `authority=source_declared`, `status=unreconciled`, and `conflicts=legacy_policy_variants`. Retrieval does not make those facts approved. Conflicting, historical, superseded, unverified, or omitted business facts remain fail-closed, while ordinary non-business conversation remains available. Provider/model choice must not change the underlying company-truth semantics.

Step 1 preserved the existing SentenceTransformer embedding path, BM25, Qdrant design/index, Recruitment session/state, identity system, and provider selection/fallback. It did not re-index vectors, change embeddings, implement fact-level policy reconciliation, or add structured topic/time-gap fields.

## Module map

| Module | Role | Notes |
|---|---|---|
| `bridge_poller` | Tail two bridge SQLite stores | 5 s tick |
| `message_router` | Central inbound dispatch | logs `[ROLE]`, `[INTENT]` |
| `admin_commands` | APPROVE / REJECT / EDIT / PAID / ADVANCE / STATUS | |
| `intent` | LLM-based label | falls back to keyword heuristic |
| `escort` / `escort_lifecycle` / `escort_slip_extractor` | Slip OCR + pay drafts | OCR semaphore = 2 |
| `recruitment` / `recruitment_flow` | Candidate funnel | session table |
| `payment_workflow` / `payment_ingest` / `payment` | Pay drafts → finalize | |
| `payroll` / `payroll_logic` | Monthly run + transition state | |
| `attendance` / `attendance_parser` | Daily attendance import | |
| `knowledge_base` / `rag` | Static rules + BM25 search | B21 |
| `outbound` | Queued send with retry | B15 |
| `scheduler` | APScheduler glue | B16 |
| `reports` | CSV / JSON exports | B17 |
| `backup` | pg_dump + rotate | B18 |
| `rbac` | Roles, per-admin API keys | B19 |
| `observability` | counters/gauges/histograms + Prom | B22 |
| `media` / `image_hash` / `voice_processor` | Inbound media handling | |
## Recruitment lifecycle and fact-level authority (Step 2)

Recruitment knowledge is now represented as an employee lifecycle: first
interest → applicant identification → role interest → eligibility → intake →
documents → interview/review → selection → training/practical learning →
probation/readiness → joining → assignment/deployment → active employment.
The existing `modules/recruitment_flow` and
`modules/recruitment_turn_decision` remain the lifecycle/session authority.

Fact authority is held in
`knowledge_base/00_governance/recruitment_fact_authority.json`, with one record
per subject. Each record carries a fact key, status, source paths, competing
values where applicable, conditions, and a safe action. The supported states
are `current_approved`, `historical`, `unverified`, `conflicting`, and
`superseded`. Only `current_approved` records have an answerable value;
similarity or source repetition cannot promote another state.

The canonical prose narrative is
`knowledge_base/04_business_rules/recruitment_lifecycle_authority.md`. It
reconciles the existing Recruitment source, employee/form policy documents,
salary and fee files, seed/migration SQL, generated KB, and lifecycle callers.
Unresolved roles/vacancies, salaries, exact fees, contact/hours, documents,
duties, training, probation, duty conditions, food/accommodation, overtime, and
leave remain in its Owner Decision Required register without selected values.

`modules/recruitment_authority` annotates existing source, Exact KB, BM25, and
hybrid RAG evidence. Relevance determines what is retrieved; fact authority
determines what may be asserted as current policy. Authority metadata survives
RAG result/citation construction and is passed as `ContextEvidence` into the
existing `ProviderContextPackage`. Direct legacy Recruitment KB replies fall
through when business facts are implicated, preventing canned text from
bypassing this boundary.

The existing `fazle_knowledge_base` admin editor remains a future update
surface, but its schema has no fact-level approval/provenance columns. Step 2
therefore adds no production migration or frontend and does not treat editing a
reply row as policy activation. Owner approval, effective dates, conditions,
and supersession history remain a future controlled editing requirement.
| `identity_brain` / `employee_verification` / `user_role` | Phone → employee resolution | |

## Auth model (B19)

- Header: `X-Internal-Key`.
- Accepts the legacy `INTERNAL_API_KEY` (env) **or** any active per-admin
  key (`fk_<urlsafe32>`, sha256 lookup against `fazle_admins.api_key_hash`).
- `/metrics` is intentionally unauthenticated for localhost Prometheus
  scraping; safe because the service binds to `127.0.0.1`.
- Webhooks (`/webhook/meta`, `/webhook/mcp1`, `/webhook/mcp2`) verify
  bridge-specific shared secrets, not `X-Internal-Key`.

## Observability (B22)

- In-process metrics registry, no external deps.
- HTTP middleware records `fazle_http_requests_total` and
  `fazle_http_request_duration_ms` keyed by route **template** (not raw
  path) for bounded cardinality.
- Histogram buckets stored per-bucket, accumulated only at Prometheus
  render time.
- Errors flow into `fazle_error_log` via `app/error_log.py:record_error()`
  and surface at `/observability/errors`.

## CI (B23)

- `.github/workflows/ci.yml` — syntax + offline tests on every push/PR.
- `scripts/run_ci.sh` — same checks plus live B19 + B21 tests when
  fazle-core is reachable on `127.0.0.1:8200`.

## Latest-approved authority and Admin Control Plane (Step 4)

The Admin UI remains part of Fazle-Core. The first control-plane vertical slice is:

`Admin UI → server-side validation/authorization → Recruitment authority service → recruitment_fact_authority.json → recruitment_authority → Exact KB/RAG evidence → ProviderContextPackage → Hermes`

For mutable policy facts, “latest” means the latest valid explicitly approved
admin update, not the newest timestamp. Save creates a draft. Approve makes the
new value `current_approved`; the previous approved value becomes
`superseded` and remains in history. Drafts, failed saves, imported documents,
applicant messages, and unverified evidence cannot override current authority.
Revision checks reject stale concurrent approvals.

The first slice is `/admin/knowledge/recruitment` with read, draft-save, and
approve actions. Its future sidebar placeholders do not imply implemented
functionality. Role Classification is intentionally a separate future concern:
identity/relationship/access classifications (for example Family or
Client-Escort) are not business/job roles (for example Escort or Security
Guard), and one contact may have multiple classifications.

The existing JSON authority register remains canonical for this slice. Writes use
an exclusive lock, temporary-file + fsync + atomic replace, and server-side RBAC;
existing `fazle_admin_audit` receives additive audit events. No DB migration or
Qdrant re-index is required for this file-backed foundation.
