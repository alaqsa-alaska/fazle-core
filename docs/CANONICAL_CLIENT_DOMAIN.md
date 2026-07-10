# Canonical Client Domain — Fazle Core

**Date:** 2026-07-09
**Status:** Architecture only. No database changes, no code changes, nothing implemented in this document.
**Precedence:** This document is a detail-layer extension of `knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md` §Client and §Client Billing — read those first for the summary; this is the full evidence and design behind them. Same authority rule applies: live code/database > `CANONICAL_BUSINESS_RULES.md` > this document.
**Companion document:** `docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md` (Task 4's phased roadmap — kept separate so architecture and execution planning don't get tangled together).

---

## Headline finding, before the detail

The previous certification pass (`docs/PRODUCTION_CERTIFICATION_v1.0.md`) recorded the Client domain as "no canonical client entity... genuinely open." Targeted production evidence gathered for this pass **narrows that gap significantly, but does not close it**: a real foundation already exists in the live database — it was simply never fully adopted or connected end-to-end. Specifically:

- **`wbom_contacts`** (2,086 rows) is the de facto canonical contact/client table — `modules/contact_sync` explicitly merges all 3 WhatsApp sources into it, by design, as "the central table."
- **`wbom_relation_types`** (a real, populated 5-row lookup table: `Client`, `Vendor`, `Partner`, `Employee`, `escort_client`) and **`wbom_business_types`** (5 rows: `Shipping Company`/`Import-Export Agent` = Maritime, `Construction Company`/`Factory`/`Bank` = Land Security) already exist as proper category infrastructure — `wbom_business_types` cleanly captures the fact that this business serves two distinct service lines (vessel escort vs. land-based guard security).
- **`wbom_billing_records`** already exists with a complete, correctly-FK'd schema (`program_id → wbom_escort_programs`, `contact_id → wbom_contacts`, `service_charge`, `other_charges`, `total_amount`, `payment_status`) — a ready billing skeleton. **It has zero rows.** Phase 2b's job is substantially to *activate* this dormant table, not invent one from scratch.
- **`wbom_escort_programs.contact_id → wbom_contacts.contact_id`** is a real, live FK — but populated on only **59 of 342 programs (17%)**. The link exists; almost nobody uses it yet.
- **`fazle_clients`** (id, name, phone, created_at — 4 columns, 0 rows) is a dead stub, superseded by `wbom_contacts`. Do not build on it.
- **Three separate, uncoordinated client-classification mechanisms exist simultaneously**, none of which is authoritative over the others: `wbom_contacts.relation` (free text, 2084/2086 rows = `'unknown'` — stale/abandoned), `wbom_contacts.relation_type_id → wbom_relation_types` (structured, but only 91 rows tagged `'Client'`, 1 tagged `'escort_client'`, 1988 NULL), and `fazle_contact_roles.role` (free text, no CHECK constraint, 47 total rows with a **different, richer, uncoordinated vocabulary**: `repeat_client`, `client_escort_buyer`, `vip_client`, `vendor`, `blocked`, etc. — none of which match `wbom_relation_types`'s single `Client` value).

**Conclusion: the foundation is better than "nothing exists," but three parallel, non-overlapping vocabularies for "this contact is a client" is exactly the Business Knowledge Drift this whole initiative exists to fix.** Canonicalizing this (Task 2 below) is real, necessary, foundational work — not a rediscovery of Task 1's certification finding.

---

# Task 1 — Client Domain Discovery

For each concept named in the brief, here is what actually exists, with evidence.

| Concept | Purpose | Current Implementation | Current Tables | Current APIs | Current Modules | Relationships | Missing Relationships | Evidence |
|---|---|---|---|---|---|---|---|---|
| **Client** (generic) | The party who orders and pays for a service | Fragmented across 3 mechanisms (see Headline Finding) | `wbom_contacts` (+`relation_type_id`), `fazle_contact_roles`, dead `fazle_clients` | `modules/contact_roles/routes.py` (CRUD by phone), `modules/contact_sync` (merge) | `modules/contact_sync`, `modules/contact_roles`, `modules/wa_chat_frontend` | `wbom_escort_programs.contact_id` (sparse), `wbom_billing_records.contact_id` (unused) | No single canonical "is this a client" flag; no client→multiple-contacts model | pg queries, 2026-07-09 |
| **Escort Client** | A client specifically for vessel-escort service | Exists only as a `relation_type_id=5` value (`'escort_client'`, 1 row) and a `fazle_contact_roles.role='client_escort_buyer'` value (7 rows) — **two different spellings for what appears to be the same concept** | `wbom_contacts`, `fazle_contact_roles` | Same as Client | Same as Client | `wbom_escort_programs` (Maritime service line, per `wbom_business_types`) | Not distinguished structurally from a generic Client — only by an inconsistent label | pg queries |
| **Corporate Client** | A company (vs. an individual) placing orders | Partially captured by `wbom_contacts.company_name` (free text) | `wbom_contacts` | — | — | None found to a dedicated company/organization entity | No "Client Branch" or multi-site company model | `db/migrations/029...sql` |
| **Customer** | Generic term, not used as a distinct entity | Not found as a distinct name anywhere in code | — | — | — | — | N/A — treat as a synonym for Client, not a separate concept | grep, no hits |
| **Importer / Consignee** | Maritime-industry terms for who receives cargo (context for *why* an escort is ordered) | Not found as a column or entity anywhere; `wbom_business_types` has `'Import/Export Agent'` as a business-type value, which is adjacent but not the same as "consignee on this specific shipment" | `wbom_business_types` (adjacent only) | — | — | Conceptually relates to `wbom_escort_programs.destination`/`mother_vessel` | Not modeled — if the business needs to track consignee-per-shipment, it doesn't exist today | grep, no hits beyond business_type value |
| **Vendor** | A supplier of services/goods to the business (opposite direction from Client) | `wbom_relation_types.relation_name='Vendor'` (a real lookup value) and `fazle_contact_roles.role='vendor'` (6 rows) — same double-vocabulary problem as Client | `wbom_contacts`, `fazle_contact_roles` | Same as Client | Same as Client | None to Payroll/AP (no accounts-payable concept exists — matches the Expense section's "not yet built" finding in `CANONICAL_BUSINESS_RULES.md`) | No vendor invoice/payment tracking exists | pg queries |
| **Contact** | The umbrella WhatsApp-identity concept underneath Client/Vendor/Employee/Family | `wbom_contacts` — this is the actual canonical identity table; Client is a *role* a contact can hold, not a separate table | `wbom_contacts` | `modules/contact_sync` (merge-on-sync), `modules/contact_roles/routes.py` (role CRUD) | `modules/contact_sync`, `modules/contact_roles` | Source-of-truth for `relation_type_id`; joined by `wbom_escort_programs.contact_id`, `wbom_billing_records.contact_id` | — | `modules/contact_sync/__init__.py` header comment: "Merges contacts from all three WhatsApp sources into the central wbom_contacts table" |
| **Company / Organization** | A business entity, potentially with multiple contacts/branches | Not modeled as its own table — `wbom_contacts.company_name` is a free-text field on the *contact*, not a separate company row; a company with 3 contact people today would be 3 unrelated `wbom_contacts` rows with the same `company_name` string, no FK linking them | `wbom_contacts.company_name` (weak) | — | — | None — no "these 3 contacts belong to the same company" relationship exists | pg schema |
| **Buyer / Service Recipient** | Whoever consumes the delivered service | Same as "Client" — no separate concept | — | — | — | — | — | — |
| **Billing Party / Paying Party** | Who actually gets invoiced/pays (may differ from the service recipient in some businesses, e.g. an agent orders on behalf of a shipowner) | Not modeled — `wbom_billing_records.contact_id` assumes the billing party and the escort's ordering contact are the same; no separate "bill-to" concept exists | `wbom_billing_records` (implicitly assumes 1) | — | — | — | This distinction (who ordered vs. who pays) is not captured anywhere — a real gap if it turns out to matter for this business (**Owner input needed** — not assumed here) | pg schema |

**Note on scope discipline:** per the task's explicit instruction, this table stops at discovery — it does not recommend which mechanism should win. That's Task 2.

---

# Task 2 — Canonical Client Model (Architecture Only)

No database changes are made by this section. This defines the target model Task 4's phased plan will implement incrementally.

## Client

**Definition:** A `wbom_contacts` row with `relation_type_id` pointing to a "client-family" relation type. **Canonical table: `wbom_contacts`** (not `fazle_clients` — deprecate/archive that stub; not a new table — the existing one already has 2,086 real rows and a working sync pipeline).

**Design decision (architecture-level, not yet implemented):** Collapse the three current client-signal mechanisms into one:
1. `wbom_contacts.relation_type_id` (FK to `wbom_relation_types`) becomes the **single source of truth** for "what kind of relationship does this contact have with the business."
2. `wbom_relation_types` gets its existing 5 rows corrected for consistency (`escort_client` → `Escort Client`, matching the Title Case convention of the other 4) and, if the business needs the finer distinctions currently living only in `fazle_contact_roles` (`repeat_client`, `vip_client`, `client_escort_buyer`, `blocked`), those become either additional `wbom_relation_types` rows or a new `client_category`/`client_tier` column on `wbom_contacts` (Client Category, below) — **this is an Owner-input decision on how finely to categorize clients, not an engineering unilateral call.**
3. `wbom_contacts.relation` (the stale free-text column, 2084/2086 = `'unknown'`) is marked **Deprecated** — do not read it for new logic; it predates `relation_type_id` and was never fully migrated away from.
4. `fazle_contact_roles` is marked **Legacy/Needs Merge** — its 47 rows' role assignments should be reconciled into `wbom_contacts.relation_type_id` (+ a category/tier field if the Owner wants that granularity preserved) rather than continuing as a second, parallel table.

**Owner Decision (2026-07-09) — implemented in `db/migrations/031_client_domain_reconciliation.sql`:**
- `supervisor`, `accountant`, and `blocked` are **not** canonical client relation types; no such `wbom_relation_types` rows exist or will be created.
- `supervisor`/`accountant` map onto the existing `Employee` relation type **only** where independently verified against `wbom_employees.employee_mobile` — every real (non-smoke-test) `supervisor`/`accountant`-tagged contact in production (6 distinct people) satisfied this check; the migration's guard is a live JOIN, not a hardcoded list, so any future unverified case is left untouched by construction, not force-mapped.
- `blocked` remains a moderation/status flag (`wbom_contacts.is_active = false`), never a relationship type.
- **`.env` authority rule:** phone numbers declared in `.env` (`ADMIN_NUMBERS`, `ACCOUNTANT_PHONE`, `FPE_CASH_AUTHORIZED_PHONES`, `FPE_INCOME_AUTHORIZED_PHONES`) are the mandatory, authoritative source of truth for functional role/authorization. `wbom_contacts`, `wbom_employees`, and cash-transaction-derived signals are supporting/enrichment sources only and must never silently override an `.env`-declared identity. Verified before applying the migration: all 22 distinct `.env`-listed numbers were cross-checked against `wbom_contacts`, `fazle_contact_roles`, and `wbom_employees` — every number this migration's UPDATEs touch resolves to a classification that agrees with its `.env`-declared role (see the migration file's header for the full verification).

## Client Contact

**Definition:** A specific person at a client company (e.g., the shipping agent's office manager vs. the vessel's captain) — **does not exist today.** Currently, one `wbom_contacts` row conflates "the company" and "the person," because there is no company/branch entity for multiple people to attach to.

**Architecture:** Introduce this only if the business actually has multi-person client accounts in practice (**Owner input needed** — the current data shows `wbom_contacts.company_name` as a loose free-text field, which is weak evidence multiple people per company already happens, but not proof). If needed, this becomes a `client_contacts` table with a FK to a new `Client Branch`/company grouping, `wbom_contacts` becoming the identity layer underneath it.

## Client Branch

**Definition:** A distinct site/location for a client with multiple offices or vessel-management branches — **does not exist today.** Same open question as Client Contact: is this needed given current data, or is one `wbom_contacts` row per company sufficient? **Owner input needed.**

## Client Billing Profile

**Definition:** How a client is billed — payment terms, preferred payment method, credit limit, tax ID. **Does not exist today** — `wbom_billing_records` captures individual bills, not a standing profile per client. This is genuinely new, needed for Phase 2b, and should attach to the canonical `wbom_contacts` row via `contact_id` once billing goes live.

**Critical re-audit finding (2026-07-09), FULLY RESOLVED by Owner decision same day:** deeper than the billing-profile gap above — **no client-facing rate/price field existed anywhere in this schema**, confirmed via three independent checks: `wbom_escort_programs`'s full column list (only cost-side fields: `total_payment`, `conveyance`, `food_bill`), `escort_calculation_config` (also cost-side — the same guard-pay `shift_rate` already documented in the Payroll section), and a repo-wide search for `%rate%`/`%price%`/`%charge%` columns (zero results). **Owner resolution, fully specified:** a new, separate `wbom_client_billing_profiles` table (Client Name, Contact Person/Chairman/Managing Member with mobiles, General/24-Hour/12-Hour/Monthly rates, deployed-employee-wage estimates, VAT/Tax/Other Charges as flat amounts), managed via a new frontend Client Settings UI nested under Payroll Page → Admin Tab. Explicitly **not** an extension of `wbom_contacts` (kept separate) but **does carry a required, nullable-valued `contact_id` FK to it** — no disconnected client system. Explicitly **not** connected to the actual payroll formula (the wage fields are for profit *estimation* only). Billing type (General/24H/12H/Monthly) is admin-selected at bill-confirmation time, not inferred. VAT/Tax/Other Charges are internal profit-estimation deductions only this phase, not added to the client's invoice. **Status, updated 2026-07-09: fully implemented and live.** `db/migrations/034_client_billing_profiles.sql` + `db/migrations/035_client_billing_wage_general.sql` (adds `employee_wage_general` — the original field list paired `employee_wage_24h/12h/monthly` with their rates but had no matching field for `general_rate`; added once flagged, since bill generation for General duty works without it but profit calculation would be wrong/incomplete without it) both applied to production. `modules/client_billing/` (CRUD + routes) implemented, mounted, restarted, and verified live (routes confirmed in the running OpenAPI schema, full Add/Edit/soft-Delete cycle tested end-to-end, table verified structurally correct, test data cleaned up). `payroll.html` Client Settings section built and confirmed rendering. 12 unit tests passing; full regression unchanged (29 failed/0 errors, identical to the pre-existing baseline). Full plans: `docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md` (architecture) and `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` (backend/frontend spec).

## Client Service Agreement

**Definition:** A standing contract/rate agreement with a client (e.g., a fixed day-rate for all future escort orders from this shipping agent). **Does not exist today.** `wbom_business_types.default_templates` (jsonb) hints at a per-business-type template concept but is not a per-client agreement. Genuinely new for Phase 2b, if the business actually negotiates standing rates (**Owner input needed** — vs. per-order pricing, which is what `wbom_escort_programs`'s existing pay-formula-adjacent fields suggest happens today).

## Client Service Location

**Definition:** Where the service is delivered — for escort duty, this is effectively `wbom_escort_programs.destination`/`mother_vessel`/`lighter_vessel` today, tied to the order, not to the client as a standing attribute. No change recommended unless the business wants to pre-register a client's common ports/routes for faster order intake.

## Client Tier, Client Status, and `is_active` — four separated dimensions (Owner Decision, 2026-07-09, final)

The Owner resolved the overlap flagged in the previous pass with a precise 4-dimension model. **This supersedes all earlier framing in this document** (the single combined `client_tier`/`client_status` field described in the prior draft was provisional; this is the final model):

```
relation_type   = IDENTITY            — Client, Vendor, Employee, Family, Escort Client
                  (= Phase 2A's existing wbom_contacts.relation_type_id -> wbom_relation_types;
                   no change)

client_tier     = BUSINESS VALUE      — Standard, Repeat, VIP, Strategic
                  (NEW field; not yet implemented)

client_status   = BUSINESS LIFECYCLE  — Active, Inactive, Suspended, Closed
                  (NEW, SEPARATE field from client_tier -- these are two different
                   dimensions, not one combined field as earlier drafted)

is_active       = SYSTEM INTERACTION PERMISSION — boolean
                  (= Phase 2A's existing wbom_contacts.is_active; no change, no new
                   field needed)
```

**"Blocked" is exclusively represented by `is_active = false`.** It is explicitly **not** a value in `client_tier` or `client_status` — do not introduce it there. This is a hard rule, not a preference: the four dimensions are independent, and a real contact can validly be `relation_type=Client, client_tier=VIP, client_status=Active, is_active=false` simultaneously — meaning "a still-active VIP customer whose communication/processing has been administratively blocked." Do not let any future code collapse these back into one field; that collapse is exactly the drift this whole initiative exists to prevent, and it already happened once with `fazle_contact_roles.role` (Phase 2A's evidence: that single free-text column tried to carry identity, tier, *and* moderation status all at once, which is why it had to be reconciled).

**Not yet implemented** — decision recorded, no migration written. When implemented: `client_tier` and `client_status` are each most likely a small new lookup table (mirroring `wbom_relation_types`'s pattern) or a constrained column with a CHECK constraint (mirroring `escort_roster_change_requests`'s pattern, the one status column in this codebase that already does this correctly) — either is consistent with this model; which one is an implementation detail, not a policy question.

**Superseded finding, kept for context only:** `interest_level`/`keep_tier` (existing `wbom_contacts` columns) looked like tier candidates at first glance but were found (`docs/PHASE2B_READINESS_REPORT.md` §4) to be an unrelated, unmaintained contact-list retention/cleanup artifact — not reusable for `client_tier`.

## Client Category

**Definition:** What kind of business/client this is. **Already exists and is well-designed, just unused**: `wbom_business_types` (`Shipping Company`, `Import/Export Agent` → Maritime service line; `Construction Company`, `Factory`, `Bank` → Land Security service line). Recommend activating this existing table (backfilling `wbom_contacts.business_type_id`, currently 0% populated) rather than designing a new categorization scheme.

## Client Identity

**Definition:** How the system recognizes a given client across channels (WhatsApp number, company name, etc.). **Already exists**: `wbom_contacts.whatsapp_number` (canonical phone key, normalized 880XXXXXXXXXX per `modules/contact_sync`'s documented convention) is the identity anchor. No change needed structurally — this part already works.

## Client Communication

**Definition:** How/what the business communicates to a client (reminders, invoices, service updates). **Partially exists**: `wbom_relation_types.greeting_template` (per-relation-type greeting text) is a real, if narrow, communication-customization hook. `modules/outbound` (Notifications, per `CANONICAL_BUSINESS_RULES.md`) is the actual send mechanism, generic across all contact types today — no client-specific communication workflow exists beyond the greeting template.

## Client Payment Profile

**Definition:** How a client pays (bKash/Nagad/bank/cash) and their payment history. **Does not exist as a client-level concept today** — `wbom_billing_records.payment_status`/`payment_date` exist per-bill, but there's no standing "this client typically pays via bank transfer within 30 days" profile. New for Phase 2b, folds into Client Billing Profile above rather than being a separate table.

## Client Relationship to Escort Programs

**Already exists and is the strongest real linkage found**: `wbom_escort_programs.contact_id → wbom_contacts.contact_id`. **Recommendation: increase adoption (currently 17% populated) rather than redesign** — the FK is correct, it's simply under-used by the intake workflow today.

## Client Relationship to Payroll

**None exists, and none should be added directly.** Payroll pays employees; it has no direct client relationship. The correct connective tissue is: Client → Escort Program → Employee assignment (`escort_employee_id`) → Payroll. Do not create a shortcut FK from Client to Payroll — that would blur Article I's transaction boundary and duplicate a path that already exists via the program.

## Client Relationship to Future Billing

**This is the whole point of `wbom_billing_records`, already correctly designed**: `wbom_billing_records.contact_id → wbom_contacts.contact_id` and `wbom_billing_records.program_id → wbom_escort_programs.program_id`. Task 4's Phase 2B work is substantially about writing the application code that populates this already-correct schema, not designing a new one.

---

# Task 3 — Database Relationship Map

## `wbom_contacts` — **Canonical**

- **Purpose:** The single merged contact/identity table for every WhatsApp-known person or company (client, vendor, employee-family, etc.).
- **Primary Key:** `contact_id` (integer)
- **Foreign Keys (outbound):** `relation_type_id → wbom_relation_types.relation_type_id`, `business_type_id → wbom_business_types.business_type_id`
- **Referenced By (inbound):** `wbom_escort_programs.contact_id`, `wbom_billing_records.contact_id`
- **Reads:** `modules/message_router` (Tier 1/2 role resolution), `modules/ai_readonly_tools` (`ai_read_contacts`), `modules/identity_brain`
- **Writes:** `modules/contact_sync` (the merge-on-sync pipeline; explicitly documented as "the central table")
- **Owner Module:** `modules/contact_sync`
- **Canonical?** Yes.
- **Legacy?** No — but its `relation` free-text column (as distinct from `relation_type_id`) is legacy and should stop being read.
- **Needs Migration?** No new table needed; needs a backfill migration/campaign to populate `relation_type_id` and `business_type_id` on existing rows (currently ~95%/100% NULL respectively) — a data-quality task, not a schema task.
- **Needs Merge?** Yes — `fazle_contact_roles`'s 47 rows should be reconciled into this table's `relation_type_id` (see below).
- **Needs Deprecation?** The `relation` column, once `relation_type_id` reaches acceptable coverage.

## `wbom_relation_types` — **Canonical (lookup)**

- **Purpose:** Enumerates the kinds of relationship a contact can have with the business.
- **Primary Key:** `relation_type_id`
- **Foreign Keys:** none (leaf lookup table)
- **Referenced By:** `wbom_contacts.relation_type_id`
- **Reads:** Anywhere `wbom_contacts` is joined for a human-readable relationship label.
- **Writes:** Manual/admin only (5 rows, static reference data).
- **Owner Module:** None dedicated — treat as shared reference data, likely owned by `modules/contact_roles` or `modules/contact_sync` going forward.
- **Canonical?** Yes.
- **Legacy?** No.
- **Needs Migration?** A data-fix migration to rename `'escort_client'` → `'Escort Client'` for casing consistency with the other 4 rows (`Client`, `Vendor`, `Partner`, `Employee`) — trivial, safe, additive-only (UPDATE, not structural).
- **Needs Merge?** Potentially absorbs `fazle_contact_roles`'s finer-grained values (`vip_client`, `repeat_client`, etc.) as new rows, if the Owner wants that granularity preserved structurally rather than as free text.
- **Needs Deprecation?** No.

## `wbom_business_types` — **Canonical (lookup), currently unused**

- **Purpose:** Classifies a contact's business type and, implicitly, which service line they belong to (Maritime vs. Land Security).
- **Primary Key:** `business_type_id`
- **Foreign Keys:** none
- **Referenced By:** `wbom_contacts.business_type_id` (0% populated in production today)
- **Reads/Writes:** None found — this table and its FK are wired correctly but not yet used by any live workflow.
- **Owner Module:** None yet — candidate for `modules/contact_roles` or a new `modules/client` module (see Phase 4).
- **Canonical?** Yes, as designed — just dormant.
- **Legacy?** No.
- **Needs Migration?** No.
- **Needs Merge?** No.
- **Needs Deprecation?** No — needs *activation* (backfill + a UI/intake step to set it), which is a Phase 2A/2B implementation task, not a schema change.

## `fazle_contact_roles` — **Legacy / Needs Merge**

- **Purpose:** A phone-keyed, free-text role tag with its own CRUD API — appears to have been built as a lighter-weight alternative to `wbom_contacts.relation_type_id`, before or in parallel with it.
- **Primary Key:** `id` (uuid)
- **Foreign Keys:** none (keyed by raw `phone`, not `contact_id` — no FK to `wbom_contacts` at all, meaning these two tables can silently disagree about the same person with no referential integrity between them)
- **Referenced By:** none found
- **Reads/Writes:** `modules/contact_roles/routes.py` (CRUD), `modules/drafts/routes.py`
- **Owner Module:** `modules/contact_roles`
- **Canonical?** No.
- **Legacy?** Yes — recommend reconciling its 47 rows into `wbom_contacts.relation_type_id` (+ a category/tier field if granularity is wanted) and retiring new writes to this table.
- **Needs Migration?** Yes — a one-time reconciliation migration (map its role values onto `wbom_relation_types`, decide what happens to the finer-grained values not currently represented there).
- **Needs Merge?** Yes (into `wbom_contacts`).
- **Needs Deprecation?** Yes, after reconciliation.

## `fazle_clients` — **Legacy / Dead**

- **Purpose:** Unknown/superseded — 4 columns (`id`, `name`, `phone`, `created_at`), 0 rows, no code found reading or writing it in this pass.
- **Primary Key:** `id`
- **Foreign Keys:** none
- **Referenced By:** none found
- **Reads/Writes:** none found
- **Owner Module:** none
- **Canonical?** No.
- **Legacy?** Yes.
- **Needs Migration?** No.
- **Needs Merge?** No — nothing to merge, it's empty.
- **Needs Deprecation?** Yes — candidate for archival/drop once confirmed truly unused (confirm via production log/query-audit before dropping; this document recommends, does not execute, any drop).

## `wbom_billing_records` — **Canonical (Phase 2b target), currently unused**

- **Purpose:** Per-escort-program billing record — already correctly modeled.
- **Primary Key:** `bill_id`
- **Foreign Keys:** `program_id → wbom_escort_programs.program_id`, `contact_id → wbom_contacts.contact_id`
- **Referenced By:** none (leaf table)
- **Reads/Writes:** None found — 0 rows, no code path writes to it in this pass.
- **Owner Module:** None yet — the natural home for Phase 2b's billing logic.
- **Canonical?** Yes, as designed.
- **Legacy?** No.
- **Needs Migration?** Possibly additive columns once Client Billing Profile/Payment Profile are designed in detail (e.g., linking to a future invoice-numbering sequence) — not yet specified, flagged for Phase 2B.
- **Needs Merge?** No.
- **Needs Deprecation?** No — needs *activation*.

## `wbom_escort_programs` — **Canonical (already covered in `CANONICAL_BUSINESS_RULES.md` §Escort)**

- **Client-relevant columns:** `contact_id → wbom_contacts.contact_id` (17% populated).
- **Relationship to Employees:** `escort_employee_id → wbom_employees.employee_id`.
- **Relationship to Attendance:** indirect, via `escort_lifecycle`'s attendance-backfill on program release (see `CANONICAL_BUSINESS_RULES.md` §Attendance).
- **Relationship to Transactions:** indirect and currently broken — `fpe_cash_transactions.program_id` is NULL on all rows (see §Payroll finding) — so today, a program's client link does not flow through to which transaction paid for it.
- **Relationship to Drafts:** `fazle_payment_drafts.escort_program_id` (see §Draft).
- **Relationship to Reports:** `modules/reports::_b_escort_utilization` reads this table.
- **Relationship to Knowledge Base/AI:** `modules/ai_readonly_tools::get_escort_program_status` reads it read-only.

## Relationship summary (text ER)

```
wbom_relation_types (lookup) ──┐
                                 ├──< wbom_contacts >──┬──< wbom_escort_programs >──── wbom_employees
wbom_business_types (lookup) ──┘         │              │         │
                                          │              │         └──< fazle_payment_drafts (via escort_program_id)
                                          │              │
                                          │              └──< wbom_billing_records >── wbom_escort_programs (double FK, denormalized snapshot)
                                          │
                          fazle_contact_roles (unlinked — no FK, reconcile-and-retire)
                          fazle_clients (dead stub — no links, archive)
```
