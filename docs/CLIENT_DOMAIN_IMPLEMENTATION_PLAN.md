# Client Domain Implementation Plan — Fazle Core

**Date:** 2026-07-09
**Status:** Roadmap only. Nothing in this document has been implemented, migrated, or deployed.
**Companion document:** `docs/CANONICAL_CLIENT_DOMAIN.md` (the architecture and evidence this plan executes against — read that first).

This plan intentionally stops short of Client Billing, Accounts Receivable, and General Ledger implementation, per this task's explicit scope. It builds the foundation those depend on.

---

## Phase 2A — Client Domain Foundation

**Status update, 2026-07-09: migration APPLIED to production (Owner-approved).** `db/migrations/031_client_domain_reconciliation.sql` implements objectives 1-2 below in full, with every mapping decision disclosed in the file's own header. Schema-level parts validated via the test-harness migration runner; data-level parts confirmed via a transaction preview (applied + inspected + rolled back) before the real, committed application. **Owner decision on `supervisor`/`accountant`/`blocked`:** none become relation types; `supervisor`/`accountant` map to the existing `Employee` type only where independently verified against `wbom_employees` (all 6 real cases matched); `blocked` sets `is_active=false` only, never a relation type. **`.env` authority rule established:** `.env`-declared identity lists (`ADMIN_NUMBERS`, `ACCOUNTANT_PHONE`, `FPE_CASH_AUTHORIZED_PHONES`, `FPE_INCOME_AUTHORIZED_PHONES`) are mandatory/authoritative; `wbom_contacts`/`wbom_employees`/cash-derived signals are enrichment-only — verified all 22 `.env`-listed numbers before applying, confirmed none were misclassified. Objective 3 (increasing `wbom_escort_programs.contact_id` adoption at intake, currently 17%) remains not started — a separate, future application-code change.

**Objectives:**
1. Make `wbom_contacts.relation_type_id` (+ `wbom_relation_types`) the single source of truth for "is this a client," reconciling the two competing mechanisms found (`wbom_contacts.relation` free text, `fazle_contact_roles.role` free text).
2. Fix the `wbom_relation_types` casing inconsistency (`'escort_client'` → `'Escort Client'`).
3. Increase `wbom_escort_programs.contact_id` adoption at intake time (currently 17%) so new programs are linked to a client by default, not as an afterthought.
4. Decide (Owner input required) whether `wbom_business_types` categorization is worth activating now or can wait for Phase 2B.

**Database changes:**
- One data-fix migration: `UPDATE wbom_relation_types SET relation_name='Escort Client' WHERE relation_type_id=5;` — additive-safe, no structural change, easily reversible.
- No new tables. No column drops. `fazle_clients` is flagged for archival but **not dropped in this phase** — confirm zero production usage over a monitoring window first (see Risk below).
- A reconciliation migration mapping `fazle_contact_roles`'s 47 rows onto `wbom_contacts.relation_type_id` (exact mapping to be decided with Owner input on the finer-grained values — `vip_client`/`repeat_client`/`blocked` don't have a `wbom_relation_types` equivalent yet).

**API changes:**
- None required to ship the reconciliation itself (a data migration, not a new endpoint).
- `modules/contact_roles/routes.py`'s CRUD API should be updated to write through to `wbom_contacts.relation_type_id` going forward (or explicitly frozen/read-only) once reconciliation completes, so it stops being a second, independent writer.

**Frontend changes:** None required for this phase — this is backend/data-model consolidation.

**Migration impact:** Low. Both migrations are additive/corrective (UPDATE statements + reconciliation), not destructive, and act on tables with modest row counts (2,086 and 47 rows respectively).

**Risk:** Low. The main risk is the `fazle_contact_roles` → `wbom_contacts` reconciliation losing information if a phone number in `fazle_contact_roles` doesn't have a matching `wbom_contacts` row (orphan risk) — must be checked before the migration runs, not assumed away.

**Dependencies:** None — this phase depends on nothing else and can start immediately.

**Rollback:** The `wbom_relation_types` rename is trivially reversible (UPDATE back). The reconciliation migration should be written to be re-runnable/idempotent and should not delete `fazle_contact_roles` rows during this phase (only read them) — full reversibility until `fazle_contact_roles` is actually retired in Phase 2B.

**Testing:** A migration-runner test verifying `wbom_relation_types` has exactly 5 rows with the corrected casing; a reconciliation-coverage test verifying every `fazle_contact_roles` phone number either matches a `wbom_contacts` row or is explicitly logged as an orphan needing manual review.

**Success Criteria:** `wbom_relation_types` casing consistent; a documented, Owner-approved mapping exists for every `fazle_contact_roles` role value; zero orphaned phone numbers left unresolved.

**Definition of Done:** Migration applied to a scratch/staging DB and validated (following the same method used for migration 029/030 — pg_dump verification, not guesswork), reviewed, and queued for Owner-approved production application — **not applied to production in this phase** without separate, explicit go-ahead (matching this session's standing "no production DB changes" rule).

---

## Phase 2B — Client Billing Activation (billing-only; no GL)

**Migration prep status (2026-07-09):** `db/migrations/032_client_tier_status_model.sql` written, validated, and **fully applied to production** (Owner-approved) — `wbom_client_tiers`/`wbom_client_statuses` lookup tables live (4 rows each), `wbom_contacts.client_tier_id`/`client_status_id` live (nullable, FK-constrained, zero backfill, as instructed). **Process exception, disclosed:** during the transaction-preview dry run, the migration's `setval()` sequence-fix statement (Part A) permanently reached production despite the surrounding `ROLLBACK`, because Postgres sequence operations are non-transactional. Owner reviewed and accepted keeping this fix. Full incident write-up and the new dry-run methodology rule this produced: `knowledge_base/06_developer_system/database_rules.md` "Migration dry-run safety rule."

### Billing Activation commit plan (2026-07-09 re-audit — see `docs/PHASE2B_READINESS_REPORT.md` Re-Audit section for full evidence)

**Blocking finding:** no client-facing rate/price field exists anywhere in this schema (confirmed via 3 independent checks). Commits B-D below cannot be implemented until the Owner decides what determines `service_charge` — see the readiness report's "New Owner decisions required." Only Commit A is decision-independent and has been implemented this pass.

---

**Commit A — Invoice number generator (IMPLEMENTED this pass)**
- **Purpose:** A concurrency-safe, deterministic `AQS-YYYY-NNNNNN` generator, decoupled from the (blocked) bill-creation flow so it can be built, tested, and reviewed now.
- **Files:** `db/migrations/033_invoice_number_counters.sql` (new), `modules/billing/__init__.py` + `modules/billing/invoice_numbering.py` (new module — `modules/billing` created now specifically to hold this decision-independent piece; bill-creation code lands here too once unblocked).
- **Design:** A dedicated `invoice_number_counters(year INT PRIMARY KEY, last_number INT)` table, incremented via a single atomic `INSERT ... ON CONFLICT (year) DO UPDATE SET last_number = last_number + 1 RETURNING last_number` statement — **not** a raw Postgres `SEQUENCE`. This is a deliberate choice, the opposite lesson from the Migration 032 incident: a raw sequence's `nextval()` is non-transactional, so a number consumed by a transaction that later rolls back (e.g. the bill insert fails for an unrelated reason) is permanently lost, leaving a gap — undesirable for invoice numbering, where gapless/explainable numbering is a common real-world expectation. The counter-table row-lock approach correctly participates in the surrounding transaction (a rollback releases the reservation, no gap), at the cost of transactions serializing on that one row under concurrency — an irrelevant tradeoff at this system's actual volume (342 escort programs total, ~64 `Completed`). Formatted as `AQS-{year}-{last_number:06d}`, naturally resetting to 1 each new calendar year (a new row).
- **Risk:** Very low — a single new sequence object, a pure function wrapping it, no other table touched, no business data affected.
- **Validation:** Unit test calling the generator concurrently (asyncio.gather of N calls) asserting all N results are distinct; schema check via test-harness migration runner; scratch-DB check confirming the sequence survives a rollback test correctly (i.e., confirming understanding of its non-transactional nature is correct, not assumed).
- **Rollback:** `DROP SEQUENCE` — trivial, no dependent data exists yet.
- **Production impact:** Applied to production 2026-07-10; wired into real use by Commit C's `generate_bill()`. Still effectively unused in practice today since Commit C's billable-programs pool is empty (see Commit C's "known limitation" note).

---

**Commit B — Schema hardening (PARTIALLY IMPLEMENTED 2026-07-10 — see Commit C)**
- **Purpose:** Add a uniqueness constraint on `wbom_billing_records.program_id` (pending Decision 2: is at-most-one-bill-per-program correct, or does partial/multi-billing need to be supported?), constrain `payment_status` to a lookup table mirroring `client_tier`/`client_status` (pending Decision 3), add `updated_at`, add a `wbom_billing_audit_log` table mirroring `wbom_payroll_approval_log`'s pattern.
- **program_id uniqueness: IMPLEMENTED** via `db/migrations/036_billing_records_program_uniqueness.sql` — see Commit C for the disclosed default applied (one bill per program) and why.
- **Still not implemented** (still blocked on Owner decisions never made): `payment_status` lookup-table constraint, `updated_at` column, `wbom_billing_audit_log` table.

**Commit A.5 — Client Billing Profile (Owner Decision 1 fully resolved 2026-07-09; migration written + validated, code planned, neither applied/coded yet)**
- **Purpose:** Resolves what was Commit C's blocker — `service_charge` now comes from a new `wbom_client_billing_profiles` table + frontend Client Settings UI, not from payroll cost. Architecture: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md`. Backend/frontend spec: `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md`.
- **Status:** All 4 original sub-questions resolved (admin-selected billing type; VAT/Tax/Other Charges as flat, profit-estimation-only deductions; flat-amount-first with percentage as a documented future enhancement; required nullable `contact_id` FK to `wbom_contacts`). Migration `db/migrations/034_client_billing_profiles.sql` written and validated (schema check + scratch-DB constraint/FK tests) — **not applied to production.** Code plan complete — **no application code written.** 2 minor items remain open: hard-vs-soft delete, RBAC granularity for wage/profit visibility.

**Commit C — Bill generation (IMPLEMENTED 2026-07-10, as a manual admin action, not the originally-sketched automatic hook)**
- **Design change from this section's original sketch, disclosed:** the sketch above assumed an automatic call inside `modules/escort_lifecycle::_handle_release_event_tx()`, firing on every first-time `Completed` transition. That flow is triggered by a live WhatsApp `[RELEASE CONFIRMED]` admin message and has no field anywhere — not on `wbom_escort_programs`, not parsed from release text — to carry a duty-type/billing-frequency selection (General/24H/12H/Monthly is a distinct concept from the existing start/end `shift` D/N field). Silently auto-generating a real, invoice-numbered client bill inside a live production messaging path, using a guessed default duty_type, was judged higher-risk than a new opt-in admin action — especially since, at implementation time, **zero Completed programs had a linked `contact_id`** (the 59/342 `contact_id`-adoption figure is entirely on non-completed programs), so there was no live data to have validated an automatic path against. Wiring an automatic trigger into `escort_lifecycle` remains a separate, explicit follow-up decision, not assumed or deferred silently.
- **What was built:** `modules/client_billing/bills.py` (`generate_bill()`, `list_bills()`, `get_bill()`, `list_billable_programs()`), new routes on the existing `client_billing` router (`GET /admin/bills/billable-programs`, `GET /admin/bills`, `GET /admin/bills/{bill_id}`, `POST /admin/bills/generate`), and a "Generate Bill" section in `payroll.html`'s Admin tab (program+duty-type selector, other-charges input, bills list table).
- **Owner Decision Required #2 (program_id uniqueness) resolved** with a disclosed default: `db/migrations/036_billing_records_program_uniqueness.sql` adds a UNIQUE constraint on `wbom_billing_records.program_id` — one bill per program under normal operation, since no credit-note/multi-bill workflow exists or has been requested. Reversible (drop the constraint) if the Owner later wants multi-billing.
- **Governance finding while building this:** `wbom_billing_records` existed live in production but had never been created by any governed migration — the "4th ungoverned schema path via inline DDL" the System Consolidation Plan (2026-07-08) already flagged. Migration 036 also backfills `CREATE TABLE IF NOT EXISTS wbom_billing_records` (exact live schema, verified via `pg_dump --schema-only`) so the table is now in the governed path and the test harness can build it from scratch.
- **service_charge / total_amount:** `service_charge` = the client billing profile's rate for the selected duty_type; `total_amount` = `service_charge + other_charges` (the bill's own other_charges, entered per-bill — distinct from the client billing profile's flat `other_charges`, which stays a profit-*estimation*-only deduction per the Owner's original rule and is never added to the invoice total). `bill_number` generated via `next_invoice_number()` (migration 033, applied to production 2026-07-10) inside the same transaction as the INSERT, so a duplicate-program rejection correctly un-reserves the invoice number (no gap).
- **Validation:** migrations 033/036 schema-checked and scratch-DB validated before production application; 9 new unit tests (`tests/unit/test_bill_generation.py`) covering success, duplicate-program rejection, non-Completed rejection, missing-contact_id rejection, missing-profile rejection, rate-not-configured rejection, invalid duty_type, sequential invoice numbering, and billable-programs filtering; full regression re-run clean (29 pre-existing failures, same baseline, 0 new failures/errors). Applied to production and restart-verified live: all four new routes appear in the running OpenAPI schema and respond correctly against real production state.
- **Known limitation, not fixed by this pass:** as of 2026-07-10, zero Completed escort programs have a `contact_id` set, so `list_billable_programs()` returns empty in production today — this feature is correctly built but currently dormant until `contact_id` linkage reaches at least one Completed program with a matching active Client Billing Profile. A full live generate-bill call against a real production program was therefore not performed (doing so would have required fabricating a fake completed-program record, which was not authorized) — confidence instead comes from the schema-accurate unit-test suite plus live route/auth/empty-state verification.
- **Not built in this pass (unchanged from the original Commit D scope):** payment recording, outstanding-balance view, mark-paid action, bill deletion/void, audit logging on bills. These remain Commit D, still blocked on Owner Decision 3 (`payment_status` free-text vs. constrained lookup).

**Commit D — Payment recording, outstanding-balance view, readonly tools, admin API, dashboard, audit logging (BLOCKED — needs Owner Decision 1, plus B and C)**
- **Not implemented this pass** — depends on Commits B and C existing first.

---

**Owner decisions locked in 2026-07-09 (full detail: `docs/PHASE2B_READINESS_REPORT.md`):**
1. **Scope: billing-only.** No GL posting hook, no payroll-cost reconciliation — deferred to a later phase, gated on the payroll pay-formula decision being made separately.
2. **Client tier/status — final model (resolved 2026-07-09):** four separated dimensions, not one field. `relation_type` = identity (Phase 2A, unchanged). `client_tier` = business value (`Standard`/`Repeat`/`VIP`/`Strategic` — new field). `client_status` = business lifecycle (`Active`/`Inactive`/`Suspended`/`Closed` — new, *separate* field from `client_tier`). `is_active` = system interaction permission (Phase 2A, unchanged). **`'Blocked'` is exclusively `is_active=false`** — never a value in `client_tier` or `client_status`. A contact can be `client_tier=VIP, client_status=Active, is_active=false` simultaneously (an active VIP customer, administratively blocked).
3. **Billing trigger:** only on escort-program confirmed/completed final-client-slip state — never draft/running.
4. **Invoice numbering:** deterministic yearly sequence, `AQS-YYYY-NNNNNN`.
5. **Billing profile scope:** contact-level only. Client Contact/Client Branch (multi-person, multi-site) deferred to a later phase.
6. **`contact_id` enforcement:** required/resolved for new escort/client intake going forward. Existing rows stay nullable, backfilled over time — not retroactively enforced.

**Objectives:** Wire real application code to the already-correctly-designed `wbom_billing_records` table (bill generation on program completion, payment recording, outstanding-balance view) and add the two new fields, `client_tier` and `client_status`. Explicitly **not** in scope: GL, chart of accounts, payroll-cost linkage, Client Branch/Contact, and no `'Blocked'` value in either new field (that stays exclusively on `is_active`).

**Database changes:** Two new fields on `wbom_contacts` — `client_tier` (`Standard`/`Repeat`/`VIP`/`Strategic`) and `client_status` (`Active`/`Inactive`/`Suspended`/`Closed`), each either a small lookup table (mirroring `wbom_relation_types`) or a CHECK-constrained column (mirroring `escort_roster_change_requests`) — implementation detail, not a policy question; either satisfies the Owner's model. Neither field gets a `'Blocked'` value under any circumstance. `wbom_billing_records` needs no schema change — its `bill_number` UNIQUE constraint and FK structure already support this scope; only an invoice-number generator (`AQS-YYYY-NNNNNN`) needs to be built in application code. `wbom_escort_programs.contact_id` becomes a required field in the new-intake code path (application-level enforcement, not necessarily a DB NOT NULL given old rows stay nullable).

**API changes:** New endpoints/admin-command to generate a bill on program completion, list/view bills, mark paid, list outstanding balance per client.

**Frontend changes:** A billing view (new dashboard tab or extension of an existing client-facing view) — none exists today.

**Migration impact:** Low-to-moderate — one new column/table for client tier, an invoice-number-generator function, an intake-validation change for `contact_id`. No GL schema, no payroll changes.

**Risk:** Low relative to the original GL-inclusive scope, specifically because Decision 1 removed the payroll-formula dependency. Main remaining risk is the tier/`is_active` overlap (Decision 2's open note) if built without reconciling it first.

**Dependencies:** Phase 2A (shipped 2026-07-09) — a stable, single-source-of-truth client identity to bill against. No dependency on the payroll pay-formula decision for this narrowed scope.

**Rollback:** New column/table only — additive, reversible (no production billing data exists yet, `wbom_billing_records` still at 0 rows as of this decision).

**Testing:** End-to-end test: escort program reaches confirmed/completed final-client-slip state → bill auto-generated with correct `AQS-YYYY-NNNNNN` number → payment recorded → outstanding balance reflects it correctly. A duplicate-completion-event test (idempotency — does completing the same program twice generate two bills?) should be added given this system's general pattern of duplicate-webhook/message risk.

**Success Criteria:** A real bill is generated automatically from a completed escort program (not before), carries a correctly-sequenced invoice number, and the client's outstanding balance is queryable.

**Definition of Done:** Design doc + migration reviewed and validated (schema via test harness, data via dry-run/transaction-preview, matching the Phase 2A method) before any production application; Owner sign-off before production deployment, per this session's standing rule.

---

## Phase 2C — General Ledger Integration

**Objectives:** Post revenue-side transactions (from Phase 2B's billing) and cost-side transactions (existing payroll, once its formula/enforcement gap is resolved) into a real double-entry ledger.

**Database changes:** New chart-of-accounts and ledger-entry tables — genuinely new, per `CANONICAL_BUSINESS_RULES.md`'s General Ledger section confirming zero existing GL vocabulary in this codebase today.

**API changes:** New GL posting hook attached to `create_transaction()` (Article I-compliant extension, not a parallel writer) and to Phase 2B's billing completion event.

**Frontend changes:** P&L / balance-sheet views.

**Migration impact:** High — this is the largest new subsystem in the roadmap.

**Risk:** High if attempted before Phase 2B's client/billing foundation and the payroll cost-ledger trust question are both resolved — posting GL entries against an unreliable cost-side linkage (`program_id` NULL on all current transactions) would compound, not fix, the existing gap.

**Dependencies:** Phase 2B complete. Payroll pay-formula Owner decision resolved (from `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 0).

**Rollback:** N/A at planning stage.

**Testing:** A sample month's GL output reconciles to zero variance against a real bKash/bank statement import (per the original roadmap's own Phase 2b done-criteria in `~/.claude/plans/now-you-are-in-squishy-chipmunk.md`).

**Success Criteria / Definition of Done:** Matches the original roadmap's existing Phase 2b criteria — not redefined here, just sequenced correctly behind 2A and 2B.

---

## Sequencing note

This plan deliberately keeps Phase 2A small and low-risk — it is pure consolidation (one rename, one reconciliation, no new tables), matching the "foundation first" instruction. Phases 2B and 2C are described for roadmap continuity but are explicitly **not** to be started until Phase 2A ships and the Owner decisions in `docs/PHASE2_ENTRY_GATE.md` are resolved.
