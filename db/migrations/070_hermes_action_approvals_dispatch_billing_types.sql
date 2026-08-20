-- Migration 070: widen hermes_action_approvals.action_type CHECK
-- constraint -- add 'dispatch_assignment' and 'billing_mutation'
-- (2026-08-21)
--
-- P1-A/P1-B of the Phase 4 business-domain coverage-audit follow-on (see
-- HERMES_PHASE4_BUSINESS_DOMAIN_COVERAGE_AUDIT memory). Owner instruction:
-- Hermes's real escort-dispatch-assignment and client-billing mutation
-- tools (fazle-mcp/dispatch_tools.py, client_billing_tools.py) must NOT
-- rely on a bare confirm=True -- they use the same propose/authorize ->
-- approve -> execute gate opencode_dispatch already uses
-- (hermes_action_approvals), which requires action_type to be one of a
-- fixed, code- and DB-enforced set (see modules/hermes_tasks/__init__.py's
-- ACTION_TYPES frozenset and this table's own CHECK constraint, added in
-- migration 068).
--
-- Two new values, matching the granularity opencode_dispatch already set
-- as precedent (one dedicated type per class of high-stakes action, not a
-- shared generic bucket -- reusing 'production_write' for both would let
-- an approval minted for one specific billing mutation be reused to
-- authorize an unrelated dispatch assignment, or vice versa):
--   'dispatch_assignment' -- covers dispatch_assign_program/
--                             dispatch_unassign_program/
--                             dispatch_replace_escort (modules/dispatch)
--   'billing_mutation'    -- covers generate_bill/mark_bill_paid/
--                             create_client_billing_profile/
--                             update_client_billing_profile/
--                             delete_client_billing_profile
--                             (modules/client_billing)
--
-- Purely additive: no default change, no existing row rewritten, no
-- existing action_type value removed.

BEGIN;

ALTER TABLE hermes_action_approvals
    DROP CONSTRAINT hermes_action_approvals_action_type_check;

ALTER TABLE hermes_action_approvals
    ADD CONSTRAINT hermes_action_approvals_action_type_check
    CHECK (action_type IN (
        'file_mutation', 'git_commit', 'deploy', 'migration', 'restart',
        'production_write', 'opencode_dispatch',
        'dispatch_assignment', 'billing_mutation'
    ));

COMMIT;

-- Downgrade (manual, run only after confirming zero rows use either new
-- action_type value):
--   BEGIN;
--   ALTER TABLE hermes_action_approvals DROP CONSTRAINT hermes_action_approvals_action_type_check;
--   ALTER TABLE hermes_action_approvals ADD CONSTRAINT hermes_action_approvals_action_type_check
--       CHECK (action_type IN ('file_mutation','git_commit','deploy','migration','restart','production_write','opencode_dispatch'));
--   COMMIT;
