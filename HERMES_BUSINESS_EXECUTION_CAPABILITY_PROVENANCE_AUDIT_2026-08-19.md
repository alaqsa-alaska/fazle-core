# Hermes Business Execution Capability & Provenance Audit — 2026-08-19

Read-only audit. No production code, schema, config, git history, mode, or
services touched. No message sent, no draft approved, no payment executed.
This is a second pass the same day as
`HERMES_MESSAGE_CONTEXT_REPLY_EXECUTION_GAP_AUDIT_2026-08-19.md` ("the
sister audit") — it does not repeat that audit's identity/masking/
recipient-resolution findings (still valid, reused by reference) and
instead goes wide across all business domains and deep on provenance and
the payment-draft chain, per this task's explicit scope.

**One important state change since the sister audit finished, verified
live this session, not assumed:** its two open items were actioned in the
interim —

- `core` commit `ee4bbc8` (2026-08-19 05:46:24 CEST) implements G1
  (`/admin/send-whatsapp` now rejects a missing `source_bridge` with 400
  instead of silently defaulting to `bridge2` — `app/main.py:3315-3324`)
  plus the message-body/decision-log/event-log exposure from that audit's
  Section A. `fazle-core.service` restarted 05:49:13 CEST, **after** the
  commit — live.
- `fazle-mcp` commit `7ff3bb6` (05:44:49 CEST) implements the matching
  client-side work plus G2 (stale test fix). `hermes-dashboard.service`
  restarted 05:54:14 CEST, **after** this commit — `kernel_tools` and
  `identity_tools` are confirmed imported in the running `server.py`
  (`server.py:25-26`, `:260,271,285,293`) — live.

So `resolve_identity`, `classify_intent`, `lookup_decisions`,
`lookup_kernel_events`, and the fixed `/admin/send-whatsapp` are now real,
live capabilities, not pending work. This audit treats them as available.
Current Hermes mode is still `READ` (`~/hermes-runner/current_mode.txt`,
unchanged since 2026-08-15T04:43:56Z) — no write action is actually
reachable right now regardless of anything else below.

---

## A. Executive Summary

**Hermes today is a strong read-only operational analyst and a narrow,
correctly-gated WhatsApp-reply executor. It is not yet a general
operational co-worker.** Concretely:

- **READ** coverage is broad and mostly deterministic: identity, intent,
  decisions, messages, attendance list, cash transactions, payroll runs,
  escort programs, recruitment leads, knowledge base, and full system/
  bridge health are all reachable through existing canonical routes with
  zero duplicated logic.
- **WRITE/EXECUTE** coverage is narrow and lopsided: Hermes can draft and
  send a WhatsApp reply, and approve an existing `fazle_draft_replies`
  draft, all correctly gated (RUN mode + `confirm=True` + superadmin
  RBAC). It can trigger a small allowlist of scheduler jobs. **It has no
  tool at all** for the two domains a "co-worker" would most need:
  payment drafts (`fazle_payment_drafts`) and any other structured
  business write (attendance, employee, escort roster, FPE operator
  review). In every one of these cases the canonical Fazle-Core logic
  already exists, is production-tested, and is reachable today only
  through the admin's own literal WhatsApp text commands
  (`modules/admin_commands`) or the web dashboard — never through
  anything Hermes can call. This is the single largest gap, and it is a
  **capability-exposure gap, not a missing-logic gap** — nothing new needs
  to be invented, only exposed.
- **Provenance** has a real, fixable root cause: `process_message()`
  already receives the bridge's own `message_id` on every real inbound
  message (`modules/message_router/__init__.py:377`, populated by
  `modules/bridge_poller/__init__.py:2000`) at the exact point the Hermes
  Kernel's shadow `DecisionRecord`/`KernelEvent` are constructed
  (`modules/message_router/__init__.py:367-383`) — but neither object
  captures it. `hermes_decision_audit_log` has no message-id column at
  all; `hermes_event_log`'s `payload` JSONB currently stores only
  `sender_phone`/`source`. This is why every provenance question below
  the decision-log layer degrades to a phone+timestamp heuristic — the
  fix is a few lines plus (optionally) one additive column on an
  already-append-only table, not a new architecture.
- **Verdict**: PARTIAL, further along than a naive tool-count would
  suggest (the read side is genuinely complete for most domains), but the
  gap to "co-worker" is concentrated in one well-understood, well-scoped
  place — exposing existing write/approve endpoints — not spread thin
  across many unknowns.

---

## B. Business Domain Action Coverage Matrix

Legend: ✅ Full · ⚠️ Partial · ❌ Missing · 🔒 Permission/mode blocked · 🐛 Bug

| Domain | READ | UNDERSTAND | CREATE DRAFT | APPROVE | EXECUTE | VERIFY |
|---|---|---|---|---|---|---|
| Recruitment | ✅ `get_recruitment_leads`, `get_recruitment_report`, `get_knowledge_base` | ✅ `classify_intent` + KB, single canonical path (see E) | ❌ no MCP tool to create a candidate/session record | — | — | ⚠️ report-level only |
| Employee / HR | ✅ `get_employees`(via `identity_brain`)/`resolve_identity` | ✅ | ❌ `POST /employees` exists (`modules/attendance/routes.py:718`... actually `fazle_payroll_engine/routes.py:718`) — no MCP tool | ❌ `PATCH /employees/{id}` exists, no tool; edit-request approve/reject routes exist (`fazle_payroll_engine/routes.py:3356-3408`), no tool | ❌ | ❌ |
| Payroll / Salary | ✅ `get_payroll_runs` | ✅ | ❌ | ❌ | ⚠️ only indirectly via `run_scheduled_task("daily_payroll_compute", confirm=True)`, DESTRUCTIVE tier, and that job is dry-run-only while `office_payroll_authoritative_enabled` defaults False (`modules/scheduler/__init__.py:135,148`) | ❌ no ledger/reconciliation tool exposed |
| Attendance | ✅ `get_attendance` | ✅ | ❌ `POST /admin/attendance/draft` exists (`modules/attendance/routes.py:168`), no tool | ❌ approval goes through `finalize_attendance_draft()` via `fazle_draft_replies` only when intent="attendance" — reachable via existing `approve_draft` MCP tool **for that one path only** | ❌ direct `POST/PUT/DELETE /admin/attendance` has no tool | ❌ |
| Escort Operations / Roster | ✅ `get_escort_programs`, `get_escort_report` | ✅ | ❌ `POST /` (roster create, `modules/escort_roster/routes.py:597`) exists, no tool | ❌ change-request approve/reject (`:768-790`) exists, no tool | ❌ `PATCH/DELETE/{id}/recalculate/sync` all exist, no tool | ❌ |
| Client / Escort Buyer | ✅ via roster/contacts reads | ✅ | ❌ `escort-clients` CRUD routes exist (`escort_roster/routes.py:286-299`), no tool | — | ❌ | ❌ |
| Accounting / Cash / Ledger | ✅ `get_cash_transactions`, `get_cash_report` | ⚠️ transaction-level yes; ledger-level no tool at all | ❌ | ❌ FPE `operator/{id}/approve|reject` (`fazle_payroll_engine/routes.py:3090,3243`) exists, no tool; `fazle_payment_drafts` approval (`modules/draft_approval`) exists, no tool — see D | ❌ manual txn create/reverse routes exist (`fazle_payroll_engine/routes.py:493,502`), no tool | ❌ no `get_ledger`/`employee_transaction_history` tool |
| WhatsApp / Communication | ✅ `audit_lookup_whatsapp_messages`, `get_recent_messages`, message-body lookup (sister audit A) | ✅ | ✅ `draft_whatsapp_reply` | ✅ `approve_draft` (fazle_draft_replies only) | ✅ `send_whatsapp_message` (RUN+confirm, superadmin) | ⚠️ send result returned inline; no independent re-fetch tool for delivery status |
| Draft / Approval workflows | ✅ `audit_get_drafts` | ✅ | ✅ (reply drafts only) | ⚠️ full for `fazle_draft_replies`, ❌ for `fazle_payment_drafts` (headline finding, Section D) | ⚠️ same split | ⚠️ same split |
| Super Admin → Hermes instructions | ✅ `lookup_decisions`, `get_mode_state` | ✅ | — | — | 🔒 correctly gated everywhere a real write exists (Section G) | ✅ `chosen_action`/mode/RBAC all independently auditable |

Do not read the many ❌ cells as "business logic is missing" — in every
case checked this session the underlying Fazle-Core route/function
already exists and is exercised by another caller (admin dashboard, admin
WhatsApp text commands, or scheduler). They are capability-exposure gaps.

---

## C. Message → Entity Provenance Map

Root cause first (new finding this session, upstream of everything the
sister audit found):

```text
bridge_poller.process_bridge_inbound()
    → process_message(sender, text, source, message_id=message_id)   [real value, always passed]
        → DecisionRecord(sender_phone, source, raw_input_text)        [message_id NOT captured]
        → KernelEvent(trace_id, "EVT-001 message.received",
                       payload={"sender_phone":…, "source":…})        [message_id NOT captured]
        → hermes_decision_audit_log  (no message_id column at all)
        → hermes_event_log.payload  (JSONB — could hold it, doesn't)
```

Evidence: `modules/bridge_poller/__init__.py:2000` passes a real
`message_id`; `modules/message_router/__init__.py:352-353` receives it as
`message_id: Optional[str] = None`; the kernel objects are constructed at
`:367-383` with no reference to the `message_id` variable in scope one
line above them; `db/migrations/060_hermes_kernel_scaffolding.sql:19-36`
confirms the table has no such column.

Downstream (reusing/confirming the sister audit's Section F, not
re-deriving it):

| Workflow | Linkage category | Evidence |
|---|---|---|
| Escort admin-confirmation dedup | **A. Deterministic** | `wbom_unmatched_confirmations(source_bridge, source_message_id)`, partial unique index (`modules/escort/__init__.py:2536-2571`) |
| `fazle_draft_replies` (reply drafts) | **B. Logical, no FK** | `meta`/`context` JSONB exists (`shared/draft_reply.py:33-41`) but every traced call site passes a static string, never the originating message id |
| `fazle_payment_drafts` (payment drafts) | **D. No usable linkage** | schema has `employee_id`, `escort_program_id`, `source_bridge`, `admin_phone` — no message/trace id column (confirmed again this session against `modules/draft_approval/__init__.py`'s own SELECT/UPDATE column lists) |
| Decision log itself → originating message | **C. Heuristic only** (this session's finding) | `hermes_decision_audit_log` correlates to `wbom_whatsapp_messages` only via `sender_phone` + `timestamp` proximity — `trace_id` is a fresh `uuid4()` per turn (`modules/hermes_kernel/data_classes.py:47`), not derived from or stored against `message_id` |
| FPE cash transactions / employee ledger | **D. No usable linkage** to the originating request/message | confirmed via `fazle_payroll_engine/routes.py` schema reads — transactions carry employee/amount/method, not a request/message reference |

**Conclusion**: the chain is one hop shorter than it looks. Fixing the
message→trace link (Section C's root cause) is a prerequisite for
everything in Section D of the target architecture (Section 4 of the
task) — without it, even a perfect payment-draft/message linkage
downstream would still bottom out in a heuristic at the very first hop.

---

## D. Payment Draft Workflow — full chain and exact stopping point

```text
inbound message / employee_conversation flow / payment_ingest
    → parsing (modules/employee_conversation, modules/payment_ingest)
    → employee resolution (identity_brain / FPE employee fallback)
    → fazle_payment_drafts INSERT, status='pending'
        (call sites: modules/employee_conversation/__init__.py:590,
         modules/payment_ingest/__init__.py:350,
         modules/payment_workflow/__init__.py:213,318,
         modules/route_b/__init__.py:194,
         modules/fazle_payroll_engine/workers.py:667)
    → admin reviews (web dashboard, or WhatsApp text commands)
    → modules.draft_approval.approve_draft(draft_id, amount, method, admin_phone)
        (modules/draft_approval/__init__.py:423)
        → create_canonical_transaction()  (:295) — writes fpe_cash_transactions-equivalent
        → ledger upsert (inside create_canonical_transaction)
        → _audit() (:157)
        → fazle_payment_drafts.status = 'completed'
    → accountant notification text generated (build_accountant_message, :186)
```

This entire chain is real, wired, and exercised **today** — but its only
caller is `modules/admin_commands/__init__.py` (`_cmd_approved`,
`_cmd_dredit`, `_cmd_dreject` at `:900-990`), which itself is only reached
when the **admin** types a literal command (`APPROVED <id> <amount>
<method>`, `DREDIT …`, `DREJECT …`) directly into WhatsApp. Grepped for
any HTTP route wrapping `modules.draft_approval` — **none exists**
(`grep -rln draft_approval` across `app/` returns nothing). It is not
reachable from `fazle-mcp` under any tool name, and it is a structurally
different table/module from the one Hermes's `approve_draft` tool
actually calls (`fazle_draft_replies` via `/api/drafts/{id}/approve`,
`send_whatsapp_tools.py:138-162`) — reconfirms the prior audit's finding
with the exact call graph, not just the schema.

### Direct answers

| Question | Answer | Why |
|---|---|---|
| Find payment drafts? | ❌ | no `get_payment_drafts`/`audit_get_payment_drafts` MCP tool (only `audit_get_drafts`, which queries `fazle_draft_replies`) |
| Inspect full draft details? | ❌ | same — no read tool for `fazle_payment_drafts` |
| Identify the employee? | ⚠️ | possible only indirectly via `get_cash_transactions`/`resolve_identity`, not from the draft itself |
| Identify originating request/message? | ❌ | no linkage exists to identify (Section C) |
| Create a payment draft? | ❌ | no tool; the canonical creation paths are all internal (parsing pipelines), not currently something an admin *asks* Hermes to trigger on demand either |
| Approve a payment draft? | ❌ | **capability-exposure gap** — canonical logic exists (`draft_approval.approve_draft`), no route, no tool |
| Reject/cancel one? | ❌ | same — `reject_draft` exists (`:715`), unreachable |
| Determine whether one was already approved? | ⚠️ | only inferable from `get_cash_transactions` matching amount/employee/time, not directly |
| Identify the resulting cash transaction? | ⚠️ | heuristic only (Section C) |
| Identify the resulting ledger entry? | ❌ | no ledger tool at all |
| Verify the final financial effect? | ❌ | no tool returns ledger state |

**Classification, per the task's explicit instruction: capability-exposure
gap** — the canonical Fazle-Core operation exists and is safe
(lock-protected, audited, race-safe per `modules/payment_workflow/__init__.py:358`'s
`FOR UPDATE`); Hermes simply has no door into it.

---

## E. Payroll / Attendance / Recruitment / Escort / Accounting Findings

**Payroll**: `modules/attendance/routes.py:198-213` exposes
eligibility-preview, dry-run, and reconciliation-report as GET routes with
no MCP tool wrapping any of them — Hermes could answer "কার salary বাকি"
today only by cross-referencing `get_payroll_runs` + `get_attendance`
manually, not via the purpose-built dry-run endpoint that already computes
this. `daily_payroll_compute` is reachable via `run_scheduled_task`
(DESTRUCTIVE tier, RUN+confirm) but is dry-run-only while
`office_payroll_authoritative_enabled` defaults False
(`modules/scheduler/__init__.py:135,148`) — this is an intentional Owner
gate outside Hermes's reach, correctly so, not a bug.

**Attendance**: full CRUD exists (`modules/attendance/routes.py:156-192`)
plus a dedicated draft-submission route for office-assistant-tier actors
(`:167`, role `office_assistant`) — none exposed to Hermes except the
one path that happens to reuse `fazle_draft_replies` (intent="attendance"
drafts approved via the existing `approve_draft` tool, per
`modules/admin_commands/__init__.py:646-653`). Direct correction/creation
by Hermes has zero exposure.

**Recruitment**: no conflicting hardcoded salary-reply module found this
session. `salary_information` is detected as an intent
(`modules/recruitment_detector/__init__.py:84-86`) but grepping the whole
repo for any handler keyed on that exact string returns only the
detector itself — no separate template answers it. This means recruitment
salary questions fall through to the same LLM-first + knowledge-base RAG
path as everything else; the canonical source of truth is the knowledge
base content itself, not a second code path. This matches the existing
KB-governance framework (`[[project_fazle_kb_governance]]` /
`[[feedback_kb_first_policy]]` in memory) — the residual risk is **content
drift** in the KB (a stale document contradicting current payroll
configuration), not duplicated logic. Not independently re-verified this
session whether current KB salary figures match live payroll config —
flag for a KB-specific pass if this becomes operationally relevant.

**Escort**: `modules/escort_roster/routes.py` exposes a very complete
CRUD + lifecycle surface (create/patch/delete/recalculate/sync,
change-request approve/reject, slip-queue confirm/reject, client
add/remove) — none reachable from `fazle-mcp` beyond the two read
reports. The one genuinely reusable provenance pattern
(`source_bridge`/`source_message_id` on `wbom_unmatched_confirmations`,
Section C) is escort-specific infrastructure already built for exactly
this problem — reusing its shape (not its table) for other domains is the
cheapest path to Section 4's target, not a new design.

**Accounting**: `fazle_payroll_engine/routes.py` has a full FPE
operator-review lane (`/operator/submit`, `/operator/pending`,
`/operator/{id}/approve|reject`, `:2925-3313`) plus manual transaction
create/reverse (`:493-528`) and income-transaction create (`:2753`) — all
admin-RBAC-gated server-side, all unreachable from Hermes. Combined with
the missing ledger read tool, Hermes today cannot reliably answer any of
the six accounting questions in Section 10 of the task prompt without a
human cross-checking the dashboard.

---

## F. Tool Coverage Inventory (condensed — 48 live tools in `server.py`)

Full list grepped directly from `server.py:54-545`, one call each.
Grouped by effect:

**Read-only (39 tools)** — contacts, employees, recent messages, KB,
attendance list, billing outstanding, cash transactions, escort programs,
bridge/module status, mode state, payroll runs, recruitment leads, social
status/queue/flagged/report, 5×`audit_*` code/doc/log/file/KB search
tools, `audit_lookup_whatsapp_messages`, `resolve_identity`,
`classify_intent`, `lookup_decisions`, `lookup_kernel_events`,
`audit_git_status`, `audit_recent_commits`, `audit_get_auto_reply_settings`,
`get_settings_status`, `audit_get_drafts`, `list_available_tasks`,
`opencode_check`, monitoring/report tools (×11).

**Write/action (9 tools)**, all correctly gated per Section G:
`run_scheduled_task` (allowlist+tiered), `opencode_dispatch`
(confirm-gated), `draft_whatsapp_reply` (no gate needed — genuinely can't
send), `send_whatsapp_message` (RUN+confirm+superadmin),
`approve_draft` (RUN+confirm+superadmin, `fazle_draft_replies` only).

**Existing logic, no Hermes tool (capability-exposure gaps — highest
value)**: payment-draft approve/edit/reject, attendance create/update/
delete, escort roster full CRUD + change-request approve/reject, employee
create/update/edit-request approve/reject, FPE operator approve/reject,
manual cash transaction create/reverse, income transaction create, ledger
read.

**Hermes tool but incomplete backend**: none found this session — every
existing tool's backend was confirmed complete and correct for what the
tool claims to do.

**Backend + tool exist but permission blocks it**: `daily_payroll_compute`
(flag-gated server-side, independent of Hermes).

**Backend + tool + permission exist but result cannot be correlated**:
`send_whatsapp_message`/`approve_draft` results are returned inline
(sufficient for that one call), but nothing lets Hermes later re-derive
"which message caused this send" — same root cause as Section C.

---

## G. Execution Gate Matrix

| Action | READ mode | RUN mode | confirm | RBAC | Admin approval | Other gate |
|---|---|---|---|---|---|---|
| Send WhatsApp (`send_whatsapp_message`) | blocked | required | required | superadmin (`COMMAND_ROLE["send_whatsapp_message"]`) | implicit (this call *is* the approval) | `source_bridge` now required (fixed today) |
| Create reply draft (`draft_whatsapp_reply`) | allowed | allowed | not required | none extra | — | 5-min dedup window |
| Approve reply draft (`approve_draft`) | blocked | required | required | superadmin | — | operates on `fazle_draft_replies` only |
| Payment draft creation | — | — | — | — | — | no Hermes path exists at all |
| Payment draft approval (`draft_approval.approve_draft`) | n/a (no tool) | n/a | n/a | `operator` via `admin_commands` text path (`COMMAND_ROLE["approve"]="operator"`) | admin's own literal WhatsApp text command | not reachable by Hermes under any gate |
| Employee create/update | n/a (no tool) | n/a | n/a | `admin` (server-side `_require_admin_role`) | — | not reachable |
| Attendance modification | n/a (no tool) | n/a | n/a | `admin`/`office_assistant` (server-side) | — | not reachable |
| Escort program create/update | n/a (no tool) | n/a | n/a | server-side key check only | — | not reachable |
| Payroll/payment execution | via `run_scheduled_task` only | required (DESTRUCTIVE tier) | required | — | — | additionally flag-gated (`office_payroll_authoritative_enabled`, default False) |
| Accounting transaction creation | n/a (no tool) | n/a | n/a | `admin` (server-side) | — | not reachable |
| `opencode_dispatch` | blocked in READ | required for non-trivial ops (per its own docstring pattern) | required | — | — | separate token path (assistant-backend JWT) |

No gate was weakened or proposed to be weakened. The pattern across every
*existing* write tool (mode + confirm + RBAC, all checked at call time,
independent of any historical `chosen_action`) is sound and should be
copied exactly for any new tool in the phases below — not redesigned.

---

## H. Existing Bugs / Technical Debt (materially relevant only)

- **Already fixed today**: server-side `source_bridge` silent default
  (was the sister audit's headline finding — now closed and live, see
  top of this document).
- **`fazle_draft_replies` vs `fazle_payment_drafts` mismatch** — reconfirmed
  with the actual call graph this session (Section D), not just schema.
  This is the single most consequential piece of debt for the
  "co-worker" target.
- **Kernel provenance gap** — `message_id` available but not threaded
  through (Section C) — new finding, small fix, real impact.
- **`hermes_decision_audit_log.trace_id` is not derived from anything
  stable** — every turn gets a fresh UUID; nothing currently makes
  re-running/replaying a turn idempotent at the kernel-log layer. Not
  a defect (the tables are explicitly append-only/shadow, per migration
  060's own comment), but worth knowing before Phase 1 assumes trace_id
  can double as an idempotency key — it currently can't.
- **Pre-existing uncommitted files in `fazle-mcp`** (`audit_tools.py`,
  `domain_reports.py`, `fazle_core_client.py`, `operational_tools.py`,
  `send_whatsapp_tools.py`, 2 test files, `test_get_settings_status.py`)
  — already tracked in memory from the sister audit, not touched again
  this session, not attributable to anything in this report.
- None of the above blocks the current READ-mode-only Hermes automation
  target; they matter for the phases proposed in Section K.

---

## I. Gap Classification

- **Tool exposure gaps** (canonical logic exists, no MCP tool): payment
  draft approve/edit/reject; attendance CRUD; escort roster CRUD +
  change-request approve/reject; employee create/update/edit-request
  approve/reject; FPE operator approve/reject; manual cash txn
  create/reverse; income txn create; ledger read. **This is the dominant
  category.**
- **Backend logic gaps**: none found this session — every backend
  traced was complete for its stated purpose.
- **Permission gaps**: `daily_payroll_compute`'s authoritative flag
  (Owner-controlled, intentional, not a Hermes-side restriction to
  relax).
- **Provenance gaps**: message→trace linkage (Section C root cause,
  new); trace/decision→business-entity linkage (Section D, reconfirmed);
  no ledger correlation.
- **Verification gaps**: no ledger/employee_transaction_history tool; no
  independent re-fetch of WhatsApp delivery status after a send.
- **Existing bugs**: none new beyond what's in Section H (the one real
  bug found this arc was already fixed and deployed before this audit
  started).
- **Convenience gaps**: 3-call message-context chaining (sister audit
  Section G4, still valid, still optional).

---

## J. Priority Queue

**P0 (safety/financial correctness)**: none newly found this session.
The previously-flagged `FAZLE_CORE_API_KEY` re-rotation
(`[[project_hermes_message_context_reply_execution_audit_20260819]]`,
Incident 5) remains outstanding but is tracked separately and is not a
gap in Hermes's capability model.

**P1 (required for autonomous operation)**:
1. Payment-draft approval capability-exposure gap (Section D) — Hermes
   cannot complete the single most likely "co-worker" request
   ("Sumon-এর advance approve করো") at all today.
2. Thread `message_id` into `KernelEvent`/`hermes_decision_audit_log`
   (Section C) — every other provenance improvement depends on this
   existing first.
3. Attendance/escort/employee/FPE-operator write-tool exposure (Section
   B) — same class as #1, lower individual urgency, high combined value.

**P2 (reliability/verification)**:
1. No ledger/`employee_transaction_history` read tool — Hermes can
   trigger nothing here yet, but once P1.1 lands, Hermes will be able to
   *act* without being able to *verify*, which is worse than the current
   all-read state.
2. Draft/message linkage on `fazle_payment_drafts`/`fazle_draft_replies`
   remains heuristic even after P1.2 (Section C, category B/D rows)
   unless the draft tables also gain a reference — smaller follow-on to
   P1.2, not a blocker for it.

**P3 (convenience/latency)**: message-context aggregator (unchanged from
sister audit); none of these justify inflating to P0/P1.

---

## K. Minimal Future Implementation Phases

**Phase 1 — Message/trace provenance** (do first; everything else reads
better once this exists)
- *Problem*: message→trace linkage is heuristic (Section C).
- *Reuse*: `message_id` already flows into `process_message()`; kernel
  scaffolding tables already exist and are append-only.
- *Missing*: thread `message_id` into `KernelEvent.payload` (zero schema
  change — JSONB already there) and add it to `DecisionRecord`/
  `hermes_decision_audit_log` (one additive column, one migration).
- *Files*: `modules/message_router/__init__.py:367-383`,
  `modules/hermes_kernel/data_classes.py`, one new migration.
- *MCP impact*: `lookup_decisions`/`lookup_kernel_events` responses gain
  a `source_message_id` field — no new tool needed, existing ones improve.
- *Permission impact*: none.
- *Tests*: unit test asserting a decision-log row for a known
  `message_id` round-trips it.
- *Production risk*: very low — additive column on an append-only,
  currently-unread-by-production-queries table.
- *Acceptance*: `lookup_decisions(phone=...)` returns a `source_message_id`
  that matches the real inbound message, for a live test message, without
  any phone+timestamp guessing.

**Phase 2 — Payment-draft action coverage** (highest business value)
- *Problem*: Section D's capability-exposure gap.
- *Reuse*: `modules.draft_approval.approve_draft/edit_draft/reject_draft`
  entirely as-is — zero new business logic.
- *Missing*: one new admin-RBAC-gated HTTP route per action (mirroring
  `/api/drafts/{id}/approve`'s existing shape) plus one new MCP tool per
  action, gated identically to `send_whatsapp_message`
  (`RUN` mode + `confirm=True` + superadmin — this is real money moving).
- *Files*: new `app/main.py` routes or a small `modules/draft_approval`
  `routes.py`; new `fazle-mcp/payment_draft_tools.py`.
- *Schema impact*: none.
- *Permission impact*: reuses existing `COMMAND_ROLE`/RBAC pattern.
- *Tests*: route-level (mirrors `test_kernel_lookup_routes.py`'s
  pattern) + tool-level mode/confirm tests (mirrors
  `test_send_whatsapp_tools.py`'s pattern).
- *Production risk*: medium — this is the first Hermes-reachable path
  that moves money; needs the same care `send_whatsapp_message` got
  (explicit Owner sign-off before RUN-mode use, dry-run demonstration
  first).
- *Acceptance*: Hermes can approve a real pending payment draft end-to-end
  (in a controlled RUN-mode test) and the resulting transaction/ledger
  entry is independently visible via `get_cash_transactions` — which
  itself motivates Phase 3's ledger tool.

**Phase 3 — Domain write/approve/verify gaps**
- *Problem*: Section B's remaining ❌ cells (attendance, escort roster,
  employee, FPE operator, ledger read).
- *Reuse*: every listed backend route as-is.
- *Missing*: one thin MCP tool per route, same pass-through pattern as
  every existing tool in `fazle-mcp`.
- *Files*: new tool modules per domain, following `draft_tools.py`'s
  existing template.
- *Schema/permission impact*: none beyond what each route already
  enforces.
- *Tests*: one per new tool, mirroring existing patterns.
- *Production risk*: low individually, needs staged rollout given volume.
- *Acceptance*: each domain's Section B matrix row moves from ❌ to ✅/⚠️
  with a live-verified example per domain.

**Phase 4 — Controlled autonomous execution**
- *Problem*: even with Phase 1-3 landed, nothing today lets Hermes chain
  CREATE→APPROVE→VERIFY within one instruction without a human re-prompt
  at each step.
- *Reuse*: existing mode/confirm gates, unchanged.
- *Missing*: an explicit Owner-approved policy for which chains may run
  without a re-confirmation per step (e.g. "approve this specific advance
  request" as one instruction, not three).
- *Production risk*: this is a policy question, not a code question —
  defer to Owner decision, do not implement speculatively.

**Phase 5 — Optional context aggregation** (unchanged from the sister
audit's G4) — only if Phase 1-3's improved individual tools still leave
excessive round-trips in practice.

---

## L. What Hermes Can Automate Today

- "01897998877-এর identity আর role কী?" — `resolve_identity`, live,
  unmasked, deterministic.
- "এই মেসেজ থেকে Hermes কী decision নিয়েছিল?" — `lookup_decisions`, live.
- "গতকালের attendance list দেখাও" — `get_attendance`, live.
- "আজকের cash transactions দেখাও" — `get_cash_transactions`, live.
- "Escort program #123 এর status কী?" — `get_escort_programs`, live.
- "মামুনকে একটা reply draft বানাও" — `draft_whatsapp_reply`, live (READ
  mode does not block drafting, only sending).
- "এই bridge/queue এর health কেমন?" — full monitoring suite, live.
- Send a specific, admin-instructed WhatsApp message or approve a
  specific reply draft — mechanically ready, currently blocked only by
  mode being `READ` (by design, awaiting Owner's own mode switch).

## M. What Hermes Cannot Automate Today

- "Sumon-এর advance approve করো" — **no tool reaches
  `fazle_payment_drafts`** (Section D); the canonical operation exists
  but nothing exposes it.
- "এই মাসে কার salary বাকি?" — the purpose-built eligibility/dry-run
  report (`modules/attendance/routes.py:198-213`) has no MCP tool; Hermes
  would have to approximate this by cross-referencing raw reads, which
  risks a wrong answer on a financial question.
- "এই payment employee ledger-এ গেছে কিনা verify করো" — **no ledger tool
  exists at all**, deterministic or otherwise.
- "নতুন escort program তৈরি করো" / "roster entry patch করো" — full CRUD
  exists server-side, zero exposure.
- "এই employee-র salary/contact info update করো" — `PATCH /employees/{id}`
  exists, zero exposure.
- "এই payment কোন message থেকে এসেছে?" — answerable only heuristically
  today (Section C); Phase 1 is the prerequisite fix.
- Anything requiring `BUILD`/`RUN` mode right now fails closed at the
  mode gate regardless of tool availability — current mode is `READ`.

---

## N. Explicit non-goals (confirmed respected)

No duplicate identity/role/intent/payroll/attendance/escort/accounting
logic was found or proposed anywhere in this report — every gap above is
closed, in the proposed phases, by exposing an existing Fazle-Core
route/function through a thin pass-through tool, exactly the pattern
every already-shipped tool in `fazle-mcp` already follows. No new policy
engine, no new identity system, no parallel provenance table invented
where an additive column/JSONB field on an existing table suffices. No
production code, schema, config, mode, or service was changed to produce
this report; no `grep -A/-B/-C` or `cat` was run against any
secret-bearing file.
