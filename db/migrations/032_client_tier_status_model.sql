-- Migration 032: Client Tier / Client Status model (Phase 2B preparation)
--
-- STATUS (2026-07-09): FULLY APPLIED TO PRODUCTION.
-- Part A (the setval() sequence fix) reached production first, as an
-- unintended side effect of a transaction-preview dry run -- PostgreSQL
-- sequence operations are non-transactional and are not undone by ROLLBACK
-- (see knowledge_base/06_developer_system/database_rules.md "Migration
-- dry-run safety rule", added 2026-07-09, for the full incident write-up and
-- the new dry-run methodology rule this caused). Owner reviewed and
-- explicitly accepted keeping the fix rather than reverting it. Parts B-D
-- (the two new lookup tables and the two new wbom_contacts columns) were
-- applied separately afterward, with explicit Owner approval, following
-- test-harness schema check + scratch-DB restore-and-apply validation.
-- Post-apply verification confirmed: both lookup tables at 4 rows each,
-- both new columns nullable with no default, all 2,086 existing contacts
-- NULL on both (no backfill performed), both FK constraints present, no
-- other table/row counts changed. This file is idempotent (IF NOT EXISTS /
-- WHERE NOT EXISTS / GREATEST throughout) and safe to re-run.
--
-- Context: knowledge_base/00_governance/CANONICAL_BUSINESS_RULES.md §Client,
-- docs/CANONICAL_CLIENT_DOMAIN.md §"Client Tier, Client Status, and is_active",
-- docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md Phase 2B (Owner decisions,
-- 2026-07-09). Implements the final, 4-dimension client model:
--
--   relation_type  = IDENTITY            -> wbom_contacts.relation_type_id
--                    (Phase 2A, unchanged, not touched here)
--   client_tier    = BUSINESS VALUE      -> wbom_contacts.client_tier_id (NEW)
--   client_status  = BUSINESS LIFECYCLE  -> wbom_contacts.client_status_id (NEW)
--   is_active      = SYSTEM PERMISSION   -> wbom_contacts.is_active
--                    (Phase 2A, unchanged, not touched here)
--
-- 'Blocked' is EXCLUSIVELY represented by wbom_contacts.is_active = false.
-- Neither wbom_client_tiers nor wbom_client_statuses may ever contain a
-- 'Blocked' row -- this migration does not create one, and none should ever
-- be added.
--
-- Lookup-table model chosen (not a CHECK-constrained column): mirrors
-- wbom_relation_types / wbom_business_types, the two existing sibling
-- dimensions on this same table, rather than escort_roster_change_requests'
-- inline-CHECK pattern -- consistency across wbom_contacts' four dimensions
-- was judged more valuable here than the (also-valid) CHECK-constraint
-- alternative; either would have satisfied the Owner's decision, this is an
-- implementation detail per docs/CLIENT_DOMAIN_IMPLEMENTATION_PLAN.md.
--
-- Defaults -- disclosed decision, not a silent guess: NEITHER new FK column
-- gets a column-level DEFAULT. Reasoning: `client_status` = 'Active' as a
-- blanket default would apply to every wbom_contacts row via ALTER TABLE ...
-- ADD COLUMN ... DEFAULT semantics, including the ~1,950 vendor/employee/
-- family/unclassified contacts for whom "client lifecycle" has no meaning.
-- Leaving both columns NULL preserves "not applicable / not yet classified"
-- as a real, honest state, matching Phase 2A's own discipline of never
-- backfilling a classification without evidence. Recommended app-layer
-- convention (not enforced by this migration): new intake code that creates
-- a contact with relation_type = Client/Escort Client should explicitly set
-- client_tier_id = 1 (Standard) and client_status_id = 1 (Active) at
-- creation time -- a future Billing/GL implementation decision, out of
-- scope for this schema-only migration.
--
-- Bonus fix (found while building this, disclosed not smuggled in):
-- migration 031 inserted wbom_relation_types id=6 ('Family') via an explicit
-- SELECT rather than nextval(), so wbom_relation_types_relation_type_id_seq
-- was left at 5 -- the next auto-generated insert would have collided with
-- id=6. Advanced here.

-- ── Part A: fix the sequence desync migration 031 introduced ──────────────────
SELECT setval(
    'wbom_relation_types_relation_type_id_seq',
    GREATEST(6, (SELECT MAX(relation_type_id) FROM wbom_relation_types))
);

-- ── Part B: wbom_client_tiers (business-value dimension) ───────────────────────
CREATE TABLE IF NOT EXISTS wbom_client_tiers (
    client_tier_id INTEGER PRIMARY KEY,
    tier_name       VARCHAR(40) NOT NULL UNIQUE,
    description     TEXT,
    is_active       BOOLEAN DEFAULT true
);

INSERT INTO wbom_client_tiers (client_tier_id, tier_name, description, is_active)
SELECT * FROM (VALUES
    (1, 'Standard', 'Default tier for a new or unclassified client', true),
    (2, 'Repeat',   'Client with a history of repeat orders', true),
    (3, 'VIP',      'High-value client receiving priority handling', true),
    (4, 'Strategic','Strategically important client (e.g. large/contract accounts)', true)
) AS v(client_tier_id, tier_name, description, is_active)
WHERE NOT EXISTS (
    SELECT 1 FROM wbom_client_tiers WHERE wbom_client_tiers.client_tier_id = v.client_tier_id
);

-- ── Part C: wbom_client_statuses (business-lifecycle dimension) ───────────────
CREATE TABLE IF NOT EXISTS wbom_client_statuses (
    client_status_id INTEGER PRIMARY KEY,
    status_name        VARCHAR(40) NOT NULL UNIQUE,
    description        TEXT,
    is_active          BOOLEAN DEFAULT true
);

INSERT INTO wbom_client_statuses (client_status_id, status_name, description, is_active)
SELECT * FROM (VALUES
    (1, 'Active',   'Currently active client relationship', true),
    (2, 'Inactive', 'No recent activity; not formally closed', true),
    (3, 'Suspended','Business relationship temporarily paused', true),
    (4, 'Closed',   'Client relationship formally ended', true)
) AS v(client_status_id, status_name, description, is_active)
WHERE NOT EXISTS (
    SELECT 1 FROM wbom_client_statuses WHERE wbom_client_statuses.client_status_id = v.client_status_id
);

-- ── Part D: wbom_contacts new columns ──────────────────────────────────────────
-- No column-level DEFAULT on either -- see header for reasoning. Nullable,
-- idempotent (IF NOT EXISTS), FK-constrained to the lookup tables above.

ALTER TABLE wbom_contacts
    ADD COLUMN IF NOT EXISTS client_tier_id INTEGER
        REFERENCES wbom_client_tiers(client_tier_id);

ALTER TABLE wbom_contacts
    ADD COLUMN IF NOT EXISTS client_status_id INTEGER
        REFERENCES wbom_client_statuses(client_status_id);

-- No indexes added on the new columns, matching this table's existing
-- relation_type_id / business_type_id columns (neither is indexed today) --
-- consistent with current practice, not a new gap introduced by this
-- migration.

-- No backfill of existing wbom_contacts rows in this migration -- assigning
-- a tier/status to any of the 2,086 existing contacts is a business
-- classification decision for whoever operates the future Billing/GL
-- intake flow, not something to guess via migration.
