-- Migration 071: widen hermes_action_approvals.action_type CHECK
-- constraint -- add 'escort_program_creation' (2026-08-21)
--
-- Phase 4 coverage-audit follow-on, P1-C (Escort Program <-> Roster
-- lifecycle gap). The forensic audit found that creating a NEW
-- wbom_escort_programs row is a genuinely different business action from
-- assigning an employee to an EXISTING one (already gated by migration
-- 070's 'dispatch_assignment') -- deciding whether a duty/program should
-- exist at all is a different decision than deciding who does it. Per
-- Owner instruction: "do not widen an existing action type semantically
-- just for convenience" -- this is a genuinely new action, not a
-- convenience reuse, so it gets its own dedicated value rather than
-- overloading 'dispatch_assignment' or the generic 'production_write'.
--
-- Gates fazle-mcp's new create_escort_program tool (wraps the new
-- POST /api/escort/programs route, which itself is a thin wrapper over
-- the EXISTING modules.escort.save_escort_programs() -- no new business
-- logic, no second program-creation implementation).
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
        'dispatch_assignment', 'billing_mutation',
        'escort_program_creation'
    ));

COMMIT;

-- Downgrade (manual, run only after confirming zero rows use the new
-- action_type value):
--   BEGIN;
--   ALTER TABLE hermes_action_approvals DROP CONSTRAINT hermes_action_approvals_action_type_check;
--   ALTER TABLE hermes_action_approvals ADD CONSTRAINT hermes_action_approvals_action_type_check
--       CHECK (action_type IN ('file_mutation','git_commit','deploy','migration','restart','production_write','opencode_dispatch','dispatch_assignment','billing_mutation'));
--   COMMIT;
