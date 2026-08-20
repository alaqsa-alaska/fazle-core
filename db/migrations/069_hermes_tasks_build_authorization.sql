-- Migration 069: hermes_tasks.build_authorized/... (2026-08-19)
--
-- Owner-directed revision of the same-day Task/Execution-State +
-- Action-Approval pass (migration 068): per-action `APPROVE ACTION <id>`
-- typing must not be the primary UX. This adds a broader, task-scoped
-- BUILD authorization grant -- a single authorization that covers many
-- subsequent file edits/tests within an authorized repo scope, distinct
-- from the per-action `hermes_action_approvals` gate (which remains, and
-- is what commit/deploy/restart/migration/production-write still use).
--
-- Earth calls modules.hermes_tasks.authorize_build() when it recognizes
-- the admin's real, current instruction ("fix it", "go ahead") as
-- sufficient authorization for the *current task's* implementation phase
-- -- the same "explicit instruction naming the exact entity" discipline
-- already documented for every Tier C/D tool, applied here to a task
-- rather than a single business record.
--
-- Purely additive: 5 nullable/defaulted columns on the existing
-- hermes_tasks table, no ALTER on any other table, no data migration.

ALTER TABLE hermes_tasks
    ADD COLUMN IF NOT EXISTS build_authorized BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS build_authorized_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS build_authorized_by VARCHAR(20),
    ADD COLUMN IF NOT EXISTS build_scope JSONB,
    ADD COLUMN IF NOT EXISTS build_expires_at TIMESTAMPTZ;
