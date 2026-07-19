# Writer Authority — Payroll / Roster / Payment

Compiled 2026-07-19, by direct code audit (file:line citations below).
Supersedes the writer-count claims in
`knowledge_base/00_governance/escort_dualwrite_concurrency_debug_2026-07-17.md`
where they conflict — that doc is dated 2026-07-17; writers 2 and 3 below were
each given a fix the next day (2026-07-18, "Phase 1I" and "Phase 2B" per
in-code comments). Treat this file as the current record; re-verify against
code before relying on it, as with any doc in this KB.

## Canonical principle

Writers may remain separate when they represent different business
authorities. They must **not** be unified merely to reduce duplicate-looking
code. In particular: **actual payment-draft/ledger values** (what has been
approved or paid) and **calculated roster/pay estimates** (projections shown
before approval) must not be silently merged — a viewer needs to be able to
tell which number is a promise and which is a fact.

## Writers

### 1. `escort_lifecycle._handle_release_event_tx()`
`modules/escort_lifecycle/__init__.py:359-387`
- **Destination:** `escort_roster_entries` (single `INSERT...SELECT...ON CONFLICT(program_id) DO UPDATE`)
- **Trigger:** release-slip confirmation closing an escort program (`handle_admin_release_confirmation()` → `close_program()`)
- **Source data:** `wbom_escort_programs` + `wbom_employees` + the just-created `fazle_payment_drafts` row — mirrors the payment draft's calculated values rather than recomputing independently.
- **Transaction boundary:** wrapped by the caller (`handle_admin_release_confirmation`, :282-284) — `pool.acquire()` + `async with conn.transaction()` around close/backfill/draft-create/this insert.
- **Locking:** none explicit; atomicity comes from the single-statement upsert.
- **Financial authority:** calculated/derived, mirrored from the payment draft — not itself the payable source of truth.
- **Audit:** none — does not call `escort_roster.log_audit()` or write any audit table.
- **Idempotency:** yes — `ON CONFLICT(program_id) DO UPDATE`, `calc_version` incremented; safe to re-run.

### 2. `escort_roster.sync_program_to_roster()`
`modules/escort_roster/db.py:206-379`
- **Destination:** `escort_roster_entries` (atomic upsert) + conditional shift-log write via `_write_shift_log()`
- **Trigger:** admin roster API (`escort_roster/routes.py:724`), history/backfill sync (`history_sync.py:728,917`), escort auto-confirm/draft flow (`escort/__init__.py:1378,1388`), dispatch mutations (`dispatch/__init__.py:711,1149-1150,1276,1383,1581`). **Never** called from `escort_lifecycle`.
- **Source data:** independently recomputed from `wbom_escort_programs` + `wbom_employees` via `calculate_pay()`.
- **Transaction boundary:** accepts an optional `conn` so callers compose it into their own transaction (e.g. `dispatch/__init__.py` passes `conn=conn`); autocommit-per-statement when called without one.
- **Locking:** none explicit; single atomic upsert replaced an earlier SELECT-then-branch race, fixed Phase 1I (2026-07-18) per in-code comment (:212-232).
- **Financial authority:** calculated/estimated roster pay, not canonical payable.
- **Audit:** yes — `log_audit()` (:369-376) writes `escort_roster_audit_logs` with old/new JSONB.
- **Idempotency:** yes — upsert + `calc_version`.

### 3. `escort_roster.create_roster_entry()`
`modules/escort_roster/db.py:804-934`
- **Destination:** `escort_roster_entries` (manual insert) + `escort_roster_audit_logs` (via `log_audit`, :925)
- **Trigger:** admin "manually add a roster entry" UI/API (`escort_roster/routes.py:615`) — not part of the release-event flow.
- **Source data:** admin-supplied data (manual entry), not derived from `wbom_escort_programs`.
- **Transaction boundary:** wrapped — `pool.acquire()` + `async with conn.transaction()` around ID generation, duplicate check, insert, audit (:826-923).
- **Locking:** transaction-scoped Postgres advisory lock, `pg_advisory_xact_lock(hashtext('escort_roster.create_roster_entry'))` (:829), serializing concurrent callers — added Phase 2B (2026-07-18) to fix reproduced ID-collision and duplicate-bypass races.
- **Financial authority:** calculated estimate from admin input, not canonical payable.
- **Audit:** yes, via `log_audit()`.
- **Idempotency:** by design, no — raises `ValueError` on detected vessel/date/escort duplicates rather than upserting.

### 4. `payment_workflow.create_escort_payment_draft()`
`modules/payment_workflow/__init__.py:46-`
- **Destination:** `fazle_payment_drafts` (staging table)
- **Trigger:** escort release event (called from writer #1's flow, :349).
- **Source data:** flat-rate formula (`duty_days * 400tk`, :111) + `wbom_escort_programs.food_bill/conveyance` — "Formula 1, Owner decision 2026-07-10."
- **Financial authority:** draft/estimate pending admin approval, not payable.
- **Transaction/locking:** accepts optional `conn`, composes into caller's transaction; row-lock behavior not evident from this function alone.

### 5. `draft_approval.approve_draft()`
`modules/draft_approval/__init__.py:423-, :494-529`
- **Destination:** `fazle_payment_drafts` (status→approved, explicit `SELECT ... FOR UPDATE` row lock, :498-501) → `create_canonical_transaction()` → `fpe_cash_transactions`; audit via `_audit()` (:157-181) → `fazle_draft_audit_log`.
- **Trigger:** admin sends `PAID <id> <amount> <method>`.
- **Locking:** explicit row-level `FOR UPDATE` inside `conn.transaction()` — the only writer in this set with an explicit row lock.
- **Financial authority:** **actual payable amount** — writes the canonical, immutable `fpe_cash_transactions` ledger.
- **Idempotency:** `is_draft_already_processed()` pre-check (:484) plus `transaction_id` guard inside the lock (:511-517).

### 6. `fazle_payroll_engine.accounting.create_transaction()`
`modules/fazle_payroll_engine/accounting.py:30-`
- **Destination:** `fpe_cash_transactions` (insert-only, immutable ledger).
- **Trigger:** any payment posting — payroll payout, draft approval, admin corrections.
- **Idempotency:** `txn_ref` = sha256(message/employee/amount/period/method/source), checked pre-insert (:43-51) — safely re-runnable.
- **Transaction boundary:** `async with db_conn() as conn:` wraps the insert; locking model **not evident from code — needs runtime verification**.
- **Financial authority:** actual payable/paid amount, canonical.

### 7. `payroll.compute_run()`
`modules/payroll/__init__.py:85-275`
- **Destination:** `wbom_payroll_runs` + `wbom_payroll_run_items` + `wbom_payroll_approval_log`, one `pool.acquire()`/`conn.transaction()` block (:190-248).
- **Trigger:** monthly payroll compute (scheduler or admin-triggered `compute_all_for_period`).
- **Source data:** `wbom_employees.basic_salary`, completed `wbom_escort_programs` in period, `fpe_cash_transactions` advances.
- **Locking:** none explicit; relies on partial unique index `idx_payroll_runs_unique_active` + catching `asyncpg.UniqueViolationError` (:181-260) — constraint-based, not advisory/row locks.
- **Financial authority:** calculated draft/estimate (`status='draft'`), not yet payable.
- **Idempotency:** yes, explicit pre-check plus constraint-backed fallback.

### 8. `payroll._transition()` / `_post_payroll_ledger_entry()`
`modules/payroll/__init__.py:344-487`
- **Destination:** `wbom_payroll_runs` (status update), `wbom_payroll_approval_log` (audit, every transition); on `target="paid"` → `fpe_cash_transactions` via writer #6's `create_transaction()` (never a raw INSERT, per :448 comment).
- **Trigger:** admin FSM actions (submit/approve/lock/mark_paid/cancel).
- **Financial authority:** the `paid` transition writes the actual payable amount to the canonical ledger; fails closed with no linked FPE employee (:368-378).
- **Idempotency:** `payout_idempotency_key` built from emp+period+ref (:396-400) prevents double disbursement.
- **Locking: not evident from code — no explicit row/advisory lock seen; needs runtime verification for concurrent transitions.**

### 9. `payment_correction.reverse_payment()` / `adjust_payment()`
`modules/payment_correction/__init__.py:38-127, 132-`
- **Destination:** `fazle_payment_drafts` (status update), `fazle_payment_correction_log` (audit, append-only), plus a counter-row in `fpe_cash_transactions` via `reverse_transaction()`.
- **Trigger:** admin-initiated payment correction/reversal.
- **Financial authority:** actual payable correction (counter-transaction), canonical.
- **Transaction/locking:** **`reverse_transaction()`, the draft UPDATE, and the correction-log INSERT read as three separate calls with no visible surrounding `conn.transaction()` — not confirmed atomic. Flagged for verification, not fixed here** (scope of this pass is documentation, not code change).
- **Idempotency:** guarded by a status check (blocks double-reversal) rather than a hash/key.

## Open question this document does not resolve

Writers #6, #8, and #9 have locking/atomicity properties marked "not evident
from code" above. That is a real gap in confidence, not just a documentation
gap — #9 in particular (payment correction/reversal, financial-authority
writer) not being visibly wrapped in one transaction is worth a dedicated
runtime-verification pass before it's treated as safe under concurrent
corrections. Raised here for Owner/management triage, not fixed in this pass.
