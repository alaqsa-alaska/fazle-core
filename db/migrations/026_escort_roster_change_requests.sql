-- Migration 026: Escort roster operator change-request queue
-- Purpose:
--   - store operator edit/delete requests for admin approval
--   - preserve immutable review trail for roster mutations

CREATE TABLE IF NOT EXISTS escort_roster_change_requests (
    id BIGSERIAL PRIMARY KEY,
    program_id BIGINT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('edit', 'delete')),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    requested_by TEXT,
    requested_by_phone TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by TEXT,
    review_note TEXT,
    reviewed_at TIMESTAMPTZ,
    result_payload JSONB
);

CREATE INDEX IF NOT EXISTS idx_escort_roster_change_requests_status
    ON escort_roster_change_requests (status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_escort_roster_change_requests_program
    ON escort_roster_change_requests (program_id, requested_at DESC);
