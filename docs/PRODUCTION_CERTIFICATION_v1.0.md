# Fazle Core — Production Certification v1.0 (Phase 1.5)

**Date:** 2026-07-09
**Scope:** Certify whether Phase 1 (Consolidation) is genuinely complete and Phase 2 (Dispatch → Billing → GL) may begin.
**Method:** Evidence-based only. Every claim below is backed by a live test run, a live production query (read-only), or a specific file/line reference. Where evidence was not gathered, this document says **Not verified** rather than assuming.
**Baseline:** `v1.0-consolidation-baseline` tag (commit `db33129`), plus the out-of-scope `efa1ee9` (REL-2026-07-W1) and the subsequent `8678e4b` Atlas update.

---

## Executive Summary

Phase 1's individual fixes (RBAC fail-closed, router remount, toordinal bug, credential rotation, schema bootstrap) hold up under fresh testing. But this certification pass found a **larger, previously-undocumented schema/migration governance gap** (~150 tables and ~88 columns live in production with zero corresponding migration file anywhere in the repo — up from the ~27-table gap closed in the previous pass), and a **payroll payment-formula situation that is not "two conflicting formulas" but three advisory formulas that none of them actually gate real payments** — real money moves via a free-text admin-typed amount with no cross-check against any of them. The RAG availability gap is also worse than previously documented: it is not restart-only, it recurs **daily at 18:00** via a scheduler job.

None of this is a fire — production is stable and the specific bugs fixed in the last pass are genuinely fixed. But "Article VII's freeze is genuinely satisfied" is not yet true in the strict sense the Constitution asks for. See **Certification Decision** at the end.

---

## Architecture Status

No change since `SYSTEM_ATLAS.md` §3 was last written — re-verified this session by re-reading `app/main.py`'s startup sequence and confirming the router/RBAC fixes are still live in the working tree. **PASS.**

---

## Security Status

- RBAC fail-closed (`modules/admin_commands/__init__.py`): still in place, and this session's fixture-isolation fix (see Testing Status) removed a false-negative test artifact around it. Verified via `unit/test_admin_commands.py` (27/27 pass) and `unit/test_rbac.py` (19/19 pass). **PASS.**
- `INTERNAL_API_KEY`: rotated last pass, still the value in `.env`; not re-rotated this session (not requested, no new leak found). **PASS.**
- Webhook signature enforcement: `modules/bridge_poller` and `app/main.py` log `[WEBHOOK_AUTH_AUDIT] unsigned bridge1 webhook accepted; enforcement disabled` on every unsigned request (confirmed live in `resilience/test_failure_recovery.py` output this session). This is a known, logged, intentional interim state — but it is a live security control that is currently a no-op. **PASS WITH DEBT** — Owner should confirm this is still intentional; it's been true for at least one full test-suite generation.
- AI/production boundary (Ollama read-only tools): not re-tested this session. **Not verified** (carried over from prior audit, no new evidence gathered).

---

## Testing Status

**Before this session:** the suite looked catastrophically broken — 376 setup errors, 193 failures when run as a single `pytest` invocation.

**Root-caused, not guessed.** Two compounding, unrelated causes:

1. **e2e (Playwright) tests must never share a process with the async DB suite.** Running `pytest` over the whole `tests/` tree in one process corrupts the shared asyncio event loop for every async test that runs after the e2e tests start (`RuntimeError: Cannot run the event loop while another loop is running`). Excluding `e2e/` alone dropped errors from 376→27 and failures from 193→40. This is pre-existing, not caused by this session's changes. **Recommendation: run e2e as its own separate `pytest tests/e2e` invocation, never combined with the rest of the suite** — this should be a hard rule in CI and in `docs/DEVELOPER_GUIDE.md`.
2. **The test-harness's own hand-rolled SQL statement splitter (`tests/conftest.py::_split_sql_safe`) never handled `--` line comments**, despite its docstring claiming it did. Any migration file with a `--` comment containing an internal semicolon (extremely common in pg_dump-style headers, e.g. `-- Name: x; Type: TABLE; Schema: public; Owner: -`) got mis-split into fragments, corrupting the statement stream for every migration after that comment. This produced 676 "tolerated" errors in migration 029 alone (cosmetic noise) but also **silently dropped real `ALTER TABLE ... ADD COLUMN` statements** in older migrations (002, 010), which cascaded into ~20 real test ERRORs (`canonical_employee_id`, `reversal_of` columns missing). **Fixed** — `_split_sql_safe` now consumes `--...\n` verbatim before any other tokenizing (test-only file, no production impact).

**After both fixes + 3 additional safe fixes** (see below), excluding e2e:

| Metric | Before | After |
|---|---|---|
| Setup ERRORs | 27 | **0** |
| FAILED | 40 | **29** |

### Safe fixes implemented this session

| # | File | Change | Risk |
|---|---|---|---|
| 1 | `tests/conftest.py` | `_split_sql_safe` now handles `--` line comments | test-only |
| 2 | `db/migrations/030_reconcile_drift_columns_payment_drafts_replies.sql` (new) | Adds `fazle_payment_drafts.approved_at`, `fazle_draft_replies.{sent_at,error_text,edited_at}`, and the partial unique index `idx_fpe_emp_phone_unique` — all three confirmed **already live in production** via read-only query, with zero corresponding migration anywhere in the repo | additive, idempotent, documents existing prod state only |
| 3 | `tests/conftest.py` | New `autouse` fixture clears `modules.admin_commands._dedup_cache` (a module-level in-process LRU) before/after every test — it has no TTL-aware test isolation, so two tests sending the same admin-command text within 30s collided | test-only |
| 4 | `tests/test_fpe_accounting.py` | `TestReverseTransaction`'s hand-built mock `original` dict was missing `employee_id_phone`/`employee_phone`, which `reverse_transaction()` genuinely reads — added them | test-only |

### Remaining 29 failures — classified, not fixed (all Category C except where noted)

| Cluster | Count | Category | Root cause | Fix recommendation |
|---|---|---|---|---|
| Direct escort-release finalization now requires admin confirmation | 15 (`workflows/test_escort_payment_flow.py`, `workflows/test_full_workflows.py`) | **C** — test fixture drift | `modules/escort_lifecycle/__init__.py`'s `handle_release_event()` added an `admin_confirmed: bool = False` safety gate on 2026-06-19 (commit `da64367`) — a genuine, desirable safety control. Tests were never updated to pass `admin_confirmed=True` or send a `[RELEASE CONFIRMED]` message. | Update the test suite's release-trigger helper(s) to match the current, intentional API contract. Bounded but touches ~15 call sites across 2 large files — treat as its own Tier-1 task, not a certification-pass drive-by. |
| Stale call signatures (`compute_run`, `save_attendance`, `finalize_payment`) | 3 (`test_full_workflows.py`) | **C** | Real signatures require `period_year`/`attendance_date` positionally and don't accept a `amount=` kwarg on `finalize_payment`; tests call an older shape. | Update call sites to current signatures. |
| `test_two_employees_independent_payrolls` | 1 | **C** | Test asserts "employee with a 3-day program should out-earn a no-program employee" using fixture salaries (8000 vs 10000 basic) where the real per-day rate (400 ৳/day × 3 days = 1200) doesn't close a 2000 gap. Not a bug — a numerically-unverified test assumption. Directly relevant to Task 2 below. | Fix fixture numbers so the assertion is actually guaranteed, or assert the delta directly instead of an ordering. |
| `test_attendance_cascade_delete_with_employee`, `test_transaction_cascade_delete` | 2 (`db/test_db_consistency.py`) | **C**, but flags a *good* thing | Tests assert that deleting an employee cascades and removes their attendance/cash-transaction rows. The actual FK has no `ON DELETE CASCADE` — deletion is blocked instead. Silently cascade-deleting a person's financial transaction history would be an audit-trail violation; the current behavior is the safer one. | Fix the test's expectation (block-on-delete, not cascade), don't add `ON DELETE CASCADE` to fix the test. |
| `test_missing_sender_field_returns_422` | 1 | **F** — doc/behavior mismatch | Webhook endpoint always returns 200 (`[WEBHOOK_AUTH_AUDIT] unsigned bridge1 webhook accepted; enforcement disabled`) even for a payload missing `from`. Could be intentional (never break a bridge's retry/ACK contract) or an oversight. | Owner/engineering decision needed on whether malformed payloads should 422. |
| `test_concurrent_messages_no_corruption`, `test_concurrent_employee_queries` | 2 | **C** | `/employees` returns 404 (route doesn't exist under that path — check actual mount path) and a KW_FLOOD/dedup suppression interacting with concurrent identical messages. | Needs its own short investigation; not chased further under this pass's time budget. |
| `test_none_returned_when_no_name_no_phone` | 1 | **C** vs intentional safety guard | Test expects an auto-created "Unknown" employee; code (present since the module's initial 2026-05-30 commit) refuses and logs `REVIEW REQUIRED — no phone and no name — cannot create phantom employee`. The code's behavior is the safer one (no phantom employee records). | Owner decision: confirm "no phantom employees" is the intended policy, then fix the test to match. |
| `test_sweep_once_stub_mode` | 1 | **Not verified** | Outbound queue sweep picks 0 rows instead of ≥1 in stub mode. | Needs dedicated investigation; not chased further under this pass's time budget. |
| `test_concurrent_payroll_compute` | 1 | **E** — real product finding | Concurrent `compute_run()` calls for the same employee/period raise `UniqueViolationError` on `idx_payroll_runs_unique_active` instead of the second caller detecting the existing row and returning it gracefully — a genuine race condition in the idempotency check (`SELECT` then `INSERT` is not atomic under concurrency). | Real, if narrow, concurrency bug. Low production risk today (no evidence humans double-submit payroll compute in production — see Task 2 findings), but should be fixed with `ON CONFLICT DO NOTHING`/advisory lock before Phase 2's dispatch engine adds more concurrent writers. |
| `test_advance_deducted_in_payroll`, `test_payroll_run_items_are_complete` | 2 | **C**, downstream of the release-confirmation gate above | Advance items never materialize because the upstream release flow that would create them is blocked by the same `admin_confirmed` gate the test never passes. | Same fix as the release-confirmation cluster. |

**No new `xfail` added. No test skipped or weakened.** All 4 fixes above make previously-failing assertions pass on their own real merit — no assertion was loosened.

---

## Database Status — the headline finding

A full `information_schema` diff was run between live production and everything the tracked migration set (`db/migrations/` + `modules/fazle_payroll_engine/migrations/`) would produce in a clean database. This is new evidence this session — the previous 27-table bootstrap (migration 029) closed one gap but this is a different, larger one found by comparing the *complete* schema, not a hand-picked table list.

- **~150 tables exist in production with no corresponding migration file anywhere in the repo.** Examples: `fazle_clients`, `fazle_users`, `wbom_cases`, `escort_calculation_config`, `organizations`/`organization_users`, `voice_clones`, `telephony_events`, `alembic_version`, `schema_migrations`, several `_legacy_*` and `*_bak_*` tables. Many of these are plausibly legacy/experimental/backup tables outside the 24-27 "core" tables migration 029 targeted — but that has not been individually verified table-by-table, and `alembic_version`/`schema_migrations` existing at all suggests at least one other migration tool was used against this database at some point outside this repo's own tooling.
- **88 columns exist in production that are missing from the same-named table in a from-scratch migration build**, on tables that ARE tracked, including live business tables: `wbom_contacts` (18 missing columns), `wbom_cases` (17), `wbom_salary_records` (12), `fazle_payment_drafts.approved_at` (now fixed in migration 030), `fazle_draft_replies` (3, now fixed), `wbom_escort_programs.total_days`, `wbom_payroll_runs.remarks`, and more.
- **24 columns exist in the migration files but NOT in production** (the reverse direction) — e.g. `wbom_salary_records.amount`/`.created_at`/`.record_id` vs production's `.total_advances`/`.net_salary`/`.program_allowance` naming, suggesting a column rename happened in production that the migration files never picked up.

**What this means concretely:** if production's database were ever lost and rebuilt from this repo's migrations alone, the result would be missing ~150 tables and ~88 columns that live application code currently reads and writes. This is a disaster-recovery risk, and it is **larger than what Migration Governance documentation currently states** (`db/MIGRATION_INVENTORY.md` describes the 3-legacy-path problem but not this scale of undocumented drift).

**This session's action:** fixed the narrow slice with direct test-suite evidence (migration 030 — 3 columns + 1 index, all individually confirmed live in production first). **Did not** attempt to backfill the other ~85 columns / ~150 tables — that requires careful per-table review (some may be genuinely obsolete/safe to leave undocumented; the `_legacy_*`/`*_bak_*` ones look intentionally retired) and is out of scope for a certification pass under "no new features, minimal fixes only." Recommended as a dedicated, scoped **Schema Reconciliation Sprint** — see Phase 2 Prerequisites.

**Migration Governance status: PASS WITH SIGNIFICANT DEBT**, not PASS. The previous pass's documentation described the problem as smaller than it is.

---

## Documentation Status

`SYSTEM_ATLAS.md`, `README.md`, `docs/DEVELOPER_GUIDE.md`, `docs/MODULE_MAP.md`, `docs/NEXT_IMPLEMENTATION_QUEUE.md` all exist and were internally consistent as of the last pass. This session's findings (schema drift scale, dual-formula analysis, RAG's daily recurrence) require updates — done as Task 6, synchronized against this document. **PASS**, contingent on the Task 6 sync actually landing (it has, see below).

---

## AI Status

**Not verified** this session beyond confirming the RAG subsystem's behavior (see below) and that Ollama/AI console code paths weren't touched. No new evidence gathered on the AI console's read-only tool boundary.

---

## Knowledge Base Status

Not re-audited this session — carried over from memory of prior governance work (KB v2.0 certified, Phase 6 conditional-refactoring). **Not verified** in this pass; re-verify KB governance state directly before relying on it, since that memory predates this session's evidence-based findings.

---

## Finance Status — payroll pay-formula certification (Task 2)

The audit asked to identify "Formula A" vs "Formula B" and pick one canonical. **Real production evidence changes the framing**: there are three formulas, not two, and evidence shows none of them currently gates a real payment.

### The three formulas, with evidence

| | Where | Formula | Feeds |
|---|---|---|---|
| **A** | `modules/payroll/compute_run()` | `program_allowance = total_days × 400 ৳/day` (flat, company-wide default `DEFAULT_PER_PROGRAM_RATE`), `gross = basic_salary + program_allowance`, no food/conveyance deduction at all | `wbom_payroll_runs` / `wbom_payroll_run_items` (monthly aggregation, has a draft→reviewed→approved→locked→paid state machine) |
| **B** | `modules/escort_roster/calculations.py::calculate_pay()` | `salary = total_shifts × 200 ৳/shift` (= 400/day equivalent — numerically matches A's default), `total = salary + conveyance` **(added)**, `net_payable = total − food_bill` | `escort_roster_entries` — a roster/reconciliation table, not itself a payment trigger |
| **C** | `modules/payment_workflow/create_escort_payment_draft()` | `daily_rate = basic_salary ÷ 30` **(personalized per employee, not flat 400)**, `net_payable = gross − food_bill − conveyance` **(conveyance subtracted — opposite sign convention from B)** − advances | `fazle_payment_drafts` (`draft_type='escort_payment'`) |

### Production evidence (read-only queries against the live DB, 2026-07-09)

- **`wbom_payroll_runs`: 703 rows total, ALL in `status='draft'`, ALL with `total_programs = 0`.** Formula A's escort-program component has never contributed a nonzero amount in any real payroll run, ever. The state machine (draft→reviewed→approved→locked→paid) has never progressed a single row past `draft`.
- **`fazle_payment_drafts` with `draft_type='escort_payment'`: 47 rows total, ALL `status='expired'`.** Formula C has never resulted in one completed payment.
- **`fpe_cash_transactions`: 3,671 rows, all `source IN ('whatsapp','migration','operator','manual')`, `program_id` is `NULL` on every single row.** Real money movement (2,606 WhatsApp-sourced 'salary' transactions) happens through a path that never populates `program_id`, i.e. is not linked to an escort program by either Formula A or C's own bookkeeping.
- **The actual payment-recording command** (`modules/admin_commands/__init__.py`, `_PAID_RE = r"^paid\s+(\d+)\s+(\d[\d,]*)\s*(bkash|nagad|cash)?$"`) accepts a **free-typed amount from the admin** and passes it straight through to `create_transaction()`. Formula C's draft text *suggests* a `net_payable` figure ending in "PAID <id> {net_payable} bkash" — but the admin can type any number and the system records it with no cross-check against any formula's output.

### Conclusion

None of the task's three offered options ("A canonical," "B canonical," "both valid for different cases") fits the evidence cleanly. **The real finding is a control gap, not a formula conflict**: three formulas exist, each computing an advisory suggestion, but the actual money-recording step accepts an arbitrary admin-typed number with zero automatic reconciliation against any of them. Today's low incident risk is incidental — it depends entirely on admins manually calculating correctly and not being confused by which draft/roster figure to trust — not on any system-enforced control.

**This session did not silently pick a canonical formula and code it in** — that is a business-policy decision (should escort pay be a flat company-wide day-rate, or `basic_salary/30`, and should conveyance be added to pay or deducted from it?), not an engineering call, and picking one would be a real behavior change requiring Owner sign-off, out of scope for "minimal fixes only."

### Recommendation (needs Owner decision, not code, right now)

1. Pick ONE canonical formula and rate policy (flat rate vs. per-employee `basic_salary/30`; conveyance added-to-pay vs. deducted-from-pay).
2. Wire the `PAID` command to compute the expected amount from that canonical formula and require explicit override confirmation if the admin's typed figure differs by more than a small tolerance — this is the actual control gap, and closing it matters more than which formula wins.
3. Decide whether `wbom_payroll_runs` (currently 703 inert draft rows) is meant to be the enforced payment pipeline going forward, or should be retired in favor of the per-release draft flow, or vice versa — right now neither is actually used to gate payment.

This is flagged as a **Phase 2 prerequisite**, not a Phase 1.5 blocker in itself — but Phase 2b (Client Billing / GL) explicitly depends on trusting the cost-side ledger's category/program linkage, and `program_id` being NULL on every transaction plus three unenforced formulas means that trust does not yet exist.

---

## Escort Status

Dual-write between `escort_lifecycle` and `escort_roster_entries` (already documented in `NEXT_IMPLEMENTATION_QUEUE.md` Tier 2) is unchanged. The admin-confirmation release gate (2026-06-19) is a real, working safety control — confirmed via code and via the 15 test failures it causes in a stale test suite (see Testing Status). **PASS WITH DEBT.**

---

## Payroll Status

See Finance Status above. **PASS WITH SIGNIFICANT DEBT** — the mechanism works for basic salary (2,606 real transactions prove that), but the escort-duty pay computation layer is effectively unused/unenforced in production today.

---

## Deployment Status

No build step; service runs directly off the git working tree (`run.py` → uvicorn). Unchanged since last pass. `docs/DEPLOYMENT_RESTART_GATE.md` remains a design, not a mechanically-enforced gate. **PASS WITH DEBT** (documented, not fixed — matches prior pass's finding, not newly discovered).

---

## RAG Certification (Task 3)

Prior documentation (from the last pass) described this as a restart-only, one-time-per-restart availability gap. **New evidence this session shows it is worse: it recurs automatically every day, not just on restart.**

### Why the rebuild occurs

Two independent triggers call the exact same full rebuild function, `modules/rag/__init__.py::build_index()`:
1. **On every service restart** — `app/main.py:297`, `asyncio.create_task(rag.build_index())`, fire-and-forget (doesn't block `/health`, confirmed both by code and last pass's live observation).
2. **Every day at 18:00** (`RAG_REBUILD_HOUR`, default 18) — `modules/scheduler/__init__.py:579`, `CronTrigger(hour=rag_rebuild_hour, minute=0)` calling `job_rag_rebuild()` → `rag.build_index()`. **This was not previously documented as a recurring, scheduled event** — it is, and 18:00 is a plausible high-traffic hour for a WhatsApp-driven business.

### Whether embeddings are persisted

Vectors are stored in Qdrant, a real persistent vector store — but `build_index()` (lines ~625-686) **unconditionally drops the entire Qdrant collection and re-encodes every chunk from scratch** on every single call, regardless of whether the underlying text changed. Qdrant's persistence is real but is defeated by the rebuild routine's own drop-and-recreate design.

### Whether startup/scheduled rebuild can reuse existing vectors

Yes, in principle — the code already computes a **deterministic UUID per chunk** (`uuid5(NAMESPACE_DNS, f"{source}:{chunk_idx}")`), with an explicit comment: *"Enables upsert semantics and future incremental sync."* The author already anticipated this improvement; it was never implemented. A content hash stored in each point's payload, checked before re-encoding, would let unchanged chunks skip encoding entirely.

### Whether incremental rebuild is possible

Yes — straightforward given the existing deterministic-ID design: add a `content_hash` field to the point payload, diff against current source content, only encode chunks whose hash changed, and only delete points whose source was actually removed (instead of dropping the whole collection).

### Whether the previous index can stay online during rebuild

Currently **no**, for two independent reasons: (1) `client.delete_collection(...)` is called before rebuilding, so there is a window where the collection doesn't exist at all; (2) `_IDX.lock` (an `asyncio.Lock`) is held for the *entire* build duration including the CPU-bound encode step, and both `rag.search()` and `/rag/stats` block on that same lock. A safe design would build into a shadow collection and atomically swap the alias only once ready, decoupled from the in-memory lock.

### Whether restart/scheduled rebuild blocks customer replies

**Yes, confirmed.** `rag.search()` is in the customer-facing reply path via `message_router`. Last pass's live observation after a manual restart showed 15-20 minutes of blocking on `/rag/stats` and `rag.search()`, with CPU sustained at 420-590% (confirmed actively computing, not deadlocked) before full recovery. This same blocking now provably recurs **daily at 18:00** via the scheduler, independent of restarts.

### This session's action

Per the task's own instruction ("Do NOT redesign now — produce a production improvement proposal"), no code change was made. Documentation updated (Task 6) to correct the "restart-only" framing to "restart + daily 18:00, whichever comes first" and escalate this from Tier 2 to **Tier 1** given the confirmed daily recurrence and customer-facing impact.

---

## Phase 2 Readiness Review (Task 4)

| Area | Verdict | Evidence |
|---|---|---|
| Security / RBAC | PASS | 27/27 + 19/19 tests pass this session |
| Secrets | PASS | `INTERNAL_API_KEY` rotated last pass, unchanged this session, no new leak found |
| Restart | PASS WITH DEBT | Works; RAG blocks reads for 15-20 min, now confirmed to also recur daily regardless of restart |
| Deployment | PASS WITH DEBT | No build step / no mechanical restart gate (documented, unchanged) |
| Migration governance | **FAIL** | ~150 tables / ~88 columns of undocumented prod-vs-migration drift found this session — larger than previously known |
| Documentation | PASS | Synced this session (Task 6) |
| Test suite | PASS WITH DEBT | 0 setup errors (was 27), 29 real failures remain, all classified, none blocking Phase 2 directly except the concurrency bug noted below |
| Knowledge Base | Not verified | Not re-audited this session |
| SYSTEM_ATLAS / Dev Guide / Module Map / Queue | PASS | Updated this session |
| Database | **FAIL** | Same as migration governance — this is the same underlying finding |
| RAG | PASS WITH SIGNIFICANT DEBT | Daily recurring 15-20 min customer-facing availability gap, confirmed this session, escalated to Tier 1 |
| AI Console | Not verified | Out of this session's evidence-gathering |
| Payroll | **FAIL** (as a *control*, not as "money moves incorrectly") | Three unenforced formulas, `program_id` never populated, 703 payroll runs stuck in draft, 47/47 escort payment drafts expired — Phase 2b (Billing/GL) needs to trust this layer and cannot yet |
| Escort | PASS WITH DEBT | Admin-confirmation gate works; dual-write to `escort_roster_entries` still open (known, Tier 2) |
| Finance | PASS | Article I's `create_transaction()` single-writer rule intact; the gap is upstream (what amount reaches it), not the ledger itself |
| Recruitment | Not verified | Not in this session's scope |
| Message Router | PASS (indirect) | No failures attributed to `message_router` in this session's classified 29; existing tests pass |
| Identity Brain | PASS (indirect) | Same as above |

---

## Known Technical Debt

- e2e/unit test-process isolation (must run separately)
- Release-confirmation-gate test staleness (~15 tests, Tier 1)
- Stale call-signature tests (`compute_run`/`save_attendance`/`finalize_payment`, 3 tests)
- Concurrent `compute_run()` race on `idx_payroll_runs_unique_active` (real, narrow concurrency bug)
- `escort_lifecycle`/`escort_roster_entries` dual-write (pre-existing, documented)
- Cross-directory migration collisions on 5 tables (pre-existing, documented)
- Frontend shared-fetch-utility duplication, RBAC-dependency duplication (pre-existing, documented)
- ~85 remaining undocumented drift columns / ~150 undocumented tables (new, this session — the large one)
- RAG daily 15-20 min customer-facing availability gap (escalated Tier 2 → Tier 1 this session)
- Payroll: three unenforced pay formulas, `program_id` never populated on real transactions, 703 inert draft payroll runs, 47/47 expired escort payment drafts

## Accepted Technical Debt

- HR-officer dead API keys/passwords cleanup (Owner-deferred, no active risk)
- No mechanical restart gate (design exists, not wired in — Owner has not asked for this to be built yet)
- Webhook signature enforcement disabled (logged, presumably intentional interim state — needs a fresh Owner confirmation given how long it's been true)

## Blocking Issues

None of the findings above have caused an active incident. Nothing here requires an emergency fix before the next restart. The blocking question is whether Article VII's freeze conditions are **genuinely** satisfied before Phase 2 starts — see Certification Decision.

## Phase 2 Prerequisites

1. Owner decision on the payroll pay-formula/control-gap question (Finance/Payroll Status above) — Phase 2b (Billing/GL) cannot safely build revenue-side postings on top of a cost-side ledger whose linkage integrity (`program_id`) is currently zero.
2. A scoped Schema Reconciliation Sprint (or at minimum, an explicit Owner-accepted risk statement) for the ~150-table/~88-column migration gap — this is a disaster-recovery risk independent of Phase 2, but Phase 2's new tables will make the reconciliation harder the longer it's deferred.
3. RAG daily-rebuild fix (or at minimum a scheduled-hour change to a genuine off-hours window plus an Owner-acknowledged interim-risk note) before Phase 2 adds more real-time customer-facing dependencies on RAG.
4. Test-suite hygiene: separate e2e from the rest of CI; update the ~15 release-confirmation-gate tests; these are process fixes, not blockers, but should not be inherited into Phase 2's own test suite.

---

## Certification Decision

**NOT CERTIFIED FOR PHASE 2 — CONDITIONAL, NARROW GAP.**

Reasoning: `BUSINESS-CONSTITUTION.md` Article VII requires the System Consolidation Sprint to be genuinely complete, not just declared. Two concrete, evidence-based items are still open that speak directly to that requirement: (1) the migration/schema governance gap is real and larger than previously stated, and (2) the payroll pay-formula situation is a live control gap on the cost side of the ledger that Phase 2b explicitly needs to trust. Neither is an emergency — production is stable, the previous pass's specific bug fixes hold — but neither is resolved either, and both are the Owner's decision to make (accept the risk explicitly and proceed, or close them first), not an engineering unilateral call.

This is **not** a "start over" verdict. Dispatch/scheduling (Phase 2a) does not depend on either open item — it reads guard availability and escort program data, not the payroll ledger. **A defensible alternative for the Owner to consider: begin Phase 2a (dispatch) now, since it has no dependency on the two open items, while the Schema Reconciliation Sprint and the payroll-formula decision happen in parallel, and gate Phase 2b (Billing/GL) specifically on those two items closing.** That would respect Article VII's spirit (don't build revenue/GL logic on an untrusted cost ledger) without stalling all forward progress on the item the Owner already prioritized first (dispatch over accounting, per the 2026-07-08 decision).
