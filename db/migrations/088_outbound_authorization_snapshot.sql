-- Migration 088: persist explicit outbound authorization snapshots.
--
-- Snapshot fields are nullable for compatibility with all existing queue rows.
-- No legacy row is backfilled or made eligible by this migration. A later
-- dispatcher must compare the snapshot with current identity, role, hold,
-- channel, and domain state before transport.

BEGIN;

ALTER TABLE fazle_outbound_queue
    ADD COLUMN IF NOT EXISTS authorization_identity_id BIGINT,
    ADD COLUMN IF NOT EXISTS authorization_version BIGINT,
    ADD COLUMN IF NOT EXISTS authorization_conversation_key TEXT,
    ADD COLUMN IF NOT EXISTS authorization_domain VARCHAR(80),
    ADD COLUMN IF NOT EXISTS authorization_scope_type VARCHAR(40),
    ADD COLUMN IF NOT EXISTS authorization_scope_key VARCHAR(255),
    ADD COLUMN IF NOT EXISTS authorization_role_names JSONB,
    ADD COLUMN IF NOT EXISTS authorization_captured_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'fazle_outbound_queue'
           AND c.conname = 'fazle_outbound_queue_authorization_identity_fk'
    ) THEN
        ALTER TABLE fazle_outbound_queue
            ADD CONSTRAINT fazle_outbound_queue_authorization_identity_fk
            FOREIGN KEY (authorization_identity_id)
            REFERENCES fazle_identity_subjects(identity_id)
            ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'fazle_outbound_queue'
           AND c.conname = 'fazle_outbound_queue_authorization_version_ck'
    ) THEN
        ALTER TABLE fazle_outbound_queue
            ADD CONSTRAINT fazle_outbound_queue_authorization_version_ck
            CHECK (authorization_version IS NULL OR authorization_version > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'fazle_outbound_queue'
           AND c.conname = 'fazle_outbound_queue_authorization_roles_ck'
    ) THEN
        ALTER TABLE fazle_outbound_queue
            ADD CONSTRAINT fazle_outbound_queue_authorization_roles_ck
            CHECK (
                authorization_role_names IS NULL
                OR jsonb_typeof(authorization_role_names) = 'array'
            );
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_outbound_authorization_identity_version
    ON fazle_outbound_queue (authorization_identity_id, authorization_version)
    WHERE authorization_identity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outbound_authorization_conversation
    ON fazle_outbound_queue (authorization_conversation_key, authorization_domain)
    WHERE authorization_conversation_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_outbound_authorization_pending
    ON fazle_outbound_queue (status, next_retry_at, authorization_identity_id)
    WHERE status IN ('pending', 'failed');

COMMIT;
