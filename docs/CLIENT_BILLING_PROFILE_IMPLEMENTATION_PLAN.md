# Client Billing Profile — Implementation Plan (Phase 2B, Owner Decision 2026-07-09)

**Status:** Plan only. No code, no migration file, no production/database change in this pass, per explicit instruction ("Stop before coding unless Owner explicitly approves implementation").

**Owner decision this plan implements:** Client billing price/rate comes from a new, frontend-managed Client Settings system — **not** from escort payroll cost. This resolves the blocking finding in `docs/PHASE2B_READINESS_REPORT.md` ("no client-facing rate/price field exists anywhere in this schema"). Core rule, quoted exactly as given:

```
Client Rate            = company revenue side
Deployed Employee Wage  = company cost side
Profit = Client Bill − Employee Wage − VAT/Tax/Other Charges
```

---

## Re-audit findings that shape this plan

1. **`ESCORT_CLIENT_PHONES` is the exact `.env` variable the Owner means by "escort/client-related authorized numbers."** Confirmed via its own in-file comment: `# Phones allowed to submit escort client orders`. It already has a live, working management API — `modules/escort_roster/routes.py`'s `GET/POST/DELETE /escort-clients` — backed by `app.config.update_repo_env_value()` (rewrites `.env` in place, clears cached settings) and a `_normalize_client_phone()`/`_escort_client_phone_list()` helper pair. **This plan reuses these existing functions rather than re-implementing phone validation** — the new Client Settings feature does not add numbers to this list itself; it only validates that a submitted Contact Person Mobile is already a member of it, exactly as instructed ("if the number is not present... reject it").
2. `payroll.html` already has a hidden, role-gated **Admin tab** (`data-tab="admin"`, `admin-only-nav` class) with an existing "Admin Console" section (user add/list) — the natural, already-established place to add a "Client Settings" submenu, matching the Owner's instruction to nest it there.
3. `wbom_contacts` + `relation_type_id` (Phase 2A) remains the identity layer for "a WhatsApp-known client contact." The new client billing entity is a different, richer concept (a client *company*, with commercial terms) — per the Owner's explicit preference for a separate table, this plan does **not** extend `wbom_contacts`.

---

## Resolved Owner decisions (2026-07-09, final — supersedes the "Open design questions" this section used to contain)

1. **Duty-type-to-rate mapping:** admin selects billing type manually at bill-confirmation time — General / 24 Hour / 12 Hour / Monthly. **Not** inferred automatically from `wbom_escort_programs` in this phase.
2. **VAT/Tax/Other Charges treatment:** internal profit-estimation deductions only, this phase. **Not** added to the client's invoice total.
   ```
   Client Bill = selected client rate
   Profit      = Client Bill − Deployed Employee Wage − VAT − Tax − Other Charges
   ```
3. **Charge type:** flat amount only for VAT, Tax, and Other Charges in this phase. Percentage support is a documented **later enhancement**, not built now (no unused percentage columns added speculatively).
4. **Link to `wbom_contacts`:** **required** — `wbom_client_billing_profiles.contact_id` is a nullable FK to `wbom_contacts.contact_id`. Populated when resolvable; old/legacy records may remain nullable (no forced backfill). This is a hard requirement, not a recommendation — the Owner was explicit: "Do not create a disconnected client system." `.env` `ESCORT_CLIENT_PHONES` remains the mandatory authority for Contact Person Mobile; this feature reads it, never writes to it.

**Resolved by implementation (2026-07-09):** Hard delete vs. soft delete — implemented and verified as soft-delete (`is_active=false`), reversible.

**Still open, not yet decided by the Owner:**
- RBAC granularity — does every `admin`-tier user see wage/profit figures, or only `superadmin`? Currently implemented unrestricted (see "Remaining open items" below) — the simpler default in the absence of a decision, not a considered restriction.

---

## 1. Required DB changes — FINALIZED, ready for migration file (see `db/migrations/034_client_billing_profiles.sql`)

**New table: `wbom_client_billing_profiles`:**

```sql
CREATE TABLE wbom_client_billing_profiles (
    client_id                  SERIAL PRIMARY KEY,
    client_name                VARCHAR(200) NOT NULL,
    office_address              TEXT,
    contact_person_name        VARCHAR(120),
    contact_person_mobile      VARCHAR(20) NOT NULL,   -- validated against ESCORT_CLIENT_PHONES at write time (application layer — see §4), not a DB constraint
    chairman_name               VARCHAR(120),
    chairman_mobile             VARCHAR(20),
    managing_member_name        VARCHAR(120),
    managing_member_mobile      VARCHAR(20),
    general_rate                NUMERIC(12,2),
    rate_24h                    NUMERIC(12,2),
    rate_12h                    NUMERIC(12,2),
    rate_monthly                NUMERIC(12,2),
    employee_wage_24h           NUMERIC(12,2),          -- profit-ESTIMATION input only, see note below
    employee_wage_12h           NUMERIC(12,2),
    employee_wage_monthly       NUMERIC(12,2),
    vat_amount                  NUMERIC(12,2) DEFAULT 0, -- flat amount, Owner decision 2026-07-09 #3 (percentage deferred, see below)
    tax_amount                  NUMERIC(12,2) DEFAULT 0, -- flat amount
    other_charges                NUMERIC(12,2) DEFAULT 0, -- flat amount
    notes                       TEXT,
    is_active                   BOOLEAN NOT NULL DEFAULT true,
    contact_id                  INTEGER REFERENCES wbom_contacts(contact_id), -- nullable FK, Owner decision 2026-07-09 #4 (required field, nullable value)
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_client_billing_profiles_active ON wbom_client_billing_profiles(is_active);
CREATE INDEX idx_client_billing_profiles_contact_id ON wbom_client_billing_profiles(contact_id);
CREATE UNIQUE INDEX idx_client_billing_profiles_contact_mobile ON wbom_client_billing_profiles(contact_person_mobile);
```

**Future enhancement, documented not built:** percentage-based VAT/Tax (Owner decision #3 explicitly deferred this). When needed, the natural extension is adding `vat_is_percentage BOOLEAN DEFAULT false` / `tax_is_percentage BOOLEAN DEFAULT false` flags next to the existing flat-amount columns, so the application layer can interpret the stored number as either a flat charge or a percentage of `general_rate` depending on the flag — additive, no breaking change to this migration.

**Important, disclosed interpretation:** `employee_wage_24h/12h/monthly` are for the **profit-estimation calculation on this table only** — per the Owner's own "Do not touch payroll formula" rule, these fields must never feed into, override, or be read by `modules/payroll`, `modules/payment_workflow`, or any actual guard-payment calculation. They exist so the Client Settings screen can show an estimated profit margin per client; the real payroll formula question (`docs/PRODUCTION_CERTIFICATION_v1.0.md` Finance Status) remains completely separate and unresolved, as instructed.

**Profit formula (Owner decision #2), computed at read/display time, not stored as a column** (storing a derived value risks it silently going stale if a rate changes — compute it live from the source columns instead):
```
profit_estimate(duty_type) =
    CASE duty_type
        WHEN 'general' THEN general_rate
        WHEN '24h'     THEN rate_24h     - employee_wage_24h
        WHEN '12h'     THEN rate_12h     - employee_wage_12h
        WHEN 'monthly' THEN rate_monthly - employee_wage_monthly
    END - vat_amount - tax_amount - other_charges
```
(`general_rate` has no paired `employee_wage_general` field in the Owner's original field list — flagged: confirm whether General duty type should have its own wage field too, or whether "General" bills don't have a distinct deployed-employee-wage figure. Not assumed either way; the illustrative formula above leaves this gap visible rather than silently picking one.)

**Unique constraint on `contact_person_mobile`:** one client company per authorized phone number — prevents the same authorized number being silently assigned to two different client profiles, which would make inbound-message-to-client-program linking ambiguous.

**No backfill.** Per "do not auto-backfill old clients" — this table starts empty; existing escort programs and `wbom_contacts` rows are not retroactively linked.

---

## 2. Frontend changes

- **Location:** `app/static/payroll.html`, inside the existing hidden `data-tab="admin"` section, as a new subsection below or beside "Admin Console."
- **New submenu:** "Client Settings" with three actions — Add Client, Edit Client, Delete Client — each opening the same form (pre-filled for Edit) matching the existing Admin Console form pattern (labeled inputs, a submit button, a results/status area).
- **Form fields:** exactly the Owner's list (Client Name, Office Address, Contact Person Name/Mobile, Chairman Name/Mobile, Managing Member Name/Mobile, General/24h/12h/Monthly Rate, Deployed Employee Wage ×3, VAT, Tax, Other Charges, Notes, Active/Inactive toggle).
- **Contact Person Mobile field, client-side:** on blur/submit, call the new validation endpoint (§3) before allowing submit, showing an inline error matching this system's existing Bengali-first error-message convention if the number isn't authorized.
- **List view:** a new table (Client Name, Contact Person, Contact Mobile, Office Address, General/24h/12h/Monthly Rate, Status columns) with icon-based View/Edit/Remove actions, matching the existing dashboard table styling already used elsewhere (e.g. `drafts.html`, `escort-roster.html`).
- **Not built yet** — this section describes the plan; no HTML/JS has been written.

---

## 3. Backend API changes

New routes (exact module home TBD — likely `modules/client_billing/routes.py`, a new module, kept separate from `modules/billing` which owns invoice numbering only, matching this codebase's one-module-one-responsibility pattern):

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/admin/client-billing-profiles` | List (supports `?active=true` filter) |
| `GET` | `/admin/client-billing-profiles/{client_id}` | View one |
| `POST` | `/admin/client-billing-profiles` | Add Client |
| `PUT` | `/admin/client-billing-profiles/{client_id}` | Edit Client |
| `DELETE` | `/admin/client-billing-profiles/{client_id}` | Delete Client (soft-delete via `is_active=false`, not a hard `DELETE` — matching this system's general preference for reversible actions; a hard delete is a bigger decision than a plan should make unilaterally) |
| `GET` | `/admin/escort-clients/authorized-numbers` | New, thin read-only endpoint returning the current `ESCORT_CLIENT_PHONES` list (reuses `_escort_client_phone_list()`), so the frontend's Contact Person Mobile field can offer a dropdown/autocomplete restricted to valid values instead of free text |

All routes reuse the existing `_require_key`/RBAC dependency pattern already used by `modules/escort_roster/routes.py`'s `/escort-clients` endpoints, not a new auth mechanism.

---

## 4. Validation rules

1. **Contact Person Mobile must be a member of the current `ESCORT_CLIENT_PHONES` list**, checked server-side (never trust client-side validation alone) on both Add and Edit, using the existing `_normalize_client_phone()` + `_escort_client_phone_list()` from `modules/escort_roster/routes.py` (imported/reused, not duplicated). Reject with a clear error naming which number was rejected, matching the Owner's explicit rule.
2. **Client Name required, non-empty.**
3. **All rate/wage/charge fields must be non-negative** (reject negative numbers) — a data-entry safety check, not a business-rule invention.
4. **VAT/Tax/Other Charges are flat amounts this phase** (Owner decision #3) — no 0-100 range check needed; the existing non-negative check (#3 above) covers them.
5. **`contact_person_mobile` uniqueness** enforced at the DB level (§1) and re-checked at the API level with a clear error message before hitting the DB constraint.
6. **No auto-backfill, no default rate guessed for any field** — every rate/wage/charge field defaults to NULL/0, never a guessed value, per the Owner's explicit "do not guess rates" rule.

---

## 5. Security / RBAC rules

- **Role tier:** Recommend `admin` for Add/Edit/List/View, `superadmin` for Delete (even as a soft-delete) — matching the existing `COMMAND_ROLE` pattern where destructive/high-trust actions (`user_add`, `user_role`, `user_remove`) already sit at `superadmin` while list/view-type actions sit lower. Not yet added to `modules/rbac`'s `COMMAND_ROLE` dict — this is a WhatsApp-admin-command role map; the new routes are dashboard/API routes, so they'd use the existing `_require_key` dashboard-auth dependency instead, with role-gating added the same way `payroll.html`'s Admin tab is already hidden/shown based on the logged-in admin's role.
- **Rate/wage/VAT/Tax data is commercially sensitive** — confirm with Owner whether these fields should be visible to every `admin`-tier user or restricted further (e.g. only `superadmin` can see wage/profit figures, `admin` can see client rates only). Not decided in this plan.
- **`.env` remains the sole authority** for which phone numbers may be a Contact Person Mobile — this plan's validation reads that list, it never writes to it (writing to `ESCORT_CLIENT_PHONES` stays exclusively the job of the existing `/escort-clients` endpoints).

---

## 6. Migration plan — STATUS: prepared, validated, NOT applied

1. `db/migrations/034_client_billing_profiles.sql` — written, containing the finalized DDL in §1.
2. Validated using this session's established method: test-harness schema check → scratch-DB restore of real production schema + apply → concurrency/edge-case check on the `contact_person_mobile` unique index (two concurrent attempts to register the same authorized number — confirm the second is rejected, not silently duplicated) → FK-enforcement check (an invalid `contact_id` must be rejected, matching the pattern already proven for Migration 032). See the accompanying validation run for exact results.
3. **No transaction-preview dry run against production this time** — deliberately. This table uses `SERIAL` (`client_id_seq`), and per the dry-run safety rule added after the Migration 032 incident, a preview-and-rollback would let `nextval()` calls leak past the `ROLLBACK` (non-transactional sequence behavior). Validation for this migration is scratch-DB-only, avoiding the issue entirely rather than risking a repeat and then asking for forgiveness.
4. **Not applied to production.** Awaiting explicit Owner approval, per instruction ("Do not apply production migration or start code until I separately approve").

---

## 7. Rollback plan

- Table is new and starts empty — `DROP TABLE wbom_client_billing_profiles` is a complete, safe rollback with no data loss, as long as no bill has yet been generated referencing a rate from it (once Commit C exists and bills start referencing these rates, rollback needs re-assessment — not a concern for this migration alone).
- New backend routes: remove the route module; no data migration needed to roll back API-only changes.
- New frontend section: remove the HTML/JS block; no data impact.

---

## 8. Tests (planned, not yet written)

- Unit: `_normalize_client_phone`/`_escort_client_phone_list` reuse — confirm the new validation correctly rejects an unauthorized number and accepts an authorized one (mirroring the existing `/escort-clients` test coverage, if any exists — **not yet confirmed; check before writing new tests**, since duplicate/missing test coverage for the *existing* endpoint would be worth knowing regardless of this new feature).
- Unit: CRUD round-trip for `wbom_client_billing_profiles` (Add → List → Edit → soft-Delete → confirm `is_active=false`, confirm it drops out of an `?active=true` filtered list).
- Unit: uniqueness constraint on `contact_person_mobile` — attempt to register two profiles with the same authorized number, confirm rejection.
- Integration: RBAC — confirm a non-admin-role dashboard session cannot reach these routes.
- Regression: full non-e2e suite re-run after implementation, matching the standard established this session (confirm 0 new failures beyond the existing 29-failure baseline).

---

## 9. Updated Phase 2B readiness

The `service_charge` blocker in `docs/PHASE2B_READINESS_REPORT.md` ("New Owner Decisions Required" #1) is **fully resolved** — all 4 of this plan's original open design questions now have final Owner decisions (see "Resolved Owner decisions" above). **Status, updated 2026-07-09: implemented, applied, and verified live** — see `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` for the full activation report. Commit C (bill generation hook) can now be designed in detail against this finalized, live schema; it is not yet implemented.

---

## Remaining open items before Commit C

Only 1 question remains unresolved:
1. RBAC granularity — does `admin` see wage/profit figures, or only `superadmin`? Currently implemented as **unrestricted** (every `admin`-tier `GET` returns all fields via `SELECT *`) — this was the simpler, non-inventing-a-rule default in the absence of an Owner decision, not a considered restriction. Confirmed as the live behavior during restart verification (2026-07-09).

**Resolved 2026-07-09:** Hard-vs-soft delete — implemented as soft-delete (`is_active=false`), confirmed working live. The `employee_wage_general` data-model gap flagged below — **resolved same day**, Owner decision: add it now, since bill generation for General duty works without it but profit calculation would be wrong/incomplete without it. `db/migrations/035_client_billing_wage_general.sql` applied to production; `modules/client_billing/profiles.py`, `routes.py`, and the `payroll.html` form all updated and verified.

See `docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md` for the full backend/frontend specification and activation report.
