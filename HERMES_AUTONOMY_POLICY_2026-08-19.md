# Hermes Autonomy Policy — 2026-08-19

Produced as Sections 16-19 of the deployment → controlled live acceptance
→ autonomy-policy task, same day as
`HERMES_BUSINESS_EXECUTION_CAPABILITY_PROVENANCE_AUDIT_2026-08-19.md` and
the 4-task implementation it authorized. This is a policy document, not
code — it defines **when Hermes may call an already-authorized tool**, on
top of the existing mode/RBAC/confirmation gates already enforced in
`fazle-mcp`/`fazle-core`. It does not duplicate, replace, or weaken any
backend authorization.

Live tool inventory verified against the running `fazle-mcp` process
(post-2026-08-19 09:43 CEST restart): **80 tools**, enumerated below in
full, each assigned exactly one tier.

---

## Tier A — Autonomous read (no confirmation, no mode gate)

Hermes may call these freely, any time, without asking first.

`audit_get_auto_reply_settings`, `audit_get_drafts`, `audit_git_status`,
`audit_lookup_whatsapp_messages`, `audit_read_file`,
`audit_recent_commits`, `audit_search_code`, `audit_search_docs`,
`audit_search_kb`, `audit_search_logs`, `classify_intent`,
`get_attendance`, `get_billing_outstanding`, `get_bridge_diagnostics`,
`get_bridge_health_report`, `get_bridge_message_stats`, `get_cash_report`,
`get_cash_transactions`, `get_contacts`, `get_dlq_status`,
`get_employee_ledger`, `get_employee_transaction_history`,
`get_employees`, `get_escort_programs`, `get_escort_report`,
`get_knowledge_base`, `get_mode_state`, `get_module_bridge_status`,
`get_monitoring_report`, `get_monitoring_status`, `get_operator_pending`,
`get_payment_draft_audit`, `get_payment_draft_employee_balance`,
`get_payment_drafts`, `get_payroll_report`, `get_payroll_runs`,
`get_recent_messages`, `get_recruitment_leads`, `get_recruitment_report`,
`get_settings_status`, `get_social_flagged`, `get_social_queue`,
`get_social_report`, `get_social_status`, `get_system_health`,
`get_system_health_report`, `get_whatsapp_ops_report`,
`list_available_tasks`, `list_my_capabilities`, `list_roster`,
`lookup_decisions`, `lookup_kernel_events`, `opencode_check`,
`resolve_identity`, `search_employees`, `suggest_employees`,
`get_directives` — **57 tools.**

*(2026-08-19, later same day: `search_employees`/`suggest_employees` added,
closing the exact `get_contacts`/`get_employees` no-search gap the
agentic-behavior audit found live; `get_directives` added alongside Item 3's
`remember_business_rule` below. All three appended here rather than
renumbering — see that audit doc and
`HERMES_MESSAGE_CONTEXT_REPLY_EXECUTION_GAP_AUDIT_2026-08-19.md` for
context. This doc's own "80, zero unclassified" tally from earlier the same
day was itself stale within hours — treat any tool-count claim as a
point-in-time snapshot, verify against the live registry before relying on
it.)*

## Tier B — Autonomous draft/preparation (no real mutation)

The backend operation is non-mutating from a business-record standpoint
— it produces a pending proposal a human still reviews, never a final
state, never money, never a send.

`draft_whatsapp_reply`, `create_attendance_draft` — **2 tools.**

## Tier C — Explicit-instruction operational write (non-financial, reversible)

Requires the Super Admin's clear instruction naming the exact entity.
Never triggered by inference from intent, history, or a vague general
request. Uses the existing RUN + confirm + RBAC gates exactly as built —
this tier is a usage policy on top of them, not a substitute.

`send_whatsapp_message`, `approve_draft`, `create_attendance`,
`update_attendance`, `delete_attendance`, `update_employee`,
`approve_employee_edit_request`, `reject_employee_edit_request`,
`approve_roster_change_request`, `reject_roster_change_request`,
`create_roster_entry`, `recalculate_roster_entry`,
`remember_business_rule`, `revoke_directive` — **14 tools.**

*(2026-08-19, later same day: `remember_business_rule`/`revoke_directive`
added — Item 3 of the Hermes->Earth rename+autonomy pass. Unlike every
other admin_directives write path, this one is triggered by Earth's own
recognition of the admin's explicit instruction rather than a literal typed
command match — see `core/.claude/CLAUDE.md`'s updated admin_directives
bullet for the narrow exception this represents. Still Tier C: same
RUN+confirm+superadmin RBAC as every other entry in this tier, and "explicit,
current instruction naming the exact rule" is the bar, not general
inference.)*

*(`update_employee`'s own backend route already forbids touching cash
transactions or hard-deleting the employee — its Tier C placement relies
on that existing route-level restriction, not on Hermes's own judgment.)*

## Tier D — Financial / destructive / high-impact (always strong explicit authorization)

Never inferred from `chosen_action`, intent, or a general instruction.
Hermes must restate entity + amount/value + action + target before
calling. Same RUN+confirm+RBAC gates, used with maximum caution.

`approve_payment_draft`, `edit_payment_draft`, `reject_payment_draft`,
`create_manual_transaction`, `reverse_transaction`,
`approve_operator_pending`, `reject_operator_pending`, `create_employee`,
`patch_roster_entry`, `delete_roster_entry`, `opencode_dispatch` —
**11 tools.**

`patch_roster_entry`/`delete_roster_entry` are here specifically because
this account is superadmin-tier: these mutate the roster directly and
immediately, skipping the pending-change-request review an operator-tier
human would go through (Owner-confirmed acceptable at the code-gate
level, 2026-08-19 — see the tool docstrings) — the autonomy policy still
treats them at the highest caution tier regardless. `create_employee` is
here (not Tier C, unlike `update_employee`) because creating a new
employee record has downstream payroll/salary implications a field edit
doesn't. `opencode_dispatch` is here because its blast radius (arbitrary
code/investigation dispatch across repos) is the least predictable of
any tool in the surface.

`run_scheduled_task` is **not force-fit into one tier** — it already
carries its own internal risk classification
(`scheduler_tools.JOB_REGISTRY`: `READ_ONLY`/`LOW_RISK`/`DESTRUCTIVE`
per `job_name`) with its own mode-gate strength per tier. That existing
classification is authoritative for this tool; treat a `DESTRUCTIVE`-tier
job (`daily_payroll_compute`, `payment_reconciliation`) as Tier D and
everything else as Tier B-ish (informational/maintenance), per-call, not
as one fixed bucket.

**Tally (updated 2026-08-19, later same day): 57 (A) + 2 (B) + 14 (C) +
11 (D) + 1 (`run_scheduled_task`, internally tiered) = 85 — matches the
live registry as of this update; the original same-day "80" tally above
was verified programmatically at the time but is a point-in-time snapshot,
not a static fact — re-verify against the live registry before trusting
any tool-count claim in this doc.**

---

## Natural-language instruction rules

**Strong identifier reuse.** If an exact `employee_id`/`contact_id`/
`draft_id`/`message_id`/`transaction_id`/`escort program_id`/full
canonical phone/`source_bridge` is already known from this conversation
or an earlier tool call, reuse it exactly. Never downgrade to fuzzy
text or last-4-digit matching once an exact identifier exists.

**Ambiguity fails closed.** If more than one entity could match, Hermes
must not mutate anything — resolve/disambiguate first (search, list
candidates, ask). No tool in this surface performs fuzzy entity
resolution server-side; every mutating tool takes an exact ID.

**Financial actions never infer.** Amount, method, employee, draft ID,
transaction, or account must come from the admin's explicit instruction
or an exact prior tool result — never guessed, never defaulted, never
carried over from an unrelated earlier turn.

**Execute → read back → verify → report.** After every successful
mutation, re-fetch via the matching read tool (e.g. `approve_payment_
draft` → `get_employee_ledger`/`get_employee_transaction_history`;
`update_attendance` → `get_attendance`) before telling the admin it's
done. An HTTP 200 from the write call is not itself proof of the
intended effect.

**Historical decision ≠ execution authorization.** A `chosen_action`
value (`"reply"`, `"draft"`, etc.) from `lookup_decisions` describes what
the deterministic pipeline decided for a past message — it is never
sufficient grounds to call a Tier C/D tool now. Every real action still
needs its own current RUN mode + `confirm=True` + a real, current
instruction.

---

## Acceptance scenarios (for reference, not re-litigated per tool call)

1. **Message investigation** ("এই লোকটা কী চেয়েছে?") — Tier A chain only
   (`resolve_identity` → `audit_lookup_whatsapp_messages` →
   `lookup_decisions`). No write.
2. **Payment approval** ("Payment draft #X, ৳Y approve করো") — Tier D:
   `get_payment_drafts`/`get_payment_draft_audit` (verify pending, read
   full row) → `approve_payment_draft` (RUN+confirm) →
   `get_employee_transaction_history`/`get_employee_ledger` (read-back
   verify) → report.
3. **Attendance correction** — Tier C: `get_attendance` (resolve exact
   employee+date) → `update_attendance` → `get_attendance` (verify).
4. **Escort update** — Tier D (`patch_roster_entry`): exact `program_id`
   required, no fuzzy target, update only that record, read back via
   `list_roster`/detail read.
5. **Ambiguous request** ("8877-এর payment approve করো", no prior
   context) — fails closed. `get_payment_drafts`/search first; never
   guess which draft "8877" means.
6. **Historical decision** (`chosen_action="reply"` in the log) — never
   itself authorizes `send_whatsapp_message`.

---

## Provenance follow-through (Section 19, recommendation only — not implemented)

Task 1 (2026-08-19) solved `message → decision/trace` — `hermes_decision_
audit_log.source_message_id` is now deterministic going forward. The
next hop (`decision/trace → payment draft → cash transaction → ledger`)
is still heuristic (phone+timestamp), per the original audit's Section C.

**Smallest next step, if this becomes a real operational need**: thread
`source_message_id`/`trace_id` into `fazle_payment_drafts` at the point
each draft-creation call site already runs (`modules/employee_
conversation/__init__.py:590`, `modules/payment_ingest/__init__.py:350`,
`modules/payment_workflow/__init__.py:213,318`, `modules/route_b/
__init__.py:194`, `modules/fazle_payroll_engine/workers.py:667`) — one
additive nullable column on `fazle_payment_drafts`, no new table, same
pattern as migration 067. Not implemented now: no live operational need
has surfaced yet, and every one of those five call sites would need its
own small edit — a larger, separately-scoped piece of work, not a
same-day deployment addition.
