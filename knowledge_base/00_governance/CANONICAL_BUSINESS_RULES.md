# Canonical Business Rules — Fazle Core

**Version:** 1.0
**Date:** 2026-07-09
**Status:** Authoritative business-rule document for Fazle Core.
**Precedence:** Second-highest priority document in the repository, immediately below `BUSINESS-CONSTITUTION.md`:

1. `BUSINESS-CONSTITUTION.md` — immutable governance and architecture principles.
2. **This document** — the single source of truth for the company's current, effective business rules.
3. `SYSTEM_ATLAS.md` — the technical map of the whole system.
4. `docs/PRODUCTION_CERTIFICATION_v1.0.md` — current production-readiness certification.
5. `docs/NEXT_IMPLEMENTATION_QUEUE.md` — the queue of work still to do.

If any module, README, KB article, prompt, parser, workflow, or older documentation disagrees with this file, **this file wins** — with one exception: live code and the live database are always the final tie-breaker over any document, including this one (see `SYSTEM_ATLAS.md` §"Authority order"). If this document and the code disagree, that is a bug in this document — fix the document, don't silently trust it.

---

## Fundamental Principle

> **Business Policy is changeable. Business Structure is canonical.**

Business rules change because the company's needs change. The Owner/Admin may change any business **policy** at any time — that is a normal, healthy part of running the business, not a system defect.

Example of a policy (changeable, Owner's call, any time):
> Should advances be deducted before or after basic salary in a payroll run?

Example of structure (not changeable by casual edit, must stay unified across the whole system):
> `employee_id`, `employee_mobile`, `escort_employee_id`, `canonical_employee_id` — these must mean one consistent thing each, everywhere, not drift into four different meanings across four modules.

**Policies that may change over time, at the Owner's discretion:**
- Salary calculation (rate, formula, deductions)
- Escort payment policy (day-rate, shift-rate, or personalized-rate; conveyance added or deducted)
- Billing policy (once built)
- Recruitment workflow (funnel stages, scoring)
- Deposit/advance policy
- Approval workflow (who approves what, thresholds)
- Attendance rules

**Structure that must remain internally consistent everywhere, and is NOT subject to casual/local change:**
- Database table names and column names
- API names and route names
- Canonical identifiers (which ID means what, and how systems cross-reference)
- Entity names (what a "client," "employee," "program," "draft" *is*)
- Module responsibilities (which module owns which table/workflow)
- Event names and status/state values
- Search-parameter names
- Foreign-key relationships
- Canonical business objects

If multiple names exist today for the same business object (and several do — see below), they must be **documented as aliases of one canonical name**, not silently treated as different things. No module should invent a new meaning for an entity that already exists elsewhere in the system.

---

## Conflict Resolution Rule

Whenever multiple policies, names, or formulas exist for the same business function, resolve it in this order:

1. Search every module for implementations.
2. Search every migration/table definition for the underlying schema.
3. Search every workflow/call chain that touches it.
4. Search every README/KB article/prompt that describes it.
5. Search actual production data for evidence of which implementation is actually used in practice (not just which one is newest or best-documented).
6. Choose **ONE** canonical rule, name, or formula.
7. Document *why* it was chosen (usually: what production evidence shows is actually in use, or what the Constitution/Owner has already decided).
8. Mark every other variant found as one of:
   - **Legacy** — superseded, should be migrated away from, not actively extended.
   - **Historical** — kept for audit/reference only, never written to going forward.
   - **Alternative** — a second real code path still active, needs an Owner decision on which one wins (do not silently pick one yourself when both are still live).
   - **Owner Changeable** — this is a policy, not structure; multiple values are fine because the Owner may pick differently at different times.

**Never leave a conflicting policy or name undocumented.** An undocumented conflict is exactly the "Business Knowledge Drift" this document exists to stop — the escort-duty pay-formula situation (three formulas, none of them actually enforced) documented in `docs/PRODUCTION_CERTIFICATION_v1.0.md` is the concrete example that motivated this document.

---

## Naming Rule

For every entity where more than one name is found in the wild, this document records:

```
Canonical:    <the one name to use going forward>
Aliases:      <every other name found, with where each is used>
Deprecated:   <names that should be actively removed/migrated away from>
Future:       <the name to converge on if a rename is ever justified — usually same as Canonical>
```

A module using an alias is not automatically "wrong" — renaming live database columns is a real, risky migration, not a documentation exercise. This document's job is to make the drift **visible and named**, so a future change either (a) converges on the canonical name, or (b) is made with full knowledge that it's adding to an already-known list of aliases, not accidentally inventing a fifth name for the same thing.

---

## Workflow Rule

Each major business workflow has exactly one **Canonical Workflow** — the sequence of steps real production evidence shows is actually used end-to-end. Where an alternative shorter/older path exists in code (e.g., a direct write that bypasses a step the canonical workflow goes through), it is marked **Legacy** or **Alternative** with the reason, not silently treated as equally valid.

---

## Formula Rule

Where more than one formula computes the same business quantity (this document found exactly one such case — escort-duty pay, see §Payroll below), each is recorded as:

```
Canonical Formula:     <the one to trust / the one Owner has confirmed is intended>
Alternative Formula:   <a second real, live formula — needs Owner decision>
Historical Formula:    <superseded, kept for reference>
Future Formula:        <if a change is already planned/approved>
```

## Database Rule

Where a business object's schema is defined or duplicated in more than one place (a real, large problem in this codebase — see `docs/PRODUCTION_CERTIFICATION_v1.0.md` Database Status), exactly one definition is **Canonical Schema**; every other copy is a **Reference** only, and must not be used as the source of truth for new work.

## AI Rule

Every AI system that works on this codebase — Claude, Copilot, Gemini, ChatGPT, Kilo, Cursor, or any other — follows the rules in this document identically. No AI agent may invent a new business policy, a new name for an existing entity, or a new formula without it being recorded here first. If an AI agent finds a genuine conflict this document doesn't yet cover, its job is to **document the conflict here** (per the Conflict Resolution Rule above) and flag it for an Owner decision — not to quietly pick one side and move on.

---

# Business Function Sections

Each section below covers: Business Purpose, Canonical Workflow, Canonical Entity Names, Canonical Database Tables, Canonical APIs, Canonical Status Values, Known Legacy Variants, Owner Changeable Rules, Structural Rules, Validation Rules, Related Modules, Related Documents.

## Roles (RBAC)

**Business Purpose:** Control which WhatsApp admin commands each authenticated admin/staff member is allowed to execute, ranked by sensitivity.

**Canonical Workflow:** Admin sends a command via WhatsApp → `modules/admin_commands` matches it to a command name → `modules/rbac.check_permission()` looks up the admin's role level via `fazle_admin_roles` → `fazle_roles` → compares against the command's required role → **fails closed** on any lookup exception (fixed 2026-07-09, see `docs/PRODUCTION_CERTIFICATION_v1.0.md`) → allowed or denied, both paths write `fazle_audit_log`... actually audit table name should be confirmed against code, not assumed (see Structural Rules).

**Canonical Entity Names:** "Role" = a named permission tier (`fazle_roles.name`) with a numeric `level` for ranking. "Admin" = an authenticated WhatsApp operator (`fazle_admins`), not to be confused with "Owner" (the business owner, who is also an admin but with `superadmin` role).

**Canonical Database Tables:** `fazle_admins` (id, phone, api_key_hash, status, username, password_hash), `fazle_roles` (name, level, description), `fazle_admin_roles` (admin_id, role_name, granted_at, granted_by) — a many-to-many join, so an admin can hold more than one role.

**Canonical APIs:** `modules/rbac/__init__.py`: `check_permission()`, `get_roles()`, `get_admin_by_api_key()`, `get_admin_by_username()`, `record_audit()`.

**Canonical Status Values:** Role hierarchy, lowest to highest privilege: `viewer` → `accountant` → `admin` → `superadmin` (`modules/rbac/__init__.py::COMMAND_ROLE` maps each command name to its minimum required role; `DEFAULT_REQUIRED_ROLE = "admin"` for any unlisted command — fail toward the stricter default, not the looser one). `fazle_admins.status`: `active` is the only confirmed value in the default column definition; other values not yet catalogued in this pass.

**Known Legacy Variants:** None found this pass specific to roles naming. The 2 dead `HROFFICER_*_APIKEY`/`_PASSWORD` `.env` values (confirmed not read by any code path, see `docs/NEXT_IMPLEMENTATION_QUEUE.md`) are a related cleanup item, not a naming conflict.

**Owner Changeable Rules:** Which role a specific command requires (`COMMAND_ROLE` mapping) is a business policy — the Owner may decide `payroll_paid` should require `admin` instead of `accountant`, for example. Changing that mapping is a policy edit, not a structural change.

**Structural Rules:** `viewer`/`accountant`/`admin`/`superadmin` are the only 4 role names in use — do not introduce a 5th name for an existing responsibility (e.g., do not add "manager" if it means the same tier as "admin"). Fail-closed on any RBAC lookup exception is a structural, non-negotiable rule (Article-level, per the 2026-07-09 fix) — never revert to fail-open.

**Validation Rules:** Every admin command must appear in `COMMAND_ROLE` or fall back to `DEFAULT_REQUIRED_ROLE`; there is no "no role required" path for any admin command.

**Related Modules:** `modules/rbac`, `modules/admin_commands`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md` (Security Status), `docs/MODULE_MAP.md`.

---

## Attendance

**Business Purpose:** Track whether a guard/employee was present for a given day, both for standalone daily attendance and as a backfilled record of escort-duty days.

**Canonical Workflow:** Two entry points converge on the same table: (1) a guard reports attendance directly (draft → admin APPROVEs → `save_attendance()`), or (2) an escort program's release event backfills attendance for every day of the duty (`modules/escort_lifecycle`'s `_backfill_attendance()`-equivalent, `ON CONFLICT DO NOTHING` idempotent). Both write `wbom_attendance`.

**Canonical Entity Names:** "Attendance" always refers to one row per (employee, date). Do not conflate with "duty days" (`wbom_escort_programs.day_count`), which is a program-level aggregate, not a per-day attendance row — they are related but distinct concepts.

**Canonical Database Tables:** `wbom_attendance` (employee_id, attendance_date, status, location, recorded_by, remarks) is canonical, per `modules/attendance/__init__.py`'s own docstring and every write path found in this repo.

**Canonical APIs:** `modules/attendance/__init__.py::save_attendance(employee_id, attendance_date, status="Present", location="", recorded_by="system", remarks="")` — note the real signature requires `attendance_date` positionally; several tests in this repo call an older shape and fail (documented in `docs/PRODUCTION_CERTIFICATION_v1.0.md` Testing Status, Category C).

**Canonical Status Values:** `Present`, `Absent` (confirmed via `modules/attendance/__init__.py` filtering logic). `wbom_employees.status` uses `Active` (capitalized) as its confirmed value — note the capitalization convention differs from the lowercase RBAC/admin status values (`active`) — this is a real, minor naming/casing inconsistency, recorded here rather than silently normalized.

**Known Legacy Variants:** `ops_attendance` exists live in production with no migration record anywhere in this repo (found during the 2026-07-09 schema-drift diff, `docs/PRODUCTION_CERTIFICATION_v1.0.md` Database Status) — it is presumed to be a legacy/superseded table from an older "ops_*" system, not a second active attendance pipeline, but this has **not been individually confirmed**; treat as **Legacy** pending verification, not as a second canonical source.

**Owner Changeable Rules:** Attendance-approval workflow (who approves a self-reported attendance draft) is a policy choice, not structure.

**Structural Rules:** `wbom_attendance` is the only table application code should write new attendance rows to. `ops_attendance` must not be written to by any new code without an explicit decision to un-deprecate it.

**Validation Rules:** `(employee_id, attendance_date)` must be unique per the idempotent backfill design — confirm a unique constraint actually enforces this at the DB level before relying on `ON CONFLICT DO NOTHING` semantics (**Not verified** this pass — flagged for confirmation, not assumed).

**Related Modules:** `modules/attendance`, `modules/escort_lifecycle`, `modules/attendance_parser`.

**Related Documents:** `docs/MODULE_MAP.md`, `docs/DEVELOPER_GUIDE.md` §F/§G.

---

## Payroll

**Business Purpose:** Pay employees, including a monthly basic salary and, for guards, escort-duty pay on top of it.

**Canonical Workflow — basic salary (this one is real, in active use):** Admin types a WhatsApp `PAID <draft_id> <amount> <method>` or equivalent command (free-typed amount) → `modules/admin_commands` → `fazle_payroll_engine.accounting.create_transaction()` (the one, Constitution-Article-I-mandated writer) → `fpe_cash_transactions` (`txn_category='salary'`). **Confirmed live**: 2,606 real WhatsApp-sourced salary transactions exist in production as of 2026-07-09.

**Canonical Workflow — escort-duty pay (this one is NOT actually in use — see Owner Changeable Rules below):** `modules/payroll::compute_run()` is supposed to aggregate `wbom_escort_programs` into a monthly `wbom_payroll_runs` row with a `draft → reviewed → approved → locked → paid` state machine. **Production evidence, 2026-07-09: all 703 rows are stuck in `status='draft'`, all with `total_programs=0`.** This pipeline has never actually paid anyone for escort duty in production.

**Canonical Entity Names:** "Payroll run" = one `wbom_payroll_runs` row per (employee, period_year, period_month). "Payment draft" = a `fazle_payment_drafts` row awaiting admin approval — a different, narrower concept than a payroll run (a draft is per-event; a run is per-month-aggregate).

**Canonical Database Tables:** `wbom_payroll_runs`, `wbom_payroll_run_items`, `wbom_payroll_approval_log` (the monthly-aggregate system); `fazle_payment_drafts` (the per-event draft system); `fpe_cash_transactions` (the only table real money is ever actually recorded in, regardless of which upstream system suggested the amount).

**Canonical APIs:** `modules/payroll::compute_run(employee_id, period_year, period_month, computed_by, per_program_rate=None)`, `submit_run/approve_run/lock_run/mark_paid/cancel_run`. `modules/payment_workflow::create_escort_payment_draft(employee_id, escort_program_id, override_days, source)`, `finalize_payment(draft_id, approved_amount, method)`.

**Canonical Status Values:** Payroll run state machine: `draft → reviewed → approved → locked → paid`, with `cancelled` reachable from any non-`paid` state (`modules/payroll/__init__.py::ALLOWED_TRANSITIONS`). Payment draft status: `pending`/`approved`/`rejected`/`sent`/`expired` (confirmed all 47 real escort-payment drafts ended in `expired`).

### Formula Rule — escort-duty pay (three formulas found, none canonical in practice)

```
Formula A (modules/payroll::compute_run):
    program_allowance = total_days × 400 ৳/day  (flat, DEFAULT_PER_PROGRAM_RATE, company-wide)
    gross = basic_salary + program_allowance   (no food/conveyance deduction)
    Status: defined, never actually executed with total_programs > 0 in production.

Formula B (modules/escort_roster/calculations.py::calculate_pay):
    salary = total_shifts × 200 ৳/shift  (= 400/day equivalent — numerically matches A's default)
    total  = salary + conveyance          (conveyance ADDED)
    net_payable = total − food_bill
    Status: feeds escort_roster_entries, a roster/reconciliation record — not itself a payment trigger.

Formula C (modules/payment_workflow::create_escort_payment_draft):
    daily_rate = basic_salary ÷ 30        (personalized per employee — NOT flat 400)
    net_payable = (duty_days × daily_rate) − food_bill − conveyance − advances
                                             (conveyance SUBTRACTED — opposite sign from B)
    Status: feeds fazle_payment_drafts; all 47 real drafts ever created expired unused.

Actual production payment path: admin types a free amount in "PAID <id> <amount> <method>";
the system records exactly that number via create_transaction(), with NO cross-check against
any of A, B, or C. fpe_cash_transactions.program_id is NULL on all 3,671 real transactions.
```

**Owner Changeable Rules:** Which of A/B/C's rate policy (flat company-wide day-rate vs. personalized `basic_salary/30`) is the intended one, and whether conveyance should add to or subtract from pay, are **business policy decisions the Owner has not yet made explicitly** — this document cannot invent that decision. Until decided, this is recorded as an **open Alternative** (per the Conflict Resolution Rule), not silently resolved. See `docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status for the full evidence and `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 0 for the queued decision item.

**Structural Rules:** Regardless of which formula policy the Owner picks, `fpe_cash_transactions` remains the only table real money is recorded in, and `create_transaction()` remains the only function allowed to write it (Article I) — that structural rule does not change no matter which pay-formula policy wins.

**Validation Rules:** None currently enforced between the admin-typed `PAID` amount and any formula's suggested figure — this is the concrete control gap this document exists to surface, not to silently patch. A future fix should validate the typed amount against the Owner-chosen canonical formula and require override confirmation on mismatch, per the certification report's recommendation.

**Related Modules:** `modules/payroll`, `modules/payment_workflow`, `modules/escort_roster`, `modules/fazle_payroll_engine`, `modules/admin_commands`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md` (Finance/Payroll Status — full evidence), `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 0, `BUSINESS-CONSTITUTION.md` Article I.

---

## Finance (umbrella)

**Business Purpose:** "Finance" is not one module in this codebase — it is the umbrella over four related but distinct sections below, kept separate deliberately because each has its own tables, own vocabulary, and own state of completeness. Read this as a map, not a duplicate:

- **Cash Transaction** (below) — the canonical record of real money movement, live and in daily use.
- **Income** (below) — a separate, currently disconnected ledger for money coming in.
- **Expense** — not yet a distinct tracked entity (see below).
- **General Ledger** (below) — the Phase 2b double-entry layer, not yet built.

**Structural Rule:** Do not create a fifth "finance" table or module that tries to unify these — the correct unification point, per the roadmap, is the future General Ledger attaching as an extension to `create_transaction()` (Article I), not a new parallel structure.

**Related Documents:** `BUSINESS-CONSTITUTION.md` Article I, `docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status.

---

## Cash Transaction

**Business Purpose:** Record every real movement of money — salary, advances, bonuses, deductions, corrections — in one place that the whole business trusts.

**Canonical Workflow:** Any real money movement → `modules/fazle_payroll_engine/accounting.py::create_transaction()` → `fpe_cash_transactions` (Constitution Article I). One documented exception exists: `reverse_transaction()` inserts the reversal row **directly**, not through `create_transaction()` (`accounting.py:157`) — this is a structural fact worth naming explicitly, not silently glossing over, since Article I says "exactly one function creates a financial transaction." Whether `reverse_transaction()`'s direct insert is an intentional, audited exception (it does set `is_reversal=TRUE` and links `reversed_txn_id`) or a gap in Article I's own enforcement is an open structural question — flagged here, not resolved unilaterally.

**Canonical Entity Names:** "Transaction" always means one `fpe_cash_transactions` row. `wbom_cash_transactions` is a **separate, legacy, read-only reference table** (per Owner directive 2026-06-29) — never write to it; it exists for historical/reference queries only.

**Canonical Database Tables:** `fpe_cash_transactions` (canonical, all real money). `wbom_cash_transactions` (Legacy, read-only reference — do not write). `fpe_employee_ledger` (per-employee-per-period running balance, not a transaction log). `fpe_income_transactions` (a **separate** table for income, written by `create_income_transaction()` — explicitly does not touch `fpe_cash_transactions` or `fpe_employee_ledger`, per its own docstring).

**Canonical APIs:** `create_transaction()`, `reverse_transaction()` (`modules/fazle_payroll_engine/accounting.py`). `tools/repair_fpe_transactions.py` is a separate, explicitly-labeled repair tool (its own dry-run mode and its own audit table `fpe_transaction_repairs`) — not part of the normal write path, used only for manual data correction.

**Canonical Status Values — `txn_category`** (`TxnCategory` enum, `modules/fazle_payroll_engine/models.py:45-51`): `salary`, `advance`, `bonus`, `deduction`, `correction`. No other literal value is ever assigned anywhere in the codebase (confirmed by grep).

**Canonical Status Values — `transaction_status`** (`TransactionStatus` enum, `models.py:53-57`): `final`, `pending`, `reversed`, `corrected`.

**Canonical Status Values — `approval_status`** (`ApprovalStatus` enum, `models.py:60-63`): `approved`, `rejected`, `pending_review`.

**Canonical Status Values — `review_status` on `fpe_cash_transactions`** (`ReviewStatus` enum, `models.py:66-70`): `pending`, `reviewed`, `auto_resolved`, `dismissed`.

**Known Legacy Variants / Naming Collision (important structural finding):** A **different** column, also named `review_status`, exists on `fpe_unmatched_messages` (`modules/fazle_payroll_engine/migrations/002_fpe_normalization.sql:71`) with a **different vocabulary**: `pending`, `promoted`, `operator_pending`, `reviewed`, `rejected`. Same column name, two unrelated vocabularies on two different tables — exactly the kind of structural drift this document exists to name. **Rule going forward: do not assume `review_status` means the same thing on every table — check which table before trusting the vocabulary.** A future rename (e.g. `message_review_status` on `fpe_unmatched_messages`) would resolve this, but is not made unilaterally here — recorded as a known collision, not silently fixed.

**Owner Changeable Rules:** None — the 5 `txn_category` values and 4 `transaction_status` values are structural (application logic branches on them), not business policy in the "Owner can add a 6th category tomorrow without a code change" sense.

**Structural Rules:** `fpe_cash_transactions` is the only table Phase 2b's General Ledger work may read as the cost-side source of truth. `wbom_cash_transactions` must never be written to by any new code.

**Validation Rules:** No DB-level CHECK constraint enforces any of the 4 enum column value-sets on `fpe_cash_transactions` (confirmed — plain TEXT columns) — validation is entirely at the Python/Pydantic layer via the enum classes in `models.py`. A direct SQL INSERT bypassing the Python layer (as `tools/repair_fpe_transactions.py` does, deliberately, with its own safeguards) is not blocked by the database itself.

**Related Modules:** `modules/fazle_payroll_engine`, `modules/payment_workflow`, `modules/payment_ingest`, `modules/admin_commands`, `modules/payment_correction`.

**Related Documents:** `BUSINESS-CONSTITUTION.md` Article I, `docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status.

---

## Draft (Payment Drafts & Reply Drafts)

**Business Purpose:** Hold a proposed action (a payment to approve, or a WhatsApp reply to send) for human review before it takes effect.

**Canonical Workflow:** Two entirely separate draft systems exist under the same word "draft" — do not conflate them. (1) **Payment drafts** (`fazle_payment_drafts`): created by `payment_workflow`/`escort_lifecycle`/`admin_commands`, reviewed by an admin, approved/rejected via WhatsApp command. (2) **Reply drafts** (`fazle_draft_replies`): an AI-suggested WhatsApp reply held for admin review before sending (safe-mode pattern).

**Canonical Entity Names:** "Payment draft" ≠ "Reply draft" — despite both being "a draft," they are unrelated tables with unrelated, non-overlapping status vocabularies (see below). Do not write code that assumes one draft-status vocabulary applies to both tables.

**Canonical Database Tables:** `fazle_payment_drafts`, `fazle_draft_replies`. Also note `escort_roster_change_requests` is a third, narrower "pending change awaiting approval" table (see Approval section below).

**Canonical Status Values — `fazle_payment_drafts.status`:** table comment says `pending | approved | rejected | sent` (`db/migrations/002_align_tables.sql:63`), but actual code also assigns `expired`, `completed`, and `reversed` — the column has **no DB-level CHECK constraint**, so this is undocumented-until-now drift between the schema comment and reality. **Full real set: `pending`, `approved`, `rejected`, `sent`, `expired`, `completed`, `reversed`.**

**Canonical Status Values — `fazle_draft_replies.status`:** `pending`, `approved`, `sent`, `rejected`, `edited`, `deleted`. This is a **different set** than `fazle_payment_drafts` — no overlap on `sent`/`edited`/`deleted` vs `completed`/`expired`/`reversed`. `modules/draft_quality/__init__.py`'s docstring additionally *describes* intended values `rejected_quality`/`rejected_fallback`, but no code site was found actually writing them — this is a documentation-vs-implementation gap, not a live status value; do not assume it's in use.

**Known Legacy Variants:** None beyond the documentation/implementation gap above.

**Owner Changeable Rules:** Which statuses a draft can move through, and what triggers `expired` (a timeout policy) vs. manual rejection, are policy choices.

**Structural Rules:** `fazle_payment_drafts` and `fazle_draft_replies` remain two separate tables with two separate status vocabularies — do not merge them or assume shared meaning. If a genuine 7th/8th status is ever needed, add a DB-level CHECK constraint at the same time (neither table has one today) so schema comments stop silently drifting from reality.

**Validation Rules:** Add CHECK constraints to both tables' `status` columns as a Tier-1/Tier-2 hygiene item — today, any string can be written and nothing enforces the documented set.

**Related Modules:** `modules/payment_workflow`, `modules/draft_approval`, `modules/drafts`, `modules/admin_commands`, `modules/draft_quality`, `modules/wa_chat_frontend`.

**Related Documents:** `docs/MODULE_MAP.md`.

---

## Approval

**Business Purpose:** Require a human (admin/accountant/superadmin, per Roles above) to confirm an action before it becomes final and money moves or a message sends.

**Canonical Workflow:** Varies by domain — payroll runs move through the 6-state machine in Payroll above; payment/reply drafts move through their own status sets in Draft above; escort roster changes move through `escort_roster_change_requests`.

**Canonical Entity Names:** "Approval" is not one single mechanism in this codebase — it is implemented per-domain, each with its own status vocabulary (see above). Do not assume a single generic "approval" table or status set exists system-wide.

**Canonical Database Tables:** `wbom_payroll_approval_log` (audit trail of payroll state transitions, `from_status`/`to_status` columns — not itself a live-status table, a log). `escort_roster_change_requests` — **the only approval-style table found in this pass with an enforced DB-level CHECK constraint**: `status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected'))` (`db/migrations/026_escort_roster_change_requests.sql:12`). `wbom_employee_requests` — status default is `'Pending'` (**capitalized** — a real, minor casing inconsistency versus every other lowercase status vocabulary in this system, recorded here rather than silently normalized).

**Canonical Status Values:** See per-table breakdown above — there is no single shared "approval status" enum across domains today.

**Known Legacy Variants:** The `'Pending'` (capitalized) vs `'pending'` (lowercase) casing split between `wbom_employee_requests` and every other approval-style table is a real, minor structural inconsistency. Not fixed in this pass (would require a data migration); recorded as a known variant.

**Owner Changeable Rules:** Approval thresholds and who is authorized to approve what (see Roles) are policy.

**Structural Rules:** `escort_roster_change_requests`'s CHECK-constraint pattern (`db/migrations/026...sql:12`) is the model to follow for any new approval-style table — every other approval status column in this system lacks a DB-level constraint and relies entirely on application-code discipline.

**Validation Rules:** Recommend adding CHECK constraints to `fazle_payment_drafts.status` and `fazle_draft_replies.status` following `escort_roster_change_requests`'s pattern.

**Related Modules:** `modules/draft_approval`, `modules/payroll`, `modules/escort_roster`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md`.

---

## General Ledger (Phase 2b — not yet built)

**Business Purpose:** Double-entry accounting — chart of accounts, debit/credit postings, P&L and balance-sheet views. This is a **Phase 2b deliverable, not yet implemented.**

**Canonical Workflow:** None exists yet.

**Canonical Entity Names:** N/A — not yet built. Do not confuse `fpe_employee_ledger` (see below) with a general ledger; they are unrelated concepts that happen to share the word "ledger."

**Canonical Database Tables:** **Confirmed via exhaustive grep (2026-07-09): zero hits for `chart_of_accounts`, `general_ledger`, `ledger_entry`, `debit`, or `credit` anywhere in `modules/` or `db/migrations/`.** This vocabulary does not exist in the codebase today. The only ledger-like table is `fpe_employee_ledger` — a per-employee-per-period **running-balance** table (`opening_balance`, `total_earned`, `total_paid`, `total_advance`, `closing_balance`, `txn_count`), **not** a double-entry ledger (no account dimension, no debit/credit pair).

**Canonical APIs:** None yet.

**Canonical Status Values:** N/A.

**Known Legacy Variants:** None — there is nothing to be legacy relative to; this is a clean-slate build.

**Owner Changeable Rules:** The entire chart-of-accounts structure, once built, is a policy the Owner will define (which accounts exist, how they roll up).

**Structural Rules:** Per the Roadmap (`~/.claude/plans/now-you-are-in-squishy-chipmunk.md` and `SYSTEM_CONSOLIDATION_PLAN_2026-07-08.md`), the GL posting hook must attach to `create_transaction()` as an extension, not a parallel writer — preserving Article I's single-source-of-truth rule. Building it before the Payroll formula question (see Payroll section) is resolved would mean posting revenue/cost entries on top of an untrusted cost-side linkage (`program_id` is NULL on every real transaction today) — this document recommends resolving Payroll's Owner decision first, consistent with `docs/PHASE2_ENTRY_GATE.md`'s Phase 2b NO-GO.

**Validation Rules:** N/A yet.

**Related Modules:** None yet — future home likely `modules/fazle_payroll_engine` (extension) or a new `modules/general_ledger`.

**Related Documents:** `docs/PHASE2_ENTRY_GATE.md`, `docs/PRODUCTION_CERTIFICATION_v1.0.md`.

---

## Income

**Business Purpose:** Track money coming into the business (as distinct from money paid out to employees).

**Canonical Workflow:** `create_income_transaction()` (`modules/fazle_payroll_engine/accounting.py`) writes `fpe_income_transactions` directly.

**Canonical Entity Names:** "Income transaction" is a **structurally separate concept from "Cash Transaction" above** — `fpe_income_transactions` does not touch `fpe_cash_transactions` or `fpe_employee_ledger` (confirmed via the function's own docstring). Do not assume income and expense/payroll cash movements share a table or a reconciliation path today — they don't.

**Canonical Database Tables:** `fpe_income_transactions`.

**Canonical APIs:** `create_income_transaction()` (`modules/fazle_payroll_engine/accounting.py:375-433`).

**Canonical Status Values:** Not catalogued in this pass — **Not verified**.

**Known Legacy Variants:** None found.

**Owner Changeable Rules:** Income categorization/sourcing is a policy question, once Client Billing (below) exists to actually generate income records tied to invoices.

**Structural Rules:** Until Phase 2b (Billing/GL) is built, `fpe_income_transactions` and `fpe_cash_transactions` are two disconnected ledgers — a GL layer will need to define how they reconcile; that link does not exist today.

**Validation Rules:** Not verified this pass.

**Related Modules:** `modules/fazle_payroll_engine`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md`.

---

## Expense

**Business Purpose:** Track money the business spends that is not employee payroll (e.g. operational costs).

**Canonical Workflow:** **Not found as a distinct concept in this pass.** No dedicated "expense" table or module was located; employee-facing cost movements go through `fpe_cash_transactions` (categories `salary`/`advance`/`bonus`/`deduction`/`correction` — see Cash Transaction above), which does not include a general business-expense category.

**Canonical Entity Names:** Not established — **Not verified / not yet built as a distinct entity.**

**Canonical Database Tables:** None found.

**Canonical APIs:** None found.

**Owner Changeable Rules:** Whether "expense" becomes its own tracked entity (vs. folded into the future General Ledger's chart of accounts) is a Phase 2b design decision, not yet made.

**Structural Rules:** When built, should attach to the same `create_transaction()`-extension pattern as Income and the future GL, not a third parallel writer.

**Related Documents:** `docs/PHASE2_ENTRY_GATE.md`.

---

## Recruitment

**Business Purpose:** Turn a WhatsApp inquiry from a job candidate into a scored, tracked recruitment lead.

**Canonical Workflow:** WhatsApp message → `modules/recruitment_flow::route_recruitment_message()` (its own docstring calls this **"Canonical recruitment route"**) → `intake_message()` collects fields step-by-step per `COLLECTION_STEPS` (`name → age → area → job_preference → experience → phone_confirm`) → session tracked in `fazle_recruitment_sessions` with `funnel_stage` values `collecting` → `new` → `scored` (or `expired`).

**Canonical Entity Names:** "Recruitment session" = one `fazle_recruitment_sessions` row per candidate phone number, owned by `modules/recruitment_flow`. Do not confuse with `wbom_candidates` (see Known Legacy Variants — a real structural gap, not just a naming one).

**Canonical Database Tables:** `fazle_recruitment_sessions` is canonical, but **its schema was defined three separate times with conflicting column names**, each migration superseding the last: `db/migrations/002_align_tables.sql:28` (columns `full_name, collection_step, funnel_stage, score, score_bucket`) → `db/migrations/003_kb_schema_fix_and_recruitment.sql:38` (different columns: `name, step, status, phone_confirm`, plus a `candidate_id` FK) → `db/migrations/003b_recruitment_sessions_fix.sql:7-9` (`DROP TABLE IF EXISTS` and recreate, with an explicit comment: *"The prior migration (003) created fazle_recruitment_sessions with wrong columns"*). **The columns migration 003b actually creates — `collection_step`, `funnel_stage`, `full_name`, `confirmed_phone`, `score`, `score_bucket` — match what the live code (`modules/recruitment_flow`) actually reads and writes.** Treat 003b's schema as canonical; 002 and 003's column names (`name`, `step`, `status`) are **Historical** — superseded, not in use.

**Known Legacy Variants (structural gap, not just naming):** `wbom_candidates` is referenced as an FK target in both migration 003 and 003b (`candidate_id INTEGER REFERENCES wbom_candidates(candidate_id)`) and is directly read/written by `resources/recruitment.py`, but **no `CREATE TABLE wbom_candidates` statement exists anywhere in this repository** — it is one concrete, named instance of the ~150-table undocumented-migration gap found in `docs/PRODUCTION_CERTIFICATION_v1.0.md` Database Status. If this table exists in production (it must, for 003/003b's FK to have ever succeeded), its schema is entirely unrecorded in version control.

**Known Legacy Variants — modules:** Three recruitment modules exist, two generations apart. `resources/recruitment.py` (553 lines) is **Legacy** — uses a synchronous DB helper (`get_row`/`insert_row`/`update_row`), writes directly to `wbom_candidates`, not imported by any live code path except a comment cross-reference. `modules/recruitment_flow` is **Canonical** — async, `app.database`-based, writes `fazle_recruitment_sessions`, wired into `app/main.py`. `modules/recruitment_ai` and `modules/recruitment_assistant` are live companion modules (AI reply generation, follow-up detection) that support the canonical flow, not alternatives to it.

**Canonical APIs:** `modules/recruitment_flow::route_recruitment_message()`, `intake_message()`, `is_recruitment_trigger()`, `get_active_session()`, `recruitment_eligibility()`.

**Canonical Status Values (`funnel_stage`):** `collecting`, `new`, `scored`, `expired`. (`already_applied` is an action/response, not a stored stage value.)

**Owner Changeable Rules:** Scoring thresholds, funnel-stage timeout duration, and which fields are collected (`COLLECTION_STEPS`) are business policy.

**Structural Rules:** All new recruitment code must go through `modules/recruitment_flow`, never `resources/recruitment.py`. Any work that needs `wbom_candidates` must first recover/document its real schema (e.g. via `pg_dump` against production, the same method used for migration 029) before building on it further — building more code on an undocumented table compounds the exact risk this document exists to reduce.

**Validation Rules:** Not verified this pass beyond the funnel-stage vocabulary above.

**Related Modules:** `modules/recruitment_flow`, `modules/recruitment_ai`, `modules/recruitment_assistant`, `resources/recruitment.py` (legacy, do not extend).

**Related Documents:** `db/MIGRATION_INVENTORY.md`, `docs/PRODUCTION_CERTIFICATION_v1.0.md` Database Status.

---

## Employee

**Business Purpose:** Represent a person who works for the company (guard/escort/office staff) consistently enough that payroll, attendance, escort assignment, and identity resolution all agree on who they mean.

**Canonical Workflow:** There are genuinely **two separate employee systems** in this codebase, bridged by an explicit soft-link, not merged: `wbom_employees` (the operational system — attendance, escort assignment, basic salary) and `fpe_employees` (the payroll-engine's own employee record, used for transaction normalization and dedup). `fpe_employees.wbom_employee_id` (added additively, `modules/fazle_payroll_engine/migrations/008_unification.sql:12`) is the real cross-system link, backfilled by phone-number matching. `modules/payment_ingest/wbom_fpe_sync.py::sync_wbom_transaction()`/`backfill_wbom_to_fpe()` is the canonical bridge between the two systems.

**Canonical Entity Names — important, easily-confused pair:**
- `fpe_employees.canonical_employee_id` — **self-referential within `fpe_employees` only** (`REFERENCES fpe_employees(id)`). Used to mark a duplicate FPE row resolved to another FPE row (dedup **within** the payroll-engine's own employee table), tracked via `fpe_employee_resolution_links`. **This is NOT a cross-system link.**
- `fpe_employees.wbom_employee_id` — the actual **cross-system** soft-link to `wbom_employees.employee_id`.
- These two names look similar and are easy to confuse; they solve different problems. Do not use one where the other is meant.

**Canonical Database Tables:** `wbom_employees` (PK `employee_id`, operational system — attendance, escort, basic salary). `fpe_employees` (PK `id`, payroll-normalization system). `escort_roster_entries`, `wbom_escort_programs.escort_employee_id` (FK → `wbom_employees.employee_id`).

**Canonical APIs:** `modules/fazle_payroll_engine/employee.py::match_or_create_employee(payout_phone, employee_id_phone, ...)` (resolves/creates on the FPE side). `shared/identity_map.py::resolve_employee()` (resolves on the `wbom_employees` side only — does **not** itself bridge to `fpe_employees`), `validate_payment_identity()`, `sender_to_employee()`.

**Canonical Status Values:** `wbom_employees.status = 'Active'` (capitalized — see the casing note in the Attendance section; this is the same inconsistency, not a new one).

### Naming Rule — employee phone/mobile number (the single largest naming-drift finding in this pass)

`shared/identity_map.py` already contains hand-written prior art documenting exactly this problem (quoted directly, this document ratifies and extends it rather than re-deriving it):

```
employee_id        → wbom_employees.employee_id          (PK, internal integer)
employee_mobile    → wbom_employees.employee_mobile      (operational WhatsApp contact) — CANONICAL for "an employee's phone"
payout_phone       → fpe_cash_transactions.payout_phone   (payment destination — MAY differ from employee_mobile)
payment_mobile     → wbom_cash_transactions.payment_mobile (historical transaction record)
sender_phone       → derived from WhatsApp JID at message-receive time
candidate_phone    → recruitment tables (an applicant, NOT yet an employee)
escort_employee_id → wbom_escort_programs.escort_employee_id (FK → wbom_employees)
```

Confirmed via repo-wide grep, the full real picture is **larger** than that comment alone documents — on `wbom_cash_transactions` alone there are **four** distinct phone-shaped columns (`payment_mobile`, `employee_phone`, `payment_number`, plus the joined `wbom_employees.employee_mobile`):

```
Canonical:   employee_mobile   (wbom_employees — "an employee's operational WhatsApp contact number")
Aliases:     employee_phone    (wbom_cash_transactions — a DIFFERENT column on the same table as payment_mobile)
             payment_mobile    (wbom_cash_transactions — yet another phone-shaped column)
             payment_number    (wbom_cash_transactions — bKash/Nagad account number, payment-channel-specific)
             primary_phone     (fpe_employees — the FPE-side canonical phone, its own separate concept)
             payout_phone      (fpe_cash_transactions — payment destination; explicitly documented to MAY differ from employee_mobile)
             employee_id_phone (fpe_employees — parsed from a WhatsApp "ID: 01XXXXXXXXX" field, a third distinct FPE-side phone concept)
Deprecated:  none — all of the above are structurally different concepts (contact number vs. payment destination vs. ID-field-parsed number), not simply typos of one canonical name. Collapsing them into one column would lose real information (a guard's contact number and their bKash payout number are legitimately allowed to differ).
Future:      no rename recommended — the real fix is documentation (this section), not a schema change. The risk this drift creates is a developer assuming two of these are interchangeable when they are not; `employee_mobile ≠ payout_phone (always)` per identity_map.py's own explicit warning.
```

**Known Legacy Variants:** None of the phone-shaped columns above are simply "legacy typos" of each other — each represents a genuinely different real-world fact (contact number, payment destination, ID-field-parsed number). This is the one case in this document where the Naming Rule's normal remedy ("pick one canonical name") does not apply — the multiplicity is *intentional and correct*, and the risk is purely that a future developer might wrongly assume interchangeability. The Naming Rule table above exists specifically to prevent that mistake.

**Owner Changeable Rules:** None specific to naming — this is structural.

**Structural Rules:** Never assume `employee_mobile` and `payout_phone` are the same value for a given employee — always resolve them independently via the appropriate table. `canonical_employee_id` and `wbom_employee_id` on `fpe_employees` must never be confused (see above) — any new code touching employee dedup/cross-system resolution must cite `shared/identity_map.py` and this section before adding a new resolver.

**Validation Rules:** Not verified this pass beyond the naming map above.

**Related Modules:** `modules/fazle_payroll_engine` (employee.py, normalization.py), `modules/payment_ingest` (wbom_fpe_sync.py), `shared/identity_map.py`.

**Related Documents:** `shared/identity_map.py` (source of the original concept map), `docs/PRODUCTION_CERTIFICATION_v1.0.md`.

---

## AI Console

**Business Purpose:** Let the Owner/admin ask natural-language questions about the business and get answers sourced from real data, without giving the AI any ability to write to production or send messages on its own.

**Canonical Workflow:** A natural-language query → `modules/ai_console` (registry/governance layer, families: `runtime`, `business`, `memory`, `evidence`, `standard`, `orchestration`) → dispatches to a named tool in `modules/ai_readonly_tools::run_tool()` → each tool builds a fixed, parameterized SQL template against approved `ai_read_*` views only, never raw/caller-supplied SQL.

**Canonical Entity Names:** "AI tool" = one named, read-only function in `modules/ai_readonly_tools` (e.g. `get_contact_summary`, `get_payment_summary`, `get_escort_program_status`, `get_payroll_run_status`). There is no generic "ask the database anything" capability, by design.

**Canonical Database Tables:** `ai_read_contacts`, `ai_read_kb_articles`, `ai_read_module_bridge_status`, `ai_read_recent_messages`, `ai_read_recruitment_leads` — a dedicated, restricted set of read-optimized views/tables, distinct from the production tables they source from.

**Canonical APIs:** `modules/ai_readonly_tools::run_tool()` (dispatcher), plus 14 named tools (`get_contact_summary`, `get_contacts_list`, `get_recent_messages`, `get_payment_summary`, `get_escort_program_status`, `get_attendance_summary`, `get_payroll_run_status`, `get_daily_payments`, `get_employee_month_payments`, `get_lighter_assignment`, `get_recruitment_leads`, `get_employee_list`, `get_module_bridge_status`, `get_kb_articles`), plus `fetch_web_page` (read-only HTTP GET, not a DB tool).

**Known Legacy Variants:** None found — this is a clean, consistently-enforced pattern (confirmed: every tool's docstring/module header states the "ONLY SELECT... approved `ai_read_*` views only" constraint, and no exception was found).

**Owner Changeable Rules:** Which named tools exist and what data they expose is a product decision, made incrementally as new domains (dispatch, GL) come online — Phase 3 explicitly plans to extend this list, not replace the pattern.

**Structural Rules — non-negotiable:** No AI tool may accept raw/arbitrary SQL. No AI tool may write to a production table. No AI tool may send a WhatsApp/Meta message directly. `modules/ai_console/registry.py`'s `prohibited_behavior` field explicitly lists `unrestricted_sql` and `unrestricted_shell` as forbidden for every tool family — this is the correct, working model for all future AI-facing work (Phase 3's dispatch/GL tools must follow the identical pattern).

**Validation Rules:** Not verified beyond confirming no raw-SQL code path exists in this pass.

**Related Modules:** `modules/ai_readonly_tools`, `modules/ai_console`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md`, `SYSTEM_ATLAS.md` §5 rule 4.

---

## Search (Knowledge Base vs. RAG)

**Business Purpose:** Answer a customer or admin's question, either by exact policy lookup (Knowledge Base) or by semantic/AI-assisted retrieval over a larger corpus (RAG).

**Canonical Workflow:** These are two distinct, **already-coordinated** (not independent, and not redundant) systems: `modules/knowledge_base::get_reply()` tries, in order: (1) exact/substring keyword match against `fazle_knowledge_base` (DB) → (2) a hardcoded keyword fallback list → (3) `rag.answer()` as a tertiary semantic fallback. Separately, `modules/message_router` calls the Knowledge Base at Tier 13 and calls RAG **again, independently**, at Tier 15 (AI fallback) purely to enrich LLM context — this is a second, distinct invocation of RAG from the one nested inside KB's own fallback chain, not a bug, but worth naming so it isn't mistaken for one.

**Canonical Entity Names:** "Knowledge Base" (KB) = exact/keyword lookup against `fazle_knowledge_base`. "RAG" = semantic/vector (BM25 + optional Qdrant hybrid) retrieval over `resources/*.txt` files **and** the same `fazle_knowledge_base` active rows. They share a data source (the KB table) but are functionally different retrieval mechanisms — do not use the names interchangeably.

**Canonical Database Tables:** `fazle_knowledge_base` (`is_active`, `trigger_keywords`, `reply_text`, `key`, `category`) is the single source both systems read from. RAG additionally reads `resources/*.txt` files (not yet the curated `knowledge_base/` directory tree — a known, already-documented Phase 3 gap).

**Canonical APIs:** `modules/knowledge_base::get_reply()` (the coordinating entry point), `modules/rag::search()`/`answer()`/`build_index()`.

**Known Legacy Variants:** None found in the retrieval logic itself. The already-documented gap (Phase 3 backlog) is that RAG doesn't yet ingest the curated `knowledge_base/` markdown tree (this document's own home) — only `resources/*.txt`.

**Owner Changeable Rules:** The hardcoded keyword fallback list content is a policy/content question, not structure.

**Structural Rules:** KB's 3-step precedence (exact → hardcoded fallback → RAG) must not be reordered casually — it exists to prefer a precise, Owner-curated answer over a probabilistic one wherever one exists. RAG's daily/restart rebuild availability gap (see `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 1) affects both this section's RAG calls equally.

**Validation Rules:** Not verified this pass beyond the precedence chain above.

**Related Modules:** `modules/knowledge_base`, `modules/rag`, `modules/message_router`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md` RAG Certification, `docs/NEXT_IMPLEMENTATION_QUEUE.md`.

---

## Reports

**Business Purpose:** Turn raw operational data into a small set of standard, named summaries an admin can request on demand or receive on a schedule.

**Canonical Workflow:** `modules/reports::run_report(name, args, ...)` checks `fazle_report_cache` first, then executes the named builder, then logs the run to `fazle_report_runs`.

**Canonical Entity Names:** A "report" is one of exactly 5 registered names — there is no free-form/ad-hoc report concept.

**Canonical Database Tables:** `fazle_report_cache`, `fazle_report_runs`.

**Canonical APIs:** `run_report()`; the 5 registered report builders: `daily_summary`, `monthly_payroll`, `escort_utilization`, `payment_reconciliation`, `cash_position`. Scheduler-facing: `job_daily_admin_digest()`, enqueued through `modules.outbound`.

**Canonical Status Values:** N/A (reports are generated, not stateful).

**Known Legacy Variants:** None found.

**Owner Changeable Rules:** Which reports exist, their schedule/cadence, and their content thresholds are policy.

**Structural Rules:** New report types should be added as a 6th+ named builder in `_BUILDERS`, not as a separate ad-hoc reporting mechanism.

**Related Modules:** `modules/reports`, `modules/outbound`, `modules/scheduler`.

**Related Documents:** `docs/MODULE_MAP.md`.

---

## Notifications (Outbound)

**Business Purpose:** Send every outbound WhatsApp/Meta/Messenger message through one reliable, retrying, deduplicated queue rather than firing sends directly and losing them on a transient failure.

**Canonical Workflow:** Any module that needs to send a message calls `modules/outbound::enqueue()` (idempotency-deduped) → `sweep_once()` picks up queued rows and attempts delivery, transitioning status with exponential backoff on failure, eventually landing in a dead-letter queue if retries are exhausted.

**Canonical Entity Names:** There is no separate "notifications" system — `modules/outbound` is the one and only outbound-message queue in this codebase; do not build a parallel notification mechanism.

**Canonical Database Tables:** `fazle_outbound_queue` — the only table referenced for this purpose anywhere in the codebase (confirmed via grep).

**Canonical APIs:** `enqueue()`, `sweep_once()`.

**Canonical Status Values:** `pending`, `sending`, `sent`, `failed`, `dlq` (dead-letter queue).

**Known Legacy Variants:** None found.

**Owner Changeable Rules:** Retry/backoff timing and DLQ alerting thresholds are policy (`modules/scheduler`'s `dlq_alert` job interval is configurable).

**Structural Rules:** Every outbound send in the system must go through this one queue — a module sending a WhatsApp message via a direct bridge call instead of `enqueue()` would be a structural violation of the single-outbound-path rule.

**Validation Rules:** Not verified beyond the status vocabulary above.

**Related Modules:** `modules/outbound`, `modules/scheduler` (`dlq_alert` job).

**Related Documents:** `docs/MODULE_MAP.md`.

---

## Scheduler

**Business Purpose:** Run recurring background jobs (payroll compute, RAG rebuild, backups, digests, cleanup) on a defined cadence without manual triggering.

**Canonical Workflow:** `modules/scheduler::start_scheduler()` registers every job via `register_job()` + APScheduler's `add_job()`; each run is logged to `fazle_scheduled_jobs` via `_record_job_run()`.

**Canonical Entity Names:** `fazle_scheduled_jobs` is a **run-history log**, not a job-definition/scheduling table — the actual schedule (cron hour/interval) is defined in Python code (`modules/scheduler/__init__.py`), not data-driven from this table. Do not confuse "a row in `fazle_scheduled_jobs`" with "a configured job" — the former just records that a run happened.

**Canonical Database Tables:** `fazle_scheduled_jobs` (run-history only).

**Canonical APIs:** `start_scheduler()`, `register_job()`.

**Canonical Status Values / Full Job List (all 15 confirmed registered jobs — this document is the first place all of them have been listed together):**

| Job | Trigger | Default schedule |
|---|---|---|
| `daily_payroll_compute` | Cron | hour=2 (`PAYROLL_AUTO_COMPUTE_HOUR`) |
| `dlq_alert` | Interval | every 15 min (`DLQ_ALERT_INTERVAL_MIN`) |
| `health_summary` | Interval | every 6 hours |
| `agent_incident_summary` | Interval | every 6 hours |
| `stale_escort_reminder` | Cron | hour=9 |
| `payment_reconciliation` | Interval | every 1 hour |
| `backup_staleness_alert` | Cron | hour=3 |
| `combined_draft_cleanup` | Interval | every 1 hour |
| `daily_memory_review` | Cron | hour=9 (`MEMORY_REVIEW_HOUR`) |
| **`rag_rebuild`** | Cron | **hour=18** (`RAG_REBUILD_HOUR`) — see RAG certification finding, blocks customer replies 15-20 min daily |
| `lock_cleanup` | Interval | every 5 min (conditional) |
| `draft_ttl_cleanup` | Interval | every 30 min (conditional) |
| `bridge_watchdog` | Interval | every 5 min (conditional) |
| `daily_admin_digest` | Cron | hour=8 (`DAILY_DIGEST_HOUR`, conditional) |
| `daily_db_backup` | Cron | hour=2:30 (`DAILY_BACKUP_HOUR`/`_MIN`, conditional) |

**Known Legacy Variants:** None found.

**Owner Changeable Rules:** Every schedule hour above is an environment variable with a policy default — the Owner may retime any job (most urgently, `RAG_REBUILD_HOUR` — see `docs/NEXT_IMPLEMENTATION_QUEUE.md` Tier 1) without a code change.

**Structural Rules:** New recurring jobs must be added via `register_job()` in this one module, not as an ad-hoc `asyncio.create_task` loop elsewhere (which would be invisible to this canonical job list).

**Related Modules:** `modules/scheduler`, `modules/rag`, `modules/backup`, `modules/reports`.

**Related Documents:** `docs/PRODUCTION_CERTIFICATION_v1.0.md` RAG Certification.

---

## Message Parser (Message Router)

**Business Purpose:** Take an inbound WhatsApp/Meta message and route it to the correct business workflow based on who sent it and what they're asking for.

**Canonical Workflow:** `modules/message_router` evaluates **15 numbered tiers in order**: (1) family role, (2) escort-client role, (3) admin, (4) attendance, (5) intent classification, (6) accountant, (7-8) recruitment eligibility, (9) escort order, (10) employee, (11) advance/payment request, (12) office-location fast path, (13) knowledge base, (14) reviewed-reply lookup, (15) AI fallback. Earlier tiers win — a message is routed to the first tier that claims it, not the "best" one by some other measure.

**Canonical Entity Names:** "Role" (who the sender is: `family`, `admin`, `accountant`, `employee`, `candidate`/`new_lead`/`unknown`) is a distinct concept from "Intent" (what they want: `recruitment`, `client_order`, `escort_duty`, `attendance`, `slip_submission`, `employee_salary_complaint`, `legal_issue`, `payment_issue`, `salary_query`, `payment_due`, `office_location`, `unknown`). Do not conflate role and intent — a message has exactly one role (the sender's identity classification) and one intent (the message's classified purpose), evaluated by different tiers.

**Canonical Database Tables:** N/A directly (routing logic, not its own storage) — reads identity from `modules/identity_brain`, dispatches into the domain modules covered elsewhere in this document.

**Canonical APIs:** The tiered routing function in `modules/message_router/__init__.py` (main entry point).

**Canonical Status Values — named intent sets:** `_SAFE_AUTOSEND_INTENTS` (`recruitment, join, greeting, office_location, salary_query, payment_due, attendance, leave, escort_duty`) — intents allowed to auto-send a reply without human review. `_OPERATIONAL_AUTO_CREATE_INTENTS` (`salary_query, payment_due, attendance, leave, escort_duty, slip_submission`) — intents allowed to auto-create a draft/record without human review first.

**Known Legacy Variants:** None found.

**Owner Changeable Rules:** Which intents are "safe" to auto-send or auto-create is a real policy lever — tightening or loosening `_SAFE_AUTOSEND_INTENTS`/`_OPERATIONAL_AUTO_CREATE_INTENTS` directly controls how much the system acts without human review.

**Structural Rules:** New workflows must be added as a new numbered tier in this one router, not as a parallel routing mechanism elsewhere — the 15-tier, first-match-wins order is the single canonical routing structure for all inbound messages.

**Validation Rules:** Not verified beyond the tier/intent vocabulary above.

**Related Modules:** `modules/message_router`, `modules/identity_brain`, `modules/intent`.

**Related Documents:** `docs/DEVELOPER_GUIDE.md` §H, `docs/MODULE_MAP.md`.

---

## Escort

**Business Purpose:** Track a vessel-escort security assignment from client order through completion, roster reconciliation, and payment.

**Canonical Workflow:** Order intake → `wbom_escort_programs` row created (`status` default `'Assigned'`) → confirmed → `modules/escort_roster::sync_program_to_roster()` mirrors it into `escort_roster_entries` (computing pay via `calculate_pay()`, see Payroll) → duty completes → `escort_lifecycle::handle_release_event()` (admin-confirmation-gated, see Payroll/Testing findings) closes the program and creates a payment draft.

**Canonical Entity Names:** "Program" = one `wbom_escort_programs` row (one escort assignment). "Roster entry" = the mirrored `escort_roster_entries` row for the same program — a reconciliation/reporting record, not a second source of truth for the assignment itself (`wbom_escort_programs` is upstream; `escort_roster_entries` is downstream/derived).

**Canonical Database Tables:** `wbom_escort_programs` (canonical assignment record), `escort_roster_entries` (derived roster/reconciliation record), `escort_order_lighters` (per-lighter detail).

### Naming Rule — escort program status (significant, unenforced casing/vocabulary drift)

```
Canonical:   'Assigned' (DB default), transitioning through 'confirmed', 'Completed'
Aliases in the wild (case-sensitive, all found as literal write-sites):
             'draft'      — used in comments/reads, no confirmed write-site found
             'Assigned'   — capitalized, DEFAULT value and confirmed write-site
             'confirmed'  — lowercase, confirmed write-site
             'Completed'  — capitalized, confirmed write-site, read back case-insensitively (.lower()=="completed")
             'Running'    — appears ONLY in read-filter IN-lists, never written by any code found — likely dead/aspirational
             'cancelled', 'expired' — appear in the status_map lookup table and in read filters, but no write-site found for either
Deprecated:  none formally — but the read filter mixing lowercase 'confirmed' with capitalized 'Assigned','Running','Completed'
             in the SAME IN-list (modules/escort_roster/routes.py:490, modules/escort/__init__.py:1571) is a live bug risk:
             a case-sensitive TEXT column with no CHECK constraint means a query like this only works if every writer
             happens to match the exact casing the reader expects, which is not guaranteed anywhere by the schema.
Future:      pick one casing convention (this document recommends lowercase, matching every other status vocabulary
             found elsewhere in this system except wbom_employee_requests.status and wbom_employees.status — see
             Attendance/Approval sections) and add a DB-level CHECK constraint, following escort_roster_change_requests's
             existing pattern (the one status column in this whole codebase that already does this correctly).
```

`escort_roster_entries.roster_status` is a **separate, derived vocabulary**, produced by `modules/escort_roster/db.py`'s `status_map` from the program status above: `draft→draft, confirmed→confirmed, assigned→active, running→active, completed→completed, cancelled→cancelled, expired→expired`. The table's own DDL comment additionally lists a `'paid'` value that **no code was found ever writing** — likely aspirational/planned, not live; do not assume it is reachable today.

**Naming collision warning:** A completely unrelated function in `modules/fazle_payroll_engine/routes.py` also builds a dict literally named `status_map`, for a different domain entirely (escort-roster-**change-request** statuses: `pending/promoted/dismissed/duplicate → pending/approved/rejected/rejected`). Same variable name, two unrelated vocabularies — the same pattern already flagged for `review_status` in the Cash Transaction section. **Rule: `status_map` is a generic variable name reused across this codebase for unrelated lookups — always check which function/module you're in before trusting what a `status_map` maps.**

### Naming Rule — vessel identity

```
Canonical:   mother_vessel / lighter_vessel (paired columns — the dominant DB vocabulary,
             defined identically on wbom_escort_programs, escort_roster_entries, and escort_order_lighters)
Aliases:     vessel_name  — never a DB column; only an OCR-extraction dict key / regex label
             vessel       — a collapsed single-field alias used only in modules/ocr_processor and
                             modules/bridge_poller, which fold mother_vessel/lighter_vessel into one key
Deprecated:  none — vessel_name/vessel are legitimate OCR/extraction-layer conveniences, not schema drift,
             as long as they're translated back to mother_vessel/lighter_vessel before reaching a DB write.
Future:      no schema change needed; the risk is three separate normalization functions
             (_clean_vessel_name in modules/escort, _normalize_vessel_name in modules/ocr_processor,
             _normalize_vessel in modules/escort_slip_extractor) doing related text-cleanup with different
             names and, likely, different edge-case behavior — worth consolidating into one shared
             normalizer as a Tier-2 hygiene item, not urgent.
```

**Owner Changeable Rules:** None specific to naming above — purely structural findings.

**Structural Rules:** Any new code reading `wbom_escort_programs.status` must handle both casings until the column gets a CHECK constraint and a data-cleanup pass; do not add a 5th casing variant.

**Validation Rules:** Recommend a DB-level CHECK constraint on `wbom_escort_programs.status` and `escort_roster_entries.roster_status`, following the `escort_roster_change_requests` pattern (Approval section) — queued as a Tier-2 hygiene item.

**Related Modules:** `modules/escort`, `modules/escort_roster`, `modules/escort_lifecycle`, `modules/ocr_processor`, `modules/escort_slip_extractor`.

**Related Documents:** `docs/DEVELOPER_GUIDE.md` §G, `docs/NEXT_IMPLEMENTATION_QUEUE.md` (dual-write item).

---

## Client

**Business Purpose:** Represent the corporate/vessel clients who order escort/security services, distinct from employees.

**Updated 2026-07-09 (Client Domain Canonicalization pass) — full detail in `docs/CANONICAL_CLIENT_DOMAIN.md`, not duplicated here.** Summary: `wbom_contacts` is the de facto canonical contact/client table (`modules/contact_sync` explicitly merges all 3 WhatsApp sources into it as "the central table," 2,086 real rows). The earlier "genuinely open" framing is narrowed, not resolved: **three separate, uncoordinated client-classification mechanisms coexist** — `wbom_contacts.relation` (free text, stale, 2084/2086 = `'unknown'`), `wbom_contacts.relation_type_id → wbom_relation_types` (structured, real, but only ~5% populated — 91 rows tagged `'Client'`), and `fazle_contact_roles.role` (free text, no CHECK constraint, no FK to `wbom_contacts` at all, its own richer vocabulary: `repeat_client`, `vip_client`, `client_escort_buyer`, `vendor`, `blocked`). `wbom_relation_types` and `wbom_business_types` are both real, populated (5 rows each), well-designed lookup tables — `wbom_business_types` cleanly captures a Maritime vs. Land Security service-line split — but `business_type_id` is 0% populated on `wbom_contacts`.

**Canonical Database Tables:** `wbom_contacts` (**Canonical**), `wbom_relation_types` (**Canonical lookup**), `wbom_business_types` (**Canonical lookup, unused**). `fazle_contact_roles` (**Legacy — needs merge into `wbom_contacts.relation_type_id`**, no FK linking it to `wbom_contacts` at all, so the two can silently disagree about the same person). `fazle_clients` (**Legacy/dead** — 4 columns, id/name/phone/created_at, **0 rows**, no code reads or writes it — confirmed dead this pass, not just "not found in migrations").

**Owner Decision (2026-07-09), Phase 2A:** `supervisor`, `accountant`, and `blocked` are **NOT canonical client relation types** — no `wbom_relation_types` row is ever created for any of them. `supervisor`/`accountant` map onto the existing `Employee` relation type *only* where the phone number independently, verifiably matches a real `wbom_employees.employee_mobile` row (never a blanket free-text-label mapping) — every one of the 6 real `supervisor`/`accountant`-tagged contacts in production satisfied this check. `blocked` remains purely a moderation/status flag, applied via `wbom_contacts.is_active = false`, never a relationship type.

**Structural Rule — `.env` authority (Owner decision, 2026-07-09):** Phone numbers declared in `.env` (`ADMIN_NUMBERS`, `ACCOUNTANT_PHONE`, `FPE_CASH_AUTHORIZED_PHONES`, `FPE_INCOME_AUTHORIZED_PHONES`) are the **mandatory, authoritative source of truth** for who holds a functional role/authorization in this system. `wbom_contacts` (contact_book), `wbom_employees`, and cash-transaction-derived signals are **supporting/enrichment sources only** — never authoritative on their own, and must never silently override an `.env`-declared identity. Any future reconciliation/classification work must check this before writing a relation type, exactly as Phase 2A's migration (`db/migrations/031_client_domain_reconciliation.sql`) did — see that file's header for the full verification performed against all 22 distinct `.env`-listed numbers before it was applied.

**Structural Rules:** `wbom_contacts.relation_type_id` is the target single source of truth for "is this a client" going forward (see `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2A) — new code should not add a fourth classification mechanism.

**Owner Decision (2026-07-09), Phase 2B, final:** Four separated dimensions, not one combined field. `relation_type` = identity (Client/Vendor/Employee/Family/Escort Client — Phase 2A's existing `relation_type_id`, unchanged). `client_tier` = business value (Standard/Repeat/VIP/Strategic — new field, not yet implemented). `client_status` = business lifecycle (Active/Inactive/Suspended/Closed — new, *separate* field from `client_tier`, not yet implemented). `is_active` = system interaction permission (Phase 2A's existing boolean, unchanged). **`'Blocked'` is exclusively represented by `is_active=false`** — it must never appear as a value in `client_tier` or `client_status`. A contact can validly be `relation_type=Client, client_tier=VIP, client_status=Active, is_active=false` simultaneously (an active VIP customer, administratively blocked from communication). Full detail: `docs/CANONICAL_CLIENT_DOMAIN.md` §Client Tier, Client Status, and `is_active`.

**Related Modules:** `modules/contact_sync`, `modules/contact_roles`, `modules/wa_chat_frontend`, `modules/drafts`.

**Related Documents:** `docs/CANONICAL_CLIENT_DOMAIN.md` (full discovery + model), `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` (phased fix), `docs/PRODUCTION_CERTIFICATION_v1.0.md`, `docs/PHASE2_ENTRY_GATE.md`.

---

## Client Billing (Phase 2b — not yet built)

**Business Purpose:** Invoice clients for completed escort/security services, track accounts receivable, and record client payments — the revenue side of the business, which today has zero coverage.

**Updated 2026-07-09 — full detail in `docs/CANONICAL_CLIENT_DOMAIN.md` Task 1/Task 3, not duplicated here.** Correction to the prior pass: `wbom_billing_records` **does exist in production with a complete, correctly-designed schema** — `bill_id` PK, `program_id → wbom_escort_programs`, `contact_id → wbom_contacts`, `bill_date`, `bill_number`, `service_charge`, `other_charges`, `total_amount`, `payment_status` (default `'Pending'`), `payment_date`, `remarks` — confirmed via direct production schema query. **It has zero rows** — the schema is ready but no application code has ever written to it. Phase 2b is substantially an *activation* task, not a from-scratch design task.

**Canonical Database Tables:** `wbom_billing_records` (**Canonical target, currently dormant**).

**Owner Decision (2026-07-09), Phase 2B scope:** Billing-only — no General Ledger, no payroll-cost reconciliation, until the payroll pay-formula question (Payroll section, above) is separately finalized. Bill generation trigger: only once an escort program reaches **confirmed/completed final-client-slip state**, never draft/running. Invoice numbering: deterministic yearly sequence, `AQS-YYYY-NNNNNN`. Billing profile: **contact-level only** for this phase — Client Contact/Client Branch (multi-person, multi-site) deferred. `wbom_escort_programs.contact_id`: required/resolved for new intake going forward; existing rows stay nullable, backfilled over time.

**Structural Rules:** Before writing any Client Billing code, Phase 2A (Client entity reconciliation, above) must ship first — billing needs a stable, single-source-of-truth client identity to attach invoices to. (Phase 2A shipped 2026-07-09.)

**Blocking finding (re-audit, 2026-07-09), FULLY RESOLVED same day by Owner decision:** no client-facing rate/price field existed anywhere in this schema — confirmed via three independent checks (see `docs/PHASE2B_READINESS_REPORT.md` Re-Audit section for full evidence). **Owner resolution, fully specified:** a new `wbom_client_billing_profiles` table, managed via a frontend Client Settings UI (Payroll Page → Admin Tab), separate from `wbom_contacts` but carrying a required nullable-valued `contact_id` FK to it (no disconnected client system), and explicitly disconnected from the actual payroll formula (its wage fields are profit-estimation inputs only). Billing type is admin-selected at bill time (not inferred); VAT/Tax/Other Charges are flat-amount, internal profit-estimation deductions this phase (not added to the client's invoice); percentage support is a documented future enhancement. Contact Person Mobile must be a member of the existing `.env` `ESCORT_CLIENT_PHONES` list — `.env` remains the sole authority; this feature reads that list, never writes to it. **Status, updated 2026-07-09: fully implemented, applied, and live in production.** `db/migrations/034_client_billing_profiles.sql` (schema) + `db/migrations/035_client_billing_wage_general.sql` (adds `employee_wage_general`, closing the gap where the original field list had no wage figure paired with `general_rate`) both applied. `modules/client_billing/` implemented, mounted, service restarted, and verified live end-to-end (routes registered, full Add/Edit/soft-Delete cycle tested, table structurally confirmed, test rows cleaned up). `payroll.html` Client Settings UI built and confirmed rendering. Full plans: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md` and `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md`. Separately, `modules/billing/invoice_numbering.py` (`db/migrations/033_invoice_number_counters.sql`) remains written and tested but **not yet applied to production** — a distinct, still-pending piece, not part of this Client Billing Profile work.

**Related Documents:** `docs/CANONICAL_CLIENT_DOMAIN.md`, `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2B, `docs/PHASE2_ENTRY_GATE.md`, `docs/PHASE2B_READINESS_REPORT.md`, `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md`, `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md`.

---

## Identity

**Business Purpose:** Resolve "who is this WhatsApp sender" into a role (family/admin/accountant/employee/candidate/unknown) and, where applicable, a specific employee/client record — before any workflow acts on the message.

**Canonical Workflow:** Inbound message → `modules/identity_brain` resolves sender phone → role + confidence → `modules/message_router` Tier 1 (family) / Tier 2 (escort-client) / Tier 3 (admin) consume that resolution before any domain-specific tier runs. `shared/identity_map.py::resolve_employee()` is the canonical resolver specifically for "this phone number → which `wbom_employees` row," used by payment/validation code that must not conflate an employee's contact number with their payout number (see Employee section's Naming Rule for the full phone-naming picture this resolver exists to guard against).

**Canonical Entity Names:** "Role" (message_router's sender classification: `family`, `admin`, `accountant`, `employee`, `candidate`/`new_lead`/`unknown`) is resolved once per message and consumed by every downstream tier — not to be confused with RBAC's "Role" concept (Roles section above), which governs admin *command* permissions, not sender *identity*. These are two different, same-named concepts in two different subsystems — another instance of the same-name-different-meaning pattern this document exists to surface.

**Canonical Database Tables:** Identity resolution reads `wbom_employees`, `fazle_contact_roles`, `wbom_contacts` (see Client section — the ambiguity there directly affects identity resolution's completeness for non-employee senders).

**Canonical APIs:** `modules/identity_brain` (sender → role resolution), `shared/identity_map.py::resolve_employee()`, `validate_payment_identity()`, `sender_to_employee()`.

**Known Legacy Variants:** The "Role" naming collision with RBAC (above) is the main structural finding here.

**Owner Changeable Rules:** Confidence thresholds for identity resolution are policy.

**Structural Rules:** Never use "role" ambiguously in new code or documentation — always specify "sender role" (identity/message_router) vs. "permission role" (RBAC) when both could apply.

**Validation Rules:** Not verified beyond the resolver functions named above.

**Related Modules:** `modules/identity_brain`, `modules/message_router`, `shared/identity_map.py`, `modules/rbac`.

**Related Documents:** `docs/DEVELOPER_GUIDE.md` §H.

---

# Business Rules Change Log

Future **policy** changes are expected and welcome — the Owner may change any business rule marked "Owner Changeable" in any section above at any time, as the business evolves. That is this document's whole purpose: giving policy room to move without the underlying structure drifting apart.

**Structural inconsistency is forbidden.** No module, migration, or AI agent may introduce a new name for an existing entity, a new status value outside a documented vocabulary, or a new formula for an existing business calculation without first updating this document.

**Rule: this document must be updated *before* the corresponding implementation change ships, not after.** If a change is made and this document is only updated afterward as cleanup, that is itself a process failure worth naming in the next entry below — the whole point of Business Knowledge Drift is that "we'll document it later" is how every naming/formula conflict catalogued above actually happened.

## How to add an entry

```
### YYYY-MM-DD — <short title>
**Type:** Policy change | Structural fix | New conflict discovered | Conflict resolved
**Section(s) affected:** <name(s) from above>
**What changed:**
**Why:**
**Owner approval:** <if a policy change, confirm it was the Owner's decision, not an engineering unilateral call>
```

## Log

### 2026-07-09 — Document created
**Type:** New conflict discovered (multiple) + governance structure established.
**Section(s) affected:** All. Created following `docs/PRODUCTION_CERTIFICATION_v1.0.md`'s finding that the biggest remaining risk to Fazle Core is "Business Knowledge Drift," not code defects.
**What changed:** First version of this document. Surveyed 21+ business functions across the full codebase; found and documented (not resolved) two open Owner decisions (escort-duty pay formula, Client entity unification), several real naming collisions (`review_status` meaning different things on `fpe_cash_transactions` vs `fpe_unmatched_messages`; `status_map` used as a generic variable name for two unrelated domains; RBAC "Role" vs message_router "Role"), one large casing inconsistency (`wbom_escort_programs.status`), and three concretely-named instances of the previously-known-but-now-better-scoped undocumented-migration gap (`wbom_candidates`, `wbom_clients`/`fazle_clients`, `wbom_billing_records`).
**Why:** These conflicts already existed in the code; they were simply undocumented. This document's job was to surface them, not invent new policy to resolve them — several remain open Owner decisions by design (see Payroll, Client sections).
**Owner approval:** N/A — this pass documents existing reality; it does not itself change any policy or structure. Any resolution of the open items above requires a future dated entry with explicit Owner approval.

### 2026-07-09 — Client Domain Canonicalization (discovery + architecture)
**Type:** Conflict narrowed (not yet resolved) + new architecture designed.
**Section(s) affected:** Client, Client Billing.
**What changed:** Targeted production-schema investigation (`docs/CANONICAL_CLIENT_DOMAIN.md`) confirmed `wbom_contacts` as the de facto canonical client/contact table and found `wbom_billing_records` already exists in production with a complete, correct schema (0 rows — dormant, not missing). Narrowed the Client section's "genuinely open" framing to a specific, three-way classification-mechanism conflict (`wbom_contacts.relation` vs `relation_type_id` vs `fazle_contact_roles.role`) with a proposed resolution (Phase 2A in `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`) not yet implemented. `fazle_clients` reclassified from "undocumented drift, unknown status" to confirmed dead (0 rows, no code references).
**Why:** Phase 2B (Client Billing) cannot proceed without a stable client identity to bill against; this was identified as the largest concrete blocker after Payroll's formula question.
**Owner approval:** N/A — discovery and architecture only, no policy or structure changed yet. Phase 2A's actual migration (renaming `wbom_relation_types`'s `'escort_client'` row and reconciling `fazle_contact_roles`) requires Owner sign-off before production application, per `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md`.


