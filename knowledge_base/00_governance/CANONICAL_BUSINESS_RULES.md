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

**Canonical Workflow (updated 2026-07-11, Office Payroll Hardening):** Four entry points now converge on `wbom_attendance`, distinguished by the `entry_source` column (migration 040):
1. `manual_office` — direct admin entry via the frontend Office Attendance tab (`modules.attendance.crud`), approved immediately at creation (no draft stage).
2. `message_parser` — a supervisor's WhatsApp message parsed by `modules.attendance_parser`, held as a `fazle_draft_replies` draft until an admin approves it (via WhatsApp `APPROVE <id>` or the frontend Review Queue's approve button — both now call the same shared `modules.attendance.finalize_attendance_draft()` service, so the two channels cannot drift).
3. `whatsapp_escort` — a guard's own self-report ("হাজির আছি"), same draft→approve mechanism, but this source **never** counts toward office payroll regardless of approval (Owner decision).
4. `escort_release` — `modules.escort_lifecycle`'s retroactive backfill on program release, automatic, escort-only.

**Owner decision (2026-07-10/11): office payroll's duty-day source is `manual_office` and admin-approved `message_parser` only** — never `whatsapp_escort` or `escort_release`, regardless of approval status. This is enforced by `modules.payroll.dry_run`'s explicit `entry_source` filter, not by inference.

**Canonical Entity Names:** "Attendance" always refers to one row per (employee, date). Do not conflate with "duty days" (`wbom_escort_programs.day_count`, escort-only shift-based aggregate) — office attendance's duty-day value is separately derived per row from `status` (`Present`=1.0, `Half Day`=0.5, `Absent`=0.0), never a client-submitted number.

**Canonical Database Tables:** `wbom_attendance` (employee_id, attendance_date, status, location, recorded_by, remarks, **+ `entry_source`/`approval_channel`/`approved_by`/`approved_at`/`source_draft_id`, migration 040**) is canonical, per `modules/attendance/__init__.py`'s own docstring and every write path found in this repo.

**Canonical APIs:** `modules/attendance/__init__.py::save_attendance(employee_id, attendance_date, status="Present", location="", recorded_by="system", remarks="", entry_source="legacy", approval_channel=None, approved_by=None, source_draft_id=None, conn=None)`, `finalize_attendance_draft(draft_id, *, channel, actor)` (the shared approval service). `modules/attendance/crud.py::create_manual_attendance/update_manual_attendance/delete_manual_attendance/list_attendance/lookup_employee_by_id/suggest_employees_by_name` (2026-07-11, manual CRUD — `POST /admin/attendance` rejects a duplicate employee+date with 409, unlike the parser-approval path's idempotent upsert).

**Canonical Status Values:** `Present`, `Half Day`, `Absent` (extended 2026-07-11 for manual entry; the escort-side self-report/backfill paths still only ever write `Present`). `wbom_employees.status` uses `Active` (capitalized) as its confirmed value — note the capitalization convention differs from the lowercase RBAC/admin status values (`active`) — this is a real, minor naming/casing inconsistency, recorded here rather than silently normalized.

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

### Formula Rule — RESOLVED 2026-07-10 (Owner decision, canonical going forward)

Two separate, non-overlapping formulas — the earlier "three formulas, none canonical" state is now resolved by explicit domain split:

```
Formula 1 — escort/vessel-duty pay (canonical for Escort-designation employees):
    12h shift = 200tk, 24h (D+N) = 400tk (flat, shift-based, independent of
    the guard's own basic_salary). Matches modules/escort_roster/
    calculations.py::calculate_pay's existing, already-correct shift-rate
    model.
    modules/payment_workflow::create_escort_payment_draft (FIXED 2026-07-10,
    was previously using basic_salary/30 -- Formula 3, wrong for escort duty):
        gross_amount = duty_days × DEFAULT_DAILY_RATE (400tk/day)
        net_payable  = gross_amount − food_bill + conveyance − advances
                       (conveyance ADDED, not subtracted -- Owner decision)
    Status: this is the function that actually computes every real escort
    payment draft on release -- now correct.

Formula 3 — monthly office-staff salary (canonical for Office-staff-
classified employees only, per the Office-Staff Eligibility rule below --
NEVER for Escort-designation employees):
    monthly_payable_salary = basic_salary ÷ calendar_days_in_payroll_month
                              × approved_duty_days
    approved_duty_days comes ONLY from wbom_attendance rows with
    entry_source IN ('manual_office', 'message_parser') -- see Attendance
    section above. No separate absence/unpaid-leave/holiday/overtime
    deduction -- not being credited a duty day for a day not attended is
    the entire mechanism (Owner decision, 2026-07-11: "do not invent leave
    logic, holiday logic, overtime, or deductions").
    Implemented in modules/payroll/dry_run.py (read-only preview/dry-run/
    reconciliation) and modules/payroll::compute_all_for_period_office_only
    (the authoritative path -- NOT YET SAFE TO ENABLE, see below).

modules/payroll::compute_run's OWN internal formula (basic_salary, full and
unprorated, + a flat per-day escort-program allowance) has NOT been
rewritten to Formula 3 yet -- compute_all_for_period_office_only filters
WHO compute_run runs for (office staff only, via classify_office_staff),
but compute_run itself still computes the OLD, pre-Owner-decision amount.
This is why job_daily_payroll (modules.scheduler) is guarded behind
settings.office_payroll_authoritative_enabled (default False, 2026-07-11)
-- the authoritative path must not be enabled until compute_run's own
formula is rewritten to match Formula 3.

Actual production payment path for basic salary today, unchanged: admin
types a free amount in "PAID <id> <amount> <method>"; the system records
exactly that number via create_transaction(), with no cross-check against
Formula 3 either. fpe_cash_transactions.program_id is NULL on all 3,671
real transactions.
```

### Office-Staff Eligibility Rule (Owner decision, 2026-07-10/11, canonical)

An employee is Office Staff (and therefore eligible for Formula 3, never Formula 1) when, per `modules/payroll/eligibility.py::classify_office_staff` (the single centralized helper -- do not duplicate this logic):
1. `status='Active'` (case-sensitive, matching `wbom_employees`'s own confirmed casing).
2. `designation` is not `'Escort'` (case-insensitive).
3. Not an obvious bookkeeping/test/placeholder record (best-effort name-substring heuristic — disclosed as imperfect, not a guarantee).
4. `basic_salary > 0`.
5. Either the employee name contains (case-insensitive, anywhere) `Al-Aqsa`/`Al-Manaratayn`/`Office`/`Operation`/`SV`, OR `designation` is one of `Staff`/`Guard`/`Labor`/`Day Labour`/`Supervisor`/`Operation Officer`/`Office Assistant`/`Security Guard`. (`Al-Manaratayn` added 2026-07-21 alongside the pre-rebrand `Al-Aqsa` keyword — existing employee-name records still carry the old name.)

**Confirmed production result (2026-07-11): 0 of 188 employees are currently eligible** — 184 are Escort-designation (excluded by rule #2), and the remaining 4 non-Escort records each fail a different gate (one inactive, two with `basic_salary=0`, one flagged as a likely bookkeeping/test row). This is a **data-state condition, not an implementation failure** — the eligibility pipeline is built and verified correct against real production data; it is simply dormant until a real, active, positively-salaried office employee exists.

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

**Known open gap (confirmed live 2026-07-21, not yet fixed):** `recruitment_eligibility()`'s `OPERATIONAL_INTENTS` set (`attendance, leave, salary_query, payment_due, advance_request, escort_duty, client_order, slip_submission`) rejects a message as `eligible: False, reason: operational_intent` based on the classified *intent* alone, evaluated before the smarter 3-layer `recruitment_detector` gets a chance to run — regardless of whether the sender's *role* is `unknown`/`candidate`/`new_lead` (i.e. clearly not yet a known operational identity). Confirmed live: a fresh/unidentified sender asking "how much salary for escort, monthly" is classified intent `salary_query` by both the deterministic and live-Ollama classifiers, which trips this gate and prevents the message from ever reaching the recruitment funnel's KB-grounded reply (with the actual `SALARY_REPLY` figures) — it instead falls through to `modules/route_b`'s generic Ollama-only handler, which asks a reasonable clarifying question but doesn't have those figures pre-loaded. This is not a silent-drop (Route B still answers) but it is a misroute — same architectural pattern (blunt keyword/intent match overriding role-aware nuance) as the keyword-priority bug in `modules/social_auto_reply/classifier.py` (see that module's own known issues, not covered by this document). Recommended fix, not yet applied: don't hard-reject on `intent in OPERATIONAL_INTENTS` alone for `unknown`/`candidate`/`new_lead` roles — let the 3-layer detector weigh in first. See `management_decisions.md` 2026-07-21 entry and `../05_workflows/whatsapp_reply_workflow.md`.

**Canonical Status Values (`funnel_stage`):** `collecting`, `new`, `scored`, `expired`. (`already_applied` is an action/response, not a stored stage value.)

**Owner Changeable Rules:** Scoring thresholds, funnel-stage timeout duration, and which fields are collected (`COLLECTION_STEPS`) are business policy.

**Structural Rules:** All new recruitment code must go through `modules/recruitment_flow`, never `resources/recruitment.py`. Any work that needs `wbom_candidates` must first recover/document its real schema (e.g. via `pg_dump` against production, the same method used for migration 029) before building on it further — building more code on an undocumented table compounds the exact risk this document exists to reduce.

**Validation Rules:** Not verified this pass beyond the funnel-stage vocabulary above.

**Related Modules:** `modules/recruitment_flow`, `modules/recruitment_ai`, `modules/recruitment_assistant`, `resources/recruitment.py` (legacy, do not extend).

**Related Documents:** `db/MIGRATION_INVENTORY.md`, `docs/PRODUCTION_CERTIFICATION_v1.0.md` Database Status.

---

## Employee

**Business Purpose:** Represent a person who works for the company (guard/escort/office staff) consistently enough that payroll, attendance, escort assignment, and identity resolution all agree on who they mean.

**Canonical Workflow:** There are genuinely **two separate employee systems** in this codebase, bridged by an explicit soft-link, not merged: `wbom_employees` (the operational system — attendance, escort assignment, basic salary) and `fpe_employees` (the payroll-engine's own employee record, used for transaction normalization and dedup). `fpe_employees.wbom_employee_id` (added additively, `modules/fazle_payroll_engine/migrations/008_unification.sql:12`) is the real cross-system link, backfilled by phone-number matching. **Two bridges exist, serving different purposes — not one superseding the other, corrected 2026-07-17 (this document previously named only the first):** `modules/payment_ingest/wbom_fpe_sync.py::sync_wbom_transaction()`/`backfill_wbom_to_fpe()` is a **historical/backfill migration adapter** (its own header: "WBOM → FPE Historical Migration Adapter (Phase 7)"), routing through the well-guarded `fazle_payroll_engine.employee.match_or_create_employee()`. `modules/payment_ingest/fpe_bridge.py::resolve_fpe_employee_for_wbom_employee()`/`create_fpe_transaction_from_ingest()` is the **live, real-time bridge** used by the actual admin→accountant WhatsApp shorthand path (`ingest_admin_cash_entry()`) every time an accountant types a cash instruction — this is the one audited and partially fixed 2026-07-17 (Session 20 Phase 2, S20-10: added a `payout_mobile` fallback search to `_match_or_create_instruction_employee()` in `modules/payment_ingest/__init__.py`, since it previously only ever tried one collapsed lookup value). Both bridges can auto-create an `fpe_employees` row on a miss; `resolve_fpe_employee_for_wbom_employee()`'s own creating INSERT leaves `resolution_status` at its schema default (`'unresolved'`) even when it sets `wbom_employee_id` in the same statement — a known, documented, non-broken metadata staleness (see `modules/reports::_b_unresolved_fpe_employees()`'s `linked_but_mislabeled` bucket, which exists specifically to separate these rows from genuinely-unlinked ones).

**Canonical Entity Names — important, easily-confused pair:**
- `fpe_employees.canonical_employee_id` — **self-referential within `fpe_employees` only** (`REFERENCES fpe_employees(id)`). Used to mark a duplicate FPE row resolved to another FPE row (dedup **within** the payroll-engine's own employee table), tracked via `fpe_employee_resolution_links`. **This is NOT a cross-system link.**
- `fpe_employees.wbom_employee_id` — the actual **cross-system** soft-link to `wbom_employees.employee_id`.
- These two names look similar and are easy to confuse; they solve different problems. Do not use one where the other is meant.

**Canonical Database Tables:** `wbom_employees` (PK `employee_id`, operational system — attendance, escort, basic salary). `fpe_employees` (PK `id`, payroll-normalization system). `escort_roster_entries`, `wbom_escort_programs.escort_employee_id` (FK → `wbom_employees.employee_id`).

**Canonical APIs:** `modules/fazle_payroll_engine/employee.py::match_or_create_employee(payout_phone, employee_id_phone, ...)` (resolves/creates on the FPE side). `shared/identity_map.py::resolve_employee()` (resolves on the `wbom_employees` side only — does **not** itself bridge to `fpe_employees`), `validate_payment_identity()`, `sender_to_employee()`.

**Updated 2026-07-13 — display-consistency fix, not a schema/naming change:** `payroll.html`'s Add Attendance form did its employee lookup purely against `wbom_employees` (via `modules.attendance.crud`), unlike every other form on the page (Add Payment, Manual Entry, Search), which all resolve through `fpe_employees` and show `employee_id_phone`. `modules.attendance.crud.lookup_employee_by_id()`/`suggest_employees_by_name()` now also attach `employee_id_phone` via a read-only scalar subquery, preferring `fpe_employees.wbom_employee_id` (the cross-system link described above) when it already exists, falling back to matching `employee_mobile`/`bkash_number`/`nagad_number` against `fpe_employees.primary_phone`/`employee_id_phone`. This does not create or write any link — it is `NULL` when no `fpe_employees` row has ever been matched to that `wbom_employees` row (common for office staff who have never been through a payment flow).

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

**Canonical Workflow (updated 2026-07-21 — Route A / Route B):** `modules/message_router` evaluates numbered tiers in order, first match wins: (1) family, (2) escort-client role, (3) admin, (4) attendance, (5) intent classification, (6) accountant, (7-8) recruitment eligibility, (9) escort order, **(9.5) Route B — Ollama-only handler (`modules/route_b`) for any sender whose role is in `identity_brain.ROUTE_B_ROLES` and whose message wasn't claimed by the escort-order safety net**, (10) employee, (11) advance/payment request (Sprint-3A), **(11.5) Route B again — same handler, now reached by escort/security_guard/candidate/unknown senders whose message wasn't a genuine advance request**, (12) office-location fast path, (13) knowledge base, (14) reviewed-reply lookup, (15) AI fallback. Full policy: `../05_workflows/whatsapp_reply_workflow.md`.

**Route A / Route B split:** every sender resolves to Route A (admin, accountant, client/buyer roles, family, employee/staff, `internal_coordinator` — the existing tiers above, KB/template/multi-provider `ai.generate_reply()` chain) or Route B (escort, security_guard, candidate, unknown — `modules/route_b`, Ollama only, no Groq/GitHub fallback). Route B is deliberately positioned *after* every existing operational safety tier (escort-order parsing, attendance, Sprint-3A advance flow) so none of that mature logic is preempted — it only claims what would otherwise fall through to the generic KB/AI-fallback tiers. `SOCIAL_AUTO_REPLY_SINGLE_ENGINE`/`_requires_legacy_workflow()`'s content-based fork to `modules/social_auto_reply` was removed for the three WhatsApp entry points (`bridge_poller`, `/webhook/meta`, `/webhook/mcp1`/`mcp2`) — `social_auto_reply` itself is unmodified and remains the live engine for Facebook Messenger DMs and Facebook Page comments only.

**Route A — No Auto Reply, name-based gate (2026-07-22):** within the Route B population, a sender whose *saved contact name* contains Client/Escort/Office/Operation/Accountant/Security Guard/Al-Aqsa/Al-Manaratayn/আল-আকসা/আল-মানারাতাইন anywhere (substring, not prefix) gets no AI/generic/recruitment reply at all — enforced both by `_should_silent_skip()` (early, `wbom_contacts.display_name`) and a backstop immediately before `handle_route_b_message()` is called (`identity_brain`'s resolved name, for contacts not yet synced). Escort-named contacts get one exception: a genuinely order-shaped message (mother vessel + lighter vessel + a mobile number) is still routed to `handle_escort_client_message()` for a structured admin-review draft — the sender still gets no reply, only the destination differs. Full policy: `../05_workflows/whatsapp_reply_workflow.md` §14.

**Route B Conversation Handoff (built 2026-07-22, `ROUTE_B_HANDOFF_ENABLED=false` — NOT YET ACTIVE):** `modules.route_b_handoff` lets Route B transfer a conversation to an existing canonical workflow (recruitment intake, an employee-claim admin review, or — separately flagged, also inactive — an escort-order draft) once identity/intent becomes clear. `employee` was NOT added to `ROUTE_B_ROLES`; a phone already on file in `wbom_employees` (Active or Inactive) still never reaches Route B, so an "employee claim" handoff can never grant Tier 10 access directly — it always produces an admin-review draft, verified or not. Ollama only ever supplies a hint category (`app.ollama.classify_route_b_category()`, Ollama-only); every state-changing call is an existing, unmodified canonical function. Full policy: `../05_workflows/whatsapp_reply_workflow.md` §15.

**Canonical Entity Names:** "Role" (who the sender is: `family`, `admin`, `accountant`, `employee`, `escort`, `security_guard`, `internal_coordinator`, `candidate`/`new_lead`/`unknown`) is a distinct concept from "Intent" (what they want: `recruitment`, `client_order`, `escort_duty`, `attendance`, `slip_submission`, `employee_salary_complaint`, `legal_issue`, `payment_issue`, `salary_query`, `payment_due`, `office_location`, `unknown`). Do not conflate role and intent — a message has exactly one role (the sender's identity classification) and one intent (the message's classified purpose), evaluated by different tiers.

**Canonical Database Tables:** N/A directly (routing logic, not its own storage) — reads identity from `modules/identity_brain`, dispatches into the domain modules covered elsewhere in this document.

**Canonical APIs:** The tiered routing function in `modules/message_router/__init__.py` (main entry point).

**Canonical Status Values — named intent sets:** `_SAFE_AUTOSEND_INTENTS` (`recruitment, join, greeting, office_location, salary_query, payment_due, attendance, leave, escort_duty`) — intents allowed to auto-send a reply without human review, still live. `_OPERATIONAL_AUTO_CREATE_INTENTS` (`salary_query, payment_due, attendance, leave, escort_duty, slip_submission`) — **disabled 2026-07-17 (Session 20 Phase 1, Owner directive)**, see Known Legacy Variants below; the constant is still defined but has no live consumer.

**Known Legacy Variants:** `_OPERATIONAL_AUTO_CREATE_INTENTS` is now dead policy, not a live lever — its one consumer, `_seed_unknown_employee_identity()`, was disabled (function body replaced with `return identity, False`) because every sender reaching it had already failed every trust check `identity_brain.detect_identity()` performs (not admin, not accountant, not escort-client, not an existing employee/contact match), making the auto-create it gated exactly the "random/unapproved number creates a row" pattern ruled out by the Owner's 2026-07-17 finance-integrity audit. Kept in code (not deleted) and named here (not silently dropped) per this document's own discipline — do not resurrect this as a live gate without a new dated Owner decision. Full record: `management_decisions.md` Session 20 Step 3 Phase 1, S20-09, commit `47018f7`.

**Owner Changeable Rules:** `_SAFE_AUTOSEND_INTENTS` remains a real policy lever — tightening or loosening it directly controls how much the system auto-sends without human review. `_OPERATIONAL_AUTO_CREATE_INTENTS` is no longer a live lever (see above) — changing its contents today has no effect until/unless a future Owner decision reinstates a consumer.

**Structural Rules:** New workflows must be added as a new numbered tier in this one router, not as a parallel routing mechanism elsewhere — the 15-tier, first-match-wins order is the single canonical routing structure for all inbound messages.

**Validation Rules:** Not verified beyond the tier/intent vocabulary above.

**Related Modules:** `modules/message_router`, `modules/identity_brain`, `modules/intent`, `modules/route_b`, `modules/bridge_poller`.

**Related Documents:** `docs/DEVELOPER_GUIDE.md` §H, `docs/MODULE_MAP.md`, `../05_workflows/whatsapp_reply_workflow.md`.

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

### Dispatch write layer and `escort_changed` status (Phase 2A.2/2A.3, added 2026-07-19)

`modules/dispatch` is the canonical write-orchestration layer for every
controlled Dispatch mutation against `wbom_escort_programs`
(assignment/reassignment, escort-replacement segments, safe unassignment,
manual completion, release-slip approval, controlled override) — it never
duplicates roster-sync or audit SQL, always calling
`modules.escort_roster.db.sync_program_to_roster()`/`log_audit()` inside
the same transaction. All Dispatch writes are manual/human-triggered; no
auto-assignment, LLM ranking, or automatic real-overlap resolution exists
anywhere in this layer.

`escort_changed` (migration 049) is a new, additive status value alongside
the existing vocabulary above: the earlier segment of a vessel operation
that was replaced by a later escort before reaching normal completion.
Historical and non-blocking for bookability/attendance/release-target
purposes, exactly like `completed`/`cancelled` — but semantically distinct
(a real duty segment that happened, not a cancellation). Set only by
`modules.dispatch.create_replacement_segment()`, never by any WhatsApp
message-driven path directly.

`wbom_escort_programs.replaces_program_id` (migration 050, nullable
self-FK) links a replacement segment back to the program it replaced. A
partial unique index allows at most one **non-terminal** replacement per
predecessor while permitting multiple historical/terminal replacements to
coexist (Owner Decision 2, 2026-07-19).

**Seven-day boundary rule (Owner Decision 6, 2026-07-19):** when an earlier
same-vessel program is replaced by a different escort, `date_delta < 7
days → escort_changed`; `date_delta >= 7 days → completed`; exactly seven
days is `completed`. Explicit release/completion evidence always overrides
this duration inference. Same-vessel matching itself requires both
`mother_vessel` and `lighter_vessel` non-empty and matching on both sides
(Owner Decision 3) — an empty `lighter_vessel` on either side routes to
manual review instead of an automatic match, a rule specifically added
after real bulk-import artifacts were found producing false matches
without it.

Full detail, all seven Owner Decisions, and the stage-by-stage
implementation record: `knowledge_base/00_governance/management_decisions.md`,
"Dispatch Phase 2A.3" section.

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

**Updated 2026-07-09 (Client Domain Canonicalization pass) — full detail in `docs/CANONICAL_CLIENT_DOMAIN.md`, not duplicated here.** Summary: `wbom_contacts` is the de facto canonical contact/client table (`modules/contact_sync` explicitly merges all 3 WhatsApp sources into it as "the central table," 2,086 real rows). The earlier "genuinely open" framing is narrowed, not resolved: **three separate, uncoordinated client-classification mechanisms coexist** — `wbom_contacts.relation` (free text, stale, 2084/2086 = `'unknown'`), `wbom_contacts.relation_type_id → wbom_relation_types` (structured, real, but only ~5% populated — 91 rows tagged `'Client'`), and `fazle_contact_roles.role` (free text, no CHECK constraint, no FK to `wbom_contacts` at all, its own richer vocabulary: `repeat_client`, `vip_client`, `client_escort_buyer`, `vendor` — `blocked` was also a value here until it was permanently removed, Owner decision 2026-07-11, see migration 042; there is no "blocked number" concept in this system). `wbom_relation_types` and `wbom_business_types` are both real, populated (5 rows each), well-designed lookup tables — `wbom_business_types` cleanly captures a Maritime vs. Land Security service-line split — but `business_type_id` is 0% populated on `wbom_contacts`.

**Canonical Database Tables:** `wbom_contacts` (**Canonical**), `wbom_relation_types` (**Canonical lookup**), `wbom_business_types` (**Canonical lookup, unused**). `fazle_contact_roles` (**Legacy — needs merge into `wbom_contacts.relation_type_id`**, no FK linking it to `wbom_contacts` at all, so the two can silently disagree about the same person). `fazle_clients` (**Legacy/dead** — 4 columns, id/name/phone/created_at, **0 rows**, no code reads or writes it — confirmed dead this pass, not just "not found in migrations").

**Owner Decision (2026-07-09), Phase 2A:** `supervisor` and `accountant` are **NOT canonical client relation types** — no `wbom_relation_types` row is ever created for either. They map onto the existing `Employee` relation type *only* where the phone number independently, verifiably matches a real `wbom_employees.employee_mobile` row (never a blanket free-text-label mapping) — every one of the 6 real `supervisor`/`accountant`-tagged contacts in production satisfied this check.

**Superseded (Owner decision, 2026-07-11):** This passage previously also described `blocked` as a moderation/status flag applied via `wbom_contacts.is_active = false`. That framing is removed — there is no "blocked number"/moderation-block concept in this system at all, in any form. `is_active` itself is untouched and remains a legitimate, ordinary contact-record active/inactive flag with no "blocked" semantic attached to it.

**Structural Rule — `.env` authority (Owner decision, 2026-07-09):** Phone numbers declared in `.env` (`ADMIN_NUMBERS`, `ACCOUNTANT_PHONE`, `FPE_CASH_AUTHORIZED_PHONES`, `FPE_INCOME_AUTHORIZED_PHONES`) are the **mandatory, authoritative source of truth** for who holds a functional role/authorization in this system. `wbom_contacts` (contact_book), `wbom_employees`, and cash-transaction-derived signals are **supporting/enrichment sources only** — never authoritative on their own, and must never silently override an `.env`-declared identity. Any future reconciliation/classification work must check this before writing a relation type, exactly as Phase 2A's migration (`db/migrations/031_client_domain_reconciliation.sql`) did — see that file's header for the full verification performed against all 22 distinct `.env`-listed numbers before it was applied.

**Structural Rules:** `wbom_contacts.relation_type_id` is the target single source of truth for "is this a client" going forward (see `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` Phase 2A) — new code should not add a fourth classification mechanism.

**Owner Decision (2026-07-09), Phase 2B, final:** Four separated dimensions, not one combined field. `relation_type` = identity (Client/Vendor/Employee/Family/Escort Client — Phase 2A's existing `relation_type_id`, unchanged). `client_tier` = business value (Standard/Repeat/VIP/Strategic — new field, not yet implemented). `client_status` = business lifecycle (Active/Inactive/Suspended/Closed — new, *separate* field from `client_tier`, not yet implemented). `is_active` = system interaction permission (Phase 2A's existing boolean, unchanged).

**Superseded (Owner decision, 2026-07-11):** This passage previously described `'Blocked'` as a value exclusively represented by `is_active=false`. That framing is removed along with the rest of the "blocked number" concept — `is_active=false` simply means an inactive contact record, nothing more; it carries no "blocked"/moderation semantic. Full detail: `docs/CANONICAL_CLIENT_DOMAIN.md` §Client Tier, Client Status, and `is_active`.

**Updated 2026-07-22 — `fazle_contact_roles` phone-format duplicates found and cleaned.**
The "no FK linking it to `wbom_contacts`, so the two can silently disagree" gap above is
still open (not resolved by this pass), but a concrete symptom of it was: 8 phone numbers
had duplicate rows in `fazle_contact_roles` itself, purely from inconsistent phone-string
format (`01XXXXXXXXX` vs `8801XXXXXXXXX`) bypassing its own `(phone, platform)` unique
constraint (a string-equality constraint, blind to two different strings representing the
same number). Cleaned (8 rows removed, audit-backed) and the write path
(`modules/contact_roles/routes.py`) now normalizes phone before every read/write, making
the existing constraint actually effective. `wbom_contacts` itself was independently
checked and found to already have zero such duplicates (protected by consistent
normalization in `modules/contact_sync`). Full record: `management_decisions.md`
2026-07-22 "Daily Phone-Contact-Book Sync + Contact-Identity Deduplication."

**Related Modules:** `modules/contact_sync`, `modules/contact_roles`, `modules/wa_chat_frontend`, `modules/drafts`.

**Related Documents:** `docs/CANONICAL_CLIENT_DOMAIN.md` (full discovery + model), `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` (phased fix), `docs/PRODUCTION_CERTIFICATION_v1.0.md`, `docs/PHASE2_ENTRY_GATE.md`, `../06_developer_system/contact_sync.md`.

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

**Business Purpose:** Resolve "who is this WhatsApp sender" into a role (family/admin/accountant/employee/escort/security_guard/internal_coordinator/candidate/unknown) and, where applicable, a specific employee/client record — before any workflow acts on the message.

**Canonical Workflow:** Inbound message → `modules/identity_brain` resolves sender phone → role + confidence → `modules/message_router` Tier 1 (family) / Tier 2 (escort-client) / Tier 3 (admin) consume that resolution before any domain-specific tier runs. `shared/identity_map.py::resolve_employee()` is the canonical resolver specifically for "this phone number → which `wbom_employees` row," used by payment/validation code that must not conflate an employee's contact number with their payout number (see Employee section's Naming Rule for the full phone-naming picture this resolver exists to guard against).

**Updated 2026-07-21 — escort/guard split + internal_coordinator:** a `wbom_employees` row no longer resolves unconditionally to role `employee` — `identity_brain._designation_to_role()` maps `designation` to `escort` or `security_guard` when it matches, `employee` otherwise (covers office/"Staff" designations). `wbom_escort_programs` roster matches resolve directly to `escort` (previously `employee`). New role `internal_coordinator` is seed-row-only (`fazle_contact_roles`, checked before `wbom_employees` in resolution order) — used for people who coordinate dispatch/payroll/paperwork but aren't front-line escort/guard staff (e.g. `+8801817201596`). A read-only former/on-leave lifecycle heuristic (`status='Inactive'` → former; `Active` + no recent `wbom_attendance` row → on-leave; no new columns) is attached as `identity_lifecycle` — context for Route B's Ollama prompt, not a routing input (active/former/on-leave escort/guard all route identically). See `../05_workflows/whatsapp_reply_workflow.md` §7.

**Canonical Entity Names:** "Role" (message_router's sender classification: `family`, `admin`, `accountant`, `employee`, `candidate`/`new_lead`/`unknown`) is resolved once per message and consumed by every downstream tier — not to be confused with RBAC's "Role" concept (Roles section above), which governs admin *command* permissions, not sender *identity*. These are two different, same-named concepts in two different subsystems — another instance of the same-name-different-meaning pattern this document exists to surface.

**Canonical Database Tables:** Identity resolution reads `wbom_employees`, `fazle_contact_roles`, `wbom_contacts` (see Client section — the ambiguity there directly affects identity resolution's completeness for non-employee senders).

**Canonical APIs:** `modules/identity_brain` (sender → role resolution), `shared/identity_map.py::resolve_employee()`, `validate_payment_identity()`, `sender_to_employee()`.

**Known Legacy Variants:** The "Role" naming collision with RBAC (above) is the main structural finding here.

**Owner Changeable Rules:** Confidence thresholds for identity resolution are policy.

**Structural Rules:** Never use "role" ambiguously in new code or documentation — always specify "sender role" (identity/message_router) vs. "permission role" (RBAC) when both could apply.

**Validation Rules:** Not verified beyond the resolver functions named above.

**Related Modules:** `modules/identity_brain`, `modules/message_router`, `modules/route_b`, `shared/identity_map.py`, `modules/rbac`.

**Related Documents:** `docs/DEVELOPER_GUIDE.md` §H, `../05_workflows/whatsapp_reply_workflow.md`.

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

### 2026-07-17 — Finance-First Link/Sync Integrity: unknown-sender auto-create disabled, trusted-path search gap fixed
**Type:** Structural fix (2) + conflict corrected (this document was stale in 2 places).
**Section(s) affected:** Message Parser (Message Router), Employee.
**What changed:** A dedicated audit (Session 20 Step 3, full report in `management_decisions.md` S20-08 through S20-11) traced every writer to `wbom_employees`/`fpe_employees`/`fpe_cash_transactions`/`fpe_employee_ledger` and found: (1) `message_router::_seed_unknown_employee_identity()` auto-created a `wbom_employees` row (and indirectly an `fpe_employees` row) for **any** sender identity_brain classified as `"unknown"` whose message merely looked like an attendance/escort/advance-request — by construction, every such sender had already failed every trust check the system has. Disabled (Owner directive), not deleted. This document's Message Parser section previously described `_OPERATIONAL_AUTO_CREATE_INTENTS` as a live policy lever without qualification — corrected in place, not left silently stale. (2) The trusted admin→accountant finance path (`payment_ingest::_match_or_create_instruction_employee()`) collapsed two distinct phone values into one search key before ever querying, so an explicit ID-mobile miss never fell back to a payout-number retry — fixed with a 2-line addition. (3) This document's Employee section named only one of two real `wbom_employees`↔`fpe_employees` bridges (`wbom_fpe_sync.py`, a historical/backfill adapter) — added the second, live, real-time bridge (`fpe_bridge.py`) that the fix in (2) actually touched, so a future reader isn't misled into thinking only the backfill path exists. (4) Added a new read-only report, `modules/reports::_b_unresolved_fpe_employees()`, distinguishing genuinely-unlinked employee rows from rows that are already correctly linked but carry a stale `resolution_status` label — confirmed live against production (172 + 81 = 253 unlinked, 153 mislabeled-but-linked).
**Why:** Owner-initiated audit into unlinked/out-of-sync employee row creation breaking finance workflows. Both code fixes were narrowly scoped (Owner explicitly excluded: `wbom_*`→`fpe_*` renames, the `wbom_employees`/`fpe_employees` merge, `employee_id_phone` uniqueness changes, escort dual-write concurrency, `accounting.py` semantics, `admin_transactions` bypass cleanup — all remain open, undecided).
**Owner approval:** Yes, each of the 3 implementation phases (disable auto-create; add search fallback; add report) was shown to the Owner as exact current code + exact proposed diff and explicitly approved before any change, per this session's standing process. Full authorization trail: `management_decisions.md` Session 20 Step 3, S20-08 through S20-11; commits `47018f7`, `38dbedd`, `b0c0057`.

### 2026-07-21 — WhatsApp Reply Workflow: Route A / Route B (identity-first routing)
**Type:** Structural fix + new architecture implemented + one open gap discovered (not yet resolved).
**Section(s) affected:** Message Parser (Message Router), Identity, Recruitment.
**What changed:** Replaced the content-based WhatsApp reply-routing gate (`_requires_legacy_workflow()` keyword/regex sniffing on message text) with identity-first routing. New roles `escort`, `security_guard`, `internal_coordinator` added to `modules/identity_brain` (designation-driven, no longer collapsed into generic `employee`). New `modules/route_b` (Ollama-only, no Groq/GitHub fallback) handles escort/security_guard/candidate/unknown senders, wired into `modules/message_router` as a branch positioned after every existing operational safety tier so none is preempted. `modules/social_auto_reply`'s content/code is unmodified — only its WhatsApp entry points (`bridge_poller`, `/webhook/meta`, `/webhook/mcp1`/`mcp2`) were disconnected; it remains live for Facebook Messenger/Page comments. Phone normalization: 2 unauthorized reimplementations (`identity_brain`, `payment_ingest`) fixed to delegate to the documented canonical roots instead of re-implementing digit parsing. Office/deposit canonical text unified across `fazle_knowledge_base` and every code-level fallback. `modules/rag` gap closed: `knowledge_base/**/*.md` DB-mirror rows were reachable by candidate/employee-facing RAG search (no role restriction), now admin/developer-only, matching the raw-file loader's existing intent. Company rebrand applied to customer-facing output; recognition keywords parsing *incoming* content (`escort._AL_AQSA_RE`, OCR/matching lists) augmented, not replaced. Full detail: `management_decisions.md` 2026-07-21 entry, `../05_workflows/whatsapp_reply_workflow.md`.
**Why:** The prior content-based gate caused two confirmed incidents: a WhatsApp number auto-classified as generic "employee" from one misread OCR string then silently answered by the wrong engine; and casual messages from escorts/guards/unknown senders answered with unrelated `social_auto_reply` keyword-template replies. Root cause was routing on message content instead of sender identity.
**Owner approval:** Yes — implementation proceeded through an explicit multi-step approval sequence (3 drafts for text/DB-boundary approval, then implementation authorization, then commit authorization, then rebrand + remaining-KB-rows authorization, then push authorization), each step confirmed before proceeding, per `management_decisions.md`. Deployed live via `scripts/restart_gate.sh --restart` (0 warnings) on Owner instruction, verified healthy post-restart.
**Known open gap (discovered during post-implementation verification, not yet fixed):** `modules/recruitment_flow::recruitment_eligibility()`'s `OPERATIONAL_INTENTS` gate has the same architectural flaw class (blunt intent-keyword rejection ahead of role-aware nuance) — see Recruitment section above for detail. Flagged for Owner review; no code changed for this gap under this entry.

### 2026-07-22 — Daily contact sync + `fazle_contact_roles` phone-format duplicate cleanup
**Type:** Structural fix (2) + new mechanism implemented.
**Section(s) affected:** Client.
**What changed:** Implemented the Owner's daily phone-contact-book ↔ `wbom_contacts` sync
policy (soft-delete on first miss, hard-delete after N consecutive misses, phone-book
name canonical over WhatsApp profile name) — found and fixed a bridge2 SQLite-path bug
along the way (silently reading zero contacts), and found the same wrong
name-priority bug in `modules/route_b` (built one day earlier in this same project) and
fixed it there too. On Owner follow-up: found and removed 8 duplicate rows in
`fazle_contact_roles` caused by inconsistent phone-string format bypassing its own
unique constraint; normalized the write path so the constraint is now actually effective.
Full record: `management_decisions.md` 2026-07-22, `../06_developer_system/contact_sync.md`.
**Why:** Contact data (name, active/deleted status, identity-routing role) needs to stay
accurate and free of format-based duplication, since it feeds both display and
identity-based routing.
**Owner approval:** Yes — implemented per explicit instruction, with the duplicate-cleanup
scope (found via investigation, not originally specified) confirmed safe before deletion
(all 8 duplicate groups had identical role/priority/confidence, zero routing-outcome
risk) and audit-backed before any row was removed.

### 2026-07-22 — Route A: No Auto Reply, contact-name-based gate
**Type:** New mechanism + bug fix.
**Section(s) affected:** Message Parser (Message Router).
**What changed:** Within the Route B population (escort/security_guard/candidate/unknown),
a sender whose saved contact name contains Client/Escort/Office/Operation/Accountant/
Security Guard/Al-Aqsa/Al-Manaratayn/আল-আকসা/আল-মানারাতাইন anywhere (substring match, not
prefix-only) now gets no AI/generic/recruitment reply at all, enforced at two points in
`modules/message_router/__init__.py`: `_should_silent_skip()` (early gate) and a new
backstop immediately before Route B's `handle_route_b_message()` call. Escort-named
contacts get one exception — a genuinely order-shaped message still produces a structured
admin-review draft via the existing `handle_escort_client_message()` pipeline, with no
reply to the sender either way. Found and fixed a real bug while implementing:
`_should_silent_skip()`'s name-token check had an either/or logic error (`list(...) is
list(...)` is always `False`) that silently dropped the static keyword list entirely
whenever `.env`'s `DRAFT_ALWAYS_NAMES`/`DRAFT_ALWAYS_ROLES` had any custom tokens
configured — true in production — so generic contacts like "Md Karim Escort" were never
actually being matched. Fixed to a real union of both lists. Full record:
`management_decisions.md` 2026-07-22, `../05_workflows/whatsapp_reply_workflow.md` §14.
**Why:** Known, name-tagged business contacts (staff, clients, accountant, operations)
should never receive a risky AI-generated reply, even from the new Ollama-based Route B —
only structured extraction for a well-defined case (a genuine escort order) plus human
review, or silence.
**Owner approval:** Yes — implemented and committed per explicit instruction.

### 2026-07-22 — Route B Conversation Handoff (staged rollout, built + tested, NOT activated)
**Type:** New mechanism, feature-flagged off by default.
**Section(s) affected:** Message Parser (Message Router), Recruitment, Employee.
**What changed:** New `modules/route_b_handoff` lets Route B (Ollama-only chat for
escort/security_guard/candidate/unknown senders) hand a conversation off to an existing
canonical workflow once identity/intent is clear enough, without Route B gaining write
access to any business table. `ROUTE_B_ROLES` unchanged (`employee` deliberately NOT
added — Tier 10 stays the sole employee path). Stage 1: active-recruitment-session
continuation and a deterministic-first (Ollama-hint-corroborated) recruitment handoff,
reusing `recruitment_eligibility()`/`route_recruitment_message()`/`intake_message()`
unmodified. Stage 2 (built, tested, same flag): employee/former-employee claims always
resolve to an admin-review draft, never live Tier 10 access, because a phone reaching
Route B by construction already failed identity_brain's `wbom_employees` match. Stage 3
(built, tested, separate flag `ROUTE_B_ESCORT_HANDOFF_ENABLED`, explicitly not activated
even after Stage 1/2 review): multi-message escort-order completeness check, reusing the
newly-extracted `modules.escort.is_order_complete()`. New audit table
`fazle_route_b_handoffs` (migration 057, not yet applied to production). Full record:
`management_decisions.md` 2026-07-22, `../05_workflows/whatsapp_reply_workflow.md` §15.
**Why:** A prior-session proposal suggested adding generic `employee` to Route B for
better conversational handling; investigation found this would have been a regression
(Tier 10 already correctly handles every registered phone). This narrower, additive
capability captures the genuinely missing piece — letting Route B's richer multi-turn
understanding still result in a real canonical-workflow outcome — without that risk.
**Owner approval:** Yes — staged approval: build/test Stage 1+2, leave inactive pending
report review; Stage 3 explicitly build-only, not to be activated even after that review.


