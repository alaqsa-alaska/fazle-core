# Fazle Payroll Engine (FPE) — System Overview

**Article Type:** Developer System Reference
**Visibility:** Admin / Developer / Superadmin
**Source Module:** `modules/fazle_payroll_engine/`
**Production Status:** ACTIVE — started on every `fazle-core.service` startup
**Wave:** Wave-2A (initial) | Wave-2B (enriched) | 2026-06-22
**Traceability:** PKCA Report 05, PKMA Report 13, PKMA Report 17, PKMA Report 20

---

## Visibility and AI Exposure Rules

| Content | Visibility | AI May Expose? |
|---|---|---|
| FPE exists and processes payments | Admin | Admin only — confirm to admin |
| Worker names, count, intervals | Developer | No — developer only |
| Transaction amounts or ledger entries | Restricted | Never |
| Employee payment history | Restricted | Never |
| `fpe_employees` or `fpe_cash_transactions` table existence | Developer | Never expose table names |
| API routes (`/api/fpe/...`) | Developer | Never expose to candidates/employees |
| Zero-loss unmatched queue | Admin | Summarize count to admin; no details |
| FPE vs core payroll distinction | Developer | Never |

---

## Purpose

The Fazle Payroll Engine (FPE) is the financial processing subsystem of the platform. It ingests WhatsApp messages from the accountant-owner conversation, parses payment records (bKash, Nagad, cash, escort duty payments), matches them to employees, and writes verified transactions to an immutable accounting ledger.

FPE is distinct from the core payroll module (`modules/payroll`). The core payroll module manages the employee salary cycle (draft → reviewed → approved → locked → paid). FPE ingests raw message data to build the underlying transaction record that the salary cycle operates on.

**FPE answers the question:** "What was actually paid to whom, when, and via what method?"
**Core payroll answers the question:** "Has this payroll run been reviewed and approved by management?"

---

## Scope

FPE handles:
- Parsing payment messages from the accountant-owner WhatsApp conversation
- Employee matching and auto-creation
- Immutable transaction ledger maintenance
- Historical backfill from bridge SQLite stores
- Review queue for unmatched or ambiguous payments
- Admin API for transaction review, reversal, and reconciliation

FPE does NOT handle:
- The main salary cycle (draft/review/approve states) — that is `modules/payroll`
- Employee attendance — that is `modules/attendance`
- Escort program state management — that is `modules/escort_lifecycle`
- WhatsApp message routing for employees/admins — that is `app/message_router`

---

## Architecture

```
Bridge SQLite stores
  └── historical_sync_worker ──┐
  └── gap_scan_worker ─────────┤
                                ↓
Bridge webhooks ────────────> POST /api/fpe/ingest
                                ↓
                        fpe_wa_messages (immutable)
                                ↓
                        fpe_message_processing_state (FSM)
                                ↓
                        message_processor_worker
                         ├── parse_message()
                         ├── ai_enhance_parse() [if confidence < 0.7]
                         └── fpe_parser_results
                                ↓
                        accounting_worker
                         ├── validate_for_accounting()
                         ├── match_or_create_employee()
                         └── create_transaction() → fpe_cash_transactions (immutable)
                                ↓
                        fpe_employee_ledger (running totals per employee per month)
```

---

## Startup Lifecycle

**Entry point:** `modules/fazle_payroll_engine/__init__.py`

1. `start_fpe(chat_jids)` is called from `app/main.py` lifespan on startup.
2. All 9 migration files (`migrations/001_fpe_schema.sql` → `009_stabilization.sql`) are run in alphabetical order using idempotent SQL (`IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
3. Schema is ready after migrations complete.
4. Five background asyncio tasks are started via `start_workers()`.

**Shutdown:** `stop_fpe()` cancels all 5 workers gracefully on `fazle-core.service` stop.

**Migration policy:** Add `NNN_description.sql`. Never DROP columns. Never rename columns. Restart service to apply.

---

## The 5 Workers

### Worker 1 — message_processor_worker (`fpe_msg_processor`)

**Status:** ACTIVE — core pipeline worker
**Poll interval:** 3 seconds
**Batch size:** 20 messages per tick

**What it does:**
1. Polls `fpe_message_processing_state` for rows with `status = 'pending'`.
2. Marks the row as `parsing`.
3. Calls `parse_message(content, msg_date)` to classify and extract payment data.
4. If parser confidence < 0.7 AND message has content: calls `ai_enhance_parse()` (Ollama) to recover the parse.
5. Determines next status based on `message_type`:
   - `payment` or `escort_payment` → `parsed` (proceeds to accounting_worker)
   - `cash_command` or `income_command` → checks sender authorization → `parsed` if authorized, `skipped` if not
   - All others (balance_summary, other) → `skipped`; logged to `fpe_unmatched_messages` for admin review
6. Non-payment messages from non-owner senders (`is_from_me=False`, type=`other`) → `skipped` immediately.

**Authorization check for commands:**
- `cash_command`: sender phone must be in `settings.fpe_cash_authorized_phone_list`
- `income_command`: sender phone must be in `settings.fpe_income_authorized_phone_list`
- Unauthorized senders → `skipped` silently (no error reply)

---

### Worker 2 — accounting_worker (`fpe_accounting`)

**Status:** ACTIVE — creates the immutable ledger
**Poll interval:** 5 seconds (3s base + 2s offset from parser)
**Batch size:** 20 messages per tick

**What it does:**
1. Polls for `fpe_message_processing_state` rows with `status = 'parsed'`.
2. Joins `fpe_parser_results` to get parsed data.
3. Calls `validate_for_accounting(msg_type, pdata)` — type-aware validation (see `validation.py MESSAGE_RULES`).
4. On validation failure → stores to `fpe_unmatched_messages`, marks `skipped`.

**Per message_type behavior:**

| Message Type | Behavior |
|---|---|
| `payment` | Requires `is_from_me=True`. Calls `match_or_create_employee()`. Creates transaction in `fpe_cash_transactions`. |
| `escort_payment` | Parses multi-employee list (NAME=AMOUNT/ per line). Creates one `fazle_payment_drafts` row per entry. Links to escort program if duty_date matches. |
| `cash_command` | Employee MUST already exist in `fpe_employees` (no auto-create). If not found: sends WhatsApp error reply to the source chat JID. Creates salary transaction. |
| `income_command` | Auto-creates employee if not found. Creates income transaction via `create_income_transaction()`. |

**Zero-loss invariant:** If money is detected but cannot be matched to an employee, the detected data (amount, phone, name, method, date, confidence) is stored in `fpe_unmatched_messages`. The ledger is NEVER touched. An admin must promote the unmatched row to a verified transaction.

**Prometheus gauges updated on each tick:**
- `fpe_pending_review_count` — rows in `fpe_unmatched_messages` with `review_status='pending'`
- `fpe_dlq_count` — messages with `status='failed'` and `attempts >= MAX_ATTEMPTS (5)`

---

### Worker 3 — historical_sync_loop (`fpe_hsync`)

**Status:** ACTIVE — required for financial completeness
**Poll interval:** `FPE_HSYNC_INTERVAL_S` env (default 15 seconds)
**Batch size:** 200 messages per SQLite fetch

**What it does:**
- Reads bridge SQLite stores directly from disk (NOT the WhatsApp API):
  - Bridge1: `/home/azim/whatsapp1/store/messages.db`
  - Bridge2: `/home/azim/whatsapp2/store/messages.db`
- Loads LID resolution map from `whatsapp.db` (LID → phone number)
- Processes ALL messages from the target chat JIDs (both `is_from_me=True` and `is_from_me=False`)
- Advances by `last_timestamp` per checkpoint stored in `fpe_sync_checkpoints`
- Does NOT send any replies — pure read/ingest into `fpe_wa_messages`

**Target JIDs:** Configured via `FPE_SYNC_CHAT_JIDS` env. Default includes owner→accountant conversation JID for Bridge2 (8801880446111 → accountant).

**`BRIDGE1_INGEST_ALL_DMS=true`:** Enumerates ALL individual DM chat JIDs from Bridge1 SQLite instead of only the configured JIDs.

**Purpose:** Provides financial completeness. The main bridge poller only sees live messages. Historical sync recovers all payment messages from before FPE was installed, and fills any gaps during bridge downtime.

---

### Worker 4 — gap_scan_loop (`fpe_gapscan`)

**Status:** ACTIVE — safety net for missed messages
**Poll interval:** 300 seconds (5 minutes)
**Limit:** 5,000 IDs compared per chat per pass

**What it does:**
- Computes ID-based diff: `set(SQLite message IDs for chat JID) - set(fpe_wa_messages.wa_message_id for that source + JID)`
- Ingests any IDs found in SQLite but missing from FPE via the standard `ingest_message()` pipeline
- Catches messages that `historical_sync_loop` missed due to out-of-order timestamps, clock skew, or replay from WhatsApp servers

**Why this exists:** `historical_sync_loop` advances by timestamp. A message written to SQLite out of timestamp order (e.g., WhatsApp server replay) is invisible to the timestamp-based advance. `gap_scan_loop` uses IDs (which are monotonic) as the diff key instead.

---

### Worker 5 — bridge_health_loop (`fpe_bridge_health`)

**Status:** ACTIVE — monitoring and alerting
**Poll interval:** 300 seconds (5 minutes)

**What it does:**
- Checks bridge ingestion gaps: no new messages in last 30 minutes → `_GAP_ALERT_MINS` threshold → WARNING log
- Checks skip ratio: >20% of messages skipped in last 1 hour → `_SKIP_RATIO_WARN` threshold → WARNING log
- Checks retry storm: >10 messages with `attempts > 2` → `_RETRY_STORM_THRESH` threshold → WARNING log
- Checks DLQ depth: >20 messages in DLQ → `_DLQ_WARN_THRESH` threshold → WARNING log
- Publishes Prometheus gauges via `modules.observability`

**This worker is observability-only.** It does not ingest, parse, or modify any data. Safe to disable (`SCHEDULER_ENABLED=false` does not affect this worker — it is an asyncio task, not a scheduled job).

---

## Processing State Machine

```
         ┌──────────┐
ingest →  │  pending  │
         └────┬─────┘
              │ message_processor_worker picks up
              ↓
         ┌──────────┐
         │  parsing  │
         └────┬─────┘
              │
        ┌─────┴──────────┐
        ↓                ↓
   ┌────────┐       ┌─────────┐
   │ parsed │       │ skipped │ ← non-payment, unauthorized command,
   └────┬───┘       └─────────┘   validation failure
        │ accounting_worker picks up
        ↓
   ┌───────────┐
   │ accounting │
   └─────┬─────┘
         │
    ┌────┴─────┐
    ↓          ↓
┌──────┐  ┌─────────┐
│ done │  │ failed  │ ← employee not found (payment), accounting error
└──────┘  └────┬────┘
               │ attempts >= 5
               ↓
            (DLQ state — remains failed, surfaced via /api/fpe/admin/dlq)
```

**State transitions are tracked in `fpe_message_processing_state`.**
Each status change also increments `attempts`.

---

## Message Types and Parser

**Source:** `modules/fazle_payroll_engine/parser.py`

| MessageType | Format Example | Key Fields |
|---|---|---|
| `payment` | "Al Momin 01712345678(N) 5000/-" | employee_name_raw, payout_phone, payout_method, amount, txn_date |
| `balance_summary` | "জমা: 45000/-, বাকি: 12000/-" | summary_date, total_due, total_collected |
| `cash_command` | "Cash 01712345678 Al Momin 5000" | payout_phone, employee_name_raw, amount |
| `income_command` | "Income 01712345678 Al Momin 5000" | payout_phone, employee_name_raw, amount |
| `escort_payment` | "Al Momin=5000/\nRashid=4500/" | entries (name_raw, amount), shift, duty_date |
| `other` | (anything else) | None |

**Payout methods parsed:** bkash, nagad, cash, rocket, bank, unknown

**AI enhancement:** If `confidence < 0.7` and message has content, Ollama is called with the raw text. The AI response is converted to a `ParseResult` only if `is_payment=True` in the JSON response. AI enhancement is logged as `ai_enhanced=True` in `fpe_parser_results`.

---

## Employee Identity in FPE

FPE maintains its own employee table (`fpe_employees`) that is **separate from the main `wbom_employees` table**.

**Why separate:** FPE reconstructs employee identity from historical WhatsApp message text, not from the HR system. Employee records are auto-created from parsed payment messages and merged over time.

**Employee matching hierarchy (from `employee.match_or_create_employee()`):**

1. Exact `employee_id_phone` match
2. Exact `primary_phone` match
3. Alias phone match (`fpe_employee_aliases`)
4. Exact normalized name match
5. Alias name match
6. Fuzzy name match (rapidfuzz, threshold 90%)
7. Auto-create if nothing found (for payment and income_command types)

**Soft-merge (canonical resolution):**
Duplicate employee rows are folded via `canonical_employee_id`. All transactions from merged rows are reported under the canonical employee. The duplicate row is preserved (never deleted). Transaction history is immutable regardless of merge.

**`fpe_employee_aliases` table:** Stores alternative phones, names, and employee ID numbers per employee. An alias can only belong to one employee (UNIQUE constraint on alias_type + alias_value).

---

## Database Tables

All FPE tables use the `fpe_*` prefix. They are managed exclusively by FPE migrations and never written to by the main application.

| Table | Purpose | Mutable? |
|---|---|---|
| `fpe_wa_messages` | Immutable raw WhatsApp message archive | No (immutable) |
| `fpe_message_processing_state` | Per-message processing FSM | Yes (status updates) |
| `fpe_parser_results` | Parser output per message | No (write-once) |
| `fpe_employees` | FPE employee directory | Yes (name, phone, merge) |
| `fpe_employee_aliases` | Multi-phone/name per employee | Yes (additive only) |
| `fpe_cash_transactions` | Verified transaction ledger | No (immutable; reversal creates new row) |
| `fpe_employee_ledger` | Running totals per employee per month | Yes (updated per transaction) |
| `fpe_unmatched_messages` | Parse failures / review queue | Yes (review_status updates) |
| `fpe_accounting_audit_logs` | Immutable accounting audit trail | No |
| `fpe_sync_checkpoints` | Historical sync progress per bridge+JID | Yes |
| `fpe_processing_diagnostics` | Processing latency and outcome per message | Yes |
| `fpe_review_audit_logs` | Admin review actions (promote/dismiss) | No (immutable) |
| `fazle_payment_drafts` | Escort payment drafts created by FPE | Yes (status, approval) |
| `fazle_processing_locks` | Distributed advisory locks | Yes |
| `fazle_bridge_heartbeats` | Per-bridge liveness heartbeats | Yes |
| `fazle_message_queue` | Central inbound message queue | Yes |

---

## API Routes

All routes are under `/api/fpe/`. Authentication: `X-Internal-Key` header (same as all other fazle-core routes).

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/fpe/ingest` | Ingest one WhatsApp message (bridge webhook) |
| GET | `/api/fpe/health` | Module health check |
| GET | `/api/fpe/stats` | Pipeline status counts + method totals |
| GET | `/api/fpe/transactions` | List transactions (filterable by employee, period, method, date) |
| GET | `/api/fpe/transactions/{id}` | Single transaction |
| POST | `/api/fpe/transactions/{id}/reverse` | Create reversal transaction |
| POST | `/api/fpe/transactions/manual` | Create manual transaction |
| GET | `/api/fpe/employees` | List canonical employees with totals |
| GET | `/api/fpe/employees/search` | Multi-tier search (phone, name, alias, fuzzy) |
| GET | `/api/fpe/employees/suggest` | Type-ahead autocomplete (6-tier, trigram) |
| GET | `/api/fpe/employees/{id}` | Single employee + ledger + aliases |
| GET | `/api/fpe/employees/{id}/transactions` | Employee transaction history |
| PATCH | `/api/fpe/employees/{id}` | Edit employee (name, phone; auto-merge on collision) |
| GET | `/api/fpe/ledger/{emp_id}` | Full monthly ledger for employee |
| GET | `/api/fpe/unmatched` | List unmatched messages |
| POST | `/api/fpe/unmatched/{id}/mark-reviewed` | Mark unmatched as reviewed |
| GET | `/api/fpe/sync/status` | Sync checkpoint status per bridge |
| POST | `/api/fpe/sync/trigger` | Trigger immediate historical sync pass |
| GET | `/api/fpe/normalization/summary` | Employee normalization health |
| GET | `/api/fpe/normalization/review` | Pending normalization reviews |
| POST | `/api/fpe/normalization/review/{id}/resolve` | Resolve review (approved_merge, rejected, kept_separate) |
| POST | `/api/fpe/normalization/employees/{id}/link-canonical` | Manually link employee to canonical |
| POST | `/api/fpe/normalization/employees/{id}/aliases` | Add alias to employee |
| POST | `/api/fpe/normalization/employees/{id}/inactivate` | Mark employee inactive |
| GET | `/api/fpe/normalization/employees/{id}/canonical` | Resolve canonical for employee |
| GET | `/api/fpe/admin/reconcile` | Reconciliation: ledger + unmatched == parser total |
| GET | `/api/fpe/admin/needs-review` | Accounting candidates awaiting admin action |
| POST | `/api/fpe/admin/needs-review/{id}/promote` | Promote unmatched → verified transaction |
| POST | `/api/fpe/admin/needs-review/{id}/dismiss` | Dismiss unmatched (or mark as duplicate) |
| GET | `/api/fpe/admin/dlq` | Dead-letter queue — messages stopped retrying |

---

## Failure Handling

| Failure | Behavior |
|---|---|
| Parser returns `confidence < 0.7` | AI enhancement called (Ollama). If AI also fails, stored as `other` type → `skipped`. |
| Parser fails entirely | `store_unmatched(reason='parser_failed')` → `failed` status |
| Employee not found (payment) | `store_unmatched(reason='no_employee_match')` → `failed` status. Money visible in review queue. |
| Employee not found (cash_command) | Error reply sent to source chat via WhatsApp bridge. Row → `skipped`. |
| Accounting exception | `store_unmatched(reason='accounting_failed')` → `failed` status |
| Max attempts reached (5) | Message remains in `failed` state. Surfaced in `/api/fpe/admin/dlq`. |
| Historical sync gap | `gap_scan_loop` rescans by message ID to catch missed messages |
| Bridge health alert | WARNING log + Prometheus gauge. No automatic recovery action. |

---

## Security and Visibility

- All FPE API routes require `X-Internal-Key` header (same as main app)
- `cash_command` and `income_command` require sender phone to be in admin-controlled environment lists (`fpe_cash_authorized_phone_list`, `fpe_income_authorized_phone_list`)
- `fpe_cash_transactions` is immutable — no route allows UPDATE or DELETE on transaction rows
- Reversals create a NEW transaction row (`is_reversal=True`, `reversed_txn_id` FK) — original row preserved
- All admin actions (promote, dismiss) are recorded in `fpe_review_audit_logs` with actor, before_state, after_state
- `fpe_employees` are never hard-deleted — only `status='inactive'` via `/normalization/employees/{id}/inactivate`

---

## FPE vs Core Payroll — Key Distinction

| | FPE (`fpe_*` tables) | Core Payroll (`wbom_payroll_runs`) |
|---|---|---|
| What it records | Individual transactions parsed from WhatsApp messages | Monthly payroll computation runs |
| Employee source | `fpe_employees` (FPE-specific) | `wbom_employees` (main HR table) |
| State machine | pending→parsing→parsed→accounting→done | draft→reviewed→approved→locked→paid |
| Who triggers | WhatsApp message (automated) | Admin PAYROLL command or daily_payroll_compute job |
| Modification | Immutable — reversals only | Cancellable until paid |
| Audit trail | `fpe_accounting_audit_logs` + `fpe_review_audit_logs` | `wbom_payroll_approval_log` |

---

## Current vs Legacy / Uncertain Behavior

| Item | Status |
|---|---|
| All 5 workers | ACTIVE — all started on every platform startup |
| `historical_sync_loop` | ACTIVE — reads production SQLite files; essential for financial completeness |
| `gap_scan_loop` | ACTIVE — runs every 5 minutes; safety net |
| `bridge_health_loop` | ACTIVE — monitoring only; safe to disable without data loss |
| `normalization` routes | ACTIVE — but normalization_reviews workflow not fully documented |
| `escort_payment` → `fazle_payment_drafts` | ACTIVE — links escort duty payments to main platform drafts |
| `BRIDGE1_INGEST_ALL_DMS` env | Documented in code but not yet managed by admin KB |
| `fpe_income_authorized_phone_list` | Known config flag; exact format (comma-separated? env list?) not verified |

---

## Unresolved Questions

| # | Question | Impact |
|---|---|---|
| 1 | Is `fpe_employees` synchronized with `wbom_employees` in any direction? | Determines whether FPE and core payroll share an employee record |
| 2 | What triggers the `/api/fpe/ingest` endpoint — bridge poller, separate webhook, or both? | Understanding of ingestion flow completeness |
| 3 | What is `fpe_income_authorized_phone_list` populated with? | Authorization boundary for income commands |
| 4 | Does `escort_payment` processing via `fazle_payment_drafts` require a matching escort program, or does it create an orphan draft? | Escorts with no matching program ID get `escort_program_id=NULL` |
| 5 | Are migrations 002–009 all currently applied in production? | Schema completeness cannot be verified without DB access |

---

## Traceability

| Knowledge Item | Source File | Source Function |
|---|---|---|
| 5-worker startup | `workers.py` | `start_workers()` |
| Processing state machine | `workers.py`, `ingestion.py` | `mark_processing_status()` |
| Message type enum | `models.py` | `MessageType` |
| Processing status enum | `models.py` | `ProcessingStatus` |
| AI enhancement threshold | `workers.py` | `_process_pending_batch()` — `result.confidence < 0.7` |
| Zero-loss invariant | `workers.py` | Comment: "Money MUST remain visible in the review queue" |
| Cash command no-employee error | `workers.py` | `_handle_accounting_batch()` — `cash_command` branch |
| Historical sync SQLite paths | `historical_sync.py` | `_BRIDGE_CONFIGS` |
| Gap scan interval | `gap_scan.py` | `GAP_SCAN_INTERVAL = 300` |
| Bridge health thresholds | `diagnostics.py` | `_GAP_ALERT_MINS`, `_SKIP_RATIO_WARN`, `_DLQ_WARN_THRESH` |
| Poll interval | `workers.py` | `POLL_INTERVAL = 3`, `BATCH_SIZE = 20`, `MAX_ATTEMPTS = 5` |
| API routes | `routes.py` | Router docstring + route decorators |
| FPE tables | `migrations/001_fpe_schema.sql` | All `CREATE TABLE IF NOT EXISTS` statements |
| Escort payment drafts table | `workers.py` | `_handle_escort_payment()` |
| Immutable transaction rule | `routes.py` | Comment: "STRICTLY FORBIDDEN: Mutating fpe_cash_transactions" |

---

---

## Entity Ownership and Cross-Domain Notes (Wave-2B)

The following clarifications result from the Entity Ownership Audit completed 2026-06-22.

**Entity Ownership Decisions applied:**
- `fazle_payment_drafts` — owned by **CASH/FPE domain** (C-01). Created by escort payment workflow, managed by FPE. See `database_rules.md` Domain 4.
- `fazle_processing_locks` — owned by **SYSTEM domain**. FPE uses it but does not own it. See `database_rules.md` Domain 10.
- `fazle_bridge_heartbeats` — owned by **MESSAGING domain** (C-06). Created by FPE migration but tracks bridge liveness for all consumers.
- `fazle_message_queue` — owned by **MESSAGING domain**. FPE workers consume from it but it is a shared platform resource.

**CASH/FPE table count correction:**
PKMA Report 17 listed 43 tables as undocumented. The Entity Ownership Audit found 21 tables in the CASH/FPE domain specifically (including 17 `fpe_*` prefix tables and 4 shared tables). Total platform tables = 84.

**FPE ↔ HR master soft-link (migration 008):**
`fpe_employees.wbom_employee_id` is a nullable column added by FPE migration 008. It is populated via phone last-10 normalization backfill on service restart. This is NOT an enforced FK — FPE can run without any wbom_employees rows. The link is informational for reconciliation purposes.

**Unresolved Questions — Updated Status:**

| # | Question | Status (Wave-2B) |
|---|---|---|
| 1 | Is `fpe_employees` synchronized with `wbom_employees`? | Partially resolved: soft-link via `wbom_employee_id` (migration 008). One-way: FPE reads wbom for phone matching. No reverse sync confirmed. |
| 2 | What triggers `/api/fpe/ingest`? | Unresolved — bridge poller or separate webhook not confirmed from KB |
| 3 | `fpe_income_authorized_phone_list` format? | Unresolved — comma-separated env list inferred but not verified |
| 4 | Orphan escort payment draft (no matching program)? | Confirmed: `escort_program_id=NULL` — creates orphan draft; admin must link manually |
| 5 | Migrations 002–009 all applied in production? | Unresolved — requires DB access to verify |

---

## Related Articles

- `06_developer_system/database_rules.md` — Full domain documentation for all 84 tables including CASH/FPE domain
- `06_developer_system/automation_pipeline.md` — Scheduler jobs that interact with FPE (payment_reconciliation, combined_draft_cleanup)
- `04_business_rules/payroll_rules.md` — Core payroll workflow that uses FPE transaction data

---

## Revision History

| Date | Change | Author |
|---|---|---|
| 2026-06-22 | Wave-2A: Initial documentation created from production file read | KSP Wave-2A |
| 2026-06-22 | Wave-2B: Added Visibility Matrix, Entity Ownership cross-references, updated unresolved questions | KSP Wave-2B |
