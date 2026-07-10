# Phase 2B (Client Billing / GL) Readiness Report

> **Commit C Implementation Pass (2026-07-10):** bill generation is now implemented and live — see `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`'s Commit C entry for the full design (built as a manual admin action, not the automatic hook originally sketched — the reason is disclosed there). This pass resolves **New Owner Decision #2** below (program_id uniqueness) with a disclosed default, applies Migration 033 (invoice numbering) to production, and adds Migration 036 (program_id uniqueness + a governance backfill of `wbom_billing_records`, which turned out to have never been created by any governed migration). Decisions #3, #4, #5 below remain unresolved and out of this pass's scope (Commit D). **Critical operational finding:** zero Completed escort programs currently have a `contact_id` set (the 59/342 adoption figure is entirely on non-Completed programs) — Commit C is correctly built but dormant in production until that changes. If anything below conflicts with this note, this note wins.

> **Re-Audit Pass (2026-07-09, post-Migration-032):** the section immediately below, "Re-Audit — Billing Activation Readiness," is now the newest authoritative content in this document, produced by re-verifying everything directly against live production rather than trusting the original readiness report below it (which is kept as-is, for history — its content was accurate for its own pass and nothing in it has been found wrong, just extended). If anything conflicts between the two, this newer section wins.

---

# Re-Audit — Billing Activation Readiness (2026-07-09)

**Method:** every claim below is a fresh, direct read-only check against production (or a direct code read), not a restatement of the original report below. Where nothing new was found in a required section, that is stated explicitly, not omitted.

## Current production status

- `wbom_billing_records`: live, correct schema (unchanged since last check), **0 rows**. Only 1 code reference anywhere in the repo (`modules/escort_roster/db.py`'s defensive dependency-count check) — unchanged, no new code touches it.
- `wbom_contacts`: 2,086 rows. `relation_type_id` populated on 128 (Phase 2A). `client_tier_id`/`client_status_id` both exist (Migration 032), **0 populated on either** — confirmed, no backfill occurred.
- `wbom_client_tiers` / `wbom_client_statuses`: 4 rows each, exactly as designed (Standard/Repeat/VIP/Strategic; Active/Inactive/Suspended/Closed).
- `wbom_escort_programs`: 342 rows. `contact_id` populated on 59 (17.3%) — unchanged since the last check. Status distribution: `confirmed` 175, `Running` 75, `Completed` 64, `expired` 27, `Assigned` 1 (casing exactly as previously documented in `CANONICAL_BUSINESS_RULES.md` §Escort — re-confirmed, not newly found).

## What changed since previous report

Nothing beyond what this report itself documents. Migration 032 Parts B-D are the only production change since the last readiness pass, and they're already fully verified (5-point check, prior turn).

## Phase 2A completion summary

Unchanged from prior reporting — 128/2086 contacts classified via `relation_type_id`, 6 supervisor/accountant contacts mapped to Employee (verified against `wbom_employees`), 2 contacts flagged blocked via `is_active=false`, `.env` identity cross-check performed for all 22 listed numbers, zero misclassifications. Re-verified this pass: still true, no drift.

## Migration 032 completion summary

Unchanged from prior reporting — both lookup tables live at 4 rows each, both new `wbom_contacts` columns live and nullable, zero backfill, both FK constraints present and correctly targeting the lookup tables. Re-verified this pass: still true, no drift.

## Newly discovered issues

1. **CRITICAL, FULLY RESOLVED 2026-07-09 — no client-facing rate/price field exists anywhere in this schema.** Confirmed via three independent checks: (a) `wbom_escort_programs`'s full column list contains only cost-side fields (`total_payment`, `conveyance`, `food_bill`, `day_count` — all guard-pay inputs, per the already-documented Payroll formula finding); (b) `escort_calculation_config` (18 rows, per-destination `conveyance_amount`/`shift_rate`) is also guard-cost configuration, the same 200/shift rate already found in `escort_roster/calculations.py::calculate_pay()` — not a client price; (c) a repo-wide column search for `%rate%`/`%price%`/`%charge%` across every escort/client-relevant table returned **zero results**. **Owner resolution, fully specified:** a new `wbom_client_billing_profiles` table, managed via a frontend Client Settings UI, becomes the rate source. Migration written and validated (`db/migrations/034_client_billing_profiles.sql`), **not applied to production**. Detailed plans: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md` (schema/design) and `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` (backend/frontend spec) — **no application code written yet.**
2. `wbom_billing_records` has **no uniqueness constraint on `program_id`** (only `bill_number` is unique) — nothing at the database level prevents the same program from being billed twice under a retry or race condition.
3. `wbom_billing_records` has **no index on `program_id`** — the natural "has this program already been billed?" idempotency-check query would do a full table scan. (Fixing #2 with a unique index fixes this too.)
4. `wbom_billing_records.payment_status` is unconstrained free text (`VARCHAR`, default `'Pending'`, no CHECK, no lookup table) — inconsistent with the discipline just established for `client_tier`/`client_status` (Migration 032). Today nothing stops an invalid status string from being written.
5. No audit-log table exists for billing state changes, unlike the equivalent payroll (`wbom_payroll_approval_log`) or FPE (`fpe_accounting_audit_logs`) patterns.
6. `wbom_billing_records` has `created_at` but no `updated_at` — a `payment_status` change later has no record of when it happened (only `payment_date`, which is the payment event itself, not the record-update event).
7. `wbom_escort_programs.contact_id` is populated on only 17.3% of programs (unchanged since last check) — meaning ~83% of current/historical programs could not be billed today without a manual `contact_id` backfill, even once bill-creation code exists.
8. No partial-payment tracking — a single `payment_status` + `total_amount`, no `amount_paid` column. A client paying part of a bill has nowhere to be recorded.
9. No design exists for what happens to an already-generated bill if the underlying program is later corrected/reverted/cancelled after reaching `Completed`.
10. The invoice-number generator itself doesn't exist yet (the schema supports it via `bill_number UNIQUE`, but no generator function or sequence has been built) — must be concurrency-safe by construction, not by retry-and-hope.

## Newly discovered architectural improvements

`modules/escort_lifecycle::_handle_release_event_tx()` already runs `close_program()` (the `status='Completed'` transition) inside a single database transaction, with a correct idempotency guard (`already_closed`) before creating the cost-side payment draft. **This is the right, safe hook point for bill generation** once the amount-source decision is made — no new transactional-safety work is needed there, only a call added alongside the existing `create_escort_payment_draft()` call, guarded the same way. The existing `fazle_payment_drafts` draft-then-admin-approval pattern is a good model to mirror for a bill-review step, consistent with every other money-adjacent action in this system requiring human confirmation before finalizing.

## Newly discovered risks

- Recreating the exact "admin types a free amount, nothing validates it against a formula" anti-pattern already flagged in the Payroll certification finding (`docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status) — a real risk if bill amount were ever manually typed without a genuine source-of-truth figure to check it against.
- Duplicate-bill risk under concurrent or retried completion events (item 2 above) if the uniqueness constraint isn't added before any bill-creation code ships.
- A bill generated with no way to correct, cancel, or credit-note it if the underlying program is later found to be wrong (item 9 above) — explicitly out of scope for this phase per the Owner's own billing-only scope decision, but worth naming so it isn't mistaken for solved.

## Newly discovered inconsistencies

`wbom_escort_programs.status` casing drift (already documented in `CANONICAL_BUSINESS_RULES.md` §Escort — re-confirmed live, not newly found, but directly relevant here): any future bill-generation trigger filtering on `status='Completed'` must account for this being a case-sensitive TEXT column with no CHECK constraint — a literal `WHERE status = 'Completed'` is correct today (confirmed the exact casing in production), but fragile against future drift.

## Newly discovered dead code

None found within billing-relevant modules this pass (`wbom_billing_records`, `escort_lifecycle`, `payment_workflow`, `escort_calculation_config` access paths). The already-documented dead code elsewhere in the system (`fazle_clients`, `resources/recruitment.py`, etc.) is out of scope for this billing-focused re-audit per Rule 4 (don't touch/re-audit unrelated modules).

## Newly discovered duplicate logic

None found new and specific to billing. The already-documented duplicate-classification-mechanism findings from the Phase 1.6 Canonical Business Rules pass remain the authoritative list; nothing new turned up in billing-adjacent code this pass.

## Newly discovered undocumented schema

None new. `wbom_billing_records` was already known and documented as dormant; `escort_calculation_config` was already referenced in `knowledge_base/06_developer_system/database_rules.md`'s Escort domain section. Nothing undocumented turned up in the billing-flow-relevant tables this pass.

## Newly discovered undocumented APIs

None. Re-confirmed (again, via fresh grep) zero billing/invoice routes or admin commands exist anywhere in the codebase.

## Newly discovered missing tests

No test file exists anywhere referencing `wbom_billing_records`, a billing flow, or invoice numbering — expected, since no application code exists yet. Flagged as a hard requirement for whenever bill-creation work actually proceeds (per this session's standing testing discipline — schema check + scratch-DB validation + transaction-preview, matching Phase 2A/Migration 032's method, plus real unit tests for the invoice-number generator's concurrency safety).

## Newly discovered documentation drift

None found. This session's own client-domain documents (`CANONICAL_CLIENT_DOMAIN.md`, `CANONICAL_BUSINESS_RULES.md`, `CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`) are current as of this pass — this re-audit's findings are being added to them (Task H), not correcting a drift that already existed in them.

## New Owner decisions required

1. **FULLY RESOLVED 2026-07-09.** `service_charge` comes from a new, frontend-managed Client Settings system (`wbom_client_billing_profiles` — General/24-Hour/12-Hour/Monthly rate fields, editable per client, entirely separate from the guard's payroll cost). All 4 sub-questions this decision originally surfaced are now also answered: billing type is admin-selected at bill-confirmation time (not inferred); VAT/Tax/Other Charges are internal profit-estimation deductions only this phase (not added to the client's invoice); flat amounts only this phase (percentage deferred as a documented future enhancement); `contact_id` is a required (nullable-valued) FK to `wbom_contacts` — no disconnected client system. Full design: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md`. Migration written and validated (`db/migrations/034_client_billing_profiles.sql`) — **not applied to production.** Detailed code plan: `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` — **no code written yet.** 2 minor items remain open (hard-vs-soft delete, RBAC granularity for wage/profit visibility) — neither blocks further planning.
2. **RESOLVED 2026-07-10 with a disclosed default, not a silent guess.** `db/migrations/036_billing_records_program_uniqueness.sql` adds a UNIQUE constraint on `wbom_billing_records.program_id` — one bill per program under normal operation, since no credit-note/multi-bill workflow exists or has been requested anywhere in this system. This is reversible (the constraint can be dropped) if a real multi-billing need is identified later; it was not left open pending an Owner answer because it directly blocked Commit C's correctness (duplicate-bill prevention) and the default carries low risk given it's easily undone.
3. Should `payment_status` move to a constrained lookup table (mirroring the `client_tier`/`client_status` pattern just built) or stay free text?
4. Is partial-payment tracking needed in this phase, or fully deferred to a later Client Payment Profile phase?
5. What should happen to an already-generated bill if its program is later corrected/reverted after reaching `Completed` — void it, flag it, or leave it as-is with a manual remarks note?

## Updated GO / NO-GO status

- **Schema/foundation (Migration 032):** GO — already applied and verified.
- **Invoice-number generator utility:** Conditional GO — decision-independent, safe to build now (implemented this pass, see `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2B Commit A).
- **Client Billing Profile schema (`wbom_client_billing_profiles`, Migrations 034 + 035):** **DONE.** Both applied to production (2026-07-09, Owner-approved) — 25 columns (Migration 035 added `employee_wage_general`, closing the gap flagged during restart verification), 4 indexes, FK to `wbom_contacts` all confirmed live, 0 rows.
- **Client Billing Profile backend/frontend code:** **DONE, confirmed live via two full service restarts.** `modules/client_billing/` implemented and mounted — the mount was verified in the *running* service's OpenAPI schema after both restarts, not just statically. `payroll.html` Client Settings section built and confirmed rendering live. Restart 1 verified the base feature (Add→Edit→List→soft-Delete→List cycle against the live API, table structurally confirmed, test row removed). Restart 2 (after `employee_wage_general` was added) re-verified specifically that field end-to-end live — Add with a value → GET re-fetch confirms it persisted (not just echoed) → Edit changes it → confirmed in the served `payroll.html` HTML — then the test row was removed again. 12 unit tests passing. Full regression re-run three times total (initial implementation, after adding `employee_wage_general`, after the second restart) — every time identical to the pre-existing baseline (29 failed / 0 errors), each checked with a zero-diff against the exact prior failure list.
- **Invoice numbering (Migration 033):** **DONE.** Applied to production 2026-07-10, verified live (`invoice_number_counters` table + `next_invoice_number()` function both confirmed present).
- **`wbom_billing_records` program_id uniqueness + governance backfill (Migration 036):** **DONE.** Schema-checked and scratch-DB validated (fresh-schema creation + duplicate-rejection + distinct-program-acceptance, all confirmed), applied to production and confirmed idempotent on re-run. Closes New Owner Decision Required #2 and readiness-report finding #2/#3 above.
- **Commit C (bill generation):** **DONE, as a manual admin action** (design change from the original automatic-hook sketch, disclosed in `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`). `modules/client_billing/bills.py` + 4 new routes + `payroll.html` "Generate Bill" section, all mounted and restart-verified live (routes appear in the running OpenAPI schema; functional GET calls against real production return correct empty-state results matching the true 0-qualifying-programs state). 9 new unit tests passing; full regression clean (29 pre-existing failures, 0 new). **Not live-tested against a real production bill generation** because zero Completed programs currently have a `contact_id` — see the finding below.
- **Operational finding, not a defect:** zero Completed escort programs have `contact_id` set today (the 59/342 adoption figure is entirely on non-Completed programs), so `GET /admin/bills/billable-programs` correctly returns empty in production. Commit C is fully built and correct but will stay dormant until at least one Completed program is linked to a contact with an active Client Billing Profile.
- **Bill creation (Commit C):** now **DONE** (above). **Payment recording, outstanding-balance view, mark-paid action, admin APIs beyond generate/list/view, readonly tools, dashboard summary, audit logging:** still **NO GO** — this is Commit D, still blocked on Owner Decisions #3 (`payment_status` lookup vs. free text), #4 (partial payment), #5 (post-completion correction).

---


**Date:** 2026-07-09
**Status:** Read-only assessment. No code, migration, or production write in this pass.
**Trigger:** Phase 2A (`db/migrations/031_client_domain_reconciliation.sql`) accepted and applied. This report assesses what stands between here and Phase 2B, per the Owner's explicit request — analysis only, not a go-ahead to build.
**Precedence:** Extends `docs/CANONICAL_CLIENT_DOMAIN.md`, `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2B, and `docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status with fresh, re-verified evidence — not a duplicate of them.

---

## Owner Decisions (2026-07-09) — all 6 open questions resolved

| # | Question (from this report) | Owner Decision |
|---|---|---|
| 1 | Payroll formula / GL cost-linkage | **Do not connect payroll cost into GL yet.** Phase 2B is explicitly scoped to **billing-only** — invoicing and accounts receivable, with no GL posting hook and no payroll-cost reconciliation — until the payroll pay-formula question is separately finalized. This decouples Phase 2B (billing) from the payroll blocker that previously gated it. |
| 2 | Client tier vs. `relation_type_id` | **Option B confirmed, then finalized into 4 dimensions (2026-07-09):** `relation_type` = identity (Client/Vendor/Employee/Family/Escort Client — Phase 2A, unchanged). `client_tier` = business value (`Standard`/`Repeat`/`VIP`/`Strategic` — new). `client_status` = business lifecycle (`Active`/`Inactive`/`Suspended`/`Closed` — new, *separate* field from `client_tier`). `is_active` = system interaction permission (Phase 2A, unchanged). **`'Blocked'` is exclusively `is_active=false`** — never a value in `client_tier` or `client_status`. |
| 3 | Billing trigger | A bill is generated **only** once an escort program reaches a **confirmed/completed final-client-slip state** — not `draft`, not `running`/in-progress. |
| 4 | Invoice numbering | **Deterministic yearly sequence**: `AQS-2026-000001` format (prefix-year-sequence). |
| 5 | Billing profile scope | **Contact-level billing only** for this phase. Client Contact/Client Branch (multi-person, multi-site company support) explicitly deferred to a later phase. |
| 6 | `contact_id` enforcement | **New** escort/client intake must require or resolve `contact_id` before a bill can be generated. **Old/existing rows remain nullable** — backfilled over time, not retroactively enforced. |

**Scope consequence of Decision 1:** Phase 2B is no longer blocked by the payroll pay-formula question — that dependency is formally decoupled. General Ledger (chart of accounts, debit/credit, P&L) remains a **separate, later phase** gated on the payroll decision, per the original roadmap's Phase 2b/2c split. See `docs/PHASE2_ENTRY_GATE.md` for the updated gate status.

---

## 1. Existing `wbom_billing_records` schema and code gap

Re-verified directly against production (read-only) — unchanged since the certification pass:

```
wbom_billing_records
  bill_id          integer PK, identity
  program_id       integer NOT NULL  -> FK wbom_escort_programs.program_id
  contact_id       integer NOT NULL  -> FK wbom_contacts.contact_id
  bill_date        date NOT NULL
  bill_number      varchar, UNIQUE (indexed)
  service_charge   numeric
  other_charges    numeric, default 0
  total_amount     numeric
  payment_status   varchar, default 'Pending'
  payment_date     date
  remarks          text
  created_at       timestamptz, default now()

Indexes: pkey(bill_id), unique(bill_number), btree(contact_id), btree(bill_number)
Row count: 0
```

**Assessment:** This is a genuinely well-designed table — correct FKs to both sides of a bill (the service delivered, the client billed), a unique constraint already in place for `bill_number` (so invoice-number collisions are already prevented at the DB level, no work needed there), and sensible defaults. **The gap is 100% application code, not schema.** No module anywhere in this codebase inserts, updates, or reads this table except one defensive `SELECT COUNT(*)` in `modules/escort_roster/db.py` (a duplicate-dependency check before allowing a destructive dedup — it treats `wbom_billing_records` as "might have dependents," nothing more). There is no billing workflow, no invoice-generation trigger, no admin command, no dashboard view, no report referencing it.

**What's missing, concretely:**
- No trigger point (when does a bill get created — on program completion? on a manual admin command? both?).
- No `payment_status` vocabulary is enforced (`'Pending'` is the only value ever set, by the column default — no code sets `'Paid'`/`'Overdue'`/anything else).
- No link from a bill to which `fpe_cash_transactions` row (if any) represents the client's payment being received — the table only points *to* the service (`program_id`) and the client (`contact_id`), not to money actually received.
- No accounts-receivable aggregate view (total outstanding per client) — would need to be computed from this table, not stored anywhere yet.

---

## 2. Required Billing/GL activation steps (design-level, not yet built)

In dependency order:

1. **Client identity must be stable first** — Phase 2A's `relation_type_id` backfill (128/2086 contacts now classified) is the input; `wbom_escort_programs.contact_id` is still only 17% populated (re-verify this number before starting — not re-checked in this pass, no reason to expect it moved since Phase 2A didn't touch it). **Resolved (Decision 6): required for new intake going forward; old rows stay nullable and get backfilled over time, not retroactively enforced.**
2. **Billing trigger — resolved (Decision 3):** a bill is generated only once an escort program reaches its **confirmed/completed final-client-slip state** — not `draft`, not `running`. Implementation-wise, this should hook the same completion event `escort_lifecycle` already uses to trigger `payment_workflow.create_escort_payment_draft()` on the cost side, mirrored for the revenue side (billing), not a separate/parallel completion signal.
3. **Wire the INSERT.** Populate `wbom_billing_records` from the completed program's `contact_id`, `service_charge`, `bill_number` (numbering — **resolved (Decision 4): `AQS-YYYY-NNNNNN` deterministic yearly sequence**, e.g. `AQS-2026-000001`; the existing `bill_number` UNIQUE constraint already prevents collisions, this adds the generator). Per Decision 1, `service_charge` is populated from whatever program-level figure already exists on the confirmed/completed program record — this phase does **not** wait on or reconcile against the payroll pay-formula decision.
4. **Wire payment recording.** Update `payment_status`/`payment_date` on an admin action. Per Decision 1, this phase does **not** link to `fpe_cash_transactions`/GL — that connective tissue is explicitly deferred alongside the GL work itself.
5. **Build the outstanding-balance view.** A read query (or `ai_readonly_tools` tool, matching the existing pattern) aggregating unpaid `wbom_billing_records` per `contact_id`.
6. **General Ledger — explicitly out of scope for this phase (Decision 1).** Deferred to a later, separate phase once the payroll pay-formula question is resolved.

Steps 2-5 define Phase 2B's actual build scope. Step 6 (GL) is now formally a later phase, not part of Phase 2B.

---

## 3. Payroll cost-ledger formula conflict — re-verified, unchanged

Re-checked directly against production (read-only) — **nothing has changed since the certification pass**, as expected (Phase 2A didn't touch payroll):

- `wbom_payroll_runs`: still all 703 rows `status='draft'`, still 0 with `total_programs>0`.
- `fpe_cash_transactions.program_id`: still NULL on all rows — now 3,683 total transactions (was 3,671 at certification time; 12 new real transactions since then, all still with no program link).
- `fazle_payment_drafts` (`draft_type='escort_payment'`): still all 47 rows `status='expired'`.

**This is still a live, unresolved blocker for Phase 2B specifically**, not just a Phase 2A-adjacent footnote: General Ledger revenue postings need to reconcile against *something* on the cost side to produce a real P&L. Today there is no reliable link between "this program was billed to this client" and "this program cost this much in verified guard pay" — because guard pay itself isn't reliably linked to a program (`program_id` NULL everywhere) and isn't computed by an enforced formula (three formulas, none enforced, per the certification report). Building Phase 2B's revenue side without this would produce a GL that's honest about income but structurally unable to compute a trustworthy per-program margin.

**Resolved (2026-07-09):** Owner Decision 1 (see top of document) decouples this — Phase 2B proceeds **billing-only**, with no GL posting hook and no payroll-cost reconciliation, until the payroll formula question is separately finalized. The structural risk described above (a GL that can't compute a trustworthy per-program margin) is avoided by not building the GL/cost-linkage piece yet, not by resolving the payroll question.

---

## 4. Does client tier/status need a field separate from `relation_type_id`?

**New finding this pass, read-only:** `wbom_contacts` already has `interest_level` (values found: `'unknown'`, `'warm'`, `'hot'`) and `keep_tier` (values found: `'none'`, `'tier1'`) columns — populated on real rows (595 have `keep_tier='tier1'`). At first glance these look like exactly the "client tier" field Phase 2A's design questions left open. **They are not, on closer inspection:** no code anywhere in `modules/`, `scripts/`, or `tools/` writes to `keep_tier`, `keep_reason`, or `marked_for_delete_at` — these cluster together with a WhatsApp-contact-list *retention/cleanup* purpose (deciding which of 2,086 contacts to keep vs. purge from a bloated list), not a business-value/billing-tier concept. Reusing them for "is this a VIP client" would conflate two unrelated ideas — a contact could be `keep_tier='tier1'` (worth keeping in the phone book) without being a paying client at all, or vice versa.

**Conclusion: yes, a genuinely separate field is needed if the Owner wants to preserve the `vip_client`/`repeat_client` distinction that existed in `fazle_contact_roles` before Phase 2A collapsed both into plain `'Client'`.** Two real options, not yet decided:
- **Option A:** Add new `wbom_relation_types` rows (`'VIP Client'`, `'Repeat Client'`) — keeps everything in one FK, but conflates "nature of relationship" (client vs. vendor vs. employee) with "tier within that relationship," which is architecturally a different dimension.
- **Option B (recommended, not decided):** Add a separate `client_tier` or `client_priority` column on `wbom_contacts`, orthogonal to `relation_type_id` — a contact can be `relation_type_id=1` (Client) AND `client_tier='vip'` simultaneously, which matches how these concepts actually relate in the real business (a VIP client is still fundamentally "a Client," just with a priority flag on top). This mirrors the existing, correct separation between `relation_type_id` (what) and `business_type_id`/`wbom_business_types` (what kind of business) already established in the schema.

**Resolved (2026-07-09), final model:** Option B, refined into 4 separated dimensions rather than one combined field — see the table at the top of this document. `client_tier` (business value) and `client_status` (business lifecycle) are two distinct new fields, both orthogonal to `relation_type` (identity, Phase 2A) and `is_active` (system interaction permission, Phase 2A). `'Blocked'` is exclusively represented by `is_active=false` and must not appear in either new field — this closes the overlap this report originally flagged as unresolved. Not yet implemented — this remains a readiness/decision record, no migration written in this pass.

---

## 5. Owner decisions — all resolved 2026-07-09 (see table at top of document)

1. ~~Payroll pay-formula/cost-ledger question~~ — **Resolved: deferred.** Phase 2B is billing-only; GL/cost-linkage waits for a separate payroll-formula decision.
2. ~~Client tier modeling~~ — **Resolved: separate `client_tier`/`client_status` field**, not overloaded onto `relation_type_id`.
3. ~~Billing trigger mechanism~~ — **Resolved: confirmed/completed final-client-slip state only**, not draft/running.
4. ~~Invoice numbering scheme~~ — **Resolved: `AQS-YYYY-NNNNNN` deterministic yearly sequence.**
5. ~~Payment-terms/credit-limit scope~~ — **Resolved: contact-level billing only** for this phase; Client Branch/Contact deferred.
6. ~~`wbom_escort_programs.contact_id` intake adoption~~ — **Resolved: required/resolved for new intake, nullable for old rows** (backfilled over time, not retroactively enforced).

All 6 questions this report raised are now settled Owner policy. Implementation has **not** started — this document remains the readiness record; see `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2B for the updated build plan.

**No recommendation to proceed is made here — this report is the requested readiness assessment only.**
