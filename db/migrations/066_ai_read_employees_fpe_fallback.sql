-- Migration 066: extend ai_read_employees to include fpe_employees-linked
-- employees (Hermes Identity/Role/Intent Capability Audit — 2026-08-18)
--
-- HERMES_IDENTITY_ROLE_INTENT_CONTENT_CAPABILITY_AUDIT_2026-08-18.md found
-- that ai_read_employees (migration 012) selects only from wbom_employees,
-- while modules.identity_brain._lookup_employee() (the live resolver every
-- real WhatsApp message goes through) additionally falls back to
-- fpe_employees for employees who exist only via the payroll/cash/escort
-- auto-create path. An employee identity_brain can resolve was therefore
-- invisible to Hermes's get_employees tool. This migration mirrors
-- identity_brain's exact fallback query (identity_brain/__init__.py:386-401)
-- so the view and the resolver agree by construction.
--
-- CREATE OR REPLACE VIEW keeps the same view OID/name, so the existing
-- GRANT SELECT ... TO fazle_ai_reader from migration 012's grant-all loop
-- still applies — no new GRANT needed here.
--
-- Deliberately excludes bkash_number (financial PII), matching every other
-- ai_read_* view's pattern of excluding sensitive fields at the view level
-- (KB hard constraint #5, knowledge_base/06_developer_system/ai_readonly_data_access.md)
-- — same field the new /api/identity/resolve route also strips.
--
-- NOT RUN BY AI. KB hard constraint #7: "Production DB schema cannot be
-- changed by AI" (knowledge_base/06_developer_system/ai_readonly_data_access.md).
-- Run this file against production Postgres as a role with CREATE/GRANT on
-- `public` (not the read-only fazle_ai_reader connection) — e.g.
-- `psql "$DATABASE_URL" -f db/migrations/066_ai_read_employees_fpe_fallback.sql`,
-- or paste the statements below directly. Requires explicit Owner sign-off
-- per this repo's "no DB schema change without an explicit ask" rule —
-- this migration file is the explicit ask; applying it is a separate step.
--
-- Rollback (explicit, self-contained — does not require cross-referencing
-- migration 012): re-run the view's pre-066 definition below. Instant, no
-- data impact (view only ever reads; CREATE OR REPLACE is itself the
-- rollback mechanism for a view — no DROP, no GRANT re-issue needed, same
-- reasoning as the "no new GRANT needed" note above).
--
--   CREATE OR REPLACE VIEW ai_read_employees AS
--   SELECT
--       e.employee_id,
--       e.employee_name,
--       e.employee_mobile,
--       e.designation,
--       e.joining_date,
--       e.status,
--       e.basic_salary
--   FROM wbom_employees e
--   ORDER BY e.employee_name;

CREATE OR REPLACE VIEW ai_read_employees AS
SELECT
    e.employee_id,
    e.employee_name,
    e.employee_mobile,
    e.designation,
    e.joining_date,
    e.status,
    e.basic_salary
FROM wbom_employees e
UNION ALL
-- Explicit CASTs below (2026-08-18 correction, first execution attempt):
-- fpe_employees stores full_name/primary_phone/status as unbounded text,
-- while wbom_employees' equivalents are varchar(100)/varchar(20)/varchar(20).
-- CREATE OR REPLACE VIEW requires exact output-type equality with the
-- current live view, not just a compatible/wider type -- without these
-- casts, Postgres's UNION ALL type-unification widens the result to
-- unconstrained varchar and the CREATE OR REPLACE is rejected. Verified
-- no truncation risk against live data before adding these: among the 86
-- rows this branch currently selects, max lengths are full_name=24,
-- primary_phone=12, status=6 -- all well under their 100/20/20 limits.
-- designation needed the same treatment on a second dry-run pass: Postgres's
-- COALESCE drops the varchar(30) length modifier from w.designation once
-- combined with the untyped 'Staff' literal, same general rule as above --
-- not specific to the fpe/wbom type mismatch, just COALESCE's own typmod
-- behavior. No data-length reason to check here (literal is 5 chars,
-- w.designation is already-live varchar(30) data).
SELECT
    w.employee_id,
    CAST(COALESCE(NULLIF(w.employee_name, ''), NULLIF(fe.full_name, '')) AS character varying(100)) AS employee_name,
    CAST(fe.primary_phone AS character varying(20))                                                  AS employee_mobile,
    CAST(COALESCE(NULLIF(w.designation, ''), 'Staff') AS character varying(30))                       AS designation,
    NULL::date                                                      AS joining_date,
    CAST(COALESCE(w.status, CASE WHEN fe.status = 'active' THEN 'Active' ELSE 'Inactive' END) AS character varying(20)) AS status,
    w.basic_salary
FROM fpe_employees fe
LEFT JOIN wbom_employees w ON w.employee_id = fe.wbom_employee_id
WHERE fe.status = 'active'
  AND NOT EXISTS (
      SELECT 1 FROM wbom_employees w2 WHERE w2.employee_id = fe.wbom_employee_id
  )
ORDER BY employee_name;
