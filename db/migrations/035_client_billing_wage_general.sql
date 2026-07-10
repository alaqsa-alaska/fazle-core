-- Migration 035: Add employee_wage_general to wbom_client_billing_profiles
--
-- Context: docs/PHASE2B_READINESS_REPORT.md flagged that the Owner's
-- original Client Settings field list paired employee_wage_24h/12h/monthly
-- with their matching rates, but had no equivalent wage field for
-- general_rate. Owner decision (2026-07-09): add it now, before Commit C,
-- because bill generation for "General" duty type can work without it
-- (general_rate alone is enough to bill the client), but profit
-- calculation for General duty would be wrong/incomplete without a wage
-- figure to subtract.
--
-- Same profit-estimation-only scope as the other three wage columns
-- (db/migrations/034_client_billing_profiles.sql) -- must never feed into,
-- override, or be read by modules.payroll, modules.payment_workflow, or
-- any real guard-payment calculation.
--
-- Additive, nullable, no default -- consistent with every other rate/wage
-- field on this table (no guessed values, per "do not guess rates").

ALTER TABLE wbom_client_billing_profiles
    ADD COLUMN IF NOT EXISTS employee_wage_general NUMERIC(12,2);
