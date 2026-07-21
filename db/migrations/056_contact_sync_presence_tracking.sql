-- Migration 056: Contact sync presence tracking + hard-delete audit trail.
--
-- Owner decision (2026-07-22): daily phone-contact-book <-> wbom_contacts
-- sync must not hard-delete a contact the instant it's missing from one
-- sync (phone offline / bridge failure / incomplete export could cause a
-- wrongful deletion). Soft-delete (wbom_contacts.is_active=false,
-- marked_for_delete_at set -- both columns already exist, no change here)
-- on first miss; hard-delete only after N consecutive misses.
--
-- fazle_contact_presence tracks the miss-streak per (phone, bridge) --
-- deliberately not a single wbom_contacts column, since absence is scoped
-- per bridge: a contact present in bridge1's phone book but not bridge2's
-- is normal (two different phones), not evidence of deletion.
--
-- fazle_contact_hard_delete_log is written before every hard-delete
-- (irreversible by definition) so a mistaken deletion is reconstructable.

CREATE TABLE IF NOT EXISTS fazle_contact_presence (
    phone                TEXT        NOT NULL,
    bridge               TEXT        NOT NULL,
    consecutive_absences INTEGER     NOT NULL DEFAULT 0,
    last_present_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (phone, bridge)
);

CREATE TABLE IF NOT EXISTS fazle_contact_hard_delete_log (
    id                    BIGSERIAL   PRIMARY KEY,
    phone                 TEXT        NOT NULL,
    bridge                TEXT        NOT NULL,
    last_display_name     TEXT,
    last_relation         TEXT,
    consecutive_absences  INTEGER,
    deleted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fazle_contact_presence_bridge
    ON fazle_contact_presence (bridge);
CREATE INDEX IF NOT EXISTS idx_fazle_contact_hard_delete_log_phone
    ON fazle_contact_hard_delete_log (phone, deleted_at DESC);
