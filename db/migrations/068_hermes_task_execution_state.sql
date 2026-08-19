-- Migration 068: hermes_tasks / hermes_task_events / hermes_action_approvals
-- (2026-08-19)
--
-- Owner-directed follow-on to the P0-P2 reliability-hardening pass and the
-- BUILD/RUN/business-action capability audit (see
-- project_hermes_earth_rename_autonomy_20260819 memory). Owner's priority:
-- Earth already has, once granted RUN mode or via opencode_dispatch,
-- essentially raw host-level file/shell/code-execution capability -- the
-- real gap toward a governed AI co-worker is state that survives a
-- conversation, and approval that isn't just Earth's own self-asserted
-- confirm=True text. This migration adds both:
--
--   hermes_tasks            -- persistent goal/task/subtask state, survives
--                               a conversation ending; recoverable in a later
--                               session (session-recovery)
--   hermes_task_events       -- append-only audit trail per task
--   hermes_action_approvals  -- diff-level "propose -> approve -> execute"
--                               gate for file mutations / git commits /
--                               deploys / migrations / restarts / production
--                               writes / opencode_dispatch calls
--
-- Shape deliberately mirrors modules/draft_approval/'s existing, proven
-- fazle_payment_drafts + fazle_draft_audit_log pattern (pending/approved/
-- rejected states, before/after JSONB audit rows, server-side-only actor
-- resolution) rather than inventing a new one.
--
-- Purely additive: three new tables, no existing table touched. hermes_
-- prefix matches this migration's siblings (060 hermes_kernel_scaffolding,
-- 067 hermes_decision_source_message_id) since these are Hermes-kernel-
-- owned. depends_on is a plain bigint[] Earth checks client-side before
-- claiming a task -- not an enforced FK/constraint, no auto-scheduler (see
-- this pass's plan doc's explicit non-goals).

CREATE TABLE IF NOT EXISTS hermes_tasks (
    id               BIGSERIAL PRIMARY KEY,
    parent_task_id   BIGINT REFERENCES hermes_tasks(id),
    title            TEXT NOT NULL,
    description      TEXT,
    status           VARCHAR(20) NOT NULL DEFAULT 'BACKLOG'
        CHECK (status IN ('BACKLOG','READY','IN_PROGRESS','WAITING_APPROVAL','VERIFYING','DONE','FAILED','BLOCKED')),
    owner            VARCHAR(50) NOT NULL DEFAULT 'earth',
    priority         VARCHAR(10) NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high','urgent')),
    created_from     VARCHAR(30),                 -- 'whatsapp' | 'web' | 'admin_directive' | ...
    trace_id         UUID,                         -- links to hermes_decision_audit_log.trace_id (by convention, no FK -- different table's PK shape)
    depends_on       BIGINT[] NOT NULL DEFAULT '{}',
    execution_mode   VARCHAR(10) CHECK (execution_mode IN ('READ','BUILD','RUN')),
    artifact_refs    JSONB NOT NULL DEFAULT '[]'::jsonb,
    verification     JSONB,
    failure_reason   TEXT,
    next_action      TEXT,                         -- session-recovery hint: what should happen next
    created_by_phone VARCHAR(20),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_status ON hermes_tasks(status);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_parent ON hermes_tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_hermes_tasks_trace ON hermes_tasks(trace_id);

-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hermes_task_events (
    id            BIGSERIAL PRIMARY KEY,
    task_id       BIGINT NOT NULL REFERENCES hermes_tasks(id),
    event         VARCHAR(30) NOT NULL,   -- created/claimed/status_changed/approval_requested/approved/rejected/failed/verified
    before_status VARCHAR(20),
    after_status  VARCHAR(20),
    performed_by  VARCHAR(50) NOT NULL,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hermes_task_events_task ON hermes_task_events(task_id);

-- ──────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS hermes_action_approvals (
    id               BIGSERIAL PRIMARY KEY,
    task_id          BIGINT REFERENCES hermes_tasks(id),
    action_type      VARCHAR(30) NOT NULL
        CHECK (action_type IN ('file_mutation','git_commit','deploy','migration','restart','production_write','opencode_dispatch')),
    summary          TEXT NOT NULL,
    files_changed    TEXT[],
    diff             TEXT,
    command          TEXT,
    risk             VARCHAR(10) NOT NULL DEFAULT 'medium'
        CHECK (risk IN ('low','medium','high','critical')),
    expected_effect  TEXT,
    rollback_plan    TEXT,
    status           VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected','executed','expired')),
    requested_by     VARCHAR(50) NOT NULL DEFAULT 'earth',
    reviewed_by      VARCHAR(20),
    reviewed_at      TIMESTAMPTZ,
    executed_at      TIMESTAMPTZ,
    execution_result JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT now() + interval '24 hours'
);
CREATE INDEX IF NOT EXISTS idx_hermes_action_approvals_task ON hermes_action_approvals(task_id);
CREATE INDEX IF NOT EXISTS idx_hermes_action_approvals_status ON hermes_action_approvals(status);
