-- Migration 089: retain the actor for verified-identifier revocation.
--
-- Migration 087 already preserves identifier state and revocation reason, but
-- its link row has no dedicated revoking actor.  This additive column keeps
-- the identity-role admin transaction auditable without replacing the link
-- table or inferring identity from names.

BEGIN;

ALTER TABLE wbom_identity_links
    ADD COLUMN IF NOT EXISTS revoked_by TEXT,
    ADD COLUMN IF NOT EXISTS revoked_by_admin_id BIGINT
        REFERENCES fazle_admins(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_wbom_identity_links_revocation_actor
    ON wbom_identity_links (revoked_by_admin_id, revoked_at)
    WHERE revoked_at IS NOT NULL;

COMMIT;
