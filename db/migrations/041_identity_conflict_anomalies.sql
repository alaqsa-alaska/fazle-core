-- Migration 041: Persisted identity-conflict anomaly log (Owner decision, 2026-07-11)
--
-- Payment Parser Canonical Spec section 4.3/4.4: a stale `blocked` row in
-- fazle_contact_roles must never silently suppress a number the deployment
-- itself configures as admin/owner/bridge/accountant identity. This was
-- previously fixed in modules.message_router._should_silent_skip() to stop
-- dropping the message, but the conflict was only surfaced via a WARNING
-- log line -- invisible to admin/superadmin unless they read server logs.
--
-- This table gives that event a persisted, UI-visible record so it can be
-- reviewed and resolved without server access.
--
-- One open (unresolved) row per (anomaly_type, phone): repeated messages
-- from the same still-conflicted number bump occurrence_count/
-- last_detected_at instead of spamming the queue with duplicate rows. Once
-- an admin resolves it (e.g. after fixing the stale fazle_contact_roles
-- entry), a future conflict on the same phone opens a fresh row.
--
-- Safe to apply: purely additive new table, no existing table touched.

CREATE TABLE IF NOT EXISTS fpe_identity_anomalies (
    id                  BIGSERIAL PRIMARY KEY,
    anomaly_type        TEXT NOT NULL DEFAULT 'identity_conflict',
    phone               TEXT NOT NULL,
    detail              TEXT,
    source_module       TEXT,
    first_detected_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_detected_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    resolved            BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_by         TEXT,
    resolved_at         TIMESTAMPTZ,
    resolution_note     TEXT,
    CONSTRAINT fpe_identity_anomalies_occurrence_count_positive
        CHECK (occurrence_count > 0),
    CONSTRAINT fpe_identity_anomalies_resolved_fields_consistent
        CHECK (
            (resolved = FALSE AND resolved_by IS NULL AND resolved_at IS NULL)
            OR (resolved = TRUE)
        )
);

-- Only one open row per (type, phone) -- see dedupe rationale above.
CREATE UNIQUE INDEX IF NOT EXISTS uq_fpe_identity_anomalies_open
    ON fpe_identity_anomalies (anomaly_type, phone)
    WHERE NOT resolved;

CREATE INDEX IF NOT EXISTS idx_fpe_identity_anomalies_unresolved_recent
    ON fpe_identity_anomalies (last_detected_at DESC)
    WHERE NOT resolved;
