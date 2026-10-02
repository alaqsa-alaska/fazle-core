-- Migration 087: additive Identity-Role persistence foundation.
--
-- This migration creates stable identity subjects, extends the existing
-- wbom_identity_links table, and adds historical multi-role and durable hold
-- records. It does not infer identities, backfill contacts, touch the queue,
-- or change the legacy phone/role controls.
--
-- Authorization-version invariant: a caller changing roles, verified links,
-- identity verification state, or an identity hold must call
-- fazle_bump_identity_authorization_version() in the same transaction as the
-- state change, then persist the returned version on the history row. A
-- conversation-only hold uses its own state_version and is rechecked by the
-- later dispatcher; it does not stale unrelated conversations.

BEGIN;

CREATE TABLE IF NOT EXISTS fazle_identity_subjects (
    identity_id          BIGSERIAL PRIMARY KEY,
    identity_kind        VARCHAR(20) NOT NULL
        CHECK (identity_kind IN ('person', 'organization')),
    display_name         TEXT,
    verified_name        TEXT,
    verification_state   VARCHAR(20) NOT NULL DEFAULT 'unverified'
        CHECK (verification_state IN ('unverified', 'pending', 'verified', 'revoked')),
    lifecycle_state      VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (lifecycle_state IN ('active', 'suspended', 'closed')),
    authorization_version BIGINT NOT NULL DEFAULT 1
        CHECK (authorization_version > 0),
    version_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verified_by          TEXT,
    verified_at          TIMESTAMPTZ,
    verification_reason  TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fazle_identity_subjects_kind_state
    ON fazle_identity_subjects (identity_kind, lifecycle_state, verification_state);
CREATE INDEX IF NOT EXISTS idx_fazle_identity_subjects_auth_version
    ON fazle_identity_subjects (identity_id, authorization_version);

-- The single authoritative version increment primitive. It locks the subject
-- row through the UPDATE and is intentionally not called by legacy readers.
CREATE OR REPLACE FUNCTION fazle_bump_identity_authorization_version(
    p_identity_id BIGINT
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_version BIGINT;
BEGIN
    UPDATE fazle_identity_subjects
       SET authorization_version = authorization_version + 1,
           version_updated_at = NOW(),
           updated_at = NOW()
     WHERE identity_id = p_identity_id
    RETURNING authorization_version INTO v_version;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'identity subject % does not exist', p_identity_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    RETURN v_version;
END;
$$;

-- This table exists in the qualified live schema but was not represented by a
-- numbered migration in this checkout. The bootstrap keeps isolated rebuilds
-- compatible with that existing table before its additive columns are added.
CREATE TABLE IF NOT EXISTS wbom_identity_links (
    identity_link_id   BIGSERIAL PRIMARY KEY,
    identity_key       VARCHAR(255) NOT NULL,
    platform           VARCHAR(40) NOT NULL,
    platform_user_id   VARCHAR(255) NOT NULL,
    contact_id         INTEGER REFERENCES wbom_contacts(contact_id) ON DELETE SET NULL,
    confidence         NUMERIC(5,4) NOT NULL DEFAULT 1.0000
        CHECK (confidence >= 0 AND confidence <= 1),
    linked_by          VARCHAR(100) NOT NULL DEFAULT 'system',
    active             BOOLEAN NOT NULL DEFAULT TRUE,
    metadata_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE wbom_identity_links
    ADD COLUMN IF NOT EXISTS identity_id BIGINT,
    ADD COLUMN IF NOT EXISTS identifier_type VARCHAR(40),
    ADD COLUMN IF NOT EXISTS verification_state VARCHAR(20),
    ADD COLUMN IF NOT EXISTS evidence_source TEXT,
    ADD COLUMN IF NOT EXISTS verified_by TEXT,
    ADD COLUMN IF NOT EXISTS verified_by_admin_id BIGINT,
    ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS revocation_reason TEXT,
    ADD COLUMN IF NOT EXISTS authorization_version_at_change BIGINT NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'wbom_identity_links'
           AND c.conname = 'wbom_identity_links_identity_fk'
    ) THEN
        ALTER TABLE wbom_identity_links
            ADD CONSTRAINT wbom_identity_links_identity_fk
            FOREIGN KEY (identity_id) REFERENCES fazle_identity_subjects(identity_id)
            ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'wbom_identity_links'
           AND c.conname = 'wbom_identity_links_verified_admin_fk'
    ) THEN
        ALTER TABLE wbom_identity_links
            ADD CONSTRAINT wbom_identity_links_verified_admin_fk
            FOREIGN KEY (verified_by_admin_id) REFERENCES fazle_admins(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint c
          JOIN pg_class t ON t.oid = c.conrelid
         WHERE t.relname = 'wbom_identity_links'
           AND c.conname = 'wbom_identity_links_verification_state_ck'
    ) THEN
        ALTER TABLE wbom_identity_links
            ADD CONSTRAINT wbom_identity_links_verification_state_ck
            CHECK (verification_state IS NULL OR verification_state IN
                   ('unverified', 'pending', 'verified', 'revoked'));
    END IF;
END;
$$;

-- Preserve one active platform identifier while allowing revoked/corrected
-- rows to remain queryable as history. Existing deployments used this name
-- for a non-partial unique index, so replace only that index definition.
DO $$
DECLARE
    v_is_unique BOOLEAN;
    v_is_partial BOOLEAN;
BEGIN
    SELECT i.indisunique, i.indpred IS NOT NULL
      INTO v_is_unique, v_is_partial
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
      JOIN pg_index i ON i.indexrelid = c.oid
     WHERE n.nspname = 'public'
       AND c.relname = 'idx_wbom_identity_platform_user';

    IF FOUND AND (NOT v_is_unique OR NOT v_is_partial) THEN
        DROP INDEX public.idx_wbom_identity_platform_user;
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wbom_identity_platform_user
    ON wbom_identity_links (platform, platform_user_id)
    WHERE active;
CREATE INDEX IF NOT EXISTS idx_wbom_identity_links_subject_active
    ON wbom_identity_links (identity_id, active);
CREATE INDEX IF NOT EXISTS idx_wbom_identity_links_identifier
    ON wbom_identity_links (identifier_type, platform, platform_user_id);

CREATE TABLE IF NOT EXISTS fazle_identity_role_assignments (
    assignment_id       BIGSERIAL PRIMARY KEY,
    identity_id         BIGINT NOT NULL
        REFERENCES fazle_identity_subjects(identity_id) ON DELETE RESTRICT,
    role_name            VARCHAR(80) NOT NULL,
    business_domain      VARCHAR(80) NOT NULL,
    scope_type           VARCHAR(40) NOT NULL DEFAULT 'global',
    scope_key            VARCHAR(255),
    effective_from      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    effective_until      TIMESTAMPTZ,
    active               BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_by          TEXT NOT NULL DEFAULT 'system',
    assigned_by_admin_id BIGINT REFERENCES fazle_admins(id) ON DELETE SET NULL,
    assignment_reason    TEXT NOT NULL DEFAULT 'unspecified',
    revoked_at           TIMESTAMPTZ,
    revoked_by           TEXT,
    revoked_by_admin_id  BIGINT REFERENCES fazle_admins(id) ON DELETE SET NULL,
    revocation_reason    TEXT,
    authorization_version_at_change BIGINT NOT NULL DEFAULT 1,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fazle_identity_role_scope_ck CHECK (
        (scope_type = 'global' AND scope_key IS NULL)
        OR (scope_type <> 'global' AND scope_key IS NOT NULL)
    ),
    CONSTRAINT fazle_identity_role_period_ck CHECK (
        effective_until IS NULL OR effective_until > effective_from
    ),
    CONSTRAINT fazle_identity_role_revoke_ck CHECK (
        active OR revoked_at IS NOT NULL
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fazle_identity_role_active
    ON fazle_identity_role_assignments (
        identity_id, role_name, business_domain, scope_type, COALESCE(scope_key, '')
    ) WHERE active;
CREATE INDEX IF NOT EXISTS idx_fazle_identity_role_subject_active
    ON fazle_identity_role_assignments (identity_id, active, business_domain);
CREATE INDEX IF NOT EXISTS idx_fazle_identity_role_domain_scope
    ON fazle_identity_role_assignments (business_domain, scope_type, scope_key, active);
CREATE INDEX IF NOT EXISTS idx_fazle_identity_role_history
    ON fazle_identity_role_assignments (identity_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fazle_outbound_holds (
    hold_id             BIGSERIAL PRIMARY KEY,
    target_type         VARCHAR(20) NOT NULL
        CHECK (target_type IN ('identity', 'conversation')),
    identity_id         BIGINT REFERENCES fazle_identity_subjects(identity_id)
        ON DELETE RESTRICT,
    conversation_key    TEXT,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    reason              TEXT NOT NULL,
    placed_by           TEXT NOT NULL DEFAULT 'system',
    placed_by_admin_id  BIGINT REFERENCES fazle_admins(id) ON DELETE SET NULL,
    placed_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_by         TEXT,
    released_by_admin_id BIGINT REFERENCES fazle_admins(id) ON DELETE SET NULL,
    release_reason      TEXT,
    released_at         TIMESTAMPTZ,
    state_version       BIGINT NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fazle_outbound_hold_target_ck CHECK (
        (target_type = 'identity' AND identity_id IS NOT NULL AND conversation_key IS NULL)
        OR (target_type = 'conversation' AND identity_id IS NULL AND conversation_key IS NOT NULL
            AND length(conversation_key) > 0)
    ),
    CONSTRAINT fazle_outbound_hold_release_ck CHECK (
        active OR released_at IS NOT NULL
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_fazle_outbound_hold_identity_active
    ON fazle_outbound_holds (identity_id)
    WHERE target_type = 'identity' AND active;
CREATE UNIQUE INDEX IF NOT EXISTS uq_fazle_outbound_hold_conversation_active
    ON fazle_outbound_holds (conversation_key)
    WHERE target_type = 'conversation' AND active;
CREATE INDEX IF NOT EXISTS idx_fazle_outbound_holds_identity_state
    ON fazle_outbound_holds (identity_id, active, state_version)
    WHERE target_type = 'identity';
CREATE INDEX IF NOT EXISTS idx_fazle_outbound_holds_conversation_state
    ON fazle_outbound_holds (conversation_key, active, state_version)
    WHERE target_type = 'conversation';
CREATE INDEX IF NOT EXISTS idx_fazle_outbound_holds_history
    ON fazle_outbound_holds (target_type, created_at DESC);

COMMIT;
