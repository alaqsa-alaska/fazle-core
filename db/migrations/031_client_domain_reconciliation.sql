-- Migration 031: Client Domain Reconciliation (Phase 2A)
--
-- Context: docs/CANONICAL_CLIENT_DOMAIN.md / docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md
-- (Phase 2A). Production evidence (2026-07-09, read-only queries) found THREE
-- uncoordinated client-classification mechanisms: wbom_contacts.relation (stale
-- free text), wbom_contacts.relation_type_id -> wbom_relation_types (structured,
-- ~5% populated), and fazle_contact_roles.role (free text, no FK to wbom_contacts
-- at all, 47 rows). This migration makes wbom_contacts.relation_type_id the
-- single source of truth going forward by backfilling it from
-- fazle_contact_roles, using the SAME normalized last-10-digit phone-matching
-- convention already established in
-- modules/fazle_payroll_engine/migrations/008_unification.sql (fe.primary_phone
-- <-> we.employee_mobile), since fazle_contact_roles.phone and
-- wbom_contacts.whatsapp_number are stored in inconsistent formats
-- (some 01XXXXXXXXX, some 8801XXXXXXXXX).
--
-- Verified read-only against production before writing this file:
--   - 6 of 47 fazle_contact_roles rows are source='smoke-test', is_active=false,
--     explicitly noted "temporary smoke test identity (deactivated)" (created
--     2026-07-04) -- excluded entirely, not real client/contact data.
--   - Of the remaining 41 real rows, EVERY one matches exactly one
--     wbom_contacts row via normalized phone (0 orphans, 0 fan-out/duplicate
--     matches) -- this reconciliation is safe to run as written.
--   - No row being touched already has a conflicting relation_type_id set
--     (the one pre-existing match, relation_type_id=5, already agrees with
--     fazle_contact_roles' 'client_escort_buyer' for that same phone).
--
-- Mapping decisions (disclosed, not silently made -- see accompanying report):
--   repeat_client, vip_client   -> 'Client'         (relation_type_id=1)
--   client_escort_buyer          -> 'Escort Client'  (relation_type_id=5, after
--                                    the casing fix below)
--   vendor                       -> 'Vendor'         (relation_type_id=2)
--   employee                     -> 'Employee'       (relation_type_id=4)
--   family                       -> 'Family'         (NEW relation_type_id=6 --
--                                    no existing row covered this; adding one
--                                    rather than leaving 8 real, already-
--                                    classified contacts unclassified)
--   blocked                      -> NOT a relation type at all -- handled by
--                                    setting wbom_contacts.is_active = false
--                                    (the column that already exists for this
--                                    purpose), not by inventing a "Blocked"
--                                    relationship type.
--
-- OWNER DECISION (2026-07-09), supersedes this file's original draft:
--   'supervisor' and 'accountant' are NOT canonical client relation types
--   (no such rows are ever created -- Part A only adds 'Family', nothing else).
--   They map to the EXISTING 'Employee' relation_type_id=4 *only* where the
--   phone number independently, verifiably matches a real row in
--   wbom_employees.employee_mobile -- never as a blanket free-text-label
--   mapping. Checked exhaustively against production (2026-07-09): every
--   single one of the 5 distinct 'supervisor'-tagged phones and both
--   'accountant'-tagged phone-format rows (1 real person, 2 formats) matches
--   an Active wbom_employees row. Zero 'supervisor'/'accountant' rows failed
--   this check in this dataset, but the guard below is a real JOIN, not a
--   hardcoded list -- if a future 'supervisor'/'accountant' tag does NOT match
--   an employee, it is left untouched by construction, not force-mapped.
--
-- .env AUTHORITY RULE (Owner decision 2026-07-09, documented per Rule 5 --
-- also recorded in knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md
-- and docs/CANONICAL_CLIENT_DOMAIN.md):
--   Phone numbers declared in .env (ADMIN_NUMBERS, ACCOUNTANT_PHONE,
--   FPE_CASH_AUTHORIZED_PHONES, FPE_INCOME_AUTHORIZED_PHONES) are the
--   MANDATORY, authoritative source of truth for who holds a functional
--   role/authorization in this system. wbom_contacts (contact_book),
--   wbom_employees, and cash-transaction-derived signals are supporting /
--   enrichment sources only -- never authoritative on their own, and must
--   never silently override an .env-declared identity.
--   Verified before writing this migration (read-only cross-check of all 22
--   distinct .env-listed numbers against wbom_contacts, fazle_contact_roles,
--   and wbom_employees): every .env-listed number that this migration's
--   UPDATEs would touch resolves to a classification that AGREES with its
--   .env-declared role (the 5 supervisor/accountant-turned-Employee cases are
--   all independently confirmed employees; the escort-client-tagged numbers
--   that also appear in ESCORT_CLIENT_PHONES agree with 'Escort Client';
--   numbers in the non-authoritative DRAFT_ALWAYS_PHONES safety list carry no
--   identity claim and are not in conflict with any relation type). No
--   .env-listed identity is overwritten incorrectly by this migration.
--
-- Idempotent: every UPDATE is guarded by relation_type_id IS NULL (never
-- overwrites an existing value) or by an is_active check. Safe to re-run.

-- ── Part A: wbom_relation_types reference-data fixes ───────────────────────────

-- A1: casing consistency -- the other 4 rows are Title Case ('Client',
-- 'Vendor', 'Partner', 'Employee'); 'escort_client' was the odd one out.
UPDATE wbom_relation_types
   SET relation_name = 'Escort Client'
 WHERE relation_type_id = 5
   AND relation_name = 'escort_client';

-- A2: add the missing 'Family' relation type (8 real fazle_contact_roles rows
-- need it; no existing row covers this concept).
INSERT INTO wbom_relation_types (relation_type_id, relation_name, description, is_active)
SELECT 6, 'Family', 'Family member of the Owner/admin, contacts via WhatsApp', true
WHERE NOT EXISTS (SELECT 1 FROM wbom_relation_types WHERE relation_type_id = 6);

-- ── Part B: wbom_contacts.relation_type_id backfill from fazle_contact_roles ──
-- Guarded: only fills NULL relation_type_id, never overwrites an existing
-- classification. Excludes smoke-test/inactive fazle_contact_roles rows.

UPDATE wbom_contacts wc
   SET relation_type_id = 1  -- Client
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role IN ('repeat_client', 'vip_client')
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

UPDATE wbom_contacts wc
   SET relation_type_id = 5  -- Escort Client
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role = 'client_escort_buyer'
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

UPDATE wbom_contacts wc
   SET relation_type_id = 2  -- Vendor
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role = 'vendor'
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

UPDATE wbom_contacts wc
   SET relation_type_id = 4  -- Employee
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role = 'employee'
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

UPDATE wbom_contacts wc
   SET relation_type_id = 6  -- Family
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role = 'family'
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

-- 'supervisor' / 'accountant' -> Employee, ONLY where independently verified
-- against wbom_employees.employee_mobile (see header). This is a real JOIN
-- guard, not a blanket free-text-label mapping -- an unverified
-- supervisor/accountant row is left untouched by construction.
UPDATE wbom_contacts wc
   SET relation_type_id = 4  -- Employee (existing type; no new type created)
  FROM fazle_contact_roles fcr
  JOIN wbom_employees we
    ON RIGHT(REGEXP_REPLACE(we.employee_mobile, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
 WHERE fcr.is_active = true
   AND fcr.role IN ('supervisor', 'accountant')
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.relation_type_id IS NULL;

-- ── Part C: 'blocked' is a moderation flag, not a relationship type ───────────
-- Sets is_active = false on the matching wbom_contacts row (the column that
-- already exists for this purpose) instead of inventing a "Blocked" relation
-- type.

UPDATE wbom_contacts wc
   SET is_active = false
  FROM fazle_contact_roles fcr
 WHERE fcr.is_active = true
   AND fcr.role = 'blocked'
   AND RIGHT(REGEXP_REPLACE(wc.whatsapp_number, '\D', '', 'g'), 10)
     = RIGHT(REGEXP_REPLACE(fcr.phone, '\D', '', 'g'), 10)
   AND wc.is_active = true;
