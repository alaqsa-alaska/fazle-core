-- Migration 034: Client Billing Profiles (Phase 2B, resolves the service_charge blocker)
--
-- Context: docs/PHASE2B_READINESS_REPORT.md ("no client-facing rate/price
-- field exists anywhere in this schema" — confirmed via 3 independent
-- checks) and docs/CLIENT_BILLING_PROFILE_IMPLEMENTATION_PLAN.md (full
-- design). Owner decisions, 2026-07-09, all final:
--
--   1. Billing/rate type (General/24H/12H/Monthly) is selected manually by
--      an admin at bill-confirmation time -- NOT inferred from
--      wbom_escort_programs in this phase. No duty_type column is added to
--      this table; that selection happens at bill-generation time
--      (Commit C, not yet built) and is out of this migration's scope.
--   2. VAT / Tax / Other Charges are internal profit-ESTIMATION deductions
--      only this phase -- NOT added to the client's invoice total.
--         Client Bill = selected client rate
--         Profit      = Client Bill - Deployed Employee Wage - VAT - Tax - Other Charges
--   3. Flat amounts only for VAT/Tax/Other Charges this phase. Percentage
--      support is a documented future enhancement (see column comments
--      below), not built now.
--   4. wbom_client_billing_profiles.contact_id is a required column
--      (nullable value) -- a real FK to wbom_contacts, never a
--      disconnected client system. Populated when resolvable; legacy
--      records may stay NULL, no forced backfill.
--
-- Contact Person Mobile validation (must be a member of the current
-- ESCORT_CLIENT_PHONES .env list) is enforced at the APPLICATION layer,
-- not by this migration -- the database has no way to read a running
-- process's .env state, and .env remains the sole authority per the Owner's
-- explicit instruction. See docs/CLIENT_BILLING_PROFILE_CODE_PLAN.md for
-- the validation-layer design.
--
-- employee_wage_24h/12h/monthly are for the profit-estimation calculation
-- on THIS table only -- they must never feed into, override, or be read by
-- modules/payroll, modules/payment_workflow, or any real guard-payment
-- calculation, per the Owner's "do not touch payroll formula" rule.
--
-- STATUS (2026-07-09): validated (test-harness schema check + scratch-DB
-- restore-and-apply against a real production schema copy, including FK
-- and unique-constraint enforcement tests). NOT applied to production --
-- awaiting explicit Owner approval. Deliberately NOT validated via a
-- transaction-preview dry run against production this time -- this table
-- uses SERIAL (client_id_seq), and per the dry-run safety rule added after
-- the Migration 032 incident, a preview-and-rollback would let nextval()
-- calls leak past the ROLLBACK (sequences are non-transactional). Scratch-
-- DB-only validation avoids the issue rather than risking a repeat.

CREATE TABLE IF NOT EXISTS wbom_client_billing_profiles (
    client_id              SERIAL PRIMARY KEY,
    client_name             VARCHAR(200) NOT NULL,
    office_address           TEXT,
    contact_person_name     VARCHAR(120),
    contact_person_mobile   VARCHAR(20) NOT NULL,
    chairman_name            VARCHAR(120),
    chairman_mobile          VARCHAR(20),
    managing_member_name     VARCHAR(120),
    managing_member_mobile   VARCHAR(20),
    general_rate             NUMERIC(12,2),
    rate_24h                 NUMERIC(12,2),
    rate_12h                 NUMERIC(12,2),
    rate_monthly              NUMERIC(12,2),
    employee_wage_24h        NUMERIC(12,2),
    employee_wage_12h        NUMERIC(12,2),
    employee_wage_monthly     NUMERIC(12,2),
    -- Flat amounts only this phase (Owner decision #3). Future enhancement:
    -- add vat_is_percentage/tax_is_percentage BOOLEAN DEFAULT false flags
    -- alongside these columns so the application layer can interpret the
    -- stored number as a percentage of general_rate when the flag is set --
    -- additive, no breaking change needed to add this later.
    vat_amount                NUMERIC(12,2) NOT NULL DEFAULT 0,
    tax_amount                NUMERIC(12,2) NOT NULL DEFAULT 0,
    other_charges              NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes                     TEXT,
    is_active                 BOOLEAN NOT NULL DEFAULT true,
    contact_id                INTEGER REFERENCES wbom_contacts(contact_id),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast filtering on the list view (Add/Edit/Delete-soft screen defaults to
-- active clients only).
CREATE INDEX IF NOT EXISTS idx_client_billing_profiles_active
    ON wbom_client_billing_profiles(is_active);

-- Supports the future "does this WhatsApp contact already have a billing
-- profile" lookup once Commit C (bill generation) is built.
CREATE INDEX IF NOT EXISTS idx_client_billing_profiles_contact_id
    ON wbom_client_billing_profiles(contact_id);

-- One client billing profile per authorized Contact Person Mobile --
-- prevents the same .env-authorized number being silently assigned to two
-- different client profiles, which would make inbound-message-to-client
-- linking ambiguous.
CREATE UNIQUE INDEX IF NOT EXISTS idx_client_billing_profiles_contact_mobile
    ON wbom_client_billing_profiles(contact_person_mobile);

-- No backfill of existing wbom_contacts/wbom_escort_programs rows in this
-- migration -- per "do not auto-backfill old clients," this table starts
-- empty; profiles are created going forward via the (not yet built)
-- Client Settings UI.
